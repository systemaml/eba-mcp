# Limitations and Legal Notices

This document describes the known limitations, scope boundaries, and legal notices for the EBA MCP project.

---

## Product Corpus Scope

The intended production corpus is the **EBA Current Applicable Corpus**. The MCP server should prioritize documents lawyers and compliance professionals can rely on for current obligations and supervisory expectations.

### Included by Default

- Current/applicable EBA Guidelines.
- Current/applicable EBA Recommendations.
- Current/applicable RTS/ITS or standards-style publications when they are not merely draft/proposed material.
- English-language canonical PDFs from `eba.europa.eu`.

### Excluded by Default

- Consultation papers.
- Draft/proposed publications.
- Track-changes files.
- Annex-only, instruction-only, mapping-only, and template-like files unless explicitly selected as canonical support material.
- Superseded, repealed, withdrawn, deprecated, or archival documents.
- Non-PDF artefacts such as XLS/XLSM/ZIP/ACCDB/DOCX files.

Archive/proposed corpora can still be built for research, but must use an explicit non-default discovery profile.

---

## Current Corpus State

The current production corpus (`eba-corpus.db`, versioned by GitHub Release tag) contains **346 current/applicable EBA documents** with **42,146 chunks** and **42,146 semantic embedding vectors** (`nomic-embed-text`, dim 768). Hybrid retrieval (FTS5 + sqlite-vec) is active when the DB includes vectors and Ollama is running. See [README.md](../README.md#production-corpus) for eval results and versioning policy.

---

## POC Scope

This is a **Proof of Concept** (POC) implementation that has since grown beyond initial POC scope. The following notes describe the original scope and current state:

- **Original 9 EBA AML/CFT documents** — the small seed corpus was only used for initial development; the current production corpus covers 346 current/applicable EBA documents
- **English only** — no multi-language support; EBA publications in other languages are not processed
- **Hybrid retrieval available** — FTS5 keyword search is always active; sqlite-vec semantic search is available when the DB includes vectors (`nomic-embed-text` 768-dim) and Ollama is running locally; `EBA_SEARCH_MODE=auto` activates hybrid automatically
- **Stdio transport only** — no HTTP or SSE transport; MCP server communicates via stdin/stdout
- **Partial document relationship resolution** — M4 relationship extraction exists for curated seed notes but is not yet complete enough for production-scale current/applicable curation
- **Partial version/status resolution** — status fields exist, but production curation still requires stronger detection of superseded/amended/withdrawn documents

---

## Known Limitations

### Document Coverage

- The baseline seed corpus is small and AML/CFT-focused.
- The production target is not the entire EBA archive. It is a filtered current/applicable regulatory corpus.
- Generated discovery manifests must be reviewed for status, canonicality, and supersession before being treated as production-ready.

### Incomplete Document Relationship Resolution

Cross-references and lifecycle relationships between EBA documents (e.g., "amends", "repeals", "supersedes") are only partially resolved. Production curation needs a stronger relationship graph to remove superseded/amending-only material from the default corpus.

### Incomplete Status / Version Resolver

There are status/version fields and MCP tools, but the automated large-corpus discovery still relies on heuristics. Users must verify important documents against [eba.europa.eu](https://www.eba.europa.eu) until production curation is complete.

### No Incremental Index Updates

Index updates require a full corpus rebuild. There is no incremental update mechanism; adding or updating documents requires re-running the full pipeline and publishing a new `eba-corpus.db` release artifact. True incremental support (file-hash diff + chunk-hash/embedding cache) is a future backlog item. See the Corpus Versioning Policy in [README.md](../README.md#corpus-versioning-policy).

### No Multi-Language Support

Only English-language EBA publications are supported. EBA documents published in EU member state languages are not processed.

### PDF Extraction Accuracy

PDF-to-text extraction accuracy varies depending on document structure. Documents with complex tables, footnotes, or non-standard formatting may have reduced extraction quality. Always verify important passages against the original PDF.

### No HTTP / SSE Transport

The MCP server uses stdio transport only. Deployment scenarios requiring HTTP endpoints or Server-Sent Events are not supported in this POC.

---

## Privacy / RODO

This tool processes **publicly available EBA publications** sourced directly from `eba.europa.eu`.

- **No personal data is collected or stored** by this tool.
- Document metadata and text originate from public EBA sources and contain no personal information.
- The SQLite corpus database (`data/corpora/`) contains only document text and metadata from official EBA publications.
- No usage data, queries, or user information is transmitted to any external service.

Users integrating this tool into their own systems are responsible for their own compliance with Regulation (EU) 2016/679 (GDPR/RODO), including data processing activities performed using the tool's output. This tool does not relieve integrators of their own RODO obligations.

---

## AI Act Statement

This tool is a **retrieval and citation tool**, not an AI system within the scope of Regulation (EU) 2024/1689 (EU AI Act).

- No machine learning models are embedded in this tool.
- All outputs are **direct citations** from official source documents with explicit provenance (document ID, page reference).
- No autonomous AI decisions are made by this system.
- The tool does not generate, infer, or synthesize content — it retrieves and surfaces existing text from indexed EBA publications.

If this tool is used as a component within a larger AI system, the operator of that system is responsible for their own EU AI Act compliance obligations.

---

## Legal Disclaimer

**This tool does not provide legal advice.**

Citations and document excerpts are provided for **research and reference purposes only**. The retrieved text represents excerpts from EBA publications and must not be interpreted as legal advice, regulatory guidance, or a substitute for professional legal counsel.

- Always verify citations against the **official EBA publication** at [eba.europa.eu](https://www.eba.europa.eu).
- EBA guidelines, opinions, and recommendations may be amended, superseded, or withdrawn — always check the current status.
- The authors of this tool make no warranties regarding the accuracy, completeness, or currency of the indexed content.
- Compliance decisions must be made by qualified legal and compliance professionals, not solely on the basis of automated tool output.
