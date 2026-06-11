import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from eba_pipeline.index.build_index import build_index
from eba_pipeline.parser.metadata import ChunkType, DocumentRegion, make_parser_chunk
from eba_pipeline.parser.paragraphize import PageData


def page(page_no: int, text: str) -> PageData:
    return cast(
        PageData,
        cast(
            object,
            {
                "page_no": page_no,
                "text": text,
                "extraction_method": "synthetic",
                "char_count": len(text),
            },
        ),
    )


def write_processed_fixture(root: Path, chunks: list[dict[str, object]]) -> tuple[Path, Path, Path]:
    processed_dir = root / "processed"
    quality_dir = root / "quality"
    doc_dir = processed_dir / "synthetic-toc-doc"
    doc_dir.mkdir(parents=True)
    quality_dir.mkdir()
    _ = (doc_dir / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
    seed_path = root / "missing-seed.yaml"
    return processed_dir, quality_dir, seed_path


class IndexTocTests(unittest.TestCase):
    def test_build_index_persists_enriched_chunks_and_document_toc(self) -> None:
        chunks = [
            make_parser_chunk(
                page(1, "Contents\n1. Scope"),
                "EBA/GL/2099/99",
                sequence_no=0,
                chunk_type=ChunkType.FRONT_MATTER,
                document_region=DocumentRegion.FRONT_MATTER,
                section_ref="0",
                section_title="Contents",
            ),
            make_parser_chunk(
                page(2, "1. Scope"),
                "EBA/GL/2099/99",
                sequence_no=1,
                chunk_type=ChunkType.HEADING,
                document_region=DocumentRegion.BODY,
                section_ref="1",
                section_title="1. Scope",
                metadata_confidence=0.97,
                metadata_source="deterministic",
            ),
            make_parser_chunk(
                page(3, "1.1 Institutions shall identify risks."),
                "EBA/GL/2099/99",
                sequence_no=2,
                chunk_type=ChunkType.PARAGRAPH,
                document_region=DocumentRegion.BODY,
                paragraph_ref="1.1",
                section_ref="1",
                section_title="1. Scope",
                metadata_confidence=0.91,
                metadata_source="deterministic",
            ),
            make_parser_chunk(
                page(10, "Annex I\nTemplate"),
                "EBA/GL/2099/99",
                sequence_no=3,
                chunk_type=ChunkType.ANNEX,
                document_region=DocumentRegion.ANNEX,
                section_ref="A1",
                section_title="Annex I",
                metadata_confidence=0.9,
                metadata_source="deterministic",
            ),
            make_parser_chunk(
                page(20, "Feedback on the public consultation"),
                "EBA/GL/2099/99",
                sequence_no=4,
                chunk_type=ChunkType.CONSULTATION_RESPONSE,
                document_region=DocumentRegion.CONSULTATION_FEEDBACK,
                section_ref="CF",
                section_title="Consultation feedback",
            ),
            make_parser_chunk(
                page(21, "Explicit consultation feedback"),
                "EBA/GL/2099/99",
                sequence_no=5,
                chunk_type=ChunkType.PARAGRAPH,
                document_region=DocumentRegion.BODY,
                section_ref="CF2",
                section_title="Consultation feedback marker",
            ),
        ]
        chunks[-1]["document_region"] = "consultation_feedback"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processed_dir, quality_dir, seed_path = write_processed_fixture(root, chunks)
            output_db = root / "eba-corpus.db"

            build_index(output_db, processed_dir, quality_dir, seed_yaml_path=seed_path)

            conn = sqlite3.connect(output_db)
            try:
                enriched = cast(tuple[str, str, int, None, str, float, str] | None, conn.execute(
                    """SELECT section_ref, section_title, section_level, parent_section_ref,
                              document_region, metadata_confidence, metadata_source
                       FROM chunks
                       WHERE section_ref = '1' AND document_region = 'body'"""
                ).fetchone())
                self.assertEqual(enriched, ("1", "1. Scope", 1, None, "body", 0.97, "deterministic"))

                toc_rows = cast(list[tuple[object, ...]], conn.execute(
                    """SELECT section_ref, title, level, parent_section_ref, page_start,
                              page_end, sequence_start, sequence_end, confidence, source
                       FROM document_toc
                       ORDER BY sequence_start"""
                ).fetchall())
            finally:
                conn.close()

        self.assertEqual(
            toc_rows,
            [
                ("1", "1. Scope", 1, None, 2, 3, 2, 3, 0.97, "deterministic"),
                ("A1", "Annex I", 1, None, 10, 10, 4, 4, 0.9, "deterministic"),
            ],
        )

    def test_resume_embed_only_populates_vectors_without_repairing_toc(self) -> None:
        chunks = [
            make_parser_chunk(
                page(2, "2. Governance"),
                "EBA/GL/2099/99",
                sequence_no=1,
                chunk_type=ChunkType.HEADING,
                document_region=DocumentRegion.BODY,
                section_ref="2",
                section_title="2. Governance",
            ),
            make_parser_chunk(
                page(3, "2.1 Management body responsibilities."),
                "EBA/GL/2099/99",
                sequence_no=2,
                chunk_type=ChunkType.PARAGRAPH,
                document_region=DocumentRegion.BODY,
                paragraph_ref="2.1",
                section_ref="2",
                section_title="2. Governance",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processed_dir, quality_dir, seed_path = write_processed_fixture(root, chunks)
            output_db = root / "eba-corpus.db"
            build_index(output_db, processed_dir, quality_dir, seed_yaml_path=seed_path)
            conn = sqlite3.connect(output_db)
            try:
                _ = conn.execute(
                    """INSERT INTO document_toc (
                         document_version_id, section_ref, title, level, parent_section_ref,
                         page_start, page_end, sequence_start, sequence_end, confidence, source
                       ) VALUES (1, 'stale', 'Stale row', 1, NULL, 99, 99, 99, 99, 0.1, 'fixture')"""
                )
                conn.commit()
            finally:
                conn.close()

            with patch("eba_pipeline.index.build_index._populate_vectors"):
                build_index(output_db, processed_dir, quality_dir, seed_yaml_path=seed_path, embed=True, resume=True)
                build_index(output_db, processed_dir, quality_dir, seed_yaml_path=seed_path, embed=True, resume=True)

            conn = sqlite3.connect(output_db)
            try:
                toc_count_row = cast(tuple[int], conn.execute("SELECT count(*) FROM document_toc").fetchone())
                toc_count = toc_count_row[0]
                toc_rows = cast(list[tuple[str, int, int, int, int]], conn.execute(
                    "SELECT section_ref, page_start, page_end, sequence_start, sequence_end FROM document_toc ORDER BY section_ref"
                ).fetchall())
            finally:
                conn.close()

        self.assertEqual(toc_count, 2)
        self.assertEqual(toc_rows, [("2", 2, 3, 1, 2), ("stale", 99, 99, 99, 99)])


if __name__ == "__main__":
    _ = unittest.main()
