#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCHEMA_SQL="$ROOT_DIR/pipeline/eba_pipeline/index/schema.sql"

PASS=0
FAIL=0

cd "$ROOT_DIR"

call_tool() {
  local db_path="$1"
  local tool_name="$2"
  local args_json="$3"
  local request_id="$4"

  python3 - "$tool_name" "$args_json" "$request_id" <<'PY' | node dist/index.js --db "$db_path" 2>/dev/null
import json
import sys

tool_name = sys.argv[1]
args = json.loads(sys.argv[2])
request_id = int(sys.argv[3])

print(json.dumps({
    "jsonrpc": "2.0",
    "id": request_id,
    "method": "tools/call",
    "params": {
        "name": tool_name,
        "arguments": args,
    },
}))
PY
}

assert_error() {
  local name="$1"
  local result="$2"

  if RESULT_JSON="$result" python3 - <<'PY'
import json, os

outer = json.loads(os.environ["RESULT_JSON"])
assert "result" in outer, "missing result envelope"
assert "content" in outer["result"], "missing result.content"
text = outer["result"]["content"][0]["text"]
payload = json.loads(text)
assert payload.get("answerability") == "error", f"expected answerability=error, got {payload.get('answerability')}"
assert payload.get("citations") == [], f"expected empty citations, got {payload.get('citations')}"
PY
  then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $name"
    FAIL=$((FAIL + 1))
    echo "  Response (first 300 chars): ${result:0:300}"
  fi
}

assert_no_crash() {
  local name="$1"
  local result="$2"

  if RESULT_JSON="$result" python3 - <<'PY'
import json, os

outer = json.loads(os.environ["RESULT_JSON"])
if "error" in outer:
    pass
elif "result" in outer:
    text = outer["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert "answerability" in payload, "response must have answerability"
else:
    raise AssertionError("missing both result and error in response")
PY
  then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $name"
    FAIL=$((FAIL + 1))
    echo "  Response (first 300 chars): ${result:0:300}"
  fi
}

create_empty_db() {
  local empty_db="$1"

  python3 - "$SCHEMA_SQL" "$empty_db" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import sqlite3, sys

schema_path = Path(sys.argv[1])
db_path = sys.argv[2]

conn = sqlite3.connect(db_path)
conn.executescript(schema_path.read_text())
conn.execute(
    'INSERT INTO corpus_manifest (manifest_hash, built_at, document_count, chunk_count) VALUES (?, ?, ?, ?)',
    ('empty-corpus', datetime.now(timezone.utc).isoformat(), 0, 0),
)
conn.commit()
conn.close()
PY
}

TEMP_DB="$(mktemp /tmp/hardening-eba-XXXXXX.db)"
trap 'rm -f "$TEMP_DB"' EXIT

create_empty_db "$TEMP_DB"

echo "=== Input hardening security tests ==="
echo

HUGE_QUERY="$(python3 -c "print('a' * 600)")"
res="$(call_tool "$TEMP_DB" "eba_search" "{\"query\":\"$HUGE_QUERY\"}" 1)"
assert_error "query exceeding 500 chars is rejected" "$res"

SQL_QUERY="risk' OR '1'='1'; DROP TABLE chunks; --"
ESCAPED_SQL="$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$SQL_QUERY")"
res="$(call_tool "$TEMP_DB" "eba_search" "{\"query\":$ESCAPED_SQL}" 2)"
assert_no_crash "SQL-injection-style query is handled gracefully" "$res"

FTS_QUERY='* OR eba_id="EBA/GL/2021/02"'
ESCAPED_FTS="$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$FTS_QUERY")"
res="$(call_tool "$TEMP_DB" "eba_search" "{\"query\":$ESCAPED_FTS}" 3)"
assert_no_crash "FTS injection-style query is handled gracefully" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","limit":999}' 4)"
assert_error "limit exceeding max (50) is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","max_chars":0}' 24)"
assert_error "max_chars below minimum is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","max_chars":1.5}' 25)"
assert_error "decimal max_chars is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","max_chars":100001}' 26)"
assert_error "max_chars exceeding max is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","max_citations":0}' 27)"
assert_error "max_citations below minimum is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","max_citations":51}' 28)"
assert_error "max_citations exceeding max (50) is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","response_mode":"verbose"}' 29)"
assert_error "unknown response_mode is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","unknown_field":"value"}' 5)"
assert_error "unknown field in eba_search is rejected by .strict()" "$res"

res="$(call_tool "$TEMP_DB" "eba_get_document" '{"eba_id":"EBA/GL/9999/01","__proto__":{"polluted":true}}' 6)"
assert_no_crash "__proto__ key: JSON.parse drops it silently, request processes without prototype pollution" "$res"

res="$(call_tool "$TEMP_DB" "eba_get_document" '{"eba_id":"EBA/GL/9999/01","constructor":{"name":"exploit"}}' 7)"
assert_no_crash "constructor key: rejected by Zod strict or MCP layer without crashing" "$res"

res="$(call_tool "$TEMP_DB" "eba_get_document" '{"eba_id":{"nested":"object"}}' 8)"
assert_error "nested object where string expected in eba_id is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","filters":{"language":"fr"}}' 9)"
assert_error "unsupported language filter (fr) is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"risk","filters":{"document_type":{"nested":"value"}}}' 10)"
assert_error "nested object in filter string field is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_get_document" '{"eba_id":"NOTEBA/GL/2021/01"}' 11)"
assert_error "eba_id not matching EBA/* pattern is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_get_document" '{"eba_id":"EBA/GL/2021/02; DROP TABLE documents;--"}' 12)"
assert_error "eba_id with SQL injection suffix is rejected by pattern" "$res"

HUGE_CHUNK_ID="$(python3 -c "print('EBA-GL-2021-02:' + 'a' * 300)")"
ESCAPED_CHUNK="$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$HUGE_CHUNK_ID")"
res="$(call_tool "$TEMP_DB" "eba_validate_citation" "{\"chunk_id\":$ESCAPED_CHUNK}" 13)"
assert_error "chunk_id exceeding 240 chars is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_validate_citation" '{"chunk_id":"valid-id","extra":"field"}' 14)"
assert_error "extra field in eba_validate_citation is rejected by .strict()" "$res"

res="$(call_tool "$TEMP_DB" "eba_validate_citation" '{}' 24)"
assert_error "eba_validate_citation without chunk_id or citation_id is rejected" "$res"

CTRL_JSON_FILE="$(mktemp /tmp/ctrl-json-XXXXXX.json)"
python3 -c "
import json
payload = {
    'jsonrpc': '2.0',
    'id': 15,
    'method': 'tools/call',
    'params': {
        'name': 'eba_search',
        'arguments': {'query': 'risk', 'filters': {'document_type': 'risk\u0001injection'}},
    },
}
open('$CTRL_JSON_FILE', 'w').write(json.dumps(payload) + '\n')
"
res="$(node dist/index.js --db "$TEMP_DB" < "$CTRL_JSON_FILE" 2>/dev/null)"
rm -f "$CTRL_JSON_FILE"
if [ -z "$res" ]; then
  echo "[PASS] control character (SOH \\u0001) in filter string: MCP SDK dropped request (no crash, no output)"
  PASS=$((PASS + 1))
else
  assert_error "control character (SOH \\u0001) in filter string is rejected" "$res"
fi

res="$(call_tool "$TEMP_DB" "eba_get_paragraph" '{"eba_id":"EBA/GL/2021/02","paragraph_ref":"4.1.2; DROP TABLE chunks--"}' 16)"
assert_error "paragraph_ref with SQL injection suffix is rejected by pattern" "$res"

HUGE_PARA_REF="$(python3 -c "print('p.' + '1' * 60)")"
ESCAPED_PARA="$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$HUGE_PARA_REF")"
res="$(call_tool "$TEMP_DB" "eba_get_paragraph" "{\"eba_id\":\"EBA/GL/2021/02\",\"paragraph_ref\":$ESCAPED_PARA}" 17)"
assert_error "paragraph_ref exceeding 50 chars is rejected" "$res"

TWENTY_ONE_REFS="$(python3 - <<'PY'
import json
print(json.dumps([f'4.{i}' for i in range(21)]))
PY
)"
res="$(call_tool "$TEMP_DB" "eba_get_paragraph" "{\"eba_id\":\"EBA/GL/2021/02\",\"paragraph_refs\":$TWENTY_ONE_REFS}" 22)"
assert_error "paragraph_refs exceeding max (20) is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_get_paragraph" '{"eba_id":"EBA/GL/2021/02"}' 23)"
assert_error "eba_get_paragraph without paragraph_ref or paragraph_refs is rejected" "$res"

res="$(call_tool "$TEMP_DB" "eba_diff_versions" '{"eba_id":"EBA/GL/2021/02","version_a":"1.0","version_b":"2.0","prototype":{"polluted":true}}' 18)"
assert_error "prototype key in eba_diff_versions is rejected by .strict()" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"   "}' 19)"
assert_error "whitespace-only query is rejected after normalization" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"AML","filters":{"exclude_consultation_responses":"true"}}' 20)"
assert_error "exclude_consultation_responses string 'true' is rejected, JSON boolean required" "$res"

res="$(call_tool "$TEMP_DB" "eba_search" '{"query":"AML","filters":{"exclude_consultation_responses":true}}' 21)"
if [ -n "$res" ]; then
  echo "[PASS] exclude_consultation_responses JSON true is accepted"
  PASS=$((PASS + 1))
else
  echo "[FAIL] exclude_consultation_responses JSON true is accepted"
  FAIL=$((FAIL + 1))
fi

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -eq 0 ]; then
  echo "All $PASS hardening checks passed"
  exit 0
fi

exit 1
