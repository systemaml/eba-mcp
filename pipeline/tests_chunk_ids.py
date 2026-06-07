import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import cast

from eba_pipeline.index.build_index import build_index
from eba_pipeline.parser.paragraphize import ChunkData, PageData, paragraphize_document
from eba_pipeline.parser.quality import summarize_duplicate_chunk_ids, validate_unique_chunk_ids


class ChunkIdTests(unittest.TestCase):
    def test_repeated_identical_paragraph_text_on_different_pages_get_distinct_chunk_ids(self) -> None:
        pages = [
            {
                "page_no": 1,
                "text": "4. Quality of controls",
                "extraction_method": "test",
                "char_count": 22,
            },
            {
                "page_no": 2,
                "text": "4. Quality of controls",
                "extraction_method": "test",
                "char_count": 22,
            },
        ]

        chunks = paragraphize_document(cast(list[PageData], pages), "EBA/Op/2021/04")

        self.assertEqual(len(chunks), 2)
        self.assertEqual(len({chunk["chunk_id"] for chunk in chunks}), 2)
        self.assertEqual(chunks[0]["paragraph_ref"], "4")
        self.assertEqual(chunks[1]["paragraph_ref"], "4")
        self.assertTrue(chunks[0]["chunk_id"].endswith(":p1:s0"))
        self.assertTrue(chunks[1]["chunk_id"].endswith(":p2:s1"))

    def test_paragraph_heading_with_twelve_words_updates_section_path(self) -> None:
        pages = [
            {
                "page_no": 1,
                "text": "1.30. Firms should always consider the following sources of information:\nSome text under 1.30.",
                "extraction_method": "pdfplumber",
                "char_count": 90,
            },
            {
                "page_no": 2,
                "text": "4.40. SDD measures firms may apply include but are not limited to:\nA list item here.",
                "extraction_method": "pdfplumber",
                "char_count": 80,
            },
        ]

        chunks = paragraphize_document(cast(list[PageData], pages), "EBA/GL/2021/02")

        chunk_440 = next((chunk for chunk in chunks if chunk["paragraph_ref"] == "4.40"), None)

        self.assertIsNotNone(chunk_440)
        chunk_440 = cast(ChunkData, chunk_440)
        section_path = chunk_440["section_path"]

        self.assertEqual(
            section_path,
            "4.40. SDD measures firms may apply include but are not limited to:",
        )
        self.assertIn("4.40", section_path)
        self.assertNotIn("1.30", section_path)

    def test_validate_unique_chunk_ids_reports_actionable_samples(self) -> None:
        chunks = [
            {"chunk_id": "dup-1", "text": "A"},
            {"chunk_id": "dup-1", "text": "B"},
            {"chunk_id": "dup-2", "text": "C"},
            {"chunk_id": "dup-2", "text": "D"},
            {"chunk_id": "ok-1", "text": "E"},
        ]

        summary = summarize_duplicate_chunk_ids(chunks)

        self.assertEqual(summary["duplicate_chunk_id_count"], 2)
        self.assertEqual(summary["duplicate_chunk_row_count"], 2)
        self.assertEqual(summary["duplicate_chunk_id_samples"], ["dup-1", "dup-2"])

        with self.assertRaisesRegex(ValueError, "Duplicate chunk_id values detected") as exc:
            validate_unique_chunk_ids(chunks, "test-doc")

        message = str(exc.exception)
        self.assertIn("dup-1", message)
        self.assertIn("dup-2", message)
        self.assertIn("test-doc", message)

    def test_build_index_fails_before_insert_on_duplicate_chunk_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            processed_dir = tmp_path / "processed"
            reports_dir = tmp_path / "quality_reports"
            output_db = tmp_path / "eba.db"
            seed_path = tmp_path / "seed.yaml"

            doc_dir = processed_dir / "EBA-TEST-2026-01"
            doc_dir.mkdir(parents=True)
            _ = (doc_dir / "chunks.json").write_text(
                json.dumps(
                    [
                        {
                            "chunk_id": "dup-id",
                            "eba_id": "EBA/TEST/2026/01",
                            "language": "en",
                            "section_path": "Section",
                            "paragraph_ref": "1",
                            "page_start": 1,
                            "page_end": 1,
                            "text": "Alpha",
                            "text_hash": "a1",
                            "chunk_type": "paragraph",
                            "sequence_no": 0,
                        },
                        {
                            "chunk_id": "dup-id",
                            "eba_id": "EBA/TEST/2026/01",
                            "language": "en",
                            "section_path": "Section",
                            "paragraph_ref": "2",
                            "page_start": 2,
                            "page_end": 2,
                            "text": "Beta",
                            "text_hash": "b2",
                            "chunk_type": "paragraph",
                            "sequence_no": 1,
                        },
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )
            reports_dir.mkdir(parents=True)
            _ = (reports_dir / "EBA-TEST-2026-01.json").write_text(
                json.dumps({"passed": True}, indent=2), encoding="utf-8"
            )
            _ = seed_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "eba_id": "EBA/TEST/2026/01",
                                "title": "Test",
                                "document_type": "guidelines",
                                "topic": "AML/CFT",
                                "language": "en",
                                "publication_url": "",
                                "published_at": "2026-01-01",
                                "applicability_status": "applicable",
                                "publication_status": "final",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate chunk_id values detected"):
                build_index(output_db, processed_dir, reports_dir, seed_path)

            if output_db.exists():
                conn = sqlite3.connect(output_db)
                try:
                    count = cast(int, conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
                finally:
                    conn.close()
                self.assertEqual(count, 0)


if __name__ == "__main__":
    _ = unittest.main()
