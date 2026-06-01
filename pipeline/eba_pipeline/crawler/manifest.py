"""Manifest builder for downloaded EBA documents."""

from __future__ import annotations

import json
from pathlib import Path

from eba_pipeline.config import DATA_DIR

MANIFEST_PATH = DATA_DIR / "manifest.jsonl"


def _load_existing_manifest(path: Path) -> set[str]:
    """Return the set of ``file_sha256`` values already recorded."""
    seen: set[str] = set()
    if not path.exists():
        return seen
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add(entry.get("file_sha256", ""))
    return seen


def build_manifest(output_dir: str, documents: list[dict] | None = None) -> Path:
    """Append new document entries to ``data/manifest.jsonl``.

    *documents* should be the list returned by
    :func:`eba_pipeline.crawler.downloader.download_documents`.
    If omitted, the function scans *output_dir* for PDFs and builds
    minimal entries (metadata will be incomplete).
    """
    output_dir_obj = Path(output_dir)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    seen = _load_existing_manifest(MANIFEST_PATH)

    entries: list[dict] = []

    if documents:
        for doc in documents:
            sha = doc.get("file_sha256", "")
            if sha and sha not in seen:
                entries.append({
                    "eba_id": doc["eba_id"],
                    "title": doc["title"],
                    "document_type": doc["document_type"],
                    "topic": doc["topic"],
                    "file_url": doc["file_url"],
                    "language": doc.get("language", "en"),
                    "published_at": doc["published_at"],
                    "file_sha256": sha,
                    "downloaded_at": doc["downloaded_at"],
                    "file_path": doc["file_path"],
                })
                seen.add(sha)
    else:
        for pdf_file in output_dir_obj.rglob("*.pdf"):
            rel_path = str(pdf_file.relative_to(output_dir_obj.parent))
            entries.append({
                "eba_id": "",
                "title": "",
                "document_type": "",
                "topic": "",
                "file_url": "",
                "language": pdf_file.parent.name,
                "published_at": "",
                "file_sha256": pdf_file.stem,
                "downloaded_at": "",
                "file_path": rel_path,
            })

    if entries:
        with MANIFEST_PATH.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"Manifest updated: {MANIFEST_PATH} (+{len(entries)} entries)")
    else:
        print("Manifest unchanged (no new downloads).")

    return MANIFEST_PATH
