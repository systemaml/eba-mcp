#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB_PATH="${1:-$ROOT_DIR/data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db}"
QUERY="${2:-additional checks on customers ownership and control structure beneficial ownership politically exposed persons}"

cd "$ROOT_DIR"

node - <<'JS' "$DB_PATH" "$QUERY"
const dbPath = process.argv[2];
const query = process.argv[3];

process.env.EBA_SEARCH_MODE = 'hybrid';
process.env.OLLAMA_TIMEOUT_MS = process.env.OLLAMA_TIMEOUT_MS || '10000';

const { initDb } = require('./dist/db/sqlite.js');
const { searchChunksWithMode } = require('./dist/db/retrieval.js');

initDb(dbPath);

(async () => {
  const result = await searchChunksWithMode(query, {}, 10);
  const ids = result.chunks.map((chunk) => `${chunk.eba_id}|${chunk.paragraph_ref ?? 'null'}`);

  console.log(`query=${query}`);
  console.log(`search_mode=${result.search_mode}`);
  ids.forEach((id, index) => console.log(`${String(index + 1).padStart(2, '0')} ${id}`));

  const canonicalIndex = result.chunks.findIndex((chunk) => chunk.eba_id === 'EBA/GL/2023/03' && chunk.paragraph_ref === '20.7');
  const largeIndex = result.chunks.findIndex((chunk) => chunk.eba_id === 'EBA/LARGE-GL/0000/0070' && chunk.paragraph_ref === '20.7');

  if (canonicalIndex === -1 || largeIndex === -1) {
    throw new Error('Expected both canonical and LARGE 20.7 citations in top-10 results');
  }

  if (canonicalIndex >= largeIndex) {
    throw new Error(`Canonical 20.7 should rank ahead of LARGE duplicate (canonical=${canonicalIndex + 1}, large=${largeIndex + 1})`);
  }
})();
JS
