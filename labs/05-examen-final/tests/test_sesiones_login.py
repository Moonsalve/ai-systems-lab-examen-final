from pathlib import Path

import numpy as np
import pytest

import login as login_mod
from auditoria import AuditoriaMemoria
from conftest import DOCENTE, ESTUDIANTE
from liveness import ResultadoVida
from login import MENSAJE_BLOQUEO, MENSAJE_RECHAZO, ServicioLogin
from sesiones import RETOS_ACTIVOS, SessionStore


def _store(reloj) -> SessionStore:
    return SessionStore(
        session_minutes=10, liveness_timeout_seconds=60, max_intentos=3, lockout_minutes=5, reloj=reloj
    )


def test_reto_aleatorio_entre_al_menos_tres(reloj):
    store = _store(reloj)
    assert len(RETOS_ACTIVOS) >= 3
    vistos = {store.emitir_reto(1).nombre for _ in range(60)}
    assert len(vistos) >= 2  # aleatorio (con 60 sorteos es prácticamente seguro)


def test_reto_se_usa_una_sola_vez(reloj):
    store = _store(reloj)
    store.emitir_reto(1)
    reto, motivo = store.consumir_reto(1)
    assert reto is not None and motivo is None
    assert store.consumir_reto(1) == (None, "reto_usado")


def test_reto_vencido(reloj):
    store = _store(reloj)
    store.emitir_reto(1)
    reloj.avanzar(seconds=61)
    assert store.consumir_reto(1) == (None, "reto_vencido")


def test_sin_reto(reloj):
    assert _store(reloj).consumir_reto(1) == (None, "sin_reto")


def test_bloqueo_tras_tres_fallos_y_desbloqueo(reloj):
    store = _store(reloj)
    assert not store.registrar_fallo(1)
    assert not store.registrar_fallo(1)
    assert store.registrar_fallo(1)
    assert store.bloqueado(1)
    reloj.avanzar(minutes=5, seconds=1)
    assert not store.bloqueado(1)


def test_sesion_con_datos_de_usuarios_json_y_expiracion(reloj):
    store = _store(reloj)
    sesion = store.login_ok(111, ESTUDIANTE)
    assert (sesion.rol, sesion.codigo, sesion.nombre) == ("estudiante", "2026010", "Ana Prueba")
    assert store.obtener(111)[0].rol == "estudiante"
    reloj.avanzar(minutes=11)
    sesion, expiro = store.obtener(111)
    assert expiro and sesion.rol == "publico"


def test_docente_no_lleva_codigo_y_logout(reloj):
    store = _store(reloj)
    assert store.login_ok(222, DOCENTE).codigo is None
    assert store.logout(222)
    assert store.obtener(222)[0].rol == "publico"


# --- ServicioLogin (sin Telegram; la prueba de vida se sustituye) ------------------------------


@pytest.fixture
def servicio(reloj, tmp_path, monkeypatch):
    np.save(tmp_path / "integrante_1.npy", np.ones((3, 128), dtype=np.float32))
    auditoria = AuditoriaMemoria()
    svc = ServicioLogin(_store(reloj), [ESTUDIANTE, DOCENTE], object(), tmp_path, 0.4, auditoria)
    svc.resultado = ResultadoVida(True, "ok", similitud=0.71)
    monkeypatch.setattr(login_mod, "verificar", lambda *a, **k: svc.resultado)
    return svc


def test_login_exitoso_saluda_por_nombre(servicio):
    reto, error = servicio.iniciar(111)
    assert reto is not None and error is None
    resultado = servicio.verificar_video(111, Path("x.mp4"))
    assert resultado.ok and "Ana Prueba" in resultado.mensaje
    assert servicio.auditoria.eventos[-1]["evento"] == "login_aceptado"


def test_rechazo_no_revela_el_motivo_pero_lo_audita(servicio):
    servicio.iniciar(111)
    servicio.resultado = ResultadoVida(False, "movimiento_no_detectado")
    resultado = servicio.verificar_video(111, Path("x.mp4"))
    assert not resultado.ok and resultado.mensaje == MENSAJE_RECHAZO
    assert "movimiento" not in resultado.mensaje
    assert servicio.auditoria.eventos[-1]["motivo"] == "movimiento_no_detectado"


def test_usuario_no_enrolado_es_rechazado(servicio):
    servicio.iniciar(222)  # el docente no tiene rostros/integrante_2.npy en esta prueba
    assert not servicio.verificar_video(222, Path("x.mp4")).ok
    assert servicio.auditoria.eventos[-1]["motivo"] == "usuario_no_enrolado"


def test_cuenta_no_registrada(servicio):
    reto, error = servicio.iniciar(555)
    assert reto is None and "no está registrada" in error


def test_reenviado_gasta_el_reto(servicio):
    servicio.iniciar(111)
    servicio.rechazar_entrada(111, "reenviado")
    assert servicio.verificar_video(111, Path("x.mp4")).ok is False
    motivos = [e["motivo"] for e in servicio.auditoria.eventos]
    assert "reenviado" in motivos and "reto_usado" in motivos


def test_tres_fallos_bloquean_el_login(servicio):
    servicio.resultado = ResultadoVida(False, "similitud_baja", similitud=0.1)
    for _ in range(3):
        servicio.iniciar(111)
        ultimo = servicio.verificar_video(111, Path("x.mp4"))
    assert ultimo.mensaje == MENSAJE_BLOQUEO
    reto, error = servicio.iniciar(111)
    assert reto is None and "bloqueado" in error


# --- Regresiones encontradas en la revisión de seguridad --------------------------------------


@pytest.mark.parametrize(
    "detalles",
    [{"max_rostros_en_un_frame": 2}, {"n_muestras": 16, "n_con_un_rostro": 3}, {"motivo": "x", "frames": 3}],
)
def test_rechazo_con_detalles_cuenta_para_el_bloqueo_y_no_rompe(servicio, tmp_path, detalles):
    from auditoria import Auditoria

    servicio.auditoria = Auditoria(tmp_path / "auditoria.jsonl")  # la real, con sus validaciones
    servicio.resultado = ResultadoVida(False, "rostros_invalidos", detalles=detalles)
    servicio.iniciar(111)
    resultado = servicio.verificar_video(111, Path("x.mp4"))
    assert resultado.mensaje == MENSAJE_RECHAZO
    assert servicio.store.intentos_restantes(111) == 2
    assert "rostros_invalidos" in (tmp_path / "auditoria.jsonl").read_text(encoding="utf-8")


def test_video_anterior_al_reto_se_rechaza(servicio, reloj):
    from datetime import timedelta

    servicio.iniciar(111)
    resultado = servicio.verificar_video(111, Path("x.mp4"), enviado_en=reloj() - timedelta(minutes=1))
    assert not resultado.ok
    assert servicio.auditoria.eventos[-1]["motivo"] == "video_anterior_al_reto"
