"""RAG con control de acceso por niveles.

Pipeline de consulta, en cada pregunta:
    pregunta → embedding → búsqueda FILTRADA por niveles → contexto → prompt → LLM

Novedad del examen final:
  - retrieve/answer reciben los niveles permitidos de la sesión (acceso.niveles_permitidos).
  - `_hay_restringido_relevante`: busca también en los niveles NO permitidos, pero solo
    devuelve un booleano. El texto restringido nunca sale de esa función, así que nunca
    llega al LLM; sirve para decirle al usuario "necesitas iniciar sesión".
"""

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from acceso import NIVELES
from embeddings import Embedder
from llm_client import LLMClient, LLMResponse, Message
from prompts import build_rag_messages
from vector_store import SearchResult, VectorStore

NO_CONTEXT = "(No se encontraron fragmentos relevantes en los documentos del curso.)"


@dataclass
class RAGAnswer:
    text: str
    sources: list[SearchResult]  # fragmentos recuperados y enviados al LLM
    messages: list[Message]  # lo que realmente recibió el LLM
    response: LLMResponse
    restringido: bool = False  # la mejor respuesta estaba en un nivel no permitido


@dataclass
class Recuperacion:
    resultados: list[SearchResult]  # SOLO fragmentos permitidos y relevantes
    restringido: bool  # True si hay algo más relevante en un nivel no permitido


def format_context(results: list[SearchResult]) -> str:
    """Numera los fragmentos para que el LLM pueda citarlos: "[1] (evaluacion.md) texto"."""
    if not results:
        return NO_CONTEXT
    return "\n\n".join(f"[{i}] ({result.chunk.source}) {result.chunk.text}" for i, result in enumerate(results, 1))


class CourseRAG:
    def __init__(self, llm: LLMClient | None, embedder: Embedder, store: VectorStore, top_k: int, min_score: float):
        self.llm = llm
        self.embedder = embedder
        self.store = store
        self.top_k = top_k
        self.min_score = min_score

    def recuperar(self, question: str, niveles: Iterable[str]) -> Recuperacion:
        permitidos = frozenset(niveles)
        query_vector = self.embedder.embed_query(question)
        resultados = [r for r in self.store.search(query_vector, self.top_k, permitidos) if r.score >= self.min_score]
        restringido = self._hay_restringido_relevante(query_vector, permitidos)
        return Recuperacion(resultados=resultados, restringido=restringido)

    def _hay_restringido_relevante(self, query_vector: np.ndarray, permitidos: frozenset[str]) -> bool:
        """¿Algún fragmento NO permitido habría entrado al top-k si no existiera el filtro?

        Solo se devuelve un booleano; el texto restringido se descarta aquí mismo y nunca
        llega al LLM ni al usuario.
        """
        if not frozenset(NIVELES) - permitidos:
            return False
        sin_filtro = self.store.search(query_vector, self.top_k, NIVELES)
        return any(r.chunk.nivel not in permitidos and r.score >= self.min_score for r in sin_filtro)

    def retrieve(self, question: str, niveles: Iterable[str]) -> list[SearchResult]:
        return self.recuperar(question, niveles).resultados

    def answer(
        self,
        question: str,
        history: list[Message] | None,
        niveles: Iterable[str],
        *,
        nombre_usuario: str | None = None,
        rol: str = "publico",
    ) -> RAGAnswer:
        if self.llm is None:
            raise RuntimeError("CourseRAG sin LLM: solo puede recuperar.")
        recuperacion = self.recuperar(question, niveles)
        messages = build_rag_messages(
            history or [],
            question,
            format_context(recuperacion.resultados),
            restringido=recuperacion.restringido,
            nombre_usuario=nombre_usuario,
            rol=rol,
        )
        response = self.llm.chat(messages)
        return RAGAnswer(
            text=response.text,
            sources=recuperacion.resultados,
            messages=messages,
            response=response,
            restringido=recuperacion.restringido,
        )
