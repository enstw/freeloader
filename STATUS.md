# FreelOAder status — 2026-04-05

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.0 — scaffolding (pre-code)
## Task: stand up orientation framework (this file, ROADMAP, JOURNAL, scripts/)

Purpose (why this step exists):
  Before any code, the build-time loop (orient → plan → execute → verify →
  reflect → advance) needs durable artifacts. Without them, context loss
  across compactions/restarts turns full-auto mode into drift.

Entry criteria (met):
  - [x] PLAN.md exists and is stable
  - [x] README.md, ROADMAP.md written
  - [x] Four-artifact framework decided (PLAN / ROADMAP / STATUS / JOURNAL)

Exit criteria (open):
  - [x] STATUS.md created (this file)
  - [x] JOURNAL.jsonl seeded with spike lessons + phase_start
  - [x] AGENT.md written — tells any agent how to engage the loop
  - [x] CLAUDE.md stub — Claude-specific environment notes (gstack etc.)
  - [x] scripts/orient.sh — single entry point for "where am I"
  - [x] scripts/reflect.sh — validated append to JOURNAL
  - [x] scripts/advance.sh — phase transition enforcer
  - [x] scripts/gate_common.sh + gate_1.sh … gate_5.sh stubs
  - [x] .gitignore
  - [ ] First commit containing the scaffolding

Next step after this: 1.1 — pyproject.toml, src/freeloader/ skeleton, uv lock
Blockers: none

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination is observable, not preventable, under OAuth
  - gemini is a compound provider (stats.models per turn)
