# FreelOAder Roadmap

Five phases, one per MVP step in `PLAN.md`. Each phase has a gate script at
`scripts/gate_<n>.sh` that must exit 0 before the next phase begins. Gates
are cumulative — phase 3 re-runs gates 1 and 2.

Advance rule: a phase is done when (a) its gate exits 0, (b) a
`phase_done` event is appended to `JOURNAL.jsonl` with the commit sha, and
(c) `STATUS.md` is rewritten to point at the next phase.

---

## Phase 1 — ClaudeAdapter, non-streaming, single conversation

**Purpose.** Prove the end-to-end shape: OpenAI request in, CLI shell-out,
JSONL parse, OpenAI response out. Establishes the `CLIAdapter` seam
(principle #1), the canonical message format (principle #4), the
conversation/backend-session split (principle #3), and the first entries in
the per-turn log (principle #7).

**Exit criteria.**
- `curl` → `/v1/chat/completions` (non-streaming) → `claude -p
  --output-format stream-json --verbose --session-id <uuid>` → OpenAI
  `ChatCompletion` JSON response.
- Two turns in the same conversation carry context (turn 2 references turn 1).
- Second turn uses `-r <uuid>` to resume the backend session.
- Client-sent `tools=[...]` is stripped with a logged warning.
- Scratch cwd is created per turn under the configured data dir; backend
  cannot read project files.
- Golden JSONL fixture replay test for `ClaudeAdapter` exists and is green.
- `src/freeloader/canonical/history_diff.py` exists with a
  `diff_against_stored(conversation, incoming_messages) -> new_turn_messages`
  entry point and a unit test covering (a) append-only new turn,
  (b) client regeneration replacing the last assistant turn,
  (c) mismatch (raise) when stored history diverges from the prefix of
  `incoming_messages`.
- `JOURNAL.jsonl` contains a per-turn record with `{conversation_id,
  backend_session_id, provider, outcome, usage}`.

**Gate.** `scripts/gate_1.sh`

---

## Phase 2 — Streaming + cancellation

**Purpose.** Make the frontend usable for real clients (SSE) and make the
turn state machine observable (principle #2, decision #5). This is where
the hard problem is not parsing but *lifecycle*: a dropped client must not
leak a CLI subprocess.

**Exit criteria.**
- `/v1/chat/completions` with `stream=true` emits OpenAI-shaped SSE deltas
  live as the JSONL stream arrives.
- Client disconnect mid-stream sends `SIGTERM` to the CLI subprocess;
  `SIGKILL` after 3s if still alive.
- Turn is marked `cancelled` in `JOURNAL.jsonl`; backend_session_id is
  preserved if the backend already reported one.
- No zombie `claude` processes after a disconnect stress test (50 drops).
- Turn state machine has explicit states
  `queued/spawning/streaming/drained/logged/cancelled/backend_error/
  rate_limited/timed_out` and unit tests for each transition.
- 5-minute hard timeout is enforced.

**Gate.** `scripts/gate_2.sh`

---

## Phase 3 — GeminiAdapter + CodexAdapter + round-robin routing

**Purpose.** Flush out the `CLIAdapter` Protocol boundaries and the
provider-switch replay path. The three vendors have different session-id
shapes (claude: client UUID; gemini: server index; codex: server
thread_id) — if the seam is wrong, this is where it shows.

**Exit criteria.**
- The same contract test suite (phase 1 golden-style) runs green against
  all three adapters.
- `/v1/models` advertises `freeloader/auto`, `freeloader/claude`, `freeloader/codex`,
  `freeloader/gemini`.
- Round-robin router cycles providers per new conversation.
- Provider switch mid-conversation: `bind(conversation, new_provider)`
  replays canonical history into the new backend's first turn, then
  resumes via its session id on subsequent turns.
- Gemini's per-model stats (`stats.models`) are captured in the journal as
  a compound-provider event.
- Codex `--ephemeral` mode is used; claude sessions are cleaned up on
  shutdown if feasible.

**Gate.** `scripts/gate_3.sh`

---

## Phase 4 — Quota tracking + threshold switching

**Purpose.** Replace round-robin with quota-aware routing — the core value
proposition. Principle #5: quota is an event stream, not a counter.

**Exit criteria.**
- Claude's `rate_limit_event` JSONL records are ingested directly as
  `quota_signal` events; switch triggers within one turn of a
  `status != "allowed"` event.
- Gemini/codex quota pressure is inferred from cumulative input+output
  tokens per rolling window plus 429 detection.
- Router reads a derived view (`estimated_pressure`, `last_rate_limit_at`,
  `requests_in_window`) over the event log and picks the next provider on
  threshold breach.
- Replay test: given a fixture JOURNAL, the router makes deterministic
  routing decisions — no wall-clock dependency.
- Thresholds and weights come from `freeloader.toml`.

**Gate.** `scripts/gate_4.sh`

---

## Phase 5 — Tool-call decision

**Purpose.** Decide hard problem #1 with real data in hand. By now phases
1–4 show whether chat-only covers the actual use cases.

**Exit criteria.**
- A written decision in `JOURNAL.jsonl` (`kind:decision`) picking one of:
  chat-only strip, output-parsing shim, passthrough.
- The chosen strategy has at least one end-to-end test from an OpenAI
  client that actually exercises `tools=[...]`.
- If shim is chosen: adapter owns the system-prompt slot and the shim
  lives entirely inside the adapter, not in the frontend.
- Documentation in `README.md` of the supported tool-call mode and its
  limits.

**Gate.** `scripts/gate_5.sh`

---

## Cross-phase invariants (checked by every gate)

- `ruff check` and `ruff format --check` are green.
- `pytest` is green.
- `src/freeloader/` is the only package root; no top-level modules.
- No file in `src/freeloader/frontend/` imports from `src/freeloader/adapters/*`
  directly — frontend talks to the router, router talks to adapters.
- `JOURNAL.jsonl` is append-only: the gate diffs it against the previous
  commit and fails if any line was removed or rewritten.
- `STATUS.md` was updated in the same commit that advances the phase.
