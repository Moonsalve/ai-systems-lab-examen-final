import json

import numpy as np
import pytest

from documents import Chunk, chunk_documents, load_documents
from rag import CourseRAG
from vector_store import VectorStore


def _store() -> VectorStore:
    """3 fragmentos restringidos MUY parecidos a la consulta y 2 públicos menos parecidos."""
    store = VectorStore("fake")
    chunks = [
        Chunk("d#0", "casos.md", "secreto docente", "docente"),
        Chunk("e#0", "guia.md", "simulacro 23 de noviembre", "estudiante"),
        Chunk("e#1", "guia.md", "temas del parcial", "estudiante"),
        Chunk("p#0", "evaluacion.md", "segundo parcial 26 de noviembre", "publico"),
        Chunk("p#1", "anuncios.md", "anuncio", "publico"),
    ]
    vectors = np.array([[1, 0.0], [0.99, 0.1], [0.98, 0.2], [0.6, 0.8], [0.0, 1.0]], dtype=np.float32)
    store.add(chunks, vectors)
    return store


class EmbedderFijo:
    def embed_query(self, text: str) -> np.ndarray:
        return np.array([1.0, 0.0], dtype=np.float32)


def test_filtro_ocurre_antes_del_top_k():
    # Si se filtrara DESPUÉS del top-3, el público no recibiría ningún fragmento.
    resultados = _store().search(np.array([1.0, 0.0]), k=3, niveles={"publico"})
    assert [r.chunk.nivel for r in resultados] == ["publico", "publico"]


def test_estudiante_no_ve_docente():
    resultados = _store().search(np.array([1.0, 0.0]), k=5, niveles={"publico", "estudiante"})
    assert "docente" not in {r.chunk.nivel for r in resultados}
    assert len(resultados) == 4


def test_sin_niveles_no_devuelve_nada():
    assert _store().search(np.array([1.0, 0.0]), k=5, niveles=set()) == []


def test_rag_marca_restringido_sin_entregar_su_texto():
    rag = CourseRAG(None, EmbedderFijo(), _store(), top_k=3, min_score=0.3)
    recuperacion = rag.recuperar("¿cuándo es el simulacro?", {"publico"})
    assert recuperacion.restringido is True
    assert all(r.chunk.nivel == "publico" for r in recuperacion.resultados)
    assert "simulacro" not in " ".join(r.chunk.text for r in recuperacion.resultados)


def test_docente_no_tiene_nada_restringido():
    rag = CourseRAG(None, EmbedderFijo(), _store(), top_k=3, min_score=0.3)
    assert rag.recuperar("x", {"publico", "estudiante", "docente"}).restringido is False


def test_save_load_conserva_nivel(tmp_path):
    store = _store()
    store.save(tmp_path)
    cargado = VectorStore.load(tmp_path)
    assert [c.nivel for c in cargado.chunks] == [c.nivel for c in store.chunks]


def test_indice_sin_niveles_se_rechaza(tmp_path):
    store = _store()
    store.save(tmp_path)
    data = json.loads((tmp_path / "chunks.json").read_text(encoding="utf-8"))
    for c in data["chunks"]:
        del c["nivel"]
    (tmp_path / "chunks.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        VectorStore.load(tmp_path)


def test_load_documents_etiqueta_por_carpeta(tmp_path):
    (tmp_path / "publico").mkdir()
    (tmp_path / "restringido" / "estudiante").mkdir(parents=True)
    (tmp_path / "restringido" / "docente").mkdir(parents=True)
    (tmp_path / "publico" / "a.md").write_text("uno dos", encoding="utf-8")
    (tmp_path / "restringido" / "estudiante" / "b.md").write_text("tres", encoding="utf-8")
    (tmp_path / "restringido" / "docente" / "c.md").write_text("cuatro", encoding="utf-8")
    (tmp_path / "publico" / "x.json").write_text("{}", encoding="utf-8")  # se ignora
    docs = load_documents(tmp_path)
    assert {(d.source, d.nivel) for d in docs} == {("a.md", "publico"), ("b.md", "estudiante"), ("c.md", "docente")}
    assert {c.nivel for c in chunk_documents(docs, 5, 1)} == {"publico", "estudiante", "docente"}
