# FreelOAder

A unified AI gateway that routes OpenAI-compatible API requests across
**existing CLI subscriptions** (Claude Pro, ChatGPT Plus, Gemini Pro) to hit
a **fixed monthly cost ceiling** equal to the sum of those subscription fees.

You paid for three all-you-can-eat buffets. FreelOAder makes sure you never
hit the "sir, please leave" moment at any single one.

Unlike OpenRouter's pay-per-token model, FreelOAder treats each subscription's
quota as a consumable resource pool and only switches providers when quota
runs low.

> **Personal-use / research prototype only.** Driving Claude Pro / ChatGPT
> Plus / Gemini Pro CLIs as the backend of a programmatic API proxy is very
> likely against each vendor's terms of service. Not shippable as a product.

## How it works

```
   OpenAI-compatible clients (Hermes, Cursor, aider, curl, …)
                         │
                         ▼
        ┌────────────────────────────────┐
        │  FreelOAder  (FastAPI frontend)   │
        │   /v1/chat/completions         │
        │   /v1/models                   │
        └────────────────┬───────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
      claude code   codex CLI     gemini CLI
      (Claude Pro)  (ChatGPT+)    (Gemini Pro)
```

Each request becomes one shell-out to an authorized CLI in non-interactive
JSONL mode (`claude -p`, `gemini -p`, `codex exec`), parsed live into OpenAI
SSE deltas. Conversations are bound to a vendor session id and can be
rebound to a different provider mid-flight when quota pressure rises.

See [`PLAN.md`](PLAN.md) for architecture principles, design decisions, and
the CLI capability matrix from the 2026-04-05 spike.

## Status

Pre-MVP. No code yet — planning and scaffolding only.

Current phase and step: see [`STATUS.md`](STATUS.md).
Phase roadmap and exit criteria: see [`ROADMAP.md`](ROADMAP.md).
Build-time decision log and lessons: see `JOURNAL.jsonl`.

## Orientation framework

This repo uses a four-artifact discipline so that long autonomous build
sessions can always answer: *where have I been, where am I, where am I
going, and why.*

| Question | File |
|---|---|
| Why (purpose) | `PLAN.md` |
| Where I'm heading | `ROADMAP.md` |
| Where I am now | `STATUS.md` |
| Where I've been | `JOURNAL.jsonl` |

Agents working on this repo should read [`AGENT.md`](AGENT.md) first — it
specifies the build-time loop (orient → plan → execute → verify → reflect
→ advance) and is AI-agnostic. Claude Code sessions additionally have
[`CLAUDE.md`](CLAUDE.md) for environment-specific tooling notes.

Each MVP phase has a gate script under `scripts/` that must exit 0 before
the next phase begins. See `ROADMAP.md` for the gates.

## Stack

- Python 3.11+
- FastAPI frontend
- `uv` for dependency management
- `src/freeloader/` layout, `pyproject.toml`
- Backends: `claude` (Claude Code), `gemini`, `codex` — all invoked as
  subprocesses per turn, no persistent processes

## Non-goals

- Public or multi-tenant deployment (ToS)
- Tool-call translation in the MVP (deferred to phase 5)
- Session persistence across FreelOAder restarts
- A CLI plugin system
- A unified stream parser across vendors
