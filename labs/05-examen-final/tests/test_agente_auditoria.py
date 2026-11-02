import json

import numpy as np
import pytest

from agent import DENIED, Agent
from auditoria import Auditoria, AuditoriaMemoria
from conftest import ESTUDIANTE, sesion_de
from face import Rostro
from liveness import verificar_movimiento
from llm_client import LLMResponse, ToolCall
from nucleo import _con_aviso
from tools import Tool


def _tool(nombre: str, read_only: bool = True) -> Tool:
    async def run(arguments):
        return json.dumps({"ok": nombre})

    return Tool(nombre, "", {"type": "object", "properties": {}}, run, read_only, "test")


class LLMGuion:
    """LLM falso: primero pide consultar_notas (no permitida), luego responde."""

    def __init__(self):
        self.llamadas = 0

    def chat(self, messages, **kwargs):
        self.llamadas += 1
        if self.llamadas == 1:
            llamada = ToolCall("c1", "consultar_notas", '{"codigo":"2026001"}')
            return LLMResponse("", "fake", "tool_calls", 0, 0, [llamada])
        return LLMResponse("No tengo acceso.", "fake", "stop", 0, 0)


async def _confirm_si(tool, args):
    return True


@pytest.mark.asyncio
async def test_agente_niega_tool_no_permitida_aunque_exista():
    # Aun si por un error la tool estuviera en la lista, execute vuelve a verificar el rol.
    auditoria = AuditoriaMemoria()
    agente = Agent(LLMGuion(), [_tool("consultar_notas")], _confirm_si, sesion_de(ESTUDIANTE), auditoria)
    respuesta = await agente.run("dame las notas de 2026001")
    run = respuesta.steps[0].tool_runs[0]
    assert run.status == "denegada" and run.result == DENIED
    assert auditoria.eventos[0]["evento"] == "acceso_denegado"
    assert auditoria.eventos[0]["tool"] == "consultar_notas"


@pytest.mark.asyncio
async def test_accion_requiere_confirmacion():
    async def confirm_no(tool, args):
        return False

    agente = Agent(LLMGuion(), [_tool("solicitar_supletorio", read_only=False)], confirm_no, sesion_de(ESTUDIANTE))
    run = await agente.execute(ToolCall("c", "solicitar_supletorio", "{}"))
    assert run.status == "rechazada"


@pytest.mark.asyncio
async def test_tool_restringida_ejecutada_se_audita():
    auditoria = AuditoriaMemoria()
    agente = Agent(LLMGuion(), [_tool("consultar_mis_notas")], _confirm_si, sesion_de(ESTUDIANTE), auditoria)
    run = await agente.execute(ToolCall("c", "consultar_mis_notas", "{}"))
    assert run.status == "ok"
    assert auditoria.eventos[0]["evento"] == "tool_restringida_ejecutada"


def test_auditoria_jsonl_y_sin_biometria(tmp_path):
    auditoria = Auditoria(tmp_path / "auditoria.jsonl")
    auditoria.registrar("login_rechazado", 1, motivo="similitud_baja", similitud=0.123456)
    linea = json.loads((tmp_path / "auditoria.jsonl").read_text(encoding="utf-8"))
    assert linea["motivo"] == "similitud_baja" and linea["similitud"] == 0.1235 and "fecha" in linea
    with pytest.raises(ValueError):
        auditoria.registrar("login_aceptado", 1, embedding=[0.1])
    with pytest.raises(ValueError):
        auditoria.registrar("evento_inventado", 1)


def test_aviso_restringido_lo_garantiza_el_software():
    assert "/login" in _con_aviso("No encontré esa información.", sesion_de(None))
    assert _con_aviso("Debes iniciar sesión con /login.", sesion_de(None)).count("/login") == 1


# --- Movimiento de la prueba de vida con landmarks sintéticos ----------------------------------


def _rostro(nariz_x: float, ancho: float = 100) -> Rostro:
    fila = np.zeros(15, dtype=np.float32)
    fila[:4] = [0, 0, ancho, ancho]
    fila[4:6] = [40, 50]  # ojo derecho (de la persona)
    fila[6:8] = [60, 50]  # ojo izquierdo
    fila[8:10] = [nariz_x, 60]  # nariz; medio de los ojos en x=50, distancia 20
    return Rostro(fila)


def _secuencia(*narices: float) -> list[Rostro]:
    return [_rostro(x) for x in narices]


def test_giro_izquierda_detectado():
    ok, _ = verificar_movimiento("girar_izquierda", _secuencia(50, 50, 53, 57, 50))
    assert ok


def test_sin_movimiento_no_pasa():
    for reto in ("girar_izquierda", "girar_derecha", "girar_izquierda_y_derecha"):
        assert not verificar_movimiento(reto, _secuencia(50, 50, 50.5, 50, 49.8))[0]


def test_giro_en_sentido_contrario_no_pasa():
    assert not verificar_movimiento("girar_derecha", _secuencia(50, 50, 55, 58, 52))[0]


def test_izquierda_y_derecha_exige_orden():
    assert verificar_movimiento("girar_izquierda_y_derecha", _secuencia(50, 50, 57, 50, 43))[0]
    assert not verificar_movimiento("girar_izquierda_y_derecha", _secuencia(50, 50, 43, 50, 57))[0]


def test_debe_empezar_de_frente():
    assert not verificar_movimiento("girar_izquierda", _secuencia(58, 58, 62, 66))[0]


def test_acercarse():
    rostros = [_rostro(50, ancho=a) for a in (100, 100, 120, 140)]
    assert verificar_movimiento("acercarse", rostros)[0]
    assert not verificar_movimiento("acercarse", [_rostro(50, 100) for _ in range(4)])[0]


@pytest.mark.asyncio
async def test_sesion_que_vence_durante_la_confirmacion_no_ejecuta():
    import asyncio

    sesion = sesion_de(ESTUDIANTE, minutos=0.002)  # vence en ~0.1 s
    auditoria = AuditoriaMemoria()

    async def confirm_lento(tool, args):
        await asyncio.sleep(0.3)
        return True

    agente = Agent(LLMGuion(), [_tool("solicitar_supletorio", read_only=False)], confirm_lento, sesion, auditoria)
    run = await agente.execute(ToolCall("c", "solicitar_supletorio", "{}"))
    assert run.status == "denegada"
    assert auditoria.eventos[-1]["motivo"] == "sesion_vencida_durante_confirmacion"
