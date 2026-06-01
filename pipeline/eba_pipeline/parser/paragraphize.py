import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import TypedDict, cast

import yaml


PARA_REF_PATTERN = re.compile(r"^\s*(\d+)\.\s+\S")
NUMBERED_PARA_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*\.?)\s+\S")
STANDALONE_REF_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*\.?)\s*$")
HEADING_PATTERN = re.compile(r"^(Title\s+[IVXLC]+|Section\s+\d+|Article\s+\d+|Annex\s+[IVXLC\d]+|Chapter\s+\d+|Contents|Executive Summary|Background and rationale|Background|Rationale|Status of these Guidelines|Status of these guidelines|Reporting requirements|Subject matter(?: and scope of application)?|Definitions|Date of application|Repeal|General considerations|Next steps|Guidelines)$", re.IGNORECASE)
EBA_ID_LINE_PATTERN = re.compile(r"^EBA/[A-Za-z]+/\d{4}/\d+$")


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
    text: str
    text_hash: str
    chunk_type: str
    sequence_no: int


def slugify(eba_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", eba_id.replace("/", "-")).strip("-")


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
    type_initial = {"paragraph": "p", "heading": "h", "table": "t", "annex": "a", "footnote": "f"}.get(chunk_type, "p")
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:8]
    safe_ref = re.sub(r"[^A-Za-z0-9._-]", "_", ref)[:50]
    return f"{eba_id_slug}:{text_hash}:{lang}:{type_initial}:{safe_ref}:p{page_start}:s{sequence_no}"


def detect_paragraph_ref(line: str) -> str | None:
    match = NUMBERED_PARA_PATTERN.match(line)
    if match:
        value = normalize_paragraph_ref(match.group(1))
        if "." not in value and value.isdigit() and int(value) > 999:
            return None
        return value
    match = PARA_REF_PATTERN.match(line)
    if match:
        value = normalize_paragraph_ref(match.group(1))
        if value.isdigit() and int(value) > 999:
            return None
        return value
    return None


def detect_standalone_paragraph_ref(line: str) -> str | None:
    match = STANDALONE_REF_PATTERN.match(line)
    if not match:
        return None
    value = normalize_paragraph_ref(match.group(1))
    if "." not in value and value.isdigit() and int(value) > 999:
        return None
    return value


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def looks_like_short_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
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
    if len(stripped) <= 90 and re.match(r"^\d+(?:\.\d+)*\.\s+[A-Z]", stripped) and len(stripped.split()) <= 8:
        return True
    return False


def detect_heading_ref(line: str) -> str | None:
    match = re.match(r"^(\d+(?:\.\d+)*)\.\s+", line.strip())
    if match:
        return normalize_paragraph_ref(match.group(1))
    return None


def repeated_noise_lines(pages: list[PageData]) -> set[str]:
    counts: Counter[str] = Counter()
    for page in pages:
        page_lines = {
            normalize_line(line)
            for line in page["text"].splitlines()
            if normalize_line(line)
        }
        counts.update(page_lines)

    threshold = max(3, int(len(pages) * 0.35))
    noise: set[str] = set()
    for line, count in counts.items():
        if count < threshold:
            continue
        if re.fullmatch(r"\d+", line):
            noise.add(line)
            continue
        upper_ratio = sum(1 for char in line if char.isupper()) / max(1, sum(1 for char in line if char.isalpha()))
        if EBA_ID_LINE_PATTERN.match(line) or "FINAL REPORT" in line.upper() or upper_ratio >= 0.8:
            noise.add(line)
    return noise


def paragraphize_document(pages: list[PageData], eba_id: str, language: str = "en") -> list[ChunkData]:
    eba_id_slug = slugify(eba_id)
    noise_lines = repeated_noise_lines(pages)
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

    def next_meaningful_lines(start_index: int, limit: int = 3) -> list[str]:
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
            in_contents = True
            continue
        if in_contents and page_no == 2:
            continue
        if in_contents and page_no != 2:
            in_contents = False

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
            continue

        paragraph_ref = detect_paragraph_ref(stripped)
        if paragraph_ref:
            flush_chunk()
            current_para_ref = paragraph_ref
            heading_ref = detect_heading_ref(stripped)
            if heading_ref and len(stripped) <= 100 and len(stripped.split()) <= 10:
                current_section_path = stripped[:100]
                current_ref_context = heading_ref
            current_chunk_lines.append((page_no, stripped))
            continue

        if is_heading(stripped):
            flush_chunk()
            current_section_path = stripped[:100]
            current_ref_context = detect_heading_ref(stripped)
            continue

        current_chunk_lines.append((page_no, stripped))

    flush_chunk()
    return chunks


def load_eba_id_map(processed_dir: Path, manifest_path: Path | None = None) -> dict[str, str]:
    manifest = manifest_path or processed_dir.parent.parent / "pipeline" / "seed_documents.yaml"
    if not manifest.exists():
        return {}

    loaded = cast(dict[str, object], yaml.safe_load(manifest.read_text(encoding="utf-8")) or {})
    documents = cast(list[dict[str, object]], loaded.get("documents", []))
    return {
        slugify(str(doc["eba_id"])): str(doc["eba_id"])
        for doc in documents
        if "eba_id" in doc
    }


def paragraphize_all(processed_dir: Path, manifest_path: Path | None = None) -> None:
    """Run paragraphizer on all processed documents."""
    eba_id_map = load_eba_id_map(processed_dir, manifest_path)

    for doc_dir in sorted(processed_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        pages_file = doc_dir / "pages.json"
        chunks_file = doc_dir / "chunks.json"
        if not pages_file.exists():
            continue

        eba_id = eba_id_map.get(doc_dir.name, doc_dir.name)
        print(f"  Paragraphizing {doc_dir.name} ({eba_id})...")
        pages = cast(list[PageData], json.loads(pages_file.read_text(encoding="utf-8")))
        chunks = paragraphize_document(pages, eba_id)
        _ = chunks_file.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    -> {len(chunks)} chunks")
