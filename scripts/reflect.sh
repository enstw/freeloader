#!/usr/bin/env bash
# reflect.sh — the only sanctioned writer to JOURNAL.jsonl.
#
# Usage:
#   scripts/reflect.sh <kind> <text> [key=value ...]
#
# Allowed kinds:
#   decision  — a choice made, with alternatives rejected
#   lesson    — something learned (usually the hard way)
#   surprise  — observed behavior that contradicts the plan or assumptions
#   step_start  step_done
#   phase_start phase_done
#
# Examples:
#   scripts/reflect.sh lesson "claude -p exits 0 on rate limit" \
#       severity=high subject=claude_exit_code
#   scripts/reflect.sh decision "use httpx disconnect callback" \
#       subject=cancellation why="only way to detect SSE drop pre-timeout"
#   scripts/reflect.sh step_done "1.1" phase=1 step=1.1

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <kind> <text> [key=value ...]" >&2
  exit 2
fi

kind="$1"; shift
text="$1"; shift

case "$kind" in
  decision|lesson|surprise|step_start|step_done|phase_start|phase_done) ;;
  *)
    echo "error: unknown kind '$kind'" >&2
    echo "allowed: decision lesson surprise step_start step_done phase_start phase_done" >&2
    exit 2
    ;;
esac

if [[ ! -f JOURNAL.jsonl ]]; then
  echo "error: JOURNAL.jsonl missing. Run from repo root and ensure loop is initialized." >&2
  exit 2
fi

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Build JSON using python for safe escaping (no jq dependency).
python3 - "$kind" "$text" "$ts" "$@" <<'PY' >> JOURNAL.jsonl
import json, sys
kind, text, ts, *rest = sys.argv[1:]
obj = {"ts": ts, "kind": kind, "text": text}
for kv in rest:
    if "=" not in kv:
        print(f"error: extra arg '{kv}' must be key=value", file=sys.stderr)
        sys.exit(2)
    k, v = kv.split("=", 1)
    obj[k] = v
print(json.dumps(obj, ensure_ascii=False))
PY

echo "appended: $kind — $text"
