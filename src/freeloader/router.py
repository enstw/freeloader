# Router — dispatches to ClaudeAdapter, manages conversation→backend
# binding (PLAN principle #3), emits turn_done events (principle #7).
#
# Phase 1 scope: one backend. Quota-aware routing (principle #5) lands
# phase 4; round-robin lands phase 3.
from __future__ import annotations

import datetime as _dt
import uuid
from collections.abc import AsyncIterator
from typing import Protocol

from freeloader.adapters.claude import ClaudeAdapter
from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    SessionIdDelta,
    UsageDelta,
)
from freeloader.canonical.messages import CanonicalMessage
from freeloader.storage import _NoOpEventWriter, default_events


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
        async for delta in self.claude.send(
            prompt,
            session_id=session_id,
            resume_session_id=resume,
        ):
            if isinstance(delta, SessionIdDelta):
                captured_sid = delta.session_id
                self._bindings[conversation_id] = delta.session_id
            elif isinstance(delta, FinishDelta):
                finish_reason = delta.reason
            elif isinstance(delta, UsageDelta):
                usage = delta
            yield delta

        # If the adapter never emitted a SessionIdDelta, fall back to the
        # --session-id we generated so the binding still pins the
        # conversation to a claude session.
        if captured_sid is None:
            captured_sid = session_id
            self._bindings[conversation_id] = session_id

        self.events.write(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                "kind": "turn_done",
                "conversation_id": conversation_id,
                "backend_session_id": captured_sid,
                "provider": "claude",
                "outcome": finish_reason,
                "usage": (
                    {name: stats.model_dump() for name, stats in usage.models.items()}
                    if usage
                    else {}
                ),
            }
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
