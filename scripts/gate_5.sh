#!/usr/bin/env bash
# gate_5.sh — tool-call decision.
# Exit criteria: ROADMAP.md § Phase 5.

set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/gate_common.sh

gate_common_invariants

echo "─ phase 5 specific ─"

gate_check "phase 4 gate still green" scripts/gate_4.sh

# A written decision must exist in the journal with subject=tool_call_strategy.
gate_check "tool-call decision recorded in JOURNAL" bash -c '
  grep -q "\"kind\":\"decision\"" JOURNAL.jsonl \
    && grep -q "\"subject\":\"tool_call_strategy\"" JOURNAL.jsonl
'

gate_check "end-to-end tool_calls test exists" \
  test -f tests/e2e/test_tool_calls.py

gate_check "README.md documents tool-call mode and limits" bash -c '
  grep -qi "tool call" README.md
'

gate_report 5
