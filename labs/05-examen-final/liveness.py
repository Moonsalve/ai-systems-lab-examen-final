"""Prueba de vida sobre una video-nota de Telegram (mp4).

Verificaciones (todas obligatorias; si una falla, se rechaza):
  1. Rostros válidos: ningún frame analizado tiene más de un rostro, y al menos el 70 % de
     los frames muestreados tiene exactamente uno (al girar mucho, YuNet puede perder la cara
     en algún frame; eso se tolera, dos caras no).
  2. Mismo rostro: el frame más frontal de la primera mitad y el de la segunda mitad se
     parecen (similitud ≥ umbral). Detecta que alguien cambie la cara a mitad del video.
  3. Movimiento pedido, medido con landmarks:
       yaw = (nariz_x − centro_del_rostro_x) / ancho_del_rostro
       girar_*: el yaw parte cerca de 0 (frente) y alcanza |Δyaw| ≥ YAW_MINIMO en el sentido
                pedido; en los retos de un lado, el giro al lado contrario debe quedar bajo el mínimo. Una FOTO girada frente a la cámara no cambia este cociente (es plana),
                por eso el giro detecta fotos mostradas en otra pantalla.
       acercarse: ancho_máximo_del_rostro / ancho_inicial ≥ CRECIMIENTO_MINIMO.
  4. Identidad 1:1: el frame más frontal se compara con los embeddings del DUEÑO de la cuenta.

Devuelve (ok, motivo, similitud). El motivo solo va a la auditoría, nunca al usuario.

Calibración del signo (la cámara frontal puede espejar):
    uv run python labs/05-examen-final/liveness.py --calibrar video.mp4
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from face import MotorFacial, Rostro, similitud, similitud_con_enrolados

FRAMES_A_MUESTREAR = 16
FRACCION_MIN_FRAMES_VALIDOS = 0.7
MIN_FRAMES_VALIDOS = 6
# Calibrados con video-notas reales (ver informe): quieto ≤ 0.04, giro suave ~0.46, giro completo 0.5–0.65.
YAW_FRONTAL_MAX = 0.15  # |yaw| inicial máximo para considerar que empezó de frente
YAW_MINIMO = float(os.getenv("LIVENESS_YAW_MINIMO", "0.25"))  # cuánto debe girar hacia el lado pedido
CRECIMIENTO_MINIMO = 1.3
# Signo del yaw cuando la persona gira a SU izquierda. +1 en video sin espejo; calibrar con
# --calibrar y, si la cámara espeja, poner LIVENESS_SIGNO_IZQUIERDA=-1 en el .env.
SIGNO_IZQUIERDA = float(os.getenv("LIVENESS_SIGNO_IZQUIERDA", "1"))


@dataclass
class Muestra:
    indice: int
    frame: np.ndarray
    rostros: list[Rostro]


@dataclass
class ResultadoVida:
    ok: bool
    motivo: str  # "ok" o la verificación que falló (solo para la auditoría)
    similitud: float | None = None
    detalles: dict = field(default_factory=dict)


def muestrear_frames(video: Path, n: int = FRAMES_A_MUESTREAR) -> list[tuple[int, np.ndarray]]:
    """Lee el video y devuelve ~n frames distribuidos uniformemente (sin decodificar todos)."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:  # algunos mp4 no traen el conteo: se cuenta con grab() (barato)
            total = 0
            while cap.grab():
                total += 1
            cap.release()
            cap = cv2.VideoCapture(str(video))
        if total <= 0:
            return []
        objetivos = set(np.linspace(0, total - 1, num=min(n, total)).astype(int).tolist())
        frames = []
        for i in range(total):
            if not cap.grab():
                break
            if i in objetivos:
                ok, frame = cap.retrieve()
                if ok:
                    frames.append((i, frame))
        return frames
    finally:
        cap.release()


def _yaw_izquierda(rostro: Rostro) -> float:
    """Yaw con el signo calibrado: positivo = girado hacia la izquierda de la persona."""
    return rostro.yaw() * SIGNO_IZQUIERDA


def verificar_movimiento(reto: str, rostros: list[Rostro]) -> tuple[bool, dict]:
    yaws = [_yaw_izquierda(r) for r in rostros]
    anchos = [r.ancho for r in rostros]
    inicio = float(np.median(yaws[:2]))
    detalles = {"yaw_inicial": round(inicio, 3), "yaw_min": round(min(yaws), 3), "yaw_max": round(max(yaws), 3)}

    if reto == "acercarse":
        crecimiento = max(anchos) / max(float(np.median(anchos[:2])), 1e-6)
        detalles["crecimiento"] = round(crecimiento, 3)
        return crecimiento >= CRECIMIENTO_MINIMO, detalles

    if abs(inicio) > YAW_FRONTAL_MAX:  # debe empezar mirando a la cámara
        return False, detalles
    giro_izq = max(yaws) - inicio  # cuánto llegó a la izquierda
    giro_der = inicio - min(yaws)  # cuánto llegó a la derecha
    # En los retos de un lado, el giro hacia el lado CONTRARIO debe quedarse por debajo del mínimo:
    # si no, un video que gira a ambos lados (p. ej. pregrabado) pasaría cualquier reto.
    if reto == "girar_izquierda":
        return giro_izq >= YAW_MINIMO and giro_der < YAW_MINIMO, detalles
    if reto == "girar_derecha":
        return giro_der >= YAW_MINIMO and giro_izq < YAW_MINIMO, detalles
    if reto == "girar_izquierda_y_derecha":
        i_izq, i_der = int(np.argmax(yaws)), int(np.argmin(yaws))
        orden_ok = i_izq < i_der  # primero izquierda, luego derecha
        return giro_izq >= YAW_MINIMO and giro_der >= YAW_MINIMO and orden_ok, detalles
    raise ValueError(f"Reto desconocido: {reto!r}")


def verificar(
    motor: MotorFacial, video: Path, reto: str, enrolados: np.ndarray, umbral: float
) -> ResultadoVida:
    frames = muestrear_frames(video)
    if not frames:
        return ResultadoVida(False, "video_ilegible")

    muestras = [Muestra(i, f, motor.detectar(f)) for i, f in frames]
    max_rostros = max(len(m.rostros) for m in muestras)
    if max_rostros > 1:
        return ResultadoVida(False, "rostros_invalidos", detalles={"max_rostros_en_un_frame": max_rostros})
    validas = [m for m in muestras if len(m.rostros) == 1]
    if len(validas) < max(MIN_FRAMES_VALIDOS, FRACCION_MIN_FRAMES_VALIDOS * len(muestras)):
        return ResultadoVida(
            False, "rostros_invalidos", detalles={"n_muestras": len(muestras), "n_con_un_rostro": len(validas)}
        )
    rostros = [m.rostros[0] for m in validas]

    # 3. Movimiento pedido
    movimiento_ok, detalles = verificar_movimiento(reto, rostros)
    detalles["n_validas"] = len(validas)
    if not movimiento_ok:
        return ResultadoVida(False, "movimiento_no_detectado", detalles=detalles)

    # 2. Mismo rostro en todo el video: frontal de la 1.ª mitad vs frontal de la 2.ª mitad
    mitad = len(validas) // 2
    a = min(validas[:mitad], key=lambda m: abs(m.rostros[0].yaw()))
    b = min(validas[mitad:], key=lambda m: abs(m.rostros[0].yaw()))
    emb_a = motor.embedding(a.frame, a.rostros[0])
    emb_b = motor.embedding(b.frame, b.rostros[0])
    continuidad = similitud(emb_a, emb_b)
    detalles["continuidad"] = round(continuidad, 3)
    if continuidad < umbral:
        return ResultadoVida(False, "rostro_cambio", detalles=detalles)

    # 4. Identidad 1:1 con el frame más frontal de todo el video
    frontal = min(validas, key=lambda m: abs(m.rostros[0].yaw()))
    emb = motor.embedding(frontal.frame, frontal.rostros[0])
    sim = similitud_con_enrolados(emb, enrolados)
    if sim < umbral:
        return ResultadoVida(False, "similitud_baja", similitud=sim, detalles=detalles)
    return ResultadoVida(True, "ok", similitud=sim, detalles=detalles)


def _calibrar(video: Path) -> None:
    motor = MotorFacial()
    print(f"Signo configurado para IZQUIERDA: {SIGNO_IZQUIERDA:+.0f}")
    print("frame  rostros  yaw     ancho")
    for i, frame in muestrear_frames(video, 30):
        rostros = motor.detectar(frame)
        if len(rostros) == 1:
            print(f"{i:5d}  1        {rostros[0].yaw():+.3f}  {rostros[0].ancho:.0f}")
        else:
            print(f"{i:5d}  {len(rostros)}")
    print("\nSi giraste a TU izquierda y el yaw crudo se volvió NEGATIVO, la cámara espeja:")
    print("agrega LIVENESS_SIGNO_IZQUIERDA=-1 al .env")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--calibrar":
        _calibrar(Path(sys.argv[2]))
    else:
        print("Uso: uv run python labs/05-examen-final/liveness.py --calibrar video.mp4")
