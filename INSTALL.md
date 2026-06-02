# Install EBA MCP

This guide is for consumers and LLM agents that need to clone and run the EBA MCP server. The production corpus database is **not included in the repository** — it must be generated locally using the Python ingestion pipeline. This guide explains both the quick MCP server setup and the full corpus generation workflow.

## Software Requirements

- **Git**
- **Node.js** >= 18
- **npm**
- **Python** >= 3.11 (for corpus generation)
- **uv** >= 0.4 — Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **Ollama** — recommended for hybrid semantic retrieval ([install](https://ollama.com/))
- An MCP-compatible client (Claude Desktop, etc.)

---

## Part 1: MCP Server Setup

### 1. Clone the repository

```bash
git clone <REPOSITORY_URL> eba-mcp
cd eba-mcp
```

### 2. Install Node dependencies

```bash
npm install
```

### 3. Build the MCP server

```bash
npm run build
```

This compiles TypeScript to `dist/index.js`.

### 4. Generate the corpus database (required — see Part 2)

The production database is not included in the repository. Before starting the server, generate it locally:

```bash
# Quick check: does the DB already exist from a previous run?
ls data/corpora/*.db 2>/dev/null || echo "No corpus DB found — run Part 2 to generate."
```

### 5. Start the MCP server

Once the database exists:

```bash
node dist/index.js --db data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

For MCP client configuration, use absolute paths:

```bash
node /absolute/path/to/eba-mcp/dist/index.js \
  --db /absolute/path/to/eba-mcp/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

### 6. Optional: Ollama for hybrid retrieval

Hybrid retrieval embeds the query at runtime with Ollama and fuses semantic results with FTS5 keyword results. FTS-only retrieval works without Ollama.

```bash
# Install Ollama from https://ollama.com/, then:
ollama pull nomic-embed-text
```

Default Ollama URL: `http://localhost:11434`. Override with `OLLAMA_URL` env var if needed.

---

## Part 2: Corpus Generation

The corpus database (~147 MB) is generated locally by the Python pipeline. It is too large for Git and is therefore excluded from the repository. The manifest JSON (`*.manifest.json`) is tracked as a lightweight reference only.

### 2a. Install Python pipeline

```bash
cd pipeline
uv sync
```

Verify the CLI is available:

```bash
uv run eba-pipeline --help
```

### 2b. Discover EBA publications

Generate a seed manifest listing current/applicable EBA publications:

```bash
cd pipeline  # if not already there
uv run eba-pipeline discover \
  --profile current-applicable \
  --output seed_documents_current.yaml \
  --limit 350
```

Expected output: `Discovered NNN official EBA PDFs using profile=current-applicable -> seed_documents_current.yaml`

### 2c. Download EBA PDFs

```bash
uv run eba-pipeline download \
  --manifest seed_documents_current.yaml \
  --output ../data/current-pdfs \
  --continue-on-error
```

This downloads the PDFs to `data/current-pdfs/`. The `--continue-on-error` flag skips documents that fail to download.

### 2d. Parse PDFs into structured chunks

```bash
uv run eba-pipeline parse \
  --input ../data/current-pdfs \
  --output ../data/current-parsed \
  --manifest seed_documents_current.yaml
```

### 2e. Run quality gates

```bash
uv run eba-pipeline quality \
  --input ../data/current-parsed \
  --reports ../data/current-quality
```

Expected output: `Quality complete: N/N passed.`

### 2f. Build the SQLite index with embeddings

This step requires Ollama with `nomic-embed-text` to be running. It builds the FTS5 + vector index.

```bash
# Ensure Ollama is running and model is available:
ollama pull nomic-embed-text

# Build the index with embeddings:
uv run eba-pipeline build-index \
  --output ../data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db \
  --processed ../data/current-parsed \
  --quality-reports ../data/current-quality \
  --seed seed_documents_current.yaml \
  --embed \
  --model nomic-embed-text \
  --ollama-url http://localhost:11434 \
  --batch-size 32
```

To build without embeddings (FTS-only, no Ollama required):

```bash
uv run eba-pipeline build-index \
  --output ../data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db \
  --processed ../data/current-parsed \
  --quality-reports ../data/current-quality \
  --seed seed_documents_current.yaml
```

Expected output ends with: `Build-index complete.`

### 2g. Verify the database

```bash
# File should exist and be non-empty:
ls -lh ../data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

Run the citation round-trip check (verifies every stored chunk resolves back to its source citation):

```bash
uv run eba-pipeline eval \
  --db ../data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db \
  --mode citation-roundtrip
```

Expected output: `Citation round-trip: NNN/NNN passed (100.0%)`

Optional: run the retrieval eval suite (requires the built `dist/index.js` at repo root):

```bash
uv run eba-pipeline eval \
  --db ../data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db \
  --queries eba_pipeline/eval/queries.yaml \
  --tags full_curated_semantic
```

---

## Part 3: Updating the Corpus

To rebuild the corpus for a new date (e.g., `2026-09-01`):

1. Re-run discovery with a new seed output name:

   ```bash
   cd pipeline
   uv run eba-pipeline discover \
     --profile current-applicable \
     --output seed_documents_2026-09-01.yaml \
     --limit 350
   ```

2. Download and parse as in steps 2c–2e above, pointing `--output` / `--input` to new versioned directories.

3. Build the index with a new versioned DB filename:

   ```bash
   uv run eba-pipeline build-index \
     --output ../data/corpora/eba-current-applicable-2026-09-01-nomic-embed-text.db \
     --processed ../data/processed-2026-09-01 \
     --quality-reports ../data/quality-2026-09-01 \
     --seed seed_documents_2026-09-01.yaml \
     --embed \
     --model nomic-embed-text \
     --ollama-url http://localhost:11434 \
     --batch-size 32
   ```

4. Verify with citation round-trip check (step 2g).

5. Update your MCP client config to point `--db` at the new versioned path.

6. Keep the previous DB locally until you confirm the new version is stable.

> **Note:** Do not mutate an existing production DB in place. Always build a new versioned file to allow rollback.

---

## MCP Client Configuration

### Claude Desktop

Add to `claude_desktop_config.json`, replacing the path with your clone location:

```json
{
  "mcpServers": {
    "eba": {
      "command": "node",
      "args": [
        "/absolute/path/to/eba-mcp/dist/index.js",
        "--db",
        "/absolute/path/to/eba-mcp/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db"
      ],
      "env": {
        "EBA_SEARCH_MODE": "auto",
        "OLLAMA_URL": "http://localhost:11434"
      }
    }
  }
}
```

Without Ollama:

```json
"env": {
  "EBA_SEARCH_MODE": "fts_only"
}
```

### Other MCP Clients

```text
command: node
args:    /absolute/path/to/eba-mcp/dist/index.js
         --db /absolute/path/to/eba-mcp/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

Use stdio transport. This server does not expose HTTP, SSE, or Streamable HTTP.

---

## Search Modes

| Mode | Description |
|------|-------------|
| `auto` | Default. Uses hybrid when vectors + Ollama are available; falls back to FTS5. |
| `fts_only` | SQLite FTS5 keyword search only. No Ollama required. |
| `hybrid` | Requires vector-enabled DB and reachable Ollama. Fails if either is unavailable. |

---

## Troubleshooting

### `dist/index.js` is missing

```bash
npm run build
```

### `sqlite-vec` or native module install fails

```bash
rm -rf node_modules package-lock.json
npm install
npm run build
```

Requires Node.js >= 18 on a supported OS and architecture.

### Ollama is unavailable

Set `EBA_SEARCH_MODE=fts_only` to use keyword-only search, or start Ollama and pull the model:

```bash
ollama pull nomic-embed-text
```

### Database is missing

The DB is not tracked in Git — it must be generated locally. Run Part 2 of this guide.

To confirm which DB is expected:

```bash
cat data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.manifest.json
```

The manifest JSON (tracked in Git) describes the target corpus version, document count, chunk count, and sha256 of the expected DB file.

### Permissions or path problems

Use absolute paths in MCP client configuration. Ensure the user running the MCP client can read `dist/index.js` and the corpus database.

---

## Agent Query Guidance

- Use English queries. The corpus is English, and `nomic-embed-text` is optimized for English.
- Run multiple focused searches rather than one broad legal question.
- Use EBA regulatory terms: `customer due diligence`, `high-risk third countries`, `source of funds`, `beneficial ownership`, `PEP`, `ongoing monitoring`, `risk factors`.
- Cite only excerpts returned by the MCP tools.
- Do not present MCP output as legal advice or a definitive legal interpretation.
- Use `eba_get_paragraph` after discovery when you need surrounding context for a known `eba_id` and `paragraph_ref`.
