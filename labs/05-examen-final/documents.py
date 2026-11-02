"""Carga de documentos y chunking.

Novedad del examen final: cada documento y cada fragmento llevan su NIVEL de acceso,
que se decide por la carpeta de origen (no por el contenido del archivo):

    data/publico/*.md                  → "publico"
    data/restringido/estudiante/*.md   → "estudiante"
    data/restringido/docente/*.md      → "docente"

Uso (prueba de niveles, sin modelos ni API):
    uv run python labs/05-examen-final/documents.py
"""

from dataclasses import dataclass
from pathlib import Path

# Carpeta relativa a data/ → nivel. Lo que no esté aquí NO se indexa.
CARPETAS_POR_NIVEL: dict[str, Path] = {
    "publico": Path("publico"),
    "estudiante": Path("restringido") / "estudiante",
    "docente": Path("restringido") / "docente",
}


@dataclass(frozen=True)
class Document:
    source: str  # nombre del archivo, p. ej. "evaluacion.md"
    text: str
    nivel: str = "publico"


@dataclass(frozen=True)
class Chunk:
    id: str  # documento de origen + posición, p. ej. "evaluacion.md#2"
    source: str
    text: str
    nivel: str = "publico"


def load_documents(data_dir: Path) -> list[Document]:
    """Recorre publico/ y restringido/<rol>/ y etiqueta cada documento con su nivel.

    Solo lee .md (los .json del sistema académico no son documentos del RAG).
    """
    documents = []
    for nivel, carpeta in CARPETAS_POR_NIVEL.items():
        for path in sorted((data_dir / carpeta).glob("*.md")):
            documents.append(Document(source=path.name, text=path.read_text(encoding="utf-8"), nivel=nivel))
    return documents


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Divide el texto en ventanas de `chunk_size` palabras.

    Ventanas consecutivas comparten `overlap` palabras, para no cortar una idea
    justo en el borde entre dos fragmentos.
    """
    words = text.split()
    step = chunk_size - overlap
    chunks = []
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start : start + chunk_size]))
        if start + chunk_size >= len(words):
            break
    return chunks


def chunk_documents(documents: list[Document], chunk_size: int, overlap: int) -> list[Chunk]:
    # El nivel del documento se hereda a cada fragmento: es lo que filtra el vector store.
    return [
        Chunk(id=f"{doc.source}#{i}", source=doc.source, text=text, nivel=doc.nivel)
        for doc in documents
        for i, text in enumerate(chunk_text(doc.text, chunk_size, overlap))
    ]


if __name__ == "__main__":
    from config import DATA_DIR

    for document in load_documents(DATA_DIR):
        print(f"{document.nivel:<11} {document.source}")
