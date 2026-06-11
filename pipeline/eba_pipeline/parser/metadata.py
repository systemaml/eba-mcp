from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Mapping
from enum import StrEnum
from typing import cast

from eba_pipeline.ids import slugify_eba_id
from eba_pipeline.parser.paragraphize import (
    PageData,
    make_chunk_id,
    parent_section_ref,
    section_level,
)


class ChunkType(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE = "table"
    ANNEX = "annex"
    FOOTNOTE = "footnote"
    FRONT_MATTER = "front_matter"
    BACK_MATTER = "back_matter"
    CONSULTATION_RESPONSE = "consultation_response"


class DocumentRegion(StrEnum):
    FRONT_MATTER = "front_matter"
    BODY = "body"
    ANNEX = "annex"
    BACK_MATTER = "back_matter"
    CONSULTATION_FEEDBACK = "consultation_feedback"


CHUNK_TYPE_VALUES = tuple(item.value for item in ChunkType)
DOCUMENT_REGION_VALUES = tuple(item.value for item in DocumentRegion)


def make_parser_chunk(
    page: PageData,
    eba_id: str,
    *,
    sequence_no: int,
    chunk_type: ChunkType,
    document_region: DocumentRegion,
    language: str = "en",
    paragraph_ref: str | None = None,
    section_ref: str | None = None,
    section_title: str | None = None,
    metadata_confidence: float = 1.0,
    metadata_source: str = "synthetic-fixture",
) -> dict[str, object]:
    text = page["text"].strip()
    ref = paragraph_ref or section_ref or f"seq-{sequence_no}"
    chunk_id = make_chunk_id(
        slugify_eba_id(eba_id),
        language,
        chunk_type.value,
        ref,
        text,
        page["page_no"],
        sequence_no,
    )
    return {
        "chunk_id": chunk_id,
        "eba_id": eba_id,
        "language": language,
        "section_path": section_title or section_ref or "",
        "paragraph_ref": paragraph_ref,
        "page_start": page["page_no"],
        "page_end": page["page_no"],
        "section_ref": section_ref,
        "section_title": section_title,
        "section_level": section_level(section_ref),
        "parent_section_ref": parent_section_ref(section_ref),
        "document_region": document_region.value,
        "metadata_confidence": metadata_confidence,
        "metadata_source": metadata_source,
        "text": text,
        "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
        "chunk_type": chunk_type.value,
        "sequence_no": sequence_no,
    }


def build_toc_entries(
    chunks: list[Mapping[str, object]],
    document_version_id: int,
    *,
    source: str = "parser_metadata",
) -> list[dict[str, object]]:
    entries: OrderedDict[str, dict[str, object]] = OrderedDict()
    for chunk in sorted(chunks, key=lambda item: cast(int, item["sequence_no"])):
        section_ref = cast(str | None, chunk.get("section_ref"))
        if not section_ref:
            continue
        sequence_no = cast(int, chunk["sequence_no"])
        page_start = cast(int | None, chunk.get("page_start"))
        page_end = cast(int | None, chunk.get("page_end"))
        entry = entries.setdefault(
            section_ref,
            {
                "document_version_id": document_version_id,
                "section_ref": section_ref,
                "title": cast(str | None, chunk.get("section_title")) or section_ref,
                "level": section_level(section_ref) or 1,
                "parent_section_ref": parent_section_ref(section_ref),
                "page_start": page_start,
                "page_end": page_end,
                "sequence_start": sequence_no,
                "sequence_end": sequence_no,
                "confidence": chunk.get("metadata_confidence"),
                "source": source,
            },
        )
        entry["page_start"] = min(cast(int, entry["page_start"]), page_start or cast(int, entry["page_start"]))
        entry["page_end"] = max(cast(int, entry["page_end"]), page_end or cast(int, entry["page_end"]))
        entry["sequence_end"] = sequence_no
    return list(entries.values())
