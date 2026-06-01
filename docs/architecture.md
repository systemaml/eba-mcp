# EBA MCP Architecture

## Overview

EBA MCP is a local, citation-first MCP connector for EBA regulatory publications. It follows a hybrid stack: Python handles document ingestion and indexing, while TypeScript/Node.js serves the MCP runtime. All data lives in a local SQLite database with FTS5 full-text search and optional sqlite-vec semantic vectors. No external services are required at runtime for FTS-only mode; hybrid retrieval additionally requires a locally running Ollama instance for query-time embeddings.

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        EBA Official Website                         │
│         publications, PDF files, metadata, language versions        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  Seed / Discovery Manifest                          │
│         (curated YAML or current-applicable discovery profile)      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Python Ingestion Pipeline                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐   │
│  │  downloader │→ │ PDF parser  │→ │paragraphizer│→ │quality    │   │
│  │  (requests) │  │(PyMuPDF4LLM │  │             │  │  gates    │   │
│  └─────────────┘  │ + fallback) │  └─────────────┘  └─────┬─────┘   │
│                   └─────────────┘                         │         │
└──────────────────────────────┬────────────────────────────┘         │ 
                               │                                      │
                               ▼                                      │
┌─────────────────────────────────────────────────────────────────────┐
│                      Local Corpus Artifacts                         │
│  data/raw/{eba_id_slug}/{lang}/{sha256}.pdf                         │
│  data/manifest.jsonl                                                │
│  data/quality_reports/                                              │
│  data/corpora/eba-current-applicable-YYYY-MM-DD-<model>.db          │
│  data/manifest.jsonl                                                │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     SQLite / FTS5 Index Layer                       │
│ documents │ document_versions │ chunks │ chunks_fts │ corp_manifest │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    TypeScript MCP Runtime                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐   │
│  │   stdio     │  │   tools     │  │   schemas   │  │  citation │   │
│  │  transport  │  │  (9 tools)  │  │   (Zod)     │  │ formatter │   │
│  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘   │
│  ┌─────────────┐  ┌─────────────┐                                   │
│  │better-sqlite│  │  retrieval  │                                   │
│  │   (sync)    │  │   engine    │                                   │
│  └─────────────┘  └─────────────┘                                   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│              MCP Client (LLM)                                       │
└─────────────────────────────────────────────────────────────────────┘
```

## Stack Decisions

### TypeScript/Node.js for MCP Runtime

The MCP server layer uses TypeScript and Node.js to stay consistent with the MateMatic Solutions connector ecosystem. The `@modelcontextprotocol/sdk` provides the canonical MCP server implementation. All tool contracts, input validation, and response formatting live in this layer.

### Python for Ingestion Pipeline

PDF parsing for regulatory documents is the hardest problem in this project. Python has a stronger ecosystem for this: PyMuPDF4LLM for structured extraction, fallback to pdftotext, and robust page-level output. The pipeline also handles paragraphization, quality gates, and index building. Keeping this separate from the runtime means the MCP server never parses PDFs.

### SQLite + FTS5 for the Index

SQLite with FTS5 gives us a local, deterministic, file-based index. It requires no external database server. The current POC uses SQLite FTS5 `rank` ordering and does not implement custom BM25 column weights. The `better-sqlite3` Node.js adapter is native, synchronous, and battle-tested, which simplifies the retrieval code.

### stdio Transport Only (POC)

The POC uses stdio transport exclusively. This keeps the surface area small, avoids network configuration, and works out of the box with local MCP clients like Claude Desktop and Patron. HTTP or Streamable HTTP may be added in later milestones.

### Hybrid Retrieval (FTS5 + sqlite-vec)

The production corpus (`data/corpora/`) includes precomputed `nomic-embed-text` embeddings (768-dim) stored in a `chunks_vec` virtual table via the `sqlite-vec` extension. At query time, the retrieval engine embeds the query using a locally running Ollama instance and fuses FTS5 keyword scores with cosine similarity scores via Reciprocal Rank Fusion (RRF). `EBA_SEARCH_MODE=auto` activates hybrid when vectors are present; `fts_only` falls back to keyword-only search.

Embeddings and the Ollama dependency are optional at runtime: if Ollama is not running, the server falls back to FTS5 automatically.

### No Docling in POC

Docling is reserved as a potential fallback for difficult PDFs in MVP. The POC uses PyMuPDF4LLM as the primary parser and pdftotext as the fallback.

## Component Responsibilities

### Python Ingestion Pipeline

| Module | Responsibility |
|--------|----------------|
| `downloader` | Fetches PDFs from hardcoded seed URLs, stores by SHA256, never overwrites existing files |
| `manifest` | Builds `manifest.jsonl` with eba_id, title, type, status, URL, hash, and timestamp |
| `pdf_extract` | Extracts page-level text and structure using PyMuPDF4LLM, falls back to pdftotext |
| `paragraphize` | Maps extracted text to citation-ready chunks with paragraph_ref, page_start, page_end, and section_path |
| `quality` | Runs quality gates: page_coverage, paragraph_ref_detection, citation_roundtrip, broken_word_ratio |
| `sqlite_store` | Creates and populates the SQLite schema: documents, versions, chunks, relationships, manifest |
| `fts` | Builds the contentless FTS5 virtual table and hydrates hits through `chunks.rowid` |

### TypeScript MCP Runtime

| Module | Responsibility |
|--------|----------------|
| `server.ts` | Instantiates the MCP server over stdio, registers tools, handles lifecycle |
| `tools.ts` | Implements the 9 MCP tools: eba_search, eba_get_document, eba_get_paragraph, eba_list_documents, eba_corpus_info, eba_get_status, eba_get_versions, eba_validate_citation, eba_diff_versions |
| `schemas.ts` | Zod schemas for input validation and output structure |
| `sqlite.ts` | Opens the SQLite corpus DB at the path provided via `--db` CLI argument via better-sqlite3; conditionally loads the sqlite-vec extension when the DB contains a `chunks_vec` table; provides query helpers |
| `retrieval.ts` | Executes FTS5 queries with escaping, ranking, and filtering; exact eba_id matches bypass FTS |
| `formatters.ts` | Formats citations as `EBA/GL/YYYY/NN, para. X.Y, p. Z` and builds structuredContent responses |

## Data Flow

1. **Seed/Discover**: A curated YAML manifest or a current-applicable discovery profile defines which EBA publications to ingest. Quick-start uses a small sample seed; production uses a full current-applicable manifest (188+ documents).
2. **Download**: The Python downloader fetches each PDF, computes SHA256, and stores it at `data/raw/{eba_id_slug}/en/{sha256}.pdf`.
3. **Parse**: PyMuPDF4LLM extracts page-level text, blocks, and tables. Fallback to pdftotext if quality gates fail.
4. **Paragraphize**: The paragraphizer splits text into chunks, detects paragraph references, assigns section paths, and records page_start/page_end.
5. **Quality Gates**: Each document is checked against thresholds: page_coverage >= 0.85, paragraph_ref_detection >= 0.70, citation_roundtrip >= 0.95. Failures go to `needs_review`.
6. **Index Build**: Passing documents are inserted into SQLite. The `chunks` table stores every chunk. The `chunks_fts` virtual table indexes eba_id, title, section_path, paragraph_ref, body, topic, and document_type with per-column weights.
7. **Manifest**: A `corpus_manifest` row records the build timestamp, document count, chunk count, and a manifest hash.
8. **MCP Runtime**: The TypeScript server opens the versioned corpus DB on startup. When a client calls `eba_search`, the retrieval engine selects a search mode: in `fts_only` mode it runs an escaped FTS5 query and orders by SQLite FTS5 rank; in `hybrid` mode it also queries `chunks_vec` via sqlite-vec for cosine similarity and fuses both rank lists via Reciprocal Rank Fusion (RRF); `auto` mode picks hybrid when vectors are present, FTS5 otherwise. All modes return formatted citations.
9. **Citation**: Every result includes a citation string like `EBA/GL/2024/01, para. 4.12, p. 42`, plus metadata: eba_id, title, status, applicability, source URL, and file SHA256.

## Quality Thresholds

| Metric | Threshold | Purpose |
|--------|-----------|---------|
| page_coverage_ratio | >= 0.85 | Ensure most pages yield extractable text |
| paragraph_ref_detection_ratio | >= 0.70 | Ensure numbered paragraphs are found |
| citation_roundtrip_pass_rate | >= 0.95 | Ensure citations can be verified back to source |
| broken_word_ratio | <= 0.05 | Limit word-splitting artifacts |
| empty_page_ratio | <= 0.10 | Limit completely blank extracted pages |
| duplicate_chunk_ratio | <= 0.05 | Limit repeated content |

## Determinism and Auditing

The same seed URLs, the same parser version, and the same pipeline code produce the same chunk IDs and database content. The POC chunk ID embeds a SHA256 prefix of the chunk text, not the source PDF hash. The `corpus_manifest` table stores a SHA256 over sorted chunk IDs so rebuilds can be compared deterministically.
