import sqlite3
import struct
from pathlib import Path
from collections.abc import Mapping
from typing import cast


SqlRecord = Mapping[str, object]


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    _ = conn.execute("PRAGMA journal_mode=WAL")
    _ = conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    sql = schema_path.read_text()
    _ = conn.executescript(sql)
    conn.commit()


def init_vec_schema(conn: sqlite3.Connection, dim: int) -> None:
    import sqlite_vec

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    _ = conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])"
    )
    conn.commit()


def insert_document(conn: sqlite3.Connection, doc: SqlRecord) -> str:
    _ = conn.execute(
        """INSERT OR REPLACE INTO documents
           (eba_id, title, document_type, topic, language, publication_url, published_at,
            application_date, applicability_status, publication_status, is_canonical)
           VALUES (:eba_id, :title, :document_type, :topic, :language, :publication_url,
                   :published_at, :application_date, :applicability_status, :publication_status, :is_canonical)""",
        doc,
    )
    conn.commit()
    return cast(str, doc["eba_id"])


def insert_document_version(conn: sqlite3.Connection, version: SqlRecord) -> int:
    cursor = conn.execute(
        """INSERT OR REPLACE INTO document_versions
           (document_id, version_label, published_at, file_sha256, file_path, is_current)
           VALUES (:document_id, :version_label, :published_at, :file_sha256, :file_path, :is_current)""",
        version,
    )
    conn.commit()
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise ValueError("Failed to insert document version")
    return lastrowid


def insert_chunk(conn: sqlite3.Connection, chunk: SqlRecord) -> None:
    _ = conn.execute(
        """INSERT INTO chunks
           (chunk_id, document_version_id, language, section_path, paragraph_ref,
             page_start, page_end, text, text_hash, chunk_type, sequence_no)
            VALUES (:chunk_id, :document_version_id, :language, :section_path, :paragraph_ref,
                    :page_start, :page_end, :text, :text_hash, :chunk_type, :sequence_no)""",
        chunk,
    )


def insert_chunk_vectors(
    conn: sqlite3.Connection,
    rowid_vector_pairs: list[tuple[int, list[float]]],
) -> None:
    rows = [
        (rowid, struct.pack(f"{len(vector)}f", *vector))
        for rowid, vector in rowid_vector_pairs
    ]
    conn.executemany(
        "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
        rows,
    )
    conn.commit()


def update_corpus_manifest(
    conn: sqlite3.Connection,
    manifest_hash: str,
    doc_count: int,
    chunk_count: int,
    embedding_model: str | None = None,
    embedding_dim: int | None = None,
) -> None:
    from datetime import datetime, timezone

    if embedding_model is not None:
        _ensure_manifest_embedding_columns(conn)

    _ = conn.execute("DELETE FROM corpus_manifest")
    if embedding_model is not None and embedding_dim is not None:
        _ = conn.execute(
            "INSERT INTO corpus_manifest (manifest_hash, built_at, document_count, chunk_count, embedding_model, embedding_dim) VALUES (?, ?, ?, ?, ?, ?)",
            (manifest_hash, datetime.now(timezone.utc).isoformat(), doc_count, chunk_count, embedding_model, embedding_dim),
        )
    else:
        _ = conn.execute(
            "INSERT INTO corpus_manifest (manifest_hash, built_at, document_count, chunk_count) VALUES (?, ?, ?, ?)",
            (manifest_hash, datetime.now(timezone.utc).isoformat(), doc_count, chunk_count),
        )
    conn.commit()


def _ensure_manifest_embedding_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(corpus_manifest)").fetchall()
    }
    if "embedding_model" not in existing:
        conn.execute("ALTER TABLE corpus_manifest ADD COLUMN embedding_model TEXT")
    if "embedding_dim" not in existing:
        conn.execute("ALTER TABLE corpus_manifest ADD COLUMN embedding_dim INTEGER")
    conn.commit()
