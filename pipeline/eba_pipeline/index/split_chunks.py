from __future__ import annotations

import hashlib
import re
from typing import Any

MAX_CHUNK_SIZE = 4000

_BOUNDARY_PATTERNS = re.compile(
    r"^(?:"
    r"\s*$"                       # blank line
    r"|(?:\d+\.\s)"               # numbered list: "1. "
    r"|(?:[a-z]\)\s)"             # lettered list: "a) "
    r"|(?:[ivxlcdm]+\)\s)"        # roman numeral: "i) "
    r"|(?:[-–•*]\s)"              # bullet: "- ", "• "
    r"|(?:\(\d+\)\s)"             # parenthesised number: "(1) "
    r"|(?:\([a-z]\)\s)"           # parenthesised letter: "(a) "
    r")",
    re.MULTILINE,
)


def _split_at_boundaries(text: str, max_size: int) -> list[str]:
    if len(text) <= max_size:
        return [text]

    lines = text.splitlines(keepends=True)
    segments: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line)

        if current and (current_len + line_len > max_size):
            stripped = line.strip()
            is_boundary = bool(_BOUNDARY_PATTERNS.match(stripped)) or stripped == ""

            if is_boundary or current_len >= max_size // 2:
                segment = "".join(current).strip()
                if segment:
                    segments.append(segment)
                current = [line]
                current_len = line_len
                continue

        current.append(line)
        current_len += line_len

    if current:
        segment = "".join(current).strip()
        if segment:
            segments.append(segment)

    final: list[str] = []
    for seg in segments:
        if len(seg) <= max_size:
            final.append(seg)
        else:
            sub_lines = seg.splitlines(keepends=True)
            if len(sub_lines) > 1:
                final.extend(_split_at_boundaries(seg, max_size))
            else:
                pos = 0
                while pos < len(seg):
                    final.append(seg[pos : pos + max_size])
                    pos += max_size

    return final if final else [text]


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_mega_chunks(
    chunks: list[dict[str, Any]],
    max_size: int = MAX_CHUNK_SIZE,
) -> list[dict[str, Any]]:
    """
    Split chunks with len(text) > max_size into sub-chunks at natural boundaries.

    Chunks <= max_size pass through unchanged with their original IDs.
    Oversized chunks are replaced by sub-chunks with IDs {parent_id}:sub1,
    {parent_id}:sub2, etc., inheriting all parent metadata fields.
    After splitting, all chunks in the returned list receive dense consecutive
    sequence_no values (1-based) matching their position, so ORDER BY sequence_no
    and BETWEEN-based context windows reflect true reading order.
    """
    expanded: list[dict[str, Any]] = []

    for chunk in chunks:
        text = str(chunk.get("text", ""))
        if len(text) <= max_size:
            expanded.append(chunk)
            continue

        parent_id = str(chunk["chunk_id"])
        pieces = _split_at_boundaries(text, max_size)

        if len(pieces) == 1:
            expanded.append(chunk)
            continue

        for idx, piece in enumerate(pieces, start=1):
            sub_chunk: dict[str, Any] = {
                **chunk,
                "chunk_id": f"{parent_id}:sub{idx}",
                "text": piece,
                "text_hash": _text_hash(piece),
            }
            expanded.append(sub_chunk)

    for position, chunk in enumerate(expanded, start=1):
        chunk["sequence_no"] = position

    return expanded
