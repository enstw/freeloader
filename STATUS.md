# FreelOAder status — 2026-04-24

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.4 — history_diff + CanonicalMessage  ✅ complete
## Task: advance to step 1.5 — sandbox. Spawn ClaudeAdapter subprocess
         inside a scratch cwd under data_dir; invoke with
         `--add-dir <scratch>`. Flips gate_1 sandbox test line green.

Purpose (why this step existed):
  Materialized PLAN principle #4 as code: the history-diff contract
  between the stateless /v1/chat/completions surface and the internal
  canonical model. CanonicalMessage lands as a minimal BaseModel
  (role + content + metadata). Library-only — wiring into the frontend
  handler happens at 1.7 alongside conversation identity (decision #14).

Entry criteria (met):
  - [x] Step 1.3 shipped (HEAD=ae3aae5)
  - [x] Frontend → Router → Adapter seam works via TestClient
  - [x] 13/13 tests green pre-1.4

Exit criteria (met):
  - [x] src/freeloader/canonical/messages.py — CanonicalMessage
        (role, content, metadata); frozen BaseModel
  - [x] src/freeloader/canonical/history_diff.py — real impl:
        HistoryMismatchError + DiffResult{action, new_messages} +
        diff_against_stored(stored, incoming) covering 3 MVP cases
  - [x] tests/canonical/__init__.py + test_history_diff.py — 10 tests:
          append: first turn, new user after assistant, system prefix
                  preserved, equal-to-stored no-op
          regenerate: drop assistant no new turn, drop assistant +
                  new user
          mismatch: mid-history edit, incoming-shorter-and-last-is-user,
                  reordered system message
  - [x] `uv run ruff check src tests`          exits 0
  - [x] `uv run ruff format --check src tests` exits 0
  - [x] `uv run pytest -q`                     22/22 green
  - [x] scripts/gate_1.sh "history_diff unit test exists" flipped
        to [ok]  (7 of 10 phase-specific green after 1.4)
  - [x] JOURNAL.jsonl: step_start + 3 decisions + step_done

Scope — things 1.4 deliberately did NOT do:
  - No adapter/router/frontend signature migration to
    list[CanonicalMessage]. Deferred to 1.7 (e2e) — no live caller
    of the new shape exists yet. Captured as JOURNAL decision.
  - No mid-history edit (case 4). HistoryMismatchError surfaces 400
    at 1.7; revisit in phase 5 if a real client needs it.
  - No multimodal content_blocks, no tool_calls on CanonicalMessage.
    Minimal shape per MVP scope.
  - No conversation persistence (1.7).
  - No conversation identity / hash-of-prefix (decision #14, 1.7).

Next step: 1.5 — sandbox.
  - Resolve data_dir: FREELOADER_DATA_DIR env, default
    $XDG_DATA_HOME/freeloader → fallback ~/.local/share/freeloader.
  - ClaudeAdapter.send() creates a scratch cwd under
    <data_dir>/scratch/<session_id>/ and spawns with
    `--add-dir <scratch>` and `cwd=<scratch>`. The scratch dir is
    cleaned up in the finally block.
  - tests/adapters/test_claude_sandbox.py — monkeypatches
    asyncio.create_subprocess_exec so no live claude runs; asserts
    argv contains `--add-dir <scratch>`, cwd==scratch, and scratch
    exists during the call.
  - Flips "scratch cwd sandbox test" in gate_1 → green (8/10).

Blockers: none.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names spike-observed; confirm at 1.7 e2e
