"""Contrato compartido: la sesión de un usuario de Telegram.

La sesión es la ÚNICA fuente de identidad del núcleo. Rol, nombre y código se copian
de data/usuarios.json cuando el login facial tiene éxito; nunca se toman del chat.
Una sesión sin usuario (o vencida) equivale al rol "publico".
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

Rol = Literal["publico", "estudiante", "docente"]
ROLES: tuple[Rol, ...] = ("publico", "estudiante", "docente")


def ahora() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Sesion:
    telegram_user_id: int
    usuario_id: str | None = None  # id en usuarios.json; None = sin sesión
    nombre: str | None = None
    rol: Rol = "publico"
    codigo: str | None = None  # solo el rol estudiante tiene código
    expira: datetime | None = None

    def activa(self, momento: datetime | None = None) -> bool:
        """True si hay un usuario autenticado y la sesión no ha vencido."""
        if self.rol == "publico" or self.usuario_id is None or self.expira is None:
            return False
        return (momento or ahora()) < self.expira

    def efectiva(self, momento: datetime | None = None) -> "Sesion":
        """La sesión que debe usar el núcleo: si venció, se degrada a pública."""
        return self if self.activa(momento) else sesion_publica(self.telegram_user_id)

    def minutos_restantes(self, momento: datetime | None = None) -> float:
        if not self.activa(momento):
            return 0.0
        return max(0.0, (self.expira - (momento or ahora())).total_seconds() / 60)

    def con(self, **cambios) -> "Sesion":
        return replace(self, **cambios)


def sesion_publica(telegram_user_id: int) -> Sesion:
    return Sesion(telegram_user_id=telegram_user_id)
