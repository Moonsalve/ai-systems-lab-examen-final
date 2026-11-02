"""Directorio de usuarios (data/usuarios.json): de aquí, y solo de aquí, salen rol, nombre y código."""

import json
from dataclasses import dataclass
from pathlib import Path

from sesion import ROLES, Rol


@dataclass(frozen=True)
class Usuario:
    id: str
    nombre: str
    rol: Rol
    codigo_estudiante: str | None
    telegram_user_id: int | None
    # Signo del giro "izquierda" en el video del DISPOSITIVO de este usuario: +1 si la video-nota
    # no sale espejada, -1 si sale espejada (depende del celular). None = el global del .env.
    signo_camara: float | None = None


def cargar_usuarios(path: Path) -> list[Usuario]:
    data = json.loads(path.read_text(encoding="utf-8"))
    usuarios = []
    for item in data["usuarios"]:
        rol = item["rol"]
        if rol not in ROLES or rol == "publico":
            raise ValueError(f"Rol inválido en usuarios.json para {item.get('id')!r}: {rol!r}")
        tg = item.get("telegram_user_id")
        usuarios.append(
            Usuario(
                id=item["id"],
                nombre=item["nombre"],
                rol=rol,
                codigo_estudiante=item.get("codigo_estudiante"),
                telegram_user_id=int(tg) if tg is not None else None,
                signo_camara=_signo(item.get("signo_camara")),
            )
        )
    return usuarios


def _signo(valor) -> float | None:
    if valor is None:
        return None
    if valor not in (1, -1):
        raise ValueError(f"signo_camara debe ser 1 o -1, no {valor!r}")
    return float(valor)


def buscar_por_telegram(usuarios: list[Usuario], telegram_user_id: int) -> Usuario | None:
    """Factor de posesión: la cuenta de Telegram determina QUÉ usuario intenta entrar."""
    for usuario in usuarios:
        if usuario.telegram_user_id is not None and usuario.telegram_user_id == telegram_user_id:
            return usuario
    return None


def buscar_por_id(usuarios: list[Usuario], usuario_id: str) -> Usuario | None:
    return next((u for u in usuarios if u.id == usuario_id), None)


def actualizar_telegram_id(path: Path, usuario_id: str, telegram_user_id: int) -> None:
    """Lo usa enroll.py (local) para asociar la cuenta de Telegram al usuario."""
    data = json.loads(path.read_text(encoding="utf-8"))
    otros = [u for u in data["usuarios"] if u["id"] != usuario_id and u.get("telegram_user_id") == telegram_user_id]
    if otros:
        raise ValueError(f"El telegram_user_id {telegram_user_id} ya pertenece a {otros[0]['id']!r}.")
    for item in data["usuarios"]:
        if item["id"] == usuario_id:
            item["telegram_user_id"] = telegram_user_id
            break
    else:
        raise ValueError(f"No existe el usuario {usuario_id!r} en {path.name}.")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
