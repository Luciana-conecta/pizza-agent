"""Formateadores deterministas: texto final para Telegram a partir de datos ya
resueltos (el `data` de un `Result`, o un `OrderDraft`). Funciones puras, sin
acceso a `db` ni a `session_store` — el LLM nunca decide este texto.
"""

from .session_store import OrderDraft


def format_menu(productos: list[dict]) -> str:
    if not productos:
        return "No hay productos disponibles en esa categoría."
    lineas = [
        f"- {p['nombre']} ({p['categoria']}): {p['descripcion'] or 'sin descripción'} — Gs. {p['precio']}"
        for p in productos
    ]
    return "\n".join(lineas)


def _format_items(items: list[dict]) -> str:
    return ", ".join(f"{it['cantidad']}x {it['nombre']}" for it in items)


def format_customer_summary(data: dict) -> str:
    return f"{data['nombre']}: gastó Gs. {data['total_gastado']} en {data['cantidad_pedidos']} pedidos."


def format_last_order(data: dict) -> str:
    return (
        f"Último pedido de {data['cliente_nombre']}: "
        f"#{data['pedido_id']} ({data['fecha']}) — {data['estado']} — "
        f"{_format_items(data['items'])} — Total: Gs. {data['total']}"
    )


def format_order_history(data: dict) -> str:
    if not data["pedidos"]:
        return f"{data['cliente_nombre']} todavía no tiene pedidos."
    lineas = [f"#{p['id']} — {p['fecha']} — {p['estado']} — Gs. {p['total']}" for p in data["pedidos"]]
    return f"Historial de {data['cliente_nombre']}:\n" + "\n".join(lineas)


def format_frequent_customers(clientes: list[dict], ordenar_por: str) -> str:
    if not clientes:
        return "No hay clientes registrados todavía."
    etiqueta = "gastado" if ordenar_por == "gasto" else "pedidos"
    return "\n".join(
        f"{c['nombre']} ({c['telefono']}): {c['cantidad_pedidos']} pedidos, "
        f"Gs. {c['total_gastado']} {etiqueta}"
        for c in clientes
    )


def format_orders_by_status(pedidos: list[dict], estado: str) -> str:
    if not pedidos:
        return f"No hay pedidos en estado '{estado}'."
    lineas = [
        f"#{p['id']} — {p['cliente_nombre']} ({p['telefono']}) — {p['fecha']} — Gs. {p['total']}"
        for p in pedidos
    ]
    return f"Pedidos en estado '{estado}' ({len(pedidos)}):\n" + "\n".join(lineas)


def format_order_status(pedido: dict) -> str:
    return (
        f"Pedido #{pedido['id']} — estado: {pedido['estado']}\n"
        f"Fecha: {pedido['fecha']}\n"
        f"Items: {_format_items(pedido['items'])}\n"
        f"Total: Gs. {pedido['total']}"
    )


def format_draft_view(draft: OrderDraft) -> str:
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


def format_order_confirmation(pedido_id: int, draft: OrderDraft) -> str:
    resumen = "\n".join(f"- {it.cantidad}x {it.nombre}" for it in draft.items)
    return f"¡Pedido #{pedido_id} confirmado!\n{resumen}\nTotal: Gs. {draft.total}"
