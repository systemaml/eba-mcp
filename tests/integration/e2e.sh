#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCHEMA_SQL="$ROOT_DIR/pipeline/eba_pipeline/index/schema.sql"

MODE="all"
DB="data/corpora/eba-corpus.db"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --empty-db-only)
      MODE="empty"
      shift
      ;;
    --real-db-only)
      MODE="real"
      shift
      ;;
    *)
      DB="$1"
      shift
      ;;
  esac
done

PASS=0
FAIL=0
TOOL_PASS=0
TOOL_FAIL=0

cd "$ROOT_DIR"

print_response() {
  RESPONSE_TO_PRINT="$1" python3 - <<'PY'
import os

text = os.environ.get("RESPONSE_TO_PRINT", "")
print(text[:500])
PY
}

assert_json() {
  local name="$1"
  local result="$2"
  local expected="$3"
  local counts_as_tool="${4:-false}"

  if RESULT_JSON="$result" ASSERT_CODE="$expected" python3 - <<'PY'
import json
import os

outer = json.loads(os.environ["RESULT_JSON"])
assert "result" in outer, "missing result envelope"
assert "content" in outer["result"], "missing result.content"
assert outer["result"]["content"], "empty result.content"
text = outer["result"]["content"][0]["text"]
payload = json.loads(text)
namespace = {"outer": outer, "payload": payload}
exec(os.environ["ASSERT_CODE"], namespace)
PY
  then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
    if [ "$counts_as_tool" = "true" ]; then
      TOOL_PASS=$((TOOL_PASS + 1))
    fi
  else
    echo "[FAIL] $name"
    FAIL=$((FAIL + 1))
    if [ "$counts_as_tool" = "true" ]; then
      TOOL_FAIL=$((TOOL_FAIL + 1))
    fi
  fi
}

assert_mcp_envelope() {
  local name="$1"
  local result="$2"
  local expected="$3"
  local counts_as_tool="${4:-false}"

  if RESULT_JSON="$result" ASSERT_CODE="$expected" python3 - <<'PY'
import json
import os

outer = json.loads(os.environ["RESULT_JSON"])
assert "result" in outer, "missing result envelope"
namespace = {"outer": outer, "payload": outer["result"]}
exec(os.environ["ASSERT_CODE"], namespace)
PY
  then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
    if [ "$counts_as_tool" = "true" ]; then
      TOOL_PASS=$((TOOL_PASS + 1))
    fi
  else
    echo "[FAIL] $name"
    FAIL=$((FAIL + 1))
    if [ "$counts_as_tool" = "true" ]; then
      TOOL_FAIL=$((TOOL_FAIL + 1))
    fi
    print_response "$result"
  fi
}

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

mcp_call() {
  local db_path="$1"
  local method="$2"
  local params_json="$3"
  local request_id="$4"

  python3 - "$method" "$params_json" "$request_id" <<'PY' | node dist/index.js --db "$db_path" 2>/dev/null
import json
import sys

method = sys.argv[1]
params = json.loads(sys.argv[2])
request_id = int(sys.argv[3])

print(json.dumps({
    "jsonrpc": "2.0",
    "id": request_id,
    "method": method,
    "params": params,
}))
PY
}

sqlite_value() {
  local db_path="$1"
  local sql="$2"

  python3 - "$db_path" "$sql" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
sql = sys.argv[2]

conn = sqlite3.connect(db_path)
row = conn.execute(sql).fetchone()
if row is None:
    raise SystemExit(1)
value = row[0]
print("" if value is None else value)
PY
}

build_project() {
  npm run build --silent >/dev/null
}

run_real_db_checks() {
  local db_path="$1"

  local expected_doc_count
  expected_doc_count="$(sqlite_value "$db_path" 'SELECT document_count FROM corpus_manifest LIMIT 1')"
  local expected_chunk_count
  expected_chunk_count="$(sqlite_value "$db_path" 'SELECT chunk_count FROM corpus_manifest LIMIT 1')"
  local first_eba_id
  first_eba_id="$(sqlite_value "$db_path" 'SELECT eba_id FROM documents ORDER BY eba_id LIMIT 1')"
  local search_target
  search_target="$(sqlite_value "$db_path" "
    SELECT d.eba_id || '|risk'
    FROM chunks c
    JOIN document_versions dv ON dv.version_id = c.document_version_id
    JOIN documents d ON d.eba_id = dv.document_id
    WHERE lower(c.text) LIKE '%risk%'
    ORDER BY d.eba_id, c.sequence_no
    LIMIT 1
  ")"
  local search_eba_id="${search_target%%|*}"
  local search_query="${search_target#*|}"
  local paragraph_target
  paragraph_target="$(sqlite_value "$db_path" "
    SELECT d.eba_id || '|' || c.paragraph_ref
    FROM chunks c
    JOIN document_versions dv ON dv.version_id = c.document_version_id
    JOIN documents d ON d.eba_id = dv.document_id
    WHERE c.paragraph_ref IS NOT NULL
      AND c.sequence_no > 1
      AND EXISTS (
        SELECT 1
        FROM chunks c2
        WHERE c2.document_version_id = c.document_version_id
          AND c2.sequence_no = c.sequence_no + 1
      )
    ORDER BY d.eba_id, c.sequence_no
    LIMIT 1
  ")"
  local paragraph_eba_id="${paragraph_target%%|*}"
  local paragraph_ref="${paragraph_target#*|}"
  local section_ref="${paragraph_ref%%.*}"
  local filter_document_type
  filter_document_type="$(sqlite_value "$db_path" 'SELECT document_type FROM documents GROUP BY document_type ORDER BY COUNT(*) DESC, document_type LIMIT 1')"
  local hyphen_eba_id
  hyphen_eba_id="$(sqlite_value "$db_path" "SELECT eba_id FROM documents WHERE eba_id LIKE 'EBA/%-%/%/%' ORDER BY eba_id LIMIT 1")"
  local status_eba_id
  status_eba_id="$(sqlite_value "$db_path" "
    SELECT COALESCE(
      (SELECT target_eba_id FROM document_relationships WHERE relationship_type = 'amends' LIMIT 1),
      (SELECT eba_id FROM documents ORDER BY eba_id LIMIT 1)
    )
  ")"
  local has_amends_relationships
  has_amends_relationships="$(sqlite_value "$db_path" 'SELECT COUNT(*) FROM document_relationships WHERE relationship_type = '\''amends'\''')"
  local relationship_count
  relationship_count="$(sqlite_value "$db_path" 'SELECT COUNT(*) FROM document_relationships')"
  local aml_compliance_officers_present
  aml_compliance_officers_present="$(sqlite_value "$db_path" "SELECT COUNT(*) FROM documents WHERE eba_id = 'EBA/GL/2022/05'")"

  local res

  res="$(mcp_call "$db_path" 'tools/list' '{}' 1)"
  assert_mcp_envelope "tools/list exposes all 11 MCP tools" "$res" "
expected = {
  'eba_search', 'eba_get_document', 'eba_get_paragraph', 'eba_get_section',
  'eba_get_toc', 'eba_get_versions', 'eba_diff_versions', 'eba_list_documents',
  'eba_corpus_info', 'eba_get_status', 'eba_validate_citation',
}
assert expected.issubset({t['name'] for t in payload['tools']})
" true

  res="$(call_tool "$db_path" "eba_corpus_info" '{}' 1)"
  assert_json "eba_corpus_info returns corpus metadata" "$res" "
info = payload['corpus_info']
assert payload['answerability'] == 'exact'
assert info['document_count'] == int('$expected_doc_count')
assert info['chunk_count'] == int('$expected_chunk_count')
assert isinstance(info['manifest_hash'], str) and info['manifest_hash']
assert isinstance(payload['query_trace_id'], str) and payload['query_trace_id']
" true

  res="$(call_tool "$db_path" "eba_search" "{\"query\":\"$search_query\",\"filters\":{\"eba_id\":\"$search_eba_id\"},\"limit\":3}" 2)"
  assert_json "eba_search returns citation payload" "$res" "
assert payload['answerability'] in ('exact', 'partial')
assert payload['documents_considered'] == ['$search_eba_id']
assert payload['filters_applied']['eba_id'] == '$search_eba_id'
assert len(payload['citations']) > 0
first = payload['citations'][0]
for key in ('citation_id', 'eba_id', 'text', 'citation', 'chunk_type', 'page_start', 'page_end'):
    assert key in first
assert first['eba_id'] == '$search_eba_id'
" true

  if [ -n "$hyphen_eba_id" ]; then
    res="$(call_tool "$db_path" "eba_search" "{\"query\":\"$hyphen_eba_id\",\"limit\":3}" 21)"
    assert_json "eba_search exact lookup supports hyphenated EBA IDs" "$res" "
assert payload['answerability'] == 'exact'
assert payload['documents_considered'] == ['$hyphen_eba_id']
assert len(payload['citations']) > 0
assert all(citation['eba_id'] == '$hyphen_eba_id' for citation in payload['citations'])
"
  fi

  res="$(call_tool "$db_path" "eba_list_documents" '{"limit":5}' 3)"
  assert_json "eba_list_documents returns documents" "$res" "
assert payload['answerability'] in ('partial', 'exact')
assert len(payload['documents']) > 0
assert payload['total'] == len(payload['documents'])
first = payload['documents'][0]
for key in ('eba_id', 'title', 'document_type', 'topic', 'language', 'publication_status'):
    assert key in first
" true

  res="$(call_tool "$db_path" "eba_list_documents" "{\"filters\":{\"document_type\":\"$filter_document_type\"},\"limit\":10}" 4)"
  assert_json "eba_list_documents respects filters" "$res" "
assert len(payload['documents']) > 0
assert payload['filters_applied']['document_type'] == '$filter_document_type'
assert all(doc['document_type'] == '$filter_document_type' for doc in payload['documents'])
"

  if [ "$aml_compliance_officers_present" -gt 0 ]; then
    res="$(call_tool "$db_path" "eba_list_documents" '{"filters":{"topic":"AML/CFT"},"limit":100}' 41)"
    assert_json "eba_list_documents expands AML/CFT topic coverage" "$res" "
assert payload['filters_applied']['topic'] == 'AML/CFT'
ids = [doc['eba_id'] for doc in payload['documents']]
assert 'EBA/GL/2022/05' in ids, ids
" true
  fi

  res="$(call_tool "$db_path" "eba_get_document" "{\"eba_id\":\"$first_eba_id\"}" 5)"
  assert_json "eba_get_document returns document record" "$res" "
assert payload['answerability'] == 'exact'
assert payload['document']['eba_id'] == '$first_eba_id'
assert isinstance(payload['citations'], list)
assert 'warnings' in payload
sample = payload.get('citation_sample', {})
assert sample.get('full_document_dump') == False
assert isinstance(sample.get('navigation_tools'), list) and len(sample['navigation_tools']) > 0
" true

  res="$(call_tool "$db_path" "eba_get_paragraph" "{\"eba_id\":\"$paragraph_eba_id\",\"paragraph_ref\":\"$paragraph_ref\",\"context_before\":1,\"context_after\":1}" 6)"
  assert_json "eba_get_paragraph returns paragraph citations" "$res" "
assert payload['answerability'] == 'exact'
assert len(payload['citations']) >= 1
assert any(citation['paragraph_ref'] == '$paragraph_ref' for citation in payload['citations'])
" true

  res="$(call_tool "$db_path" "eba_get_section" "{\"eba_id\":\"$paragraph_eba_id\",\"section\":\"$section_ref\",\"limit\":20}" 61)"
  assert_json "eba_get_section returns section citations" "$res" "
assert payload['answerability'] == 'exact'
assert payload['section'] == '$section_ref'
assert payload['total_chunks'] == len(payload['citations'])
assert len(payload['citations']) >= 1
assert any((citation.get('paragraph_ref') or '').startswith('$section_ref') for citation in payload['citations'])
" true

  res="$(call_tool "$db_path" "eba_get_toc" "{\"eba_id\":\"$paragraph_eba_id\",\"limit\":20}" 62)"
  assert_json "eba_get_toc returns outline entries" "$res" "
assert payload['answerability'] == 'exact'
assert payload['total'] == len(payload['toc'])
assert len(payload['toc']) >= 1
first = payload['toc'][0]
for key in ('section_path', 'paragraph_refs', 'first_sequence_no', 'last_sequence_no', 'chunk_count'):
    assert key in first
" true

  res="$(call_tool "$db_path" "eba_search" '{"query":"xyznonexistent999","limit":3}' 7)"
  assert_json "eba_search handles nonsense query gracefully" "$res" "
assert payload['answerability'] in ('partial', 'no_match')
assert isinstance(payload['citations'], list)
" 

  res="$(call_tool "$db_path" "eba_search" '{"query":"consultation responses","filters":{"exclude_consultation_responses":true},"limit":10}' 71)"
  assert_json "eba_search can exclude consultation response sections" "$res" "
assert payload['filters_applied']['exclude_consultation_responses'] == True
for citation in payload['citations']:
    section = citation.get('section_path', '').lower()
    assert 'feedback on' not in section or 'consultation' not in section, section
    assert 'summary of responses' not in section or 'consultation' not in section, section
    assert 'public consultation' not in section, section
    assert 'analysis of responses' not in section, section
" true

  # --- M4 new tools ---

  res="$(call_tool "$db_path" "eba_get_status" "{\"eba_id\":\"$status_eba_id\"}" 8)"
  assert_json "eba_get_status returns status for indexed doc" "$res" "
assert payload['answerability'] in ('exact', 'partial')
status = payload.get('status', {})
assert status.get('eba_id') == '$status_eba_id'
if int('$has_amends_relationships') > 0:
    assert len(status.get('amended_by', [])) > 0, f'expected amended_by non-empty, got {status}'
" true

  res="$(call_tool "$db_path" "eba_get_status" "{\"eba_id\":\"$first_eba_id\"}" 9)"
  assert_json "eba_get_status with clean doc returns empty relationships" "$res" "
assert payload['answerability'] in ('exact', 'partial')
status = payload.get('status', {})
assert status.get('is_superseded', False) == False
" true

  res="$(call_tool "$db_path" "eba_get_status" '{"eba_id":"EBA/GL/9999/99"}' 10)"
  assert_json "eba_get_status unknown eba_id returns no_match" "$res" "
assert payload['answerability'] == 'no_match'
" true

  res="$(call_tool "$db_path" "eba_get_versions" "{\"eba_id\":\"$first_eba_id\"}" 11)"
  assert_json "eba_get_versions returns versions array" "$res" "
assert payload['answerability'] in ('exact', 'partial')
assert isinstance(payload.get('versions', []), list)
assert len(payload.get('versions', [])) > 0
" true

  res="$(call_tool "$db_path" "eba_get_versions" '{"eba_id":"EBA/GL/9999/99"}' 12)"
  assert_json "eba_get_versions unknown eba_id returns no_match" "$res" "
assert payload['answerability'] == 'no_match'
" true

  local real_chunk_id
  real_chunk_id="$(sqlite_value "$db_path" "
    SELECT chunk_id
    FROM chunks
    WHERE chunk_id LIKE '%.%'
    ORDER BY chunk_id
    LIMIT 1
  ")"

  res="$(call_tool "$db_path" "eba_validate_citation" "{\"chunk_id\":\"$real_chunk_id\"}" 13)"
  assert_json "eba_validate_citation with real chunk_id returns valid=true" "$res" "
validation = payload.get('validation', {})
assert validation.get('valid') == True, f'expected valid=True, got {validation}'
" true

  res="$(call_tool "$db_path" "eba_validate_citation" '{"chunk_id":"nonexistent-chunk-id-xyz"}' 14)"
  assert_json "eba_validate_citation with fake chunk_id returns valid=false" "$res" "
validation = payload.get('validation', {})
assert validation.get('valid') == False, f'expected valid=False, got {validation}'
" true

  res="$(call_tool "$db_path" "eba_diff_versions" "{\"eba_id\":\"$first_eba_id\",\"version_a\":\"1.0\",\"version_b\":\"99.0\"}" 15)"
  assert_json "eba_diff_versions with missing version returns error" "$res" "
assert payload.get('answerability') == 'error', f'expected error answerability, got {payload.get(\"answerability\")}'
"
}

create_empty_db() {
  local empty_db="$1"

  python3 - "$SCHEMA_SQL" "$empty_db" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

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

run_empty_db_checks() {
  local temp_db
  temp_db="$(mktemp /tmp/empty-eba-XXXXXX.db)"
  trap 'rm -f "$temp_db"' EXIT

  create_empty_db "$temp_db"

  local res
  res="$(call_tool "$temp_db" "eba_corpus_info" '{}' 101)"
  assert_json "empty DB eba_corpus_info returns zero counts" "$res" "
assert payload['answerability'] == 'exact'
assert payload['corpus_info']['document_count'] == 0
assert payload['corpus_info']['chunk_count'] == 0
" 

  res="$(call_tool "$temp_db" "eba_search" '{"query":"money laundering","limit":3}' 102)"
  assert_json "empty DB eba_search returns no_match" "$res" "
assert payload['answerability'] == 'no_match'
assert payload['citations'] == []
assert payload['documents_considered'] == []
" 

  res="$(call_tool "$temp_db" "eba_get_toc" '{"eba_id":"EBA/GL/9999/99"}' 103)"
  assert_json "empty DB eba_get_toc returns no_match" "$res" "
assert payload['answerability'] == 'no_match'
assert payload['citations'] == []
"

  res="$(call_tool "$temp_db" "eba_get_section" '{"eba_id":"EBA/GL/9999/99","section":"4"}' 104)"
  assert_json "empty DB eba_get_section returns no_match" "$res" "
assert payload['answerability'] == 'no_match'
assert payload['citations'] == []
"

  rm -f "$temp_db"
  trap - EXIT
}

build_project

if [ "$MODE" = "all" ] || [ "$MODE" = "real" ]; then
  run_real_db_checks "$DB"
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "empty" ]; then
  run_empty_db_checks
fi

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$TOOL_FAIL" -eq 0 ] && [ "$TOOL_PASS" -ge 15 ] && [ "$FAIL" -eq 0 ]; then
  echo "${TOOL_PASS}/${TOOL_PASS} tool tests passed"
  exit 0
fi

exit 1
