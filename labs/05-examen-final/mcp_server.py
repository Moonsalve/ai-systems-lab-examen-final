"""Servidor MCP "Servicios académicos" (ficticio) con control de acceso por rol.

Tools por rol:
  estudiante: consultar_mis_notas(), consultar_mis_solicitudes(), solicitar_supletorio(parcial, causa)
  docente:    consultar_notas(codigo), consultar_solicitudes(codigo), registrar_nota(codigo, evaluacion, valor)
Resource público: academico://calendario

Inyección de identidad (opción A): el CLIENTE (mcp_client.py) envía la identidad de la
sesión en el `_meta` de cada llamada (`call_tool(..., meta={"rol", "codigo", ...})`). El
servidor la lee con `Context`. El LLM no controla `_meta`: no es un argumento de la tool y
no aparece en el JSON Schema que ve el modelo. Por eso las tools "mis_*" no tienen
parámetro de código: el código lo pone el software desde la sesión.

Además de que el orquestador solo entrega al modelo las tools del rol, el servidor vuelve a
validar el rol en cada tool (tercera capa de defensa).

IMPORTANTE: con el transporte stdio, stdout es el canal del protocolo. Para depurar usa
print(..., file=sys.stderr).
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

# El cliente puede indicar otro archivo (lo usa la suite de seguridad con una copia).
DATA_FILE = Path(os.getenv("ACADEMICO_FILE") or Path(__file__).resolve().parent / "data" / "academico.json")
DATA = json.loads(DATA_FILE.read_text(encoding="utf-8"))
ESTUDIANTES: dict[str, Any] = DATA["estudiantes"]

EVALUACIONES_REGISTRABLES = ("parcial_1", "parcial_2", "proyecto")

# Solicitudes creadas en esta ejecución (en memoria: se pierden al cerrar el servidor).
SOLICITUDES: list[dict[str, Any]] = []

CodigoEstudiante = Annotated[str, Field(description="Código del estudiante, p. ej. 2026001")]

mcp = MCPServer(
    "servicios-academicos",
    instructions="Servicios académicos (ficticios) del curso de Inteligencia Artificial.",
    log_level="WARNING",
)


# --- Identidad y autorización ---------------------------------------------------------------


def identidad(ctx: Context) -> dict[str, Any]:
    meta = ctx.request_context.meta or {}
    return {"rol": meta.get("rol", "publico"), "codigo": meta.get("codigo"), "usuario_id": meta.get("usuario_id")}


def exigir_rol(ctx: Context, rol: str) -> dict[str, Any]:
    quien = identidad(ctx)
    if quien["rol"] != rol:
        # Mensaje genérico: no revela qué datos existen.
        raise ToolError(f"Acceso denegado: esta herramienta requiere el rol {rol}.")
    return quien


def codigo_de_sesion(ctx: Context) -> str:
    quien = exigir_rol(ctx, "estudiante")
    if not quien["codigo"]:
        raise ToolError("La sesión no tiene un código de estudiante asociado.")
    return str(quien["codigo"])


def buscar_estudiante(codigo: str) -> dict[str, Any]:
    estudiante = ESTUDIANTES.get(str(codigo).strip())
    if estudiante is None:
        raise ToolError(f"No existe un estudiante con código {codigo!r}.")
    return estudiante


def resumen_notas(codigo: str, estudiante: dict[str, Any], incluir_observaciones: bool) -> dict[str, Any]:
    notas = estudiante["notas"]
    laboratorios = notas["laboratorios"]
    resultado = {
        "codigo": codigo,
        "nombre": estudiante["nombre"],
        "notas": notas,
        "promedio_laboratorios": round(sum(laboratorios) / len(laboratorios), 2) if laboratorios else None,
    }
    if incluir_observaciones:
        resultado["observaciones_docente"] = estudiante.get("observaciones_docente")
    return resultado


def guardar_datos() -> None:
    """Escritura atómica: archivo temporal + reemplazo (no deja el JSON a medias)."""
    contenido = json.dumps(DATA, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=DATA_FILE.parent, delete=False, suffix=".tmp") as tmp:
        tmp.write(contenido)
    try:
        os.replace(tmp.name, DATA_FILE)
    except OSError:
        Path(tmp.name).unlink(missing_ok=True)
        raise ToolError("No se pudo guardar la nota en el sistema académico.") from None


# --- Tools del rol estudiante (sin parámetro de código) ---------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def consultar_mis_notas(ctx: Context) -> dict[str, Any]:
    """Consulta las notas del estudiante que inició sesión (sus propias notas).

    Una nota null significa que aún no está registrada. Incluye el promedio de los laboratorios.
    """
    codigo = codigo_de_sesion(ctx)
    return resumen_notas(codigo, buscar_estudiante(codigo), incluir_observaciones=False)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def consultar_mis_solicitudes(ctx: Context) -> list[dict[str, Any]]:
    """Lista las solicitudes de supletorio del estudiante que inició sesión."""
    codigo = codigo_de_sesion(ctx)
    return [s for s in SOLICITUDES if s["codigo"] == codigo]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def solicitar_supletorio(
    parcial: Annotated[Literal[1, 2], Field(description="Número del parcial: 1 o 2")],
    causa: Annotated[
        Literal["incapacidad médica", "calamidad doméstica", "representación institucional"],
        Field(description="Causa justificada de la inasistencia"),
    ],
    ctx: Context,
) -> dict[str, Any]:
    """Registra una solicitud de supletorio del estudiante que inició sesión.

    Es una ACCIÓN: crea un registro en el sistema académico.
    """
    codigo = codigo_de_sesion(ctx)
    estudiante = buscar_estudiante(codigo)
    if any(s["codigo"] == codigo and s["parcial"] == parcial for s in SOLICITUDES):
        raise ToolError(f"Ya existe una solicitud de supletorio del parcial {parcial}.")
    solicitud = {
        "radicado": f"SUP-2026-{len(SOLICITUDES) + 1:03d}",
        "codigo": codigo,
        "nombre": estudiante["nombre"],
        "parcial": parcial,
        "causa": causa,
        "estado": "pendiente: adjuntar el soporte por Teams dentro de los tres días hábiles siguientes al parcial",
    }
    SOLICITUDES.append(solicitud)
    return solicitud


# --- Tools del rol docente --------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def consultar_notas(codigo: CodigoEstudiante, ctx: Context) -> dict[str, Any]:
    """Consulta las notas de cualquier estudiante del curso, con las observaciones del docente."""
    exigir_rol(ctx, "docente")
    return resumen_notas(codigo, buscar_estudiante(codigo), incluir_observaciones=True)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def consultar_solicitudes(codigo: CodigoEstudiante, ctx: Context) -> list[dict[str, Any]]:
    """Lista las solicitudes de supletorio registradas por un estudiante."""
    exigir_rol(ctx, "docente")
    buscar_estudiante(codigo)
    return [s for s in SOLICITUDES if s["codigo"] == codigo]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def registrar_nota(
    codigo: CodigoEstudiante,
    evaluacion: Annotated[
        Literal["parcial_1", "parcial_2", "proyecto"], Field(description="Evaluación: parcial_1, parcial_2 o proyecto")
    ],
    valor: Annotated[float, Field(ge=0.0, le=5.0, description="Nota de 0.0 a 5.0")],
    ctx: Context,
) -> dict[str, Any]:
    """Registra (o corrige) la nota de una evaluación de un estudiante.

    Es una ACCIÓN de escritura: modifica el sistema académico.
    """
    exigir_rol(ctx, "docente")
    if evaluacion not in EVALUACIONES_REGISTRABLES:
        raise ToolError(f"evaluacion debe ser una de {', '.join(EVALUACIONES_REGISTRABLES)}.")
    if not 0.0 <= float(valor) <= 5.0:
        raise ToolError("valor debe estar entre 0.0 y 5.0.")
    estudiante = buscar_estudiante(codigo)
    anterior = estudiante["notas"].get(evaluacion)
    estudiante["notas"][evaluacion] = round(float(valor), 2)
    guardar_datos()
    return {
        "codigo": codigo,
        "nombre": estudiante["nombre"],
        "evaluacion": evaluacion,
        "nota_anterior": anterior,
        "nota_nueva": estudiante["notas"][evaluacion],
        "estado": "registrada",
    }


# --- Resource público -------------------------------------------------------------------------


@mcp.resource("academico://calendario", description="Fechas clave del curso (parciales y proyecto final)")
def calendario() -> str:
    return "\n".join(f"{item['fecha']}: {item['evento']}" for item in DATA["calendario"])


if __name__ == "__main__":
    # Solo stdio: la identidad del _meta es confiable porque el servidor es un proceso hijo del
    # asistente. Por HTTP, cualquiera que llegara al puerto podría declarar "rol": "docente"
    # (el modo --http del Lab 04 se retiró a propósito).
    mcp.run()
