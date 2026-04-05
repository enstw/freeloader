#!/usr/bin/env bash
# advance.sh — phase transition enforcer.
#
# Refuses to advance unless:
#   1. The current phase's gate exits 0
#   2. STATUS.md has been updated in the same commit as this advance
#   3. A phase_done entry is appended to JOURNAL.jsonl with the commit sha
#
# Usage:
#   scripts/advance.sh            # advance from current to current+1
#   scripts/advance.sh 3          # explicitly advance to phase 3

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

current_phase="$(grep -oE '## Phase: [0-9]+' STATUS.md | head -1 | grep -oE '[0-9]+')"
if [[ -z "${current_phase:-}" ]]; then
  echo "error: cannot parse current phase from STATUS.md" >&2
  exit 2
fi

target_phase="${1:-$((current_phase + 1))}"

if (( target_phase != current_phase + 1 )); then
  echo "error: can only advance one phase at a time (current=${current_phase}, target=${target_phase})" >&2
  exit 2
fi

if (( target_phase < 1 || target_phase > 5 )); then
  echo "error: target phase ${target_phase} out of range [1,5]" >&2
  exit 2
fi

echo "→ running gate for phase ${current_phase}..."
if ! "scripts/gate_${current_phase}.sh"; then
  echo "error: gate ${current_phase} is RED — cannot advance" >&2
  exit 1
fi
echo "✓ gate ${current_phase} green"

# Require STATUS.md to have been touched in the working tree or a recent commit.
if ! git diff --cached --name-only | grep -q '^STATUS\.md$'; then
  if ! git diff --name-only | grep -q '^STATUS\.md$'; then
    echo "warning: STATUS.md has no uncommitted changes." >&2
    echo "  Advance means updating STATUS.md to point at phase ${target_phase}." >&2
    echo "  Update STATUS.md first, then re-run this script." >&2
    exit 1
  fi
fi

# Require a clean git state except for STATUS.md so the advance commit is atomic.
dirty_other="$(git status --porcelain | grep -v ' STATUS\.md$' | grep -v ' JOURNAL\.jsonl$' || true)"
if [[ -n "$dirty_other" ]]; then
  echo "error: other files are dirty — advance commit must only touch STATUS.md and JOURNAL.jsonl" >&2
  echo "$dirty_other" >&2
  exit 1
fi

commit_sha="$(git rev-parse HEAD)"
scripts/reflect.sh phase_done "phase ${current_phase} complete" \
  phase="${current_phase}" commit="${commit_sha}"
scripts/reflect.sh phase_start "begin phase ${target_phase}" \
  phase="${target_phase}" source="ROADMAP.md#phase-${target_phase}"

echo
echo "→ advanced to phase ${target_phase}."
echo "  Next: commit STATUS.md + JOURNAL.jsonl together with message:"
echo "    [${target_phase}.0] advance to phase ${target_phase}"
