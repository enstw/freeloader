# FreelOAder status — 2026-04-25

## Phase: 4/5 — quota tracking + threshold switching
## Step: 4.5 — deterministic routing replay over fixture JOURNAL

Purpose (why this step exists):
  Step 4.3 built `QuotaAwareStrategy` with no clock dependency
  precisely so this step could prove replay determinism: given a
  fixture `JOURNAL.jsonl`, the strategy produces the same routing
  decisions every time. That's the foundation for offline analysis
  ("what would the router have done given that history?") and the
  durable safety check for any future strategy refinement — if a
  later change to pressure-folding accidentally introduces clock
  reads or randomness, this test fails.

  ROADMAP § Phase 4: "Replay test: given a fixture JOURNAL, the
  router makes deterministic routing decisions — no wall-clock
  dependency."

Why this is 4.5, not 4.4:
  4.4 (`freeloader.toml` thresholds) doesn't yet exist; the
  inference threshold is still a placeholder constant. But the
  Strategy doesn't read thresholds — it consumes pre-computed
  status from quota_signal events. So replay determinism is
  testable today without 4.4. Building 4.5 first proves the seam
  is clean before config introduces a new failure mode.

Design (one sentence per file):
  - `tests/core/fixtures/routing_replay/realistic_session.jsonl` —
    a hand-crafted JOURNAL of mixed `quota_signal` and `turn_done`
    records: claude five_hour goes allowed → exceeded → allowed,
    codex inferred_window goes allowed → exceeded, gemini stays
    allowed throughout. Byte-shape matches what `build_quota_signal`
    / `build_quota_signal_from_usage` actually produce so a future
    canonical-shape change breaks this fixture (a desirable forcing
    function — replay must consume the real shape).
  - `tests/core/fixtures/routing_replay/all_pressured.jsonl` —
    every provider exceeded simultaneously; exercises the
    "deterministic fallback" path (PLAN principle #5: signal not
    gate; never refuse service).
  - `tests/core/test_routing_replay.py` — loads each fixture
    line-by-line, replays through a fresh `QuotaAwareStrategy`
    via `observe(event)`, and asserts:
      * documented checkpoint decisions (after replaying records
        [0..N], `pick(order)` returns provider X)
      * idempotence — the same fixture replayed twice with two
        fresh strategies produces identical decision sequences
      * non-quota events ignored — feeding the same fixture with
        all `turn_done` records stripped produces equivalent
        strategy state
      * real-builder shape — events synthesized via the canonical
        builders are valid replay input (catches drift if
        `build_quota_signal*` shape ever diverges from the JSONL
        consumer)

Why fixture FILES (not inline literals):
  ROADMAP says "fixture JOURNAL." A real `.jsonl` file proves the
  consumer doesn't depend on Python object identity — it parses
  bytes the same way the production journal would be re-read. If
  this test passed with inline dicts but failed with bytes, we'd
  have a silent gap.

Why NOT through the Router:
  4.3's integration tests already cover Router→Strategy wiring.
  4.5's claim is purely about strategy replay determinism. Going
  through Router would re-test what 4.3 verified and add adapter
  scaffolding noise. Pure-strategy replay is the right scope.

Exit criteria for step 4.5:
  - [ ] `tests/core/fixtures/routing_replay/realistic_session.jsonl`
        and `tests/core/fixtures/routing_replay/all_pressured.jsonl`
        exist, valid JSONL, each record matches the canonical
        `quota_signal` shape (or a `turn_done`-style filler).
  - [ ] `tests/core/test_routing_replay.py` covers:
          * documented checkpoint decisions for both fixtures
          * idempotence: two fresh strategies, same fixture →
            identical pick() sequences
          * non-quota records ignored: stripped vs full fixture
            yield equivalent state
          * real-builder shape: events from `build_quota_signal` /
            `build_quota_signal_from_usage` are valid input
  - [ ] All existing tests still green (207 → ~213 expected).
  - [ ] ruff check + ruff format clean.
  - [ ] gate_4.sh's "deterministic routing replay over fixture
        JOURNAL" check passes (file existence — does not
        introspect contents).

Out of scope for 4.5 (deferred):
  - Binding replay (rebuild conversation→provider bindings from
    `turn_done` records). Separate concern; not required by
    gate_4. Could be a future step if cold-start needs to
    reconstitute bindings.
  - Wall-clock decay / `resets_at` consultation — explicitly
    excluded from 4.3, still excluded.
  - 4.4 (`freeloader.toml` thresholds) — Strategy doesn't read
    thresholds; 4.4 lands separately.
  - 4.2b (gemini/codex 429 detection) — adapter stderr work.
    When 4.2b lands and emits `quota_signal` records, replay
    will consume them transparently — no test change needed.

Phase 4 sketch:
  - 4.1 claude rate_limit_event → quota_signal events ✅
  - 4.2a gemini/codex token-window inference ✅
  - 4.2b gemini/codex 429 detection (adapter stderr work) — TODO
  - 4.3 Quota-aware Strategy reading the derived view ✅
  - 4.4 Threshold + weight config in freeloader.toml — TODO
  - 4.5 Replay test (this step).

Recent lessons (see JOURNAL.jsonl for full text):
  - claude -p exits 0 on rate_limit; inspect events
  - cold cache tax 6k–14k input tokens; warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini compound provider (stats.models per turn); UUID
    session_id (spike drift)
  - GEMINI_CLI_HOME couples auth to state — per-adapter mutex
    fallback per PLAN decision #16
  - codex exec resume rejects -s; sandbox set on first turn persists
  - codex --json emits pure JSONL on stdout; no model id field
  - asyncio create_subprocess_exec env=dict REPLACES env entirely
  - Starlette aclose() injects GeneratorExit, not CancelledError
  - json.dumps default separators differ from OpenAI wire bytes
  - Router._bindings is now (provider, sid|None); None pins for
    replay (mid-conversation provider switch)
  - 4.1 frozen quota_signal shape: rate_limit_type/status/resets_at/
    overage_status/raw — sibling builders must produce identical
    shape so 4.3 reads ONE stream
  - 4.2a inferred token-window pressure for codex/gemini; in-memory
    rolling window per provider; same canonical shape with
    rate_limit_type="inferred_window"
  - 4.3 Strategy.observe(event) is fed only after a successful
    journal write — keeps strategy view aligned with the durable
    record so a restart that re-reads JOURNAL gives the same state
