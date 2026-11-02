"""Adaptador del canal Telegram (long polling). Solo TRADUCE entre Telegram y el núcleo.

    Telegram ⇄ bot.py ⇄ nucleo.responder(pregunta, historial, sesion, confirm)
                      ⇄ login.ServicioLogin (video-nota → prueba de vida → sesión)

Aquí no hay reglas de negocio ni de seguridad: rol, permisos, filtros y verificación
facial viven en el núcleo y en login.py, que se prueban sin Telegram.

Por qué concurrent_updates(True): la confirmación con botones espera un CallbackQuery
DENTRO del handler del mensaje de texto. Si los updates se procesaran en secuencia, el
bot quedaría esperando un update que nunca procesa (deadlock).

Uso (desde la raíz del repositorio):
    uv run python labs/05-examen-final/bot.py [--modo agente|router]
"""

import asyncio
import json
import logging
import os
import secrets
import sys
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from auditoria import Auditoria
from config import AUDIT_FILE, AUTOR_ASISTENTE, ROSTROS_DIR, USUARIOS_FILE, load_security_settings
from face import MotorFacial
from llm_client import Message
from login import ServicioLogin
from nucleo import Nucleo, Respuesta, create_nucleo
from sesion import Sesion
from sesiones import SessionStore
from tools import Tool
from usuarios import cargar_usuarios

logger = logging.getLogger("bot")

MAX_HISTORIAL = 20  # mensajes (10 turnos) por chat
CONFIRM_TIMEOUT_SECONDS = 60
MAX_TELEGRAM = 4000  # Telegram corta en 4096 caracteres
MAX_SEGUNDOS_VIDEO = 60  # una video-nota de Telegram dura como máximo 60 s
MAX_BYTES_VIDEO = 20 * 1024 * 1024
PRIVADO = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE  # solo chats 1:1, sin ediciones

AYUDA = """Comandos:
/start – presentación
/quiensoy – tu telegram_user_id y el estado de tu sesión
/login – iniciar sesión con reconocimiento facial y prueba de vida (video-nota)
/logout – cerrar sesión
/traza – qué tools y documentos usó la última respuesta (para la demo)
/reiniciar – borrar el historial de este chat
/ayuda – esta ayuda

Sin sesión puedes preguntar lo público del curso. Lo restringido (notas, guías, casos) requiere /login."""


@dataclass
class EstadoBot:
    nucleo: Nucleo
    login: ServicioLogin
    store: SessionStore
    auditoria: Auditoria
    # Por USUARIO (no por chat): nada de una sesión anterior se mezcla con la siguiente.
    historiales: dict[int, list[Message]] = field(default_factory=dict)
    trazas: dict[int, str] = field(default_factory=dict)
    pendientes: dict[str, tuple[asyncio.Future, int]] = field(default_factory=dict)


def estado(context: ContextTypes.DEFAULT_TYPE) -> EstadoBot:
    return context.application.bot_data["estado"]


async def responder_largo(update: Update, texto: str) -> None:
    for i in range(0, len(texto), MAX_TELEGRAM):
        await update.effective_message.reply_text(texto[i : i + MAX_TELEGRAM])


def olvidar_conversacion(est: EstadoBot, uid: int) -> None:
    """Al cambiar de sesión (login, logout, expiración) se borra lo conversado: las respuestas
    restringidas de la sesión anterior no pueden volver como contexto para el LLM ni en /traza."""
    est.historiales.pop(uid, None)
    est.trazas.pop(uid, None)


def sesion_vigente(est: EstadoBot, uid: int) -> tuple[Sesion, bool]:
    sesion, expiro = est.store.obtener(uid)
    if expiro:
        est.auditoria.registrar("sesion_expirada", uid)
        olvidar_conversacion(est, uid)
    return sesion, expiro


# --- Comandos ---------------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"¡Hola! Soy el Asistente del Curso de IA desarrollado por {AUTOR_ASISTENTE}.\n"
        "Puedo responder preguntas del curso (documentos, calendario, cálculos) y, si inicias sesión "
        "con tu rostro, consultar tu información académica según tu rol.\n\n" + AYUDA
    )


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(AYUDA)


async def cmd_quiensoy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    est = estado(context)
    uid = update.effective_user.id
    sesion, _ = sesion_vigente(est, uid)
    lineas = [f"telegram_user_id: {uid}"]
    if sesion.activa():
        lineas += [
            "sesión: ACTIVA",
            f"nombre: {sesion.nombre}",
            f"rol: {sesion.rol}",
            f"minutos restantes: {sesion.minutos_restantes():.1f}",
        ]
    else:
        lineas.append("sesión: sin iniciar (rol público)")
    if est.store.bloqueado(uid):
        lineas.append(f"login bloqueado: {est.store.minutos_bloqueo(uid):.1f} min")
    await update.message.reply_text("\n".join(lineas))


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    est = estado(context)
    uid = update.effective_user.id
    reto, error = est.login.iniciar(uid)
    if reto is None:
        await update.message.reply_text(error)
        return
    segundos = int(est.store.liveness_timeout_seconds)
    await update.message.reply_text(
        "🔐 Prueba de vida.\n"
        f"Envía una VIDEO-NOTA (el video circular de Telegram) en la que {reto.instruccion}.\n"
        f"Empieza mirando de frente a la cámara. Tienes {segundos} s. Solo se acepta una video-nota."
    )


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    est = estado(context)
    uid = update.effective_user.id
    olvidar_conversacion(est, uid)
    if est.store.logout(uid):
        est.auditoria.registrar("logout", uid)
        await update.message.reply_text("Sesión cerrada. Ahora solo tienes acceso público.")
    else:
        await update.message.reply_text("No tenías una sesión activa.")


async def cmd_traza(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    traza = estado(context).trazas.get(update.effective_user.id)
    await update.message.reply_text(f"Traza de la última respuesta:\n{traza}" if traza else "Aún no hay traza.")


async def cmd_reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    olvidar_conversacion(estado(context), update.effective_user.id)
    await update.message.reply_text("Historial borrado.")


# --- Login con video-nota ---------------------------------------------------------------------


async def on_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    est = estado(context)
    msg = update.message
    uid = update.effective_user.id

    if msg.forward_origin is not None:  # una video-nota reenviada no prueba que la persona esté ahí
        resultado = est.login.rechazar_entrada(uid, "reenviado")
        await msg.reply_text(resultado.mensaje)
        return

    nota = msg.video_note
    if (nota.duration or 0) > MAX_SEGUNDOS_VIDEO or (nota.file_size or 0) > MAX_BYTES_VIDEO:
        resultado = est.login.rechazar_entrada(uid, "video_demasiado_largo")
        await msg.reply_text(resultado.mensaje)
        return

    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    fd, ruta = tempfile.mkstemp(suffix=".mp4", prefix="vnota_")
    os.close(fd)
    try:
        archivo = await nota.get_file()
        await archivo.download_to_drive(ruta)
        resultado = await asyncio.to_thread(est.login.verificar_video, uid, Path(ruta), msg.date)
    finally:
        try:  # el video NUNCA se conserva
            os.remove(ruta)
        except OSError:
            logger.exception("No se pudo borrar el video temporal %s", ruta)
    if resultado.ok:
        olvidar_conversacion(est, uid)
    await msg.reply_text(resultado.mensaje)


async def on_media_no_valida(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fotos, videos normales o archivos: no sirven para iniciar sesión."""
    est = estado(context)
    uid = update.effective_user.id
    if est.store.tiene_reto_pendiente(uid):
        resultado = est.login.rechazar_entrada(uid, "no_es_video_nota")
        await update.message.reply_text(f"Solo se acepta una video-nota para iniciar sesión. {resultado.mensaje}")
    else:
        await update.message.reply_text("No proceso fotos ni videos. Para iniciar sesión usa /login.")


# --- Confirmación con botones (la ConfirmFn del Lab 04, en Telegram) --------------------------


def confirmacion_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    est = estado(context)
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    async def confirm(tool: Tool, arguments: dict[str, Any]) -> bool:
        clave = secrets.token_hex(6)
        futuro: asyncio.Future = asyncio.get_running_loop().create_future()
        est.pendientes[clave] = (futuro, uid)
        teclado = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Sí", callback_data=f"cf:{clave}:si"),
                    InlineKeyboardButton("❌ No", callback_data=f"cf:{clave}:no"),
                ]
            ]
        )
        argumentos = json.dumps(arguments, ensure_ascii=False)
        mensaje = await context.bot.send_message(
            chat_id,
            f"⚠️ El asistente quiere ejecutar una ACCIÓN:\n{tool.name}({argumentos})\n\n¿La autorizas?",
            reply_markup=teclado,
        )
        sesion_al_pedir, _ = est.store.obtener(uid)
        try:
            aprobado = await asyncio.wait_for(futuro, CONFIRM_TIMEOUT_SECONDS)
            sesion_ahora, _ = est.store.obtener(uid)
            if aprobado and sesion_ahora != sesion_al_pedir:  # logout o expiración mientras esperaba
                aprobado = False
                await mensaje.edit_text(f"{mensaje.text}\n\n⏱️ Tu sesión cambió: acción cancelada.")
        except asyncio.TimeoutError:
            aprobado = False
            await mensaje.edit_text(f"{mensaje.text}\n\n⏱️ Sin respuesta: acción cancelada.")
        finally:
            est.pendientes.pop(clave, None)
        return aprobado

    return confirm


async def on_boton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    est = estado(context)
    query = update.callback_query
    partes = (query.data or "").split(":")
    if len(partes) != 3 or partes[0] != "cf":
        await query.answer()
        return
    _, clave, decision = partes
    pendiente = est.pendientes.get(clave)
    if pendiente is None:
        await query.answer("Esta confirmación ya no está vigente.")
        return
    futuro, uid_esperado = pendiente
    if query.from_user.id != uid_esperado:  # solo quien pidió la acción puede autorizarla
        await query.answer("Solo quien pidió la acción puede confirmarla.", show_alert=True)
        return
    if not futuro.done():
        futuro.set_result(decision == "si")
    await query.answer()
    texto = "✅ Autorizada." if decision == "si" else "❌ Rechazada."
    await query.edit_message_text(f"{query.message.text}\n\n{texto}")


# --- Preguntas en texto -----------------------------------------------------------------------


async def on_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    est = estado(context)
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    pregunta = update.message.text.strip()

    sesion, expiro = sesion_vigente(est, uid)
    if expiro:
        await update.message.reply_text("⏱️ Tu sesión expiró. Para información restringida vuelve a usar /login.")

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    historial = est.historiales.setdefault(uid, [])
    confirm = confirmacion_telegram(update, context)
    respuesta: Respuesta = await est.nucleo.responder(pregunta, list(historial), sesion, confirm)
    est.trazas[uid] = respuesta.traza()
    logger.info(
        "uid=%s rol=%s tools=%s denegadas=%s fragmentos=%s",
        uid,
        respuesta.rol,
        respuesta.tools_llamadas,
        respuesta.tools_denegadas,
        [(f.source, f.nivel) for f in respuesta.fragmentos],
    )
    await responder_largo(update, respuesta.texto)

    if not respuesta.error:
        historial += [{"role": "user", "content": pregunta}, {"role": "assistant", "content": respuesta.texto}]
        del historial[:-MAX_HISTORIAL]


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Error no controlado en un handler", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("Ocurrió un error inesperado. Ya quedó registrado; intenta de nuevo.")
        except Exception:
            logger.exception("Tampoco se pudo avisar al usuario")


# --- Arranque ---------------------------------------------------------------------------------


def construir_app(token: str, est: EstadoBot) -> Application:
    app = Application.builder().token(token).concurrent_updates(True).build()
    app.bot_data["estado"] = est
    app.add_handler(CommandHandler("start", filters=PRIVADO, callback=cmd_start))
    app.add_handler(CommandHandler(["ayuda", "help"], filters=PRIVADO, callback=cmd_ayuda))
    app.add_handler(CommandHandler("quiensoy", filters=PRIVADO, callback=cmd_quiensoy))
    app.add_handler(CommandHandler("login", filters=PRIVADO, callback=cmd_login))
    app.add_handler(CommandHandler("logout", filters=PRIVADO, callback=cmd_logout))
    app.add_handler(CommandHandler("traza", filters=PRIVADO, callback=cmd_traza))
    app.add_handler(CommandHandler("reiniciar", filters=PRIVADO, callback=cmd_reiniciar))
    app.add_handler(CallbackQueryHandler(on_boton))
    app.add_handler(MessageHandler(PRIVADO & filters.VIDEO_NOTE, on_video_note))
    app.add_handler(MessageHandler(PRIVADO & (filters.PHOTO | filters.VIDEO | filters.Document.ALL), on_media_no_valida))
    app.add_handler(MessageHandler(PRIVADO & filters.TEXT & ~filters.COMMAND, on_texto))
    app.add_error_handler(on_error)
    return app


async def main() -> None:
    mode = "router" if "--modo" in sys.argv and "router" in sys.argv else "agente"
    seguridad = load_security_settings(require_token=True)
    auditoria = Auditoria(AUDIT_FILE)
    store = SessionStore(
        session_minutes=seguridad.session_minutes,
        liveness_timeout_seconds=seguridad.liveness_timeout_seconds,
        max_intentos=seguridad.login_max_attempts,
        lockout_minutes=seguridad.lockout_minutes,
    )
    try:
        motor = MotorFacial()
    except FileNotFoundError as exc:
        logger.error("%s  → /login no funcionará hasta descargar los modelos.", exc)
        motor = None
    servicio = ServicioLogin(
        store, cargar_usuarios(USUARIOS_FILE), motor, ROSTROS_DIR, seguridad.face_match_threshold, auditoria
    )

    async with AsyncExitStack() as stack:
        nucleo = await create_nucleo(stack, auditoria, mode=mode)
        app = construir_app(seguridad.telegram_bot_token, EstadoBot(nucleo, servicio, store, auditoria))
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            logger.info(
                "Bot en línea (modo %s, umbral %.3f, sesión %.0f min). Ctrl+C para detener.",
                mode,
                seguridad.face_match_threshold,
                seguridad.session_minutes,
            )
            try:
                await asyncio.Event().wait()
            finally:
                await app.updater.stop()
                await app.stop()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot detenido.")
    except ValueError as exc:
        print(f"[configuración] {exc}")
