"""PDF downloader for EBA regulatory documents."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

import requests
import yaml

from eba_pipeline.config import ALLOWED_DOMAIN, DOWNLOAD_RETRIES


def _make_eba_id_slug(eba_id: str) -> str:
    """Convert an EBA ID to a filesystem-safe slug.

    Example: ``EBA/GL/2021/02`` → ``EBA-GL-2021-02``
    """
    slug = eba_id.replace("/", "-")
    slug = re.sub(r"[^\w\-]", "", slug)
    return slug


def _download_with_retries(url: str, retries: int = DOWNLOAD_RETRIES) -> bytes:
    """Download *url* with exponential backoff.

    Raises :exc:`RuntimeError` if all attempts fail.
    """
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url, timeout=60, headers={"User-Agent": "EBA-Pipeline/0.1.0"}
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            if attempt == retries:
                raise RuntimeError(f"Failed to download {url} after {retries} attempts: {exc}")
            wait = 2 ** (attempt - 1)
            print(f"  Retry {attempt}/{retries} for {url} in {wait}s…")
            time.sleep(wait)
    raise RuntimeError(f"Failed to download {url} after {retries} attempts")


def download_documents(manifest_path: str, output_dir: str, continue_on_error: bool = False) -> list[dict[str, Any]]:
    """Download PDFs listed in *manifest_path* into *output_dir*.

    Returns a list of document dictionaries enriched with ``file_sha256``,
    ``downloaded_at``, and ``file_path``.
    """
    manifest_path_obj = Path(manifest_path)
    output_dir_obj = Path(output_dir)
    output_dir_obj.mkdir(parents=True, exist_ok=True)

    with manifest_path_obj.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    documents = data.get("documents", [])
    downloaded: list[dict[str, Any]] = []

    total = len(documents)
    for idx, doc in enumerate(documents, start=1):
        eba_id = doc["eba_id"]
        file_url = doc["file_url"]
        language = doc.get("language", "en")

        print(f"[{idx}/{total}] {eba_id} — {file_url}")

        if ALLOWED_DOMAIN not in file_url:
            raise ValueError(f"URL not on allowlist ({ALLOWED_DOMAIN}): {file_url}")

        eba_id_slug = _make_eba_id_slug(eba_id)
        dest_dir = output_dir_obj / eba_id_slug / language
        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            content = _download_with_retries(file_url)
        except Exception as error:
            if not continue_on_error:
                raise
            print(f"  [download-failed] {error}")
            continue
        file_sha256 = hashlib.sha256(content).hexdigest()
        dest_file = dest_dir / f"{file_sha256}.pdf"

        if dest_file.exists():
            print(f"  Already exists: {dest_file}")
        else:
            dest_file.write_bytes(content)
            print(f"  Saved ({len(content):,} bytes) → {dest_file}")

        downloaded.append({
            **doc,
            "file_sha256": file_sha256,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "file_path": str(dest_file.relative_to(output_dir_obj.parent)),
        })

    return downloaded
