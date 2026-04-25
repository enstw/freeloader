# FreelOAder status — 2026-04-25

## Phase: 3/5 — CodexAdapter + GeminiAdapter + round-robin
## Step: 3.3 — GeminiAdapter (compound provider)
## Task: implement src/freeloader/adapters/gemini.py: shell out via
         `gemini -p -o stream-json`, parse the {init, message,
         result} event stream, emit canonical Deltas with multi-model
         UsageDelta from stats.models, capture the server-assigned
         session_id (now a UUID — spike said integer index), resume
         via `--resume <id>`. State isolation: per-adapter
         asyncio.Lock; GEMINI_CLI_HOME is unusable (couples OAuth to
         state) — see "empirical findings" below.

Purpose (why this step exists):
  Gemini is the compound-provider outlier. Phase 3 is about
  flushing out what the `CLIAdapter` Protocol can express across
  vendors that genuinely differ. Codex (3.1) and claude have one
  model per turn; gemini auto-routes across sub-models within a
  single turn (live capture: a one-word reply touched both
  `gemini-2.5-flash-lite` and `gemini-3-flash-preview`). The
  UsageDelta union variant is already keyed by sub-model (PLAN
  principle #1), so the canonical layer is ready — what's new here
  is adapter-side: producing a UsageDelta with multiple entries
  from one `result.stats.models` event.

Empirical findings (to confirm in JOURNAL after step done):
  - `session_id` is a UUID string (e.g. `004e7cb9-…-1f1c59c9ab2f`),
    not the integer index PLAN's 2026-04-05 spike recorded. The
    canonical layer treats it as opaque so the spike-vs-reality
    drift doesn't break anything — but the session_id_shapes lesson
    needs an addendum.
  - `GEMINI_CLI_HOME` exists and redirects gemini's state root,
    but it redirects EVERYTHING — including `oauth_creds.json`,
    `google_accounts.json`, `settings.json`. PLAN's "OAuth
    inherits via process env, only state dir is redirected"
    invariant cannot be honored for gemini. Falling back to PLAN's
    documented alternative: per-provider serialization mutex.

Step 3.3 exit criteria (must all be true before step_done):
  - [ ] `src/freeloader/adapters/gemini.py` exists, mirroring
        adapters/{claude,codex}.py: `map_event`, `parse_stream`,
        `GeminiAdapter.send`. SIGTERM→3s→SIGKILL→rmtree finally.
  - [ ] `map_event` handles:
        - `init` → `SessionIdDelta(session_id)`.
        - `message` with `role=user` → `[]` (echo, not surfaced).
        - `message` with `role=assistant` → `TextDelta(content)`.
        - `result` with `status=success` → `FinishDelta(stop)` +
          `UsageDelta(models={each sub-model in stats.models})`.
        - `result` with `status` other than `success` →
          `FinishDelta(error)`.
        - Unknown event → `RawDelta`.
        - Malformed JSONL → `ErrorDelta(parse)`, stream continues.
  - [ ] `GeminiAdapter` carries `_send_lock = asyncio.Lock()` and
        `send()` acquires it for the duration of the subprocess —
        per-provider mutex per PLAN decision #16 fallback for CLIs
        without selective state isolation.
  - [ ] `GeminiAdapter.send` does NOT set GEMINI_CLI_HOME (auth
        coupling); inherits parent env unchanged for OAuth.
  - [ ] Golden fixture `tests/adapters/fixtures/gemini_basic.jsonl`
        exists (live-derived 2026-04-25, gemini-cli 0.39.0).
  - [ ] `tests/adapters/test_gemini_golden.py` green (mirrors
        test_codex_golden.py shape).
  - [ ] `tests/adapters/test_gemini_compound.py` green: a single
        `result` event with multiple `stats.models` entries
        produces a `UsageDelta` with one `ModelUsage` per entry
        — the compound-provider invariant.
  - [ ] `tests/adapters/test_gemini_serialization.py` green: two
        concurrent `GeminiAdapter.send()` calls serialize via the
        per-adapter lock; concurrent calls to *different* adapter
        instances do not block each other (lock is instance-scoped).
  - [ ] gate_2 still GREEN. Cross-phase invariants green.

Out-of-scope for 3.3 (deferred):
  - Symlinking auth files into a per-conversation GEMINI_CLI_HOME
    so gemini can have both isolated state AND inherited auth — too
    fragile for the value (per-conversation gemini concurrency is a
    rare workload for a personal proxy).
  - Round-robin Router across the three adapters (3.4).
  - Provider-switch replay (3.5); /v1/models (3.6); contract
    suite (3.7); live-CLI smoke harness (3.7-ish).

Phase 3 sketch:
  - 3.1 CodexAdapter ✅
  - 3.2 Per-conversation CLI state isolation env vars ✅
  - 3.3 GeminiAdapter — compound provider, stats.models per turn (this step).
  - 3.4 Round-robin Router (cycles providers per new conversation).
  - 3.5 Provider-switch mid-conversation: bind() rewires + replays
    canonical history into the new backend.
  - 3.6 /v1/models advertises freeloader/{auto,claude,codex,gemini}.
  - 3.7 Same contract test suite runs green against all three.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn) — confirmed
    empirically 2026-04-25
  - three session id shapes; adapter normalizes to opaque string (gemini
    is now UUID, not int — spike drift)
  - codex exec resume rejects -s; sandbox set on first turn persists
  - codex --json is pure stdout; chatter on stderr; no model id field
  - asyncio create_subprocess_exec env=dict REPLACES env entirely; must
    {**os.environ, ...overrides} or OAuth/PATH are stripped
  - Starlette aclose() injects GeneratorExit, not CancelledError
  - json.dumps default separators differ from OpenAI wire bytes
