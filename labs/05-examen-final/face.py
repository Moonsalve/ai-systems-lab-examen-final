"""Rostros: detección (YuNet) y reconocimiento (SFace) con OpenCV, en CPU y en local.

    imagen → FaceDetectorYN → [bbox + 5 landmarks + score] por rostro
    imagen + rostro → FaceRecognizerSF.alignCrop → feature → embedding (128 dims, normalizado)
    similitud(a, b) = coseno (igual que FaceRecognizerSF.match con FR_COSINE)

Es la misma idea del RAG del Lab 02, pero con caras: el "documento" es el rostro enrolado,
la "consulta" es el rostro del login y el umbral decide si son la misma persona.

Landmarks de YuNet (en este orden): ojo derecho, ojo izquierdo, punta de la nariz,
comisura derecha, comisura izquierda (derecha/izquierda DE LA PERSONA).

Los modelos ONNX se descargan de opencv/opencv_zoo a modelos/ (no se versionan).
"""

import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from config import SFACE_MODEL, YUNET_MODEL

SCORE_DETECCION = 0.8  # confianza mínima de YuNet para contar un rostro
MAX_LADO = 1280  # las fotos grandes se reducen: más rápido y sin pérdida para estos modelos


class RostroError(ValueError):
    """La imagen no sirve: no tiene rostro o tiene más de uno."""

    def __init__(self, motivo: str, cantidad: int):
        super().__init__(motivo)
        self.cantidad = cantidad


@dataclass(frozen=True)
class Rostro:
    fila: np.ndarray  # la fila cruda de YuNet (15 valores), la necesita alignCrop

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        x, y, w, h = self.fila[:4]
        return float(x), float(y), float(w), float(h)

    @property
    def ojo_der(self) -> np.ndarray:
        return self.fila[4:6]

    @property
    def ojo_izq(self) -> np.ndarray:
        return self.fila[6:8]

    @property
    def nariz(self) -> np.ndarray:
        return self.fila[8:10]

    @property
    def score(self) -> float:
        return float(self.fila[14])

    @property
    def ancho(self) -> float:
        return self.bbox[2]

    def yaw(self) -> float:
        """Giro horizontal aproximado: desplazamiento de la nariz respecto al punto medio de los ojos,
        normalizado por la distancia entre ojos. ≈0 de frente; el signo depende del lado (y de si la
        cámara espeja la imagen)."""
        medio = (self.ojo_der + self.ojo_izq) / 2
        distancia = float(np.linalg.norm(self.ojo_der - self.ojo_izq))
        return float((self.nariz[0] - medio[0]) / max(distancia, 1e-6))


def normalizar(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    return v / max(float(np.linalg.norm(v)), 1e-12)


def similitud(a: np.ndarray, b: np.ndarray) -> float:
    """Similitud coseno entre dos embeddings (de -1 a 1)."""
    return float(np.dot(normalizar(a), normalizar(b)))


def reducir(img: np.ndarray, max_lado: int = MAX_LADO) -> np.ndarray:
    h, w = img.shape[:2]
    escala = max_lado / max(h, w)
    if escala >= 1:
        return img
    return cv2.resize(img, (int(w * escala), int(h * escala)), interpolation=cv2.INTER_AREA)


class MotorFacial:
    """Envuelve los dos modelos. Las redes de OpenCV no son seguras entre hilos: se usa un lock."""

    def __init__(self, yunet: Path = YUNET_MODEL, sface: Path = SFACE_MODEL, score: float = SCORE_DETECCION):
        for modelo in (yunet, sface):
            if not modelo.exists():
                raise FileNotFoundError(f"Falta el modelo {modelo.name} en {modelo.parent} (ver README del examen).")
        self._detector = cv2.FaceDetectorYN.create(str(yunet), "", (320, 320), score, 0.3, 5000)
        self._recognizer = cv2.FaceRecognizerSF.create(str(sface), "")
        self._lock = threading.Lock()

    def detectar(self, img: np.ndarray) -> list[Rostro]:
        h, w = img.shape[:2]
        with self._lock:
            self._detector.setInputSize((w, h))
            _, faces = self._detector.detect(img)
        if faces is None:
            return []
        return [Rostro(fila=f) for f in faces]

    def embedding(self, img: np.ndarray, rostro: Rostro) -> np.ndarray:
        with self._lock:
            alineado = self._recognizer.alignCrop(img, rostro.fila)
            feature = self._recognizer.feature(alineado)
        return normalizar(feature)

    def rostro_unico(self, img: np.ndarray) -> Rostro:
        """El único rostro de la imagen. Lanza RostroError si hay 0 o más de 1."""
        rostros = self.detectar(img)
        if not rostros:
            raise RostroError("No se detectó ningún rostro.", 0)
        if len(rostros) > 1:
            raise RostroError(f"Se detectaron {len(rostros)} rostros; debe haber exactamente uno.", len(rostros))
        return rostros[0]

    def embedding_de_archivo(self, path: Path) -> np.ndarray:
        img = cargar_imagen(path)
        return self.embedding(img, self.rostro_unico(img))


def cargar_imagen(path: Path) -> np.ndarray:
    # imdecode + fromfile soporta rutas con tildes o espacios en Windows (cv2.imread no siempre).
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"No se pudo leer la imagen {path}")
    return reducir(img)


# --- Embeddings enrolados (rostros/<usuario_id>.npy: matriz n×128) ---------------------------


def ruta_embeddings(rostros_dir: Path, usuario_id: str) -> Path:
    return rostros_dir / f"{usuario_id}.npy"


def cargar_enrolados(rostros_dir: Path, usuario_id: str) -> np.ndarray | None:
    path = ruta_embeddings(rostros_dir, usuario_id)
    if not path.exists():
        return None
    return np.load(path)


def similitud_con_enrolados(embedding: np.ndarray, enrolados: np.ndarray) -> float:
    """Verificación 1:1: media de la similitud contra los n embeddings del MISMO usuario."""
    sims = [similitud(embedding, e) for e in enrolados]
    return float(np.mean(sims))
