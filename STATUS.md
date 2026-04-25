# FreelOAder status — 2026-04-25

## Phase: 3/5 — CodexAdapter + GeminiAdapter + round-robin
## Step: 3.2 — Per-conversation CLI state isolation (PLAN decision #16)
## Task: redirect each subprocess's CLI state root to
         `<data_dir>/cli-state/<conversation_id>/<provider>/` via the
         CLI-specific env var (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and
         `XDG_CONFIG_HOME`+`XDG_DATA_HOME` for gemini at 3.3). OAuth
         credentials remain in the user's global state and are
         inherited via process env merge. Establishes a teardown hook
         (`lifecycle.purge_cli_state`) for the FreelOAder restart
         contract; wiring it into a serve entry point lands when one
         exists.

Purpose (why this step exists):
  Two concurrent turns on different conversations to the same backend
  share a global state dir (`~/.claude/`, `~/.codex/`) — race
  conditions: gemini's `-r latest` resolves to whichever session was
  last written; thread-id assignment can collide; context leaks
  between conversations. Decision #16 redirects each subprocess to a
  per-conversation state root, removing the shared-mutex problem the
  router would otherwise have to solve with a per-provider lock.
  Step 3.1 closed the codex-mapping seam; this step closes the
  cross-conversation isolation seam before 3.3/3.4 add concurrent
  routing.

Step 3.2 exit criteria (must all be true before step_done):
  - [ ] `_Adapter` Protocol in `router.py` carries
        `conversation_id: str` as a required keyword on `send()`.
  - [ ] `Router.dispatch` passes `conversation_id` through to the
        adapter (it already has the value in scope).
  - [ ] `ClaudeAdapter.send` resolves
        `<data_dir>/cli-state/<conversation_id>/claude/`, mkdirs it,
        and spawns the subprocess with
        `env={**os.environ, "CLAUDE_CONFIG_DIR": <state_dir>}`.
  - [ ] `CodexAdapter.send` does the same with `CODEX_HOME` and
        `<data_dir>/cli-state/<conversation_id>/codex/`.
  - [ ] All 8 in-tree fake adapters and 3 direct `adapter.send` call
        sites updated to accept/pass `conversation_id`.
  - [ ] New `src/freeloader/lifecycle.py` exposes
        `purge_cli_state(data_dir: Path) -> None` that rmtrees
        `<data_dir>/cli-state/`. Not wired into a startup hook (no
        entry point yet); that's a follow-up when `freeloader serve`
        lands.
  - [ ] Two new adapter tests (one per provider) verifying:
        - the env passed to `create_subprocess_exec` contains the
          right key with the right value;
        - the state dir exists on disk at spawn time;
        - OAuth-relevant env vars from `os.environ` are NOT clobbered
          (env-merge invariant).
  - [ ] One new lifecycle test verifying purge removes only
        `<data_dir>/cli-state/` and leaves siblings (`scratch/`,
        `events.jsonl`) untouched.
  - [ ] gate_2 still GREEN. Cross-phase invariants green.

Out-of-scope for 3.2 (deferred):
  - Wiring `purge_cli_state` into a uvicorn/serve entry point — no
    entry point exists yet.
  - Per-provider mutex fallback for CLIs without an isolation env
    var (PLAN: caught at adapter-implementation time; gemini probe at
    3.3 will tell us).
  - GeminiAdapter (3.3); round-robin Router (3.4); replay (3.5).

Phase 3 sketch:
  - 3.1 CodexAdapter ✅
  - 3.2 Per-conversation CLI state isolation env vars (this step).
  - 3.3 GeminiAdapter — compound provider, stats.models per turn.
  - 3.4 Round-robin Router (cycles providers per new conversation).
  - 3.5 Provider-switch mid-conversation: bind() rewires + replays
    canonical history into the new backend.
  - 3.6 /v1/models advertises freeloader/{auto,claude,codex,gemini}.
  - 3.7 Same contract test suite runs green against all three.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - codex exec resume rejects -s; sandbox set on first turn persists
  - codex --json is pure stdout; chatter on stderr; no model id field
  - Starlette aclose() injects GeneratorExit, not CancelledError
  - json.dumps default separators differ from OpenAI wire bytes
