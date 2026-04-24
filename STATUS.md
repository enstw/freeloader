# FreelOAder status — 2026-04-24

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.1 — scaffold pyproject.toml + src/freeloader/ + uv lock  ✅ complete
## Task: advance to step 1.2 — ClaudeAdapter subprocess spawn + JSONL→Delta
         mapping + golden fixture replay test.

Purpose (why this step exists):
  Move from "no code" to "tooling works." Make gate_common's
  python-hygiene branch executable (it currently skips because
  there's no pyproject.toml). Create the empty module tree so
  later steps (1.2 adapter, 1.3 frontend, 1.4 history_diff,
  1.5 sandbox, 1.6 tools-strip, 1.7 e2e) drop into the right
  slots without churning paths. Lock decisions #11 (3.11+, src
  layout, uv) and #12 (FastAPI, deferred to 1.3) in writing —
  not yet in imports.

Entry criteria (met):
  - [x] Step 1.0.5 architecture review closed (HEAD=2b08eff)
  - [x] gate_common.sh already routes ruff/pytest via `uv run`
  - [x] .gitignore already excludes .venv/, __pycache__/, caches
  - [x] No files under src/ to conflict with a fresh skeleton

Exit criteria (met):
  - [x] pyproject.toml exists, targets Python 3.11+, src layout,
        package name `freeloader`
  - [x] Ruff config: line-length 88, select F,E,W,I,B,UP,SIM,C4,
        ignore E501
  - [x] Pytest config: testpaths=tests, asyncio_mode=auto
  - [x] src/freeloader/__init__.py               (version="0.0.0")
  - [x] src/freeloader/adapters/__init__.py
  - [x] src/freeloader/adapters/claude.py        (stub)
  - [x] src/freeloader/frontend/__init__.py
  - [x] src/freeloader/frontend/app.py           (stub)
  - [x] src/freeloader/canonical/__init__.py
  - [x] src/freeloader/canonical/history_diff.py (stub)
  - [x] tests/__init__.py + tests/test_scaffold.py  (2 passes)
  - [x] uv.lock committed
  - [x] `uv run ruff check src tests`           exits 0
  - [x] `uv run ruff format --check src tests`  exits 0
  - [x] `uv run pytest -q`                      exits 0
  - [x] JOURNAL.jsonl: step_start + 3 decisions + step_done for 1.1

Scope — things 1.1 deliberately does NOT do:
  - No FastAPI import yet; frontend/app.py is a stub. FastAPI
    lands in step 1.3 when the route handler is actually written.
  - No httpx, anyio, or other runtime deps until something imports
    them. Deps-without-use is exactly the kind of premature
    abstraction AGENT.md warns against.
  - No subprocess wiring. claude.py is a stub; the shell-out lands
    in 1.2 alongside the golden-fixture test.
  - No Pydantic models for the Delta union. Schema lives in PLAN
    principle #1; the code materializes in 1.2.
  - Gate 1 will remain RED after 1.1 — the e2e / golden / sandbox
    / tools-strip / history_diff unit tests don't exist yet.
    That's expected. Step 1.1 greens the file-existence slice of
    gate 1; the behavior slice greens across 1.2–1.7.

Next step: 1.2 — ClaudeAdapter subprocess spawn + JSONL→Delta
         mapping + golden fixture replay test.
Blockers: none.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination is observable, not preventable, under OAuth
  - gemini is a compound provider (stats.models per turn)
  - Anthropic 3p-ban: shell-out to claude -p is the sanctioned path
  - review-round scope creep: decisions #14/#15/#16 provisional
  - journal_append_atomicity (phase 3+ concern)
  - rate_limit_stickiness (phase 4 concern)
  - openai_field_passthrough (phase 2 concern)
  - events_jsonl_derived_view (phase 4 concern)
  - codex_streaming_illusion (phase 2 concern)
