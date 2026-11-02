"""Vector store en memoria: guarda fragmentos con sus vectores y busca los más parecidos.

Novedad del examen final: `search` recibe los NIVELES permitidos y enmascara los
fragmentos no permitidos ANTES de ordenar. Si se filtrara después del top-k, los
fragmentos restringidos ocuparían los k cupos y desplazarían a los públicos (y, peor,
cualquier error en el filtro posterior dejaría pasar texto restringido).
"""

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from documents import Chunk

VECTORS_FILE = "vectors.npy"
CHUNKS_FILE = "chunks.json"


@dataclass
class SearchResult:
    chunk: Chunk
    score: float  # similitud coseno entre la consulta y el fragmento (de -1 a 1)


def normalize(vectors: np.ndarray) -> np.ndarray:
    """Divide cada vector por su norma. Después, producto punto = similitud coseno."""
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


class VectorStore:
    def __init__(self, embedding_model: str):
        # Los vectores solo son comparables con otros producidos por el mismo modelo.
        self.embedding_model = embedding_model
        self.chunks: list[Chunk] = []
        self._vectors: np.ndarray | None = None  # una fila normalizada por fragmento

    def __len__(self) -> int:
        return len(self.chunks)

    def niveles(self) -> dict[str, int]:
        """Cuántos fragmentos hay de cada nivel (para el resumen de ingest.py)."""
        conteo: dict[str, int] = {}
        for chunk in self.chunks:
            conteo[chunk.nivel] = conteo.get(chunk.nivel, 0) + 1
        return conteo

    def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Debe haber exactamente un vector por fragmento.")
        vectors = normalize(np.asarray(vectors, dtype=np.float32))
        self._vectors = vectors if self._vectors is None else np.vstack([self._vectors, vectors])
        self.chunks.extend(chunks)

    def search(self, query_vector: np.ndarray, k: int, niveles: Iterable[str]) -> list[SearchResult]:
        """Devuelve los k fragmentos PERMITIDOS más parecidos a la consulta.

        `niveles` es obligatorio: no existe una búsqueda "sin filtro" por accidente.
        Los fragmentos de otros niveles reciben -inf antes del ordenamiento, así que
        nunca entran al top-k.
        """
        if self._vectors is None:
            return []
        permitidos = frozenset(niveles)
        mask = np.array([chunk.nivel in permitidos for chunk in self.chunks], dtype=bool)
        if not mask.any():
            return []
        scores = self._vectors @ normalize(query_vector)
        scores = np.where(mask, scores, -np.inf)
        top = np.argsort(scores)[::-1][: min(k, int(mask.sum()))]
        return [SearchResult(chunk=self.chunks[i], score=float(scores[i])) for i in top]

    def save(self, index_dir: Path) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        np.save(index_dir / VECTORS_FILE, self._vectors)
        data = {"embedding_model": self.embedding_model, "chunks": [asdict(c) for c in self.chunks]}
        (index_dir / CHUNKS_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, index_dir: Path) -> "VectorStore":
        chunks_path = index_dir / CHUNKS_FILE
        if not chunks_path.exists():
            raise FileNotFoundError(f"No existe el índice en {index_dir}. Ejecuta primero ingest.py.")
        data = json.loads(chunks_path.read_text(encoding="utf-8"))
        if any("nivel" not in c for c in data["chunks"]):
            # Un índice del Lab 04 no tiene niveles: usarlo dejaría todo como "publico".
            raise ValueError("El índice no tiene niveles de acceso. Vuelve a ejecutar ingest.py del examen final.")
        store = cls(data["embedding_model"])
        store.chunks = [Chunk(**c) for c in data["chunks"]]
        store._vectors = np.load(index_dir / VECTORS_FILE)
        return store
