import json
import subprocess
from pathlib import Path
from collections import Counter
from typing import Optional


def extract_pages_pymupdf(pdf_path: Path) -> list:
    import pymupdf
    pages = []
    try:
        doc = pymupdf.open(str(pdf_path))
        for i, page in enumerate(doc):
            text = page.get_text()
            pages.append({
                "page_no": i + 1,
                "text": text,
                "extraction_method": "pymupdf4llm",
                "char_count": len(text)
            })
        doc.close()
    except Exception as e:
        raise RuntimeError(f"pymupdf failed: {e}")
    return pages


def extract_pages_pdftotext(pdf_path: Path) -> list:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr}")
    raw_pages = result.stdout.split("\f")
    return [
        {
            "page_no": i + 1,
            "text": p.strip(),
            "extraction_method": "pdftotext",
            "char_count": len(p.strip())
        }
        for i, p in enumerate(raw_pages) if p.strip()
    ]


def strip_headers_footers(pages: list) -> list:
    if not pages:
        return pages
    candidates = []
    for p in pages:
        lines = p["text"].splitlines()
        if lines:
            candidates.append(lines[0].strip())
        if len(lines) > 1:
            candidates.append(lines[-1].strip())
    counts = Counter(candidates)
    threshold = len(pages) * 0.5
    repeated = {t for t, c in counts.items() if c > threshold and 0 < len(t) <= 100}
    if not repeated:
        return pages
    cleaned = []
    for p in pages:
        lines = [line for line in p["text"].splitlines() if line.strip() not in repeated]
        updated = dict(p)
        updated["text"] = "\n".join(lines)
        updated["char_count"] = len(updated["text"])
        cleaned.append(updated)
    return cleaned


def extract_document(pdf_path: Path, eba_id_slug: str, output_dir: Path) -> Optional[Path]:
    out_dir = output_dir / eba_id_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "pages.json"
    if out_file.exists():
        print(f"  [skip] {eba_id_slug} already extracted")
        return out_file
    pages = None
    try:
        pages = extract_pages_pymupdf(pdf_path)
        print(f"  [pymupdf] {eba_id_slug}: {len(pages)} pages")
    except Exception as e:
        print(f"  [warn] pymupdf failed for {eba_id_slug}: {e}, trying pdftotext")
        try:
            pages = extract_pages_pdftotext(pdf_path)
            print(f"  [pdftotext] {eba_id_slug}: {len(pages)} pages")
        except Exception as e2:
            print(f"  [error] Both extractors failed for {eba_id_slug}: {e2}")
            return None
    if pages:
        pages = strip_headers_footers(pages)
        out_file.write_text(json.dumps(pages, ensure_ascii=False, indent=2))
    return out_file


def extract_all_documents(input_dir: Path, output_dir: Path) -> None:
    for eba_dir in sorted(input_dir.iterdir()):
        if not eba_dir.is_dir():
            continue
        eba_id_slug = eba_dir.name
        for lang_dir in eba_dir.iterdir():
            if not lang_dir.is_dir():
                continue
            for pdf_file in sorted(lang_dir.glob("*.pdf")):
                print(f"Extracting {eba_id_slug}...")
                extract_document(pdf_file, eba_id_slug, output_dir)
