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
# Use a python json scan instead of grep — reflect.sh writes whitespace-laden
# JSON ("kind": "decision") while early bootstrap entries are no-space; an
# anchored grep flips depending on which entry happened to land first.
gate_check "tool-call decision recorded in JOURNAL" python3 -c '
import json, sys
with open("JOURNAL.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("kind") == "decision" and rec.get("subject") == "tool_call_strategy":
            sys.exit(0)
sys.exit(1)
'

gate_check "end-to-end tool_calls test exists" \
  test -f tests/e2e/test_tool_calls.py

gate_check "README.md documents tool-call mode and limits" bash -c '
  grep -qi "tool call" README.md
'

gate_report 5
