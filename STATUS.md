# FreelOAder status — 2026-04-25

## Phase: 3/5 — CodexAdapter + GeminiAdapter + round-robin
## Step: 3.7 — Cross-adapter contract suite + phase-3 close
## Task: extract the round-robin selection logic into
         `src/freeloader/core/routing/round_robin.py` behind a
         simple Strategy Protocol (so phase-4 quota-aware routing
         plugs into the same seam), add a forwarding shim at
         `src/freeloader/core/router.py`, and write
         `tests/adapters/test_contract_all.py` — a parametrized
         suite asserting the canonical-contract invariants that
         must hold across every CLIAdapter implementation. Then
         close gate_3 and advance.

Purpose (why this step exists):
  Phases 3.1–3.6 produced three concrete adapters and the routing
  plumbing around them. Step 3.7 is the protocol-level closure:
  prove that the canonical Delta contract holds *uniformly* across
  every adapter, not just one-at-a-time via per-adapter golden
  tests. If a fourth adapter ever lands, the contract suite catches
  drift on day one. The round-robin extraction is structurally the
  same move: phase 4 will add quota-aware routing, and pulling the
  selection strategy out of Router now means phase 4 plugs in,
  rather than rewires.

Step 3.7 exit criteria:
  - [ ] `src/freeloader/core/routing/__init__.py` exists.
  - [ ] `src/freeloader/core/routing/round_robin.py` exists with
        a `RoundRobinStrategy` class implementing `pick(order:
        list[str]) -> str` (or equivalent shape). Stateful: holds
        the cycle index across calls.
  - [ ] `src/freeloader/core/router.py` exists as a re-export shim
        for `freeloader.router.Router` (preserves existing imports;
        documents itself as a forwarding facade).
  - [ ] `freeloader.router.Router` delegates provider selection to
        an injected Strategy (default: `RoundRobinStrategy`).
        Existing round-robin behavior unchanged.
  - [ ] `tests/adapters/test_contract_all.py` exists and covers,
        parametrized over `(claude, codex, gemini)`:
        - `parse_stream(empty_async_iter)` yields nothing.
        - `parse_stream` of one malformed JSONL line yields exactly
          one `ErrorDelta(source="parse")` and continues.
        - `parse_stream` of blank-only lines yields nothing.
        - `map_event({"type": "<future_unknown>"})` yields exactly
          one `RawDelta`.
        - Each adapter's golden fixture parses to the canonical
          minimum sequence: at least one `SessionIdDelta`, then at
          least one `TextDelta`, then exactly one `FinishDelta`,
          then exactly one `UsageDelta`.
  - [ ] `scripts/gate_3.sh` exits 0 (gate 3 GREEN).
  - [ ] `phase_done` event in JOURNAL.jsonl with the phase-close
        commit sha; `phase_start` for phase 4 appended; STATUS.md
        rewritten to point at phase 4.

Out-of-scope for 3.7 (explicitly):
  - Live-CLI smoke harness for all three adapters under an env
    flag. Per phase-3 entry-criteria carry-forward, this is still
    "want to do" but not gating phase-3 close. The three adapters
    are exercised live during local development; the carry-forward
    will land in a phase-4 step once quota signals demand it.
  - Actually using the Strategy Protocol for anything other than
    round-robin (that's phase 4's job).
  - Refactoring all imports of `freeloader.router` to
    `freeloader.core.router`. The shim makes both work.

Phase 3 sketch:
  - 3.1 CodexAdapter ✅
  - 3.2 Per-conversation CLI state isolation env vars ✅
  - 3.3 GeminiAdapter — compound provider ✅
  - 3.4 Round-robin Router ✅
  - 3.5 Provider switch + canonical history replay ✅
  - 3.6 /v1/models endpoint ✅
  - 3.7 Cross-adapter contract suite + phase-3 close (this step).

Next phase: 4/5 — quota tracking + threshold switching.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 on rate_limit; inspect events
  - cold cache tax 6k–14k input tokens
  - agent-loop contamination observable not preventable under OAuth
  - gemini compound provider; UUID session_id (spike drift)
  - GEMINI_CLI_HOME couples auth to state — per-adapter mutex fallback
  - codex exec resume rejects -s
  - asyncio create_subprocess_exec env=dict REPLACES env entirely
  - Starlette aclose() injects GeneratorExit, not CancelledError
  - json.dumps default separators differ from OpenAI wire bytes
  - Router._bindings is now (provider, sid|None) — None pins for replay
