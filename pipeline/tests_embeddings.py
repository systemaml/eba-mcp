import json
import sqlite3
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast
from unittest.mock import patch

from eba_pipeline.index.build_index import build_index
from eba_pipeline.index.embeddings import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    EmbeddingGenerationError,
    generate_embeddings,
)
from eba_pipeline.index.sqlite_store import update_corpus_manifest


def unit_vector(*values: float) -> list[float]:
    return list(values)


def embedding_chunk(chunk_id: str, text: str, sequence_no: int) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "eba_id": "EBA/GL/2099/99",
        "language": "en",
        "section_path": "",
        "paragraph_ref": str(sequence_no),
        "page_start": sequence_no,
        "page_end": sequence_no,
        "section_ref": None,
        "section_title": None,
        "section_level": None,
        "parent_section_ref": None,
        "document_region": "body",
        "metadata_confidence": 1.0,
        "metadata_source": "fixture",
        "text": text,
        "text_hash": f"hash-{chunk_id}",
        "chunk_type": "paragraph",
        "sequence_no": sequence_no,
    }


def write_embedding_fixture(root: Path, chunks: list[dict[str, object]]) -> tuple[Path, Path, Path]:
    processed_dir = root / "processed"
    quality_dir = root / "quality"
    doc_dir = processed_dir / "synthetic-embedding-doc"
    doc_dir.mkdir(parents=True)
    quality_dir.mkdir()
    _ = (doc_dir / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
    return processed_dir, quality_dir, root / "missing-seed.yaml"


def create_test_vec_table(conn: sqlite3.Connection, _dim: int) -> None:
    _ = conn.execute("CREATE TABLE IF NOT EXISTS chunks_vec (rowid INTEGER PRIMARY KEY, embedding BLOB NOT NULL)")
    conn.commit()


class FakeResponse:
    status_code: int
    _payload: Mapping[str, object] | None
    text: str

    def __init__(self, *, status_code: int = 200, payload: Mapping[str, object] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Mapping[str, object]:
        if self._payload is None:
            raise ValueError("invalid json")
        return self._payload


class EmbeddingGenerationTests(unittest.TestCase):
    def test_generate_embeddings_batches_input_and_preserves_order(self) -> None:
        chunks = [
            {"text": "alpha"},
            {"text": "beta"},
            {"text": "gamma"},
        ]
        calls: list[dict[str, object]] = []

        def fake_post(url: str, *, json: Mapping[str, object], timeout: int) -> FakeResponse:
            calls.append({"url": url, "json": json, "timeout": timeout})
            inputs = json["input"]
            if inputs == ["alpha", "beta"]:
                return FakeResponse(payload={"embeddings": [unit_vector(1.0, 0.0), unit_vector(0.0, 1.0)]})
            return FakeResponse(payload={"embeddings": [unit_vector(0.70710678, 0.70710678)]})

        with patch("eba_pipeline.index.embeddings.requests.post", side_effect=fake_post):
            vectors = generate_embeddings(chunks, model="custom-embed", ollama_url="http://ollama:11434/", batch_size=2)

        self.assertEqual(
            vectors,
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.70710678, 0.70710678],
            ],
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], "http://ollama:11434/api/embed")
        self.assertEqual(calls[0]["json"], {"model": "custom-embed", "input": ["alpha", "beta"]})
        self.assertEqual(calls[1]["json"], {"model": "custom-embed", "input": ["gamma"]})

    def test_generate_embeddings_reports_progress_every_100_and_at_completion(self) -> None:
        chunks = [{"text": f"chunk-{index}"} for index in range(205)]

        def fake_post(_url: str, *, json: Mapping[str, object], timeout: int) -> FakeResponse:
            inputs = json["input"]
            input_count = len(cast(list[object], inputs)) if isinstance(inputs, list) else 0
            self.assertEqual(timeout, DEFAULT_TIMEOUT_SECONDS)
            return FakeResponse(payload={"embeddings": [unit_vector(1.0, 0.0) for _ in range(input_count)]})

        with patch("eba_pipeline.index.embeddings.requests.post", side_effect=fake_post):
            with patch("builtins.print") as print_mock:
                vectors = generate_embeddings(
                    chunks,
                    model="custom-embed",
                    ollama_url="http://localhost:11434",
                    batch_size=80,
                )

        self.assertEqual(len(vectors), 205)
        self.assertEqual(
            [call.args[0] for call in print_mock.call_args_list],
            [
                "  Embeddings: 100/205 chunks",
                "  Embeddings: 200/205 chunks",
                "  Embeddings: 205/205 chunks",
            ],
        )

    def test_generate_embeddings_enforces_default_nomic_dimension(self) -> None:
        with patch(
            "eba_pipeline.index.embeddings.requests.post",
            return_value=FakeResponse(payload={"embeddings": [[1.0, 0.0]]}),
        ):
            with self.assertRaisesRegex(EmbeddingGenerationError, "expected 768"):
                _ = generate_embeddings(
                    [{"text": "alpha"}],
                    model="nomic-embed-text",
                    ollama_url="http://localhost:11434",
                    batch_size=1,
                )

    def test_generate_embeddings_retries_and_succeeds_on_third_attempt(self) -> None:
        responses: Final[list[FakeResponse]] = [
            FakeResponse(status_code=500, text="temporary failure"),
            FakeResponse(status_code=502, text="still failing"),
            FakeResponse(payload={"embeddings": [unit_vector(1.0, 0.0)]}),
        ]

        with patch("eba_pipeline.index.embeddings.requests.post", side_effect=responses) as post_mock:
            with patch("eba_pipeline.index.embeddings.time.sleep") as sleep_mock:
                vectors = generate_embeddings([{"text": "alpha"}], model="custom-embed", ollama_url="http://localhost:11434", batch_size=1)

        self.assertEqual(vectors, [[1.0, 0.0]])
        self.assertEqual(post_mock.call_count, 3)
        sleep_mock.assert_any_call(RETRY_BACKOFF_SECONDS[0])
        sleep_mock.assert_any_call(RETRY_BACKOFF_SECONDS[1])

    def test_generate_embeddings_raises_clear_error_after_retries_exhausted(self) -> None:
        with patch(
            "eba_pipeline.index.embeddings.requests.post",
            return_value=FakeResponse(status_code=503, text="service unavailable"),
        ):
            with patch("eba_pipeline.index.embeddings.time.sleep"):
                with self.assertRaisesRegex(EmbeddingGenerationError, f"failed after {DEFAULT_RETRIES} attempts"):
                    _ = generate_embeddings(
                        [{"text": "alpha"}],
                        model="custom-embed",
                        ollama_url="http://localhost:11434",
                        batch_size=1,
                    )

    def test_generate_embeddings_rejects_non_normalized_vectors(self) -> None:
        with patch(
            "eba_pipeline.index.embeddings.requests.post",
            return_value=FakeResponse(payload={"embeddings": [[2.0, 0.0]]}),
        ):
            with patch("eba_pipeline.index.embeddings.time.sleep"):
                with self.assertRaisesRegex(EmbeddingGenerationError, "L2 norm"):
                    _ = generate_embeddings(
                        [{"text": "alpha"}],
                        model="custom-embed",
                        ollama_url="http://localhost:11434",
                        batch_size=1,
                    )

    def test_generate_embeddings_rejects_missing_chunk_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required string field 'text'"):
            _ = generate_embeddings(
                [{"chunk_id": "no-text"}],
                model="custom-embed",
                ollama_url="http://localhost:11434",
                batch_size=1,
            )

    def test_generate_embeddings_rejects_response_count_mismatch(self) -> None:
        with patch(
            "eba_pipeline.index.embeddings.requests.post",
            return_value=FakeResponse(payload={"embeddings": [unit_vector(1.0, 0.0), unit_vector(0.0, 1.0)]}),
        ):
            with patch("eba_pipeline.index.embeddings.time.sleep"):
                with self.assertRaisesRegex(EmbeddingGenerationError, "count mismatch"):
                    _ = generate_embeddings(
                        [{"text": "alpha"}],
                        model="custom-embed",
                        ollama_url="http://localhost:11434",
                        batch_size=1,
                    )

    def test_populate_vectors_persists_each_batch_for_resume_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processed_dir, quality_dir, seed_path = write_embedding_fixture(
                root,
                [embedding_chunk("chunk-1", "alpha", 1), embedding_chunk("chunk-2", "beta", 2)],
            )
            output_db = root / "eba-corpus.db"

            with patch("eba_pipeline.index.build_index.init_vec_schema", side_effect=create_test_vec_table), patch(
                "eba_pipeline.index.embeddings.generate_embeddings",
                side_effect=[[[1.0, 0.0]], EmbeddingGenerationError("boom")],
            ):
                with self.assertRaisesRegex(EmbeddingGenerationError, "boom"):
                    build_index(
                        output_db,
                        processed_dir,
                        quality_dir,
                        seed_yaml_path=seed_path,
                        embed=True,
                        model="custom-embed",
                        ollama_url="http://localhost:11434",
                        batch_size=1,
                    )

            conn = sqlite3.connect(output_db)
            try:
                saved_rowids = [
                    row[0]
                    for row in cast(list[tuple[int]], conn.execute("SELECT rowid FROM chunks_vec ORDER BY rowid").fetchall())
                ]
            finally:
                conn.close()
        self.assertEqual(saved_rowids, [1])

    def test_populate_vectors_resume_skips_existing_vector_rows(self) -> None:
        calls: list[list[Mapping[str, object]]] = []

        def fake_generate(chunks: list[Mapping[str, object]], **_kwargs: object) -> list[list[float]]:
            calls.append(chunks)
            return [[1.0, 0.0] for _ in chunks]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processed_dir, quality_dir, seed_path = write_embedding_fixture(
                root,
                [
                    embedding_chunk("chunk-1", "alpha", 1),
                    embedding_chunk("chunk-2", "beta", 2),
                    embedding_chunk("chunk-3", "gamma", 3),
                ],
            )
            output_db = root / "eba-corpus.db"
            build_index(output_db, processed_dir, quality_dir, seed_yaml_path=seed_path)

            conn = sqlite3.connect(output_db)
            try:
                create_test_vec_table(conn, 2)
                _ = conn.execute("INSERT INTO chunks_vec (rowid, embedding) VALUES (?, ?)", (1, b"already-done"))
                conn.commit()
            finally:
                conn.close()

            with patch("eba_pipeline.index.build_index.init_vec_schema", side_effect=create_test_vec_table), patch(
                "eba_pipeline.index.embeddings.generate_embeddings",
                side_effect=fake_generate,
            ):
                build_index(
                    output_db,
                    processed_dir,
                    quality_dir,
                    seed_yaml_path=seed_path,
                    embed=True,
                    model="custom-embed",
                    ollama_url="http://localhost:11434",
                    batch_size=2,
                    resume=True,
                )

            conn = sqlite3.connect(output_db)
            try:
                saved_rowids = [
                    row[0]
                    for row in cast(list[tuple[int]], conn.execute("SELECT rowid FROM chunks_vec ORDER BY rowid").fetchall())
                ]
            finally:
                conn.close()

        self.assertEqual([[chunk["chunk_id"] for chunk in batch] for batch in calls], [["chunk-2", "chunk-3"]])
        self.assertEqual(saved_rowids, [1, 2, 3])

    def test_manifest_embedding_metadata_columns_added_for_embedded_corpus(self) -> None:
        conn = sqlite3.connect(":memory:")
        _ = conn.execute(
            """CREATE TABLE corpus_manifest (
                manifest_hash TEXT PRIMARY KEY,
                built_at TEXT NOT NULL,
                document_count INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL
            )"""
        )

        update_corpus_manifest(
            conn,
            manifest_hash="abc123",
            doc_count=2,
            chunk_count=3,
            embedding_model="custom-embed",
            embedding_dim=384,
        )

        columns = {row[1] for row in cast(list[tuple[object, str]], conn.execute("PRAGMA table_info(corpus_manifest)").fetchall())}
        self.assertIn("embedding_model", columns)
        self.assertIn("embedding_dim", columns)
        row = cast(tuple[str, int, int, str, int] | None, conn.execute(
            "SELECT manifest_hash, document_count, chunk_count, embedding_model, embedding_dim FROM corpus_manifest"
        ).fetchone())
        self.assertEqual(row, ("abc123", 2, 3, "custom-embed", 384))

    def test_manifest_embedding_metadata_requires_model_and_dim_together(self) -> None:
        conn = sqlite3.connect(":memory:")
        _ = conn.execute(
            """CREATE TABLE corpus_manifest (
                manifest_hash TEXT PRIMARY KEY,
                built_at TEXT NOT NULL,
                document_count INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL
            )"""
        )

        with self.assertRaisesRegex(ValueError, "embedding_model and embedding_dim"):
            update_corpus_manifest(
                conn,
                manifest_hash="abc123",
                doc_count=2,
                chunk_count=3,
                embedding_model="custom-embed",
            )

    def test_manifest_embedding_metadata_absent_for_fts_only_corpus(self) -> None:
        conn = sqlite3.connect(":memory:")
        _ = conn.execute(
            """CREATE TABLE corpus_manifest (
                manifest_hash TEXT PRIMARY KEY,
                built_at TEXT NOT NULL,
                document_count INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL
            )"""
        )

        update_corpus_manifest(
            conn,
            manifest_hash="fts123",
            doc_count=2,
            chunk_count=3,
        )

        columns = {row[1] for row in cast(list[tuple[object, str]], conn.execute("PRAGMA table_info(corpus_manifest)").fetchall())}
        self.assertNotIn("embedding_model", columns)
        self.assertNotIn("embedding_dim", columns)
        row = cast(tuple[str, int, int] | None, conn.execute(
            "SELECT manifest_hash, document_count, chunk_count FROM corpus_manifest"
        ).fetchone())
        self.assertEqual(row, ("fts123", 2, 3))


if __name__ == "__main__":
    _ = unittest.main()
