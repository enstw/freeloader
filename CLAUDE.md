# Claude Code — environment notes for this repo

Read [`AGENT.md`](AGENT.md) first. It is the AI-agnostic source of truth
for the build-time loop (orient → plan → execute → verify → reflect →
advance), commit discipline, scope rules, and cold-start procedure. Every
rule in `AGENT.md` applies unchanged here.

This file only records things that are specific to running in a Claude
Code session — tooling that exists in this environment but is not a
property of the repo itself.

## gstack skills are available

This environment has the [`gstack`](https://github.com/) skill suite
installed, exposed as slash-command skills. They are build-time tools
available to you, the agent — they are **not** dependencies of FreelOAder
itself and must never be imported, vendored, or referenced from code
under `src/`.

Skills likely to be useful while building FreelOAder:

- **`/investigate`** — root-cause debugging with an explicit investigate →
  analyze → hypothesize → implement loop. Use when a gate fails in a
  non-obvious way. Pairs well with the loop in `AGENT.md`: its findings
  should be recorded via `scripts/reflect.sh lesson ...`.
- **`/review`** — pre-landing diff review against the base branch. Run
  before `scripts/advance.sh` at phase boundaries as an extra check on
  top of the gate.
- **`/codex`** — independent second-opinion review via OpenAI Codex CLI.
  Useful for adversarial review of adapter code, where quiet bugs in
  JSONL parsing or cancellation paths hide easily.
- **`/careful`** / **`/freeze`** / **`/guard`** — safety guardrails. Use
  `/freeze src/freeloader/adapters/claude` during phase 1 to prevent
  accidental edits to unrelated modules. `/careful` is worth enabling
  whenever touching subprocess lifecycle code.
- **`/simplify`** — post-edit review for reuse, quality, efficiency.
  Matches this repo's anti-scope-creep stance (see `PLAN.md` § "Things
  to explicitly *not* do").
- **`/retro`** — weekly engineering retrospective. Appropriate at phase
  boundaries; its output should feed `scripts/reflect.sh lesson` entries
  where applicable.

Skills **not** applicable to this repo:

- Browser/QA skills (`/qa`, `/browse`, `/design-review`, `/canary`,
  `/benchmark`, `/connect-chrome`, `/setup-browser-cookies`) — FreelOAder
  has no web UI.
- Design skills (`/design-shotgun`, `/design-html`, `/design-consultation`,
  `/plan-design-review`) — FreelOAder is a local API proxy, not a product
  with a visual surface.
- Deploy skills (`/ship`, `/land-and-deploy`, `/setup-deploy`) — FreelOAder
  is personal-use and must not be deployed publicly (see `PLAN.md` on
  ToS).

## Rule of thumb

If a gstack skill would help you complete the current task in `STATUS.md`,
use it. If it would change what the current task *is*, ignore it — the
loop in `AGENT.md` decides priorities, not the tool list.

When a skill surfaces a lesson or decision worth remembering across
sessions, capture it via `scripts/reflect.sh`. The skill output is
ephemeral; `JOURNAL.jsonl` is not.
