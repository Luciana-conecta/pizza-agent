from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .. import db

# `is_owner` se fija en el constructor a partir de RouterState.is_owner (main.py),
# nunca es un argumento que el LLM controle. Es la segunda capa de seguridad
# además de que `admin_query` ni se ofrece como categoría al clasificador
# cuando el chat no es el de la dueña.
_NO_AUTORIZADO = "No autorizado: esta consulta es solo para la dueña de la pizzería."


class ConsultarClienteInput(BaseModel):
    identificador: str = Field(..., description="Teléfono o id interno del cliente.")


class ConsultarClienteTool(BaseTool):
    name: str = "consultar_cliente"
    description: str = "Busca los datos de un cliente puntual por teléfono o id."
    args_schema: Type[BaseModel] = ConsultarClienteInput
    is_owner: bool = False

    def _run(self, identificador: str) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        cliente = db.consultar_cliente(identificador)
        if cliente is None:
            return "No encontré ningún cliente con ese dato."
        return str(cliente)


class ConsultarClientePorNombreInput(BaseModel):
    nombre_parcial: str = Field(..., description="Nombre o parte del nombre del cliente a buscar.")


class ConsultarClientePorNombreTool(BaseTool):
    name: str = "consultar_cliente_por_nombre"
    description: str = "Busca clientes cuyo nombre coincida (parcial) con el texto dado."
    args_schema: Type[BaseModel] = ConsultarClientePorNombreInput
    is_owner: bool = False

    def _run(self, nombre_parcial: str) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        clientes = db.consultar_cliente_por_nombre(nombre_parcial)
        if not clientes:
            return "No encontré clientes con ese nombre."
        return "\n".join(str(c) for c in clientes)


class ConsultarTotalClienteInput(BaseModel):
    cliente_id: int = Field(..., description="Id interno del cliente.")


class ConsultarTotalClienteTool(BaseTool):
    name: str = "consultar_total_cliente"
    description: str = "Devuelve cuánto gastó en total un cliente y cuántos pedidos hizo."
    args_schema: Type[BaseModel] = ConsultarTotalClienteInput
    is_owner: bool = False

    def _run(self, cliente_id: int) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        info = db.consultar_total_cliente(cliente_id)
        if info is None:
            return "No encontré ese cliente."
        return f"{info['nombre']}: gastó Gs. {info['total_gastado']} en {info['cantidad_pedidos']} pedidos."


class ConsultarUltimoPedidoInput(BaseModel):
    cliente_id: int = Field(..., description="Id interno del cliente.")


class ConsultarUltimoPedidoTool(BaseTool):
    name: str = "consultar_ultimo_pedido"
    description: str = "Devuelve el último pedido de un cliente puntual, con sus items."
    args_schema: Type[BaseModel] = ConsultarUltimoPedidoInput
    is_owner: bool = False

    def _run(self, cliente_id: int) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        pedido = db.consultar_ultimo_pedido(cliente_id)
        if pedido is None:
            return "Ese cliente no tiene pedidos."
        items = ", ".join(f"{it['cantidad']}x {it['nombre']}" for it in pedido["items"])
        return f"Pedido #{pedido['id']} ({pedido['fecha']}) — {pedido['estado']} — {items} — Total: Gs. {pedido['total']}"


class ConsultarPedidoClienteInput(BaseModel):
    cliente_id: int = Field(..., description="Id interno del cliente.")


class ConsultarPedidoClienteTool(BaseTool):
    name: str = "consultar_historial_pedidos_cliente"
    description: str = "Devuelve el historial completo de pedidos de un cliente."
    args_schema: Type[BaseModel] = ConsultarPedidoClienteInput
    is_owner: bool = False

    def _run(self, cliente_id: int) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        pedidos = db.consultar_pedido_cliente(cliente_id)
        if not pedidos:
            return "Ese cliente no tiene pedidos."
        return "\n".join(f"#{p['id']} — {p['fecha']} — {p['estado']} — Gs. {p['total']}" for p in pedidos)


class ConsultarClientesFrecuentesInput(BaseModel):
    limite: int = Field(default=10, description="Cuántos clientes traer, ordenados por cantidad de pedidos.")


class ConsultarClientesFrecuentesTool(BaseTool):
    name: str = "consultar_clientes_frecuentes"
    description: str = "Lista los clientes con más pedidos."
    args_schema: Type[BaseModel] = ConsultarClientesFrecuentesInput
    is_owner: bool = False

    def _run(self, limite: int = 10) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        clientes = db.consultar_clientes_frecuentes(limite)
        if not clientes:
            return "No hay clientes registrados todavía."
        return "\n".join(
            f"{c['nombre']} ({c['telefono']}): {c['cantidad_pedidos']} pedidos, Gs. {c['total_gastado']} gastados"
            for c in clientes
        )


class ConsultarClienteQueMasCompraInput(BaseModel):
    pass


class ConsultarClienteQueMasCompraTool(BaseTool):
    name: str = "consultar_cliente_que_mas_compra"
    description: str = "Devuelve el cliente que más gastó en total, históricamente."
    args_schema: Type[BaseModel] = ConsultarClienteQueMasCompraInput
    is_owner: bool = False

    def _run(self) -> str:
        if not self.is_owner:
            return _NO_AUTORIZADO
        cliente = db.consultar_cliente_que_mas_compra()
        if cliente is None:
            return "Todavía no hay clientes registrados."
        return f"{cliente['nombre']} ({cliente['telefono']}): Gs. {cliente['total_gastado']} gastados en total."
