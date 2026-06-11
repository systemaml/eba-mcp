import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import cast

from click.testing import CliRunner

from eba_pipeline.cli import cli
from eba_pipeline.eval.metadata import run_metadata_eval
from eba_pipeline.eval.navigation import run_navigation_eval
from eba_pipeline.eval.toc import run_toc_eval

EvalResult = dict[str, object]


def create_eval_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        _ = conn.executescript(
            """
            CREATE TABLE documents (
              eba_id TEXT PRIMARY KEY,
              title TEXT,
              document_type TEXT,
              topic TEXT,
              language TEXT,
              publication_url TEXT,
              published_at TEXT,
              application_date TEXT,
              applicability_status TEXT,
              publication_status TEXT,
              is_canonical INTEGER
            );
            CREATE TABLE document_versions (
              version_id INTEGER PRIMARY KEY,
              document_id TEXT,
              version_label TEXT,
              published_at TEXT,
              file_sha256 TEXT,
              file_path TEXT,
              is_current INTEGER
            );
            CREATE TABLE chunks (
              chunk_id TEXT PRIMARY KEY,
              document_version_id INTEGER NOT NULL,
              language TEXT NOT NULL,
              section_path TEXT,
              paragraph_ref TEXT,
              page_start INTEGER,
              page_end INTEGER,
              section_ref TEXT,
              section_title TEXT,
              section_level INTEGER,
              parent_section_ref TEXT,
              document_region TEXT,
              metadata_confidence REAL,
              metadata_source TEXT,
              text TEXT,
              text_hash TEXT,
              chunk_type TEXT,
              sequence_no INTEGER
            );
            CREATE TABLE document_toc (
              document_version_id INTEGER NOT NULL,
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
            );
            INSERT INTO documents VALUES (
              'EBA/GL/2099/99', 'Synthetic eval doc', 'Guidelines', 'AML/CFT', 'en', '',
              '2099-01-01', NULL, 'applicable', 'current', 1
            );
            INSERT INTO document_versions VALUES (1, 'EBA/GL/2099/99', 'current', '2099-01-01', 'sha', 'file.pdf', 1);
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_chunk(
    db_path: Path,
    *,
    chunk_id: str,
    sequence_no: int,
    section_ref: str | None,
    section_level: int | None,
    parent_section_ref: str | None,
    chunk_type: str = "paragraph",
    document_region: str = "body",
    section_title: str | None = None,
    page_start: int = 1,
    page_end: int | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        _ = conn.execute(
            """INSERT INTO chunks (
                 chunk_id, document_version_id, language, section_path, paragraph_ref,
                 page_start, page_end, section_ref, section_title, section_level,
                 parent_section_ref, document_region, metadata_confidence, metadata_source,
                 text, text_hash, chunk_type, sequence_no
               ) VALUES (?, 1, 'en', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 'fixture', ?, ?, ?, ?)""",
            (
                chunk_id,
                section_title or section_ref or "",
                section_ref,
                page_start,
                page_end or page_start,
                section_ref,
                section_title or section_ref,
                section_level,
                parent_section_ref,
                document_region,
                f"{section_title or section_ref or chunk_id} text",
                f"hash-{chunk_id}",
                chunk_type,
                sequence_no,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def insert_toc(
    db_path: Path,
    *,
    section_ref: str,
    title: str,
    level: int,
    parent_section_ref: str | None = None,
    page_start: int = 1,
    page_end: int | None = None,
    sequence_start: int = 1,
    sequence_end: int | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        _ = conn.execute(
            """INSERT INTO document_toc (
                 document_version_id, section_ref, title, level, parent_section_ref,
                 page_start, page_end, sequence_start, sequence_end, confidence, source
               ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 'fixture')""",
            (section_ref, title, level, parent_section_ref, page_start, page_end or page_start, sequence_start, sequence_end or sequence_start),
        )
        conn.commit()
    finally:
        conn.close()


class EvalMetadataTocTests(unittest.TestCase):
    def test_metadata_eval_rejects_invalid_enum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "eval.db"
            create_eval_db(db_path)
            insert_chunk(
                db_path,
                chunk_id="bad-enum",
                sequence_no=1,
                section_ref="1",
                section_level=1,
                parent_section_ref=None,
                chunk_type="made_up",
                document_region="body",
            )

            result = run_metadata_eval(str(db_path))

        self.assertEqual(result["failed_count"], 1)
        failures = cast(list[dict[str, object]], result["failures"])
        self.assertEqual(failures[0]["check"], "invalid_chunk_type")

    def test_metadata_eval_rejects_orphan_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "eval.db"
            create_eval_db(db_path)
            insert_chunk(
                db_path,
                chunk_id="orphan-child",
                sequence_no=1,
                section_ref="4.1",
                section_level=2,
                parent_section_ref="4",
            )

            result = run_metadata_eval(str(db_path))

        self.assertEqual(result["failed_count"], 1)
        failures = cast(list[dict[str, object]], result["failures"])
        self.assertEqual(failures[0]["check"], "missing_parent_section")

    def test_metadata_eval_rejects_empty_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "eval.db"
            create_eval_db(db_path)

            result = run_metadata_eval(str(db_path))

        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["pass_rate"], 0.0)
        failures = cast(list[dict[str, object]], result["failures"])
        self.assertEqual(failures[0]["check"], "no_chunks")

    def test_toc_eval_rejects_boilerplate_dominant_toc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "eval.db"
            create_eval_db(db_path)
            insert_chunk(
                db_path,
                chunk_id="body-1",
                sequence_no=1,
                section_ref="1",
                section_level=1,
                parent_section_ref=None,
                section_title="1. Scope",
            )
            insert_toc(db_path, section_ref="1", title="Guidelines", level=1)

            result = run_toc_eval(str(db_path))

        summary = cast(dict[str, object], result["summary"])
        failures = cast(list[dict[str, object]], result["failures"])
        self.assertEqual(summary["boilerplate_rows"], 1)
        self.assertGreater(cast(int, result["failed_count"]), 0)
        self.assertEqual(failures[0]["check"], "boilerplate_toc_title")

    def test_toc_eval_rejects_empty_toc_and_empty_section_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "eval.db"
            create_eval_db(db_path)
            insert_chunk(
                db_path,
                chunk_id="body-without-section",
                sequence_no=1,
                section_ref=None,
                section_level=None,
                parent_section_ref=None,
            )

            result = run_toc_eval(str(db_path))

        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["pass_rate"], 0.0)
        failures = cast(list[dict[str, object]], result["failures"])
        self.assertEqual(failures[0]["check"], "no_toc_sections")

    def test_navigation_eval_rejects_empty_document_toc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "eval.db"
            create_eval_db(db_path)

            result = run_navigation_eval(str(db_path))

        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["pass_rate"], 0.0)
        failures = cast(list[dict[str, object]], result["failures"])
        self.assertEqual(failures[0]["check"], "no_toc_rows")

    def test_eval_cli_exposes_new_mode_choices(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["eval", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("[queries|citation-roundtrip|metadata|toc|navigation]", result.output)


if __name__ == "__main__":
    _ = unittest.main()
