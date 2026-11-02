"""Configuración de pytest: los módulos del lab se importan como en los scripts (por nombre)."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LAB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB_DIR))

from sesion import Sesion  # noqa: E402
from usuarios import Usuario  # noqa: E402

ESTUDIANTE = Usuario("integrante_1", "Ana Prueba", "estudiante", "2026010", 111)
DOCENTE = Usuario("integrante_2", "Beto Prueba", "docente", None, 222)


def sesion_de(usuario: Usuario | None, minutos: float = 10) -> Sesion:
    if usuario is None:
        return Sesion(telegram_user_id=999)
    return Sesion(
        telegram_user_id=usuario.telegram_user_id,
        usuario_id=usuario.id,
        nombre=usuario.nombre,
        rol=usuario.rol,
        codigo=usuario.codigo_estudiante,
        expira=datetime.now(timezone.utc) + timedelta(minutes=minutos),
    )


class Reloj:
    """Reloj manual para probar caducidad y bloqueo sin esperar."""

    def __init__(self) -> None:
        self.t = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def avanzar(self, **kwargs: float) -> None:
        self.t += timedelta(**kwargs)


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()
