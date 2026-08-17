"""Resultado estructurado y predecible para las funciones de `db.py`.

Nunca cruza al LLM como JSON: las tools lo consumen internamente (chequean
`.success` y pasan `.data` a un formatter, o devuelven `.error` tal cual) y
siempre terminan devolviendo un string ya formateado. Ver `formatters.py`.
"""

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    error: Optional[str] = None


def ok(data=None) -> Result:
    return Result(success=True, data=data, error=None)


def fail(error: str) -> Result:
    return Result(success=False, data=None, error=error)
