# FreelOAder status — 2026-04-23

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.0.5 — architecture review (pre-code)  ✅ complete (v2, post-/codex)
## Task: advance to step 1.1 (scaffold pyproject.toml + src/freeloader/)

Purpose (why this step existed):
  PLAN.md encoded 8 principles + 11 decisions + spike findings. The next
  step (1.1) is the first code commit, after which architectural
  decisions harden into a codebase. Step 1.0.5 ran two review passes —
  an eng-manager pass (Claude) and an independent adversarial pass
  (/codex consult) — to stress-test those decisions while revision is
  still an edit to PLAN.md, not a refactor.

Entry criteria (met):
  - [x] Step 1.0 scaffolding committed (8e01c36 … 17dd2dd)
  - [x] PLAN.md, ROADMAP.md, AGENT.md stable and reviewable
  - [x] No code in src/ yet — architectural decisions cheap to revise

Exit criteria (met):
  - [x] Eng-manager review pass (Claude) against PLAN.md + ROADMAP.md
  - [x] Pre-coding tooling review (FastAPI, logging, type checker,
        pytest plugins, CI)
  - [x] Independent second-opinion review — /codex consult ran against
        PLAN.md + ROADMAP.md + AGENT.md + STATUS.md; produced 10
        findings (5 P1 / 4 P2 / 1 P3)
  - [x] Phase-3 adapter ordering locked: codex before gemini
  - [x] Each finding classified (PLAN/ROADMAP revision / decision /
        lesson / deferred)
  - [x] PLAN.md revisions committed across two rounds (eng-manager +
        /codex)
  - [x] ROADMAP.md updated to stay consistent with revised PLAN
  - [x] scripts/gate_common.sh updated to use `uv run`
  - [x] JOURNAL.jsonl contains 17 `decision` + 3 `lesson` entries for
        step 1.0.5 across both review rounds

Summary round 1 (eng-manager + tooling): 13 findings → 7 PLAN revisions,
9 decisions, 2 deferred lessons.

Summary round 2 (/codex consult adversarial pass): 10 findings → 6
PLAN revisions, 3 ROADMAP revisions, 1 script revision, 8 decisions,
1 deferred lesson.

Totals for step 1.0.5: 23 findings, 17 decisions, 3 lessons.

Key revisions landed:
  - principle #1 — Delta union expanded to 7 variants (adds Finish,
    Error, Raw); health() removed from Protocol
  - principle #2 — `complete` replaces `drained + logged`; atomic
    journal write at terminal
  - principle #3 — canonical replay excludes agent contamination
  - principle #4 — history_diff MVP scope frozen at 3 cases
  - decision #6 — THREE logs (repo JOURNAL build-time only; data_dir
    events.jsonl runtime; per-conversation messages)
  - decision #8 — retry only pre-observation; post-observation retries
    start fresh session
  - decision #12 (new) — FastAPI as HTTP framework
  - decision #13 (new) — stdlib logging + three-log split for events
  - decision #14 (new) — chat-completions conversation identity =
    hash-of-prefix + X-FreelOAder-Conversation-Id header
  - decision #15 (new) — Responses API owns response_id namespace;
    internal map to (conversation, turn, backend_session_id)
  - "not to do" — no type checker, no CI, no pytest plugins
  - phase-3 adapter order: codex → gemini
  - ROADMAP phase 1 — filesystem sandbox weakened to defense-in-depth;
    JOURNAL.jsonl changed to <data_dir>/events.jsonl for turn records
  - ROADMAP phase 2 — state names updated (complete vs drained/logged)
  - scripts/gate_common.sh — ruff/pytest via `uv run`

Deferred to later phases (recorded as lessons):
  - journal_append_atomicity — single-writer asyncio lock in phase 3+
  - rate_limit_stickiness — resetsAt scheduler in phase 4
  - openai_field_passthrough — enumerate honored/ignored/rejected
    request fields in phase 2

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
