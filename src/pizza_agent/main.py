#!/usr/bin/env python
import json
import os
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field

from crewai import LLM, Agent
from crewai.flow import Flow, listen, router, start

from .session_store import session_store
from .tools.admin_tools import (
    ConsultarClienteQueMasCompraTool,
    ConsultarClienteTool,
    ConsultarClientePorNombreTool,
    ConsultarClientesFrecuentesTool,
    ConsultarPedidoClienteTool,
    ConsultarTotalClienteTool,
    ConsultarUltimoPedidoTool,
)
from .tools.menu_tool import InfoLookupTool, MenuLookupTool
from .tools.order_tools import (
    AddItemToDraftTool,
    RemoveItemFromDraftTool,
    SetCustomerInfoTool,
    ViewDraftTool,
)
from .tools.tracking_tool import OrderTrackingTool

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "meta/llama-3.1-8b-instruct"


def nvidia_llm() -> LLM:
    return LLM(
        model=NVIDIA_MODEL,
        base_url=NVIDIA_BASE_URL,
        api_key=os.getenv("NVIDIA_API_KEY"),
        custom_openai=True,
        timeout=30,
    )


CONFIDENCE_THRESHOLD = 0.6

CLASSIFIER_PROMPT_TEMPLATE = """Clasificá el siguiente mensaje de un cliente de una pizzería.

Mensaje: {request}

Categorías:
- menu_faq: Preguntas generales sobre el menú, precios, ingredientes, horarios, ubicación o delivery.
- take_order: El cliente quiere pedir algo, está agregando/quitando productos, o dando sus datos de entrega.
- track_order: El cliente pregunta por el estado de un pedido que ya hizo.
- complaint_or_other: Quejas, reclamos, problemas con un pedido, o cualquier cosa que no encaje arriba.{admin_category}

Marcá can_automate=True solo si un agente automático puede resolver esto sin que un humano
tenga que revisar el resultado después. Escalá (can_automate=False) cualquier enojo o queja
seria, pedidos de reembolso, o cualquier cosa de la que no estés seguro.
"""

COMMON_AGENT_RULES = (
    " Reglas importantes: (1) Si usás una herramienta, tu respuesta final SIEMPRE tiene que "
    "incluir los datos concretos que te devolvió esa herramienta (nombres, precios, montos, "
    "estados, etc.) — nunca digas que 'ahí va la información' sin ponerla de verdad. "
    "(2) Si el mensaje es un saludo o charla casual sin un pedido concreto, respondé de forma "
    "natural, corta y amable, sin necesidad de usar ninguna herramienta. Nunca respondas algo "
    "como 'no hay una función que responda a este mensaje' — siempre podés al menos saludar y "
    "preguntar en qué ayudar."
)

ADMIN_CATEGORY_TEXT = """
- admin_query: La dueña de la pizzería pregunta algo sobre clientes o ventas (ej. "cuánto
  gastó fulano", "quién es mi cliente más frecuente", "última compra de tal cliente").
"""


def build_classifier_prompt(request: str, is_owner: bool) -> str:
    return CLASSIFIER_PROMPT_TEMPLATE.format(
        request=request,
        admin_category=ADMIN_CATEGORY_TEXT if is_owner else "",
    )


class IntentClassification(BaseModel):
    intent: Literal["menu_faq", "take_order", "track_order", "complaint_or_other", "admin_query"] = Field(
        description="La categoría que mejor describe el mensaje del cliente."
    )
    can_automate: bool = Field(
        description="True si un agente automático puede resolver esto sin que un humano revise el resultado."
    )
    confidence: float = Field(description="Confianza en esta clasificación, de 0.0 a 1.0.")
    reasoning: str = Field(description="Justificación breve de la clasificación y la decisión de ruteo.")


class OrderTurnOutput(BaseModel):
    reply: str = Field(description="Mensaje para responderle al cliente en este turno de la conversación.")
    ready_to_confirm: bool = Field(
        description=(
            "True solo si el pedido ya tiene al menos un producto y los datos de contacto y "
            "entrega del cliente, y se le puede pedir que confirme el pedido."
        )
    )


class RouterState(BaseModel):
    request: str = ""
    chat_id: str = ""
    is_owner: bool = False
    last_bot_message: str = ""
    intent: str = ""
    can_automate: bool = False
    confidence: float = 0.0
    reasoning: str = ""
    response: str = ""
    escalated: bool = False


class PizzaAgentFlow(Flow[RouterState]):

    @start()
    def classify_intent(self):
        draft = session_store.get(self.state.chat_id) if self.state.chat_id else None
        if draft is not None and draft.status in ("building", "awaiting_confirmation"):
            self.state.intent = "take_order"
            self.state.can_automate = True
            self.state.confidence = 1.0
            self.state.reasoning = "Ya hay un pedido en construcción para este chat."
            print(f"Ruteo pegajoso a take_order para chat {self.state.chat_id}")
            return

        print(f"Classifying request: {self.state.request}")

        llm = nvidia_llm()
        prompt = build_classifier_prompt(self.state.request, self.state.is_owner)
        classification = llm.call(
            messages=[{"role": "user", "content": prompt}],
            response_model=IntentClassification,
        )

        self.state.intent = classification.intent
        self.state.can_automate = classification.can_automate
        self.state.confidence = classification.confidence
        self.state.reasoning = classification.reasoning

        print(
            f"Intent: {self.state.intent} | can_automate={self.state.can_automate} "
            f"| confidence={self.state.confidence:.2f}"
        )

    @router(classify_intent)
    def route_request(self):
        if self.state.can_automate and self.state.confidence >= CONFIDENCE_THRESHOLD:
            return "automate"
        return "manual_review"

    @listen("automate")
    def handle_automated(self):
        print(f"Routing to automated handler for intent: {self.state.intent}")

        if self.state.intent == "take_order":
            self._handle_take_order()
            return

        handlers = {
            "menu_faq": self._faq_agent,
            "track_order": self._tracking_agent,
            "admin_query": self._admin_agent,
        }
        build_agent = handlers.get(self.state.intent, self._faq_agent)
        agent = build_agent()

        result = agent.kickoff(self.state.request)
        self.state.response = result.raw
        self.state.last_bot_message = self.state.response

    @listen("manual_review")
    def send_to_human(self):
        print("Escalating to human review")
        self.state.escalated = True
        self.state.response = "Tu mensaje fue derivado a alguien del local, en breve te responden."
        self.state.last_bot_message = self.state.response
        self._enqueue_for_review()
        self._notify_owner_telegram(
            f"Nueva escalación (chat {self.state.chat_id}):\n"
            f"Mensaje: {self.state.request}\n"
            f"Intención: {self.state.intent} (confianza {self.state.confidence:.2f})\n"
            f"Motivo: {self.state.reasoning}"
        )

    def _handle_take_order(self):
        session_store.get_or_create(self.state.chat_id)
        contexto = f"Mensaje anterior del bot: {self.state.last_bot_message}\n" if self.state.last_bot_message else ""
        prompt = f"{contexto}Mensaje del cliente: {self.state.request}"

        agent = self._order_agent()
        result = agent.kickoff(prompt, response_format=OrderTurnOutput)
        output: OrderTurnOutput = result.pydantic

        self.state.response = output.reply
        self.state.last_bot_message = output.reply

        draft = session_store.get(self.state.chat_id)
        if output.ready_to_confirm and draft is not None and draft.items:
            draft.status = "awaiting_confirmation"
            session_store.save(draft)

    def _faq_agent(self) -> Agent:
        return Agent(
            role="Agente de Preguntas Frecuentes",
            goal="Responder preguntas sobre el menú, precios, horarios, ubicación y delivery.",
            backstory="Conocés el menú y la información del local al detalle y nunca inventás datos."
            + COMMON_AGENT_RULES,
            llm=nvidia_llm(),
            tools=[MenuLookupTool(), InfoLookupTool()],
        )

    def _order_agent(self) -> Agent:
        tools = [
            MenuLookupTool(),
            AddItemToDraftTool(chat_id=self.state.chat_id, session_store=session_store),
            RemoveItemFromDraftTool(chat_id=self.state.chat_id, session_store=session_store),
            ViewDraftTool(chat_id=self.state.chat_id, session_store=session_store),
            SetCustomerInfoTool(chat_id=self.state.chat_id, session_store=session_store),
        ]
        return Agent(
            role="Agente de Pedidos",
            goal=(
                "Ayudar al cliente a armar su pedido de pizza: elegir productos y cantidades, "
                "y juntar nombre, teléfono y dirección para el delivery."
            ),
            backstory=(
                "Trabajás en una pizzería. Sos amable y directo. Nunca inventás productos ni "
                "precios: siempre los buscás con las herramientas. Solo marcás el pedido como "
                "listo para confirmar cuando ya hay al menos un producto y los datos de "
                "contacto y entrega del cliente." + COMMON_AGENT_RULES
            ),
            llm=nvidia_llm(),
            tools=tools,
        )

    def _tracking_agent(self) -> Agent:
        return Agent(
            role="Agente de Seguimiento de Pedidos",
            goal="Informar el estado de los pedidos del cliente que escribe.",
            backstory="Consultás siempre la herramienta de rastreo, nunca inventás un estado." + COMMON_AGENT_RULES,
            llm=nvidia_llm(),
            tools=[OrderTrackingTool(chat_id=self.state.chat_id)],
        )

    def _admin_agent(self) -> Agent:
        tools = [
            ConsultarClienteTool(is_owner=self.state.is_owner),
            ConsultarClientePorNombreTool(is_owner=self.state.is_owner),
            ConsultarTotalClienteTool(is_owner=self.state.is_owner),
            ConsultarUltimoPedidoTool(is_owner=self.state.is_owner),
            ConsultarPedidoClienteTool(is_owner=self.state.is_owner),
            ConsultarClientesFrecuentesTool(is_owner=self.state.is_owner),
            ConsultarClienteQueMasCompraTool(is_owner=self.state.is_owner),
        ]
        return Agent(
            role="Agente Administrativo",
            goal="Responder preguntas de la dueña sobre clientes y ventas usando las herramientas de consulta.",
            backstory="Tenés acceso de solo lectura a los datos de clientes y pedidos para reportarle a la dueña."
            + COMMON_AGENT_RULES,
            llm=nvidia_llm(),
            tools=tools,
        )

    def _enqueue_for_review(self):
        queue_dir = Path("output")
        queue_dir.mkdir(exist_ok=True)
        queue_file = queue_dir / "human_review_queue.jsonl"
        with open(queue_file, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "chat_id": self.state.chat_id,
                        "request": self.state.request,
                        "intent": self.state.intent,
                        "confidence": self.state.confidence,
                        "reasoning": self.state.reasoning,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        print(f"Queued for human review: {queue_file}")

    def _notify_owner_telegram(self, text: str) -> None:
        owner_chat_id = os.getenv("OWNER_TELEGRAM_CHAT_ID")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not owner_chat_id or not token:
            print("OWNER_TELEGRAM_CHAT_ID o TELEGRAM_BOT_TOKEN no configurados; no se notifica por Telegram.")
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": owner_chat_id, "text": text},
                timeout=10,
            )
        except requests.RequestException as exc:
            print(f"No se pudo notificar a la dueña por Telegram: {exc}")


def kickoff():
    flow = PizzaAgentFlow()
    flow.kickoff(
        inputs={
            "request": "¿Qué pizzas tienen y cuánto cuestan?",
            "chat_id": "local-test",
            "is_owner": False,
        }
    )
    print("\n--- Result ---")
    print(f"Intent: {flow.state.intent}")
    print(f"Escalated: {flow.state.escalated}")
    print(f"Response: {flow.state.response}")


def plot():
    flow = PizzaAgentFlow()
    flow.plot()


if __name__ == "__main__":
    kickoff()
