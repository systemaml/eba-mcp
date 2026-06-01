"""Relationship extractor — parses seed YAML notes fields for document relationships."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


# Patterns: (regex, relationship_type)
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"amend(?:ing|s)\b.*?(EBA/[\w]+/\d{4}/\d+)", re.IGNORECASE), "amends"),
    (re.compile(r"repeal(?:ing|s)\b.*?(EBA/[\w]+/\d{4}/\d+)", re.IGNORECASE), "repeals"),
    (re.compile(r"replac(?:ing|es)\b.*?(EBA/[\w]+/\d{4}/\d+)", re.IGNORECASE), "replaces"),
    (re.compile(r"supersed(?:ing|es)\b.*?(EBA/[\w]+/\d{4}/\d+)", re.IGNORECASE), "supersedes"),
]


def _parse_seed(seed_yaml_path: str) -> list[dict[str, Any]]:
    import yaml

    text = Path(seed_yaml_path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return list(data.get("documents", []))


def _extract_from_notes(source_eba_id: str, notes: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for pattern, rel_type in _PATTERNS:
        for match in pattern.finditer(notes):
            target = match.group(1)
            if target == source_eba_id:
                print(
                    f"[relationships] skipping self-reference: {source_eba_id} -> {target}",
                    file=sys.stderr,
                )
                continue
            results.append(
                {
                    "source_eba_id": source_eba_id,
                    "target_eba_id": target,
                    "relationship_type": rel_type,
                }
            )
    return results


def _load_overrides(override_yaml_path: str | None) -> list[dict[str, str]]:
    if not override_yaml_path:
        return []
    path = Path(override_yaml_path)
    if not path.exists():
        return []
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("relationships") or []
    overrides: list[dict[str, str]] = []
    for entry in raw:
        overrides.append(
            {
                "source_eba_id": str(entry["source"]),
                "target_eba_id": str(entry["target"]),
                "relationship_type": str(entry["type"]),
            }
        )
    return overrides


def extract_relationships(
    seed_yaml_path: str,
    override_yaml_path: str | None = None,
) -> list[dict[str, str]]:
    """Extract document relationships from seed YAML notes and optional override YAML.

    Returns a list of dicts with keys: source_eba_id, target_eba_id, relationship_type.
    Override entries for the same (source, target) pair replace auto-detected ones.
    """
    documents = _parse_seed(seed_yaml_path)
    known_ids = {str(doc["eba_id"]) for doc in documents}

    detected: list[dict[str, str]] = []
    for doc in documents:
        source_id = str(doc.get("eba_id", ""))
        notes = str(doc.get("notes") or "")
        if not source_id or not notes:
            continue
        rels = _extract_from_notes(source_id, notes)
        for rel in rels:
            target = rel["target_eba_id"]
            if target not in known_ids:
                print(
                    f"[relationships] warning: unknown target eba_id '{target}' "
                    f"(source={source_id}); inserting anyway",
                    file=sys.stderr,
                )
            detected.append(rel)

    overrides = _load_overrides(override_yaml_path)

    result_map: dict[tuple[str, str], dict[str, str]] = {}
    for rel in detected:
        key = (rel["source_eba_id"], rel["target_eba_id"])
        result_map[key] = rel
    for rel in overrides:
        key = (rel["source_eba_id"], rel["target_eba_id"])
        result_map[key] = rel

    return list(result_map.values())
