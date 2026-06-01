import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast


JsonScalar = str | int | float | bool | None
MetricValue = JsonScalar | list[str]
NumericMetric = int | float
PageRecord = Mapping[str, object]
ChunkRecord = Mapping[str, object]


THRESHOLDS = {
    "min_chars_total": 1000,
    "page_coverage_ratio": 0.85,
    "paragraph_ref_detection_ratio": 0.70,
    "broken_word_ratio": 0.05,
    "empty_page_ratio": 0.10,
    "duplicate_chunk_ratio": 0.05,
}


def summarize_duplicate_chunk_ids(chunks: Sequence[ChunkRecord]) -> dict[str, MetricValue]:
    chunk_ids = [str(chunk.get("chunk_id", "")) for chunk in chunks if chunk.get("chunk_id")]
    counts = Counter(chunk_ids)
    duplicates = {chunk_id: count for chunk_id, count in counts.items() if count > 1}
    duplicate_rows = sum(count - 1 for count in duplicates.values())
    sample_ids = sorted(duplicates)[:5]
    return {
        "duplicate_chunk_id_count": len(duplicates),
        "duplicate_chunk_row_count": duplicate_rows,
        "duplicate_chunk_id_samples": sample_ids,
        "has_duplicate_chunk_ids": bool(duplicates),
    }


def validate_unique_chunk_ids(chunks: Sequence[ChunkRecord], context: str) -> None:
    summary = summarize_duplicate_chunk_ids(chunks)
    if not bool(summary["has_duplicate_chunk_ids"]):
        return
    sample_values = summary["duplicate_chunk_id_samples"]
    sample_ids = sample_values if isinstance(sample_values, list) else []
    sample = ", ".join(sample_ids)
    message = (
        f"Duplicate chunk_id values detected for {context}: "
        f"{summary['duplicate_chunk_id_count']} duplicate ids / "
        f"{summary['duplicate_chunk_row_count']} colliding rows. Sample ids: {sample}"
    )
    raise ValueError(
        message
    )


def _as_float(value: MetricValue) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    raise TypeError(f"Expected numeric metric, got {type(value).__name__}")


def _as_bool(value: MetricValue) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean metric, got {type(value).__name__}")


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    raise TypeError(f"Expected integer-like value, got {type(value).__name__}")


def compute_metrics(
    pages: Sequence[PageRecord], chunks: Sequence[ChunkRecord], _eba_id_slug: str
) -> dict[str, MetricValue]:
    total_chars = sum(
        _as_int(p.get("char_count", len(str(p.get("text", ""))))) for p in pages
    )
    total_pages = len(pages)
    non_empty_pages = sum(1 for p in pages if len(str(p.get("text", "")).strip()) > 50)
    page_coverage = non_empty_pages / total_pages if total_pages > 0 else 0.0
    empty_page_ratio = 1.0 - page_coverage

    para_chunks = [c for c in chunks if c.get("chunk_type") == "paragraph"]
    chunks_with_ref = [c for c in para_chunks if c.get("paragraph_ref")]
    para_ref_ratio = len(chunks_with_ref) / len(para_chunks) if para_chunks else 1.0

    all_text = " ".join(str(p.get("text", "")) for p in pages)
    words = all_text.split()
    broken = sum(1 for w in words if w.endswith("-") and len(w) > 2)
    broken_word_ratio = broken / len(words) if words else 0.0

    texts = [str(c.get("text", "")) for c in chunks]
    unique_texts = set(texts)
    dup_ratio = 1.0 - (len(unique_texts) / len(texts)) if texts else 0.0
    duplicate_chunk_ids = summarize_duplicate_chunk_ids(chunks)

    first_page_text = str(pages[0].get("text", "")) if pages else ""
    detected_title = len(first_page_text.strip()) > 50

    return {
        "total_chars": total_chars,
        "total_pages": total_pages,
        "total_chunks": len(chunks),
        "page_coverage_ratio": round(page_coverage, 4),
        "empty_page_ratio": round(empty_page_ratio, 4),
        "paragraph_ref_detection_ratio": round(para_ref_ratio, 4),
        "broken_word_ratio": round(broken_word_ratio, 4),
        "duplicate_chunk_ratio": round(dup_ratio, 4),
        "duplicate_chunk_id_count": duplicate_chunk_ids["duplicate_chunk_id_count"],
        "duplicate_chunk_row_count": duplicate_chunk_ids["duplicate_chunk_row_count"],
        "duplicate_chunk_id_samples": duplicate_chunk_ids["duplicate_chunk_id_samples"],
        "has_duplicate_chunk_ids": duplicate_chunk_ids["has_duplicate_chunk_ids"],
        "detected_title": detected_title,
        "detected_document_type": detected_title,
    }


def compute_quality_score(metrics: Mapping[str, MetricValue]) -> float:
    checks = [
        _as_float(metrics["total_chars"]) >= THRESHOLDS["min_chars_total"],
        _as_float(metrics["page_coverage_ratio"]) >= THRESHOLDS["page_coverage_ratio"],
        _as_float(metrics["paragraph_ref_detection_ratio"])
        >= THRESHOLDS["paragraph_ref_detection_ratio"],
        _as_float(metrics["broken_word_ratio"]) <= THRESHOLDS["broken_word_ratio"],
        _as_float(metrics["empty_page_ratio"]) <= THRESHOLDS["empty_page_ratio"],
        _as_float(metrics["duplicate_chunk_ratio"]) <= THRESHOLDS["duplicate_chunk_ratio"],
        not _as_bool(metrics["has_duplicate_chunk_ids"]),
        _as_bool(metrics["detected_title"]),
        _as_bool(metrics["detected_document_type"]),
    ]
    return round(sum(checks) / len(checks), 4)


def assess_document(doc_dir: Path, reports_dir: Path) -> Mapping[str, object]:
    eba_id_slug = doc_dir.name
    pages_file = doc_dir / "pages.json"
    chunks_file = doc_dir / "chunks.json"

    pages = cast(
        Sequence[PageRecord], json.loads(pages_file.read_text()) if pages_file.exists() else []
    )
    chunks = cast(
        Sequence[ChunkRecord],
        json.loads(chunks_file.read_text()) if chunks_file.exists() else [],
    )

    metrics = compute_metrics(pages, chunks, eba_id_slug)
    quality_score = compute_quality_score(metrics)
    passed = quality_score >= 0.85 and not _as_bool(metrics["has_duplicate_chunk_ids"])

    report = {
        "eba_id_slug": eba_id_slug,
        "quality_score": quality_score,
        "passed": passed,
        "needs_review": not passed,
        "metrics": metrics,
        "thresholds": THRESHOLDS,
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    _ = (reports_dir / f"{eba_id_slug}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    return report


def run_quality_all(processed_dir: Path, reports_dir: Path) -> list[Mapping[str, object]]:
    results: list[Mapping[str, object]] = []
    for doc_dir in sorted(processed_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        if not (doc_dir / "pages.json").exists():
            continue
        print(f"  Assessing {doc_dir.name}...")
        report = assess_document(doc_dir, reports_dir)
        status = "PASS" if report["passed"] else "FAIL (needs_review)"
        print(f"    score={report['quality_score']} [{status}]")
        results.append(report)
    return results
