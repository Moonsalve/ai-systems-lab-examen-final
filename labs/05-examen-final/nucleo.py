"""Núcleo del asistente: punto de entrada que NO depende de Telegram.

    responder(pregunta, historial, sesion, confirm) → Respuesta(texto, tools, fragmentos, ...)

Lo usan bot.py (canal Telegram), chatbot.py (consola) y pruebas_seguridad.py (suite de
ataques). Por eso la seguridad vive aquí y en los módulos que este usa, nunca en el canal.

Errores del LLM o del servidor MCP: se registran con logging.exception y el usuario recibe
un mensaje claro. Nunca se propaga la excepción (el bot no se cae).
"""

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

import acceso
from agent import ConfirmFn
from auditoria import Auditoria
from fundamentacion import fechas_no_fundamentadas
from llm_client import LLMError, Message
from orchestrator import Mode, Orchestrator, OrchestratorAnswer, create_orchestrator
from sesion import Sesion

logger = logging.getLogger(__name__)

MENSAJE_ERROR_LLM = "El modelo de lenguaje no está disponible en este momento. Intenta de nuevo en unos segundos."
MENSAJE_ERROR_GENERAL = "Ocurrió un error procesando tu pregunta. Ya quedó registrado; intenta de nuevo."
MENSAJE_RESTRINGIDO_PUBLICO = (
    "🔒 Esa información está en documentos restringidos del curso. Inicia sesión con /login para consultarla."
)
MENSAJE_RESTRINGIDO_ROL = "🔒 Esa información está en documentos restringidos a los que tu rol no tiene acceso."
PALABRAS_AVISO = ("/login", "iniciar sesión", "inicia sesión", "no tiene acceso", "no tienes acceso")


@dataclass
class Fragmento:
    source: str
    nivel: str
    score: float


@dataclass
class Respuesta:
    texto: str
    tools_llamadas: list[str] = field(default_factory=list)
    tools_denegadas: list[str] = field(default_factory=list)  # pedidas por el modelo y bloqueadas
    tools_ofrecidas: list[str] = field(default_factory=list)
    fragmentos: list[Fragmento] = field(default_factory=list)  # lo que vio el LLM
    rol: str = "publico"
    ruta: str | None = None
    restringido: bool = False
    respuesta_descartada: bool = False  # el software descartó fechas inventadas por el modelo
    error: bool = False
    detalle: OrchestratorAnswer | None = None

    def traza(self) -> str:
        """Resumen para /traza en Telegram y para --debug en consola."""
        lineas = [f"rol efectivo: {self.rol}"]
        if self.ruta:
            lineas.append(f"ruta del router: {self.ruta}")
        lineas.append(f"tools ofrecidas al modelo: {', '.join(self.tools_ofrecidas) or '(ninguna)'}")
        lineas.append(f"tools ejecutadas: {', '.join(self.tools_llamadas) or '(ninguna)'}")
        if self.tools_denegadas:
            lineas.append(f"tools DENEGADAS por el software: {', '.join(self.tools_denegadas)}")
        if self.fragmentos:
            lineas.append("fragmentos enviados al LLM:")
            lineas.extend(f"  - {f.source} [nivel {f.nivel}] score={f.score:.2f}" for f in self.fragmentos)
        else:
            lineas.append("fragmentos enviados al LLM: (ninguno)")
        if self.restringido:
            lineas.append("había coincidencias en documentos NO permitidos: se ocultaron (no llegaron al LLM)")
        if self.respuesta_descartada:
            lineas.append("el software DESCARTÓ la respuesta del modelo: traía fechas que no estaban en sus fuentes")
        return "\n".join(lineas)


def _con_aviso(texto: str, sesion: Sesion) -> str:
    """Si la mejor respuesta era restringida, el SOFTWARE garantiza el aviso (no depende del LLM)."""
    if any(palabra in texto.lower() for palabra in PALABRAS_AVISO):
        return texto
    return f"{texto}\n\n🔒 Parte de la información sobre este tema es restringida: inicia sesión con /login."


def _sin_fundamento(answer: OrchestratorAnswer, texto: str) -> set[tuple[int, int]]:
    """Fechas inventadas cuando había información restringida (ver fundamentacion.py)."""
    if not answer.restringido:
        return set()
    fuentes = [r.chunk.text for r in answer.fragmentos]
    fuentes += [run.result for step in answer.steps for run in step.tool_runs]
    return fechas_no_fundamentadas(texto, fuentes)


def _respuesta(answer: OrchestratorAnswer, sesion: Sesion) -> Respuesta:
    runs = [run for step in answer.steps for run in step.tool_runs]
    texto = answer.text.strip() or "No tengo una respuesta para eso."
    inventadas = _sin_fundamento(answer, texto)
    if inventadas:
        # El modelo afirmó una fecha que no está en nada de lo que vio, sobre un tema restringido:
        # el software descarta esa respuesta en vez de confiar en ella.
        logger.warning("Respuesta descartada: fechas no fundamentadas %s (uid=%s)", inventadas, sesion.telegram_user_id)
        texto = MENSAJE_RESTRINGIDO_PUBLICO if acceso.rol_efectivo(sesion) == "publico" else MENSAJE_RESTRINGIDO_ROL
    # Solo al público se le garantiza el aviso: iniciar sesión sí le daría acceso. A un estudiante
    # el aviso le llega al LLM dentro del resultado de la tool ("tu rol no tiene acceso"), y pegarlo
    # siempre confundiría cuando la pregunta ya se respondió con sus propios documentos.
    if answer.restringido and acceso.rol_efectivo(sesion) == "publico":
        texto = _con_aviso(texto, sesion)
    return Respuesta(
        texto=texto,
        tools_llamadas=[run.call.name for run in runs if run.status != "denegada"],
        tools_denegadas=[run.call.name for run in runs if run.status == "denegada"],
        tools_ofrecidas=answer.tools_ofrecidas,
        fragmentos=[Fragmento(r.chunk.source, r.chunk.nivel, r.score) for r in answer.fragmentos],
        rol=acceso.rol_efectivo(sesion),
        ruta=answer.routing.decision.ruta if answer.routing else None,
        restringido=answer.restringido,
        respuesta_descartada=bool(inventadas),
        detalle=answer,
    )


class Nucleo:
    def __init__(self, orchestrator: Orchestrator, mode: Mode = "agente"):
        self.orchestrator = orchestrator
        self.mode = mode

    async def responder(
        self, pregunta: str, historial: list[Message], sesion: Sesion, confirm: ConfirmFn, mode: Mode | None = None
    ) -> Respuesta:
        sesion = sesion.efectiva()
        try:
            answer = await self.orchestrator.answer(pregunta, historial, mode or self.mode, sesion, confirm)
        except LLMError:
            logger.exception("Error del LLM respondiendo a %s", sesion.telegram_user_id)
            return Respuesta(MENSAJE_ERROR_LLM, rol=acceso.rol_efectivo(sesion), error=True)
        except Exception:  # servidor MCP caído, índice dañado, etc.
            logger.exception("Error inesperado respondiendo a %s", sesion.telegram_user_id)
            return Respuesta(MENSAJE_ERROR_GENERAL, rol=acceso.rol_efectivo(sesion), error=True)
        return _respuesta(answer, sesion)


async def create_nucleo(
    stack: AsyncExitStack, auditoria: Auditoria | None = None, academico_file: Path | None = None, mode: Mode = "agente"
) -> Nucleo:
    orchestrator = await create_orchestrator(stack, auditoria, academico_file)
    return Nucleo(orchestrator, mode)
