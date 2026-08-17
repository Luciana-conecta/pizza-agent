from typing import Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .. import db
from ..session_store import OrderItem

# chat_id y session_store se fijan en el constructor (no son argumentos del LLM),
# así el modelo nunca puede leer ni escribir la sesión de otro chat.


class AddItemInput(BaseModel):
    producto_nombre: str = Field(
        ..., description="Nombre del producto tal como lo dijo el cliente, ej. 'pizza muzzarella grande'."
    )
    cantidad: int = Field(default=1, description="Cantidad de unidades.")


class AddItemToDraftTool(BaseTool):
    name: str = "agregar_item_al_pedido"
    description: str = (
        "Agrega un producto al pedido que se está armando. Busca el producto real en el menú "
        "(nunca inventes nombres ni precios) y lo suma al borrador."
    )
    args_schema: Type[BaseModel] = AddItemInput
    chat_id: str
    session_store: Any

    def _run(self, producto_nombre: str, cantidad: int = 1) -> str:
        producto = db.find_producto(producto_nombre)
        if producto is None:
            return f"No encontré '{producto_nombre}' en el menú. Pedile al cliente que aclare o consultá el menú."
        draft = self.session_store.get_or_create(self.chat_id)
        draft.items.append(
            OrderItem(
                producto_id=producto["id"],
                nombre=producto["nombre"],
                cantidad=cantidad,
                precio_unitario=producto["precio"],
            )
        )
        draft.status = "building"
        self.session_store.save(draft)
        return f"Agregado: {cantidad}x {producto['nombre']} (Gs. {producto['precio']} c/u). Total parcial: Gs. {draft.total}"


class RemoveItemInput(BaseModel):
    producto_nombre: str = Field(..., description="Nombre del producto a quitar del pedido.")


class RemoveItemFromDraftTool(BaseTool):
    name: str = "quitar_item_del_pedido"
    description: str = "Quita un producto del pedido que se está armando."
    args_schema: Type[BaseModel] = RemoveItemInput
    chat_id: str
    session_store: Any

    def _run(self, producto_nombre: str) -> str:
        draft = self.session_store.get_or_create(self.chat_id)
        antes = len(draft.items)
        draft.items = [it for it in draft.items if producto_nombre.lower() not in it.nombre.lower()]
        if len(draft.items) == antes:
            return f"No había '{producto_nombre}' en el pedido."
        self.session_store.save(draft)
        return f"Quitado. Total parcial: Gs. {draft.total}"


class ViewDraftInput(BaseModel):
    pass


class ViewDraftTool(BaseTool):
    name: str = "ver_pedido_actual"
    description: str = "Muestra el pedido que se está armando hasta ahora: items, datos del cliente y total."
    args_schema: Type[BaseModel] = ViewDraftInput
    chat_id: str
    session_store: Any

    def _run(self) -> str:
        draft = self.session_store.get_or_create(self.chat_id)
        if not draft.items:
            return "Todavía no hay productos en el pedido."
        items = "\n".join(f"- {it.cantidad}x {it.nombre} (Gs. {it.precio_unitario} c/u)" for it in draft.items)
        return (
            f"{items}\n"
            f"Total: Gs. {draft.total}\n"
            f"Cliente: {draft.cliente_nombre or '(sin definir)'}\n"
            f"Teléfono: {draft.telefono or '(sin definir)'}\n"
            f"Dirección: {draft.direccion or '(sin definir)'}"
        )


class SetCustomerInfoInput(BaseModel):
    cliente_nombre: str = Field(default="", description="Nombre del cliente, si lo dio.")
    telefono: str = Field(default="", description="Teléfono del cliente, si lo dio.")
    direccion: str = Field(default="", description="Dirección de entrega, si la dio.")


class SetCustomerInfoTool(BaseTool):
    name: str = "guardar_datos_cliente"
    description: str = "Guarda nombre, teléfono y/o dirección del cliente en el pedido que se está armando."
    args_schema: Type[BaseModel] = SetCustomerInfoInput
    chat_id: str
    session_store: Any

    def _run(self, cliente_nombre: str = "", telefono: str = "", direccion: str = "") -> str:
        draft = self.session_store.get_or_create(self.chat_id)
        if cliente_nombre:
            draft.cliente_nombre = cliente_nombre
        if telefono:
            draft.telefono = telefono
        if direccion:
            draft.direccion = direccion
        self.session_store.save(draft)
        return "Datos guardados."
