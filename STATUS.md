# FreelOAder status — 2026-04-25

## Phase: 4/5 — quota tracking + threshold switching
## Step: 4.4 — thresholds come from freeloader.toml

Purpose (why this step exists):
  Step 4.2a introduced two placeholder constants in
  `src/freeloader/router.py` (`INFERENCE_WINDOW_SECONDS=300`,
  `INFERENCE_TOKENS_THRESHOLD=1_000_000`) with the comment "4.4 moves
  these to freeloader.toml (PLAN decision #10)." This step removes
  that "operator must edit Python source" anti-pattern: thresholds
  become declarative, sit in a config file, and survive a
  re-install. The Router constructor already accepts the values via
  kwargs (router.py:76-77) — 4.4 only changes *where the values come
  from* before construction. It also closes the last red square in
  `gate_4.sh` so phase 4 can advance.

  ROADMAP § Phase 4: "Thresholds and weights come from
  `freeloader.toml`."

Scope clarification — "thresholds and weights":
  ROADMAP says "thresholds and weights." Only thresholds exist as a
  concept today; `QuotaAwareStrategy` has no weight notion (it's a
  cursor-rotating skip-if-pressured picker). Configuring a thing
  that doesn't exist is wrong order — weights wait until weighted
  strategy lands. 4.4 ships thresholds only.

Design (one sentence per file):
  - `src/freeloader/config.py` — add `load_router_config()` returning
    a dict of Router kwargs. Resolution order:
      1. `FREELOADER_CONFIG` env var (explicit override; fail loud
         if the path is set but unreadable)
      1. `$cwd/freeloader.toml` (project-local config)
      1. `<data_dir>/freeloader.toml` (user-global, via the existing
         `resolve_data_dir()`)
      1. built-in defaults matching the placeholder constants
    Uses stdlib `tomllib` (Python 3.11+, no new dep). Malformed toml
    → raises `RouterConfigError` with the source path. A typo
    silently using defaults is exactly the bug-class config files
    are supposed to prevent.
  - `src/freeloader/router.py` — delete the
    `INFERENCE_WINDOW_SECONDS` / `INFERENCE_TOKENS_THRESHOLD`
    module-level constants; defaults move into `load_router_config()`
    so there's a single source of truth. Router constructor
    signature unchanged (kwargs still default to `None` and the
    instance still keeps a `_inference_window_seconds` /
    `_inference_tokens_threshold` field — tests inject their own).
  - `src/freeloader/frontend/app.py:76` — change
    `r = router or Router()` to
    `r = router or Router(**load_router_config())` so the running
    server picks up the toml.
  - `tests/core/test_config_thresholds.py` (NEW, the file gate_4
    looks for) covers:
      * defaults returned when no toml present and no env override
      * `FREELOADER_CONFIG` pointing at a temp toml — values land
      * partial toml (only one of the two keys set) — other key
        falls back to default
      * malformed toml → raises with a clear message
      * `FREELOADER_CONFIG` set to a missing path → raises (loud,
        not silent fallback to defaults — explicit override means
        the operator wanted those values)
      * `$cwd/freeloader.toml` is preferred over `<data_dir>` when
        both exist
      * integration: `create_app()` constructs a Router whose
        `_inference_window_seconds` / `_inference_tokens_threshold`
        match values read from `$cwd/freeloader.toml`

Schema (flat — single section, no per-provider override):
  ```toml
  [router]
  inference_window_seconds = 300
  inference_tokens_threshold = 1_000_000
  ```
  Single window+threshold applied to all inferred providers (codex,
  gemini). Per-provider overrides are deferred — PLAN doesn't ask
  for them; adding now would be premature.

Why fail-loud on malformed toml (not silent default):
  A typo in `freeloader.toml` silently using defaults would be
  exactly the bug where you spend an hour wondering why your
  threshold change had no effect. This config file's whole job is
  to be a knob the operator turns; if the knob is jammed, fail
  visibly. (Same reasoning as 4.1 — surface the surprising state.)

Why not load config in `Router.__init__`:
  The Router doesn't know its config source; that's the caller's
  job (production = `frontend/app.py`, tests = explicit kwargs).
  Pushing toml-loading into the constructor would force every test
  to either tolerate or stub a filesystem read. Keep the seam at
  the call site.

Exit criteria for step 4.4:
  - [ ] `src/freeloader/config.py` has `load_router_config()` with
        the resolution order above, returns a dict suitable for
        `Router(**...)`, raises `RouterConfigError` (or stdlib
        equivalent) on malformed toml or missing `FREELOADER_CONFIG`
        path.
  - [ ] `src/freeloader/router.py` no longer defines
        `INFERENCE_WINDOW_SECONDS` / `INFERENCE_TOKENS_THRESHOLD`
        as module-level constants; defaults live in `config.py`.
  - [ ] `src/freeloader/frontend/app.py` calls
        `load_router_config()` when constructing the default
        Router.
  - [ ] `tests/core/test_config_thresholds.py` covers all six
        cases listed in Design.
  - [ ] All existing tests still green (218 → ~225 expected).
  - [ ] ruff check + ruff format clean.
  - [ ] `gate_4.sh` fully GREEN (closes the last red square,
        unblocks `phase_done` for phase 4).

Out of scope for 4.4 (deferred):
  - Per-provider threshold overrides (`[router.codex]
    inference_tokens_threshold = ...`). PLAN doesn't ask; codex and
    gemini share one threshold today. Trivially additive later.
  - Strategy weights — no weight concept exists in
    `QuotaAwareStrategy`. Lands with weighted strategy if/when.
  - Adapter list / model-name routing config from PLAN.md line 674.
    Separate config surface, not what gate_4 checks.
  - Runtime config reload — PLAN.md line 677 explicitly: "No
    runtime config reloading in the MVP."
  - 4.2b (gemini/codex 429 detection) — independent; can land
    after phase 4.

Phase 4 sketch:
  - 4.1 claude rate_limit_event → quota_signal events ✅
  - 4.2a gemini/codex token-window inference ✅
  - 4.2b gemini/codex 429 detection (adapter stderr work) — deferred
    past phase 4 (additive; consumed transparently by 4.3 + 4.5)
  - 4.3 Quota-aware Strategy reading the derived view ✅
  - 4.4 Threshold config in freeloader.toml (this step).
  - 4.5 Replay test ✅

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
  - 4.5 fixture FILES (not inline literals) prove the replay
    consumer parses bytes the same way the production journal
    would be re-read; canonical-builder shape drift breaks replay
    loud (a desirable forcing function)
