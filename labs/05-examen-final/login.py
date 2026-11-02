"""Servicio de inicio de sesión: une sesiones, prueba de vida, verificación 1:1 y auditoría.

No sabe nada de Telegram: bot.py le pasa el telegram_user_id y la ruta del mp4 ya
descargado. Así el flujo completo de /login se puede probar sin el canal.

Al usuario siempre se le responde con un mensaje GENÉRICO; el motivo real del rechazo
solo queda en la auditoría (no darle pistas al atacante sobre qué verificación falló).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from auditoria import CAMPOS_PROHIBIDOS, CAMPOS_RESERVADOS, Auditoria
from face import MotorFacial, cargar_enrolados
from liveness import verificar
from sesion import Sesion
from sesiones import Reto, SessionStore
from usuarios import Usuario, buscar_por_telegram

logger = logging.getLogger(__name__)

MENSAJE_RECHAZO = "No fue posible verificar tu identidad. Puedes intentarlo de nuevo con /login."
MENSAJE_BLOQUEO = "Demasiados intentos fallidos. El inicio de sesión está bloqueado temporalmente."
MENSAJE_NO_REGISTRADO = "Esta cuenta de Telegram no está registrada en el curso."
TOLERANCIA_RELOJ = timedelta(seconds=2)  # la hora de Telegram tiene resolución de segundos


@dataclass
class ResultadoLogin:
    ok: bool
    mensaje: str  # lo que se le muestra al usuario
    sesion: Sesion | None = None


class ServicioLogin:
    def __init__(
        self,
        store: SessionStore,
        usuarios: list[Usuario],
        motor: MotorFacial | None,
        rostros_dir: Path,
        umbral: float,
        auditoria: Auditoria,
    ):
        self.store = store
        self.usuarios = usuarios
        self.motor = motor
        self.rostros_dir = rostros_dir
        self.umbral = umbral
        self.auditoria = auditoria

    def iniciar(self, uid: int) -> tuple[Reto | None, str | None]:
        """/login: (reto, None) o (None, mensaje de error para el usuario)."""
        if self.store.bloqueado(uid):
            self.auditoria.registrar("login_rechazado", uid, motivo="bloqueado")
            return None, f"{MENSAJE_BLOQUEO} Espera {self.store.minutos_bloqueo(uid):.0f} min."
        usuario = buscar_por_telegram(self.usuarios, uid)
        if usuario is None:
            self.auditoria.registrar("login_rechazado", uid, motivo="usuario_no_registrado")
            return None, MENSAJE_NO_REGISTRADO
        reto = self.store.emitir_reto(uid)
        self.auditoria.registrar("login_reto_emitido", uid, motivo=reto.nombre)
        return reto, None

    def _rechazar(self, uid: int, motivo: str, similitud: float | None = None, **extra) -> ResultadoLogin:
        # Primero se cuenta el fallo: aunque la auditoría fallara, el intento suma al bloqueo.
        quedo_bloqueado = self.store.registrar_fallo(uid)
        try:
            self.auditoria.registrar("login_rechazado", uid, motivo=motivo, similitud=similitud, **extra)
            if quedo_bloqueado:
                self.auditoria.registrar("login_rechazado", uid, motivo="bloqueado")
        except Exception:
            logger.exception("No se pudo auditar el rechazo de %s (%s)", uid, motivo)
        return ResultadoLogin(False, MENSAJE_BLOQUEO if quedo_bloqueado else MENSAJE_RECHAZO)

    def rechazar_entrada(self, uid: int, motivo: str) -> ResultadoLogin:
        """Para entradas que el canal descarta antes de analizar (reenviado, foto, video normal)."""
        if self.store.bloqueado(uid):
            return ResultadoLogin(False, MENSAJE_BLOQUEO)
        self.store.consumir_reto(uid)  # el reto pendiente queda gastado: no se puede reintentar con él
        return self._rechazar(uid, motivo)

    def verificar_video(self, uid: int, video: Path, enviado_en: datetime | None = None) -> ResultadoLogin:
        """Analiza la video-nota. `enviado_en` (hora del mensaje en Telegram) debe ser posterior al reto."""
        if self.store.bloqueado(uid):
            self.auditoria.registrar("login_rechazado", uid, motivo="bloqueado")
            return ResultadoLogin(False, MENSAJE_BLOQUEO)

        usuario = buscar_por_telegram(self.usuarios, uid)
        if usuario is None:
            self.auditoria.registrar("login_rechazado", uid, motivo="usuario_no_registrado")
            return ResultadoLogin(False, MENSAJE_NO_REGISTRADO)

        reto, motivo = self.store.consumir_reto(uid)
        if reto is None:
            return self._rechazar(uid, motivo or "sin_reto")
        if enviado_en is not None and enviado_en < reto.emitido_en - TOLERANCIA_RELOJ:
            return self._rechazar(uid, "video_anterior_al_reto")

        enrolados = cargar_enrolados(self.rostros_dir, usuario.id)
        if enrolados is None or self.motor is None:
            return self._rechazar(uid, "usuario_no_enrolado")

        try:
            resultado = verificar(self.motor, video, reto.nombre, enrolados, self.umbral)
        except Exception:
            logger.exception("Error analizando la video-nota de %s", uid)
            return self._rechazar(uid, "error_analisis")

        if not resultado.ok:
            return self._rechazar(
                uid, resultado.motivo, resultado.similitud, reto=reto.nombre, **_solo_numeros(resultado.detalles)
            )

        if self.store.bloqueado(uid):  # pudo quedar bloqueado mientras se analizaba el video
            self.auditoria.registrar("login_rechazado", uid, motivo="bloqueado", similitud=resultado.similitud)
            return ResultadoLogin(False, MENSAJE_BLOQUEO)
        sesion = self.store.login_ok(uid, usuario)
        self.auditoria.registrar(
            "login_aceptado",
            uid,
            motivo=reto.nombre,
            similitud=resultado.similitud,
            usuario_id=usuario.id,
            **_solo_numeros(resultado.detalles),
        )
        etiqueta = "estudiante" if usuario.rol == "estudiante" else "docente"
        return ResultadoLogin(True, f"Hola, {usuario.nombre} ({etiqueta}). Sesión iniciada.", sesion)


def _solo_numeros(detalles: dict) -> dict:
    """Solo métricas del análisis (yaw, crecimiento, continuidad): nada biométrico ni campos reservados."""
    excluidos = CAMPOS_RESERVADOS | CAMPOS_PROHIBIDOS
    return {k: v for k, v in detalles.items() if isinstance(v, (int, float, str)) and k not in excluidos}
