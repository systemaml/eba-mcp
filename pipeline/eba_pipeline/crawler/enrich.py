"""Metadata enrichment: extract application dates and relationships from PDF content."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
import yaml

EBA_ID_RE = re.compile(
    r"\b((?:EBA|JC)[-/](?:GL|CP|Op|OP|REP|RTS|ITS|BS|DC|DP|REC|Rec)[-/]\d{4}[-/]\d{1,4})\b",
    re.I,
)

APPLICATION_DATE_RE = re.compile(
    r"(?:appl(?:y|ies|icable)\s+(?:from|as\s+of|since|with\s+effect\s+from))\s+"
    r"(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{4}|\d{4}-\d{2}-\d{2})",
    re.I,
)

AMENDING_RE = re.compile(r"\bamend(?:s|ing|ed)\b", re.I)
REPEALING_RE = re.compile(r"\brepeal(?:s|ing|ed)\b", re.I)
SUPERSEDING_RE = re.compile(r"\b(?:supersed(?:es|ing|ed)|replac(?:es|ing|ed))\b", re.I)
CONSOLIDATING_RE = re.compile(r"\bconsolidat(?:es|ing|ed)\b", re.I)


@dataclass
class DocumentEnrichment:
    eba_id: str
    application_date: str | None = None
    relationships: list[dict[str, str]] = field(default_factory=list)
    all_refs_in_pdf: list[str] = field(default_factory=list)


def _parse_month_date(raw: str) -> str | None:
    """Try to normalize a date string to YYYY-MM-DD or YYYY-MM format."""
    import calendar

    month_map = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
    month_abbr_map = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
    month_map.update(month_abbr_map)

    raw = raw.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]

    parts = raw.split()
    if len(parts) == 2:
        month_str, year = parts
        month_num = month_map.get(month_str.lower())
        if month_num and year.isdigit():
            return f"{year}-{month_num:02d}-01"
    elif len(parts) == 3:
        day_str, month_str, year = parts
        day = int(re.sub(r"\D", "", day_str) or "0")
        month_num = month_map.get(month_str.lower())
        if month_num and year.isdigit() and 1 <= day <= 31:
            return f"{year}-{month_num:02d}-{day:02d}"
    return None


def _infer_relationship_type(title: str, context: str) -> str:
    combined = f"{title} {context}"
    if CONSOLIDATING_RE.search(combined):
        return "consolidates"
    if REPEALING_RE.search(combined):
        return "repeals"
    if SUPERSEDING_RE.search(combined):
        return "supersedes"
    if AMENDING_RE.search(combined):
        return "amends"
    return "related_to"


def enrich_document(eba_id: str, pdf_path: Path, title: str) -> DocumentEnrichment:
    doc = pymupdf.open(str(pdf_path))
    enrichment = DocumentEnrichment(eba_id=eba_id)

    full_text = ""
    for i in range(min(10, len(doc))):
        full_text += doc[i].get_text() + "\n"
    doc.close()

    all_refs = list(dict.fromkeys(r.replace("-", "/") for r in EBA_ID_RE.findall(full_text)))
    enrichment.all_refs_in_pdf = all_refs

    app_match = APPLICATION_DATE_RE.search(full_text)
    if app_match:
        enrichment.application_date = _parse_month_date(app_match.group(1))

    own_id_upper = eba_id.upper().replace("-", "/")
    other_refs = [r for r in all_refs if r.upper() != own_id_upper]

    for ref in other_refs:
        rel_type = _infer_relationship_type(title, "")
        enrichment.relationships.append({
            "source_eba_id": eba_id,
            "target_eba_id": ref,
            "relationship_type": rel_type,
            "confidence": "medium",
        })

    return enrichment


def enrich_manifest(
    manifest_path: Path,
    pdfs_dir: Path,
    output_path: Path | None = None,
) -> list[DocumentEnrichment]:
    data = yaml.safe_load(manifest_path.read_text())
    documents = data.get("documents", [])
    enrichments: list[DocumentEnrichment] = []

    for doc in documents:
        eba_id = doc["eba_id"]
        slug_candidates = [
            eba_id.replace("/", "-"),
            doc.get("_original_slug", eba_id.replace("/", "-")),
        ]

        pdf_path = None
        for slug in slug_candidates:
            pdf_dir = pdfs_dir / slug / "en"
            if pdf_dir.exists():
                pdfs = list(pdf_dir.glob("*.pdf"))
                if pdfs:
                    pdf_path = pdfs[0]
                    break

        if not pdf_path:
            enrichments.append(DocumentEnrichment(eba_id=eba_id))
            continue

        enrichment = enrich_document(eba_id, pdf_path, doc.get("title", ""))
        enrichments.append(enrichment)

        if enrichment.application_date:
            doc["application_date"] = enrichment.application_date

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            yaml.safe_dump({"documents": documents}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    return enrichments
