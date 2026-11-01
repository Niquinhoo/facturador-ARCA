from __future__ import annotations

import asyncio
import io
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

from . import __version__
from .api import CalmaApi, CalmaApiError
from .arca import ArcaError, ArcaService
from .config import Settings
from .gemini import GeminiInterpreter, Intent
from .pdf import render_voucher_pdf

logger = logging.getLogger(__name__)


def _display_name(user: Any) -> str:
    return " ".join(part for part in (user.first_name, user.last_name) if part).strip()


def _preview_text(action: dict[str, Any]) -> str:
    preview = action.get("preview") or {}
    lines = preview.get("lines") or []
    body = "\n".join(f"• {line}" for line in lines)
    return f"🧾 {preview.get('title', 'Vista previa')}\n\n{body}\n\n¿Confirmás esta operación?"


def _event_text(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    actor = event.get("actor") or {}
    actor_name = " ".join(
        part for part in (actor.get("name"), actor.get("surname")) if part
    ) or str(payload.get("actorUserId") or "sistema")
    return (
        f"🔔 {payload.get('summary', event.get('topic', 'Cambio operativo'))}\n"
        f"Actor: {actor_name} · Canal: {payload.get('channel', '-')}\n"
        f"Fecha: {payload.get('occurredAt', event.get('createdAt', '-'))}\n"
        f"Auditoría: {payload.get('auditId', '-')}"
    )


class SuperBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.api = CalmaApi(settings.calma_api_url, settings.bot_service_token)
        self.gemini = GeminiInterpreter(settings.gemini_api_key, settings.gemini_model)
        self.arca = ArcaService(settings)
        self.fiscal_lock = asyncio.Lock()
        self.application: Application | None = None

    async def _api(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(func, *args, **kwargs)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await update.effective_message.reply_text(
            "Hola. Soy el asistente operativo de Calma.\n\n"
            "Primero vinculá tu cuenta desde Calma y enviame:\n"
            "/vincular 12345678\n\n"
            "Después podés consultar stock y rendiciones o proponer cambios por texto o audio."
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await update.effective_message.reply_text(
            "Podés escribir, por ejemplo:\n"
            "• ¿Cuánto stock queda de remeras negras?\n"
            "• Sumá 3 unidades al stock 42 por devolución.\n"
            "• Prepará una Factura C por $25.000 a consumidor final.\n"
            "• Mostrame mis rendiciones pendientes.\n\n"
            "Toda escritura muestra un preview y solo se ejecuta al confirmar."
        )

    async def link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) != 1:
            await update.effective_message.reply_text("Uso: /vincular CODIGO")
            return
        user = update.effective_user
        chat = update.effective_chat
        try:
            result = await self._api(
                self.api.link, context.args[0], user.id, chat.id,
                user.username, _display_name(user),
            )
            linked = result["user"]
            await update.effective_message.reply_text(
                f"✅ Cuenta vinculada con {linked['name']} ({linked['role']})."
            )
        except CalmaApiError as exc:
            await update.effective_message.reply_text(f"No pude vincularla: {exc}")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        try:
            identity = await self._api(self.api.identity, update.effective_user.id)
            mode = "producción" if self.settings.arca_production else "homologación"
            await update.effective_message.reply_text(
                f"✅ Vinculado como {identity['name']} ({identity['role']}).\n"
                f"ARCA: {mode} · Worker: {self.settings.worker_id}"
            )
        except CalmaApiError as exc:
            await update.effective_message.reply_text(f"Estado no disponible: {exc}")

    async def _interpret(self, update: Update, text: str | None = None,
                         audio: bytes | None = None, mime_type: str = "audio/ogg") -> None:
        message = update.effective_message
        user = update.effective_user
        try:
            await message.chat.send_action(ChatAction.TYPING)
            current = await self._api(self.api.context, user.id, (text or "")[:120])
            intent: Intent = await self._api(
                self.gemini.interpret, current, text, audio, mime_type
            )
            if intent.mode != "action":
                await message.reply_text(intent.message)
                return
            action = await self._api(
                self.api.preview, user.id, update.effective_chat.id,
                intent.kind, intent.payload, f"tg-{update.update_id}-{uuid.uuid4().hex[:8]}",
            )
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirmar", callback_data=f"ok:{action['id']}"),
                InlineKeyboardButton("Cancelar", callback_data=f"no:{action['id']}"),
            ]])
            await message.reply_text(_preview_text(action), reply_markup=keyboard)
        except CalmaApiError as exc:
            if exc.details and isinstance(exc.details, dict) and exc.details.get("candidates"):
                candidates = "\n".join(
                    f"• ID {row['stockProductoId']}: {row['label']} ({row['cantidad']})"
                    for row in exc.details["candidates"]
                )
                await message.reply_text(f"{exc}\n\n{candidates}")
            elif exc.status_code == 401:
                await message.reply_text("Tu cuenta no está vinculada. Generá un código en Calma y usá /vincular CODIGO.")
            else:
                await message.reply_text(f"No pude preparar la operación: {exc}")
        except Exception:
            logger.exception("Fallo interpretando el pedido")
            await message.reply_text("No pude interpretar el pedido. Probá de nuevo con más detalle.")

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._interpret(update, text=update.effective_message.text)

    async def voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        voice = update.effective_message.voice or update.effective_message.audio
        if voice.file_size and voice.file_size > 19 * 1024 * 1024:
            await update.effective_message.reply_text("El audio es demasiado grande; el límite es 19 MB.")
            return
        await update.effective_message.chat.send_action(ChatAction.TYPING)
        telegram_file = await context.bot.get_file(voice.file_id)
        buffer = io.BytesIO()
        await telegram_file.download_to_memory(buffer)
        mime_type = getattr(voice, "mime_type", None) or "audio/ogg"
        await self._interpret(update, audio=buffer.getvalue(), mime_type=mime_type)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        query = update.callback_query
        await query.answer()
        verb, action_id = query.data.split(":", 1)
        try:
            if verb == "no":
                await self._api(self.api.cancel, action_id, query.from_user.id)
                await query.edit_message_text("Operación cancelada.")
                return
            result = await self._api(self.api.confirm, action_id, query.from_user.id)
            action_result = result.get("result") or {}
            if action_result.get("jobId"):
                text = "✅ Solicitud confirmada. La emisión fiscal quedó en cola; te aviso cuando ARCA responda."
            else:
                text = "✅ Operación confirmada y registrada en Calma."
            await query.edit_message_text(text)
        except CalmaApiError as exc:
            await query.edit_message_text(f"No se pudo confirmar: {exc}")

    async def fiscal_worker(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.fiscal_lock.locked():
            return
        async with self.fiscal_lock:
            job = None
            try:
                job = await self._api(self.api.claim_job, self.settings.worker_id)
                if not job:
                    return
                payload = job.get("payload") or {}
                prepared = job.get("result") or {}
                expected_number = prepared.get("expectedCbteNro")

                def persist_preparation(number: int, request: dict[str, Any]) -> None:
                    self.api.prepare_job(
                        job["id"], self.settings.worker_id, number, request
                    )

                emission = await self._api(
                    self.arca.emit, payload, expected_number,
                    None if expected_number else persist_preparation,
                )
                result = dict(emission.result)
                result["reconciled"] = emission.reconciled
                documents = self.settings.data_dir / "documents"
                filename = (
                    f"{'NC-C' if int(payload['cbteTipo']) == 13 else 'Factura-C'}-"
                    f"{result['ptoVta']:05d}-{result['cbteNro']:08d}.pdf"
                )
                path = await self._api(
                    render_voucher_pdf, documents / filename,
                    self.settings.afip_cuit, payload, result,
                )
                result["pdfUrl"] = f"private://documents/{filename}"
                await self._api(
                    self.api.complete_job, job["id"], self.settings.worker_id, result
                )
                creator_link = job.get("voucher", {}).get("createdBy", {}).get("telegramLink")
                for link in [creator_link] if creator_link else []:
                    with Path(path).open("rb") as document:
                        await context.bot.send_document(
                            chat_id=int(link["chatId"]), document=document, filename=filename,
                            caption=f"✅ Comprobante autorizado. CAE {result['cae']}",
                        )
            except ArcaError as exc:
                logger.exception("Fallo fiscal ARCA")
                if job:
                    await self._api(
                        self.api.fail_job, job["id"], self.settings.worker_id,
                        str(exc), exc.retryable,
                    )
            except Exception as exc:
                logger.exception("Fallo inesperado del worker fiscal")
                if job:
                    try:
                        await self._api(
                            self.api.fail_job, job["id"], self.settings.worker_id,
                            str(exc), True,
                        )
                    except Exception:
                        logger.exception("No se pudo reportar el fallo fiscal a Calma")

    async def event_worker(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            events = await self._api(self.api.claim_events, self.settings.worker_id, 20)
            for event in events:
                try:
                    recipients = event.get("recipients") or []
                    if not recipients:
                        raise RuntimeError("No hay administradores vinculados a Telegram.")
                    for recipient in recipients:
                        await context.bot.send_message(
                            chat_id=int(recipient["chatId"]), text=_event_text(event)
                        )
                    await self._api(self.api.ack_event, str(event["id"]), self.settings.worker_id)
                except Exception as exc:
                    await self._api(
                        self.api.fail_event, str(event["id"]), self.settings.worker_id, str(exc)
                    )
        except Exception:
            logger.exception("Fallo entregando eventos del outbox")

    async def heartbeat(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        try:
            await self._api(
                self.api.heartbeat, self.settings.worker_id, __version__,
                self.settings.arca_production,
                {"geminiModel": self.settings.gemini_model, "pid": os.getpid()},
            )
            marker = self.settings.data_dir / "last_heartbeat"
            marker.touch()
        except Exception:
            logger.exception("No se pudo informar heartbeat")

    def build(self) -> Application:
        application = ApplicationBuilder().token(self.settings.telegram_token).build()
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("ayuda", self.help))
        application.add_handler(CommandHandler("vincular", self.link))
        application.add_handler(CommandHandler("estado", self.status))
        application.add_handler(CallbackQueryHandler(self.callback, pattern=r"^(ok|no):"))
        application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.voice))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text))
        application.job_queue.run_repeating(
            self.fiscal_worker, interval=self.settings.fiscal_poll_seconds, first=3,
            name="fiscal-worker", job_kwargs={"max_instances": 1, "coalesce": True},
        )
        application.job_queue.run_repeating(
            self.event_worker, interval=self.settings.event_poll_seconds, first=5,
            name="event-worker", job_kwargs={"max_instances": 1, "coalesce": True},
        )
        application.job_queue.run_repeating(
            self.heartbeat, interval=self.settings.heartbeat_seconds, first=1,
            name="heartbeat", job_kwargs={"max_instances": 1, "coalesce": True},
        )
        self.application = application
        return application

    def run(self) -> None:
        self.build().run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)


def run() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    SuperBot(settings).run()
