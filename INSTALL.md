# Install EBA MCP

This guide is for consumers and LLM agents that need to clone and run the EBA MCP server with the included production corpus. You do not need to build the corpus, run the Python pipeline, download PDFs, or install `uv` for normal use.

## LLM Agent Quick Path

1. Clone the repository:

   ```bash
   git clone <REPOSITORY_URL> eba-mcp
   cd eba-mcp
   ```

2. Install Node dependencies:

   ```bash
   npm install
   ```

3. Build the MCP server:

   ```bash
   npm run build
   ```

4. Confirm the production corpus exists:

   ```bash
   test -f data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
   test -f data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.manifest.json
   ```

5. Use this server command in an MCP client, replacing `/absolute/path/to/eba-mcp` with the cloned repository path:

   ```bash
   node /absolute/path/to/eba-mcp/dist/index.js --db /absolute/path/to/eba-mcp/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
   ```

6. If Ollama is installed and running with `nomic-embed-text`, leave `EBA_SEARCH_MODE=auto` for hybrid retrieval. If Ollama is not available, set `EBA_SEARCH_MODE=fts_only`.

## What This MCP Provides

EBA MCP is a stdio Model Context Protocol server for citation-first search across current, applicable European Banking Authority publications. It exposes tools for search, document lookup, paragraph lookup, corpus information, status/version metadata, citation validation, and version metadata comparison.

The server returns citation-ready excerpts with document IDs, page references, paragraph or section references, and exact text snippets. It is a research and retrieval tool, not legal advice.

## Software Requirements

- Git
- Node.js >= 18
- npm
- An MCP-compatible client, such as Claude Desktop or another client that can launch stdio MCP servers
- Ollama is optional but recommended for hybrid semantic retrieval

Python and `uv` are only needed for development and corpus-building workflows. They are not required for normal consumer use of the included production corpus.

## Hardware and Storage Requirements

- Disk space for the repository, `node_modules`, the included corpus database, and optional Ollama model files
- The production corpus database is about 147 MB
- At least 4 GB RAM
- 8 GB RAM preferred when running an LLM client and Ollama on the same machine
- GPU is not required

## Production Corpus Included

The production corpus is intentionally included in the repository for immediate use:

```text
data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.manifest.json
```

Corpus details:

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

No corpus build is required for installation. The database already contains SQLite FTS5 search data and `sqlite-vec` vectors for hybrid retrieval.

## Install Commands

```bash
git clone <REPOSITORY_URL> eba-mcp
cd eba-mcp
npm install
npm run build
```

## Optional Ollama Setup

Hybrid retrieval embeds the query at runtime with Ollama and fuses semantic results with FTS5 keyword results. FTS-only retrieval works without Ollama.

1. Install Ollama from <https://ollama.com/>.
2. Start Ollama.
3. Pull the embedding model:

   ```bash
   ollama pull nomic-embed-text
   ```

By default, EBA MCP connects to Ollama at:

```text
OLLAMA_URL=http://localhost:11434
```

Override `OLLAMA_URL` only if your Ollama service uses a different host or port.

## Start Command

From the repository root:

```bash
node dist/index.js --db data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

For MCP client configuration, prefer absolute paths:

```bash
node /absolute/path/to/eba-mcp/dist/index.js --db /absolute/path/to/eba-mcp/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

The server communicates over stdio JSON-RPC. It does not start an HTTP server.

## MCP Client Configuration

### Claude Desktop

Add this to `claude_desktop_config.json`, replacing `/absolute/path/to/eba-mcp` with your clone path:

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

If Ollama is not available, use:

```json
"env": {
  "EBA_SEARCH_MODE": "fts_only"
}
```

### Other MCP Clients

Configure the client to launch:

```text
command: node
args: /absolute/path/to/eba-mcp/dist/index.js --db /absolute/path/to/eba-mcp/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

Use stdio transport. This server does not expose HTTP, SSE, or Streamable HTTP.

## Search Modes

Set `EBA_SEARCH_MODE` to control retrieval:

- `auto`: default. Uses hybrid retrieval when the vector table exists and Ollama is reachable; otherwise falls back to FTS5 keyword search.
- `fts_only`: uses SQLite FTS5 keyword search only. Use this when Ollama is not installed, unavailable, too slow, or not desired.
- `hybrid`: requires the vector-enabled DB and reachable Ollama. Use this when you want semantic retrieval and want failures surfaced if hybrid cannot run.

Recommended consumer default:

```text
EBA_SEARCH_MODE=auto
```

Recommended no-Ollama setting:

```text
EBA_SEARCH_MODE=fts_only
```

## Verification

Build verification:

```bash
npm run build
```

Smoke test against the included production corpus:

```bash
bash tests/integration/canonical-preference.sh data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

The canonical-preference smoke test uses hybrid mode. Start Ollama and pull `nomic-embed-text` first, or expect it to fail because explicit `hybrid` requires runtime embeddings.

## Agent Query Guidance

- Use English queries. The corpus is English, and the default embedding model is optimized for English text.
- Run multiple focused searches instead of one broad legal question.
- Use EBA regulatory terms such as `customer due diligence`, `high-risk third countries`, `source of funds`, `beneficial ownership`, `PEP`, `ongoing monitoring`, and `risk factors`.
- Cite only excerpts returned by the MCP tools.
- Do not present MCP output as legal advice or a definitive legal interpretation.
- Use `eba_get_paragraph` after discovery when you need surrounding context for a known `eba_id` and `paragraph_ref`.

Example focused searches:

```text
customer due diligence risk factors business relationship transaction purpose
high-risk third countries enhanced due diligence monitoring
source of funds source of wealth verification risk factors
ongoing monitoring customer risk profile transaction monitoring
risk scoring methodology automated model override
```

## Troubleshooting

### `dist/index.js` is missing

Run:

```bash
npm run build
```

The TypeScript server is compiled into `dist/` during the build step.

### `sqlite-vec` or native module install fails

Check that you are using Node.js >= 18 on a supported OS and architecture. Remove `node_modules` and reinstall if the native package was installed under a different Node version:

```bash
rm -rf node_modules package-lock.json
npm install
npm run build
```

### Ollama is unavailable

If you do not need semantic retrieval, set:

```text
EBA_SEARCH_MODE=fts_only
```

If you want hybrid retrieval, start Ollama and pull the model:

```bash
ollama pull nomic-embed-text
```

Confirm `OLLAMA_URL` points to the running service. The default is `http://localhost:11434`.

### Database is missing

Confirm both files exist:

```bash
ls data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
ls data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.manifest.json
```

If they are missing, the repository checkout is incomplete. Re-clone the repository or fetch large files according to the repository host's instructions.

### Permissions or path problems

Use absolute paths in MCP client configuration. Make sure the user running the MCP client can read the cloned repository, `dist/index.js`, and the production database.

### MCP client starts but tools return no hybrid results

Check `EBA_SEARCH_MODE`. In `auto`, the server can fall back to FTS5 when Ollama is unavailable. In `hybrid`, Ollama must be reachable and the `nomic-embed-text` model must be installed.
