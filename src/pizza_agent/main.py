#!/usr/bin/env python
import json
import os
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field

from crewai import LLM, Agent
from crewai.flow import Flow, listen, router, start

from . import authorization, order_validation
from .session_store import session_store
from .tools.admin_tools import (
    ObtenerClientesFrecuentesTool,
    ObtenerHistorialPedidosTool,
    ObtenerPedidosPorEstadoTool,
    ObtenerResumenClienteTool,
    ObtenerUltimoPedidoTool,
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
# Los agentes con herramientas necesitan un modelo que soporte bien tool-calling;
# el 8b lo hace mal (contesta con texto genérico en vez de llamar a la herramienta).
NVIDIA_AGENT_MODEL = "meta/llama-3.1-70b-instruct"
# El clasificador solo genera un JSON estructurado (sin tools), así que un modelo
# chico y rápido alcanza. Usarlo acá reduce la carga sobre el modelo grande.
NVIDIA_CLASSIFIER_MODEL = "meta/llama-3.1-8b-instruct"


def _nvidia_llm(model: str) -> LLM:
    return LLM(
        model=model,
        base_url=NVIDIA_BASE_URL,
        api_key=os.getenv("NVIDIA_API_KEY"),
        custom_openai=True,
        # NVIDIA a veces deja un request colgado sin responder (no es un error, es
        # silencio). El cliente OpenAI reintenta solo, pero espera el timeout entero
        # antes de reintentar. Medido en prod (2026-08-20): ~40% de los intentos se
        # cuelgan así, casi siempre resueltos por el reintento; las llamadas que sí
        # responden lo hacen en 2-10s. 10s da margen de sobra sin alargar de más la
        # espera cuando los 3 intentos se cuelgan (antes ~47s, ahora ~32s).
        timeout=10,
    )


def nvidia_llm() -> LLM:
    return _nvidia_llm(NVIDIA_AGENT_MODEL)


def nvidia_classifier_llm() -> LLM:
    return _nvidia_llm(NVIDIA_CLASSIFIER_MODEL)


CONFIDENCE_THRESHOLD = 0.6

CLASSIFIER_PROMPT_TEMPLATE = """Clasificá el siguiente mensaje de un cliente de una pizzería.

Mensaje: {request}

Categorías:
- menu_faq: Preguntas generales sobre el menú, precios, ingredientes, horarios, ubicación o
  delivery. También un saludo o charla casual sin pedido concreto (ej. "hola", "buenas",
  "cómo andan") va acá, no en complaint_or_other.
- take_order: El cliente quiere pedir algo, está agregando/quitando productos, o dando sus datos de entrega.
- track_order: El cliente pregunta por el estado de un pedido que ya hizo.
- complaint_or_other: Quejas, reclamos o problemas reales con un pedido. No uses esta categoría
  solo porque el mensaje es corto o ambiguo: un saludo sin más contexto no es una queja.{admin_category}

Marcá can_automate=True solo si un agente automático puede resolver esto sin que un humano
tenga que revisar el resultado después. Un saludo o charla casual sin pedido concreto siempre
es can_automate=True con confidence alta. Escalá (can_automate=False) cualquier enojo o queja
seria, pedidos de reembolso, o cualquier cosa de la que no estés seguro.
"""

COMMON_AGENT_RULES = (
    " Reglas importantes: (1) Si usás una herramienta, tu respuesta final SIEMPRE tiene que "
    "incluir los datos concretos que te devolvió esa herramienta (nombres, precios, montos, "
    "estados, etc.) — nunca digas que 'ahí va la información' sin ponerla de verdad. "
    "(2) Si el mensaje es un saludo o charla casual sin un pedido concreto (ej. 'hola', 'buenas'), "
    "no uses ninguna herramienta: respondé con un saludo corto y amable, en tus propias "
    "palabras, que invite a pedir. Esta regla es solo para saludos reales — un mensaje que ya "
    "tiene contenido (un sabor, una pregunta, una confirmación) nunca se responde con un saludo "
    "genérico, aunque no estés seguro de qué otra cosa decir."
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
        if self._resolve_sticky_intent():
            return
        self._classify_intent_with_llm()

    def _resolve_sticky_intent(self) -> bool:
        """Decisión de sesión: si ya hay un pedido en construcción, no reclasifica."""
        draft = session_store.get(self.state.chat_id) if self.state.chat_id else None
        if draft is None or draft.status not in ("building", "awaiting_confirmation"):
            return False

        self.state.intent = "take_order"
        self.state.can_automate = True
        self.state.confidence = 1.0
        self.state.reasoning = "Ya hay un pedido en construcción para este chat."
        print(f"Ruteo pegajoso a take_order para chat {self.state.chat_id}")
        return True

    def _classify_intent_with_llm(self):
        """Decisión de intención: delega en el LLM clasificador."""
        print(f"Classifying request: {self.state.request}")

        llm = nvidia_classifier_llm()
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
        result = agent.kickoff(prompt)

        self.state.response = result.raw
        self.state.last_bot_message = result.raw

        draft = session_store.get(self.state.chat_id)
        if draft is not None and draft.items:
            listo, _ = order_validation.validar_draft_para_confirmar(draft)
            if listo:
                try:
                    draft.transition_to("awaiting_confirmation")
                    session_store.save(draft)
                except ValueError as exc:
                    print(f"Transición de estado rechazada para chat {self.state.chat_id}: {exc}")

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
            InfoLookupTool(),
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
                "contacto y entrega del cliente. Si te preguntan qué productos/pizzas hay, "
                "precios, u horarios/dirección — aunque ya haya un pedido en curso — llamá "
                "primero a la herramienta correspondiente (consultar_menu o "
                "consultar_info_local) y recién ahí respondé con esos datos reales. Nunca "
                "respondas una lista de opciones sin haber llamado a la herramienta antes." + COMMON_AGENT_RULES
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
            ObtenerResumenClienteTool(is_owner=self.state.is_owner),
            ObtenerUltimoPedidoTool(is_owner=self.state.is_owner),
            ObtenerHistorialPedidosTool(is_owner=self.state.is_owner),
            ObtenerClientesFrecuentesTool(is_owner=self.state.is_owner),
            ObtenerPedidosPorEstadoTool(is_owner=self.state.is_owner),
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
        owner_chat_id = authorization.owner_chat_id()
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
