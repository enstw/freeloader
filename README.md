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
- Backends: `claude` (Claude Code), `codex`, `gemini` — all invoked as
  subprocesses per turn, no persistent processes. Implementation order
  is claude → codex → gemini (see `ROADMAP.md` phase 3).

## Tool-call mode

FreelOAder operates in **chat-only-strip** mode. When a request includes
OpenAI function-calling fields (`tools`, `tool_choice`), they are stripped
before reaching the backend CLI and the response carries an explicit
on-the-wire signal so clients can detect the strip.

**Wire signal.**
- Response header `X-FreelOAder-Tool-Mode: chat-only-strip` is set on
  both streaming and non-streaming responses iff the request carried
  `tools` or `tool_choice`.
- Non-streaming responses additionally include `tool_calls: []` (an
  explicit empty list, not an absent field) in
  `choices[0].message`, so OpenAI clients reading `tool_calls` get a
  deterministic answer instead of a `KeyError`.
- The frontend logs a structured warning per stripped request with
  `mode`, `conversation_id`, and the dropped field names.

**Why chat-only-strip.** The decision was made in phase 5 with full
phase-1–4 evidence in hand; the rationale is in `JOURNAL.jsonl`
(`kind:decision subject:tool_call_strategy`). The short version:

- **Cold-cache cost.** Each backend CLI invocation eats 6k–14k input
  tokens of agent-prompt overhead. An output-parsing shim would add
  one extra cold invocation per round-trip — multiplying the very
  cost FreelOAder's quota-aware routing exists to minimize.
- **No machine-readable boundary.** None of `claude`, `codex`, or
  `gemini` emit a structured tool-call event in their JSONL streams
  under OAuth — a shim would have to parse natural-language CLI
  output, with three different per-adapter system-prompt-injection
  surfaces and unreliable agent-loop contamination (the CLI runs its
  own native tools mid-turn and the parser can't tell those apart
  from intended client tool calls).
- **Schema cost.** The canonical message store has no `tool_calls`
  role today; a shim or passthrough would force a schema migration
  through storage, history-diff, and message conversion that is not
  gated by phase 5.
- **Personal-use scope.** This is a research prototype. Common chat
  clients (OpenWebUI, LibreChat) work without tools, and Aider has
  a chat-mode fallback. "Breaks agent frameworks that require
  function calling" is acceptable here.

**Limits.**
- Agent frameworks that depend on `tool_calls` to drive multi-step
  workflows (OpenAI Agents SDK, fully tool-driven Aider modes) will
  not work. Detect the `X-FreelOAder-Tool-Mode` header and either
  fall back to chat or fail loudly client-side.
- The model may *describe* a tool call in prose ("I would call
  `get_weather` with `city=Tokyo`") because the user's prompt asked
  for it. That's the client's problem to interpret — FreelOAder
  does not parse or rewrite assistant output.
- Mode is a property of the proxy, not per-request. There is no
  flag to enable shim or passthrough for a single call. If you need
  function calling, use the underlying CLI directly.

## Deploy on a dedicated host

FreelOAder is intended to run on a dedicated server (an Ubuntu host or
chroot) where the only consumer of `claude` / `codex` / `gemini` is
FreelOAder itself. Two setup layers:

1. **Adapter flags** — applied automatically per turn. The adapters spawn
   each CLI with the closest-to-bare flag set the OAuth-coupled CLIs
   allow. See `KNOWN-LIMITATIONS.md` for which dimensions of state are
   killed and which remain.

1. **Host setup** — a one-shot script that nullifies agent memory globally
   for the running user:

   ```bash
   ./scripts/setup-host.sh --yes
   ```

   This symlinks `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
   `~/.gemini/GEMINI.md` to `/dev/null` and clears `~/.codex/memories`
   and `~/.gemini/memory`. **Do not run on a workstation** where you also
   use these CLIs interactively; it is intended for dedicated hosts only.

## Non-goals

- Public or multi-tenant deployment (ToS)
- OpenAI tool-call translation beyond chat-only-strip (output-parsing
  shim and passthrough were rejected in phase 5; see "Tool-call mode"
  above)
- Session persistence across FreelOAder restarts
- A CLI plugin system
- A unified stream parser across vendors
