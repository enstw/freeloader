# Router — dispatches to ClaudeAdapter, manages conversation→backend
# binding (PLAN principle #3), drives the per-turn state machine
# (principle #2), emits turn_done events (principle #7).
#
# Phase 1 scope: one backend. Quota-aware routing (principle #5) lands
# phase 4; round-robin lands phase 3.
from __future__ import annotations

import datetime as _dt
import logging
import uuid
from collections.abc import AsyncIterator
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
from freeloader.core.turn_state import Turn, TurnState
from freeloader.storage import _NoOpEventWriter, default_events

logger = logging.getLogger(__name__)


class _Adapter(Protocol):
    def send(
        self,
        prompt: str,
        *,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]: ...


class Router:
    def __init__(
        self,
        claude: _Adapter | None = None,
        events: object | None = None,
    ) -> None:
        self.claude: _Adapter = claude or ClaudeAdapter()
        self.events = events or default_events()
        # {conversation_id → backend_session_id}. In-memory for phase 1.
        self._bindings: dict[str, str] = {}

    async def dispatch(
        self,
        *,
        conversation_id: str,
        stored_messages: list[CanonicalMessage],
        new_messages: list[CanonicalMessage],
    ) -> AsyncIterator[Delta]:
        turn = Turn()  # QUEUED

        backend_sid = self._bindings.get(conversation_id)
        if backend_sid:
            # Resume: send only the new turn; the backend has the history.
            prompt = _flatten_canonical(new_messages)
            resume = backend_sid
        else:
            # First contact with this conversation's backend: replay full
            # canonical history into the first turn (PLAN principle #3).
            prompt = _flatten_canonical(stored_messages + new_messages)
            resume = None
        session_id = backend_sid or str(uuid.uuid4())

        captured_sid = backend_sid
        finish_reason = "error"
        usage: UsageDelta | None = None
        rate_limit_exceeded = False

        turn.goto(TurnState.SPAWNING)
        try:
            async for delta in self.claude.send(
                prompt,
                session_id=session_id,
                resume_session_id=resume,
            ):
                if turn.state is TurnState.SPAWNING:
                    # First delta from the adapter ⇒ subprocess is up
                    # and producing output. Move into streaming.
                    turn.goto(TurnState.STREAMING)

                if isinstance(delta, SessionIdDelta):
                    captured_sid = delta.session_id
                    self._bindings[conversation_id] = delta.session_id
                elif isinstance(delta, FinishDelta):
                    finish_reason = delta.reason
                elif isinstance(delta, UsageDelta):
                    usage = delta
                elif isinstance(delta, RateLimitDelta):
                    if delta.status != "allowed":
                        rate_limit_exceeded = True
                yield delta
        except Exception:  # adapter raised mid-stream
            terminal = TurnState.BACKEND_ERROR
            turn.goto(terminal)
            self._record_terminal(
                turn=turn,
                conversation_id=conversation_id,
                captured_sid=captured_sid or session_id,
                finish_reason="error",
                usage=None,
            )
            raise

        # If the adapter never emitted a SessionIdDelta, fall back to the
        # --session-id we generated so the binding still pins the
        # conversation to a claude session.
        if captured_sid is None:
            captured_sid = session_id
            self._bindings[conversation_id] = session_id

        # Decide the terminal based on what we observed.
        if turn.state is TurnState.SPAWNING:
            # Adapter exited cleanly without yielding anything — that's
            # not a successful turn. No FinishDelta, no text.
            terminal = TurnState.BACKEND_ERROR
            finish_reason = "error"
        elif rate_limit_exceeded:
            # PLAN decision #4: claude reports rate_limit_event with
            # status != "allowed" while still completing the response.
            # Mark the turn rate_limited so the router (phase 4) can act
            # on it; the assistant text is still real.
            terminal = TurnState.RATE_LIMITED
        elif finish_reason == "error":
            terminal = TurnState.BACKEND_ERROR
        else:
            terminal = TurnState.COMPLETE

        turn.goto(terminal)
        self._record_terminal(
            turn=turn,
            conversation_id=conversation_id,
            captured_sid=captured_sid,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _record_terminal(
        self,
        *,
        turn: Turn,
        conversation_id: str,
        captured_sid: str,
        finish_reason: str,
        usage: UsageDelta | None,
    ) -> None:
        event = {
            "ts": _dt.datetime.now(_dt.UTC).isoformat(),
            "kind": "turn_done",
            "conversation_id": conversation_id,
            "backend_session_id": captured_sid,
            "provider": "claude",
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
