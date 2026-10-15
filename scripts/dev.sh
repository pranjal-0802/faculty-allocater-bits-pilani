#!/usr/bin/env bash
# Developer entry points. Run from anywhere:
#   scripts/dev.sh import [workbook]   build data/store problem JSON from the ME workbook
#   scripts/dev.sh solve  [problem]    solve it and print the report
#   scripts/dev.sh serve               start the API + frontend on :8100
#   scripts/dev.sh test                run the test suite
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT"

WORKBOOK_DEFAULT="$ROOT/data/raw/Course allocation sem-II, 2025-26.xlsx"
PROBLEM_DEFAULT="$ROOT/data/examples/me-2025-26-sem2.json"

case "${1:-}" in
  import)
    mkdir -p "$ROOT/data/examples"
    "$PY" -m allocator.cli import "${2:-$WORKBOOK_DEFAULT}" --department ME \
      --semester "2025-26 Sem II" --name "ME Sem II 2025-26" -o "$PROBLEM_DEFAULT" ;;
  solve)
    "$PY" -m allocator.cli solve "${2:-$PROBLEM_DEFAULT}" --time-limit "${TIME_LIMIT:-30}" ;;
  reference)
    "$PY" -m allocator.cli reference "${2:-$PROBLEM_DEFAULT}" ;;
  serve)
    exec "$PY" -m uvicorn allocator.api.main:app --host 0.0.0.0 --port "${PORT:-8100}" --reload --reload-dir "$ROOT/backend" ;;
  test)
    exec "$PY" -m pytest -q "$ROOT/backend/tests" "${@:2}" ;;
  *)
    sed -n '2,7p' "$0"; exit 1 ;;
esac
