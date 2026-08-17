"""Acceso a la base Postgres existente de la pizzería.

Las tablas (productos, clientes, pedidos, pedido_detalles) ya existían antes
de este proyecto. Este módulo no crea tablas, solo las consulta/actualiza.

Requiere la migración aditiva (una sola vez, ver README.md):
    ALTER TABLE clientes ADD COLUMN telegram_chat_id BIGINT UNIQUE;
"""

import os
import threading
import time
from typing import Optional

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

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


def get_menu(force_refresh: bool = False) -> list[dict]:
    global _menu_cache, _menu_cache_at
    if not force_refresh and _menu_cache and (time.time() - _menu_cache_at) < _MENU_CACHE_TTL_SECONDS:
        return _menu_cache
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
    return _menu_cache


def find_producto(nombre: str) -> Optional[dict]:
    """Busca un producto disponible por nombre (parcial, sin distinguir mayúsculas)."""
    with get_pool().connection() as conn:
        return conn.execute(
            """
            SELECT id, nombre, categoria, descripcion, precio
            FROM productos
            WHERE disponible IS TRUE AND nombre ILIKE %s
            ORDER BY nombre
            LIMIT 1
            """,
            (f"%{nombre}%",),
        ).fetchone()


# --- Clientes --------------------------------------------------------------

def find_or_create_cliente(
    telegram_chat_id: str,
    nombre: str = "",
    telefono: str = "",
    direccion: str = "",
) -> int:
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
            return created["id"]

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
        return row["id"]


# --- Pedidos -----------------------------------------------------------

def crear_pedido(cliente_id: int, items: list[dict]) -> int:
    """items: [{"producto_id": int, "cantidad": int, "precio_unitario": int}, ...]"""
    total = sum(item["cantidad"] * item["precio_unitario"] for item in items)
    with get_pool().connection() as conn:
        with conn.transaction():
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
    return pedido_id


def track_pedido(telegram_chat_id: str, pedido_id: Optional[int] = None) -> Optional[dict]:
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
            return None

        pedido["items"] = conn.execute(
            """
            SELECT pr.nombre, pd.cantidad, pd.precio_unitario
            FROM pedido_detalles pd
            JOIN productos pr ON pr.id = pd.producto_id
            WHERE pd.pedido_id = %s
            """,
            (pedido["id"],),
        ).fetchall()
        return pedido


def actualizar_estado_pedido(pedido_id: int, nuevo_estado: str) -> bool:
    with get_pool().connection() as conn:
        cursor = conn.execute(
            "UPDATE pedidos SET estado = %s WHERE id = %s",
            (nuevo_estado, pedido_id),
        )
        return cursor.rowcount > 0


# --- Consultas administrativas (solo para la dueña, ver tools/admin_tools.py) ---

def consultar_cliente(identificador: str) -> Optional[dict]:
    """Busca por teléfono exacto o por id interno."""
    with get_pool().connection() as conn:
        return conn.execute(
            "SELECT * FROM clientes WHERE telefono = %s OR id::text = %s LIMIT 1",
            (identificador, identificador),
        ).fetchone()


def consultar_cliente_por_nombre(nombre_parcial: str) -> list[dict]:
    with get_pool().connection() as conn:
        return conn.execute(
            "SELECT * FROM clientes WHERE nombre ILIKE %s ORDER BY nombre LIMIT 20",
            (f"%{nombre_parcial}%",),
        ).fetchall()


def consultar_total_cliente(cliente_id: int) -> Optional[dict]:
    with get_pool().connection() as conn:
        return conn.execute(
            "SELECT id, nombre, total_gastado, cantidad_pedidos FROM clientes WHERE id = %s",
            (cliente_id,),
        ).fetchone()


def consultar_ultimo_pedido(cliente_id: int) -> Optional[dict]:
    with get_pool().connection() as conn:
        pedido = conn.execute(
            "SELECT id, fecha, total, estado FROM pedidos WHERE cliente_id = %s ORDER BY fecha DESC LIMIT 1",
            (cliente_id,),
        ).fetchone()
        if pedido is None:
            return None
        pedido["items"] = conn.execute(
            """
            SELECT pr.nombre, pd.cantidad, pd.precio_unitario
            FROM pedido_detalles pd
            JOIN productos pr ON pr.id = pd.producto_id
            WHERE pd.pedido_id = %s
            """,
            (pedido["id"],),
        ).fetchall()
        return pedido


def consultar_pedido_cliente(cliente_id: int) -> list[dict]:
    with get_pool().connection() as conn:
        return conn.execute(
            "SELECT id, fecha, total, estado FROM pedidos WHERE cliente_id = %s ORDER BY fecha DESC",
            (cliente_id,),
        ).fetchall()


def consultar_clientes_frecuentes(limite: int = 10) -> list[dict]:
    with get_pool().connection() as conn:
        return conn.execute(
            """
            SELECT id, nombre, telefono, cantidad_pedidos, total_gastado
            FROM clientes
            ORDER BY cantidad_pedidos DESC
            LIMIT %s
            """,
            (limite,),
        ).fetchall()


def consultar_cliente_que_mas_compra() -> Optional[dict]:
    with get_pool().connection() as conn:
        return conn.execute(
            """
            SELECT id, nombre, telefono, total_gastado
            FROM clientes
            ORDER BY total_gastado DESC
            LIMIT 1
            """
        ).fetchone()
