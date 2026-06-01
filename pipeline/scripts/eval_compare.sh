#!/usr/bin/env bash
# eval_compare.sh — Run hybrid vs FTS-only comparison and produce markdown report.
#
# Usage:
#   ./pipeline/scripts/eval_compare.sh [--db <path>] [--tags <tags>] [--output <path>]
#
# Defaults:
#   --db      data/atlas-real-vec.db
#   --tags    semantic
#   --output  .sisyphus/evidence/task-10-eval-comparison.md

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB="${1:-$REPO_ROOT/data/atlas-real-vec.db}"
TAGS="semantic"
OUTPUT="$REPO_ROOT/.sisyphus/evidence/task-10-eval-comparison.md"

for arg in "$@"; do
  case "$arg" in
    --db=*) DB="${arg#--db=}" ;;
    --tags=*) TAGS="${arg#--tags=}" ;;
    --output=*) OUTPUT="${arg#--output=}" ;;
  esac
done

echo "=== EBA MCP Eval Comparison ==="
echo "DB:     $DB"
echo "Tags:   $TAGS"
echo "Output: $OUTPUT"
echo ""

cd "$REPO_ROOT"

uv run --project pipeline python pipeline/scripts/eval_compare.py \
  --db "$DB" \
  --queries pipeline/eba_pipeline/eval/queries.yaml \
  --tags "$TAGS" \
  --output "$OUTPUT"

echo ""
echo "Done. Report at: $OUTPUT"
