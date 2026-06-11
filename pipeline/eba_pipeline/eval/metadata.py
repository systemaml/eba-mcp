from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import cast

from eba_pipeline.parser.metadata import CHUNK_TYPE_VALUES, DOCUMENT_REGION_VALUES
from eba_pipeline.parser.paragraphize import parent_section_ref, section_level

Failure = dict[str, object]


def _row_value(row: sqlite3.Row, key: str) -> object:
    return cast(object, row[key])


def _row_index(row: sqlite3.Row, index: int) -> object:
    return cast(object, row[index])


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = cast(list[sqlite3.Row], conn.execute(f"PRAGMA table_info({table_name})").fetchall())
    return {str(_row_index(row, 1)) for row in rows}


def _append_failure(failures: list[Failure], check: str, row: sqlite3.Row | None = None, **details: object) -> None:
    failure: Failure = {"check": check, **details}
    if row is not None:
        for key in ("chunk_id", "document_version_id", "section_ref", "parent_section_ref"):
            if key in row.keys():
                failure[key] = _row_value(row, key)
    failures.append(failure)


def _detect_cycle(section_ref: str, parents: dict[str, str | None]) -> bool:
    seen: set[str] = set()
    current: str | None = section_ref
    while current:
        if current in seen:
            return True
        seen.add(current)
        current = parents.get(current)
    return False


def run_metadata_eval(db_path: str) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = _table_columns(conn, "chunks")
        required_columns = {
            "chunk_id",
            "document_version_id",
            "chunk_type",
            "document_region",
            "section_ref",
            "section_level",
            "parent_section_ref",
            "sequence_no",
        }
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            return {
                "mode": "metadata",
                "total": 0,
                "passed": 0,
                "failed_count": len(missing_columns),
                "pass_rate": 0.0,
                "summary": {"missing_columns": len(missing_columns)},
                "failures": [{"check": "missing_column", "column": column} for column in missing_columns],
            }

        rows = cast(list[sqlite3.Row], conn.execute(
            """SELECT chunk_id, document_version_id, chunk_type, document_region,
                      section_ref, section_level, parent_section_ref, sequence_no
               FROM chunks
               ORDER BY document_version_id, sequence_no"""
        ).fetchall())
    finally:
        conn.close()

    failures: list[Failure] = []
    section_refs_by_doc: dict[int, set[str]] = defaultdict(set)
    level_by_doc_section: dict[tuple[int, str], int] = {}
    parent_by_doc_section: dict[int, dict[str, str | None]] = defaultdict(dict)

    for row in rows:
        chunk_type = _row_value(row, "chunk_type")
        if chunk_type not in CHUNK_TYPE_VALUES:
            _append_failure(failures, "invalid_chunk_type", row, value=chunk_type)

        document_region = _row_value(row, "document_region")
        if document_region not in DOCUMENT_REGION_VALUES:
            _append_failure(failures, "invalid_document_region", row, value=document_region)

        section_ref = _row_value(row, "section_ref")
        if not section_ref:
            continue

        doc_id = int(str(_row_value(row, "document_version_id")))
        section_ref = str(section_ref)
        section_refs_by_doc[doc_id].add(section_ref)
        expected_level = section_level(section_ref)
        actual_level = _row_value(row, "section_level")
        if expected_level is not None and actual_level != expected_level:
            _append_failure(
                failures,
                "section_level_mismatch",
                row,
                expected=expected_level,
                actual=actual_level,
            )

        expected_parent = parent_section_ref(section_ref)
        actual_parent = _row_value(row, "parent_section_ref")
        if actual_parent != expected_parent:
            _append_failure(
                failures,
                "parent_ref_mismatch",
                row,
                expected=expected_parent,
                actual=actual_parent,
            )

        if actual_level is not None:
            level_by_doc_section[(doc_id, section_ref)] = int(str(actual_level))
        parent_by_doc_section[doc_id][section_ref] = str(actual_parent) if actual_parent else None

    for doc_id, refs in section_refs_by_doc.items():
        for section_ref in refs:
            parent = parent_by_doc_section[doc_id].get(section_ref)
            if parent and parent not in refs:
                failures.append({
                    "check": "missing_parent_section",
                    "document_version_id": doc_id,
                    "section_ref": section_ref,
                    "parent_section_ref": parent,
                })
                continue

            if parent:
                child_level = level_by_doc_section.get((doc_id, section_ref))
                parent_level = level_by_doc_section.get((doc_id, parent))
                if child_level is not None and parent_level is not None and child_level != parent_level + 1:
                    failures.append({
                        "check": "hierarchy_not_monotonic",
                        "document_version_id": doc_id,
                        "section_ref": section_ref,
                        "parent_section_ref": parent,
                        "parent_level": parent_level,
                        "child_level": child_level,
                    })

            if _detect_cycle(section_ref, parent_by_doc_section[doc_id]):
                failures.append({
                    "check": "parent_cycle",
                    "document_version_id": doc_id,
                    "section_ref": section_ref,
                })

    total = len(rows)
    if total == 0:
        return {
            "mode": "metadata",
            "total": 1,
            "passed": 0,
            "failed_count": 1,
            "pass_rate": 0.0,
            "summary": {
                "chunks_checked": 0,
                "documents_checked": 0,
                "invalid_enums": 0,
                "missing_parents": 0,
                "cycles": 0,
            },
            "failures": [{"check": "no_chunks", "message": "chunks contains no rows to validate"}],
        }

    failed_count = len(failures)
    passed = max(total - failed_count, 0)
    pass_rate = passed / total
    return {
        "mode": "metadata",
        "total": total,
        "passed": passed,
        "failed_count": failed_count,
        "pass_rate": round(pass_rate, 4),
        "summary": {
            "chunks_checked": total,
            "documents_checked": len(section_refs_by_doc),
            "invalid_enums": sum(1 for failure in failures if failure["check"] in {"invalid_chunk_type", "invalid_document_region"}),
            "missing_parents": sum(1 for failure in failures if failure["check"] == "missing_parent_section"),
            "cycles": sum(1 for failure in failures if failure["check"] == "parent_cycle"),
        },
        "failures": failures[:50],
    }
