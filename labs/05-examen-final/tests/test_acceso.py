from datetime import timedelta

import acceso
from conftest import DOCENTE, ESTUDIANTE, sesion_de


def test_publico_solo_ve_nivel_publico_y_tools_publicas():
    sesion = sesion_de(None)
    assert acceso.niveles_permitidos(sesion) == {"publico"}
    assert acceso.tool_permitida(sesion, "buscar_documentos")
    assert not acceso.tool_permitida(sesion, "consultar_mis_notas")
    assert not acceso.tool_permitida(sesion, "consultar_notas")


def test_estudiante_no_recibe_tools_de_docente():
    sesion = sesion_de(ESTUDIANTE)
    assert acceso.niveles_permitidos(sesion) == {"publico", "estudiante"}
    assert acceso.tool_permitida(sesion, "consultar_mis_notas")
    for tool in ("consultar_notas", "consultar_solicitudes", "registrar_nota"):
        assert not acceso.tool_permitida(sesion, tool)


def test_docente_ve_todo_menos_tools_mis():
    sesion = sesion_de(DOCENTE)
    assert acceso.niveles_permitidos(sesion) == {"publico", "estudiante", "docente"}
    assert acceso.tool_permitida(sesion, "registrar_nota")
    assert not acceso.tool_permitida(sesion, "consultar_mis_notas")


def test_sesion_vencida_se_degrada_a_publico():
    sesion = sesion_de(DOCENTE, minutos=-1)
    assert not sesion.activa()
    assert acceso.rol_efectivo(sesion) == "publico"
    assert not acceso.tool_permitida(sesion, "consultar_notas")


def test_rol_sin_usuario_no_cuenta_como_sesion():
    # Aunque alguien construyera una sesión con rol "docente", sin usuario ni expiración no vale.
    falsa = sesion_de(None).con(rol="docente")
    assert acceso.rol_efectivo(falsa) == "publico"


def test_tools_restringidas_son_las_no_publicas():
    assert acceso.es_restringida("consultar_notas")
    assert not acceso.es_restringida("fecha_actual")


def test_mensaje_sin_permiso_depende_del_rol():
    assert "/login" in acceso.mensaje_sin_permiso(sesion_de(None))
    assert "rol" in acceso.mensaje_sin_permiso(sesion_de(ESTUDIANTE))
    sesion = sesion_de(ESTUDIANTE)
    assert sesion.minutos_restantes() > 9
    assert sesion.efectiva(sesion.expira + timedelta(seconds=1)).rol == "publico"
