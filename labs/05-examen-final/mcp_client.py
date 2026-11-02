"""Cliente MCP: conecta el asistente con el servidor "Servicios académicos".

Responsabilidades:
  1. Lanzar el servidor como subproceso y hablarle por stdin/stdout (transporte stdio).
  2. Descubrir sus tools UNA vez (list_tools) y guardarlas en un CatalogoMCP.
  3. Por cada sesión, producir los `Tool` del rol (CatalogoMCP.tools_para):
       - filtrados con acceso.tool_permitida (el modelo solo ve las de su rol);
       - ligados a la identidad de la sesión: cada llamada envía meta={rol, codigo, ...}.
         El LLM no puede cambiar esa identidad: no es un argumento de la tool.
       - los argumentos que el modelo invente fuera del schema se descartan.
  4. Exponer el resource academico://calendario como la tool pública "calendario".

Uso (sin LLM ni API key):
    uv run python labs/05-examen-final/mcp_client.py
"""

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult
from mcp.types import Tool as MCPTool

import acceso
from sesion import Sesion
from tools import Tool, ToolExecutionError

SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_server.py"
CALENDARIO_URI = "academico://calendario"


def server_parameters(academico_file: Path | None = None) -> StdioServerParameters:
    # El servidor se ejecuta con el mismo intérprete (el entorno de uv) que el asistente.
    env = dict(os.environ)
    if academico_file is not None:
        env["ACADEMICO_FILE"] = str(academico_file)
    return StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)], env=env)


async def open_client(stack: AsyncExitStack, academico_file: Path | None = None) -> Client:
    """Lanza el servidor y se conecta. La conexión se cierra cuando se cierra `stack`."""
    return await stack.enter_async_context(Client(server_parameters(academico_file)))


def result_to_text(result: CallToolResult) -> str:
    """El resultado MCP es una lista de bloques de contenido; el LLM recibirá su texto."""
    text = "\n".join(block.text for block in result.content if block.type == "text")
    if result.is_error:
        raise ToolExecutionError(text)
    return text


def identidad_meta(sesion: Sesion) -> dict[str, Any]:
    """Lo que el servidor sabe de quién llama. Sale de la sesión, nunca del chat ni del LLM."""
    rol = acceso.rol_efectivo(sesion)
    return {
        "rol": rol,
        "codigo": sesion.codigo if rol == "estudiante" else None,
        "usuario_id": sesion.usuario_id if rol != "publico" else None,
    }


def to_tool(client: Client, mcp_tool: MCPTool, sesion: Sesion) -> Tool:
    """Adaptador: definición MCP → Tool ligada a la identidad de la sesión."""
    meta = identidad_meta(sesion)
    permitidos = set((mcp_tool.input_schema or {}).get("properties", {}))

    async def run(arguments: dict[str, Any]) -> str:
        # Solo los argumentos del schema: un "codigo" inventado por el modelo se descarta.
        limpios = {k: v for k, v in arguments.items() if k in permitidos}
        result = await client.call_tool(mcp_tool.name, limpios, meta=meta)
        return result_to_text(result)

    annotations = mcp_tool.annotations
    return Tool(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        parameters=mcp_tool.input_schema,
        run=run,
        # Sin la anotación, se asume lo más seguro: la tool puede tener efectos.
        read_only=bool(annotations and annotations.read_only_hint),
        origin=client.server_info.name if client.server_info else "mcp",
    )


def calendario_tool(client: Client) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        return await read_resource_text(client, CALENDARIO_URI)

    return Tool(
        name="calendario",
        description="Devuelve el calendario oficial del curso: fechas de parciales, entrega y sustentación del proyecto.",
        parameters={"type": "object", "properties": {}, "required": []},
        run=run,
        read_only=True,
        origin="mcp-resource",
    )


class CatalogoMCP:
    """Las tools del servidor se descubren una vez; luego se entregan filtradas por sesión."""

    def __init__(self, client: Client, mcp_tools: list[MCPTool]):
        self.client = client
        self.mcp_tools = mcp_tools

    @classmethod
    async def cargar(cls, client: Client) -> "CatalogoMCP":
        listed = await client.list_tools()
        return cls(client, list(listed.tools))

    def nombres(self) -> list[str]:
        return [tool.name for tool in self.mcp_tools]

    def tools_para(self, sesion: Sesion) -> list[Tool]:
        tools = [to_tool(self.client, t, sesion) for t in self.mcp_tools if acceso.tool_permitida(sesion, t.name)]
        if acceso.tool_permitida(sesion, "calendario"):
            tools.append(calendario_tool(self.client))
        return tools


async def read_resource_text(client: Client, uri: str) -> str:
    result = await client.read_resource(uri)
    return "\n".join(content.text for content in result.contents if hasattr(content, "text"))


async def _demo() -> None:
    from datetime import timedelta

    from sesion import ahora

    async with Client(server_parameters()) as client:
        catalogo = await CatalogoMCP.cargar(client)
        print("Tools del servidor:", catalogo.nombres())
        fin = ahora() + timedelta(minutes=5)
        sesiones = {
            "publico": Sesion(telegram_user_id=1),
            "estudiante": Sesion(2, "integrante_1", "Estudiante", "estudiante", "2026003", fin),
            "docente": Sesion(3, "integrante_2", "Docente", "docente", None, fin),
        }
        for rol, sesion in sesiones.items():
            tools = catalogo.tools_para(sesion)
            print(f"\n[{rol}] recibe: {[t.name for t in tools]}")
            for t in tools:
                props = json.dumps(t.parameters.get("properties", {}), ensure_ascii=False)
                print(f"   {t.name}: parameters={props[:80]}")
        estudiante = {t.name: t for t in catalogo.tools_para(sesiones["estudiante"])}
        print("\nconsultar_mis_notas (estudiante, intenta colar codigo=2026001) →")
        print(await estudiante["consultar_mis_notas"].run({"codigo": "2026001"}))


if __name__ == "__main__":
    asyncio.run(_demo())
