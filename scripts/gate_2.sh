#!/usr/bin/env bash
# gate_2.sh — streaming + cancellation.
# Exit criteria: ROADMAP.md § Phase 2.

set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/gate_common.sh

gate_common_invariants

echo "─ phase 2 specific ─"

# Phase 1 must still pass.
gate_check "phase 1 gate still green" scripts/gate_1.sh

gate_check "SSE streaming handler exists" \
  test -f src/freeloader/frontend/sse.py
gate_check "turn state machine module exists" \
  test -f src/freeloader/core/turn_state.py
gate_check "turn state machine unit tests" \
  test -f tests/core/test_turn_state.py
gate_check "disconnect test: no zombie subprocesses" \
  test -f tests/frontend/test_disconnect_no_zombies.py
gate_check "5-minute hard timeout test" \
  test -f tests/core/test_turn_timeout.py
gate_check "SSE byte-diff vs OpenAI reference" \
  test -f tests/frontend/test_sse_shape.py

gate_report 2
