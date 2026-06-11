from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

Failure = dict[str, object]
MCP_NAVIGATION_LIMIT = 300


def _row_value(row: sqlite3.Row, key: str) -> object:
    return cast(object, row[key])


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _call_mcp_tool(db_path: str, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    request = {
        "jsonrpc": "2.0",
        "id": f"eval-{tool_name}",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    completed = subprocess.run(
        ["node", "dist/index.js", "--db", db_path],
        input=json.dumps(request) + "\n",
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"node exited {completed.returncode}")
    outer = cast(Mapping[str, object], json.loads(completed.stdout))
    result = cast(Mapping[str, object], outer["result"])
    content = cast(list[Mapping[str, object]], result["content"])
    text = content[0]["text"]
    if not isinstance(text, str):
        raise RuntimeError(f"MCP response for {tool_name} did not contain text content")
    return cast(dict[str, object], json.loads(text))


def _expected_section_chunk_ids(
    db_path: str,
    eba_id: str,
    sequence_start: object,
    sequence_end: object,
) -> list[str]:
    if sequence_start is None or sequence_end is None:
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = cast(list[tuple[str]], conn.execute(
            """SELECT c.chunk_id
               FROM chunks c
               JOIN document_versions dv ON c.document_version_id = dv.version_id
               JOIN documents d ON dv.document_id = d.eba_id
               WHERE d.eba_id = ?
                 AND c.sequence_no BETWEEN ? AND ?
               ORDER BY c.sequence_no""",
            (eba_id, sequence_start, sequence_end),
        ).fetchall())
    finally:
        conn.close()
    return [row[0] for row in rows]


def run_navigation_eval(db_path: str, *, sample_limit: int = 50) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "document_toc"):
            return {
                "mode": "navigation",
                "total": 0,
                "passed": 0,
                "failed_count": 1,
                "pass_rate": 0.0,
                "summary": {
                    "toc_rows_checked": 0,
                    "documents_checked": 0,
                    "range_mismatches": 0,
                    "mcp_errors": 0,
                },
                "failures": [{"check": "missing_table", "table": "document_toc"}],
            }
        rows = cast(list[sqlite3.Row], conn.execute(
            """SELECT d.eba_id, dt.section_ref, dt.page_start, dt.page_end,
                      dt.sequence_start, dt.sequence_end
               FROM document_toc dt
               JOIN document_versions dv ON dt.document_version_id = dv.version_id
               JOIN documents d ON dv.document_id = d.eba_id
               ORDER BY d.eba_id, COALESCE(dt.sequence_start, 9223372036854775807), dt.section_ref
               LIMIT ?""",
            (sample_limit,),
        ).fetchall())
    finally:
        conn.close()

    failures: list[Failure] = []
    if not rows:
        return {
            "mode": "navigation",
            "total": 1,
            "passed": 0,
            "failed_count": 1,
            "pass_rate": 0.0,
            "summary": {
                "toc_rows_checked": 0,
                "documents_checked": 0,
                "range_mismatches": 0,
                "mcp_errors": 0,
            },
            "failures": [{"check": "no_toc_rows", "message": "document_toc contains no rows to validate"}],
        }

    checked = 0
    toc_cache: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        checked += 1
        eba_id = str(_row_value(row, "eba_id"))
        section_ref = str(_row_value(row, "section_ref"))
        try:
            if eba_id not in toc_cache:
                toc_payload = _call_mcp_tool(db_path, "eba_get_toc", {"eba_id": eba_id, "limit": MCP_NAVIGATION_LIMIT})
                toc_cache[eba_id] = list(cast(list[dict[str, object]], toc_payload.get("toc", [])))
            toc_entry = next((entry for entry in toc_cache[eba_id] if entry.get("section_ref") == section_ref), None)
            if not toc_entry:
                failures.append({"check": "toc_entry_missing_from_mcp", "eba_id": eba_id, "section_ref": section_ref})
                continue

            for db_key, mcp_key in (
                ("page_start", "page_start"),
                ("page_end", "page_end"),
                ("sequence_start", "first_sequence_no"),
                ("sequence_end", "last_sequence_no"),
            ):
                db_value = _row_value(row, db_key)
                if db_value is not None and toc_entry.get(mcp_key) != db_value:
                    failures.append({
                        "check": "toc_range_mismatch",
                        "eba_id": eba_id,
                        "section_ref": section_ref,
                        "field": mcp_key,
                        "expected": db_value,
                        "actual": toc_entry.get(mcp_key),
                    })

            section_payload = _call_mcp_tool(
                db_path,
                "eba_get_section",
                {"eba_id": eba_id, "section": section_ref, "limit": MCP_NAVIGATION_LIMIT, "max_chars": 80},
            )
            citations = list(cast(list[dict[str, object]], section_payload.get("citations", [])))
            if section_payload.get("answerability") != "exact" or not citations:
                failures.append({"check": "section_missing_from_mcp", "eba_id": eba_id, "section_ref": section_ref})
                continue
            returned_chunk_ids = {str(citation.get("citation_id")) for citation in citations}
            expected_chunk_ids = _expected_section_chunk_ids(
                db_path,
                eba_id,
                _row_value(row, "sequence_start"),
                _row_value(row, "sequence_end"),
            )
            missing_chunk_ids = [chunk_id for chunk_id in expected_chunk_ids if chunk_id not in returned_chunk_ids]
            if missing_chunk_ids:
                failures.append({
                    "check": "section_missing_expected_chunks",
                    "eba_id": eba_id,
                    "section_ref": section_ref,
                    "missing_chunk_ids": missing_chunk_ids[:10],
                })
        except Exception as error:
            failures.append({
                "check": "mcp_navigation_error",
                "eba_id": eba_id,
                "section_ref": section_ref,
                "error": str(error),
            })

    failed_count = len(failures)
    passed = max(checked - failed_count, 0)
    pass_rate = passed / checked if checked else 1.0
    return {
        "mode": "navigation",
        "total": checked,
        "passed": passed,
        "failed_count": failed_count,
        "pass_rate": round(pass_rate, 4),
        "summary": {
            "toc_rows_checked": checked,
            "documents_checked": len(toc_cache),
            "range_mismatches": sum(1 for failure in failures if str(failure["check"]).endswith("mismatch")),
            "mcp_errors": sum(1 for failure in failures if failure["check"] == "mcp_navigation_error"),
        },
        "failures": failures[:50],
    }
