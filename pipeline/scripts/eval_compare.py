#!/usr/bin/env python3
"""Hybrid vs FTS-only retrieval comparison script.

Runs the semantic eval queries in two modes:
  - fts_only: EBA_SEARCH_MODE=fts_only
  - hybrid:   EBA_SEARCH_MODE=hybrid (requires Ollama + embedded DB)

Produces a markdown report with recall@10/MRR metrics.

Usage:
    python pipeline/scripts/eval_compare.py \
        --db data/atlas-real-vec.db \
        --queries pipeline/eba_pipeline/eval/queries.yaml \
        --tags semantic \
        --output .sisyphus/evidence/task-10-eval-comparison.md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import yaml


def _run_query(
    db_path: str,
    query_text: str,
    search_mode: str,
    limit: int = 10,
    root_dir: Path | None = None,
) -> list[dict[str, Any]]:
    env = os.environ.copy()
    env["EBA_SEARCH_MODE"] = search_mode

    request = {
        "jsonrpc": "2.0",
        "id": "cmp",
        "method": "tools/call",
        "params": {
            "name": "eba_search",
            "arguments": {"query": query_text, "filters": {}, "limit": limit},
        },
    }

    completed = subprocess.run(
        ["node", "dist/index.js", "--db", db_path],
        input=json.dumps(request) + "\n",
        cwd=str(root_dir or Path.cwd()),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"node exited {completed.returncode}")

    outer = json.loads(completed.stdout)
    payload = json.loads(outer["result"]["content"][0]["text"])
    return cast(list[dict[str, Any]], payload.get("citations", []))


def _score_citation(
    citation: dict[str, Any],
    expected_eba_id: str | None,
    expected_paragraph_refs: list[str],
    expected_eba_ids: list[str] | None = None,
) -> bool:
    """Return True if *citation* satisfies the expected constraints.

    *expected_eba_ids* (list) takes priority over the legacy *expected_eba_id*
    (single string).  When both are absent the EBA-ID constraint is waived.
    """
    if expected_eba_ids:
        eba_match = citation.get("eba_id") in expected_eba_ids
    elif expected_eba_id is not None:
        eba_match = citation.get("eba_id") == expected_eba_id
    else:
        eba_match = True

    if not eba_match:
        return False
    if expected_paragraph_refs:
        return citation.get("paragraph_ref") in expected_paragraph_refs
    return True


def _reciprocal_rank(
    citations: list[dict[str, Any]],
    expected_eba_id: str | None,
    expected_paragraph_refs: list[str],
    expected_eba_ids: list[str] | None = None,
) -> float:
    for rank, citation in enumerate(citations, 1):
        if _score_citation(citation, expected_eba_id, expected_paragraph_refs, expected_eba_ids):
            return 1.0 / rank
    return 0.0


def _hit_at_k(
    citations: list[dict[str, Any]],
    expected_eba_id: str | None,
    expected_paragraph_refs: list[str],
    k: int = 10,
    expected_eba_ids: list[str] | None = None,
) -> bool:
    return any(
        _score_citation(c, expected_eba_id, expected_paragraph_refs, expected_eba_ids)
        for c in citations[:k]
    )


def run_comparison(
    db_path: str,
    queries_path: str,
    tags: str | None,
    output_path: str | None,
    root_dir: Path,
) -> None:
    loaded = yaml.safe_load(Path(queries_path).read_text()) or {}
    fixtures = loaded.get("queries", [])

    tag_filter: set[str] | None = None
    if tags:
        tag_filter = {t.strip() for t in tags.split(",") if t.strip()}

    if tag_filter:
        fixtures = [f for f in fixtures if tag_filter.intersection(set(f.get("tags", [])))]

    print(f"Running {len(fixtures)} queries for comparison (tags={tags or 'all'})")
    print(f"DB: {db_path}")
    print("Hit criterion: eba_id match + paragraph_ref in expected_paragraph_refs (when defined)")

    modes = ["fts_only", "hybrid"]
    rows: list[dict[str, Any]] = []

    for fixture in fixtures:
        query_text = str(fixture.get("query", ""))
        expected_eba_id = fixture.get("expected_eba_id")
        expected_eba_ids = list(fixture.get("expected_eba_ids", [])) or None
        expected_paragraph_refs = list(fixture.get("expected_paragraph_refs", []))
        fixture_id = str(fixture.get("id", query_text[:40]))

        if not query_text.strip():
            continue

        row: dict[str, Any] = {
            "id": fixture_id,
            "query": query_text,
            "expected_eba_id": expected_eba_id,
            "expected_eba_ids": expected_eba_ids,
            "expected_paragraph_refs": expected_paragraph_refs,
        }

        for mode in modes:
            try:
                citations = _run_query(db_path, query_text, mode, limit=10, root_dir=root_dir)
                rr = _reciprocal_rank(citations, expected_eba_id, expected_paragraph_refs, expected_eba_ids)
                hit = _hit_at_k(citations, expected_eba_id, expected_paragraph_refs, k=10, expected_eba_ids=expected_eba_ids)
                top1 = f"{citations[0].get('eba_id','')} p={citations[0].get('paragraph_ref','?')}" if citations else ""
                row[mode] = {
                    "rr": rr,
                    "hit@10": hit,
                    "n_results": len(citations),
                    "top1": top1,
                }
            except Exception as exc:
                row[mode] = {"rr": 0.0, "hit@10": False, "n_results": 0, "top1": "", "error": str(exc)}

        rows.append(row)

        fts_hit = "✓" if row.get("fts_only", {}).get("hit@10") else "✗"
        hyb_hit = "✓" if row.get("hybrid", {}).get("hit@10") else "✗"
        fts_rr = row.get("fts_only", {}).get("rr", 0.0)
        hyb_rr = row.get("hybrid", {}).get("rr", 0.0)
        print(f"  [{fixture_id}] fts={fts_hit}({fts_rr:.2f}) hybrid={hyb_hit}({hyb_rr:.2f})  '{query_text[:55]}'")

    def agg(mode: str) -> tuple[float, float]:
        rrs = [r[mode]["rr"] for r in rows if mode in r]
        hits = [r[mode]["hit@10"] for r in rows if mode in r]
        mrr = sum(rrs) / len(rrs) if rrs else 0.0
        recall = sum(hits) / len(hits) if hits else 0.0
        return mrr, recall

    fts_mrr, fts_recall = agg("fts_only")
    hyb_mrr, hyb_recall = agg("hybrid")

    import datetime
    lines = [
        "# Hybrid vs FTS-only Retrieval Comparison",
        "",
        f"**Date:** {datetime.date.today()}",
        f"**DB:** `{db_path}`",
        f"**Query set:** {tags or 'all'} ({len(rows)} queries)",
        "**Hit criterion:** `eba_id` match **AND** `paragraph_ref` in `expected_paragraph_refs` (strict paragraph-level relevance)",
        "",
        "## Aggregate Metrics",
        "",
        "| Mode | MRR | Recall@10 |",
        "|------|-----|-----------|",
        f"| fts_only | {fts_mrr:.3f} | {fts_recall:.3f} |",
        f"| hybrid   | {hyb_mrr:.3f} | {hyb_recall:.3f} |",
        "",
        f"**Delta MRR:** {hyb_mrr - fts_mrr:+.3f}  **Delta Recall@10:** {hyb_recall - fts_recall:+.3f}",
        "",
        "## Per-Query Results",
        "",
        "| ID | Query | Target Doc(s) | Target Para(s) | FTS hit@10 | Hybrid hit@10 | FTS RR | Hybrid RR |",
        "|----|-------|--------------|---------------|-----------|--------------|--------|-----------|",
    ]

    for row in rows:
        fts = row.get("fts_only", {})
        hyb = row.get("hybrid", {})
        fts_h = "✓" if fts.get("hit@10") else "✗"
        hyb_h = "✓" if hyb.get("hit@10") else "✗"
        fts_rr_s = f"{fts.get('rr', 0.0):.3f}"
        hyb_rr_s = f"{hyb.get('rr', 0.0):.3f}"
        q_short = row["query"][:50].replace("|", "\\|")
        para_refs = ", ".join(row["expected_paragraph_refs"]) if row["expected_paragraph_refs"] else "any"
        ids_label = " \\| ".join(row["expected_eba_ids"]) if row.get("expected_eba_ids") else (row["expected_eba_id"] or "any")
        lines.append(f"| {row['id']} | {q_short} | {ids_label} | {para_refs} | {fts_h} | {hyb_h} | {fts_rr_s} | {hyb_rr_s} |")

    report = "\n".join(lines) + "\n"
    print()
    print(report)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report)
        print(f"Report written to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hybrid vs FTS-only eval comparison")
    parser.add_argument("--db", required=True, help="SQLite DB with vector embeddings")
    parser.add_argument("--queries", required=True, help="Path to queries.yaml")
    parser.add_argument("--tags", default=None, help="Comma-separated tags filter")
    parser.add_argument("--output", default=None, help="Output markdown report path")
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parents[2]
    run_comparison(
        db_path=str(Path(args.db).resolve()),
        queries_path=args.queries,
        tags=args.tags,
        output_path=args.output,
        root_dir=root_dir,
    )


if __name__ == "__main__":
    main()
