from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .. import config, db, formatters


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
        result = db.get_menu()
        productos = result.data
        if categoria:
            productos = [p for p in productos if categoria.lower() in p["categoria"].lower()]
        return formatters.format_menu(productos)


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
