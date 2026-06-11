# EBA MCP — Citation-First MCP Connector for EBA Publications

A Model Context Protocol (MCP) server that provides structured access to current, applicable European Banking Authority (EBA) regulatory publications.

## Consumer Install

For full consumer setup, see [INSTALL.md](INSTALL.md). You need Git, Node.js >= 18, npm, an MCP-compatible client, and optionally Ollama for hybrid semantic retrieval.

The production corpus database is **not included in the repository**. Consumers should download the release artifact named `eba-corpus.db` and place it at:

```text
data/corpora/eba-corpus.db
```

Maintainers who need to rebuild or validate a corpus should use [DEVELOPMENT.md](DEVELOPMENT.md).

## What It Is

This project is a two-part system:

1. **TypeScript MCP Server** (`src/`) — serves 11 MCP tools over stdio JSON-RPC, enabling AI assistants to search and cite official EBA documents with precision.
2. **Maintainer corpus tooling** (`pipeline/`) — documented in [DEVELOPMENT.md](DEVELOPMENT.md). Consumers normally do not run it.

The architecture is citation-first: every search result includes document ID, page reference, paragraph metadata where available, and exact text extracted from the source PDF.

## Production Corpus

The runtime expects the production corpus at:

```text
data/corpora/eba-corpus.db
```

Current production corpus characteristics:

| Property | Value |
|----------|-------|
| Corpus | EBA Current Applicable |
| Documents | 341 |
| Chunks | 41,345 |
| Vectors | 41,345 |
| Relationships | 559 |
| Embedding model | `nomic-embed-text` |
| Embedding dim | 768 |
| Language | English |

The target corpus is current/applicable EBA regulatory material for legal and compliance professionals. It is not intended to be a full historical EBA archive. Maintainer corpus creation and release workflow is documented in [DEVELOPMENT.md](DEVELOPMENT.md).

## Quick Start

For detailed MCP client configuration, see [INSTALL.md](INSTALL.md).

```bash
npm install
npm run build
node dist/index.js --db data/corpora/eba-corpus.db
```

The server communicates over stdio using JSON-RPC 2.0. It does not start an HTTP server.

## MCP Configuration

### Claude Desktop

Add to `claude_desktop_config.json`, replacing paths with absolute paths for your clone:

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
        "OLLAMA_URL": "http://localhost:11434"
      }
    }
  }
}
```

`OLLAMA_URL` is optional. If Ollama or vector search is unavailable, the server falls back to FTS5 keyword retrieval automatically.

## Available Tools

| Tool | Description |
|------|-------------|
| `eba_search` | Search across indexed EBA documents with bounded citation results |
| `eba_get_document` | Get metadata and leading citations for a specific EBA document by ID |
| `eba_get_paragraph` | Get one or more paragraph references with optional surrounding context |
| `eba_get_section` | Get citation chunks matching a section or paragraph prefix |
| `eba_get_toc` | Get a best-effort document outline with paragraph and page ranges |
| `eba_list_documents` | List indexed documents with optional filters |
| `eba_corpus_info` | Get corpus statistics and manifest info |
| `eba_get_status` | Get publication and applicability status for a document |
| `eba_get_versions` | Get version history for a document |
| `eba_validate_citation` | Validate a returned citation/chunk ID and related status |
| `eba_diff_versions` | Compare metadata between two document versions |

### Agent Usage Guidance

Use `eba_search` for discovery. Send English queries because the corpus is English and the default local embedding model, `nomic-embed-text`, is optimized for English text. If the user asks in Polish or another language, translate the search intent to focused English regulatory terms before calling `eba_search`.

Good AML risk-scoring query patterns include:

- `high-risk third countries customer due diligence enhanced monitoring`
- `customer due diligence risk factors business relationship transaction purpose`
- `PEP risk factors politically exposed persons enhanced due diligence`
- `source of funds source of wealth verification risk factors`
- `ongoing monitoring customer risk profile transaction monitoring`
- `risk weighting scoring methodology automated model override`

Use `eba_get_paragraph` after discovery when an exact `eba_id` and `paragraph_ref` need surrounding context for a citation. If a result has `paragraph_ref: null`, use `eba_get_section`/`eba_get_toc` or validate the returned `citation_id` with `eba_validate_citation` instead of trying paragraph navigation.

## Troubleshooting

- **Missing tools (`eba_get_toc`, `eba_get_section`) in MCP client**: rebuild the server (`npm run build`) and restart the MCP client so the tool list is re-fetched. Some clients cache `tools/list` results.
- **`Expected boolean, received string` or unknown top-level key on `exclude_consultation_responses`**: pass a JSON boolean (`true`/`false`), not the string `"true"`, and nest it under `filters`. Example: `{"filters": {"exclude_consultation_responses": true}}`.
- **No semantic retrieval**: start Ollama and pull `nomic-embed-text`; otherwise the server uses FTS5 fallback automatically.

## License

MIT — see [LICENSE](LICENSE).

**Important**: The MIT license covers **code only**. EBA publications remain © European Banking Authority and are subject to EBA's terms of use. Documents are fetched directly from `eba.europa.eu` and are **not redistributed** as part of this project.

See [docs/limitations.md](docs/limitations.md) for scope, known limitations, and legal disclaimers.
