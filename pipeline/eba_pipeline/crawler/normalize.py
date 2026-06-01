"""Post-download eba_id normalization from PDF content.

Extracts official EBA reference IDs from the first pages of downloaded PDFs,
replacing synthetic IDs (EBA/LARGE-*) with real ones where possible.
Documents where ID cannot be confidently extracted go to review queue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import yaml


EBA_ID_RE = re.compile(
    r"\b((?:EBA|JC)[-/](?:GL|CP|Op|OP|REP|RTS|ITS|BS|DC|DP|REC|Rec)[-/]\d{4}[-/]\d{1,4})\b",
    re.I,
)

TYPE_PREFIX_MAP: dict[str, str] = {
    "guidelines": "GL",
    "rts": "RTS",
    "its": "ITS",
}


@dataclass
class NormalizationResult:
    original_id: str
    normalized_id: str | None
    all_refs: list[str]
    confidence: str  # "high", "medium", "low", "unresolved"
    reason: str


def extract_refs_from_pdf(pdf_path: Path, max_pages: int = 5) -> list[str]:
    """Extract all EBA reference IDs from the first N pages of a PDF."""
    doc = pymupdf.open(str(pdf_path))
    text = ""
    for i in range(min(max_pages, len(doc))):
        text += doc[i].get_text() + "\n"
    doc.close()
    raw_refs = EBA_ID_RE.findall(text)
    # Normalize: replace dashes with slashes, deduplicate
    normalized = list(dict.fromkeys(r.replace("-", "/") for r in raw_refs))
    return normalized


def pick_primary_ref(
    refs: list[str], document_type: str, title: str
) -> tuple[str | None, str]:
    """Pick the most likely primary EBA ID from multiple candidates.

    Returns (selected_id, confidence) where confidence is "high" or "medium".
    """
    if not refs:
        return None, "unresolved"
    if len(refs) == 1:
        return refs[0], "high"

    expected_prefix = TYPE_PREFIX_MAP.get(document_type, "")

    # Filter to refs matching expected document type
    type_matching = [
        r for r in refs if expected_prefix and f"/{expected_prefix}/" in r.upper()
    ]

    if len(type_matching) == 1:
        return type_matching[0], "high"

    candidates = type_matching if type_matching else refs

    # For consolidated docs, prefer the original (older) GL number
    if "consolidat" in title.lower() and candidates:
        candidates_sorted = sorted(candidates)
        return candidates_sorted[0], "medium"

    # For "Final Report" docs, prefer the one that matches the type
    if type_matching:
        # Pick the one with the highest year/number (most recent = this doc)
        type_matching_sorted = sorted(type_matching, reverse=True)
        return type_matching_sorted[0], "medium"

    # Fallback: first ref
    return refs[0], "medium"


def normalize_manifest(
    manifest_path: Path,
    pdfs_dir: Path,
    output_path: Path | None = None,
    review_queue_path: Path | None = None,
) -> tuple[list[dict], list[NormalizationResult]]:
    """Normalize eba_ids in a manifest using PDF content extraction.

    Returns (updated_documents, all_results).
    """
    data = yaml.safe_load(manifest_path.read_text())
    documents = data.get("documents", [])
    results: list[NormalizationResult] = []
    review_queue: list[dict] = []
    seen_ids: dict[str, str] = {}  # normalized_id -> original slug (for collision detection)

    for doc in documents:
        original_id = doc["eba_id"]
        slug = original_id.replace("/", "-")
        pdf_dir = pdfs_dir / slug / "en"

        if not pdf_dir.exists():
            results.append(
                NormalizationResult(
                    original_id=original_id,
                    normalized_id=None,
                    all_refs=[],
                    confidence="unresolved",
                    reason="PDF directory not found",
                )
            )
            review_queue.append({"eba_id": original_id, "reason": "pdf_not_found", "title": doc.get("title", "")})
            continue

        pdfs = list(pdf_dir.glob("*.pdf"))
        if not pdfs:
            results.append(
                NormalizationResult(
                    original_id=original_id,
                    normalized_id=None,
                    all_refs=[],
                    confidence="unresolved",
                    reason="No PDF files found",
                )
            )
            review_queue.append({"eba_id": original_id, "reason": "no_pdf", "title": doc.get("title", "")})
            continue

        refs = extract_refs_from_pdf(pdfs[0])
        selected, confidence = pick_primary_ref(refs, doc.get("document_type", ""), doc.get("title", ""))

        if selected and not original_id.startswith("EBA/LARGE"):
            if selected.upper() == original_id.upper().replace("-", "/"):
                confidence = "high"
            final_id = original_id
            if original_id in seen_ids:
                dup_count = sum(1 for r in results if r.normalized_id and r.normalized_id.startswith(original_id))
                final_id = f"{original_id}/DUP{dup_count + 1}"
                doc["eba_id"] = final_id
                review_queue.append({
                    "eba_id": original_id,
                    "normalized_to": final_id,
                    "reason": "collision",
                    "collides_with": seen_ids[original_id],
                    "title": doc.get("title", ""),
                    "all_refs": refs,
                })
            else:
                seen_ids[original_id] = slug
            results.append(
                NormalizationResult(
                    original_id=original_id,
                    normalized_id=final_id,
                    all_refs=refs,
                    confidence=confidence,
                    reason="Already has real ID",
                )
            )
            continue

        if selected:
            # Check collision
            if selected in seen_ids:
                # Collision - add to review queue
                confidence = "low"
                review_queue.append({
                    "eba_id": original_id,
                    "normalized_to": selected,
                    "reason": "collision",
                    "collides_with": seen_ids[selected],
                    "title": doc.get("title", ""),
                    "all_refs": refs,
                })
                # Append /DUP suffix
                dup_count = sum(1 for r in results if r.normalized_id and r.normalized_id.startswith(selected))
                selected = f"{selected}/DUP{dup_count + 1}"

            seen_ids[selected] = slug
            doc["eba_id"] = selected
            doc["_original_slug"] = slug
            results.append(
                NormalizationResult(
                    original_id=original_id,
                    normalized_id=selected,
                    all_refs=refs,
                    confidence=confidence,
                    reason=f"Extracted from PDF ({len(refs)} refs found)",
                )
            )
        else:
            results.append(
                NormalizationResult(
                    original_id=original_id,
                    normalized_id=None,
                    all_refs=refs,
                    confidence="unresolved",
                    reason="No EBA reference found in PDF",
                )
            )
            review_queue.append({
                "eba_id": original_id,
                "reason": "no_ref_in_pdf",
                "title": doc.get("title", ""),
            })

    # Write outputs
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            yaml.safe_dump({"documents": documents}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    if review_queue_path and review_queue:
        review_queue_path.parent.mkdir(parents=True, exist_ok=True)
        review_queue_path.write_text(
            yaml.safe_dump({"review_queue": review_queue}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    return documents, results
