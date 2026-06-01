"""Discover official EBA PDF publications and emit seed manifests."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml


BASE_URL = "https://www.eba.europa.eu"
PUBLICATIONS_URL = f"{BASE_URL}/publications-and-media/publications"
USER_AGENT = "EBA-Pipeline/0.1.0"

DOCUMENT_TYPES: dict[str, tuple[str, str]] = {
    "250": ("guidelines", "EBA guidelines"),
    "244": ("consultation-paper", "EBA consultation paper"),
    "257": ("report", "EBA report"),
    "252": ("opinion", "EBA opinion"),
    "248": ("rts", "EBA regulatory technical standard"),
    "247": ("its", "EBA implementing technical standard"),
    "245": ("decision", "EBA decision"),
    "241": ("annual-report", "EBA annual report"),
}

DISCOVERY_PROFILES: dict[str, list[str]] = {
    "current-applicable": ["250", "248", "247"],
    "broad": list(DOCUMENT_TYPES),
}

CURRENT_APPLICABLE_EXCLUDE_RE = re.compile(
    r"\b(consultation|consultative|proposed|proposal|discussion\s+paper|call\s+for\s+advice|"
    r"response\s+to|feedback|track\s*changes?|tracked\s+changes?|redline|mapping\s+report|"
    r"annex|instructions?|templates?|workbook|presentation|slides?|errata)\b",
    re.I,
)
DRAFT_EXCLUDE_RE = re.compile(r"\bdraft\b", re.I)
FINAL_DRAFT_RE = re.compile(r"\bfinal\b.*?\bdraft\b|\bfinal\s+report\s+on\s+draft\b", re.I)
AMENDING_ONLY_RE = re.compile(r"\b(amending|amended|amends|amendment)\b", re.I)
CONSOLIDATED_RE = re.compile(r"\bconsolidated\b", re.I)

CURRENT_APPLICABLE_INCLUDE_RE = re.compile(
    r"\b(guidelines?|recommendations?|regulatory\s+technical\s+standards?|implementing\s+technical\s+standards?|RTS|ITS)\b",
    re.I,
)

PDF_LINK_RE = re.compile(r'<a\b[^>]*href="([^"]+\.pdf(?:\?[^"]*)?)"[^>]*>(.*?)</a>', re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
EBA_ID_RE = re.compile(
    r"\b((?:EBA|JC)[-/](?:GL|CP|Op|OP|REP|RTS|ITS|BS|DC|DP|REC)[-/]\d{4}[-/]\d{1,3})\b",
    re.I,
)
LANG_SUFFIX_RE = re.compile(r"(?:^|[-_\s])(DE|FR|ES|IT|PL|PT|NL|DA|SV|FI|EL|BG|CS|ET|HU|LT|LV|MT|RO|SK|SL)(?:[-_\s.]|$)", re.I)


@dataclass(frozen=True)
class DiscoveredDocument:
    eba_id: str
    title: str
    document_type: str
    topic: str
    file_url: str
    publication_url: str
    language: str
    published_at: str
    expected_difficulty: str
    publication_status: str
    applicability_status: str
    notes: str

    def as_manifest_row(self) -> dict[str, str]:
        return {
            "eba_id": self.eba_id,
            "title": self.title,
            "document_type": self.document_type,
            "topic": self.topic,
            "file_url": self.file_url,
            "publication_url": self.publication_url,
            "language": self.language,
            "published_at": self.published_at,
            "expected_difficulty": self.expected_difficulty,
            "publication_status": self.publication_status,
            "applicability_status": self.applicability_status,
            "notes": self.notes,
        }


def clean_html_text(value: str) -> str:
    text = TAG_RE.sub(" ", value)
    return " ".join(unescape(text).split())


def is_probably_english_pdf(url: str) -> bool:
    path = url.split("?", 1)[0]
    if not path.lower().endswith(".pdf"):
        return False
    filename = unescape(Path(path).name)
    return LANG_SUFFIX_RE.search(filename) is None


def infer_published_at(url: str) -> str:
    match = re.search(r"/sites/default/files/(\d{4})-(\d{2})/", url)
    if match:
        return f"{match.group(1)}-{match.group(2)}-01"
    match = re.search(r"/Publications/[^/]+/(\d{4})/", url)
    if match:
        return f"{match.group(1)}-01-01"
    return ""


def infer_eba_id(title: str, url: str, document_type: str, sequence: int) -> str:
    text = f"{title} {unescape(url)}"
    match = EBA_ID_RE.search(text)
    if match:
        return match.group(1).replace("-", "/")
    prefix = {
        "guidelines": "GL",
        "consultation-paper": "CP",
        "report": "REP",
        "opinion": "Op",
        "rts": "RTS",
        "its": "ITS",
        "decision": "DC",
        "annual-report": "AR",
    }.get(document_type, "DOC")
    year_match = re.search(r"/(20\d{2})-", url)
    year = year_match.group(1) if year_match else "0000"
    return f"EBA/LARGE-{prefix}/{year}/{sequence:04d}"


def fetch_page(session: requests.Session, document_type_id: str, page: int) -> str:
    response = session.get(
        PUBLICATIONS_URL,
        params={"text": "", "document_type": document_type_id, "media_topics": "All", "page": page},
        timeout=45,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def is_current_applicable_candidate(title: str, url: str, document_type: str) -> bool:
    """First-pass production filter for current/applicable regulatory material.

    This deliberately errs on the conservative side: if a PDF looks like a consultation,
    draft/proposal, track-changes file, annex/instruction, or mapping/support artefact,
    it is excluded from the default production corpus. Legal lifecycle status still needs
    relationship/status curation later; this is not a complete supersession engine.
    """
    if document_type not in {"guidelines", "rts", "its"}:
        return False
    text = f"{title} {unescape(url)}".replace("%20", " ").replace("_", " ").replace("-", " ")
    if CURRENT_APPLICABLE_EXCLUDE_RE.search(text):
        return False
    if DRAFT_EXCLUDE_RE.search(text) and not FINAL_DRAFT_RE.search(text):
        return False
    if AMENDING_ONLY_RE.search(text) and not CONSOLIDATED_RE.search(text):
        return False
    return bool(CURRENT_APPLICABLE_INCLUDE_RE.search(text))


def discover_publication_pdfs(
    limit: int,
    pages_per_type: int = 20,
    sleep_seconds: float = 0.5,
    document_type_ids: list[str] | None = None,
    profile: str = "current-applicable",
) -> list[DiscoveredDocument]:
    session = requests.Session()
    if profile not in DISCOVERY_PROFILES:
        raise ValueError(f"Unknown discovery profile {profile!r}; expected one of {sorted(DISCOVERY_PROFILES)}")
    selected_type_ids = document_type_ids or DISCOVERY_PROFILES[profile]
    seen_urls: set[str] = set()
    documents: list[DiscoveredDocument] = []

    for document_type_id in selected_type_ids:
        document_type, topic = DOCUMENT_TYPES[document_type_id]
        for page in range(pages_per_type):
            html = fetch_page(session, document_type_id, page)
            matches = list(PDF_LINK_RE.finditer(html))
            if not matches:
                break
            found_on_page = 0
            for match in matches:
                file_url = urljoin(BASE_URL, unescape(match.group(1))).split("?", 1)[0]
                if file_url in seen_urls or not is_probably_english_pdf(file_url):
                    continue
                anchor_text = clean_html_text(match.group(2))
                if not anchor_text or anchor_text.lower() == "download document":
                    anchor_text = unescape(Path(file_url).stem).replace("%20", " ").replace("-", " ")
                if profile == "current-applicable" and not is_current_applicable_candidate(
                    anchor_text, file_url, document_type
                ):
                    continue
                seen_urls.add(file_url)
                sequence = len(documents) + 1
                eba_id = infer_eba_id(anchor_text, file_url, document_type, sequence)
                documents.append(
                    DiscoveredDocument(
                        eba_id=eba_id,
                        title=anchor_text[:500],
                        document_type=document_type,
                        topic=topic,
                        file_url=file_url,
                        publication_url=str(response_url(document_type_id, page)),
                        language="en",
                        published_at=infer_published_at(file_url),
                        expected_difficulty="medium",
                        publication_status="final",
                        applicability_status="applicable",
                        notes=(
                            "Discovered from official EBA publications listing; English inferred from PDF filename/path; "
                            f"document_type_facet={document_type_id}; discovery_profile={profile}."
                        ),
                    )
                )
                found_on_page += 1
                if len(documents) >= limit:
                    return documents
            if found_on_page == 0 and page > 2:
                break
            time.sleep(sleep_seconds)

    return documents


def response_url(document_type_id: str, page: int) -> str:
    return f"{PUBLICATIONS_URL}?text=&document_type={document_type_id}&media_topics=All&page={page}"


def write_seed_manifest(documents: list[DiscoveredDocument], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [doc.as_manifest_row() for doc in documents]

    seen_ids: dict[str, int] = {}
    for row in rows:
        eba_id = row["eba_id"]
        seen_ids[eba_id] = seen_ids.get(eba_id, 0) + 1
        if seen_ids[eba_id] > 1:
            row["eba_id"] = f"{eba_id}/DUP{seen_ids[eba_id]}"

    output_path.write_text(yaml.safe_dump({"documents": rows}, sort_keys=False, allow_unicode=True), encoding="utf-8")
