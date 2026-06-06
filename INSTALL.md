# Install EBA MCP

This guide is for consumers and LLM agents that need to clone and run the EBA MCP server. The production corpus database is **not included in the repository**. Download `eba-corpus.db` from GitHub Releases and place it at `data/corpora/eba-corpus.db`.

This is a consumer install guide. It does not cover rebuilding the corpus or publishing releases.

## Software Requirements

- **Git**
- **Node.js** >= 18
- **npm**
- **GitHub CLI (`gh`)** — recommended for downloading the corpus release artifact
- **Ollama** — recommended for hybrid semantic retrieval ([install](https://ollama.com/))
- An MCP-compatible client (Claude Desktop, etc.)

---

## MCP Server Setup from Release Artifact

### 1. Clone the repository

```bash
git clone https://github.com/systemaml/eba-mcp.git eba-mcp
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

### 4. Download the corpus database from GitHub Releases

Create the local corpus directory:

```bash
mkdir -p data/corpora
```

Preferred agent-friendly command, from inside the cloned repository:

```bash
gh repo view --json nameWithOwner,url,defaultBranchRef
gh release list --repo systemaml/eba-mcp --limit 10
```

If `gh release list` returns no releases, the corpus artifact has not been published yet. Ask the repository maintainer to publish `eba-corpus.db` before continuing.

```bash
gh release download \
  --repo systemaml/eba-mcp \
  --pattern 'eba-corpus.db' \
  --dir data/corpora \
  --clobber
```

This downloads the asset from the latest release visible to `gh`. If you need a specific release tag, add it before the flags:

```bash
gh release download <release-tag> \
  --repo systemaml/eba-mcp \
  --pattern 'eba-corpus.db' \
  --dir data/corpora \
  --clobber
```

Manual fallback: open the repository releases page and download the asset named `eba-corpus.db`:

```text
https://github.com/systemaml/eba-mcp/releases
```

Place the downloaded file at:

```
data/corpora/eba-corpus.db
```

If no release exists yet, ask the repository maintainer to publish `eba-corpus.db` before continuing.

### 5. Start the MCP server

```bash
node dist/index.js --db data/corpora/eba-corpus.db
```

For MCP client configuration, use absolute paths:

```bash
node /absolute/path/to/eba-mcp/dist/index.js \
  --db /absolute/path/to/eba-mcp/data/corpora/eba-corpus.db
```

### 6. Optional: Ollama for hybrid retrieval

Hybrid retrieval embeds the query at runtime with Ollama and fuses semantic results with FTS5 keyword results. FTS-only retrieval works without Ollama.

```bash
# Install Ollama from https://ollama.com/, then:
ollama pull nomic-embed-text
```

Default Ollama URL: `http://localhost:11434`. Override with `OLLAMA_URL` env var if needed.

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
        "/absolute/path/to/eba-mcp/data/corpora/eba-corpus.db"
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
         --db /absolute/path/to/eba-mcp/data/corpora/eba-corpus.db
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

Run the release download command from the setup section, then verify the file exists at `data/corpora/eba-corpus.db`. If no release asset exists yet, ask the repository maintainer to publish it.

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
