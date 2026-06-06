import math
import time
from collections.abc import Mapping, Sequence
from typing import cast

import requests

DEFAULT_BATCH_SIZE = 32
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_RETRIES = 5
DEFAULT_TIMEOUT_SECONDS = 180
NOMIC_EMBED_TEXT_DIM = 768
NORM_TOLERANCE = 1e-3
RETRY_BACKOFF_SECONDS = (1.0, 4.0, 16.0, 60.0)

ChunkRecord = Mapping[str, object]
EmbeddingVector = list[float]


class EmbeddingGenerationError(RuntimeError):
    """Raised when Ollama embedding generation fails."""


def generate_embeddings(
    chunks: Sequence[ChunkRecord],
    model: str,
    ollama_url: str,
    batch_size: int,
) -> list[EmbeddingVector]:
    if batch_size <= 0:
        raise ValueError(f"batch_size must be > 0, got {batch_size}")

    texts = [_get_chunk_text(chunk, index) for index, chunk in enumerate(chunks)]
    if not texts:
        return []

    endpoint = f"{ollama_url.rstrip('/')}/api/embed"
    expected_dim = _expected_embedding_dim(model)
    embeddings: list[EmbeddingVector] = []
    total_chunks = len(texts)
    next_progress_report = 100

    for start in range(0, len(texts), batch_size):
        batch_texts = texts[start : start + batch_size]
        batch_embeddings = _embed_with_retries(
            batch_texts=batch_texts,
            model=model,
            endpoint=endpoint,
            expected_dim=expected_dim,
        )
        embeddings.extend(batch_embeddings)
        next_progress_report = _report_embedding_progress(
            processed_count=len(embeddings),
            total_count=total_chunks,
            next_progress_report=next_progress_report,
        )

    if len(embeddings) != len(texts):
        raise EmbeddingGenerationError(
            f"Expected {len(texts)} embeddings for {len(texts)} chunks, got {len(embeddings)}"
        )

    return embeddings


def _report_embedding_progress(
    *,
    processed_count: int,
    total_count: int,
    next_progress_report: int,
) -> int:
    while next_progress_report <= processed_count and next_progress_report < total_count:
        print(f"  Embeddings: {next_progress_report}/{total_count} chunks")
        next_progress_report += 100

    if processed_count == total_count and (total_count < 100 or total_count % 100 != 0):
        print(f"  Embeddings: {total_count}/{total_count} chunks")

    return next_progress_report


def _embed_with_retries(
    *,
    batch_texts: Sequence[str],
    model: str,
    endpoint: str,
    expected_dim: int | None,
) -> list[EmbeddingVector]:
    last_error: Exception | None = None
    current_batch = list(batch_texts)

    for attempt in range(1, DEFAULT_RETRIES + 1):
        try:
            return _request_embeddings(
                batch_texts=current_batch,
                model=model,
                endpoint=endpoint,
                expected_dim=expected_dim,
            )
        except requests.Timeout as error:
            last_error = error
            if len(current_batch) > 1:
                halved = max(1, len(current_batch) // 2)
                print(f"  [retry {attempt}/{DEFAULT_RETRIES}] Timeout — halving batch {len(current_batch)} → {halved}")
                current_batch = current_batch[:halved]
            else:
                print(f"  [retry {attempt}/{DEFAULT_RETRIES}] Timeout on single-item batch — waiting before retry")
        except (requests.RequestException, ValueError, EmbeddingGenerationError) as error:
            last_error = error
            if attempt == DEFAULT_RETRIES:
                break
            print(f"  [retry {attempt}/{DEFAULT_RETRIES}] {type(error).__name__}: {error}")
        backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
        time.sleep(backoff)

    raise EmbeddingGenerationError(
        f"Ollama embedding request failed after {DEFAULT_RETRIES} attempts for batch size {len(batch_texts)}: {last_error}"
    ) from last_error


def _request_embeddings(
    *,
    batch_texts: Sequence[str],
    model: str,
    endpoint: str,
    expected_dim: int | None,
) -> list[EmbeddingVector]:
    response = requests.post(
        endpoint,
        json={"model": model, "input": list(batch_texts)},
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )

    if response.status_code >= 400:
        detail = _response_detail(response)
        raise EmbeddingGenerationError(
            f"Ollama embed request returned HTTP {response.status_code}: {detail}"
        )

    try:
        payload_obj = cast(object, response.json())
    except ValueError as error:
        raise EmbeddingGenerationError("Ollama embed response was not valid JSON") from error

    if not isinstance(payload_obj, Mapping):
        raise EmbeddingGenerationError("Ollama embed response must be a JSON object")

    payload_map = cast(Mapping[object, object], payload_obj)
    payload: dict[str, object] = {}
    for key, value in payload_map.items():
        payload[str(key)] = value

    if "error" in payload:
        raise EmbeddingGenerationError(f"Ollama embed response error: {payload['error']}")

    raw_embeddings = payload.get("embeddings")
    if not isinstance(raw_embeddings, list):
        raise EmbeddingGenerationError("Ollama embed response missing 'embeddings' list")
    raw_embeddings_list = cast(list[object], raw_embeddings)
    if len(raw_embeddings_list) != len(batch_texts):
        raise EmbeddingGenerationError(
            f"Ollama embed response count mismatch: expected {len(batch_texts)}, got {len(raw_embeddings_list)}"
        )

    resolved_dim = expected_dim
    embeddings: list[EmbeddingVector] = []
    for index, raw_vector in enumerate(raw_embeddings_list):
        vector = _coerce_embedding_vector(raw_vector, index)
        if resolved_dim is None:
            resolved_dim = len(vector)
        _validate_embedding_vector(vector, index=index, expected_dim=resolved_dim)
        embeddings.append(vector)

    return embeddings


def _get_chunk_text(chunk: ChunkRecord, index: int) -> str:
    text = chunk.get("text")
    if not isinstance(text, str):
        raise ValueError(f"Chunk at index {index} is missing required string field 'text'")
    if not text.strip():
        raise ValueError(f"Chunk at index {index} has empty 'text'")
    return text


def _coerce_embedding_vector(raw_vector: object, index: int) -> EmbeddingVector:
    if not isinstance(raw_vector, Sequence) or isinstance(raw_vector, (str, bytes, bytearray)):
        raise EmbeddingGenerationError(f"Embedding at index {index} must be a list of numbers")

    vector: EmbeddingVector = []
    for value in raw_vector:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise EmbeddingGenerationError(
                f"Embedding at index {index} contains non-numeric value {value!r}"
            )
        vector.append(float(value))
    return vector


def _validate_embedding_vector(vector: EmbeddingVector, *, index: int, expected_dim: int) -> None:
    if len(vector) != expected_dim:
        raise EmbeddingGenerationError(
            f"Embedding at index {index} has dimension {len(vector)}; expected {expected_dim}"
        )

    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isclose(norm, 1.0, rel_tol=NORM_TOLERANCE, abs_tol=NORM_TOLERANCE):
        raise EmbeddingGenerationError(
            f"Embedding at index {index} has L2 norm {norm:.6f}; expected approximately 1.0"
        )


def _expected_embedding_dim(model: str) -> int | None:
    normalized = model.strip().lower()
    base_model = normalized.split(":", 1)[0]
    if base_model == "nomic-embed-text":
        return NOMIC_EMBED_TEXT_DIM
    return None


def _response_detail(response: requests.Response) -> str:
    body = response.text.strip()
    return body[:300] if body else "empty response body"


__all__ = ["EmbeddingGenerationError", "generate_embeddings", "DEFAULT_BATCH_SIZE", "DEFAULT_OLLAMA_URL"]
