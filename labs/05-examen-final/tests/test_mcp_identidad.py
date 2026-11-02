"""Integración real con el servidor MCP (subproceso stdio) sobre una COPIA de academico.json."""

import json
import shutil
from contextlib import AsyncExitStack

import pytest

from conftest import DOCENTE, ESTUDIANTE, LAB_DIR, sesion_de
from mcp_client import CatalogoMCP, open_client, to_tool
from tools import ToolExecutionError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def academico(tmp_path):
    copia = tmp_path / "academico.json"
    shutil.copy(LAB_DIR / "data" / "academico.json", copia)
    data = json.loads(copia.read_text(encoding="utf-8"))
    data["estudiantes"]["2026010"] = {
        "nombre": "Ana Prueba",
        "notas": {"parcial_1": 3.9, "parcial_2": None, "laboratorios": [4.0], "proyecto": None},
        "observaciones_docente": "Observación secreta",
    }
    copia.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return copia


async def _catalogo(stack, academico):
    return await CatalogoMCP.cargar(await open_client(stack, academico))


async def test_cada_rol_recibe_solo_sus_tools(academico):
    async with AsyncExitStack() as stack:
        catalogo = await _catalogo(stack, academico)
        publico = {t.name for t in catalogo.tools_para(sesion_de(None))}
        estudiante = {t.name for t in catalogo.tools_para(sesion_de(ESTUDIANTE))}
        docente = {t.name for t in catalogo.tools_para(sesion_de(DOCENTE))}
    assert publico == {"calendario"}
    assert estudiante == {"consultar_mis_notas", "consultar_mis_solicitudes", "solicitar_supletorio", "calendario"}
    assert docente == {"consultar_notas", "consultar_solicitudes", "registrar_nota", "calendario"}


async def test_mis_notas_sin_codigo_visible_ni_inyectable(academico):
    async with AsyncExitStack() as stack:
        tools = {t.name: t for t in (await _catalogo(stack, academico)).tools_para(sesion_de(ESTUDIANTE))}
        mis_notas = tools["consultar_mis_notas"]
        assert "codigo" not in json.dumps(mis_notas.parameters)
        resultado = json.loads(await mis_notas.run({"codigo": "2026001"}))  # el LLM intenta colar otro código
    assert resultado["codigo"] == "2026010"
    assert "observaciones_docente" not in resultado


async def test_servidor_revalida_el_rol(academico):
    async with AsyncExitStack() as stack:
        catalogo = await _catalogo(stack, academico)
        # Se fabrica a mano la tool de docente con la identidad de un ESTUDIANTE (como si el
        # orquestador fallara): el servidor la rechaza igual.
        mcp_tool = next(t for t in catalogo.mcp_tools if t.name == "consultar_notas")
        tool = to_tool(catalogo.client, mcp_tool, sesion_de(ESTUDIANTE))
        with pytest.raises(ToolExecutionError, match="Acceso denegado"):
            await tool.run({"codigo": "2026001"})


async def test_docente_registra_nota_valida_y_persiste(academico):
    async with AsyncExitStack() as stack:
        tools = {t.name: t for t in (await _catalogo(stack, academico)).tools_para(sesion_de(DOCENTE))}
        with pytest.raises(ToolExecutionError):
            await tools["registrar_nota"].run({"codigo": "2026010", "evaluacion": "parcial_1", "valor": 7})
        with pytest.raises(ToolExecutionError):
            await tools["registrar_nota"].run({"codigo": "2026010", "evaluacion": "laboratorios", "valor": 4})
        resultado = json.loads(
            await tools["registrar_nota"].run({"codigo": "2026010", "evaluacion": "proyecto", "valor": 4.5})
        )
        notas = json.loads(await tools["consultar_notas"].run({"codigo": "2026010"}))
    assert resultado["nota_nueva"] == 4.5
    assert notas["notas"]["proyecto"] == 4.5 and notas["observaciones_docente"] == "Observación secreta"
    assert json.loads(academico.read_text(encoding="utf-8"))["estudiantes"]["2026010"]["notas"]["proyecto"] == 4.5
