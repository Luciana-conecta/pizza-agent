"""Bot de Telegram: la interfaz del agente de la pizzería.

Proceso long-running (modo polling). Cada mensaje de texto crea una
instancia nueva de PizzaAgentFlow (igual patrón que request_router: un
Flow por invocación); la continuidad de un pedido de varios mensajes vive
en `session_store`, no en el Flow.
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import authorization, db, formatters, order_validation
from .main import PizzaAgentFlow
from .session_store import session_store

logger = logging.getLogger(__name__)

SESSION_EXPIRY_CHECK_SECONDS = 600


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "¡Hola! Soy el asistente de la pizzería. Preguntame por el menú, los horarios, "
        "hacé tu pedido, o consultá el estado de uno que ya hiciste. Si querés reiniciar un "
        "pedido a medias, escribí /cancelar."
    )


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with session_store.get_lock(chat_id):
        draft = session_store.get(chat_id)
        if draft is None:
            await update.message.reply_text("No tenías ningún pedido en curso.")
            return
        try:
            draft.transition_to("cancelled")
        except ValueError:
            pass
        session_store.delete(chat_id)
        await update.message.reply_text("Listo, cancelé tu pedido en curso. Podés empezar de nuevo cuando quieras.")


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if not authorization.is_owner(chat_id):
        await update.message.reply_text("No autorizado.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Uso: /estado <pedido_id> <nuevo_estado>")
        return

    pedido_id_str, nuevo_estado = context.args
    if not pedido_id_str.isdigit():
        await update.message.reply_text("El pedido_id debe ser un número.")
        return

    result = await asyncio.to_thread(db.actualizar_estado_pedido, int(pedido_id_str), nuevo_estado)
    if result.success:
        await update.message.reply_text(f"Pedido #{pedido_id_str} actualizado a '{nuevo_estado}'.")
    else:
        await update.message.reply_text(result.error)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    is_owner = authorization.is_owner(chat_id)
    text = update.message.text

    async with session_store.get_lock(chat_id):
        existing_draft = session_store.get(chat_id)
        last_bot_message = existing_draft.last_bot_message if existing_draft else ""

        flow = PizzaAgentFlow()
        await asyncio.to_thread(
            flow.kickoff,
            inputs={
                "request": text,
                "chat_id": chat_id,
                "is_owner": is_owner,
                "last_bot_message": last_bot_message,
            },
        )

        draft = session_store.get(chat_id)
        if draft is not None:
            draft.last_bot_message = flow.state.response
            session_store.save(draft)

        if draft is not None and draft.status == "awaiting_confirmation":
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Confirmar pedido", callback_data=f"confirm:{chat_id}"),
                        InlineKeyboardButton("Cancelar", callback_data=f"cancel:{chat_id}"),
                    ]
                ]
            )
            await update.message.reply_text(flow.state.response, reply_markup=keyboard)
        else:
            await update.message.reply_text(flow.state.response)


async def on_confirm_or_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, _, chat_id = query.data.partition(":")
    await query.answer()

    async with session_store.get_lock(chat_id):
        draft = session_store.get(chat_id)
        if draft is None or draft.status != "awaiting_confirmation":
            await query.edit_message_text("Este pedido ya no está disponible.")
            return

        if action == "cancel":
            draft.transition_to("cancelled")
            session_store.delete(chat_id)
            await query.edit_message_text("Pedido cancelado.")
            return

        ok, errores = order_validation.validar_draft_para_confirmar(draft)
        if not ok:
            draft.transition_to("building")
            session_store.save(draft)
            await query.edit_message_text("Antes de confirmar, corregí esto:\n- " + "\n- ".join(errores))
            return

        try:
            cliente_resultado = await asyncio.to_thread(
                db.find_or_create_cliente, chat_id, draft.cliente_nombre, draft.telefono, draft.direccion
            )
            items = [
                {"producto_id": it.producto_id, "cantidad": it.cantidad, "precio_unitario": it.precio_unitario}
                for it in draft.items
            ]
            pedido_resultado = await asyncio.to_thread(db.crear_pedido, cliente_resultado.data, items)
        except Exception:
            logger.exception("Error al confirmar el pedido del chat %s", chat_id)
            await query.edit_message_text(
                "Hubo un problema guardando tu pedido. Por favor, intentá confirmar de nuevo en un momento."
            )
            return

        if not pedido_resultado.success:
            draft.transition_to("building")
            session_store.save(draft)
            await query.edit_message_text(pedido_resultado.error)
            return

        draft.transition_to("submitted")
        mensaje = formatters.format_order_confirmation(pedido_resultado.data["pedido_id"], draft)
        session_store.delete(chat_id)
        await query.edit_message_text(mensaje)


async def expire_stale_sessions(context: ContextTypes.DEFAULT_TYPE) -> None:
    expired = session_store.expire_stale()
    if expired:
        logger.info("Sesiones de pedido expiradas: %s", expired)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CallbackQueryHandler(on_confirm_or_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    if app.job_queue is not None:
        app.job_queue.run_repeating(expire_stale_sessions, interval=SESSION_EXPIRY_CHECK_SECONDS, first=SESSION_EXPIRY_CHECK_SECONDS)

    app.run_polling()


if __name__ == "__main__":
    main()
