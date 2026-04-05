# Instructions for any agent working in this repo

This file is AI-agnostic. If you are Claude Code, also read `CLAUDE.md` for
Claude-specific environmental notes (installed skills, tooling) — it
augments, does not replace, this file.

**Before you edit any file, run `scripts/orient.sh` and read its output.**
That one command tells you where you are, what you already learned, and
whether reality still matches the recorded status. It is the entry point
to the build-time loop this repo runs on.

## The loop

```
ORIENT   scripts/orient.sh                      — know where you are
PLAN     edit STATUS.md with exit criteria      — commit before coding
EXECUTE  code + contract test, together         — never one without the other
VERIFY   scripts/gate_<current_phase>.sh        — exit 0 or you are not done
REFLECT  scripts/reflect.sh <kind> <text> [kv]  — append to JOURNAL.jsonl
ADVANCE  scripts/advance.sh <next_phase>        — only when gate is green
```

The non-negotiable rules:

1. **PLAN before EXECUTE.** Write the step's exit criteria into `STATUS.md`
   *before* touching code. If you can't write them, you don't understand
   the step yet.
2. **REFLECT before ADVANCE.** Any decision, lesson, or surprise worth
   remembering next week goes into `JOURNAL.jsonl` via `scripts/reflect.sh`.
   Do not hand-edit `JOURNAL.jsonl` — the script validates shape.
3. **Gates are the truth.** `STATUS.md` can be wrong. The gate script
   cannot. If they disagree, fix STATUS or fix the code — never skip the
   gate.
4. **Append-only journal.** Never rewrite or delete a JOURNAL line. Wrong
   past decisions stay visible as later `lesson` entries that correct them.

## The four artifacts

| Question | File | Rule |
|---|---|---|
| Why (purpose) | `PLAN.md` | Stable. Change only on deliberate scope decisions. |
| Where heading | `ROADMAP.md` | Phase exit criteria. Updated at phase boundaries. |
| Where now | `STATUS.md` | One screen. Rewritten at every task transition. |
| Where been | `JOURNAL.jsonl` | Append-only. `scripts/reflect.sh` is the only writer. |

## Commit discipline

- Every commit message starts with `[phase.step]` and cites the principle
  or decision it serves, e.g.
  `[2.3] cancel turn on client disconnect (decision #5, principle #2)`.
- If you cannot name the principle or decision, you are building something
  the plan didn't ask for. Stop and re-read `PLAN.md`.
- `STATUS.md` must be updated in the same commit that advances a step.
- Never commit a red gate as green. If a gate is red, commit the failing
  code with a commit that explicitly notes it, then fix forward.

## Scope discipline

Re-read `PLAN.md` § "Things to explicitly *not* do" at every phase
boundary. Specifically:

- No CLI plugin system.
- No session persistence across jRouter restarts.
- No unified stream parser — each CLI gets its own JSONL→delta mapper.
- No tool calls in the MVP (deferred to phase 5).

When tempted to add a helper, abstraction, or config knob, ask: which of
the 8 principles in `PLAN.md` does this serve? If the answer is "none" or
"all of them," it does not belong in the MVP.

## On cold start after a compaction or restart

1. `scripts/orient.sh`
2. Read `STATUS.md` top to bottom
3. `grep '"kind":"lesson"' JOURNAL.jsonl | tail -20`
4. Run the current phase's gate
5. If gate red → the failing assertion is your next task
6. If gate green → proceed with the `Task:` line in `STATUS.md`

Do not try to reconstruct context from `git log`. The journal is the
source of truth for build-time reasoning; git log only records the code.
