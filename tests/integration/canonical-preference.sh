#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB_PATH="${1:-$ROOT_DIR/data/corpora/eba-corpus.db}"
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
  const result = await searchChunksWithMode(query, {}, 25);
  const ids = result.chunks.map((chunk) => `${chunk.eba_id}|${chunk.paragraph_ref ?? 'null'}`);

  console.log(`query=${query}`);
  console.log(`search_mode=${result.search_mode}`);
  ids.forEach((id, index) => console.log(`${String(index + 1).padStart(2, '0')} ${id}`));

  const seriesCode = (ebaId) => {
    if (ebaId.startsWith('EBA/LARGE-')) {
      return ebaId.slice('EBA/LARGE-'.length).split('/')[0];
    }
    return ebaId.slice('EBA/'.length).split('/')[0];
  };

  let checkedPairs = 0;
  for (let index = 0; index < result.chunks.length; index += 1) {
    const chunk = result.chunks[index];
    if (!chunk.eba_id.startsWith('EBA/LARGE-') || !chunk.paragraph_ref) {
      continue;
    }

    const canonicalIndex = result.chunks.findIndex((candidate) => (
      !candidate.eba_id.startsWith('EBA/LARGE-') &&
      candidate.paragraph_ref === chunk.paragraph_ref &&
      seriesCode(candidate.eba_id) === seriesCode(chunk.eba_id)
    ));

    if (canonicalIndex === -1) {
      continue;
    }

    checkedPairs += 1;
    if (canonicalIndex > index) {
      throw new Error(`Canonical duplicate should rank ahead of LARGE duplicate (canonical=${canonicalIndex + 1}, large=${index + 1}, paragraph=${chunk.paragraph_ref})`);
    }
  }

  console.log(`canonical_duplicate_pairs_checked=${checkedPairs}`);
})();
JS
