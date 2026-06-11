from __future__ import annotations

import json
import re
import time
import warnings
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast

import requests

from eba_pipeline.config import LLM_REPAIR_CONFIDENCE_THRESHOLD, LLM_REPAIR_MODEL, OLLAMA_TIMEOUT_MS, OLLAMA_URL

LOW_CONFIDENCE_THRESHOLD = LLM_REPAIR_CONFIDENCE_THRESHOLD
# Keep repair prompts bounded so one bad document region cannot create oversized Ollama requests.
MAX_SPAN_CHUNKS = 20
SECTION_KEYS = {"section_ref", "title", "level", "parent_section_ref"}
REGION_KEYS = {"start_sequence_no", "end_sequence_no", "document_region"}
TOP_LEVEL_KEYS = {"sections", "regions"}
DOCUMENT_REGIONS = {"front_matter", "body", "annex", "back_matter", "consultation_feedback"}
SECTION_REF_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")
DEFAULT_TIMEOUT_SECONDS: float = OLLAMA_TIMEOUT_MS / 1000

ChunkMapping = Mapping[str, object]
MutableChunk = dict[str, object]


class RepairSection(TypedDict):
    section_ref: str
    title: str
    level: int
    parent_section_ref: str | None


class RepairRegion(TypedDict):
    start_sequence_no: int
    end_sequence_no: int
    document_region: str


class RepairPayload(TypedDict):
    sections: list[RepairSection]
    regions: list[RepairRegion]


def repair_low_confidence_chunks(
    chunks: list[MutableChunk],
    *,
    ollama_url: str = OLLAMA_URL,
    model: str = LLM_REPAIR_MODEL,
    confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
    log_progress: bool = False,
    progress_label: str | None = None,
) -> list[MutableChunk]:
    repaired = [dict(chunk) for chunk in chunks]
    spans = _candidate_spans(repaired, confidence_threshold=confidence_threshold)
    if log_progress:
        span_chunks = sum(len(span) for span in spans)
        label = f" for {progress_label}" if progress_label else ""
        print(
            f"    LLM repair{label}: {len(spans)} low-confidence spans, "
            + f"{span_chunks} chunks below {confidence_threshold:.2f}; model={model}; timeout={DEFAULT_TIMEOUT_SECONDS:.1f}s"
        )
    for index, span in enumerate(spans, start=1):
        if log_progress:
            print(f"      [{index}/{len(spans)}] {_span_summary(span)}")
        start_time = time.monotonic()
        try:
            payload = _request_repair(span, ollama_url=ollama_url, model=model)
            before = _span_confidence_summary(span)
            _apply_repair(repaired, span, payload, confidence_threshold=confidence_threshold)
            if log_progress:
                after_span = [_chunk_by_sequence(repaired, _int_field(chunk, "sequence_no")) for chunk in span]
                elapsed = time.monotonic() - start_time
                print(
                    f"        ok in {elapsed:.1f}s: confidence {before} -> {_span_confidence_summary(after_span)}; "
                    + f"sections={len(payload['sections'])}; regions={len(payload['regions'])}"
                )
        except (requests.RequestException, RepairValidationError, ValueError, KeyError, TypeError) as error:
            if log_progress:
                elapsed = time.monotonic() - start_time
                print(f"        skipped in {elapsed:.1f}s: {type(error).__name__}: {error}; confidence stays {_span_confidence_summary(span)}")
            warnings.warn(f"Skipping LLM repair for low-confidence span: {error}", RuntimeWarning, stacklevel=2)
    return repaired


class RepairValidationError(ValueError):
    pass


def _candidate_spans(chunks: Sequence[ChunkMapping], *, confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD) -> list[list[ChunkMapping]]:
    spans: list[list[ChunkMapping]] = []
    current: list[ChunkMapping] = []
    for chunk in sorted(chunks, key=lambda item: _int_field(item, "sequence_no", default=0)):
        if _float_field(chunk, "metadata_confidence", default=1.0) < confidence_threshold:
            current.append(chunk)
            if len(current) >= MAX_SPAN_CHUNKS:
                spans.append(current)
                current = []
            continue
        if current:
            spans.append(current)
            current = []
    if current:
        spans.append(current)
    return spans


def _request_repair(span: Sequence[ChunkMapping], *, ollama_url: str, model: str) -> RepairPayload:
    endpoint = ollama_url.rstrip("/") + "/api/generate"
    source_text = _source_text(span)
    response = requests.post(
        endpoint,
        json={
            "model": model,
            "stream": False,
            "format": "json",
            "prompt": _prompt_for_span(span),
        },
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RepairValidationError(f"Ollama repair request returned HTTP {response.status_code}: {response.text[:200]}")
    payload = cast(object, response.json())
    if not isinstance(payload, Mapping):
        raise RepairValidationError("Ollama response did not contain a JSON response string")
    response_payload = cast(ChunkMapping, payload)
    response_text = response_payload.get("response")
    if not isinstance(response_text, str):
        raise RepairValidationError("Ollama response did not contain a JSON response string")
    try:
        return validate_repair_json(response_text, span, source_text=source_text)
    except RepairValidationError as error:
        raise RepairValidationError(f"{error}; response excerpt: {_response_excerpt(response_text)}") from error


def _prompt_for_span(span: Sequence[ChunkMapping]) -> str:
    lines: list[str] = []
    for chunk in span:
        sequence_no = _int_field(chunk, "sequence_no")
        text = str(chunk.get("text", ""))
        lines.append(f"[{sequence_no}] {text}")
    joined = "\n\n".join(lines)
    return (
        "You repair parser metadata for EBA regulatory text. Return one JSON object only: no markdown, no prose, no code fences. "
        "The object must have exactly two top-level keys: sections and regions. "
        "Use this exact schema and no other keys: "
        "{\"sections\":[{\"section_ref\":\"4.2\",\"title\":\"Exact title from source\",\"level\":2,\"parent_section_ref\":\"4\"}],"
        "\"regions\":[{\"start_sequence_no\":1,\"end_sequence_no\":1,\"document_region\":\"body\"}]}. "
        "For sections: use only numeric section_ref values that appear in the source text, such as 4 or 4.2; never use Article, Annex, Roman numerals, or invented refs. "
        "title must be copied verbatim from the source text and must not include the section_ref. "
        "level must equal the number of numeric components in section_ref; parent_section_ref must be the numeric prefix or null for top-level refs. "
        "If the source span does not contain a valid numeric section ref and exact title, return sections as an empty array. "
        "For regions: use only sequence numbers shown in square brackets in the source span. "
        "Allowed document_region values are exactly front_matter, body, annex, back_matter, consultation_feedback. "
        "Do not rewrite source text. Source span:\n"
        f"{joined}"
    )


def _response_excerpt(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:240]


def validate_repair_json(raw_json: str, span: Sequence[ChunkMapping], *, source_text: str | None = None) -> RepairPayload:
    try:
        parsed = cast(object, json.loads(raw_json))
    except json.JSONDecodeError as error:
        raise RepairValidationError(f"invalid JSON: {error}") from error
    if not isinstance(parsed, Mapping):
        raise RepairValidationError("repair JSON must be an object")
    parsed_payload = cast(ChunkMapping, parsed)
    if set(parsed_payload.keys()) != TOP_LEVEL_KEYS:
        raise RepairValidationError("repair JSON must contain exactly sections and regions")

    source = normalize_source_text(source_text if source_text is not None else _source_text(span))
    sequence_numbers = {_int_field(chunk, "sequence_no") for chunk in span}
    sections = _validate_sections(parsed_payload["sections"], source)
    regions = _validate_regions(parsed_payload["regions"], sequence_numbers)
    return {"sections": sections, "regions": regions}


def _validate_sections(value: object, source_text: str) -> list[RepairSection]:
    if not isinstance(value, list):
        raise RepairValidationError("sections must be a list")
    sections: list[RepairSection] = []
    seen_refs: set[str] = set()
    for item in cast(list[object], value):
        if not isinstance(item, Mapping):
            raise RepairValidationError("section entries must be objects")
        section = cast(ChunkMapping, item)
        if set(section.keys()) != SECTION_KEYS:
            raise RepairValidationError("section entries contain missing or extra fields")
        section_ref = section["section_ref"]
        title = section["title"]
        level = section["level"]
        parent = section["parent_section_ref"]
        if not isinstance(section_ref, str) or not SECTION_REF_PATTERN.fullmatch(section_ref):
            raise RepairValidationError("section_ref must be a dotted numeric reference")
        if section_ref in seen_refs:
            raise RepairValidationError(f"duplicate section_ref {section_ref}")
        if not isinstance(title, str) or not title.strip():
            raise RepairValidationError("title must be a non-empty string")
        if not isinstance(level, int) or level != len(section_ref.split(".")):
            raise RepairValidationError("section level must match section_ref depth")
        expected_parent = section_ref.rsplit(".", 1)[0] if "." in section_ref else None
        if parent != expected_parent:
            raise RepairValidationError("parent_section_ref must match section_ref hierarchy")
        normalized_title = normalize_source_text(title)
        if normalize_source_text(section_ref) not in source_text or normalized_title not in source_text:
            raise RepairValidationError("section ref/title not found in source text")
        seen_refs.add(section_ref)
        sections.append({"section_ref": section_ref, "title": title, "level": level, "parent_section_ref": cast(str | None, parent)})
    return sections


def _validate_regions(value: object, sequence_numbers: set[int]) -> list[RepairRegion]:
    if not isinstance(value, list):
        raise RepairValidationError("regions must be a list")
    regions: list[RepairRegion] = []
    for item in cast(list[object], value):
        if not isinstance(item, Mapping):
            raise RepairValidationError("region entries must be objects")
        repair_region = cast(ChunkMapping, item)
        if set(repair_region.keys()) != REGION_KEYS:
            raise RepairValidationError("region entries contain missing or extra fields")
        start = repair_region["start_sequence_no"]
        end = repair_region["end_sequence_no"]
        region = repair_region["document_region"]
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            raise RepairValidationError("region sequence range is invalid")
        if start not in sequence_numbers or end not in sequence_numbers:
            raise RepairValidationError("region sequence range must stay within source span")
        covered = set(range(start, end + 1))
        if not covered.issubset(sequence_numbers):
            raise RepairValidationError("region sequence range crosses chunks outside source span")
        if not isinstance(region, str) or region not in DOCUMENT_REGIONS:
            raise RepairValidationError("document_region is invalid")
        regions.append({"start_sequence_no": start, "end_sequence_no": end, "document_region": region})
    return regions


def _apply_repair(chunks: list[MutableChunk], span: Sequence[ChunkMapping], payload: RepairPayload, *, confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD) -> None:
    by_sequence = {_int_field(chunk, "sequence_no"): chunk for chunk in chunks}
    span_sequences = {_int_field(chunk, "sequence_no") for chunk in span}
    sections = payload["sections"]
    selected_section = sections[-1] if sections else None
    for region in payload["regions"]:
        for sequence_no in range(region["start_sequence_no"], region["end_sequence_no"] + 1):
            if sequence_no not in span_sequences:
                continue
            chunk = by_sequence[sequence_no]
            chunk["document_region"] = region["document_region"]
            chunk["metadata_source"] = "llm_repair"
            chunk["metadata_confidence"] = min(_float_field(chunk, "metadata_confidence", default=confidence_threshold), confidence_threshold)
            if selected_section:
                chunk["section_ref"] = selected_section["section_ref"]
                chunk["section_title"] = selected_section["title"]
                chunk["section_level"] = selected_section["level"]
                chunk["parent_section_ref"] = selected_section["parent_section_ref"]
                chunk["section_path"] = selected_section["title"][:160]


def _source_text(span: Sequence[ChunkMapping]) -> str:
    return "\n".join(str(chunk.get("text", "")) for chunk in span)


def _span_summary(span: Sequence[ChunkMapping]) -> str:
    sequences = [_int_field(chunk, "sequence_no") for chunk in span]
    return f"seq={min(sequences)}-{max(sequences)} chunks={len(span)} confidence={_span_confidence_summary(span)}"


def _span_confidence_summary(span: Sequence[ChunkMapping]) -> str:
    confidences = [_float_field(chunk, "metadata_confidence", default=1.0) for chunk in span]
    average = sum(confidences) / len(confidences)
    return f"min={min(confidences):.2f} avg={average:.2f} max={max(confidences):.2f}"


def _chunk_by_sequence(chunks: Sequence[MutableChunk], sequence_no: int) -> MutableChunk:
    for chunk in chunks:
        if _int_field(chunk, "sequence_no") == sequence_no:
            return chunk
    raise RepairValidationError(f"sequence_no {sequence_no} was not found after repair")


def _int_field(mapping: ChunkMapping, field: str, *, default: int | None = None) -> int:
    value = mapping.get(field, default)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise RepairValidationError(f"{field} must be an integer")


def _float_field(mapping: ChunkMapping, field: str, *, default: float) -> float:
    value = mapping.get(field, default)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise RepairValidationError(f"{field} must be numeric")


def normalize_source_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()
