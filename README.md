# EBA MCP — Citation-First MCP Connector for EBA Publications

A Model Context Protocol (MCP) server that provides structured access to current, applicable European Banking Authority (EBA) regulatory publications.

## Consumer Install

For clone-and-run setup, see [INSTALL.md](INSTALL.md). Normal consumers only need Git, Node.js >= 18, npm, an MCP-compatible client, and optionally Ollama for hybrid semantic retrieval.

The versioned production corpus is intentionally included in this repository for immediate use:

```text
data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.manifest.json
```

No corpus-building pipeline is required to run the MCP server with the production corpus.

## What It Is

This project is a two-part system:

1. **Python Pipeline** (`pipeline/`) — ingests EBA PDF publications, parses them into structured chunks, performs quality checks, and builds a SQLite/FTS5 full-text search index.
2. **TypeScript MCP Server** (`src/`) — serves 9 MCP tools over stdio JSON-RPC, enabling AI assistants (Claude, etc.) to search and cite official EBA documents with precision.

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

- Quick-start sample seed indexes 9 EBA AML/CFT publications; production direction is the current/applicable EBA regulatory corpus
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
# Production corpus (current/applicable, 188 docs, hybrid retrieval):
node dist/index.js --db data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
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
      "args": ["/path/to/eba-mcp/dist/index.js", "--db", "/path/to/eba-mcp/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db"]
    }
  }
}
```

### Any MCP-Compatible Client

The server accepts a single argument `--db <path>` pointing to the built SQLite database. It communicates exclusively over stdio — no HTTP server is started.

### Runtime Environment Variables

Optional hybrid-retrieval settings:

- `OLLAMA_URL` (default: `http://localhost:11434`)
- `EMBEDDING_MODEL` (default: `nomic-embed-text`)
- `RRF_K` (default: `60`)
- `RRF_WEIGHT_FTS` (default: `1.0`)
- `RRF_WEIGHT_VEC` (default: `1.0`)
- `EBA_SEARCH_MODE` (default: `auto`)

Example invocation:

```bash
node dist/index.js --db /absolute/path/to/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

---

## Available Tools

| Tool | Description |
|------|-------------|
| `eba_search` | FTS/hybrid search across indexed EBA documents with citation results |
| `eba_get_document` | Get all chunks for a specific EBA document by ID |
| `eba_get_paragraph` | Get a specific paragraph with optional surrounding context |
| `eba_list_documents` | List indexed documents with optional filters |
| `eba_corpus_info` | Get corpus statistics and manifest info |
| `eba_get_status` | Get publication and applicability status for a document |
| `eba_get_versions` | Get version history for a document |
| `eba_validate_citation` | Validate a citation chunk ID and related status |
| `eba_diff_versions` | Compare metadata between two document versions |

### Tool Details

**`eba_search`** — Searches indexed EBA chunks and returns citation objects with document ID, page, paragraph reference, and text excerpt. Retrieval is controlled by `EBA_SEARCH_MODE`: `fts_only` uses SQLite FTS5, `hybrid` requires sqlite-vec vectors plus query-time Ollama embeddings, and `auto` uses hybrid when available while preserving citation-first results.

**`eba_get_document`** — Returns all parsed chunks for a given document identifier. Useful for reading a full guideline section by section.

**`eba_get_paragraph`** — Retrieves chunks matching a paragraph reference, with optional `context_before` and `context_after` parameters to include surrounding chunks.

**`eba_list_documents`** — Lists all documents in the index with metadata (title, publication date, document type). Supports optional type/keyword filters.

**`eba_corpus_info`** — Returns statistics: total documents, total chunks, index size, and manifest version.

### Agent Usage Guidance

Use `eba_search` for discovery. Send English queries because the corpus is English and the default local embedding model, `nomic-embed-text`, is optimized for English text. For broad compliance or legal questions, run several focused English searches using EBA regulatory terms, then synthesize the answer only from returned citations and excerpts. `eba_search` returns citation-ready excerpts, not final legal advice or definitive legal interpretations.

Good AML risk-scoring query patterns include:

- `high-risk third countries customer due diligence enhanced monitoring`
- `customer due diligence risk factors business relationship transaction purpose`
- `PEP risk factors politically exposed persons enhanced due diligence`
- `source of funds source of wealth verification risk factors`
- `ongoing monitoring customer risk profile transaction monitoring`
- `risk weighting scoring methodology automated model override`

Use `eba_get_paragraph` after discovery when an exact `eba_id` and `paragraph_ref` need surrounding context for a citation.

---

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
│   ├── corpora/       # Versioned production corpus artifacts
│   │   ├── eba-current-applicable-2026-06-01-nomic-embed-text.db
│   │   └── eba-current-applicable-2026-06-01-nomic-embed-text.manifest.json
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
data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

This versioned production corpus and its manifest are intentionally tracked in Git so consumers and LLM agents can run the MCP server immediately after clone, install, and build.

| Property | Value |
|----------|-------|
| Corpus | EBA Current Applicable |
| Version | 2026-06-01 |
| Documents | 188 |
| Chunks | 29,952 |
| Vectors | 29,952 |
| Embedding model | `nomic-embed-text` |
| Embedding dim | 768 |
| Language | English |
| Eval (hybrid, 30 fixtures) | MRR 0.741, Recall@10 1.000 |

A machine-readable manifest is at:
```
data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.manifest.json
```

### Corpus Versioning Policy

Corpus artifact filenames encode the date and embedding model: `eba-current-applicable-YYYY-MM-DD-<model>.db`.

**The current safe update process is a full rebuild to a new versioned DB:**

1. Run the full pipeline against new/updated EBA discovery manifest → new temp DB.
2. Validate vector integrity and schema.
3. Run eval suite (`--tags full_curated_semantic`) against the new DB.
4. If eval passes, rename/promote to a new versioned path (e.g., `eba-current-applicable-2026-09-01-nomic-embed-text.db`).
5. Update MCP client configs to point at the new versioned DB.
6. Keep the previous version for rollback until storage policy allows removal.

**No in-place mutation of a production DB.** Modifying a live DB risks invalidating the sha256 manifest and makes rollback impossible.

### Incremental Updates (Future Backlog)

True incremental update support (discovery diff + file-hash compare + chunk-hash/embedding cache + embed only new/changed chunks) is **not implemented yet**. It is a backlog item. Do not attempt partial index updates — use full rebuild only.

---

## Evaluation

### Run the Eval Suite

```bash
cd pipeline && uv run eba-pipeline eval --db ../data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db --queries eba_pipeline/eval/queries.yaml --tags full_curated_semantic
```

Runs 30 predefined queries (`full_curated_semantic` tag) against the current production corpus and measures retrieval quality (MRR, Recall@10). Current results: **hybrid MRR 0.741, Recall@10 1.000**.

### Citation Round-Trip Verification

```bash
cd pipeline && uv run eba-pipeline eval --db ../data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db --mode citation-roundtrip
```

Verifies that every stored chunk can be resolved back to its exact `chunk_id` through paragraph or section/page lookup. The current production corpus achieves **100% round-trip accuracy** on 29,952 chunks.

---

## License

MIT — see [LICENSE](LICENSE).

**Important**: The MIT license covers **code only**. EBA publications downloaded by the pipeline remain © European Banking Authority and are subject to EBA's terms of use. Documents are fetched directly from `eba.europa.eu` and are **not redistributed** as part of this project.

See [docs/limitations.md](docs/limitations.md) for scope, known limitations, and legal disclaimers.
