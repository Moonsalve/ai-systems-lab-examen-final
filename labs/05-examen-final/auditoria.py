"""Log de auditoría en JSONL (una línea JSON por evento).

Registra QUÉ pasó y POR QUÉ (el motivo que al usuario nunca se le muestra), pero nunca
guarda imágenes, videos ni embeddings: solo la similitud obtenida (un número).

Eventos:
  login_aceptado, login_rechazado (con motivo), acceso_denegado (tool o documento),
  tool_restringida_ejecutada, logout, sesion_expirada, login_reto_emitido
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any

from sesion import ahora

logger = logging.getLogger(__name__)

EVENTOS = frozenset(
    {
        "login_reto_emitido",
        "login_aceptado",
        "login_rechazado",
        "acceso_denegado",
        "tool_restringida_ejecutada",
        "logout",
        "sesion_expirada",
    }
)

# Campos que jamás deben llegar al archivo, aunque alguien los pase por error.
CAMPOS_PROHIBIDOS = frozenset({"embedding", "embeddings", "imagen", "foto", "video", "frame", "frames"})
# Campos que arma la propia auditoría: un "extra" no puede reemplazarlos.
CAMPOS_RESERVADOS = frozenset({"fecha", "telegram_user_id", "evento", "motivo", "similitud", "tool"})


def validar_evento(evento: str, extra: dict[str, Any]) -> None:
    if evento not in EVENTOS:
        raise ValueError(f"Evento de auditoría desconocido: {evento!r}")
    prohibidos = CAMPOS_PROHIBIDOS & extra.keys()
    if prohibidos:
        raise ValueError(f"La auditoría no guarda datos biométricos: {sorted(prohibidos)}")


class Auditoria:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def registrar(
        self,
        evento: str,
        telegram_user_id: int | None,
        *,
        motivo: str | None = None,
        similitud: float | None = None,
        tool: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        validar_evento(evento, extra)
        entrada: dict[str, Any] = {
            "fecha": ahora().isoformat(timespec="seconds"),
            "telegram_user_id": telegram_user_id,
            "evento": evento,
            "motivo": motivo,
            "similitud": None if similitud is None else round(float(similitud), 4),
            "tool": tool,
            **extra,
        }
        linea = json.dumps(entrada, ensure_ascii=False)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as archivo:
                    archivo.write(linea + "\n")
        except OSError:
            # Un fallo de disco no debe tumbar el bot, pero sí quedar en el log del proceso.
            logger.exception("No se pudo escribir la auditoría: %s", linea)
        return entrada

    def ultimas(self, n: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lineas = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(linea) for linea in lineas if linea.strip()]


class AuditoriaMemoria(Auditoria):
    """Para pruebas: no escribe en disco; guarda los eventos en memoria."""

    def __init__(self) -> None:
        super().__init__(Path("auditoria-memoria.jsonl"))
        self.eventos: list[dict[str, Any]] = []

    def registrar(self, evento: str, telegram_user_id: int | None, **kwargs: Any) -> dict[str, Any]:
        # Mismas validaciones que la real, para que las pruebas detecten los mismos errores.
        validar_evento(evento, {k: v for k, v in kwargs.items() if k not in CAMPOS_RESERVADOS})
        entrada = {"evento": evento, "telegram_user_id": telegram_user_id, **kwargs}
        self.eventos.append(entrada)
        return entrada
