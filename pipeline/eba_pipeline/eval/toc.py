from __future__ import annotations

import sqlite3
from typing import cast

BOILERPLATE_TOC_TITLES = {
    "guidelines",
    "background",
    "next steps",
    "definitions",
    "contents",
    "table of contents",
}

BOILERPLATE_PHRASES = (
    "do you have any comments",
    "feedback on",
    "summary of responses",
    "public consultation",
    "analysis of responses",
    "consultation responses",
)

EXCLUDED_REGIONS = {
    "front_matter",
    "back_matter",
    "consultation_feedback",
}


def _row_value(row: sqlite3.Row, key: str) -> object:
    return cast(object, row[key])


def _row_index(row: sqlite3.Row, index: int) -> object:
    return cast(object, row[index])


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = cast(list[sqlite3.Row], conn.execute(f"PRAGMA table_info({table_name})").fetchall())
    return {str(_row_index(row, 1)) for row in rows}


def _normalized(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def is_boilerplate_toc_title(title: object) -> bool:
    normalized = _normalized(title).rstrip(".:;")
    return normalized in BOILERPLATE_TOC_TITLES or any(phrase in normalized for phrase in BOILERPLATE_PHRASES)


def run_toc_eval(db_path: str, *, threshold: float = 0.95) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        table_rows = cast(list[sqlite3.Row], conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall())
        if "document_toc" not in {str(_row_index(row, 0)) for row in table_rows}:
            return {
                "mode": "toc",
                "total": 0,
                "passed": 0,
                "failed_count": 1,
                "pass_rate": 0.0,
                "threshold": threshold,
                "summary": {"missing_tables": 1},
                "failures": [{"check": "missing_table", "table": "document_toc"}],
            }

        chunk_columns = _table_columns(conn, "chunks")
        required_chunk_columns = {"document_version_id", "section_ref", "document_region"}
        missing_chunk_columns = sorted(required_chunk_columns - chunk_columns)
        toc_columns = _table_columns(conn, "document_toc")
        required_toc_columns = {"document_version_id", "section_ref", "title", "level"}
        missing_toc_columns = sorted(required_toc_columns - toc_columns)
        if missing_chunk_columns or missing_toc_columns:
            failures = [
                *({"check": "missing_column", "table": "chunks", "column": column} for column in missing_chunk_columns),
                *({"check": "missing_column", "table": "document_toc", "column": column} for column in missing_toc_columns),
            ]
            return {
                "mode": "toc",
                "total": 0,
                "passed": 0,
                "failed_count": len(failures),
                "pass_rate": 0.0,
                "threshold": threshold,
                "summary": {"missing_columns": len(failures)},
                "failures": failures,
            }

        eligible_sections = {
            (int(str(_row_value(row, "document_version_id"))), str(_row_value(row, "section_ref")))
            for row in cast(list[sqlite3.Row], conn.execute(
                """SELECT DISTINCT document_version_id, section_ref
                   FROM chunks
                   WHERE section_ref IS NOT NULL
                     AND section_ref != ''
                     AND COALESCE(document_region, 'body') NOT IN ({})""".format(
                    ",".join("?" for _ in EXCLUDED_REGIONS)
                ),
                tuple(EXCLUDED_REGIONS),
            ).fetchall())
        }
        toc_rows = cast(list[sqlite3.Row], conn.execute(
            """SELECT document_version_id, section_ref, title, level, parent_section_ref
               FROM document_toc
               ORDER BY document_version_id, sequence_start, section_ref"""
        ).fetchall())
    finally:
        conn.close()

    toc_sections = {(int(str(_row_value(row, "document_version_id"))), str(_row_value(row, "section_ref"))) for row in toc_rows}
    covered_sections = eligible_sections & toc_sections
    missing_sections = sorted(eligible_sections - toc_sections)
    extra_sections = sorted(toc_sections - eligible_sections)
    boilerplate_rows = [
        {
            "check": "boilerplate_toc_title",
            "document_version_id": int(str(_row_value(row, "document_version_id"))),
            "section_ref": _row_value(row, "section_ref"),
            "title": _row_value(row, "title"),
        }
        for row in toc_rows
        if is_boilerplate_toc_title(_row_value(row, "title"))
    ]

    if not eligible_sections and not toc_rows:
        return {
            "mode": "toc",
            "total": 1,
            "passed": 0,
            "failed_count": 1,
            "pass_rate": 0.0,
            "threshold": threshold,
            "summary": {
                "eligible_sections": 0,
                "toc_rows": 0,
                "covered_sections": 0,
                "missing_sections": 0,
                "extra_sections": 0,
                "boilerplate_rows": 0,
            },
            "failures": [{"check": "no_toc_sections", "message": "No eligible chunk sections or document_toc rows found"}],
        }

    coverage_rate = len(covered_sections) / len(eligible_sections) if eligible_sections else 0.0
    failures: list[dict[str, object]] = []
    failures.extend(boilerplate_rows)
    failures.extend(
        {
            "check": "missing_toc_section",
            "document_version_id": doc_id,
            "section_ref": section_ref,
        }
        for doc_id, section_ref in missing_sections[:50]
    )
    if coverage_rate < threshold:
        failures.append({
            "check": "toc_coverage_below_threshold",
            "coverage_rate": round(coverage_rate, 4),
            "threshold": threshold,
            "eligible_sections": len(eligible_sections),
            "covered_sections": len(covered_sections),
        })

    failed_count = len(failures)
    total = len(eligible_sections) + len(boilerplate_rows)
    passed = max(total - failed_count, 0)
    return {
        "mode": "toc",
        "total": total,
        "passed": passed,
        "failed_count": failed_count,
        "pass_rate": round(coverage_rate, 4),
        "threshold": threshold,
        "summary": {
            "eligible_sections": len(eligible_sections),
            "toc_rows": len(toc_rows),
            "covered_sections": len(covered_sections),
            "missing_sections": len(missing_sections),
            "extra_sections": len(extra_sections),
            "boilerplate_rows": len(boilerplate_rows),
        },
        "failures": failures[:50],
    }
