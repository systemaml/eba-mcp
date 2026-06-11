# Development and Corpus Maintenance

This guide is for EBA MCP maintainers. Consumers and LLM agents that only need to run the MCP server should use [INSTALL.md](INSTALL.md) and download the release artifact `eba-corpus.db`.

## Repository Shape

- TypeScript MCP runtime: `src/`
- Runtime entrypoint: `src/index.ts` -> `src/mcp/server.ts`
- Built command: `node dist/index.js --db data/corpora/eba-corpus.db`
- Python corpus pipeline: `pipeline/` (`eba-pipeline = eba_pipeline.cli:cli`)
- Production DB path: `data/corpora/eba-corpus.db` (ignored by Git)

The MCP server opens SQLite read-only and conditionally loads `sqlite-vec` when the DB has a `chunks_vec` table. FTS fallback is expected for non-vector DBs or when Ollama is unavailable.

## Development Requirements

- Node.js >= 18
- npm
- Python >= 3.11
- uv >= 0.4
- Ollama with `nomic-embed-text` when building embedded corpora or testing hybrid retrieval

## Project Structure

```text
eba-mcp/
├── src/               # TypeScript MCP server source
├── pipeline/          # Python pipeline (discover, download, parse, quality, build-index, eval)
│   ├── eba_pipeline/  # Pipeline package
│   └── pyproject.toml
├── data/
│   ├── corpora/       # eba-corpus.db release artifact or locally built DB; ignored by Git
│   ├── raw/           # Downloaded PDFs
│   ├── processed/     # Parsed chunks
│   └── quality_reports/
├── docs/
├── dist/              # Compiled JS after npm run build; ignored by Git
└── package.json
```

## Runtime Development Commands

From repo root:

```bash
npm run lint      # tsc --noEmit
npm run build     # emits dist/
npm test          # bash tests/integration/e2e.sh
```

Additional integration checks when touching retrieval, validation, ranking, or env behavior:

```bash
bash tests/integration/input-hardening.sh
bash tests/integration/canonical-preference.sh
bash tests/integration/env-precedence.sh
```

## Pipeline Development Commands

From `pipeline/`:

```bash
uv sync
uv run pytest
uv run eba-pipeline --help
uv run eba-pipeline eval --db ../data/corpora/eba-corpus.db --queries eba_pipeline/eval/queries.yaml
uv run eba-pipeline eval --db ../data/corpora/eba-corpus.db --mode citation-roundtrip
```

Python tests live as top-level `pipeline/test_*.py` / `pipeline/tests_*.py`; `pipeline/tests/` is only a placeholder.

## Target Corpus Policy

The production goal is **not** to make every historical EBA PDF searchable. The target is an **EBA Current Applicable Corpus** for legal and compliance professionals.

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

Historical/proposed material may be useful for research, but should be indexed only through an explicit archive/research profile, not the default MCP corpus.

## Corpus Artifact Policy

- Production DB filename is always `data/corpora/eba-corpus.db`.
- DB files and sidecars are ignored by Git.
- Corpus versioning is by GitHub Release tag, not date-stamped local filenames.
- Normal MCP deployment should download `eba-corpus.db` from Releases.
- Do not mutate a DB already attached to a published release unless explicitly instructed. Prefer a new release tag for any new corpus.

## Rebuilding the Corpus DB

Run the pipeline from `pipeline/`; default `build-index` output is `../data/corpora/eba-corpus.db`:

```bash
uv sync
uv run eba-pipeline discover \
  --profile current-applicable \
  --output seed_documents.yaml \
  --limit 500
uv run eba-pipeline download \
  --manifest seed_documents.yaml \
  --output ../data/raw \
  --continue-on-error
uv run eba-pipeline parse \
  --input ../data/raw \
  --output ../data/processed \
  --manifest seed_documents.yaml
uv run eba-pipeline quality \
  --input ../data/processed \
  --reports ../data/quality_reports
uv run eba-pipeline build-index \
  --seed seed_documents.yaml \
  --embed \
  --model nomic-embed-text \
  --ollama-url http://localhost:11434 \
  --batch-size 128
```

If embedding generation is interrupted, resume only missing vectors:

```bash
uv run eba-pipeline build-index \
  --seed seed_documents.yaml \
  --embed \
  --resume \
  --model nomic-embed-text \
  --batch-size 128
```

Corpus build gotchas:

- `--resume` requires `--embed` and an existing DB; it only fills missing rows in `chunks_vec`.
- `sqlite-vec` is pre-v1 and the schema depends on it (`vec0`, 768-dim `nomic-embed-text`); dependency bumps are corpus-rebuild events.
- On macOS, stock Python may not support SQLite loadable extensions; use a Python/SQLite build where `sqlite3.Connection.enable_load_extension` exists before building embedded corpora.
- `uv sync`/`uv run` will not auto-upgrade locked deps; use explicit `uv lock --upgrade-package ...` when intentionally changing a dependency.

## Evaluation Baselines

Current expected baselines:

- Query eval: `99/99 passed`
- Citation round-trip: `41345/41345 passed (100.0%)`
- MCP integration: `50 passed, 0 failed` and `40/40 tool tests passed` after the current search-mode contract update

Run evaluation from `pipeline/`:

```bash
uv run pytest
uv run eba-pipeline eval --db ../data/corpora/eba-corpus.db --queries eba_pipeline/eval/queries.yaml
uv run eba-pipeline eval --db ../data/corpora/eba-corpus.db --mode citation-roundtrip
```

## Release Readiness and Publishing

Before publishing a corpus release, run from repo root:

```bash
npm run lint
npm run build
npm test
bash tests/integration/input-hardening.sh
bash tests/integration/canonical-preference.sh
```

Then run from `pipeline/`:

```bash
uv run pytest
uv run eba-pipeline eval --db ../data/corpora/eba-corpus.db --queries eba_pipeline/eval/queries.yaml
uv run eba-pipeline eval --db ../data/corpora/eba-corpus.db --mode citation-roundtrip
```

Verify GitHub access and releases:

```bash
gh repo view systemaml/eba-mcp --json nameWithOwner,url,defaultBranchRef
gh release list -R systemaml/eba-mcp --limit 10
```

Create a new release tag after validation and committing relevant metadata/version changes:

```bash
gh release create <release-tag> \
  data/corpora/eba-corpus.db \
  -R systemaml/eba-mcp \
  --title '<release-tag>' \
  --notes 'EBA MCP corpus release'
```

Only replace an existing asset after validation and explicit intent:

```bash
gh release upload <release-tag> \
  data/corpora/eba-corpus.db \
  -R systemaml/eba-mcp \
  --clobber
```

`gh release upload --clobber` deletes the old asset before the new upload completes; if the upload fails, the original asset is lost. New release tags are safer.

## Incremental Updates

True incremental update support (discovery diff + file-hash compare + chunk-hash/embedding cache + embed only new/changed chunks) is **not implemented yet**. Do not attempt partial index updates; use full rebuild only.

## Dependency Gotchas

- `package.json` declares `@modelcontextprotocol/sdk` as `latest`, but this code uses the v1 `Server` + `StdioServerTransport` APIs. Do not casually refresh dependencies to MCP SDK v2/pre-alpha without an API migration.
- The MCP SDK peer requirement is `zod ^3.25 || ^4`; ensure installs resolve at least Zod 3.25 even though `package.json` currently starts at `^3.24.4`.
