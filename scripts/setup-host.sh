#!/usr/bin/env bash
# scripts/setup-host.sh
#
# One-shot setup for a dedicated FreelOAder host.
#
# WARNING: This nullifies agent memory globally for the running user.
# DO NOT run on a workstation; intended for hosts where the only consumer
# of claude/codex/gemini is FreelOAder itself.
#
# What this script does:
#   1. Symlinks ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ~/.gemini/GEMINI.md
#      to /dev/null. The CLIs read 0 bytes for memory_paths → no memory.
#   2. Empties ~/.codex/memories/ and ~/.gemini/memory/ (the separate
#      memory directories the CLIs also read from).
#
# Idempotent: safe to re-run. ln -sfn replaces existing symlinks.

set -euo pipefail

if [[ "${1:-}" != "--yes" ]]; then
  cat <<EOF
This script is destructive to local CLI agent memory. It will:

  - symlink \$HOME/.claude/CLAUDE.md  -> /dev/null
  - symlink \$HOME/.codex/AGENTS.md   -> /dev/null
  - symlink \$HOME/.gemini/GEMINI.md  -> /dev/null
  - empty \$HOME/.codex/memories
  - empty \$HOME/.gemini/memory

Only run on a dedicated FreelOAder host. Re-run with --yes to proceed.
EOF
  exit 1
fi

for f in "$HOME/.claude/CLAUDE.md" "$HOME/.codex/AGENTS.md" "$HOME/.gemini/GEMINI.md"; do
  mkdir -p "$(dirname "$f")"
  ln -sfn /dev/null "$f"
  echo "linked $f -> /dev/null"
done

for d in "$HOME/.codex/memories" "$HOME/.gemini/memory"; do
  rm -rf "$d"
  mkdir -p "$d"
  echo "emptied $d"
done

echo "FreelOAder host setup complete."
