"""Política de acceso: código determinista, la ÚNICA fuente de verdad de los permisos.

El LLM no decide quién puede ver qué. Este módulo responde dos preguntas:
  - ¿Qué niveles de documentos puede recuperar el RAG para esta sesión?
  - ¿Qué tools puede recibir (y ejecutar) el agente para esta sesión?

Lo usan el RAG (filtro antes del top-k), el orquestador (qué tools se le entregan al
modelo) y el agente (re-verificación al ejecutar: defensa en profundidad).
"""

from sesion import Rol, Sesion

NIVELES = ("publico", "estudiante", "docente")

NIVELES_POR_ROL: dict[Rol, frozenset[str]] = {
    "publico": frozenset({"publico"}),
    "estudiante": frozenset({"publico", "estudiante"}),
    "docente": frozenset({"publico", "estudiante", "docente"}),
}

# Tools locales del Lab 04 + calendario (resource MCP) + búsqueda en documentos (RAG).
TOOLS_PUBLICAS = frozenset({"fecha_actual", "dias_hasta", "calcular_nota_final", "calendario", "buscar_documentos"})

TOOLS_POR_ROL: dict[Rol, frozenset[str]] = {
    "publico": TOOLS_PUBLICAS,
    "estudiante": TOOLS_PUBLICAS
    | {"consultar_mis_notas", "consultar_mis_solicitudes", "solicitar_supletorio"},
    "docente": TOOLS_PUBLICAS | {"consultar_notas", "consultar_solicitudes", "registrar_nota"},
}

# Tools que manejan datos o acciones restringidas: su ejecución se audita.
TOOLS_RESTRINGIDAS = frozenset().union(*TOOLS_POR_ROL.values()) - TOOLS_PUBLICAS


def rol_efectivo(sesion: Sesion) -> Rol:
    """Una sesión vencida o sin usuario se trata como pública."""
    return sesion.rol if sesion.activa() else "publico"


def niveles_permitidos(sesion: Sesion) -> frozenset[str]:
    return NIVELES_POR_ROL[rol_efectivo(sesion)]


def tools_permitidas(sesion: Sesion) -> frozenset[str]:
    return TOOLS_POR_ROL[rol_efectivo(sesion)]


def tool_permitida(sesion: Sesion, nombre_tool: str) -> bool:
    return nombre_tool in tools_permitidas(sesion)


def es_restringida(nombre_tool: str) -> bool:
    return nombre_tool in TOOLS_RESTRINGIDAS


def mensaje_sin_permiso(sesion: Sesion) -> str:
    """Texto para el usuario cuando la respuesta está en un nivel que no puede ver."""
    if rol_efectivo(sesion) == "publico":
        return "Esa información es restringida: necesitas iniciar sesión con /login para consultarla."
    return "Tu rol no tiene acceso a esa información."
