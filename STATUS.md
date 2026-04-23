# FreelOAder status — 2026-04-23

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.0.5 — architecture review (pre-code)  ✅ complete
## Task: advance to step 1.1 (scaffold pyproject.toml + src/freeloader/)

Purpose (why this step existed):
  PLAN.md encoded 8 principles + 11 decisions + spike findings. The next
  step (1.1) is the first code commit, after which architectural
  decisions harden into a codebase. Stress-test those decisions now
  while the cost of revision is still an edit to PLAN.md.

Entry criteria (met):
  - [x] Step 1.0 scaffolding committed (8e01c36 … 17dd2dd)
  - [x] PLAN.md, ROADMAP.md, AGENT.md stable and reviewable
  - [x] No code in src/ yet — architectural decisions cheap to revise

Exit criteria (met):
  - [x] Eng-manager review pass (Claude) against PLAN.md + ROADMAP.md;
        8 findings produced
  - [x] Pre-coding tooling review: 5 gaps classified (FastAPI,
        logging, type checker, pytest plugins, CI/pre-commit)
  - [~] Independent second-opinion review (/codex consult) — deferred.
        Rationale: the 13 findings below are concrete PLAN.md revisions
        with clear rationales. /codex is better spent on the first
        adapter diff (step 1.1/1.2), where adversarial review has
        something specific to attack. Recorded as a decision this step.
  - [x] Each finding classified: PLAN.md revision / JOURNAL decision /
        JOURNAL lesson / deferred
  - [x] PLAN.md revisions committed (7 edits across principles #1–#4,
        decision #6, two new decisions #12–#13, "not to do" list)
  - [x] JOURNAL.jsonl contains 9 `decision` + 2 `lesson` entries
        covering every classified finding

Summary: 13 findings → 7 PLAN.md revisions, 9 JOURNAL decisions, 2
JOURNAL lessons, 1 deferred item (/codex consult to step 1.1/1.2).

Revisions landed:
  - principle #1 — Delta as tagged union; remove health() from MVP
  - principle #2 — drop 'logged' state, atomic journal on terminal
  - principle #3 — replay scope excludes agent contamination
  - principle #4 — history_diff MVP scope frozen at 3 cases
  - decision #6 — two logs (global JOURNAL + per-conversation messages)
  - decision #12 (new) — FastAPI as HTTP framework
  - decision #13 (new) — stdlib logging + JOURNAL split
  - "not to do" — no type checker, no CI, no pytest plugins

Deferred to later phases (recorded as lessons):
  - journal_append_atomicity — need asyncio lock in phase 3+
  - rate_limit_stickiness — need resetsAt scheduler in phase 4

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
