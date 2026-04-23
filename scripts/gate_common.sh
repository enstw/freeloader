#!/usr/bin/env bash
# gate_common.sh — invariants enforced at every phase boundary.
#
# Sourced by gate_1.sh ... gate_5.sh. Each phase gate runs these first,
# then phase-specific checks.

set -uo pipefail

_gate_fail=0
_gate_note() { printf '  [%s] %s\n' "$1" "$2"; }

gate_check() {
  # gate_check "description" command...
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    _gate_note "ok" "$desc"
  else
    _gate_note "FAIL" "$desc"
    _gate_fail=1
  fi
}

gate_check_soft() {
  # like gate_check but only warns — used for checks that don't apply yet
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    _gate_note "ok" "$desc"
  else
    _gate_note "skip" "$desc (not applicable yet)"
  fi
}

gate_common_invariants() {
  echo "─ common invariants ─"

  # Loop artifacts must exist and be coherent.
  gate_check "STATUS.md exists" test -f STATUS.md
  gate_check "JOURNAL.jsonl exists" test -f JOURNAL.jsonl
  gate_check "ROADMAP.md exists" test -f ROADMAP.md
  gate_check "PLAN.md exists" test -f PLAN.md
  gate_check "AGENT.md exists" test -f AGENT.md

  # JOURNAL.jsonl is append-only: check it parses as JSONL.
  gate_check "JOURNAL.jsonl is valid JSONL" \
    python3 -c "import json,sys; [json.loads(l) for l in open('JOURNAL.jsonl') if l.strip()]"

  # No rewrites of past journal lines in this commit (if git history has >1 commit).
  if git rev-parse HEAD~1 >/dev/null 2>&1; then
    gate_check "JOURNAL.jsonl is append-only vs HEAD~1" bash -c '
      git show HEAD~1:JOURNAL.jsonl 2>/dev/null > /tmp/freeloader_journal_prev || exit 0
      head -n "$(wc -l < /tmp/freeloader_journal_prev)" JOURNAL.jsonl \
        | diff -q - /tmp/freeloader_journal_prev >/dev/null
    '
  fi

  # Python hygiene — only enforced once pyproject.toml exists.
  # All project tooling is invoked via `uv run` so the gate matches
  # the repo-wide uv-first rule (no global pip/pipx dependency).
  if [[ -f pyproject.toml ]]; then
    gate_check "ruff check clean" uv run ruff check src tests
    gate_check "ruff format clean" uv run ruff format --check src tests
    gate_check "pytest green" uv run pytest -q
    gate_check "src/freeloader/ package root exists" test -d src/freeloader
  else
    _gate_note "skip" "python checks (no pyproject.toml yet)"
  fi
}

gate_report() {
  local phase="$1"
  if [[ $_gate_fail -eq 0 ]]; then
    echo "─ gate ${phase}: GREEN ─"
    return 0
  else
    echo "─ gate ${phase}: RED ─" >&2
    return 1
  fi
}
