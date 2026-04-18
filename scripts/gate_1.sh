#!/usr/bin/env bash
# gate_1.sh — ClaudeAdapter, non-streaming, single conversation.
# Exit criteria: ROADMAP.md § Phase 1.

set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/gate_common.sh

gate_common_invariants

echo "─ phase 1 specific ─"

# Phase 1 work has not begun until pyproject.toml and src/freeloader/ exist.
# Until then this gate deliberately reports the work that remains.
gate_check "pyproject.toml exists"                              test -f pyproject.toml
gate_check "src/freeloader/__init__.py exists"                     test -f src/freeloader/__init__.py
gate_check "src/freeloader/adapters/claude.py exists"              test -f src/freeloader/adapters/claude.py
gate_check "src/freeloader/frontend/app.py exists"                 test -f src/freeloader/frontend/app.py
gate_check "src/freeloader/canonical/history_diff.py exists"       test -f src/freeloader/canonical/history_diff.py

# End-to-end behaviors.
gate_check "end-to-end curl test: 2-turn context carries over" \
  test -f tests/e2e/test_claude_two_turn.py
gate_check "golden JSONL fixture replay for ClaudeAdapter" \
  test -f tests/adapters/test_claude_golden.py
gate_check "scratch cwd sandbox test" \
  test -f tests/adapters/test_claude_sandbox.py
gate_check "client-sent tools are stripped with warning" \
  test -f tests/frontend/test_tools_stripped.py
gate_check "history_diff unit test exists" \
  test -f tests/canonical/test_history_diff.py

gate_report 1
