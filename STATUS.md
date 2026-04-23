# FreelOAder status — 2026-04-23

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.0.5 — architecture review (pre-code)  ✅ complete (v3, post-gemini)
## Task: advance to step 1.1 (scaffold pyproject.toml + src/freeloader/)

Purpose (why this step existed):
  Pre-code architecture review across three independent passes:
  Claude (eng-manager), OpenAI Codex (adversarial), and Gemini
  (third-party domain expert on its own shape). Stress-test
  decisions while revision is still an edit to PLAN.md, not a
  refactor.

Entry criteria (met):
  - [x] Step 1.0 scaffolding committed (8e01c36 … 17dd2dd)
  - [x] PLAN.md, ROADMAP.md, AGENT.md stable and reviewable
  - [x] No code in src/ yet — architectural decisions cheap to revise

Exit criteria (met):
  - [x] Round 1 — Claude eng-manager review: 13 findings
  - [x] Round 2 — /codex consult adversarial: 10 findings
  - [x] Round 3 — gemini CLI independent pass: 7 findings
  - [x] Phase-3 adapter ordering locked: codex before gemini
  - [x] All findings classified (PLAN/ROADMAP revision / decision /
        lesson / deferred)
  - [x] PLAN.md revisions committed across three rounds
  - [x] ROADMAP.md updated to stay consistent with revised PLAN
  - [x] scripts/gate_common.sh updated to use `uv run`
  - [x] JOURNAL.jsonl contains 22 `decision` + 5 `lesson` entries
        for step 1.0.5 across all three rounds

Summary round 1 (eng-manager + tooling): 13 findings → 7 PLAN
revisions, 9 decisions, 2 deferred lessons.

Summary round 2 (/codex consult): 10 findings → 6 PLAN revisions,
3 ROADMAP revisions, 1 script revision, 8 decisions, 1 lesson.

Summary round 3 (gemini): 7 findings → 3 PLAN revisions (principle
#3 replay format, principle #1 UsageDelta schema, decision #5
cancellation), 1 new PLAN decision (#16 CLI state isolation),
1 ROADMAP revision (codex --ephemeral removal), 5 decisions, 2
lessons.

Step 1.0.5 totals across all three rounds: 30 findings, 22
decisions, 5 deferred lessons.

Post-review correction (2026-04-23): decisions #14, #15, #16 —
added in rounds 2 and 3 — demoted to *provisional* in PLAN.md.
They solve problems that reviewers imagined rather than code
that hit them; step 1.1 proceeds without needing them frozen.
Recorded as a JOURNAL lesson on review-round scope creep.

Key revisions landed (cumulative):
  - principle #1 — Delta union (7 variants) + UsageDelta.models
    sub-model schema; no health() in MVP
  - principle #2 — `complete` terminal state, atomic journal write
  - principle #3 — replay scope (client-visible turns only) +
    replay format (role-tagged plaintext delimiters)
  - principle #4 — history_diff MVP scope frozen at 3 cases
  - decision #5 — cancellation DISCARDS backend_session_id
  - decision #6 — three append-only logs (repo JOURNAL build-time;
    data_dir events.jsonl runtime; per-conversation messages)
  - decision #8 — retry only pre-observation; post-observation
    starts fresh session
  - decision #12 (new) — FastAPI
  - decision #13 (new) — stdlib logging + three-log split
  - decision #14 (new, **provisional**) — chat-completions conversation
    identity = hash-of-prefix + header override
  - decision #15 (new, **provisional**) — Responses API owns
    response_id namespace
  - decision #16 (new, **provisional**) — CLI state isolated per
    conversation via env-var redirection (CLAUDE_CONFIG_DIR,
    CODEX_HOME, XDG_*)
  - phase-3 adapter order: codex → gemini
  - ROADMAP phase 1 — filesystem sandbox weakened to
    defense-in-depth; events move to <data_dir>/events.jsonl
  - ROADMAP phase 2 — state names (complete vs drained/logged)
  - ROADMAP phase 3 — codex uses exec resume, not --ephemeral
  - scripts/gate_common.sh — ruff/pytest via `uv run`

Deferred to later phases (recorded as lessons):
  - journal_append_atomicity — single-writer asyncio lock in 3+
  - rate_limit_stickiness — resetsAt scheduler in phase 4
  - openai_field_passthrough — enumerate honored/ignored/rejected
    request fields in phase 2
  - events_jsonl_derived_view — router maintains in-memory view,
    incremental updates; phase 4 concern
  - codex_streaming_illusion — codex batch-at-end; phase 2
    document-or-investigate concern

Next step: 1.1 — pyproject.toml, src/freeloader/ skeleton, uv lock
Blockers: none

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination is observable, not preventable, under OAuth
  - gemini is a compound provider (stats.models per turn)
  - Anthropic 3p-ban: shell-out to claude -p is the sanctioned path
  - journal_append_atomicity (phase 3+ concern)
  - rate_limit_stickiness (phase 4 concern)
  - openai_field_passthrough (phase 2 concern)
  - events_jsonl_derived_view (phase 4 concern)
  - codex_streaming_illusion (phase 2 concern)
