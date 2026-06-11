import os
from pathlib import Path

from eba_pipeline.env import load_env

ROOT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
QUALITY_REPORTS_DIR = DATA_DIR / "quality_reports"
CORPORA_DIR = DATA_DIR / "corpora"
ENV_LOAD_RESULT = load_env(start_dir=ROOT_DIR)


def _configured_db_path() -> Path:
    configured = os.environ.get("EBA_DB_PATH")
    if not configured:
        return CORPORA_DIR / "eba-corpus.db"

    path = Path(configured)
    if not path.is_absolute() and ENV_LOAD_RESULT.env_path is not None and "EBA_DB_PATH" in ENV_LOAD_RESULT.loaded_keys:
        return ENV_LOAD_RESULT.env_path.parent / path
    return path


DB_PATH = _configured_db_path()


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer number of milliseconds") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0 milliseconds")
    return value


def confidence_threshold_env(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a decimal number between 0 and 1") from exc
    if value <= 0 or value > 1:
        raise ValueError(f"{name} must be greater than 0 and less than or equal to 1")
    return value

# Quality thresholds (PRD 11.1)
QUALITY_SCORE_THRESHOLD = 0.85
PAGE_COVERAGE_THRESHOLD = 0.85
CITATION_ROUNDTRIP_THRESHOLD = 0.95
BROKEN_WORD_RATIO_MAX = 0.05
EMPTY_PAGE_RATIO_MAX = 0.10
DUPLICATE_CHUNK_RATIO_MAX = 0.05
PARAGRAPH_REF_DETECTION_MIN = 0.70
MIN_CHARS_TOTAL = 1000

LANGUAGE = "en"
ALLOWED_DOMAIN = "eba.europa.eu"
DOWNLOAD_RETRIES = 3
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_TIMEOUT_MS = _positive_int_env("OLLAMA_TIMEOUT_MS", 180_000)
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
LLM_REPAIR_MODEL = os.environ.get("LLM_REPAIR_MODEL", "qwen2.5:7b-instruct-q4_K_M")
LLM_REPAIR_CONFIDENCE_THRESHOLD = confidence_threshold_env("LLM_REPAIR_CONFIDENCE_THRESHOLD", 0.8)
