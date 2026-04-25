# FreelOAder status — 2026-04-25

## Phase: 5/5 — tool-call decision
## Step: 5.2 — formalize chat_only_strip in code

Step 5.1 outcome (see JOURNAL `kind:decision subject:tool_call_strategy`):
  Chosen strategy: **chat_only_strip**. Alternatives rejected:
  output_parsing_shim, passthrough. Five evidence-grounded reasons,
  in JOURNAL: cold-cache cost (each shim round-trip is +1 cold CLI
  invocation = +6k–14k input tokens of agent prompt), per-adapter
  system-prompt-injection asymmetry (claude limited under OAuth,
  gemini no obvious slot, codex via -c overrides — three different
  shim implementations, none with a machine-readable tool-call
  boundary in JSONL), agent-loop contamination (CLI runs its OWN
  tools mid-turn under OAuth — shim parser cannot reliably
  distinguish client-tool calls from native-tool reports over
  natural-language CLI output), schema migration cost (canonical/
  has no tool_calls/tool role; shim/passthrough force a CanonicalMessage
  schema change cascading through storage + history_diff +
  openai_to_canonical), personal-use scope (PLAN ToS section —
  Aider/OpenWebUI/LibreChat all work without tools).

Step 5.2 — purpose:
  Today the frontend already drops `tools` / `tool_choice` with a
  silent `logger.warning` in `frontend/app.py:_warn_if_tools_dropped`
  (line 262-278). That's de-facto chat-only mode without honesty.
  5.2 lifts the silent drop into an **operator-visible structured
  signal** so a client (or its developer) can detect on the wire
  that their tool definitions were dropped, instead of finding out
  via "the model didn't call my function."

Step 5.2 — design (one sentence per change):
  - `frontend/app.py:_build_chat_completion` — when the request
    carried `tools` or `tool_choice`, emit
    `choices[0].message.tool_calls = []` (explicit empty list, not
    absent field) so OpenAI clients reading `tool_calls` get a
    deterministic "no calls" answer instead of `KeyError`.
  - `frontend/app.py:chat_completions` (non-streaming) and
    `_stream_chat_completion` (streaming) — set response header
    `X-FreelOAder-Tool-Mode: chat-only-strip` whenever the request
    carried `tools` or `tool_choice`. Header presence is a binary
    signal: "yes, you sent tool fields, and yes, we dropped them."
  - `frontend/app.py:_warn_if_tools_dropped` — keep the warning
    log (operator side) but add `mode="chat-only-strip"` and the
    conversation_id so logs can be grep'd per-conversation.
  - SSE: the header arrives before the first event (Starlette
    sets headers from `StreamingResponse(headers=...)`); no per-
    chunk signaling needed.

Step 5.2 — what 5.2 does NOT do:
  - Does NOT add `tool_calls` to `CanonicalMessage` (schema migration
    deferred — shim isn't being built).
  - Does NOT change the assistant's reply text. Chat-only means the
    model gets a plain prompt and answers in prose; if the client's
    prompt asked for tool use, the model's prose may say "I would
    call X" — that's the client's problem, not FreelOAder's.
  - Does NOT introduce per-request mode toggling. One mode for the
    whole proxy.

Step 5.2 — exit criteria:
  - [ ] Non-streaming response includes `tool_calls: []` in
        `choices[0].message` IFF the request carried `tools` or
        `tool_choice`.
  - [ ] Streaming response includes `X-FreelOAder-Tool-Mode:
        chat-only-strip` header IFF the request carried `tools` or
        `tool_choice`. Non-streaming response includes the same
        header under the same condition.
  - [ ] Existing `tools=None / tool_choice=None` requests are byte-
        for-byte unchanged (no tool_calls field, no header).
  - [ ] Warning log carries `mode` and `conversation_id` for grep.
  - [ ] Unit tests in `tests/frontend/` cover: tools-present →
        header + tool_calls=[]; tools-absent → no header + no
        tool_calls field; both streaming and non-streaming.
  - [ ] All 232 existing tests still green; new tests additive.
  - [ ] ruff check + ruff format clean.

Step 5.3 (next): `tests/e2e/test_tool_calls.py` exercising
  `tools=[...]` end-to-end. Gate_5 requires this file to exist.
  E2E means an OpenAI-shaped request with `tools=[{...}]` going
  through `create_app()` (with a stubbed router/adapter so we don't
  actually shell out to claude during tests) and asserting the
  full response shape — header, tool_calls=[], assistant prose.

Step 5.4 (then): README.md section documenting the chat-only-strip
  mode and its limits (which clients work, which break).
  Gate_5 case-insensitive grep for "tool call".

Phase 5 exit criteria (matches gate_5.sh + ROADMAP):
  - [x] `JOURNAL.jsonl` has a `kind:decision` row with
        `subject:tool_call_strategy`. (DONE in 5.1.)
  - [ ] `tests/e2e/test_tool_calls.py` exists, exercises
        `tools=[...]` end-to-end through chat_only_strip, green.
  - [ ] If chosen strategy were `output_parsing_shim`: shim lives
        entirely inside an adapter. (N/A — chat_only_strip chosen.)
  - [ ] `README.md` documents the supported tool-call mode and its
        limits.
  - [ ] `gate_5.sh` exits 0 (also re-runs gate_4).

Phase 4 just shipped:
  - 4.1 ✅ claude rate_limit_event → quota_signal
  - 4.2a ✅ gemini/codex token-window inference
  - 4.2b ⏸ deferred (additive; consumed transparently by 4.3 + 4.5)
  - 4.3 ✅ quota-aware strategy
  - 4.4 ✅ freeloader.toml thresholds
  - 4.5 ✅ deterministic routing replay
  Test count at phase boundary: 232. Branch is 40 commits ahead of
  origin/main (solo repo; no PR flow per memory).

Recent lessons relevant to phase 5 (see JOURNAL.jsonl for full text):
  - frontend is dumb (principle #6): tools handled at the frontend
    boundary or by an adapter that opts in — never by the router or
    by canonical/.
  - canonical/ has no tool_calls/tool role today
    (CanonicalMessage.role = system|user|assistant|developer);
    chat_only_strip avoids this schema cost.
  - claude/codex/gemini all execute their own native tools
    invisibly under OAuth — `num_turns > 1` is observable but not
    preventable (PLAN spike, 2026-04-05).
  - cold-cache tax: every CLI invocation eats 6k–14k input tokens
    of agent prompt overhead (PLAN line 239-244). Routing to
    minimize invocations is FreelOAder's whole point.
  - claude `--system-prompt` is limited under OAuth; gemini has no
    obvious injection point; codex injects via `-c` config
    overrides (PLAN line 779-786).
