"""Orquestador por rol: compone LLM, RAG, tools locales y MCP según la SESIÓN.

Dos estrategias para decidir qué capacidad usar:

  modo "router" (flujo fijo, decidido por el software después de clasificar):
      pregunta → router → "directa"   → LLM sin tools
                        → "documentos" → CourseRAG.answer con los niveles del rol
                        → "acciones"   → agente SOLO con las tools del rol

  modo "agente" (flujo dinámico, decidido por el modelo en cada paso):
      pregunta → agente con las capacidades DEL ROL (buscar_documentos filtrado + tools)

Diferencia clave con el Lab 04: allí el agente se armaba UNA vez en __init__ con TODAS las
tools. Aquí se arma en CADA pregunta con lo que la política de acceso (acceso.py) permite a
esa sesión. El modelo nunca ve una tool que no puede usar, y si la pide igual, agent.execute
la rechaza.
"""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import acceso
from agent import MAX_STEPS, Agent, AgentAnswer, AgentStep, ConfirmFn
from auditoria import Auditoria
from capabilities import rag_tool
from config import load_rag_settings, load_settings
from embeddings import Embedder
from llm_client import LLMClient, LLMResponse, Message
from mcp_client import CatalogoMCP, open_client
from prompts import build_direct_messages
from rag import CourseRAG, Recuperacion
from router import RouterResult, route
from sesion import Sesion
from tools import LOCAL_TOOLS, Tool
from vector_store import SearchResult, VectorStore

Mode = Literal["router", "agente"]


@dataclass
class OrchestratorAnswer:
    text: str
    mode: Mode
    routing: RouterResult | None = None  # solo en modo router
    steps: list[AgentStep] = field(default_factory=list)  # traza del agente, si se usó
    sources: list[str] = field(default_factory=list)  # archivos de los fragmentos enviados al LLM
    fragmentos: list[SearchResult] = field(default_factory=list)  # lo que vio el LLM (con nivel)
    restringido: bool = False  # la mejor respuesta estaba en un nivel que la sesión no puede ver
    tools_ofrecidas: list[str] = field(default_factory=list)  # lo que el modelo podía pedir
    responses: list[LLMResponse] = field(default_factory=list)  # TODAS las llamadas al LLM, en orden
    stopped_by_limit: bool = False

    def tools_used(self) -> list[str]:
        return [run.call.name for step in self.steps for run in step.tool_runs]


class Orchestrator:
    def __init__(
        self,
        llm: LLMClient,
        rag: CourseRAG,
        catalogo: CatalogoMCP | None,
        auditoria: Auditoria | None = None,
        local_tools: list[Tool] | None = None,
        max_steps: int = MAX_STEPS,
    ):
        self.llm = llm
        self.rag = rag
        self.catalogo = catalogo
        self.auditoria = auditoria
        self.local_tools = LOCAL_TOOLS if local_tools is None else local_tools
        self.max_steps = max_steps

    def action_tools(self, sesion: Sesion) -> list[Tool]:
        """Tools locales + MCP del rol (sin buscar_documentos). Filtradas por acceso.py."""
        mcp_tools = self.catalogo.tools_para(sesion) if self.catalogo else []
        return [t for t in [*self.local_tools, *mcp_tools] if acceso.tool_permitida(sesion, t.name)]

    def tools_para(self, sesion: Sesion, registro: list[Recuperacion]) -> list[Tool]:
        """Todas las capacidades del rol, incluida la búsqueda en documentos con su filtro."""
        documentos = rag_tool(
            self.rag, acceso.niveles_permitidos(sesion), registro, acceso.mensaje_sin_permiso(sesion)
        )
        return [documentos, *self.action_tools(sesion)]

    def _agent(self, tools: list[Tool], confirm: ConfirmFn, sesion: Sesion) -> Agent:
        return Agent(self.llm, tools, confirm, sesion, self.auditoria, self.max_steps)

    async def answer(
        self, question: str, history: list[Message], mode: Mode, sesion: Sesion, confirm: ConfirmFn
    ) -> OrchestratorAnswer:
        sesion = sesion.efectiva()  # una sesión vencida se trata como pública
        registro: list[Recuperacion] = []

        if mode == "agente":
            tools = self.tools_para(sesion, registro)
            result = await self._agent(tools, confirm, sesion).run(question, history)
            answer = self._from_agent(result, mode)
            answer.tools_ofrecidas = [t.name for t in tools]
            return self._con_fragmentos(answer, registro, sesion)

        routing = await asyncio.to_thread(route, self.llm, question)
        ruta = routing.decision.ruta
        rol = acceso.rol_efectivo(sesion)
        if ruta == "directa":
            response = await asyncio.to_thread(
                self.llm.chat, build_direct_messages(history, question, sesion.nombre, rol)
            )
            answer = OrchestratorAnswer(response.text, mode, responses=[response])
        elif ruta == "documentos":
            rag_answer = await asyncio.to_thread(
                self.rag.answer,
                question,
                history,
                acceso.niveles_permitidos(sesion),
                nombre_usuario=sesion.nombre,
                rol=rol,
            )
            registro.append(Recuperacion(rag_answer.sources, rag_answer.restringido))
            answer = OrchestratorAnswer(rag_answer.text, mode, responses=[rag_answer.response])
        else:  # "acciones": solo las tools del rol
            tools = self.action_tools(sesion)
            result = await self._agent(tools, confirm, sesion).run(question, history)
            answer = self._from_agent(result, mode)
            answer.tools_ofrecidas = [t.name for t in tools]

        answer.routing = routing
        answer.responses.insert(0, routing.response)
        return self._con_fragmentos(answer, registro, sesion)

    def _con_fragmentos(
        self, answer: OrchestratorAnswer, registro: list[Recuperacion], sesion: Sesion
    ) -> OrchestratorAnswer:
        answer.fragmentos = [r for rec in registro for r in rec.resultados]
        answer.sources = sorted({r.chunk.source for r in answer.fragmentos})
        answer.restringido = any(rec.restringido for rec in registro)
        if answer.restringido and self.auditoria is not None:
            self.auditoria.registrar(
                "acceso_denegado",
                sesion.telegram_user_id,
                motivo=f"documento_restringido_para_rol_{acceso.rol_efectivo(sesion)}",
            )
        return answer

    @staticmethod
    def _from_agent(result: AgentAnswer, mode: Mode) -> OrchestratorAnswer:
        return OrchestratorAnswer(
            result.text,
            mode,
            steps=result.steps,
            responses=[step.response for step in result.steps],
            stopped_by_limit=result.stopped_by_limit,
        )


async def create_orchestrator(
    stack: AsyncExitStack, auditoria: Auditoria | None = None, academico_file: Path | None = None
) -> Orchestrator:
    """Arma el sistema completo: configuración, LLM, índice, RAG, tools locales y servidor MCP.

    Lanza ValueError (configuración), FileNotFoundError (falta el índice) o un error de
    conexión con el servidor MCP. El servidor queda abierto hasta que se cierre `stack`.
    """
    settings = load_settings()
    rag_settings = load_rag_settings()
    store = VectorStore.load(rag_settings.index_dir)

    llm = LLMClient(settings)
    rag = CourseRAG(
        llm=llm,
        embedder=Embedder(store.embedding_model),  # el mismo modelo que indexó los documentos
        store=store,
        top_k=rag_settings.top_k,
        min_score=rag_settings.min_score,
    )
    client = await open_client(stack, academico_file)
    catalogo = await CatalogoMCP.cargar(client)
    return Orchestrator(llm, rag, catalogo, auditoria)
