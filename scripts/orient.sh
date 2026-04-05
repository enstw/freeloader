#!/usr/bin/env bash
# orient.sh — single entry point for "where am I" in the jRouter build loop.
#
# Prints STATUS, recent lessons/surprises, runs the current phase gate, and
# tells you what to do next. Run this before touching any code.

set -uo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
dim()  { printf '\033[2m%s\033[0m\n' "$*"; }
rule() { printf -- '─%.0s' $(seq 1 72); printf '\n'; }

if [[ ! -f STATUS.md ]]; then
  echo "fatal: STATUS.md missing. Loop is not initialized." >&2
  exit 2
fi
if [[ ! -f JOURNAL.jsonl ]]; then
  echo "fatal: JOURNAL.jsonl missing. Loop is not initialized." >&2
  exit 2
fi

rule
bold "STATUS"
rule
cat STATUS.md
echo

current_phase="$(grep -oE '## Phase: [0-9]+' STATUS.md | head -1 | grep -oE '[0-9]+' || echo "")"
if [[ -z "$current_phase" ]]; then
  echo "warning: could not parse current phase from STATUS.md" >&2
  current_phase=1
fi

rule
bold "RECENT LESSONS (last 10)"
rule
grep '"kind":"lesson"' JOURNAL.jsonl | tail -10 \
  | sed -E 's/.*"subject":"([^"]*)".*"text":"([^"]*)".*/  • \1: \2/' \
  || echo "  (none yet)"
echo

rule
bold "RECENT SURPRISES (last 5)"
rule
grep '"kind":"surprise"' JOURNAL.jsonl | tail -5 \
  | sed -E 's/.*"text":"([^"]*)".*/  • \1/' \
  || true
if ! grep -q '"kind":"surprise"' JOURNAL.jsonl 2>/dev/null; then
  dim "  (none yet)"
fi
echo

rule
bold "GATE — phase ${current_phase}"
rule
gate_script="scripts/gate_${current_phase}.sh"
if [[ -x "$gate_script" ]]; then
  if "$gate_script"; then
    bold "  ✓ gate ${current_phase} GREEN"
    gate_status=0
  else
    bold "  ✗ gate ${current_phase} RED"
    gate_status=1
  fi
else
  echo "  no executable gate at ${gate_script}" >&2
  gate_status=2
fi
echo

rule
bold "NEXT ACTION"
rule
if [[ ${gate_status} -eq 0 ]]; then
  echo "  Gate is green. Proceed with the 'Task:' line in STATUS.md."
  echo "  When the current step's exit criteria are all met, run:"
  echo "    scripts/advance.sh"
elif [[ ${gate_status} -eq 1 ]]; then
  echo "  Gate is red. The failing assertion IS the next task."
  echo "  Do not start unrelated work until the gate is green."
else
  echo "  Gate is not runnable yet. Current task is scaffolding."
  echo "  Complete the Exit criteria in STATUS.md, then re-run orient."
fi
echo
