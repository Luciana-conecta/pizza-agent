"""Acceso a la base Postgres existente de la pizzería.

Las tablas (productos, clientes, pedidos, pedido_detalles) ya existían antes
de este proyecto. Este módulo no crea tablas, solo las consulta/actualiza.

Requiere la migración aditiva (una sola vez, ver README.md):
    ALTER TABLE clientes ADD COLUMN telegram_chat_id BIGINT UNIQUE;

Todas las funciones públicas devuelven un `Result` (ver `result.py`): `error`
es un string en español listo para mostrarse tal cual. `Result` es para "no
encontrado / dato inválido / ambiguo" — errores de infraestructura (conexión
caída, timeout) siguen propagando como excepción, sin envolver.
"""

import os
import threading
import time
from typing import Optional

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config
from .result import Result, fail, ok

_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    conninfo=os.environ["DATABASE_URL"],
                    min_size=0,
                    max_size=5,
                    kwargs={"row_factory": dict_row, "autocommit": True},
                )
    return _pool


# --- Menú ----------------------------------------------------------------

_menu_cache: list[dict] = []
_menu_cache_at: float = 0.0
_MENU_CACHE_TTL_SECONDS = 300


def get_menu(force_refresh: bool = False) -> Result:
    global _menu_cache, _menu_cache_at
    if not force_refresh and _menu_cache and (time.time() - _menu_cache_at) < _MENU_CACHE_TTL_SECONDS:
        return ok(_menu_cache)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT id, nombre, categoria, descripcion, precio
            FROM productos
            WHERE disponible IS TRUE
            ORDER BY categoria, nombre
            """
        ).fetchall()
    _menu_cache = rows
    _menu_cache_at = time.time()
    return ok(_menu_cache)


def find_producto(nombre: str) -> Result:
    """Busca un producto disponible por nombre (parcial, sin distinguir mayúsculas)."""
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT id, nombre, categoria, descripcion, precio
            FROM productos
            WHERE disponible IS TRUE AND nombre ILIKE %s
            ORDER BY nombre
            LIMIT 1
            """,
            (f"%{nombre}%",),
        ).fetchone()
    if row is None:
        return fail(f"No encontré '{nombre}' en el menú.")
    return ok(row)


def get_producto_by_id(producto_id: int) -> Result:
    """Busca un producto disponible por id exacto (revalidación antes de confirmar)."""
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT id, nombre, categoria, precio
            FROM productos
            WHERE id = %s AND disponible IS TRUE
            """,
            (producto_id,),
        ).fetchone()
    if row is None:
        return fail("Ese producto ya no está disponible.")
    return ok(row)


# --- Clientes --------------------------------------------------------------

def find_or_create_cliente(
    telegram_chat_id: str,
    nombre: str = "",
    telefono: str = "",
    direccion: str = "",
) -> Result:
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT id, telefono, direccion FROM clientes WHERE telegram_chat_id = %s",
            (telegram_chat_id,),
        ).fetchone()

        if row is None:
            created = conn.execute(
                """
                INSERT INTO clientes (nombre, telefono, direccion, telegram_chat_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (nombre or "Cliente Telegram", telefono, direccion, telegram_chat_id),
            ).fetchone()
            return ok(created["id"])

        updates: dict[str, str] = {}
        if telefono and telefono != row["telefono"]:
            updates["telefono"] = telefono
        if direccion and direccion != row["direccion"]:
            updates["direccion"] = direccion
        if updates:
            set_clause = ", ".join(f"{col} = %s" for col in updates)
            conn.execute(
                f"UPDATE clientes SET {set_clause} WHERE id = %s",
                (*updates.values(), row["id"]),
            )
        return ok(row["id"])


def _resolver_cliente_por_nombre(nombre_parcial: str) -> Result:
    """Resuelve un nombre (parcial) a un único cliente_id. Nunca adivina."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT id, nombre, telefono FROM clientes WHERE nombre ILIKE %s ORDER BY nombre LIMIT 20",
            (f"%{nombre_parcial}%",),
        ).fetchall()

    if not rows:
        return fail(f"No encontré ningún cliente llamado '{nombre_parcial}'.")
    if len(rows) > 1:
        listado = ", ".join(f"{r['nombre']} ({r['telefono']})" for r in rows)
        return fail(
            f"Hay varios clientes que coinciden con '{nombre_parcial}': {listado}. "
            "Pedile a la dueña que aclare cuál."
        )
    return ok(rows[0]["id"])


# --- Pedidos -----------------------------------------------------------

def crear_pedido(cliente_id: int, items: list[dict]) -> Result:
    """items: [{"producto_id": int, "cantidad": int, "precio_unitario": int}, ...]"""
    with get_pool().connection() as conn:
        with conn.transaction():
            productos_ids = [item["producto_id"] for item in items]
            disponibles = {
                row["id"]: row["precio"]
                for row in conn.execute(
                    "SELECT id, precio FROM productos WHERE id = ANY(%s) AND disponible IS TRUE",
                    (productos_ids,),
                ).fetchall()
            }
            for item in items:
                precio_actual = disponibles.get(item["producto_id"])
                if precio_actual is None or precio_actual != item["precio_unitario"]:
                    return fail("Uno de los productos ya no está disponible. Por favor, revisá tu pedido.")

            total = sum(item["cantidad"] * item["precio_unitario"] for item in items)

            pedido = conn.execute(
                "INSERT INTO pedidos (cliente_id, total, estado) VALUES (%s, %s, 'pendiente') RETURNING id",
                (cliente_id, total),
            ).fetchone()
            pedido_id = pedido["id"]
            for item in items:
                conn.execute(
                    """
                    INSERT INTO pedido_detalles (pedido_id, producto_id, cantidad, precio_unitario)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (pedido_id, item["producto_id"], item["cantidad"], item["precio_unitario"]),
                )
            conn.execute(
                """
                UPDATE clientes
                SET cantidad_pedidos = cantidad_pedidos + 1,
                    total_gastado = total_gastado + %s,
                    ultimo_pedido = CURRENT_DATE
                WHERE id = %s
                """,
                (total, cliente_id),
            )
    return ok({"pedido_id": pedido_id, "total": total})


def track_pedido(telegram_chat_id: str, pedido_id: Optional[int] = None) -> Result:
    """Solo devuelve pedidos del cliente dueño de `telegram_chat_id` (nunca de otro chat)."""
    with get_pool().connection() as conn:
        if pedido_id is not None:
            pedido = conn.execute(
                """
                SELECT p.id, p.fecha, p.total, p.estado, p.cliente_id
                FROM pedidos p
                JOIN clientes c ON c.id = p.cliente_id
                WHERE p.id = %s AND c.telegram_chat_id = %s
                """,
                (pedido_id, telegram_chat_id),
            ).fetchone()
        else:
            pedido = conn.execute(
                """
                SELECT p.id, p.fecha, p.total, p.estado, p.cliente_id
                FROM pedidos p
                JOIN clientes c ON c.id = p.cliente_id
                WHERE c.telegram_chat_id = %s
                ORDER BY p.fecha DESC
                LIMIT 1
                """,
                (telegram_chat_id,),
            ).fetchone()

        if pedido is None:
            return fail("No encontré ningún pedido tuyo con esos datos.")

        pedido["items"] = conn.execute(
            """
            SELECT pr.nombre, pd.cantidad, pd.precio_unitario
            FROM pedido_detalles pd
            JOIN productos pr ON pr.id = pd.producto_id
            WHERE pd.pedido_id = %s
            """,
            (pedido["id"],),
        ).fetchall()
        return ok(pedido)


def actualizar_estado_pedido(pedido_id: int, nuevo_estado: str) -> Result:
    if nuevo_estado not in config.ESTADOS_PEDIDO_VALIDOS:
        validos = ", ".join(sorted(config.ESTADOS_PEDIDO_VALIDOS))
        return fail(f"Estado inválido: '{nuevo_estado}'. Válidos: {validos}.")

    with get_pool().connection() as conn:
        cursor = conn.execute(
            "UPDATE pedidos SET estado = %s WHERE id = %s",
            (nuevo_estado, pedido_id),
        )
        if cursor.rowcount == 0:
            return fail(f"No encontré el pedido #{pedido_id}.")
        return ok(None)


# --- Consultas administrativas (solo para la dueña, ver tools/admin_tools.py) ---

def obtener_resumen_cliente(nombre: str) -> Result:
    resuelto = _resolver_cliente_por_nombre(nombre)
    if not resuelto.success:
        return resuelto
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT nombre, total_gastado, cantidad_pedidos FROM clientes WHERE id = %s",
            (resuelto.data,),
        ).fetchone()
    return ok(row)


def obtener_ultimo_pedido_por_nombre(nombre: str) -> Result:
    resuelto = _resolver_cliente_por_nombre(nombre)
    if not resuelto.success:
        return resuelto
    cliente_id = resuelto.data
    with get_pool().connection() as conn:
        cliente = conn.execute("SELECT nombre FROM clientes WHERE id = %s", (cliente_id,)).fetchone()
        pedido = conn.execute(
            "SELECT id, fecha, total, estado FROM pedidos WHERE cliente_id = %s ORDER BY fecha DESC LIMIT 1",
            (cliente_id,),
        ).fetchone()
        if pedido is None:
            return fail(f"{cliente['nombre']} todavía no tiene pedidos.")
        items = conn.execute(
            """
            SELECT pr.nombre, pd.cantidad, pd.precio_unitario
            FROM pedido_detalles pd
            JOIN productos pr ON pr.id = pd.producto_id
            WHERE pd.pedido_id = %s
            """,
            (pedido["id"],),
        ).fetchall()
    return ok(
        {
            "cliente_nombre": cliente["nombre"],
            "pedido_id": pedido["id"],
            "fecha": pedido["fecha"],
            "estado": pedido["estado"],
            "items": items,
            "total": pedido["total"],
        }
    )


def obtener_historial_por_nombre(nombre: str) -> Result:
    resuelto = _resolver_cliente_por_nombre(nombre)
    if not resuelto.success:
        return resuelto
    cliente_id = resuelto.data
    with get_pool().connection() as conn:
        cliente = conn.execute("SELECT nombre FROM clientes WHERE id = %s", (cliente_id,)).fetchone()
        pedidos = conn.execute(
            "SELECT id, fecha, total, estado FROM pedidos WHERE cliente_id = %s ORDER BY fecha DESC",
            (cliente_id,),
        ).fetchall()
    return ok({"cliente_nombre": cliente["nombre"], "pedidos": pedidos})


def obtener_clientes_frecuentes(limite: int = 10, ordenar_por: str = "pedidos") -> Result:
    columna = "total_gastado" if ordenar_por == "gasto" else "cantidad_pedidos"
    with get_pool().connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, nombre, telefono, cantidad_pedidos, total_gastado
            FROM clientes
            ORDER BY {columna} DESC
            LIMIT %s
            """,
            (limite,),
        ).fetchall()
    return ok(rows)
