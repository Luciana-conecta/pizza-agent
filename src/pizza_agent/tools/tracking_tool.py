from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .. import db


class OrderTrackingInput(BaseModel):
    pedido_id: int = Field(
        default=0,
        description="Número de pedido si el cliente lo dio. 0 si no lo dio (se busca su pedido más reciente).",
    )


class OrderTrackingTool(BaseTool):
    name: str = "rastrear_pedido"
    description: str = (
        "Busca el estado de un pedido del cliente que está escribiendo. Solo puede ver sus "
        "propios pedidos. Si no dio un número de pedido, dejá pedido_id en 0."
    )
    args_schema: Type[BaseModel] = OrderTrackingInput
    chat_id: str

    def _run(self, pedido_id: int = 0) -> str:
        pedido = db.track_pedido(self.chat_id, pedido_id or None)
        if pedido is None:
            return "No encontré ningún pedido tuyo con esos datos."
        items = ", ".join(f"{it['cantidad']}x {it['nombre']}" for it in pedido["items"])
        return (
            f"Pedido #{pedido['id']} — estado: {pedido['estado']}\n"
            f"Fecha: {pedido['fecha']}\n"
            f"Items: {items}\n"
            f"Total: Gs. {pedido['total']}"
        )
