"""Capacidades del asistente expresadas como tools.

El RAG se convierte en una tool más (buscar_documentos). Solo hace la RECUPERACIÓN:
devuelve los fragmentos numerados con su fuente, y el LLM que la pidió redacta la respuesta.

Novedad del examen final: la tool se crea POR SESIÓN con los niveles permitidos ya
fijados. El modelo no puede pedir otro nivel: el parámetro no existe en el schema.
Cada recuperación se anota en `registro` para la traza (source + nivel de lo que vio el LLM).

Uso (sin LLM ni API key; requiere el índice de ingest.py):
    uv run python labs/05-examen-final/capabilities.py publico "¿Cuándo es el simulacro?"
"""

import asyncio
import sys
from collections.abc import Iterable
from typing import Any

from rag import CourseRAG, Recuperacion, format_context
from tools import Tool

AVISO_RESTRINGIDO_TOOL = (
    "\n\n[Aviso del sistema] Hay información más relevante en documentos restringidos que este "
    "usuario no puede ver (su contenido no se incluye). Si los fragmentos anteriores no responden, "
    "indícale al usuario: {mensaje} No deduzcas ni estimes fechas, horas ni lugares que no aparezcan "
    "literalmente en los fragmentos (p. ej. a partir de otras fechas del curso)."
)


def rag_tool(
    rag: CourseRAG,
    niveles: Iterable[str],
    registro: list[Recuperacion] | None = None,
    mensaje_sin_permiso: str = "necesitas iniciar sesión con /login.",
) -> Tool:
    permitidos = frozenset(niveles)

    async def run(arguments: dict[str, Any]) -> str:
        consulta = str(arguments.get("consulta", "")).strip()
        if not consulta:
            raise ValueError("Falta la consulta.")
        recuperacion = await asyncio.to_thread(rag.recuperar, consulta, permitidos)
        if registro is not None:
            registro.append(recuperacion)
        texto = format_context(recuperacion.resultados)
        if recuperacion.restringido:
            texto += AVISO_RESTRINGIDO_TOOL.format(mensaje=mensaje_sin_permiso)
        return texto

    return Tool(
        name="buscar_documentos",
        description=(
            "Busca en los documentos del curso de IA: programa, docente, horarios, evaluación, fechas de "
            "parciales y simulacros, monitorías, políticas, anuncios, preguntas frecuentes de los labs y "
            "casos especiales. Devuelve fragmentos numerados [n] con su archivo de origen."
        ),
        parameters={
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "Pregunta autocontenida, p. ej. 'fecha del simulacro del segundo parcial'",
                }
            },
            "required": ["consulta"],
        },
        run=run,
        read_only=True,
        origin="rag",
    )


async def _demo(nivel_rol: str, question: str) -> None:
    from acceso import NIVELES_POR_ROL
    from config import load_rag_settings
    from embeddings import Embedder
    from vector_store import VectorStore

    settings = load_rag_settings()
    store = VectorStore.load(settings.index_dir)
    rag = CourseRAG(None, Embedder(store.embedding_model), store, settings.top_k, settings.min_score)
    registro: list[Recuperacion] = []
    tool = rag_tool(rag, NIVELES_POR_ROL[nivel_rol], registro)
    print(f"[rol {nivel_rol}] {tool.name}({{'consulta': {question!r}}}) →\n")
    print(await tool.run({"consulta": question}))
    print("\nTraza (lo que vería el LLM):")
    for r in registro[0].resultados:
        print(f"  {r.chunk.source:<28} nivel={r.chunk.nivel:<10} score={r.score:.3f}")
    print(f"  ¿restringido relevante? {registro[0].restringido}")


if __name__ == "__main__":
    rol = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in {"publico", "estudiante", "docente"} else "publico"
    pregunta = " ".join(sys.argv[2:]) or "¿Cuándo es el simulacro del segundo parcial?"
    asyncio.run(_demo(rol, pregunta))
