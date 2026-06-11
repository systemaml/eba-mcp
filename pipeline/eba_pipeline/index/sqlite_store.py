import sqlite3
import struct
from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

SqlRecord = Mapping[str, object]


class SqliteVecModule(Protocol):
    def load(self, conn: sqlite3.Connection) -> None: ...


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    _ = conn.execute("PRAGMA journal_mode=WAL")
    _ = conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    sql = schema_path.read_text()
    _ = conn.executescript(sql)
    conn.commit()


def ensure_index_write_schema(conn: sqlite3.Connection) -> None:
    """Add pipeline-write schema extensions needed by resume builds.

    Runtime readers must not mutate production databases. This helper is only
    called from the Python indexing pipeline, including --resume --embed paths
    that reuse a partially built write-mode database.
    """
    chunk_info_rows = cast(
        list[tuple[object, str, object, object, object, object]],
        conn.execute("PRAGMA table_info(chunks)").fetchall(),
    )
    chunk_columns = {row[1] for row in chunk_info_rows}
    additions = {
        "section_ref": "TEXT",
        "section_title": "TEXT",
        "section_level": "INTEGER",
        "parent_section_ref": "TEXT",
        "document_region": "TEXT",
        "metadata_confidence": "REAL",
        "metadata_source": "TEXT",
    }
    for column, column_type in additions.items():
        if column not in chunk_columns:
            _ = conn.execute(f"ALTER TABLE chunks ADD COLUMN {column} {column_type}")

    _ = conn.execute(
        """CREATE TABLE IF NOT EXISTS document_toc (
          document_version_id INTEGER NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,
          section_ref TEXT NOT NULL,
          title TEXT NOT NULL,
          level INTEGER NOT NULL,
          parent_section_ref TEXT,
          page_start INTEGER,
          page_end INTEGER,
          sequence_start INTEGER,
          sequence_end INTEGER,
          confidence REAL,
          source TEXT
        )"""
    )
    _ = conn.execute("CREATE INDEX IF NOT EXISTS idx_document_toc_document_version_id ON document_toc(document_version_id)")
    _ = conn.execute("CREATE INDEX IF NOT EXISTS idx_document_toc_section_ref ON document_toc(section_ref)")
    _ = conn.execute("CREATE INDEX IF NOT EXISTS idx_document_toc_document_section_ref ON document_toc(document_version_id, section_ref)")
    conn.commit()


def init_vec_schema(conn: sqlite3.Connection, dim: int) -> None:
    sqlite_vec = cast(SqliteVecModule, cast(object, import_module("sqlite_vec")))

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
    row = {
        "section_ref": None,
        "section_title": None,
        "section_level": None,
        "parent_section_ref": None,
        "document_region": None,
        "metadata_confidence": None,
        "metadata_source": None,
        **chunk,
    }
    _ = conn.execute(
        """INSERT INTO chunks
            (chunk_id, document_version_id, language, section_path, paragraph_ref,
              page_start, page_end, section_ref, section_title, section_level,
              parent_section_ref, document_region, metadata_confidence, metadata_source,
              text, text_hash, chunk_type, sequence_no)
             VALUES (:chunk_id, :document_version_id, :language, :section_path, :paragraph_ref,
                     :page_start, :page_end, :section_ref, :section_title, :section_level,
                     :parent_section_ref, :document_region, :metadata_confidence, :metadata_source,
                     :text, :text_hash, :chunk_type, :sequence_no)""",
        row,
    )


def insert_document_toc_entry(conn: sqlite3.Connection, toc_entry: SqlRecord) -> None:
    row = {
        "parent_section_ref": None,
        "page_start": None,
        "page_end": None,
        "sequence_start": None,
        "sequence_end": None,
        "confidence": None,
        "source": None,
        **toc_entry,
    }
    _ = conn.execute(
        """INSERT INTO document_toc
           (document_version_id, section_ref, title, level, parent_section_ref,
            page_start, page_end, sequence_start, sequence_end, confidence, source)
           VALUES (:document_version_id, :section_ref, :title, :level, :parent_section_ref,
                   :page_start, :page_end, :sequence_start, :sequence_end, :confidence, :source)""",
        row,
    )


def insert_document_toc_entries(conn: sqlite3.Connection, toc_entries: Sequence[SqlRecord]) -> None:
    for toc_entry in toc_entries:
        insert_document_toc_entry(conn, toc_entry)


def replace_document_toc_entries(
    conn: sqlite3.Connection,
    document_version_id: int,
    toc_entries: Sequence[SqlRecord],
) -> None:
    _ = conn.execute("DELETE FROM document_toc WHERE document_version_id = ?", (document_version_id,))
    insert_document_toc_entries(conn, toc_entries)


def insert_chunk_vectors(
    conn: sqlite3.Connection,
    rowid_vector_pairs: list[tuple[int, list[float]]],
) -> None:
    rows = [
        (rowid, struct.pack(f"{len(vector)}f", *vector))
        for rowid, vector in rowid_vector_pairs
    ]
    _ = conn.executemany(
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

    if (embedding_model is None) != (embedding_dim is None):
        raise ValueError("embedding_model and embedding_dim must be provided together")

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
    column_rows = cast(
        list[tuple[object, str, object, object, object, object]],
        conn.execute("PRAGMA table_info(corpus_manifest)").fetchall(),
    )
    existing = {row[1] for row in column_rows}
    if "embedding_model" not in existing:
        _ = conn.execute("ALTER TABLE corpus_manifest ADD COLUMN embedding_model TEXT")
    if "embedding_dim" not in existing:
        _ = conn.execute("ALTER TABLE corpus_manifest ADD COLUMN embedding_dim INTEGER")
    conn.commit()
