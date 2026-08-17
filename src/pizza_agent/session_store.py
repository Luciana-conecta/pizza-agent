"""Borrador de pedido en memoria, con respaldo en disco.

El Flow de CrewAI es sin estado entre mensajes (se crea una instancia nueva
por cada `kickoff`), pero armar un pedido de pizza requiere varios mensajes
seguidos. Este módulo guarda ese estado intermedio por chat de Telegram,
fuera del Flow, para que el LLM nunca sea quien decide cuándo "confirmar"
de verdad un pedido (ver `main.py` y `telegram_bot.py`).
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

DEFAULT_SESSIONS_FILE = Path("data/sessions.json")
DEFAULT_MAX_AGE_MINUTES = 30


class OrderItem(BaseModel):
    producto_id: int
    nombre: str
    cantidad: int
    precio_unitario: int


class OrderDraft(BaseModel):
    chat_id: str
    items: list[OrderItem] = Field(default_factory=list)
    cliente_nombre: str = ""
    telefono: str = ""
    direccion: str = ""
    status: Literal["building", "awaiting_confirmation", "submitted"] = "building"
    last_bot_message: str = ""
    updated_at: float = 0.0

    @property
    def total(self) -> int:
        return sum(item.cantidad * item.precio_unitario for item in self.items)


class SessionStore:
    """Guarda un `OrderDraft` por chat_id, persistido a un único archivo JSON."""

    def __init__(self, path: Path = DEFAULT_SESSIONS_FILE):
        self._path = path
        self._drafts: dict[str, OrderDraft] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._drafts = {chat_id: OrderDraft(**data) for chat_id, data in raw.items()}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {chat_id: draft.model_dump() for chat_id, draft in self._drafts.items()}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, chat_id: str) -> Optional[OrderDraft]:
        return self._drafts.get(chat_id)

    def get_or_create(self, chat_id: str) -> OrderDraft:
        draft = self._drafts.get(chat_id)
        if draft is None:
            draft = OrderDraft(chat_id=chat_id, updated_at=time.time())
            self._drafts[chat_id] = draft
            self._persist()
        return draft

    def save(self, draft: OrderDraft) -> None:
        draft.updated_at = time.time()
        self._drafts[draft.chat_id] = draft
        self._persist()

    def delete(self, chat_id: str) -> None:
        if chat_id in self._drafts:
            del self._drafts[chat_id]
            self._persist()

    def get_lock(self, chat_id: str) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock

    def expire_stale(self, max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES) -> list[str]:
        """Limpia borradores abandonados. Devuelve los chat_ids expirados."""
        cutoff = time.time() - max_age_minutes * 60
        expired = [
            chat_id
            for chat_id, draft in self._drafts.items()
            if draft.status in ("building", "awaiting_confirmation") and draft.updated_at < cutoff
        ]
        for chat_id in expired:
            del self._drafts[chat_id]
        if expired:
            self._persist()
        return expired


# Instancia única compartida por todo el proceso del bot.
session_store = SessionStore()
