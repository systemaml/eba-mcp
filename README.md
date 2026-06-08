# EBA MCP — Citation-First MCP Connector for EBA Publications

A Model Context Protocol (MCP) server that provides structured access to current, applicable European Banking Authority (EBA) regulatory publications.

## Consumer Install

For full setup, see [INSTALL.md](INSTALL.md). You need Git, Node.js >= 18, npm, Python >= 3.11, uv, an MCP-compatible client, and optionally Ollama for hybrid semantic retrieval.

The production corpus database (~147 MB) is **not included in the repository** — download it from GitHub Releases or generate it locally using the Python pipeline:

```text
data/corpora/eba-corpus.db  # release artifact or generated locally
```

See [INSTALL.md](INSTALL.md) for step-by-step corpus generation commands (`uv run eba-pipeline discover`, `download`, `parse`, `quality`, `build-index`).

## What It Is

This project is a two-part system:

1. **Python Pipeline** (`pipeline/`) — ingests EBA PDF publications, parses them into structured chunks, performs quality checks, and builds a SQLite/FTS5 full-text search index.
2. **TypeScript MCP Server** (`src/`) — serves 11 MCP tools over stdio JSON-RPC, enabling AI assistants (Claude, etc.) to search and cite official EBA documents with precision.

The architecture is citation-first: every response includes document ID, page reference, and exact text extracted from the source PDF — making it suitable for compliance research where traceability matters.

### Target Corpus Policy

The production goal is **not** to make every historical EBA PDF searchable. The target is an **EBA Current Applicable Corpus** for legal and compliance professionals across domains.

Default production discovery should include only current/applicable regulatory materials, primarily:

- Guidelines
- Recommendations
- Regulatory Technical Standards (RTS), where not merely draft/proposed material
- Implementing Technical Standards (ITS), where not merely draft/proposed material

Default production discovery should exclude:

- consultation papers
- draft/proposed documents
- track-changes files
- annex-only/instruction-only files when they are not the canonical source
- superseded, repealed, withdrawn, deprecated, or archival documents

Historical/proposed material may still be useful for research, but it should be indexed only through an explicit archive/research profile, not the default MCP corpus.

### Key Characteristics

- Default seed targets the current/applicable EBA regulatory corpus
- SQLite + FTS5 citation search, with optional hybrid retrieval via sqlite-vec and Ollama embeddings when the database includes vectors
- Citation round-trip checks verify stored chunks can be resolved back to exact source citations
- Stdio transport (JSON-RPC 2.0 over stdin/stdout)
- English-language documents only
- Production direction: current/applicable EBA regulatory corpus, not full EBA archive

---

## Quick Start

For detailed consumer setup and MCP client configuration, see [INSTALL.md](INSTALL.md).

### Prerequisites

- Node.js >= 18
- npm
- MCP-compatible client
- Ollama optional, for hybrid semantic retrieval

### 1. Install Dependencies

```bash
npm install
```

### 2. Build the MCP Server

```bash
npm run build
```

Compiles TypeScript to `dist/index.js`.

### 3. Start the MCP Server

```bash
# Production corpus (current/applicable, hybrid retrieval):
node dist/index.js --db data/corpora/eba-corpus.db
```

The server communicates over stdio using JSON-RPC 2.0, as per the MCP specification.

---

## MCP Configuration

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "eba": {
      "command": "node",
      "args": ["/path/to/eba-mcp/dist/index.js", "--db", "/path/to/eba-mcp/data/corpora/eba-corpus.db"]
    }
  }
}
```

### Any MCP-Compatible Client

The server accepts a single argument `--db <path>` pointing to the built SQLite database. It communicates exclusively over stdio — no HTTP server is started.

### Runtime Environment Variables

Optional maintainer overrides for hybrid retrieval:

- `OLLAMA_URL` (default: `http://localhost:11434`)
- `EMBEDDING_MODEL` (default: `nomic-embed-text`)
- `RRF_K` (default: `60`)
- `RRF_WEIGHT_FTS` (default: `1.0`)
- `RRF_WEIGHT_VEC` (default: `1.0`)
- `EBA_SEARCH_MODE` (default: `auto`; leave unset for normal MCP clients)

Example invocation:

```bash
node dist/index.js --db /absolute/path/to/data/corpora/eba-corpus.db
```

---

## Available Tools

| Tool | Description |
|------|-------------|
| `eba_search` | FTS/hybrid search across indexed EBA documents with citation results |
| `eba_get_document` | Get metadata and leading citations for a specific EBA document by ID |
| `eba_get_paragraph` | Get a specific paragraph with optional surrounding context; or batch-fetch up to 20 paragraphs via `paragraph_refs` |
| `eba_get_section` | Get all citation chunks matching a section or paragraph prefix |
| `eba_get_toc` | Get a best-effort document outline with paragraph and page ranges |
| `eba_list_documents` | List indexed documents with optional filters |
| `eba_corpus_info` | Get corpus statistics and manifest info |
| `eba_get_status` | Get publication and applicability status for a document |
| `eba_get_versions` | Get version history for a document |
| `eba_validate_citation` | Validate a citation chunk ID and related status |
| `eba_diff_versions` | Compare metadata between two document versions |

### Tool Details

**`eba_search`** — Searches indexed EBA chunks and returns citation objects with document ID, page, paragraph reference, and text. Retrieval is automatic: the server uses hybrid FTS + semantic search when vectors and Ollama are available, and falls back to FTS5 when they are not. Supports filters including `eba_id`, `document_type`, `topic`, `publication_status`, `applicability_status`, and `exclude_consultation_responses`. `topic="AML/CFT"` also matches AML-relevant titles whose stored corpus topic is a publication facet such as `EBA guidelines`. Use optional `max_chars` only when a bounded excerpt is needed; omit it for full citation text.

**`eba_get_document`** — Returns document metadata and a small sample of leading parsed chunks for a given document identifier. It is not a full-document dump; use `eba_get_toc` and `eba_get_section` for document navigation.

**`eba_get_paragraph`** — Retrieves chunks matching a paragraph reference, with optional `context_before` and `context_after` parameters to include surrounding chunks. Accepts `paragraph_refs: string[]` (up to 20) for batch retrieval of multiple paragraphs in a single call; each citation includes `is_anchor` (marks the requested paragraph vs. surrounding context) and `is_complete` (`false` for split paragraph fragments ending in `:sub1`/`:sub2`, `true` otherwise) flags. Omit `max_chars` for full paragraph text.

**`eba_get_section`** — Retrieves chunks where `paragraph_ref` or `section_path` matches a section prefix such as `4` or `4.7`. This is a quick way to read a whole EBA guideline section after search discovery.

**`eba_get_toc`** — Returns a best-effort outline derived from parsed `section_path`, paragraph references, page ranges, and sequence ranges. It is not a guaranteed extraction of the PDF's printed table of contents.

**`eba_list_documents`** — Lists all documents in the index with metadata (title, publication date, document type). Supports optional type/keyword filters.

**`eba_corpus_info`** — Returns statistics: total documents, total chunks, index size, and manifest version.

### Agent Usage Guidance

Use `eba_search` for discovery. Send English queries because the corpus is English and the default local embedding model, `nomic-embed-text`, is optimized for English text. If the user asks in Polish or another language, translate the search intent to focused English regulatory terms before calling `eba_search`. For broad compliance or legal questions, run several focused English searches using EBA regulatory terms, then synthesize the answer only from returned citations and excerpts. `eba_search` returns citation-ready excerpts, not final legal advice or definitive legal interpretations.

Good AML risk-scoring query patterns include:

- `high-risk third countries customer due diligence enhanced monitoring`
- `customer due diligence risk factors business relationship transaction purpose`
- `PEP risk factors politically exposed persons enhanced due diligence`
- `source of funds source of wealth verification risk factors`
- `ongoing monitoring customer risk profile transaction monitoring`
- `risk weighting scoring methodology automated model override`

Use `eba_get_paragraph` after discovery when an exact `eba_id` and `paragraph_ref` need surrounding context for a citation. If a result has `paragraph_ref: null`, use `eba_get_section`/`eba_get_toc` or validate the returned `citation_id` with `eba_validate_citation` instead of trying paragraph navigation. `eba_get_section` is broad navigation; prefer the narrowest available section prefix after inspecting `eba_get_toc`.

---

## Troubleshooting

- **Missing tools (`eba_get_toc`, `eba_get_section`) in MCP client**: rebuild the server (`npm run build`) and restart the MCP client so the tool list is re-fetched. Some clients cache `tools/list` results.
- **`Expected boolean, received string` or unknown top-level key on `exclude_consultation_responses`**: pass a JSON boolean (`true`/`false`), not the string `"true"`, and nest it under `filters`. Example: `"filters": {"exclude_consultation_responses": true}`.

## Development Setup

### Requirements

- **Node.js** >= 18 (for the MCP server)
- **Python** >= 3.11 (for the pipeline)
- **uv** >= 0.4 — Python package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))

### Project Structure

```
eba-mcp/
├── src/               # TypeScript MCP server source
├── pipeline/          # Python pipeline (download, parse, quality, build-index, eval)
│   ├── eba_pipeline/  # Pipeline package
│   └── pyproject.toml
├── data/
│   ├── corpora/       # Corpus artifact directory (DB generated locally or downloaded from releases)
│   │   └── eba-corpus.db        # generated/downloaded locally (not in Git)
│   ├── raw/           # Downloaded PDFs (source provenance)
│   ├── processed/     # Parsed chunks (intermediate)
│   └── quality_reports/
├── docs/              # Architecture, data model, MCP contract, limitations
├── dist/              # Compiled JS (after npm run build)
└── package.json
```

### TypeScript Development

```bash
npm run build       # Compile TypeScript
npm run lint        # Type-check without emitting
```

### Pipeline Development

```bash
cd pipeline
uv run eba-pipeline --help   # List all commands
uv run pytest                # Run pipeline tests
```

Generate a current/applicable seed manifest from official EBA publication listings:

```bash
cd pipeline && uv run eba-pipeline discover \
  --profile current-applicable \
  --output ../pipeline/seed_documents_current.yaml \
  --limit 350
```

Use `--profile broad` only for stress testing or archive/research corpora.

---

## Production Corpus

The current production corpus is:

```
data/corpora/eba-corpus.db
```

The database (~147 MB) is **not tracked in Git** — download it from GitHub Releases or generate it locally using the pipeline (see [INSTALL.md](INSTALL.md)). Release tags identify corpus versions; the local filename remains stable.

| Property | Value |
|----------|-------|
| Corpus | EBA Current Applicable |
| Version | Release tag |
| Documents | 346 |
| Chunks | 42,146 |
| Vectors | 42,146 |
| Embedding model | `nomic-embed-text` |
| Embedding dim | 768 |
| Language | English |
| Eval fixtures | 99/99 passing |

### Corpus Versioning Policy

Corpus artifact filenames are stable: `eba-corpus.db`. Versioning is handled by GitHub Release tags.

**The current safe update process is a validated rebuild before publishing a release artifact:**

1. Run the full pipeline against new/updated EBA discovery manifest → new temp DB.
2. Validate vector integrity and schema.
3. Run the eval suite against the new DB.
4. If eval passes, publish `eba-corpus.db` as a GitHub Release artifact.
5. Roll back by downloading the previous release artifact if needed.

**No in-place mutation of a production DB.** Modifying a live DB risks invalidating the sha256 manifest and makes rollback impossible.

### Incremental Updates (Future Backlog)

True incremental update support (discovery diff + file-hash compare + chunk-hash/embedding cache + embed only new/changed chunks) is **not implemented yet**. It is a backlog item. Do not attempt partial index updates — use full rebuild only.

---

## Evaluation

### Run the Eval Suite

```bash
cd pipeline && uv run eba-pipeline eval --db ../data/corpora/eba-corpus.db --queries eba_pipeline/eval/queries.yaml
```

Runs the curated query fixtures against the current production corpus. Current result: **99/99 passing**.

### Citation Round-Trip Verification

```bash
cd pipeline && uv run eba-pipeline eval --db ../data/corpora/eba-corpus.db --mode citation-roundtrip
```

Verifies that every stored chunk can be resolved back to its exact `chunk_id` through paragraph or section/page lookup. The current production corpus achieves **100% round-trip accuracy** on 42,146 chunks.

---

## License

MIT — see [LICENSE](LICENSE).

**Important**: The MIT license covers **code only**. EBA publications downloaded by the pipeline remain © European Banking Authority and are subject to EBA's terms of use. Documents are fetched directly from `eba.europa.eu` and are **not redistributed** as part of this project.

See [docs/limitations.md](docs/limitations.md) for scope, known limitations, and legal disclaimers.
