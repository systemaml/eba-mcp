import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import TypedDict, cast

import yaml

from eba_pipeline.ids import canonicalize_eba_id, slugify_eba_id
from eba_pipeline.parser.repair import repair_low_confidence_chunks

PARA_REF_PATTERN = re.compile(r"^\s*(\d+)\.\s+\S")
NUMBERED_PARA_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*\.?)\s+\S")
STANDALONE_REF_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*\.?)\s*$")
HEADING_PATTERN = re.compile(r"^(Title\s+[IVXLC]+|Section\s+\d+|Article\s+\d+|Annex\s+[IVXLC\d]+|Chapter\s+\d+|Contents|Executive Summary|Background and rationale|Background|Rationale|Status of these Guidelines|Status of these guidelines|Reporting requirements|Subject matter(?: and scope of application)?|Definitions|Date of application|Repeal|General considerations|Next steps|Guidelines)$", re.IGNORECASE)
EBA_ID_LINE_PATTERN = re.compile(r"^EBA/[A-Za-z]+/\d{4}/\d+$")

# Parser threshold rationale: these values intentionally favor high precision for
# citation/navigation metadata over aggressive section inference.
REPEATED_NOISE_PAGE_RATIO = 0.35  # Header/footer text must repeat on roughly one third of pages before removal.
NOISE_UPPERCASE_RATIO = 0.8  # Mostly-uppercase repeated lines are likely running headers, not body content.
MISSING_PARENT_CONFIDENCE_CAP = 0.85  # Orphaned numeric children retain metadata but are less trusted.
UNNUMBERED_HEADING_CONFIDENCE = 0.9  # Recognized headings without numeric refs are useful but weaker anchors.
BACK_MATTER_PAGE_RATIO = 0.9  # Repeal/date markers only switch region near the end of a document.
MAX_UNDOTTED_PARAGRAPH_REF = 999  # Four-digit undotted numbers are usually years/page artefacts, not paragraph refs.
SHORT_HEADING_MAX_CHARS = 100  # Short standalone headings are usually titles; longer lines are likely body text.
NUMERIC_HEADING_MAX_CHARS = 90  # Numeric headings need a tighter length cap to avoid promoting sentences.
NUMERIC_HEADING_MAX_WORDS = 8  # Numeric headings in EBA PDFs are concise; longer matches are usually paragraphs.
FRONT_MATTER_PAGE_LIMIT = 3  # Opening cover/contents material normally ends within the first three pages.
STANDALONE_REF_LOOKAHEAD_LINES = 3  # Three following lines distinguish ToC entries from actual paragraph starts.
PARAGRAPH_HEADING_MAX_CHARS = 100  # Inline paragraph headings are short labels before substantive text.
PARAGRAPH_HEADING_MAX_WORDS = 15  # Prevent long numbered paragraphs from being promoted to section headings.


class PageData(TypedDict):
    page_no: int
    text: str
    extraction_method: str
    char_count: int


class ChunkData(TypedDict):
    chunk_id: str
    eba_id: str
    language: str
    section_path: str
    paragraph_ref: str | None
    page_start: int
    page_end: int
    section_ref: str | None
    section_title: str | None
    section_level: int | None
    parent_section_ref: str | None
    document_region: str
    metadata_confidence: float
    metadata_source: str
    text: str
    text_hash: str
    chunk_type: str
    sequence_no: int


class SectionContext(TypedDict):
    ref: str
    title: str
    level: int
    confidence: float


def slugify(eba_id: str) -> str:
    return slugify_eba_id(eba_id)


def normalize_paragraph_ref(value: str) -> str:
    return value.strip().rstrip(".")


def make_chunk_id(
    eba_id_slug: str,
    lang: str,
    chunk_type: str,
    ref: str,
    text: str,
    page_start: int,
    sequence_no: int,
) -> str:
    """Build deterministic chunk ids as {slug}:{hash}:{lang}:{type}:{ref}:p{page}:s{sequence}."""
    type_initial = {
        "paragraph": "p",
        "heading": "h",
        "table": "t",
        "annex": "a",
        "footnote": "f",
        "front_matter": "f",
        "back_matter": "b",
        "consultation_response": "c",
    }.get(chunk_type, "p")
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:8]
    safe_ref = re.sub(r"[^A-Za-z0-9._-]", "_", ref)[:50]
    return f"{eba_id_slug}:{text_hash}:{lang}:{type_initial}:{safe_ref}:p{page_start}:s{sequence_no}"


def detect_paragraph_ref(line: str) -> str | None:
    match = NUMBERED_PARA_PATTERN.match(line)
    if match:
        value = normalize_paragraph_ref(match.group(1))
        if "." not in value and value.isdigit() and int(value) > MAX_UNDOTTED_PARAGRAPH_REF:
            return None
        return value
    match = PARA_REF_PATTERN.match(line)
    if match:
        value = normalize_paragraph_ref(match.group(1))
        if value.isdigit() and int(value) > MAX_UNDOTTED_PARAGRAPH_REF:
            return None
        return value
    return None


def detect_standalone_paragraph_ref(line: str) -> str | None:
    match = STANDALONE_REF_PATTERN.match(line)
    if not match:
        return None
    value = normalize_paragraph_ref(match.group(1))
    if "." not in value and value.isdigit() and int(value) > MAX_UNDOTTED_PARAGRAPH_REF:
        return None
    return value


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def section_level(section_ref: str | None) -> int | None:
    if not section_ref:
        return None
    return len(section_ref.split("."))


def parent_section_ref(section_ref: str | None) -> str | None:
    if not section_ref or "." not in section_ref:
        return None
    return section_ref.rsplit(".", 1)[0]


def strip_section_ref(line: str, section_ref: str) -> str:
    title = re.sub(rf"^{re.escape(section_ref)}\.?\s*", "", line.strip()).strip()
    return title or line.strip()


def looks_like_short_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > SHORT_HEADING_MAX_CHARS:
        return False
    if stripped.endswith((".", ";", ",")):
        return False
    if detect_paragraph_ref(stripped) or detect_standalone_paragraph_ref(stripped):
        return False
    return any(char.isalpha() for char in stripped)


def is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if HEADING_PATTERN.match(stripped):
        return True
    if (
        len(stripped) <= NUMERIC_HEADING_MAX_CHARS
        and re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z]", stripped)
        and len(stripped.split()) <= NUMERIC_HEADING_MAX_WORDS
    ):
        return True
    return False


def detect_heading_ref(line: str) -> str | None:
    match = re.match(r"^(\d+(?:\.\d+)*)\.?\s+", line.strip())
    if match:
        return normalize_paragraph_ref(match.group(1))
    return None


def looks_like_table(line: str) -> bool:
    stripped = line.strip()
    return bool(re.match(r"^(Table|Figure)\s+\d+\b", stripped, re.IGNORECASE) or " | " in stripped)


def looks_like_footnote(line: str) -> bool:
    stripped = line.strip()
    return bool(re.match(r"^\d+\s+EBA/[A-Za-z]+/\d{4}/\d+\b", stripped))


def marker_region(line: str) -> str | None:
    lower = line.lower()
    if re.match(r"^annex\s+[ivxlcdm\d]+\b", line, re.IGNORECASE):
        return "annex"
    if "feedback on the public consultation" in lower or "summary of responses" in lower or "consultation response" in lower:
        return "consultation_feedback"
    if lower in {"repeal", "final provisions"} or lower.startswith("repeal "):
        return "back_matter"
    return None


def chunk_type_for(line: str, document_region: str, fallback: str) -> str:
    if looks_like_table(line):
        return "table"
    if looks_like_footnote(line):
        return "footnote"
    if document_region == "consultation_feedback":
        return "consultation_response"
    if document_region in {"annex", "front_matter", "back_matter"}:
        return document_region
    if fallback == "heading":
        return "heading"
    return "paragraph"


def repeated_noise_lines(pages: list[PageData]) -> set[str]:
    counts: Counter[str] = Counter()
    for page in pages:
        page_lines = {
            normalize_line(line)
            for line in page["text"].splitlines()
            if normalize_line(line)
        }
        counts.update(page_lines)

    threshold = max(3, int(len(pages) * REPEATED_NOISE_PAGE_RATIO))
    noise: set[str] = set()
    for line, count in counts.items():
        if count < threshold:
            continue
        if re.fullmatch(r"\d+", line):
            noise.add(line)
            continue
        upper_ratio = sum(1 for char in line if char.isupper()) / max(1, sum(1 for char in line if char.isalpha()))
        if EBA_ID_LINE_PATTERN.match(line) or "FINAL REPORT" in line.upper() or upper_ratio >= NOISE_UPPERCASE_RATIO:
            noise.add(line)
    return noise


def paragraphize_document(pages: list[PageData], eba_id: str, language: str = "en") -> list[ChunkData]:
    eba_id_slug = slugify(eba_id)
    noise_lines = repeated_noise_lines(pages)
    total_pages = max((page["page_no"] for page in pages), default=0)
    raw_lines: list[tuple[int, str, str]] = []
    for page in pages:
        for line in page["text"].splitlines():
            raw_lines.append((page["page_no"], line.rstrip(), normalize_line(line)))

    chunks: list[ChunkData] = []
    seq = 0
    current_chunk_lines: list[tuple[int, str]] = []
    current_para_ref: str | None = None
    current_chunk_type = "paragraph"
    current_section_path = ""
    current_ref_context: str | None = None
    current_section_ref: str | None = None
    current_section_title: str | None = None
    current_section_level: int | None = None
    current_parent_section_ref: str | None = None
    current_metadata_confidence = 1.0
    current_document_region = "front_matter"
    seen_body = False
    section_stack: list[SectionContext] = []
    in_contents = False

    def flush_chunk() -> None:
        nonlocal seq, current_para_ref, current_chunk_type
        if not current_chunk_lines:
            return
        text = "\n".join(line for _, line in current_chunk_lines).strip()
        if not text:
            current_chunk_lines.clear()
            current_para_ref = None
            current_chunk_type = "paragraph"
            return
        page_start = current_chunk_lines[0][0]
        page_end = current_chunk_lines[-1][0]
        effective_ref = current_para_ref or current_ref_context
        ref = effective_ref if effective_ref else f"seq-{seq}"
        chunk_id = make_chunk_id(
            eba_id_slug,
            language,
            current_chunk_type,
            ref,
            text,
            page_start,
            seq,
        )
        chunks.append(
            {
                "chunk_id": chunk_id,
                "eba_id": eba_id,
                "language": language,
                "section_path": current_section_path,
                "paragraph_ref": effective_ref,
                "page_start": page_start,
                "page_end": page_end,
                "section_ref": current_section_ref,
                "section_title": current_section_title,
                "section_level": current_section_level,
                "parent_section_ref": current_parent_section_ref,
                "document_region": current_document_region,
                "metadata_confidence": max(0.0, min(1.0, current_metadata_confidence)),
                "metadata_source": "deterministic",
                "text": text,
                "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
                "chunk_type": current_chunk_type,
                "sequence_no": seq,
            }
        )
        seq += 1
        current_chunk_lines.clear()
        current_para_ref = None
        current_chunk_type = "paragraph"

    def section_path_from_stack() -> str:
        if section_stack:
            return " > ".join(section["title"] for section in section_stack)
        return current_section_path

    def set_section(section_ref: str | None, title: str, confidence: float) -> None:
        nonlocal current_section_path, current_ref_context, current_section_ref, current_section_title
        nonlocal current_section_level, current_parent_section_ref, current_metadata_confidence, section_stack
        clean_title = title.strip()[:160]
        current_section_path = clean_title[:100]
        current_ref_context = section_ref
        current_section_ref = section_ref
        current_section_title = clean_title or section_ref
        current_section_level = section_level(section_ref)
        current_parent_section_ref = parent_section_ref(section_ref)
        current_metadata_confidence = confidence
        if not section_ref or current_section_level is None:
            section_stack = []
            return

        expected_parent = parent_section_ref(section_ref)
        parent_present = expected_parent is None or any(section["ref"] == expected_parent for section in section_stack)
        retained = [
            section
            for section in section_stack
            if section["level"] < current_section_level and section_ref.startswith(f"{section['ref']}.")
        ]
        section_stack = retained
        section_stack.append(
            {
                "ref": section_ref,
                "title": clean_title,
                "level": current_section_level,
                "confidence": confidence if parent_present else min(confidence, MISSING_PARENT_CONFIDENCE_CAP),
            }
        )
        current_section_path = section_path_from_stack()[:160]
        current_metadata_confidence = section_stack[-1]["confidence"]

    def inherit_section_metadata() -> None:
        nonlocal current_section_path, current_section_ref, current_section_title, current_section_level
        nonlocal current_parent_section_ref, current_metadata_confidence
        if not section_stack:
            return
        section = section_stack[-1]
        current_section_ref = section["ref"]
        current_section_title = section["title"]
        current_section_level = section["level"]
        current_parent_section_ref = parent_section_ref(section["ref"])
        current_section_path = section_path_from_stack()[:160]
        current_metadata_confidence = section["confidence"]

    def detect_document_region(page_no: int, normalized: str) -> str:
        nonlocal current_document_region, seen_body
        region = marker_region(normalized)
        if region:
            if region == "annex" and current_document_region == "consultation_feedback":
                return current_document_region
            current_document_region = region
            return current_document_region
        if current_document_region in {"annex", "consultation_feedback"}:
            return current_document_region
        if current_document_region == "back_matter":
            return current_document_region
        if detect_paragraph_ref(normalized) or is_heading(normalized):
            if not normalized.lower().startswith(("contents", "executive summary")):
                seen_body = True
                current_document_region = "body"
        if not seen_body and page_no <= min(FRONT_MATTER_PAGE_LIMIT, max(total_pages, 1)):
            current_document_region = "front_matter"
        elif total_pages and page_no >= max(1, int(total_pages * BACK_MATTER_PAGE_RATIO)) and normalized.lower().startswith(("repeal", "date of application")):
            current_document_region = "back_matter"
        elif current_document_region == "front_matter" and seen_body:
            current_document_region = "body"
        return current_document_region

    def next_meaningful_lines(start_index: int, limit: int = STANDALONE_REF_LOOKAHEAD_LINES) -> list[str]:
        collected: list[str] = []
        for _, _, normalized in raw_lines[start_index:]:
            if not normalized or normalized in noise_lines or re.fullmatch(r"\d+", normalized):
                continue
            collected.append(normalized)
            if len(collected) >= limit:
                break
        return collected

    for index, (page_no, raw_line, normalized) in enumerate(raw_lines):
        stripped = raw_line.strip()
        if not normalized:
            continue
        if normalized in noise_lines or re.fullmatch(r"\d+", normalized) or EBA_ID_LINE_PATTERN.match(normalized):
            continue

        if page_no == 2 and normalized.lower() == "contents":
            flush_chunk()
            current_section_path = stripped[:100]
            current_document_region = "front_matter"
            in_contents = True
            continue
        if in_contents and page_no == 2:
            continue
        if in_contents and page_no != 2:
            in_contents = False

        previous_document_region = current_document_region
        document_region = detect_document_region(page_no, normalized)
        if current_chunk_lines and document_region != previous_document_region:
            current_document_region = previous_document_region
            flush_chunk()
            current_document_region = document_region

        standalone_ref = detect_standalone_paragraph_ref(stripped)
        if standalone_ref:
            lookahead = next_meaningful_lines(index + 1)
            if len(lookahead) >= 2 and looks_like_short_heading(lookahead[0]) and (
                looks_like_short_heading(lookahead[1]) or detect_paragraph_ref(lookahead[1])
            ):
                flush_chunk()
                continue
            flush_chunk()
            current_para_ref = standalone_ref
            inherit_section_metadata()
            continue

        paragraph_ref = detect_paragraph_ref(stripped)
        if paragraph_ref:
            flush_chunk()
            current_para_ref = paragraph_ref
            heading_ref = detect_heading_ref(stripped)
            if heading_ref and len(stripped) <= PARAGRAPH_HEADING_MAX_CHARS and len(stripped.split()) <= PARAGRAPH_HEADING_MAX_WORDS:
                set_section(heading_ref, stripped, 1.0)
                current_chunk_type = chunk_type_for(stripped, document_region, "heading" if is_heading(stripped) else "paragraph")
            else:
                inherit_section_metadata()
                current_chunk_type = chunk_type_for(stripped, document_region, "paragraph")
            current_chunk_lines.append((page_no, stripped))
            continue

        if is_heading(stripped):
            flush_chunk()
            heading_ref = detect_heading_ref(stripped)
            set_section(heading_ref, stripped, 1.0 if heading_ref else UNNUMBERED_HEADING_CONFIDENCE)
            continue

        if not current_chunk_lines:
            inherit_section_metadata()
            current_chunk_type = chunk_type_for(stripped, document_region, "paragraph")
        current_chunk_lines.append((page_no, stripped))

    flush_chunk()
    return chunks


def load_eba_id_map(processed_dir: Path, manifest_path: Path | None = None) -> dict[str, str]:
    manifest = manifest_path or processed_dir.parent.parent / "pipeline" / "seed_documents.yaml"
    if not manifest.exists():
        return {}

    loaded = cast(dict[str, object], yaml.safe_load(manifest.read_text(encoding="utf-8")) or {})
    documents = cast(list[dict[str, object]], loaded.get("documents", []))
    eba_id_map: dict[str, str] = {}
    for doc in documents:
        if "eba_id" not in doc:
            continue
        raw_eba_id = str(doc["eba_id"])
        canonical_eba_id = canonicalize_eba_id(raw_eba_id)
        eba_id_map[slugify(raw_eba_id)] = canonical_eba_id
        eba_id_map[slugify(canonical_eba_id)] = canonical_eba_id
    return eba_id_map


def paragraphize_all(processed_dir: Path, manifest_path: Path | None = None, *, repair_low_confidence: bool = False) -> None:
    """Run paragraphizer on all processed documents."""
    eba_id_map = load_eba_id_map(processed_dir, manifest_path)

    for doc_dir in sorted(processed_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        pages_file = doc_dir / "pages.json"
        chunks_file = doc_dir / "chunks.json"
        if not pages_file.exists():
            continue

        eba_id = eba_id_map.get(doc_dir.name, canonicalize_eba_id(doc_dir.name))
        print(f"  Paragraphizing {doc_dir.name} ({eba_id})...")
        pages = cast(list[PageData], json.loads(pages_file.read_text(encoding="utf-8")))
        chunks = paragraphize_document(pages, eba_id)
        if repair_low_confidence:
            chunks = cast(
                list[ChunkData],
                repair_low_confidence_chunks(cast(list[dict[str, object]], chunks), log_progress=True, progress_label=eba_id),
            )
        _ = chunks_file.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    -> {len(chunks)} chunks")
