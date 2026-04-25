# Router — selects an adapter per turn (PLAN principle #5, simplest
# policy: round-robin across the registered providers), manages the
# conversation→(provider, backend_session_id) binding (principle #3),
# drives the per-turn state machine (principle #2), emits turn_done
# events (principle #7).
#
# Round-robin is the placeholder strategy. Quota-aware routing
# (principle #5) lands phase 4; provider-switch + canonical-history
# replay lands step 3.5.
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Protocol

from freeloader.adapters.claude import ClaudeAdapter
from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    RateLimitDelta,
    SessionIdDelta,
    UsageDelta,
)
from freeloader.canonical.messages import CanonicalMessage
from freeloader.core.quota import build_quota_signal, build_quota_signal_from_usage
from freeloader.core.routing import RoundRobinStrategy
from freeloader.core.turn_state import Turn, TurnState
from freeloader.storage import _NoOpEventWriter, default_events

logger = logging.getLogger(__name__)

# Step 4.2a placeholder constants. 4.4 moves these to freeloader.toml
# (PLAN decision #10). Round numbers, intentionally not tuned to any
# vendor's published limits — tests inject their own values.
INFERENCE_WINDOW_SECONDS: int = 300
INFERENCE_TOKENS_THRESHOLD: int = 1_000_000

# Providers that grow inferred quota signals from token usage. Claude
# is excluded because it has native RateLimitDelta; double-emitting
# would double-count in 4.3's Strategy. New providers default to
# excluded until explicitly added here.
_INFERENCE_PROVIDERS: frozenset[str] = frozenset({"codex", "gemini"})


class _Adapter(Protocol):
    def send(
        self,
        prompt: str,
        *,
        conversation_id: str,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]: ...


class Router:
    # PLAN decision #8: hard cap on per-turn wall-clock so a hung CLI
    # doesn't monopolize a conversation's serializing mutex (decision
    # #1) indefinitely. 5 minutes is the documented default; tests
    # override via the constructor.
    DEFAULT_TURN_TIMEOUT_SECONDS: float = 300.0

    def __init__(
        self,
        claude: _Adapter | None = None,
        codex: _Adapter | None = None,
        gemini: _Adapter | None = None,
        events: object | None = None,
        *,
        turn_timeout_seconds: float | None = None,
        strategy: object | None = None,
        inference_window_seconds: int | None = None,
        inference_tokens_threshold: int | None = None,
        now_monotonic: Callable[[], float] | None = None,
    ) -> None:
        # Build the active provider pool. Ordering follows the kwarg
        # order: claude → codex → gemini. That order is the round-
        # robin cycle order (Python dicts preserve insertion order,
        # which lets us derive the cycle from a single source of
        # truth).
        adapters: dict[str, _Adapter] = {}
        if claude is not None:
            adapters["claude"] = claude
        if codex is not None:
            adapters["codex"] = codex
        if gemini is not None:
            adapters["gemini"] = gemini
        if not adapters:
            # Phase-1 backward compat: no adapters supplied → default
            # to a single ClaudeAdapter. Most existing tests rely on
            # this; the round-robin behavior is opt-in by passing 2+.
            adapters["claude"] = ClaudeAdapter()
        self._adapters: dict[str, _Adapter] = adapters
        self._provider_order: list[str] = list(adapters.keys())
        # Selection policy. Default = round-robin (phase 3); phase 4
        # will plug in a quota-aware strategy via the same kwarg.
        self._strategy = strategy if strategy is not None else RoundRobinStrategy()

        self.events = events or default_events()
        self.turn_timeout_seconds: float = (
            turn_timeout_seconds
            if turn_timeout_seconds is not None
            else self.DEFAULT_TURN_TIMEOUT_SECONDS
        )
        # {conversation_id → (provider_name, backend_session_id | None)}.
        # Provider is recorded so a resumed turn dispatches to the
        # same adapter that started the conversation. The session id
        # is None in two cases:
        #   - bind() was just called (provider switch): next dispatch
        #     replays full history into the new backend, no resume;
        #   - cancellation/timeout discards the binding entirely
        #     (decision #5), in which case the entry is removed, not
        #     stored as None.
        # In-memory for phase 3; durable storage is a later concern.
        self._bindings: dict[str, tuple[str, str | None]] = {}

        # Step 4.2a: per-provider rolling token windows for inference.
        # {provider → list[(monotonic_ts, total_tokens_for_that_turn)]}.
        # In-memory only — restart loses the window, but the durable
        # quota_signal events on disk are the persistent record (the
        # window is just a derived view).
        self._token_windows: dict[str, list[tuple[float, int]]] = {}
        self._inference_window_seconds: int = (
            inference_window_seconds
            if inference_window_seconds is not None
            else INFERENCE_WINDOW_SECONDS
        )
        self._inference_tokens_threshold: int = (
            inference_tokens_threshold
            if inference_tokens_threshold is not None
            else INFERENCE_TOKENS_THRESHOLD
        )
        # Injectable for test determinism — production uses the
        # drift-free monotonic clock; tests pass a manual ticker.
        self._now_monotonic: Callable[[], float] = (
            now_monotonic if now_monotonic is not None else time.monotonic
        )

    @property
    def claude(self) -> _Adapter | None:
        """Backward-compat accessor for the claude adapter slot;
        phase-1 tests reach in here. Returns None if claude isn't in
        the active pool."""
        return self._adapters.get("claude")

    @property
    def _next_provider_idx(self) -> int:
        """Backward-compat read-only view of the strategy's cursor.
        Phase-3 tests reach in here; phase 4's quota strategy may
        not have a meaningful cursor, in which case this raises.
        New tests should not depend on this property."""
        return getattr(self._strategy, "cursor", 0)

    def _pick_next_provider(self) -> str:
        """Delegates to the active selection strategy. Called only on
        first-turn dispatch for a conversation; resumed turns
        dispatch via the binding's recorded provider."""
        return self._strategy.pick(self._provider_order)

    def bind(self, conversation_id: str, new_provider: str) -> None:
        """Pin `conversation_id` to `new_provider`. The next dispatch
        replays the canonical history into the new backend's first
        turn (PLAN principle #3). After that turn completes, the new
        backend's session id is captured and subsequent turns resume
        normally.

        Idempotent only in the trivial case (binding already points
        at this provider with a sid → re-pin to (provider, None) and
        force a replay). Round-robin index is NOT advanced — bind()
        is not a "new conversation" event.
        """
        if new_provider not in self._adapters:
            raise ValueError(
                f"unknown provider {new_provider!r}; "
                f"registered: {sorted(self._adapters)}"
            )
        self._bindings[conversation_id] = (new_provider, None)

    async def dispatch(
        self,
        *,
        conversation_id: str,
        stored_messages: list[CanonicalMessage],
        new_messages: list[CanonicalMessage],
    ) -> AsyncIterator[Delta]:
        turn = Turn()  # QUEUED

        binding = self._bindings.get(conversation_id)
        if binding is None:
            # No binding: round-robin pick, full-history replay, no resume.
            provider = self._pick_next_provider()
            backend_sid = None
            prompt = _flatten_canonical(stored_messages + new_messages)
            resume = None
        else:
            provider, backend_sid = binding
            if backend_sid is None:
                # Pinned via bind() — provider switch in progress. Same
                # shape as a first-contact turn: full-history replay,
                # no resume. The new backend's sid will be observed and
                # committed on completion.
                prompt = _flatten_canonical(stored_messages + new_messages)
                resume = None
            else:
                # Resume: send only the new turn; the backend has the
                # history.
                prompt = _flatten_canonical(new_messages)
                resume = backend_sid
        session_id = backend_sid or str(uuid.uuid4())

        adapter = self._adapters[provider]

        # Holds the backend session id that was *observed* during this turn.
        # Committed to self._bindings only when a non-cancelled terminal is
        # reached — PLAN decision #5 says cancellation discards the id
        # because a SIGTERMed CLI leaves a partial generation in its local
        # state and resuming via that id causes permanent state drift.
        observed_sid: str | None = backend_sid
        finish_reason = "error"
        usage: UsageDelta | None = None
        rate_limit_exceeded = False

        turn.goto(TurnState.SPAWNING)
        adapter_gen = adapter.send(
            prompt,
            conversation_id=conversation_id,
            session_id=session_id,
            resume_session_id=resume,
        )
        try:
            try:
                async with asyncio.timeout(self.turn_timeout_seconds):
                    async for delta in adapter_gen:
                        if turn.state is TurnState.SPAWNING:
                            # First delta from the adapter ⇒ subprocess
                            # is up and producing output. Move into
                            # streaming.
                            turn.goto(TurnState.STREAMING)

                        if isinstance(delta, SessionIdDelta):
                            observed_sid = delta.session_id
                        elif isinstance(delta, FinishDelta):
                            finish_reason = delta.reason
                        elif isinstance(delta, UsageDelta):
                            usage = delta
                            # Step 4.2a: providers without native quota
                            # telemetry get an inferred quota_signal
                            # built from a rolling token window.
                            # Claude is excluded — its native
                            # RateLimitDelta path (above) is the source
                            # of truth and double-emitting would
                            # double-count in 4.3's Strategy.
                            if provider in _INFERENCE_PROVIDERS:
                                self._emit_inferred_quota_signal(
                                    provider=provider,
                                    conversation_id=conversation_id,
                                    usage=delta,
                                )
                        elif isinstance(delta, RateLimitDelta):
                            # PLAN principle #5: every quota observation
                            # gets its own append-only event. Emit before
                            # forwarding so the journal order matches the
                            # order the signal was seen on the wire (a
                            # phase-4.3 strategy reading the log can react
                            # within the same turn). Allowed records are
                            # emitted too — pressure trends need them, not
                            # just the breaches.
                            self._emit_quota_signal(
                                provider=provider,
                                conversation_id=conversation_id,
                                delta=delta,
                            )
                            if delta.status != "allowed":
                                rate_limit_exceeded = True
                        yield delta
            except TimeoutError:
                # PLAN decision #8: hard 5-minute cap. The deadline
                # fired; asyncio injected CancelledError at our await,
                # asyncio.timeout converted it to TimeoutError on
                # context exit. Drive turn → timed_out, journal it,
                # discard the binding (forcibly killed CLI = poisoned
                # session id, same logic as cancellation in 2.3), and
                # return cleanly so the consumer sees end-of-stream.
                turn.goto(TurnState.TIMED_OUT)
                self._record_terminal(
                    turn=turn,
                    conversation_id=conversation_id,
                    captured_sid=observed_sid or session_id,
                    provider=provider,
                    finish_reason="timed_out",
                    usage=usage,
                )
                return
            except (asyncio.CancelledError, GeneratorExit):
                # Client disconnected (CancelledError when an upstream
                # task is cancelled; GeneratorExit when this generator
                # itself was aclose()'d, e.g. by Starlette's
                # StreamingResponse on a dropped HTTP connection). Drive
                # turn → cancelled and discard the backend_session_id
                # (PLAN decision #5: a SIGTERMed CLI leaves a partial
                # generation in its local state and resuming via that
                # id causes permanent state drift).
                turn.goto(TurnState.CANCELLED)
                self._record_terminal(
                    turn=turn,
                    conversation_id=conversation_id,
                    # Record what we observed for the forensic trail, but
                    # do NOT bind: the next turn will start fresh.
                    captured_sid=observed_sid or session_id,
                    provider=provider,
                    finish_reason="cancelled",
                    usage=usage,
                )
                raise
            except Exception:  # adapter raised mid-stream
                turn.goto(TurnState.BACKEND_ERROR)
                self._record_terminal(
                    turn=turn,
                    conversation_id=conversation_id,
                    captured_sid=observed_sid or session_id,
                    provider=provider,
                    finish_reason="error",
                    usage=None,
                )
                raise

            # ---- Normal completion: decide terminal based on observations.
            if turn.state is TurnState.SPAWNING:
                # Adapter exited cleanly without yielding anything — that's
                # not a successful turn. No FinishDelta, no text.
                terminal = TurnState.BACKEND_ERROR
                finish_reason = "error"
            elif rate_limit_exceeded:
                # PLAN decision #4: claude reports rate_limit_event with
                # status != "allowed" while still completing the response.
                # Mark the turn rate_limited so the router (phase 4) can
                # act on it; the assistant text is still real.
                terminal = TurnState.RATE_LIMITED
            elif finish_reason == "error":
                terminal = TurnState.BACKEND_ERROR
            else:
                terminal = TurnState.COMPLETE

            turn.goto(terminal)

            # Commit the binding. Decision #5 only excludes cancelled /
            # timed_out (forcibly killed subprocesses). Other terminals —
            # even backend_error / rate_limited — leave the backend
            # session in a consistent state, so the binding is safe to
            # keep.
            committed_sid = observed_sid or session_id
            self._bindings[conversation_id] = (provider, committed_sid)

            self._record_terminal(
                turn=turn,
                conversation_id=conversation_id,
                captured_sid=committed_sid,
                provider=provider,
                finish_reason=finish_reason,
                usage=usage,
            )
        finally:
            # Ensure the adapter's finally block runs (SIGTERM/SIGKILL +
            # scratch rmtree). aclose() is a no-op when the adapter is
            # already exhausted; on cancellation it's the only way to get
            # the inner generator's cleanup to fire, since `async for`
            # doesn't auto-close on exception.
            await adapter_gen.aclose()

    def _notify_strategy(self, event: dict) -> None:
        """Step 4.3: feed a successfully-written quota_signal to the
        active selection strategy. Duck-typed so RoundRobinStrategy
        (which has no `observe`) keeps working unchanged. Only called
        after a successful journal write so the strategy's view never
        diverges from the durable record."""
        observe = getattr(self._strategy, "observe", None)
        if observe is None:
            return
        try:
            observe(event)
        except Exception as exc:
            # Strategy state degraded; routing falls back to whatever
            # the strategy can do without this event. Same forensic-
            # gap-not-turn-failure rule as the journal-write paths.
            logger.error(
                "strategy.observe failed; routing state may be stale",
                extra={
                    "provider": event.get("provider"),
                    "rate_limit_type": event.get("rate_limit_type"),
                    "status": event.get("status"),
                    "error": str(exc),
                },
            )

    def _emit_inferred_quota_signal(
        self,
        *,
        provider: str,
        conversation_id: str,
        usage: UsageDelta,
    ) -> None:
        """Step 4.2a: append this turn's token total to the provider's
        rolling window, evict expired entries, and emit one synthetic
        quota_signal carrying the windowed pressure picture."""
        now = self._now_monotonic()
        # Sum input + output across all sub-models. Cached input tokens
        # are excluded — they typically don't count toward quota and
        # would distort the pressure signal upward.
        total_tokens = sum(
            m.input_tokens + m.output_tokens for m in usage.models.values()
        )
        window = self._token_windows.setdefault(provider, [])
        window.append((now, total_tokens))
        cutoff = now - self._inference_window_seconds
        # Evict entries older than the window. Linear scan; window
        # stays small at typical turn rates so simple beats clever.
        self._token_windows[provider] = [
            (ts, tokens) for ts, tokens in window if ts >= cutoff
        ]
        window_tokens = sum(tokens for _, tokens in self._token_windows[provider])

        event = build_quota_signal_from_usage(
            provider=provider,
            conversation_id=conversation_id,
            window_seconds=self._inference_window_seconds,
            window_tokens=window_tokens,
            tokens_threshold=self._inference_tokens_threshold,
            ts=_dt.datetime.now(_dt.UTC).isoformat(),
        )
        try:
            self.events.write(event)
        except Exception as exc:
            # Same write-failure discipline as _emit_quota_signal: a
            # forensic gap, not a turn failure. Surface via stdlib
            # logger so operators see degraded inference coverage.
            logger.error(
                "inferred quota_signal write failed; quota stream has a gap",
                extra={
                    "conversation_id": conversation_id,
                    "provider": provider,
                    "window_tokens": window_tokens,
                    "tokens_threshold": self._inference_tokens_threshold,
                    "error": str(exc),
                },
            )
            return
        self._notify_strategy(event)

    def _emit_quota_signal(
        self,
        *,
        provider: str,
        conversation_id: str,
        delta: RateLimitDelta,
    ) -> None:
        event = build_quota_signal(
            provider=provider,
            conversation_id=conversation_id,
            delta=delta,
            ts=_dt.datetime.now(_dt.UTC).isoformat(),
        )
        try:
            self.events.write(event)
        except Exception as exc:
            # A failed quota_signal write is a forensic gap, not a turn
            # failure — the turn is still streaming and the client gets
            # its response. Surface via stdlib logger like _record_terminal
            # so operators can spot a degraded journal.
            logger.error(
                "quota_signal write failed; quota stream has a gap",
                extra={
                    "conversation_id": conversation_id,
                    "provider": provider,
                    "rate_limit_type": delta.rate_limit_type,
                    "status": delta.status,
                    "error": str(exc),
                },
            )
            return
        self._notify_strategy(event)

    def _record_terminal(
        self,
        *,
        turn: Turn,
        conversation_id: str,
        captured_sid: str,
        provider: str,
        finish_reason: str,
        usage: UsageDelta | None,
    ) -> None:
        event = {
            "ts": _dt.datetime.now(_dt.UTC).isoformat(),
            "kind": "turn_done",
            "conversation_id": conversation_id,
            "backend_session_id": captured_sid,
            "provider": provider,
            "state": turn.state.value,
            # Keep `outcome` for backward compat with phase-1 consumers
            # (the e2e test asserts on it). It mirrors finish_reason; the
            # state machine is the new source of truth going forward.
            "outcome": finish_reason,
            "usage": (
                {name: stats.model_dump() for name, stats in usage.models.items()}
                if usage
                else {}
            ),
        }
        try:
            self.events.write(event)
        except Exception as exc:
            # Journal write failed: the in-memory state machine reached
            # a terminal but the event log doesn't reflect it (PLAN
            # principle #2 — that's a consistency gap). Surface via the
            # stdlib logger so an operator sees it; we cannot retroactively
            # change the response that was already emitted to the client.
            logger.error(
                "turn journal write failed; turn state on disk is unknown",
                extra={
                    "conversation_id": conversation_id,
                    "backend_session_id": captured_sid,
                    "intended_state": turn.state.value,
                    "intended_outcome": finish_reason,
                    "error": str(exc),
                },
            )


def _flatten_canonical(messages: list[CanonicalMessage]) -> str:
    # Canonical → role-tagged plaintext. Matches adapters.claude.
    # flatten_messages's output for equivalent inputs; the two exist
    # at slightly different layers (adapter-side for dict inputs vs
    # router-side for canonical inputs) and will consolidate when
    # CodexAdapter / GeminiAdapter arrive at phase 3.
    parts: list[str] = []
    for m in messages:
        role = m.role.upper()
        parts.append(f"[{role}]\n{m.content}\n[/{role}]")
    return "\n\n".join(parts)


# Silence "_NoOpEventWriter imported but unused" — it's re-exported for
# callers that want to explicitly construct it.
_ = _NoOpEventWriter
