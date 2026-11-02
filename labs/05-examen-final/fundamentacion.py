"""Chequeo determinista de fechas no fundamentadas.

Problema observado con el LLM real: sin sesión, a "¿Cuándo es el simulacro del segundo
parcial?" el modelo no recibe el documento restringido (el filtro funciona), pero a veces
DEDUCE una fecha ("el lunes de la semana del parcial") y la presenta como un hecho. A veces
acierta por casualidad (23 de noviembre) y parece una fuga; otras veces se equivoca.

Regla (código, no prompt): si en esta pregunta había información relevante en documentos que
la sesión NO puede ver, toda fecha de la respuesta debe aparecer en alguna fuente que el modelo
sí vio (fragmentos permitidos o resultados de tools, incluido el calendario ISO del MCP). Si
aparece una fecha que no está en ninguna fuente, la respuesta se descarta y el software
responde con el aviso de acceso restringido.
"""

import re
import unicodedata
from collections.abc import Iterable

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

_TEXTO = re.compile(r"\b(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")\b")
_ISO = re.compile(r"\b\d{4}-(\d{2})-(\d{2})\b")
_BARRA = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, sin negritas y con espacios simples (el modelo usa U+202F)."""
    texto = unicodedata.normalize("NFKC", texto).replace("*", "").replace("_", " ")
    texto = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).lower()


def fechas(texto: str) -> set[tuple[int, int]]:
    """(día, mes) de cada fecha del texto: '23 de noviembre', '2026-11-23' o '23/11'."""
    t = _normalizar(texto)
    encontradas = {(int(d), MESES[m]) for d, m in _TEXTO.findall(t)}
    encontradas |= {(int(d), int(m)) for m, d in _ISO.findall(t)}
    encontradas |= {(int(d), int(m)) for d, m in _BARRA.findall(t) if 1 <= int(m) <= 12}
    return {(d, m) for d, m in encontradas if 1 <= d <= 31}


def fechas_no_fundamentadas(respuesta: str, fuentes: Iterable[str]) -> set[tuple[int, int]]:
    """Fechas de la respuesta que no aparecen en ninguna de las fuentes que vio el modelo."""
    vistas: set[tuple[int, int]] = set()
    for fuente in fuentes:
        vistas |= fechas(fuente)
    return fechas(respuesta) - vistas
