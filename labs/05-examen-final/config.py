"""Configuración de la aplicación a partir de variables de entorno (.env).

Responsabilidad: reunir en un solo lugar todo lo que depende del entorno
(proveedor, credenciales, modelo y parámetros). Ningún otro módulo lee
variables de entorno directamente.

Novedad del examen final:
  - DATA_DIR apunta a labs/05-examen-final/data (público + restringido).
  - SecuritySettings: Telegram, umbral facial, sesiones, bloqueo y prueba de vida.
  - Rutas de modelos ONNX, embeddings de rostros y auditoría (todas ignoradas por git).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Todos estos proveedores exponen la misma API (Chat Completions, compatible con OpenAI).
PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
}

# Personalización (R7): el system prompt y /start usan este nombre.
AUTOR_ASISTENTE = "Daniel Felipe Aguilar Latorre"

LAB_DIR = Path(__file__).resolve().parent
DATA_DIR = LAB_DIR / "data"  # publico/ + restringido/estudiante/ + restringido/docente/
INDEX_DIR = LAB_DIR / "index"  # generado por ingest.py; no se versiona
USUARIOS_FILE = DATA_DIR / "usuarios.json"
ACADEMICO_FILE = DATA_DIR / "academico.json"
ATAQUES_FILE = DATA_DIR / "ataques.json"
MODELOS_DIR = LAB_DIR / "modelos"  # ONNX de opencv_zoo; no se versiona
ROSTROS_DIR = LAB_DIR / "rostros"  # solo embeddings (.npy); no se versiona
AUDIT_FILE = LAB_DIR / "auditoria.jsonl"  # no se versiona

ENV_FILE = LAB_DIR.parent.parent / ".env"  # raíz del repositorio

# El .env se carga AL IMPORTAR config, antes que cualquier otro módulo lea os.getenv a nivel de
# módulo (liveness.SIGNO_IZQUIERDA, YAW_MINIMO, bot.GUARDAR_VIDEOS_DIR). Antes se cargaba dentro de
# las funciones load_*, y esas constantes se quedaban con el valor por defecto sin avisar.
load_dotenv(ENV_FILE if ENV_FILE.exists() else find_dotenv(usecwd=True))

YUNET_MODEL = MODELOS_DIR / "face_detection_yunet_2023mar.onnx"
SFACE_MODEL = MODELOS_DIR / "face_recognition_sface_2021dec.onnx"


@dataclass(frozen=True)
class Settings:
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class RAGSettings:
    data_dir: Path
    index_dir: Path
    embedding_model: str
    chunk_size: int  # palabras por fragmento
    chunk_overlap: int  # palabras compartidas por fragmentos consecutivos
    top_k: int  # cuántos fragmentos recuperar por pregunta
    min_score: float  # similitud mínima para considerar relevante un fragmento


@dataclass(frozen=True)
class SecuritySettings:
    telegram_bot_token: str
    face_match_threshold: float  # similitud coseno mínima (justificada con FAR/FRR)
    session_minutes: float
    login_max_attempts: int
    lockout_minutes: float
    liveness_timeout_seconds: float


def _load_env() -> None:
    # Busca el archivo .env desde el directorio actual hacia arriba.
    load_dotenv(find_dotenv(usecwd=True))


def load_settings() -> Settings:
    _load_env()

    provider = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    if provider not in PROVIDER_BASE_URLS:
        options = ", ".join(PROVIDER_BASE_URLS)
        raise ValueError(f"LLM_PROVIDER desconocido: {provider!r}. Opciones: {options}")

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Falta LLM_API_KEY. Copia .env.example como .env y agrega tu clave.")

    model = os.getenv("LLM_MODEL", "").strip()
    if not model:
        raise ValueError("Falta LLM_MODEL en el archivo .env.")

    return Settings(
        provider=provider,
        base_url=PROVIDER_BASE_URLS[provider],
        api_key=api_key,
        model=model,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "512")),
    )


def load_rag_settings() -> RAGSettings:
    _load_env()

    chunk_size = int(os.getenv("RAG_CHUNK_SIZE", "80"))
    chunk_overlap = int(os.getenv("RAG_CHUNK_OVERLAP", "20"))
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("RAG_CHUNK_OVERLAP debe ser mayor o igual a 0 y menor que RAG_CHUNK_SIZE.")

    return RAGSettings(
        data_dir=DATA_DIR,
        index_dir=INDEX_DIR,
        embedding_model=os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        ).strip(),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        top_k=int(os.getenv("RAG_TOP_K", "4")),
        min_score=float(os.getenv("RAG_MIN_SCORE", "0.35")),
    )


def load_security_settings(require_token: bool = False) -> SecuritySettings:
    _load_env()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if require_token and not token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN en el .env (créalo con @BotFather).")

    settings = SecuritySettings(
        telegram_bot_token=token,
        face_match_threshold=float(os.getenv("FACE_MATCH_THRESHOLD", "0.40")),
        session_minutes=float(os.getenv("SESSION_MINUTES", "10")),
        login_max_attempts=int(os.getenv("LOGIN_MAX_ATTEMPTS", "3")),
        lockout_minutes=float(os.getenv("LOCKOUT_MINUTES", "5")),
        liveness_timeout_seconds=float(os.getenv("LIVENESS_TIMEOUT_SECONDS", "60")),
    )
    if not -1.0 <= settings.face_match_threshold <= 1.0:
        raise ValueError("FACE_MATCH_THRESHOLD debe estar entre -1 y 1 (similitud coseno).")
    if settings.session_minutes <= 0 or settings.liveness_timeout_seconds <= 0:
        raise ValueError("SESSION_MINUTES y LIVENESS_TIMEOUT_SECONDS deben ser positivos.")
    if settings.login_max_attempts < 1:
        raise ValueError("LOGIN_MAX_ATTEMPTS debe ser al menos 1.")
    return settings
