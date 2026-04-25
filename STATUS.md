# FreelOAder status — 2026-04-25

## Phase: 4/5 — quota tracking + threshold switching  ✅ GATE GREEN
## Next phase: 5/5 — tool-call decision
## Step: 5.1 — record the tool-call strategy decision in JOURNAL

Phase 5 purpose (ROADMAP § Phase 5):
  Decide hard problem #1 with real data in hand. Phases 1–4 have
  shipped the full chat-completion path (streaming, cancellation,
  three adapters, quota-aware routing). The one thing the MVP still
  hand-waves is `tools=[...]`: the frontend silently drops it with
  a logged warning (`frontend/app.py:_warn_if_tools_dropped`) and
  adapters never see function-calling fields at all (principle #6).
  That is *de facto* chat-only mode without anyone having committed
  to it being the answer. Phase 5 closes that ambiguity: pick a
  strategy, write the decision down, prove it works end-to-end, and
  document its limits.

  The three options PLAN.md hard-problem #1 keeps open:
  - **chat-only strip** — keep current behavior; document it as the
    supported mode. Cheapest. Breaks agent frameworks that need
    function calling (Aider, OpenAI agents SDK, ollama-bridge UIs).
  - **output-parsing shim** — adapter detects natural-language tool
    invocations in the CLI's reply, fakes `tool_calls` JSON back,
    feeds tool results in on the next turn. Most flexible; most
    fragile (CLI output isn't structured, prompt drift breaks the
    parser, no machine-readable boundary).
  - **passthrough** — advertise the CLI's *native* tools as if they
    were the client's tools. Inverts the normal control flow; almost
    no client knows how to handle "your tools are these things you
    didn't ask for." Mostly a non-starter for OpenAI clients.

Step 5.1 — purpose:
  Write the decision before the code. The implementation for any of
  the three options is small (< 1 day); the decision is what's
  load-bearing. Writing the decision first prevents post-hoc
  rationalization — if chat-only is the right call, say so
  explicitly and stop pretending the silent strip is a placeholder
  for a shim that's coming "real soon now." If shim wins, the
  rationale doc forces us to pre-state the constraints (which CLIs
  surface tool calls in their output, what the prompt-injection
  surface looks like, what the failure modes are when the parser
  misses) before we start coding to them.

Step 5.1 — exit criteria:
  - [ ] `JOURNAL.jsonl` has a `kind:decision` row with
        `subject:tool_call_strategy` whose `choice` field is
        exactly one of: `chat_only_strip`, `output_parsing_shim`,
        `passthrough`.
  - [ ] The same row carries a `rationale` field grounded in
        evidence from phases 1–4 (not abstract preference): cite
        which CLIs do/don't surface tool-call boundaries in their
        JSONL streams, the cold-cache cost of multi-turn shim
        round-trips, the per-adapter system-prompt-injection
        constraint (PLAN.md line 779-786), and the
        `num_turns > 1` agent-loop contamination observation
        (PLAN spike 2026-04-05).
  - [ ] STATUS.md updated to scope step 5.2 with the chosen
        strategy named (so 5.2 stops being abstract).

Phase 5 sketch (5.2–5.4 will be re-scoped after 5.1 picks):
  - 5.1 record decision in JOURNAL (this step).
  - 5.2 formalize chosen strategy in code:
        * if chat-only: lift `_warn_if_tools_dropped` from "silent
          warning" to "structured 200 response with explicit
          `tool_calls=[]` and operator-visible signal";
        * if shim: build the adapter-owned shim (system-prompt slot
          stays under adapter control per PLAN.md line 793);
        * if passthrough: build the `/v1/models`-driven tool
          discovery surface.
  - 5.3 `tests/e2e/test_tool_calls.py` exercising `tools=[...]`
        end-to-end through the chosen strategy (gate_5 requires
        this file to exist).
  - 5.4 README section documenting the supported tool-call mode
        and its limits — gate_5 grep's case-insensitive for
        "tool call".

Phase 5 exit criteria (matches gate_5.sh + ROADMAP):
  - [ ] `JOURNAL.jsonl` has a `kind:decision` row with
        `subject:tool_call_strategy`.
  - [ ] `tests/e2e/test_tool_calls.py` exists, exercises
        `tools=[...]` end-to-end through the chosen strategy, and
        is green.
  - [ ] If the chosen strategy is `output_parsing_shim`: the shim
        code lives entirely inside an adapter (no frontend imports
        of adapter-specific tool logic; the system-prompt slot is
        owned by the adapter).
  - [ ] `README.md` has a section documenting the supported
        tool-call mode and its limits.
  - [ ] `gate_5.sh` exits 0 (also re-runs gate_4).

Out of scope for phase 5:
  - **Multi-mode support** — pick *one* strategy. PLAN.md says
    "picked per-use-case" but we don't yet have multiple use cases
    in production; choosing a default first, adding mode flags
    later, is cheaper than building configuration for something
    that may never get a second user.
  - **Adapter-specific shim quality tuning** — if shim wins, ship
    the simplest version that passes the e2e test; tuning regex
    fragility is a separate workstream.
  - **Persisted tool-call history** — if shim wins, `tool_calls` /
    `tool` messages added to the canonical store are out of scope
    until phase 5 proves the round-trip works at all. (May force
    an `openai_to_canonical` extension; deferred.)
  - **Tool *discovery* via `/v1/models`** — gate_5 doesn't ask, and
    the shape question (do we list each CLI's native tools as
    OpenAI tool definitions?) only matters if passthrough wins.

Recent lessons relevant to phase 5 (see JOURNAL.jsonl for full text):
  - frontend is dumb (principle #6): tools must be handled at the
    frontend boundary or by an adapter that opts in — never by the
    router or by canonical/.
  - `frontend/app.py:_warn_if_tools_dropped` is the only place
    today that touches `tools` / `tool_choice`; logs a warning and
    discards. Phase 5 either keeps that or replaces it.
  - canonical/ has no `tool_calls` / `tool` role support today
    (`CanonicalMessage.role` is `system|user|assistant|developer`).
    A shim or passthrough would force that schema change.
  - claude/codex/gemini all execute *their own* native tools
    invisibly under OAuth — `num_turns > 1` is observable but not
    preventable (PLAN spike, 2026-04-05). The frontend does NOT
    know when the CLI ran tools internally.
  - cold-cache tax: every shim round-trip is two CLI invocations
    instead of one — relevant input to the shim-vs-chat-only cost
    analysis (PLAN line 239-244).
  - claude `--system-prompt` is limited under OAuth; gemini has no
    obvious injection point; codex injects via `-c` config
    overrides (PLAN line 779-786). If shim wins, the per-adapter
    system-prompt slot becomes load-bearing.

Phase 4 just shipped:
  - 4.1 ✅ claude rate_limit_event → quota_signal
  - 4.2a ✅ gemini/codex token-window inference
  - 4.2b ⏸ deferred (additive; 4.3 + 4.5 already consume any
    future quota_signal transparently)
  - 4.3 ✅ quota-aware strategy
  - 4.4 ✅ freeloader.toml thresholds
  - 4.5 ✅ deterministic routing replay
  Test count at phase boundary: 232. Branch is 39 commits ahead of
  origin/main (solo repo; no PR flow per memory).
