"""Agente: un CICLO en el que el LLM decide el siguiente paso.

    repetir hasta max_steps:
        LLM(mensajes + definiciones) → ¿tool_calls?
            no → esa es la respuesta final
            sí → ejecutar cada tool, agregar resultados a los mensajes y volver a empezar
    si se agotan los pasos → una última llamada sin tools para responder con lo que hay

Novedad del examen final (defensa en profundidad):
  - El agente se construye POR PREGUNTA con las tools del rol de la sesión.
  - `execute` vuelve a verificar con acceso.tool_permitida ANTES de ejecutar. Si el modelo
    "alucina" una tool o el usuario la nombra en el chat, no se ejecuta, se audita como
    acceso_denegado y el modelo recibe un error genérico.
  - Las llamadas al LLM (síncronas) corren en un hilo para no bloquear el bot de Telegram.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import acceso
from auditoria import Auditoria
from llm_client import LLMClient, LLMResponse, Message, ToolCall
from prompts import build_agent_messages
from sesion import Sesion
from tools import Tool, ToolExecutionError

logger = logging.getLogger(__name__)

MAX_STEPS = 5

# Quien usa el agente (la interfaz) decide cómo pedir autorización al usuario:
# en consola es input(); en Telegram son botones [Sí] [No].
ConfirmFn = Callable[[Tool, dict[str, Any]], Awaitable[bool]]

LIMIT_MESSAGE = (
    "[Aviso del sistema] Se alcanzó el límite de pasos: ya no puedes usar herramientas. "
    "Responde ahora al usuario con la información que ya tienes y di qué quedó pendiente."
)

REJECTED = (
    "El usuario NO autorizó esta acción, así que no se ejecutó. No la vuelvas a intentar en esta "
    "respuesta: infórmale que la acción quedó cancelada."
)
DENIED = "Error: esa herramienta no está disponible para este usuario. No se ejecutó."


@dataclass
class ToolRun:
    call: ToolCall  # lo que pidió el modelo
    status: Literal["ok", "error", "rechazada", "denegada"]
    result: str  # lo que recibe el modelo como resultado


@dataclass
class AgentStep:
    response: LLMResponse  # la decisión del modelo en este paso
    tool_runs: list[ToolRun] = field(default_factory=list)  # vacío = respuesta final


@dataclass
class AgentAnswer:
    text: str
    steps: list[AgentStep]  # la traza: qué decidió el modelo en cada vuelta
    messages: list[Message]  # lo que recibió el LLM en la última llamada
    stopped_by_limit: bool  # True si se agotaron los pasos


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        tools: list[Tool],
        confirm: ConfirmFn,
        sesion: Sesion,
        auditoria: Auditoria | None = None,
        max_steps: int = MAX_STEPS,
    ):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Hay dos tools con el mismo nombre.")
        self.confirm = confirm
        self.sesion = sesion
        self.auditoria = auditoria
        self.max_steps = max_steps
        # Acciones que el usuario rechazó en ESTA pregunta: si el modelo las vuelve a pedir, el
        # software las rechaza sin volver a preguntar (un "No" no se convierte en "pregúntale otra vez").
        self._rechazadas: set[str] = set()

    async def _chat(self, messages: list[Message], definitions: list[dict], allow_tools: bool = True) -> LLMResponse:
        return await asyncio.to_thread(self.llm.chat, messages, tools=definitions, allow_tools=allow_tools)

    async def run(self, question: str, history: list[Message] | None = None) -> AgentAnswer:
        messages = build_agent_messages(
            history or [], question, self.sesion.nombre, acceso.rol_efectivo(self.sesion)
        )
        definitions = [tool.definition() for tool in self.tools.values()]
        steps: list[AgentStep] = []

        for _ in range(self.max_steps):
            response = await self._chat(messages, definitions)
            if not response.tool_calls:
                steps.append(AgentStep(response))
                return AgentAnswer(response.text, steps, messages, stopped_by_limit=False)

            messages.append(response.as_message())
            step = AgentStep(response)
            for call in response.tool_calls:
                tool_run = await self.execute(call)
                step.tool_runs.append(tool_run)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": tool_run.result})
            steps.append(step)

        # Se agotaron los pasos y el modelo sigue pidiendo tools: respuesta final forzada.
        messages.append({"role": "user", "content": LIMIT_MESSAGE})
        final = await self._chat(messages, definitions, allow_tools=False)
        steps.append(AgentStep(final))
        return AgentAnswer(final.text, steps, messages, stopped_by_limit=True)

    def _audit(self, evento: str, **kwargs: Any) -> None:
        if self.auditoria is not None:
            self.auditoria.registrar(evento, self.sesion.telegram_user_id, **kwargs)

    async def execute(self, call: ToolCall) -> ToolRun:
        """Ejecuta una tool pedida por el modelo, solo si el ROL de la sesión la permite."""
        # 1. Autorización (código determinista): no importa lo que el modelo "quiera".
        if not acceso.tool_permitida(self.sesion, call.name):
            self._audit(
                "acceso_denegado",
                tool=call.name,
                motivo=f"tool_no_permitida_para_rol_{acceso.rol_efectivo(self.sesion)}",
            )
            return ToolRun(call, "denegada", DENIED)

        tool = self.tools.get(call.name)
        if tool is None:
            return ToolRun(call, "error", f"Error: no existe la tool {call.name!r}.")
        try:
            arguments = call.arguments()
        except ValueError as exc:
            return ToolRun(call, "error", f"Error: {exc}")

        # 2. Generar no es ejecutar: una acción con efectos solo se ejecuta si el usuario la autoriza.
        if not tool.read_only:
            if call.name in self._rechazadas:
                return ToolRun(call, "rechazada", REJECTED)
            if not await self.confirm(tool, arguments):
                self._rechazadas.add(call.name)
                return ToolRun(call, "rechazada", REJECTED)
            # La confirmación puede tardar: si la sesión venció mientras tanto, ya no hay permiso.
            if not acceso.tool_permitida(self.sesion, call.name):
                self._audit("acceso_denegado", tool=call.name, motivo="sesion_vencida_durante_confirmacion")
                return ToolRun(call, "denegada", DENIED)

        try:
            result = await tool.run(arguments)
        except (ToolExecutionError, ValueError, TypeError) as exc:  # error previsto: el modelo puede corregir
            return ToolRun(call, "error", f"Error: {exc}")
        except Exception:  # servidor caído u otro fallo interno: el detalle va al log, no al modelo
            logger.exception("Fallo ejecutando la tool %s", call.name)
            return ToolRun(call, "error", "Error: el servicio no está disponible en este momento.")

        if acceso.es_restringida(call.name):
            self._audit("tool_restringida_ejecutada", tool=call.name, usuario_id=self.sesion.usuario_id)
        return ToolRun(call, "ok", result)
