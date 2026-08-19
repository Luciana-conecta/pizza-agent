"""Punto único para decidir quién es la dueña de la pizzería.

Antes esto se chequeaba comparando `chat_id == OWNER_TELEGRAM_CHAT_ID` por
separado en `telegram_bot.py` (dos veces, con mensajes de error distintos) y
en `main.py`. Centralizarlo acá evita que una futura regla de autorización
(ej. más de un chat dueño) quede desincronizada entre esos lugares.
"""

import os


def owner_chat_id() -> str | None:
    return os.getenv("OWNER_TELEGRAM_CHAT_ID")


def is_owner(chat_id: str) -> bool:
    return chat_id == owner_chat_id()
