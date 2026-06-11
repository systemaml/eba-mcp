#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCHEMA_SQL="$ROOT_DIR/pipeline/eba_pipeline/index/schema.sql"

PASS=0
FAIL=0
TEMP_DIR="$(mktemp -d /tmp/eba-env-precedence-XXXXXX)"
SERVER_STDERR="$TEMP_DIR/server.err"
trap 'rm -rf "$TEMP_DIR"' EXIT

print_response() {
  RESPONSE_TO_PRINT="$1" python3 - <<'PY'
import os

text = os.environ.get("RESPONSE_TO_PRINT", "")
print(text[:500])
PY
}

create_empty_db() {
  local db_path="$1"
  local manifest_hash="$2"

  mkdir -p "$(dirname "$db_path")"
  python3 - "$SCHEMA_SQL" "$db_path" "$manifest_hash" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

schema_path = Path(sys.argv[1])
db_path = Path(sys.argv[2])
manifest_hash = sys.argv[3]

conn = sqlite3.connect(db_path)
conn.executescript(schema_path.read_text())
conn.execute(
    'INSERT INTO corpus_manifest (manifest_hash, built_at, document_count, chunk_count) VALUES (?, ?, ?, ?)',
    (manifest_hash, datetime.now(timezone.utc).isoformat(), 0, 0),
)
conn.commit()
conn.close()
PY
}

call_corpus_info() {
  local cwd="$1"
  shift

  (
    cd "$cwd"
    "$@" 2>"$SERVER_STDERR" <<'JSON'
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"eba_corpus_info","arguments":{}}}
JSON
  )
}

assert_manifest() {
  local name="$1"
  local result="$2"
  local expected_hash="$3"

  if RESULT_JSON="$result" EXPECTED_HASH="$expected_hash" python3 - <<'PY'
import json
import os

outer = json.loads(os.environ["RESULT_JSON"])
text = outer["result"]["content"][0]["text"]
payload = json.loads(text)
assert payload["answerability"] == "exact", payload
assert payload["corpus_info"]["manifest_hash"] == os.environ["EXPECTED_HASH"], payload
PY
  then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $name"
    FAIL=$((FAIL + 1))
    print_response "$result"
    if [ -s "$SERVER_STDERR" ]; then
      print_response "$(cat "$SERVER_STDERR")"
    fi
  fi
}

REPO_ROOT="$TEMP_DIR/repo"
NESTED_ROOT="$REPO_ROOT/nested/workdir"
mkdir -p "$NESTED_ROOT"
printf '{}\n' > "$REPO_ROOT/package.json"

DEFAULT_DB="$REPO_ROOT/data/corpora/eba-corpus.db"
DOTENV_DB="$REPO_ROOT/data/from-dotenv.db"
PROCESS_DB="$TEMP_DIR/from-process.db"
CLI_DB="$TEMP_DIR/from-cli.db"

create_empty_db "$DEFAULT_DB" "default-db"
create_empty_db "$DOTENV_DB" "dotenv-db"
create_empty_db "$PROCESS_DB" "process-db"
create_empty_db "$CLI_DB" "cli-db"

result="$(call_corpus_info "$NESTED_ROOT" env -i PATH="$PATH" HOME="$HOME" node "$ROOT_DIR/dist/index.js")"
assert_manifest "missing .env uses repo-root default DB" "$result" "default-db"

printf 'EBA_DB_PATH=data/from-dotenv.db\n' > "$REPO_ROOT/.env"
result="$(call_corpus_info "$NESTED_ROOT" env -i PATH="$PATH" HOME="$HOME" node "$ROOT_DIR/dist/index.js")"
assert_manifest ".env default is loaded from repo root" "$result" "dotenv-db"

result="$(call_corpus_info "$NESTED_ROOT" env -i PATH="$PATH" HOME="$HOME" EBA_DB_PATH="$PROCESS_DB" node "$ROOT_DIR/dist/index.js")"
assert_manifest "process EBA_DB_PATH overrides .env" "$result" "process-db"

result="$(call_corpus_info "$NESTED_ROOT" env -i PATH="$PATH" HOME="$HOME" EBA_DB_PATH="$PROCESS_DB" node "$ROOT_DIR/dist/index.js" --db "$CLI_DB")"
assert_manifest "CLI --db overrides process env and .env" "$result" "cli-db"

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -eq 0 ]; then
  exit 0
fi
exit 1
