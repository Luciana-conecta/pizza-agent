"""Validación determinista de un pedido antes de confirmarlo.

Se usa en dos momentos: en `main.py::_handle_take_order` para decidir si
mostrar los botones de confirmar, y en `telegram_bot.py::on_confirm_or_cancel`
como la autorización real antes de `db.crear_pedido`. El LLM nunca decide esto.
"""

import re

from . import db
from .session_store import OrderDraft


def _telefono_valido(telefono: str) -> bool:
    return len(re.sub(r"\D", "", telefono)) >= 6


def validar_draft_para_confirmar(draft: OrderDraft) -> tuple[bool, list[str]]:
    errores: list[str] = []

    if not draft.items:
        errores.append("El pedido no tiene productos.")
    else:
        errores += [f"Cantidad inválida para {it.nombre}." for it in draft.items if it.cantidad <= 0]

    if not draft.cliente_nombre.strip():
        errores.append("Falta el nombre del cliente.")
    if not _telefono_valido(draft.telefono):
        errores.append("El teléfono no parece válido (mínimo 6 dígitos).")
    if not draft.direccion.strip():
        errores.append("Falta la dirección de entrega.")
    if draft.total <= 0:
        errores.append("El total del pedido debe ser mayor a cero.")

    if not errores:
        for it in draft.items:
            resultado = db.get_producto_by_id(it.producto_id)
            if not resultado.success:
                errores.append(f"'{it.nombre}' ya no está disponible.")
            elif resultado.data["precio"] != it.precio_unitario:
                errores.append(f"El precio de '{it.nombre}' cambió, por favor revisá el pedido de nuevo.")

    return (len(errores) == 0, errores)
