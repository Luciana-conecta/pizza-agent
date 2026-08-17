from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .. import config, db


class MenuLookupInput(BaseModel):
    categoria: str = Field(
        default="",
        description="Categoría a filtrar (ej. 'pizza', 'bebida'). Vacío para traer todo el menú.",
    )


class MenuLookupTool(BaseTool):
    name: str = "consultar_menu"
    description: str = (
        "Devuelve los productos disponibles del menú, con nombre, categoría, descripción y "
        "precio. Usalo para responder cualquier pregunta sobre qué pizzas/productos hay, "
        "ingredientes o precios. No inventes productos ni precios que no aparezcan acá."
    )
    args_schema: Type[BaseModel] = MenuLookupInput

    def _run(self, categoria: str = "") -> str:
        productos = db.get_menu()
        if categoria:
            productos = [p for p in productos if categoria.lower() in p["categoria"].lower()]
        if not productos:
            return "No hay productos disponibles en esa categoría."
        lineas = [
            f"- {p['nombre']} ({p['categoria']}): {p['descripcion'] or 'sin descripción'} — Gs. {p['precio']}"
            for p in productos
        ]
        return "\n".join(lineas)


class InfoLookupInput(BaseModel):
    pass


class InfoLookupTool(BaseTool):
    name: str = "consultar_info_local"
    description: str = "Devuelve horarios, dirección, zona y costo de delivery, y teléfono del local."
    args_schema: Type[BaseModel] = InfoLookupInput

    def _run(self) -> str:
        return (
            f"Horarios: {config.HORARIOS}\n"
            f"Dirección: {config.DIRECCION}\n"
            f"Zona de delivery: {config.ZONA_DELIVERY}\n"
            f"Costo de delivery: {config.COSTO_DELIVERY}\n"
            f"Teléfono: {config.TELEFONO_LOCAL}"
        )
