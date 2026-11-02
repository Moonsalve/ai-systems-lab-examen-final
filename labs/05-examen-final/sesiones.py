"""Sesiones, retos de prueba de vida y bloqueo temporal (en memoria, por telegram_user_id).

  emitir_reto(uid)    → reto aleatorio (secrets.choice) con nonce y hora de emisión
  consumir_reto(uid)  → lo entrega UNA sola vez; rechaza si venció o si ya se usó
  login_ok(uid, u)    → crea la Sesion con rol, nombre y código de usuarios.json
  obtener(uid)        → la sesión vigente o una pública (si venció, la borra)
  registrar_fallo     → al llegar a LOGIN_MAX_ATTEMPTS, bloqueo temporal
  logout(uid)

El reloj es inyectable para probar caducidad y bloqueo sin esperar.
"""

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sesion import Sesion, ahora, sesion_publica
from usuarios import Usuario

RETOS: dict[str, str] = {
    "girar_izquierda": "gira la cabeza hacia tu IZQUIERDA y vuelve al frente",
    "girar_derecha": "gira la cabeza hacia tu DERECHA y vuelve al frente",
    "girar_izquierda_y_derecha": "gira la cabeza primero a tu IZQUIERDA y luego a tu DERECHA",
    "acercarse": "empieza lejos y ACERCA tu rostro a la cámara",
}
# "acercarse" está implementado, pero no se sortea por defecto: una FOTO acercada a la cámara
# también "crece", así que no prueba profundidad 3D (ver el informe, prueba de vida).
RETOS_ACTIVOS: tuple[str, ...] = ("girar_izquierda", "girar_derecha", "girar_izquierda_y_derecha")


@dataclass
class Reto:
    nombre: str
    nonce: str
    emitido_en: datetime
    usado: bool = False

    @property
    def instruccion(self) -> str:
        return RETOS[self.nombre]


@dataclass
class _Fallos:
    cantidad: int = 0
    bloqueado_hasta: datetime | None = None


@dataclass
class SessionStore:
    session_minutes: float
    liveness_timeout_seconds: float
    max_intentos: int
    lockout_minutes: float
    reloj: Callable[[], datetime] = ahora
    retos_activos: tuple[str, ...] = RETOS_ACTIVOS
    _sesiones: dict[int, Sesion] = field(default_factory=dict)
    _retos: dict[int, Reto] = field(default_factory=dict)
    _fallos: dict[int, _Fallos] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- Retos ----------------------------------------------------------------------------

    def emitir_reto(self, uid: int) -> Reto:
        """Reto nuevo (reemplaza cualquier reto anterior de ese usuario)."""
        reto = Reto(nombre=secrets.choice(self.retos_activos), nonce=secrets.token_hex(8), emitido_en=self.reloj())
        with self._lock:
            self._retos[uid] = reto
        return reto

    def consumir_reto(self, uid: int) -> tuple[Reto | None, str | None]:
        """Devuelve (reto, None) una sola vez; si no, (None, motivo): sin_reto | reto_usado | reto_vencido."""
        with self._lock:
            reto = self._retos.get(uid)
            if reto is None:
                return None, "sin_reto"
            if reto.usado:
                return None, "reto_usado"
            reto.usado = True  # un reto sirve una sola vez, pase lo que pase después
            edad = (self.reloj() - reto.emitido_en).total_seconds()
            if edad > self.liveness_timeout_seconds:
                return None, "reto_vencido"
            return reto, None

    def tiene_reto_pendiente(self, uid: int) -> bool:
        reto = self._retos.get(uid)
        return reto is not None and not reto.usado

    # --- Bloqueo --------------------------------------------------------------------------

    def bloqueado(self, uid: int) -> bool:
        with self._lock:
            fallos = self._fallos.get(uid)
            if fallos is None or fallos.bloqueado_hasta is None:
                return False
            if self.reloj() >= fallos.bloqueado_hasta:
                self._fallos.pop(uid, None)  # terminó el bloqueo: empieza de cero
                return False
            return True

    def minutos_bloqueo(self, uid: int) -> float:
        fallos = self._fallos.get(uid)
        if fallos is None or fallos.bloqueado_hasta is None:
            return 0.0
        return max(0.0, (fallos.bloqueado_hasta - self.reloj()).total_seconds() / 60)

    def registrar_fallo(self, uid: int) -> bool:
        """Cuenta un intento fallido. Devuelve True si con este fallo quedó bloqueado."""
        with self._lock:
            fallos = self._fallos.setdefault(uid, _Fallos())
            fallos.cantidad += 1
            if fallos.cantidad >= self.max_intentos:
                fallos.bloqueado_hasta = self.reloj() + timedelta(minutes=self.lockout_minutes)
                return True
            return False

    def intentos_restantes(self, uid: int) -> int:
        fallos = self._fallos.get(uid)
        return self.max_intentos - (fallos.cantidad if fallos else 0)

    # --- Sesiones -------------------------------------------------------------------------

    def login_ok(self, uid: int, usuario: Usuario) -> Sesion:
        """Rol, nombre y código salen de usuarios.json (el objeto Usuario), nunca del chat."""
        sesion = Sesion(
            telegram_user_id=uid,
            usuario_id=usuario.id,
            nombre=usuario.nombre,
            rol=usuario.rol,
            codigo=usuario.codigo_estudiante if usuario.rol == "estudiante" else None,
            expira=self.reloj() + timedelta(minutes=self.session_minutes),
        )
        with self._lock:
            self._sesiones[uid] = sesion
            self._fallos.pop(uid, None)  # un login exitoso reinicia el contador
            self._retos.pop(uid, None)
        return sesion

    def obtener(self, uid: int) -> tuple[Sesion, bool]:
        """(sesión vigente o pública, expiró_ahora). Una sesión vencida se borra."""
        with self._lock:
            sesion = self._sesiones.get(uid)
            if sesion is None:
                return sesion_publica(uid), False
            if sesion.activa(self.reloj()):
                return sesion, False
            del self._sesiones[uid]
            return sesion_publica(uid), True

    def logout(self, uid: int) -> bool:
        with self._lock:
            return self._sesiones.pop(uid, None) is not None
