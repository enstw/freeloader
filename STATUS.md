# FreelOAder status — 2026-04-23

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.0.5 — architecture review (pre-code)
## Task: adversarial review of PLAN.md + ROADMAP.md before first code lands

Purpose (why this step exists):
  PLAN.md encodes 8 principles + 11 decisions + the 2026-04-05 spike
  findings. The next step (1.1) is the first code commit, after which
  architectural decisions harden into a codebase. Stress-test those
  decisions now with a second set of eyes while the cost of revision is
  still an edit to PLAN.md rather than a refactor.

  Chosen as a *step*, not a phase, to avoid re-litigating settled ground
  and to match the loop's shape (AGENT.md § "Scope discipline"): a
  one-shot review with JOURNAL entries as output, not a new gate script.

Entry criteria (met):
  - [x] Step 1.0 scaffolding committed (8e01c36 … 17dd2dd)
  - [x] PLAN.md, ROADMAP.md, AGENT.md stable and reviewable
  - [x] No code in src/ yet — architectural decisions are still cheap
        to revise

Exit criteria (open):
  - [ ] Eng-manager review pass (Claude) against PLAN.md + ROADMAP.md;
        findings list produced
  - [ ] Independent second-opinion review (/codex consult) against
        PLAN.md + ROADMAP.md; findings list produced
  - [ ] Each finding classified: non-issue / PLAN.md revision / JOURNAL
        lesson or decision / defer to later phase
  - [ ] PLAN.md or ROADMAP.md revisions (if any) committed
  - [ ] JOURNAL.jsonl contains a `lesson` or `decision` entry for each
        recorded finding
  - [ ] Summary line in STATUS.md at step close: "N findings, M
        revisions, K journal entries, L deferred"

Next step after this: 1.1 — pyproject.toml, src/freeloader/ skeleton, uv lock
Blockers: none

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination is observable, not preventable, under OAuth
  - gemini is a compound provider (stats.models per turn)
  - Anthropic 3p-ban: shell-out to claude -p is the sanctioned path;
    --bare OAuth is dead
