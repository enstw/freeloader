#!/usr/bin/env bash
# gate_3.sh — GeminiAdapter + CodexAdapter + round-robin routing.
# Exit criteria: ROADMAP.md § Phase 3.

set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/gate_common.sh

gate_common_invariants

echo "─ phase 3 specific ─"

gate_check "phase 2 gate still green" scripts/gate_2.sh

gate_check "GeminiAdapter exists" test -f src/jrouter/adapters/gemini.py
gate_check "CodexAdapter exists"  test -f src/jrouter/adapters/codex.py
gate_check "router module exists" test -f src/jrouter/core/router.py
gate_check "round-robin strategy exists" \
  test -f src/jrouter/core/routing/round_robin.py

gate_check "contract test suite runs against all 3 adapters" \
  test -f tests/adapters/test_contract_all.py
gate_check "provider-switch replay test" \
  test -f tests/core/test_rebind_replay.py
gate_check "/v1/models advertises 4 virtual names" \
  test -f tests/frontend/test_models_endpoint.py
gate_check "gemini compound-provider stats.models test" \
  test -f tests/adapters/test_gemini_compound.py

gate_report 3
