# FreelOAder Project Plan

## Concept

FreelOAder is a unified AI gateway that routes requests across **existing CLI
subscriptions** (Claude Pro, ChatGPT Plus, Gemini Pro, …) to achieve a **fixed,
predictable monthly cost ceiling** equal to the sum of those subscription fees.
Unlike OpenRouter's pay-per-token model, it treats each subscription's quota as
a consumable resource pool and only switches providers when quota runs low.

### OpenRouter vs FreelOAder

| Aspect | OpenRouter | FreelOAder |
|---|---|---|
| Cost model | Pay-per-token, no ceiling | Fixed monthly subs, hard ceiling |
| Routing trigger | Quality / speed / cost | **Quota availability** |
| Provider pool | 300+ models via API keys | CLI-based subscription quotas |
| Target user | Developers at scale | Power users maximizing existing subs |

---

## Architecture: Dual OpenAI-compatible API frontend + authorized CLIs

FreelOAder exposes **two** OpenAI-compatible HTTP API surfaces and fulfills
each request by dispatching to an authorized CLI session under the hood. Any
client that speaks either OpenAI protocol — Hermes, Cursor, aider,
continue.dev, raw curl — just points `base_url` at FreelOAder and it works.

### Two API surfaces

| | Chat Completions (`/v1/chat/completions`) | Responses (`/v1/responses`) |
|---|---|---|
| State model | **Stateless** — client sends full message history every turn | **Stateful** — server holds conversation; client sends `previous_response_id` + new input |
| Provider switching | Easy — full history available for replay into any backend | Requires FreelOAder to replay from its conversation log |
| CLI mapping | Extra work: diff incoming history against the stored conversation (identity = hash-of-prefix + optional header, see decision #14) | `previous_response_id` resolves to a FreelOAder-owned turn record carrying the backend session id (see decision #15) |
| Client compatibility | Broad — most tools use this today | Growing — newer OpenAI SDKs, Codex, agents framework |

Both surfaces feed into the same pipeline: canonicalize → route → dispatch
to CLI adapter → stream back. The difference is only at the edges — how the
request is parsed and how the response is wrapped.

```
   OpenAI-compatible clients (Hermes, Cursor, aider, curl, …)
                         │
                         ▼
        ┌─────────────────────────────────────┐
        │  FreelOAder  (FastAPI frontend)     │
        │   /v1/chat/completions (stateless)  │
        │   /v1/responses        (stateful)   │
        │   /v1/models                        │
        │                                     │
        │   ├─ request translator             │
        │   ├─ session manager                │
        │   ├─ quota tracker                  │
        │   └─ CLI dispatcher                 │
        └────────────────┬────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
      claude code   codex CLI     gemini CLI
      (Claude Pro)  (ChatGPT+)    (Gemini Pro)
```

### Why both APIs matter

- **Chat Completions** is the lingua franca today — most tools use it.
- **Responses API** is a more natural fit for CLI backends, which are
  inherently session-based. The `previous_response_id` →
  `(conversation, turn, backend_session_id at that turn)` mapping
  avoids replaying full history on every turn, saving the 6k–14k token
  cold-cache tax. FreelOAder owns the `response_id` namespace; backend
  session ids never leak into OpenAI-facing surfaces (see decision
  #15).
- Clients are migrating: OpenAI's own Codex CLI and agents SDK use the
  Responses API. Supporting both means FreelOAder stays compatible as the
  ecosystem shifts.

### Why OpenAI-API-in-front beats "fork Hermes"

- **Universal surface.** Any OpenAI-compatible tool works with zero code
  changes — including Hermes itself. FreelOAder becomes infrastructure, not a
  fork.
- **Clean separation.** Frontend = protocol translator. Backend = quota-aware
  CLI dispatcher. Each is independently testable.
- **Hermes stays a client.** Its memory / skills / FTS5 recall keep working
  unchanged; it just points at `OPENAI_API_BASE=http://localhost:xxxx`.

### Component responsibilities

**FreelOAder frontend (new):**
1. Serve `/v1/chat/completions` (stateless, streaming + non-streaming),
   `/v1/responses` (stateful, streaming + non-streaming), and `/v1/models`.
1. Translate OpenAI requests into CLI stdin prompts; translate CLI stdout
   back into OpenAI SSE deltas (chat completions) or Responses API events.
1. Maintain `{conversation_id → (provider, backend_session_id)}` bindings.
   For chat completions, derive the `conversation_id` from a hash of
   the message prefix (or the `X-FreelOAder-Conversation-Id` header if
   present, see decision #14), then diff incoming history against the
   stored conversation to extract the new turn. For the Responses API,
   generate a FreelOAder-owned `response_id` per turn that resolves
   internally to `(conversation_id, turn_id, backend_session_id)` —
   backend identity never appears in client-facing fields (see
   decision #15).
1. Track per-subscription quota in real time; switch providers on
   threshold breach; schedule workload across the billing cycle.

**CLI backends (existing, unchanged):**
- `claude` (Claude Code), `codex`, `gemini` — invoked as a fresh
  non-interactive subprocess per turn (`claude -p`, `codex exec`,
  `gemini -p` — see capability matrix below). No persistent process
  per session; each turn reads JSONL on stdout and exits.

**Clients (existing, unchanged):**
- Hermes Agent provides memory, skills, user modeling, and the agent loop.
  It talks to FreelOAder as a plain OpenAI endpoint.

---

## Hard problems to solve

1. **Tool-call translation.** OpenAI `/v1/chat/completions` lets clients send
   `tools=[...]` and expects structured `tool_calls` responses. CLIs execute
   their own built-in tools and don't emit machine-readable tool-call JSON.
   Three options, picked per-use-case:
   - **Chat-only mode** — strip `tools` from incoming requests. Simple, but
     breaks agent frameworks that rely on function calling.
   - **Output-parsing shim** — detect tool invocations in CLI output, fake
     `tool_calls` responses, feed `tool` results back on next turn. Fragile.
   - **Passthrough** — advertise CLI's native tools as if they were the
     client's tools. Inverts the normal flow; most clients won't handle it.

1. **Session lifecycle.** *Largely resolved by the 2026-04-05 spike (see
   "CLI capability matrix" below).* All three target CLIs expose
   non-interactive print modes (`claude -p`, `gemini -p`, `codex exec`) that
   emit JSONL events on stdout and exit when the turn is done, and all
   three support resuming a prior session by id on the next invocation. No
   persistent pty, no `/clear`, no tmux. The "session" in FreelOAder becomes a
   vendor-specific id string stored in the conversation log, and each turn
   is a fresh shell-out. What's left of this problem: mapping FreelOAder's
   `conversation_id` to whichever id shape each backend uses (claude:
   client-chosen UUID; gemini: server-assigned index; codex: server-assigned
   thread_id), and handling the first-turn case where the id doesn't exist
   yet.

1. **Streaming translation.** *Largely resolved.* Each CLI's JSONL event
   stream already has discrete message / delta / result events; translation
   is field-level mapping to OpenAI SSE, not stream parsing. No ANSI, no
   markdown chunking, no end-of-response heuristic — the backend exits on
   completion. What's left: one small mapper per adapter, because the three
   schemas differ (claude uses Anthropic-shaped `{type:"assistant",message:
   {content:[...]}}`; gemini uses `{type:"message",role,content,delta}`;
   codex uses `{type:"item.completed",item:{type:"agent_message",text}}`).

1. **Quota signal.** *Partially resolved.* Claude emits explicit
   `rate_limit_event` JSONL records with `rateLimitType` (e.g. `five_hour`),
   `status`, `resetsAt`, and `overageStatus` — ground-truth quota
   telemetry, not inference. Gemini and codex report per-turn token usage
   (`input_tokens`, `output_tokens`, `cached_input_tokens`) in their
   `result`/`turn.completed` events but do not currently emit a
   forward-looking "how much is left" signal. Routing for those two
   remains heuristic (token accumulation + 429 detection), but claude's
   path can now be exact.

1. **Cross-provider context drift.** When FreelOAder switches provider
   mid-conversation, history must be replayed into the new CLI. Different
   models interpret the same context differently; structured markdown memory
   (Hermes-style) mitigates but does not eliminate this.

1. **ToS.** Using Claude Pro / ChatGPT Plus / Gemini Pro CLIs as the backend
   of a programmatic API proxy is very likely against each vendor's terms of
   service. **Personal-use / research prototype only, not shippable as a
   product.**

---

## CLI capability matrix (2026-04-05 spike)

All three target CLIs were probed with `--help` and a minimal real
invocation. The architecture should be driven by what's actually available,
not what was feared. Summary: **all three expose a non-interactive JSONL
mode with session resumption**, which collapses most of hard problems #2
and #3 and turns the pty / `/clear` / tmux plan into dead code.

| | `claude` (Claude Code 2.1.92) | `gemini` (0.35.3) | `codex` (codex-cli 0.118.0) |
|---|---|---|---|
| Non-interactive mode | `-p / --print` | `-p / --prompt` | `exec` subcommand |
| JSONL event stream | `--output-format stream-json --verbose` | `-o stream-json` | `--json` |
| Session id shape | Client-chosen UUID via `--session-id`; echoed in every event | Server-assigned, returned in `init` event | Server-assigned `thread_id`, returned in `thread.started` event |
| Resume | `-r <uuid>` | `-r latest\|<index>` | `exec resume <id>` / `--last` |
| Ephemeral (no on-disk persistence) | *(always persists)* | *(TBD)* | `--ephemeral` |
| Filesystem sandbox knob | `--add-dir`, scratch cwd | `--approval-mode plan` (read-only) | `-s read-only`, `-C <dir>` |
| System prompt injection | `--system-prompt` / `--append-system-prompt` (limited under OAuth) | not obvious from top-level help (TBD) | via `-c` config overrides (TBD) |
| Baseline token overhead per cold call | ~9800 cache-creation (Claude Code agent prompt) | ~6000 input (Gemini system prompt) | ~14000 input, ~3500 cached (codex agent prompt) |
| Reports cost per turn | Yes, `total_cost_usd` in `result` event | No (token stats only) | No (token stats only) |
| Explicit quota signal | **Yes** — `rate_limit_event` with `rateLimitType`, `status`, `resetsAt` | No (inferred from usage + errors) | No (inferred from usage + errors) |
| `--bare` / API-key-only mode | Exists but refuses OAuth — unusable for subscription-mode FreelOAder | N/A | N/A |

### Event shapes (representative)

**claude**
```
{"type":"system","subtype":"init","session_id":"...","model":"claude-opus-4-6[1m]","tools":[...],...}
{"type":"assistant","message":{"content":[{"type":"text","text":"..."}],"usage":{...}}}
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","rateLimitType":"five_hour","resetsAt":1775408400,...}}
{"type":"result","subtype":"success","duration_ms":2556,"num_turns":1,"result":"...","total_cost_usd":0.061,"usage":{...},"modelUsage":{...}}
```

**gemini**
```
{"type":"init","session_id":"...","model":"auto-gemini-3"}
{"type":"message","role":"user","content":"..."}
{"type":"message","role":"assistant","content":"...","delta":true}
{"type":"result","status":"success","stats":{"total_tokens":6375,"input_tokens":6085,"output_tokens":46,"models":{...}}}
```

**codex**
```
{"type":"thread.started","thread_id":"..."}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"..."}}
{"type":"turn.completed","usage":{"input_tokens":14005,"cached_input_tokens":3456,"output_tokens":27}}
```

### Consequences for the design

- **No persistent process per session.** Each turn is a fresh
  `Popen([...cli, ...flags, ...prompt]) → read JSONL on stdout → exit`.
  Architecture principle #2 (session state machine) shrinks to a
  per-*request* state, not per-process. Principle #3
  (conversation/session decoupling) still holds — in fact it's cleaner,
  because "session" is now just a vendor id string rather than a live
  process handle.
- **`CLIAdapter.send()` is implemented by shelling out.** No `clear()`
  method needed (each turn is already clean). `close()` becomes a no-op or
  a vendor-specific `--delete-session` call. The Protocol gets smaller.
- **Streaming is JSONL → SSE field mapping.** Three tiny parsers, one per
  vendor. No ANSI stripping, no markdown chunk reassembly, no
  end-of-response detection.
- **Warm-cache discipline matters.** Every cold invocation eats 6k–14k
  input tokens of agent-prompt overhead. Under subscription auth there's
  no `--bare` mode to avoid this. Keep requests to the same conversation
  within the vendor's prompt-cache window (claude's is 1 hour) to keep
  per-turn cost down. If FreelOAder itself goes idle for >1h, the first
  request back will pay the cold-cache tax.
- **Agent-loop contamination is unavoidable under OAuth.** In the spike,
  asking claude to "remember 42, reply 'ok'" produced `num_turns: 3` —
  the CLI did internal agent work the client didn't request. The backend
  CLIs are framed by their own system prompts as tool-wielding agents;
  under subscription auth we cannot replace those prompts. FreelOAder must
  treat `num_turns > 1` and unexpected tool use as *observable* (log them
  in the per-turn record) but not *preventable*. Design decision #3
  (sandboxed filesystem) is still the right defense against blast radius,
  but it's defense-in-depth, not full suppression.
- **Gemini auto-routes across models within a single turn.** One call in
  the spike used both `gemini-3-flash-preview` and `gemini-2.5-flash-lite`;
  the `stats.models` breakdown shows per-model token counts. The
  `GeminiAdapter` must surface this in its quota events — "gemini" is a
  compound provider, not a single model.

---

## Architecture principles

These are the load-bearing decisions. They shape how the hard problems above
get solved and what the MVP should look like underneath.

### 1. The CLI adapter is the seam

Define one protocol and make everything above it CLI-agnostic:

```python
class CLIAdapter(Protocol):
    async def send(
        self,
        messages: list[CanonicalMessage],
        *,
        backend_session_id: str | None,
        system: str | None = None,
    ) -> AsyncIterator[Delta]: ...
```

`Delta` is a tagged union, not a flat "chunk" record:

```python
Delta = (
    TextDelta        # streamed assistant content
    | FinishDelta    # finish_reason: stop/length/content_filter/tool_calls
    | SessionIdDelta # backend-assigned session id (first time per conv)
    | UsageDelta     # per-turn token usage (claude / gemini / codex)
    | RateLimitDelta # vendor rate-limit event (explicit from claude)
    | ErrorDelta     # adapter-observed error (bad JSONL, partial stream)
    | RawDelta       # escape hatch: vendor event we haven't canonicalized
)
```

Each variant carries exactly one kind of information. The frontend
pattern-matches on the variant rather than parsing positionally. This
matters because every adapter interleaves these on a single stream
(claude emits `rate_limit_event` and `result` records alongside
`assistant` messages) and the frontend has to dispatch them to
different destinations — text to SSE, finish reason to the OpenAI
response object, session id to the conversation record, usage and
rate-limit to the runtime event log, errors to both client and
journal. A flat `Delta` with "the last one has the session id"
semantics was the original shape and is wrong; variants must be
distinguishable at every yield.

`RawDelta` is the escape hatch for vendor events the canonical layer
doesn't recognize yet. They're written to the runtime event log for
debugging but don't reach the client. Adding a new canonical variant
is a deliberate decision, not an accident of whatever the vendor just
shipped.

`UsageDelta` schema is always keyed by sub-model, even for
single-model providers:

```python
class UsageDelta:
    models: dict[str, ModelUsage]  # { model_name: {input, output, cached?} }
    # Top-level totals are derived (sum of models.values()).

class ModelUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
```

Claude and codex report one entry (`{"claude-opus-4.6": {...}}`,
`{"gpt-5.1-codex": {...}}`); gemini is a compound provider and can
report multiple entries per turn (`{"gemini-3-flash-preview": {...},
"gemini-2.5-flash-lite": {...}}`) — matching its `stats.models`
breakdown from the 2026-04-05 spike. This is the only shape that
supports per-sub-model quota tracking (principle #5) without
discarding information.

`send()` shells out to the backend CLI for one turn, yields
appropriately-typed deltas parsed from the CLI's JSONL stream, and
returns when the subprocess exits. On a successful turn the adapter
yields: exactly one `SessionIdDelta` the first time a backend assigns
a session id for a new conversation, zero-or-more `TextDelta`, any
number of `RateLimitDelta` / `RawDelta` interleaved, one `FinishDelta`
immediately before a terminal `UsageDelta`. An `ErrorDelta` terminates
the stream in place of the normal `FinishDelta` + `UsageDelta` pair.

The `system` parameter carries the client's system message (if any) so
each adapter can decide how to inject it — the slot has to live on the
Protocol from day one, because the phase-5 tool-call shim (hard problem
#1) requires the adapter to own the system-prompt slot, and retrofitting
the signature after three adapters exist is a three-way rewrite.

No `health()` in the MVP Protocol — quota is an event stream (principle
#5), not a probe; phase 4 adds `probe_quota()` only if a routing
decision needs to happen before the first turn fires. No `clear()`
(each shell-out starts clean), no `close()` (no persistent process).

Everything above the seam — `/v1/chat/completions`, `/v1/responses`,
quota tracker, router — talks only to `CLIAdapter`. Everything below —
vendor CLI flags, JSONL event schema, token-accounting quirks,
system-prompt injection tricks — lives inside a concrete adapter
(`ClaudeAdapter`, `CodexAdapter`, `GeminiAdapter`). Tool calls (#1),
streaming (#3), quota (#4), and context drift (#5) all have CLI-specific
shapes; if the frontend reaches past the adapter, every new CLI becomes
a refactor.

### 2. Turns are state machines, not requests

*(Revised after the 2026-04-05 spike.)* There are no persistent CLI
processes to machine-model — each turn shells out, streams, and exits. The
state machine lives one level up, at the *turn* granularity:
`queued → spawning → streaming → complete`, with terminal states
`{complete, cancelled, backend_error, rate_limited, timed_out}`.
Reaching a terminal state atomically writes the per-turn JOURNAL entry
— there is no separate "logged" state, because a window where the turn
exists in conversation history but not the journal is a consistency gap
(process crash → the two disagree on the outcome). Journal-write
failures surface as `adapter_error` to the client. One mutex per
conversation (turns in the same conversation serialize, per design
decision #1), one
state enum, one "is this turn still live" predicate. The common bugs — a
stale turn leaking output after a client disconnect, a crashed CLI looking
idle, a rate-limited backend racing with a router retry — all come from
implicit state at this layer, so keep it explicit.

### 3. Decouple conversations from backend sessions

- **Conversation** — the OpenAI-level history the client sees. Owned by
  FreelOAder, persisted as append-only JSONL.
- **Backend session** — a vendor-side conversation the CLI can resume from
  (claude UUID, gemini index, codex thread id). Stored as a string on the
  conversation record. Not a live process.
- **Binding** — current `{conversation_id → (provider, backend_session_id)}`,
  *revocable*. When FreelOAder switches provider mid-conversation, the
  binding is rewritten to point at a new (provider, backend_session_id)
  pair and history is replayed into the new backend on the next turn.

Put the replay logic in one place: `bind(conversation, provider)`. First
call to a new backend = no `backend_session_id`, replay full history into
the first turn's prompt. Subsequent calls = pass the stored id back via
the backend's resume flag.

**Replay scope.** The canonical history replayed into a new backend
contains only the client-visible turns — user messages and
final-assistant-message turns. Intermediate agent contamination
(claude's `num_turns > 1` from a single client turn — internal tool
work the client didn't request) is preserved in metadata but not
replayed, because the OpenAI chat surface only exposes user +
assistant roles and the contamination was never part of the
conversation the client thinks it's having. Lossy with respect to the
backend's own reasoning trace; faithful to the client-visible
conversation.

**Replay format.** Non-interactive CLIs take a *single text prompt*
via stdin or argv, not a structured `[{role, content}, ...]` JSON
array. Replaying multi-turn history means flattening the turn list
into one prompt, which collapses role boundaries and lets prior
assistant text become prompt-injection fuel unless explicit
delimiters are used. The canonical flatten format is role-tagged
plaintext:

```
[SYSTEM]
<system message>
[/SYSTEM]

[USER]
<turn 1 user>
[/USER]

[ASSISTANT]
<turn 1 assistant final text>
[/ASSISTANT]

[USER]
<turn 2 user>
[/USER]

...

[USER]
<current user turn — what the client is asking now>
[/USER]
```

Each adapter may choose different delimiter tokens (claude may prefer
XML-like `<user>…</user>` if its tokenizer biases that way; gemini
may prefer `User:` / `Model:`; codex TBD) — the adapter owns the
exact string, the canonical layer produces the role-tagged sequence.
This replay is lossy compared to a native multi-turn API call and
pays the cold-cache tax; it is only used on the *first* turn after a
rebind (first call to a new backend, or resume flag can't be used).
On subsequent turns the backend's native resume flag is used and
only the new user turn is sent. Adapters that can pass structured
input via a native mechanism (future `--input-format=json` flag, MCP
channel, etc.) should — the flatten format is the floor, not the
ceiling.

### 4. Canonical message format in the middle

Don't pass OpenAI arrays around internally. Define:

```
CanonicalMessage = {role, content_blocks[], tool_calls?, metadata}
```

with converters on both edges: `openai_to_canonical`,
`canonical_to_claude_prompt`, `canonical_to_codex_prompt`, … The
Responses-API-vs-Chat-Completions split on the client side is the same
problem you're about to have on the backend side. Solve it once, in the
middle.

**History diff lives here, not in the frontend.** `/v1/chat/completions`
is stateless — the client resends the full `messages` array every turn,
which may include edits, regenerations, reordered system messages, and
multimodal blocks. Figuring out "what's new this turn" versus "what was
already sent" is non-trivial and can't live in the ~50-line handler
(principle #6) or in the text-in/text-out adapter (decision #3). Put it
in `src/freeloader/canonical/history_diff.py` alongside the other
canonical converters. The frontend calls `diff_against_stored(conversation,
incoming_messages) → new_turn_messages`; the router sees only the new
turn. The Responses API path skips this module entirely — its
`previous_response_id` makes the diff trivial.

**MVP scope.** Three supported diff outcomes: (a) append-only new turn,
(b) client regeneration replacing the last assistant turn, (c) mismatch
when stored history diverges from the prefix of `incoming_messages` →
raise. Mid-history edits (client replays with turn N changed and N+1
onward unchanged) are a fourth case and raise 400 for the MVP. Adding
truncate-and-replay semantics is cheap once the three-case spine works;
pre-building it is scope creep (AGENT.md § scope discipline). Revisit
in phase 5 if a real client surfaces the need.

### 5. Quota as an event stream, not a counter

Don't model quota as `{provider: remaining_tokens}` that you decrement —
you don't know the real numbers and the CLIs won't tell you. Instead, an
append-only event log per provider (`request_sent`, `rate_limit_seen`,
`limit_reached_string_matched`, `slow_response`, …), with the router
reading a derived view (`estimated_pressure`, `last_rate_limit_at`,
`requests_in_window`). Updating the estimator doesn't change the schema,
and routing decisions are debuggable after the fact by replaying the log.

### 6. The frontend is dumb

`/v1/chat/completions` and `/v1/responses` handlers should each be ~50
lines: parse, canonicalize, call `router.dispatch(conversation,
canonical_messages)`, stream the result back. No quota logic, no adapter
awareness, no session management. If a handler is doing anything
interesting, it belongs in the router or the adapter.

The two handlers differ only at the edges:
- **Chat Completions** — parse the full `messages` array, diff against
  stored history to find the new turn(s), wrap the response as a
  `ChatCompletion` or SSE deltas.
- **Responses** — resolve `previous_response_id` to a conversation +
  backend session id, send only the new `input`, wrap the response as a
  `Response` object with an `id` the client can reference next turn.

Same pipeline, different wrappers. This matters because Hermes's Codex/GPT-5
path calls `client.responses.create()` directly with no fallback to chat
completions.

### 7. Observability from day one

You are building a system where backends lie about their state, quotas are
inferred from vibes, output formats change when vendors ship a CLI update,
and failures are silent (a rate-limited CLI looks identical to a healthy
one). Structured per-turn logs with `{conversation_id, session_id,
provider, latency, tokens_in_estimate, tokens_out_estimate, outcome,
quota_signal}` from the first commit. A single JSONL file is fine; don't
overbuild it. You will need this the first time routing does something
surprising.

### 8. Contract tests per adapter

Risk #5 (CLI output-format instability) has no defense other than an early
warning. Keep a small golden-test suite per adapter: "given this canonical
input, the adapter produces deltas matching this shape, and the final
assistant message matches this content regex." Run after every `claude` /
`codex` / `gemini` update. Cheap to write, catches the class of bug that
otherwise eats weekends.

### Things to explicitly *not* do

- **No CLI plugin system.** Three hardcoded adapters behind the Protocol is
  fine. A plugin loader is a tax on a personal-use prototype.
- **No session persistence across FreelOAder restarts.** Tempting with tmux,
  but the state machine then has to reconcile with whatever the CLI was
  doing when you died. Restart fresh, replay from the conversation log.
- **No unified stream parser.** Each CLI gets its own stream parser
  producing canonical deltas. One regex-based parser for all of them is a
  tarpit.
- **No tool calls in the MVP** (already deferred). When you do add them,
  the output-parsing shim only works if the adapter owns the system-prompt
  slot — make sure that slot stays under adapter control so the option
  remains open.
- **No static type checker in the gate.** mypy/pyright are fine to run
  ad-hoc; keeping them out of `scripts/gate_*.sh` avoids fighting
  `Protocol` + `asyncio` + duck-typed adapters over what is a ~500-line
  prototype. Revisit at phase 3 when three adapters exist and the
  Protocol contract is load-bearing.
- **No CI, no pre-commit hooks.** Gates run locally
  (`scripts/gate_<n>.sh`). Personal-use / single-developer repo; a
  GitHub Actions workflow is overhead the MVP never pays back. Revisit
  if collaborators appear.
- **No pytest plugins beyond pytest itself.** `tests/conftest.py` with
  plain fixtures is enough. `pytest-asyncio` is the one likely exception
  (required for async test functions); decide at step 1.1 if needed.

---

## Design decisions

Answers to the concrete questions that shape the first commit. These are
decisions, not research — change them deliberately, not by drift.

1. **Concurrency per session = 1.** CLIs are interactive single-threaded
   processes. Pool size = concurrent request capacity. The frontend queues
   FIFO *per conversation* (turns on the same conversation serialize), and
   rejects with `429` when the global pool is exhausted. No request-level
   parallelism within a conversation.

1. **Bind localhost, require an API key.** Default bind `127.0.0.1`,
   require a static `Authorization: Bearer <key>` header, refuse to start
   on `0.0.0.0` without an explicit `--unsafe-public` flag. ToS risk (#6)
   means this is personal-use; the defaults enforce that.

1. **CLI filesystem access: sandboxed and invisible.** The OpenAI chat API
   has no concept of cwd or filesystem. Each CLI session runs in an
   ephemeral scratch directory with no access to user files; built-in file
   tools are suppressed where possible and fenced by the sandbox where
   not. Any file operation the user wants happens on the *client* side and
   arrives as message content. This is not a limitation — it's the only
   coherent interpretation of "OpenAI-compatible backend" for a
   tool-wielding CLI. Corollary: `CLIAdapter.send()` is text-in, text-out;
   it never receives or returns file handles, cwds, or tool-execution
   results. This subsumes the "what to do with CLI native tools" half of
   hard problem #1.

1. **Model names are a virtual namespace.** `/v1/models` advertises
   `freeloader/auto` (router picks by quota), plus `freeloader/claude`,
   `freeloader/codex`, `freeloader/gemini` for clients that want to pin a
   backend. Unknown model names error with `400`. The client's `model`
   field is the routing input, not a passthrough to the CLI.

1. **Cancellation on client disconnect.** When the HTTP client drops
   the SSE stream, FreelOAder sends `SIGTERM` to the backend CLI
   subprocess; `SIGKILL` 3s later if still alive. The conversation's
   last turn is marked `cancelled` in the runtime event log. The
   `backend_session_id` reported during that turn (if any) is
   **discarded**, not preserved: a SIGTERMed CLI leaves a partial
   assistant generation in its local state, and the canonical history
   (which omits the cancelled turn) no longer matches the backend's
   view of the session. Resuming via that poisoned id would cause
   permanent state drift — the backend would see `User → partial
   assistant → User retry` while the canonical record shows
   `User → User retry`. The conversation stays unbound until the
   next turn starts fresh: a new session, canonical history replayed
   in full via the role-tagged format (see principle #3). Cost: one
   cold-cache tax on the recovery turn. Benefit: no state drift.

1. **Three logs, one format — all append-only JSONL.** Different
   lifecycle, different storage, different audience.
   - `JOURNAL.jsonl` (repo root, tracked in git) — **build-time
     only**: `decision`, `lesson`, `surprise`, `step_*`, `phase_*`.
     Written by `scripts/reflect.sh`; never touched by the running
     server. Gate scripts verify this file is append-only vs HEAD~1.
   - `<data_dir>/events.jsonl` (runtime, not tracked) — runtime
     events: `turn_done` (`{conversation_id, backend_session_id,
     provider, outcome, usage}`), `rate_limit_event`, `spawn_error`,
     `quota_signal`. Router reads a derived view over this file
     (principle #5). Lives under `data_dir` so running the server
     doesn't dirty the repo and conversation metadata can't leak into
     git commits.
   - `<data_dir>/conversations/<conversation_id>.jsonl` — one line per
     canonical message (user/assistant turn + metadata). Used for
     replay when rebinding to a new backend (principle #3). Not read
     by the router.

   Crash-safe, human-readable, no SQLite, no migrations. Concurrent
   writers within the server share a single-writer asyncio lock per
   file (POSIX `O_APPEND` is atomic only under `PIPE_BUF`, which a long
   assistant message can exceed). `scripts/reflect.sh` is single-process
   so safe as-is.

1. **Token estimation: `len(text) // 4`.** Quota is event-driven
   (principle #5), so token counts only need to be good enough to detect
   "used ~X% of daily budget." A char-based heuristic is fine for the MVP
   and avoids pulling in three vendor tokenizers. Revisit only if a
   routing decision is visibly wrong because of it.

1. **Turn timeout and retry policy.** Turns hard-timeout at 5 minutes
   (kill the subprocess, mark `timed_out`). Transient backend errors
   (non-zero exit without a `result` event, spawn failures) retry at
   most once — BUT only if the failed attempt emitted no
   `SessionIdDelta` and no `TextDelta`. Once the backend has assigned
   a session id or produced partial output, the CLI has mutated state
   (created a remote session, performed native agent work, consumed
   quota); retrying with `-r <id>` against the same session would fork
   or duplicate turns. Post-observation retries must start a fresh
   backend session (no `-r`) and flag the prior attempt `ambiguous` in
   the runtime event log; the router treats `ambiguous` as stronger
   evidence of adapter unhealth than a clean failure. After one retry,
   router marks the adapter `unhealthy` and either switches backends
   or returns `503`. There are no idle sessions to close — each turn
   is ephemeral.

1. **Error taxonomy → OpenAI error shapes.** `400` bad request / unknown
   model, `401` missing/invalid API key, `429` pool exhausted or
   per-provider quota hit, `500` adapter bug (logged with traceback),
   `503` all configured backends unhealthy. Error bodies follow OpenAI's
   `{error: {message, type, code}}` shape so Hermes and other clients
   don't need FreelOAder-specific branches.

1. **Config surface.** `freeloader.toml` for the adapter list, model-name
    mapping, quota thresholds, timeouts, and data dir. Env vars
    (`FREELOADER_API_KEY`, `FREELOADER_BIND`, `FREELOADER_DATA_DIR`) for secrets
    and deploy-time overrides. No runtime config reloading in the MVP.

1. **Python 3.11+, `src/freeloader/` layout, `pyproject.toml`, `uv`.**
    Locked before the first file lands. 3.11 for `asyncio.TaskGroup`,
    `Self` type, and the better error locations.

1. **HTTP framework: FastAPI.** async-native, first-class SSE via
    `StreamingResponse`, built-in OpenAPI docs, cheap to swap for
    Starlette if ever needed (FastAPI is Starlette + Pydantic + dep
    injection). Chosen over raw Starlette because Pydantic request
    validation matches OpenAI's JSON Schema client-error shapes with
    zero glue. Chosen over Hypercorn/Quart because FastAPI is what every
    example OpenAI-compatible proxy uses, so clients debugging against
    FreelOAder see familiar error shapes.

1. **Runtime logging: stdlib `logging`; events: the three logs of
    decision #6.** Two audiences, two destinations. stdlib `logging`
    (default `WARNING`, `INFO` with `FREELOADER_LOG_LEVEL=info`) for
    server-runtime diagnostics: port binding, adapter spawn failures,
    unhandled exceptions, request IDs for correlating stacktraces.
    The three append-only JSONL logs (repo `JOURNAL.jsonl` for
    build-time, `<data_dir>/events.jsonl` for runtime events,
    per-conversation files for messages) carry the architectural
    event stream. Operator-visible diagnostics and
    routing/architectural events have different retention, volume,
    and consumers. No `structlog`, no `loguru` — stdlib can emit JSON
    via a formatter if needed.

1. **Chat Completions conversation identity: hash-of-prefix +
    optional client header.** `/v1/chat/completions` is stateless;
    OpenAI has no `conversation_id` concept. FreelOAder computes a
    conversation key from a stable fingerprint of the message prefix:
    preceding system messages + the first user message, SHA-256'd.
    Well-behaved clients that resend the same history produce the
    same key — turn N of a conversation matches turn N-1 from the
    same client. Clients that want explicit control send
    `X-FreelOAder-Conversation-Id: <opaque>`; when present, the header
    wins over the hash. Prefix-hash failure modes (mid-conversation
    system-prompt edits, regeneration of turn 1) surface as
    "conversation not found, start fresh" — no fuzzy matching, no
    LRU recovery; any divergence means a new conversation. Simplest
    thing that works for the 90% case (chat clients that resend
    history unchanged); clean fallback for the 10% (power users that
    want stable ids). Revisit only if real clients demand more.

1. **Responses API: FreelOAder-owned response IDs, internal mapping.**
    Each `/v1/responses` response gets an opaque
    FreelOAder-generated `response_id` (e.g., `fr_resp_<uuid>`).
    Internally, the response_id maps to `(conversation_id, turn_id,
    backend_session_id at that turn)`. Clients that send
    `previous_response_id` resolve to the conversation + turn +
    backend binding at *that specific point in history* — the mapping
    survives provider switches (the old response_id still resolves
    correctly even after the conversation has moved to a new
    backend). Backend session ids never appear in any OpenAI-facing
    field; they live only in the per-conversation log and the
    internal mapping table. This is the answer to codex's P1 critique
    that "`previous_response_id` → backend session id directly"
    leaked backend identity into the API shape.

1. **CLI-side state isolation per conversation.** The sandboxed
    *cwd* from decision #3 is not enough. Each CLI stores session
    state (thread IDs, history caches, config) in a global location
    — `~/.claude/`, `~/.codex/`, `~/.config/gemini/` — so two
    concurrent turns on different conversations mutate the same
    global state. Race conditions: gemini's `-r latest` resolves
    to whichever session was last written; session-index
    assignment can collide; context leaks between conversations.
    Each CLI subprocess runs with an isolated state root at
    `<data_dir>/cli-state/<conversation_id>/` and the CLI-specific
    env var that redirects state there: `CLAUDE_CONFIG_DIR`,
    `CODEX_HOME`, `XDG_CONFIG_HOME` + `XDG_DATA_HOME` for gemini.
    Adapters resolve the exact env-var name during their own
    implementation phase and capture the finding in JOURNAL. OAuth
    credentials remain in the user's global state and are inherited
    via process env; only the state/cache/session dirs are
    redirected. On FreelOAder restart, all `<data_dir>/cli-state/`
    subdirs are torn down — matches "no session persistence across
    restarts" (§ Things to not do) and removes orphaned CLI threads.
    For CLIs without an isolation env-var, fallback is a
    per-provider mutex (turns on that provider serialize globally),
    caught at adapter-implementation time.

### Still open — need per-CLI investigation

- **System prompt handling — Protocol shape resolved; per-adapter strategy
  still open.** The Protocol carries `system: str | None = None` (see
  principle #1), so the slot exists from day one. What each adapter
  *does* with the slot is still per-CLI: claude exposes
  `--system-prompt` / `--append-system-prompt` (limited under OAuth);
  gemini has no obvious injection point from top-level help; codex allows
  injection via `-c` config overrides. The three fallback options —
  prepend as the first user turn with a marker, use a CLI-specific
  injection mechanism, or ignore the client's system message — are
  chosen *per adapter*. `ClaudeAdapter` resolves its choice during MVP
  step 1; the other two during step 3.

- **Tool-call story** (already deferred — hard problem #1, MVP step 5).
  The filesystem decision above settles the *backend* half (CLI native
  tools are invisible). The *frontend* half — what to do when a client
  sends `tools=[...]` expecting the model to emit `tool_calls` — still
  needs a call between chat-only (strip), output-parsing shim, and
  passthrough. Keep the system-prompt slot under adapter control so the
  shim option stays open.

---

## MVP — de-risked implementation path

*(Revised after the 2026-04-05 spike. Each step shells out to a backend
CLI per turn — no persistent processes.)*

1. **`ClaudeAdapter`, non-streaming, single conversation.** Shell out with
   `claude -p --verbose --output-format stream-json --session-id <uuid>
   [-r <uuid>] --add-dir <scratch>` per turn, parse JSONL on stdout, assemble
   the final assistant message, return an OpenAI `ChatCompletion` response.
   Strip client-sent `tools` for now. Store `backend_session_id` on the
   conversation. Prove end-to-end: `curl` → FreelOAder → claude → response,
   two turns in the same conversation with context carrying over.
1. **Add streaming.** Map JSONL events to OpenAI SSE deltas live (tiny
   field-mapper — no stream parsing). Also wire cancellation: client
   disconnect → `SIGTERM` the subprocess → mark turn cancelled.
1. **Add `CodexAdapter` + `GeminiAdapter`, dumb round-robin routing.**
   Implementation order is codex-then-gemini: codex is structurally
   closer to claude (clean JSONL stream, server-assigned thread_id),
   gemini is the compound-provider outlier. Doing codex second flushes
   out the `CLIAdapter` Protocol boundaries, the vendor-specific
   session-id shapes, and the provider-switch replay path (principle
   #3) before gemini's `stats.models` quirk lands. Each adapter is
   ~one file: shell-out command builder + JSONL event mapper +
   session-id extractor.
1. **Add quota tracking + threshold switching.** Ingest claude's
   `rate_limit_event` records directly; infer quota pressure for gemini /
   codex from per-turn token usage + 429 detection. Router reads a derived
   view over the event log (principle #5) and picks the next provider on
   threshold breach.
1. **Decide on tool-call story.** By this point it's clear whether
   chat-only covers the real use cases or a shim is needed. Revisit hard
   problem #1 with real data.

---

## Key risks (ranked)

1. **ToS exposure** — blocks any public/shared deployment.
1. **Tool-call translation** — determines whether FreelOAder is usable by agent
   frameworks or only by simple chat clients.
1. **Cross-provider context drift** — switching providers mid-conversation is
   the core value prop and the hardest correctness problem.
1. **Quota signal fidelity** — heuristic routing may over- or under-use a
   given subscription.
1. **CLI output-format instability** — each CLI vendor can change its
   streaming format without notice and break the parser.

*Drafted: 2026-04-05. Revised same day after `claude` / `gemini` / `codex`
capability spike — see "CLI capability matrix" for empirical findings that
collapsed hard problems #2 and #3.*
