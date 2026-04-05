#!/usr/bin/env bash
# gate_4.sh — quota tracking + threshold switching.
# Exit criteria: ROADMAP.md § Phase 4.

set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/gate_common.sh

gate_common_invariants

echo "─ phase 4 specific ─"

gate_check "phase 3 gate still green" scripts/gate_3.sh

gate_check "quota event log module exists" \
  test -f src/jrouter/core/quota.py
gate_check "quota-aware routing strategy exists" \
  test -f src/jrouter/core/routing/quota_aware.py

gate_check "claude rate_limit_event triggers switch within one turn" \
  test -f tests/core/test_quota_claude_rate_limit.py
gate_check "gemini/codex 429 + token-window inference test" \
  test -f tests/core/test_quota_inference.py
gate_check "deterministic routing replay over fixture JOURNAL" \
  test -f tests/core/test_routing_replay.py
gate_check "thresholds come from jrouter.toml" \
  test -f tests/core/test_config_thresholds.py

gate_report 4
