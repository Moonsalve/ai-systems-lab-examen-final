"""Router: una llamada al LLM que CLASIFICA la pregunta en una ruta.

El router no responde ni usa tools: solo decide por qué camino debe ir la pregunta.
Después, el orquestador (código normal) ejecuta el camino elegido. La ruta NO otorga
permisos: cada ruta vuelve a aplicar los filtros de acceso de la sesión.

Uso (una llamada al LLM, sin documentos ni MCP):
    uv run python labs/05-examen-final/router.py "¿Cuándo es el primer parcial?"
"""

import sys
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from llm_client import LLMClient, LLMError, LLMResponse
from prompts import build_router_messages

Route = Literal["directa", "documentos", "acciones"]

# Si el LLM devuelve algo que no cumple el esquema, el software elige una ruta segura:
# "documentos" no ejecuta acciones y responde con evidencia o dice que no encontró la información.
FALLBACK_ROUTE: Route = "documentos"


class RouteDecision(BaseModel):
    ruta: Route
    motivo: str


@dataclass
class RouterResult:
    decision: RouteDecision
    response: LLMResponse
    fallback: bool  # True si se usó FALLBACK_ROUTE porque la salida del LLM no era válida


def route(llm: LLMClient, question: str) -> RouterResult:
    try:
        # Margen amplio: los modelos con razonamiento (gpt-oss) gastan tokens antes del JSON.
        response = llm.chat(build_router_messages(question), temperature=0, max_tokens=1000, json_mode=True)
    except LLMError as exc:  # p. ej., el proveedor no logró generar un JSON válido
        response = LLMResponse(text=str(exc), model="(error)", finish_reason="error", prompt_tokens=0, completion_tokens=0)
        decision = RouteDecision(ruta=FALLBACK_ROUTE, motivo="El router falló; se usó la ruta de respaldo.")
        return RouterResult(decision, response, fallback=True)
    try:
        decision = RouteDecision.model_validate_json(response.text)
    except ValidationError:
        decision = RouteDecision(
            ruta=FALLBACK_ROUTE,
            motivo="La salida del router no era un JSON válido; se usó la ruta de respaldo.",
        )
        return RouterResult(decision, response, fallback=True)
    return RouterResult(decision, response, fallback=False)


if __name__ == "__main__":
    from config import load_settings

    question = " ".join(sys.argv[1:]) or input("Pregunta: ")
    result = route(LLMClient(load_settings()), question)
    print(f"Texto crudo del LLM: {result.response.text}")
    print(f"Ruta: {result.decision.ruta}{'  (respaldo)' if result.fallback else ''}")
    print(f"Motivo: {result.decision.motivo}")
