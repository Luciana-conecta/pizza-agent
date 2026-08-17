from typing import Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .. import db, formatters

# `is_owner` se fija en el constructor a partir de RouterState.is_owner (main.py),
# nunca es un argumento que el LLM controle. Es la segunda capa de seguridad
# además de que `admin_query` ni se ofrece como categoría al clasificador
# cuando el chat no es el de la dueña.
_NO_AUTORIZADO = "No autorizado: esta consulta es solo para la dueña de la pizzería."


class NombreClienteInput(BaseModel):
    nombre: str = Field(..., description="Nombre o parte del nombre del cliente.")


class ObtenerResumenClienteTool(BaseTool):
    name: str = "obtener_resumen_cliente"
    description: str = (
        "Devuelve cuántos pedidos hizo un cliente y cuánto gastó en total, buscándolo por nombre."
    )
    args_schema: Type[BaseModel] = NombreClienteInput
    is_owner: bool = False

    def _run(self, nombre: str) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        result = db.obtener_resumen_cliente(nombre)
        if not result.success:
            return result.error
        return formatters.format_customer_summary(result.data)


class ObtenerUltimoPedidoTool(BaseTool):
    name: str = "obtener_ultimo_pedido"
    description: str = "Devuelve el último pedido de un cliente, buscándolo por nombre."
    args_schema: Type[BaseModel] = NombreClienteInput
    is_owner: bool = False

    def _run(self, nombre: str) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        result = db.obtener_ultimo_pedido_por_nombre(nombre)
        if not result.success:
            return result.error
        return formatters.format_last_order(result.data)


class ObtenerHistorialPedidosTool(BaseTool):
    name: str = "obtener_historial_pedidos"
    description: str = "Devuelve el historial completo de pedidos de un cliente, buscándolo por nombre."
    args_schema: Type[BaseModel] = NombreClienteInput
    is_owner: bool = False

    def _run(self, nombre: str) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        result = db.obtener_historial_por_nombre(nombre)
        if not result.success:
            return result.error
        return formatters.format_order_history(result.data)


class ClientesFrecuentesInput(BaseModel):
    limite: int = Field(default=10, description="Cuántos clientes traer.")
    ordenar_por: Literal["pedidos", "gasto"] = Field(
        default="pedidos",
        description=(
            "'pedidos' para los clientes con más pedidos (clientes frecuentes), "
            "'gasto' para los que más gastaron en total (mejores clientes)."
        ),
    )


class ObtenerClientesFrecuentesTool(BaseTool):
    name: str = "obtener_clientes_frecuentes"
    description: str = (
        "Lista los clientes ordenados por cantidad de pedidos o por total gastado. "
        "Usala tanto para 'clientes frecuentes' como para 'quién es mi mejor cliente' "
        "(en ese caso, limite=1 y ordenar_por='gasto')."
    )
    args_schema: Type[BaseModel] = ClientesFrecuentesInput
    is_owner: bool = False

    def _run(self, limite: int = 10, ordenar_por: str = "pedidos") -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        result = db.obtener_clientes_frecuentes(limite, ordenar_por)
        if not result.success:
            return result.error
        return formatters.format_frequent_customers(result.data, ordenar_por)


class PedidosPorEstadoInput(BaseModel):
    estado: Literal["pendiente", "en_preparacion", "en_camino", "entregado", "cancelado"] = Field(
        default="pendiente",
        description="Estado de pedido a listar, de todos los clientes (ej. 'pendiente' para pedidos pendientes).",
    )


class ObtenerPedidosPorEstadoTool(BaseTool):
    name: str = "obtener_pedidos_por_estado"
    description: str = (
        "Lista todos los pedidos (de cualquier cliente) que están en un estado dado, "
        "ej. 'pedidos pendientes', 'pedidos en camino'. Los más viejos primero."
    )
    args_schema: Type[BaseModel] = PedidosPorEstadoInput
    is_owner: bool = False

    def _run(self, estado: str = "pendiente") -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        result = db.obtener_pedidos_por_estado(estado)
        if not result.success:
            return result.error
        return formatters.format_orders_by_status(result.data, estado)
