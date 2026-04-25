# Router drives the turn state machine: the terminal it reaches must
# match what the adapter's Delta stream tells it, and that terminal
# must end up in the runtime event log alongside the existing outcome
# field.
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    RateLimitDelta,
    SessionIdDelta,
    TextDelta,
    UsageDelta,
)
from freeloader.canonical.messages import CanonicalMessage
from freeloader.router import Router


class _CapturingEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


class _RaisingEvents:
    def write(self, event: dict) -> None:
        raise OSError("disk full")


class _ScriptedAdapter:
    def __init__(self, deltas: list[Delta]) -> None:
        self._deltas = deltas

    async def send(
        self,
        prompt: str,
        *,
        conversation_id: str,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        for d in self._deltas:
            yield d


async def _drain(router: Router, conv_id: str = "conv-test") -> list[Delta]:
    out: list[Delta] = []
    async for d in router.dispatch(
        conversation_id=conv_id,
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hi")],
    ):
        out.append(d)
    return out


async def test_happy_path_terminal_is_complete():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s1"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=3, output_tokens=1)}
            ),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)
    assert len(events.events) == 1
    e = events.events[0]
    assert e["state"] == "complete"
    assert e["outcome"] == "stop"
    assert e["backend_session_id"] == "s1"


async def test_finish_error_terminal_is_backend_error():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s2"),
            TextDelta(text="partial"),
            FinishDelta(reason="error"),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)
    assert events.events[0]["state"] == "backend_error"
    assert events.events[0]["outcome"] == "error"


async def test_rate_limit_exceeded_terminal_is_rate_limited():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s3"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="exceeded",
                resets_at=1234567890,
                raw={"status": "exceeded"},
            ),
            TextDelta(text="still streamed"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=3, output_tokens=1)}
            ),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)
    e = events.events[0]
    # rate_limit_exceeded supersedes a clean stop.
    assert e["state"] == "rate_limited"
    # outcome still mirrors finish_reason (stop) — they're separate facets.
    assert e["outcome"] == "stop"


async def test_rate_limit_status_allowed_does_not_flip_terminal():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s4"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="allowed",
                raw={"status": "allowed"},
            ),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)
    assert events.events[0]["state"] == "complete"


async def test_empty_stream_terminal_is_backend_error():
    # Adapter yielded nothing — never reached STREAMING. Treated as
    # backend_error, not silent success.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter([])
    router = Router(claude=adapter, events=events)
    await _drain(router)
    e = events.events[0]
    assert e["state"] == "backend_error"
    assert e["outcome"] == "error"


class _BlockingAdapter:
    """First yields a SessionIdDelta then waits forever — modelling a
    long-running streaming turn. Tracks whether its finally block ran."""

    def __init__(self, sid: str = "blocked-sid") -> None:
        self.sid = sid
        self.finally_ran = False

    async def send(
        self,
        prompt: str,
        *,
        conversation_id: str,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        try:
            yield SessionIdDelta(session_id=self.sid)
            # Block on an event that nobody sets.
            await asyncio.Event().wait()
        finally:
            self.finally_ran = True


async def test_cancellation_drives_state_to_cancelled_and_skips_binding():
    # Step 2.3 exit criterion: cancellation marks the turn cancelled
    # and discards the backend session id (PLAN decision #5).
    events = _CapturingEvents()
    adapter = _BlockingAdapter(sid="poisoned-sid")
    router = Router(claude=adapter, events=events)

    gen = router.dispatch(
        conversation_id="conv-x",
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hi")],
    )
    first = await gen.__anext__()
    assert isinstance(first, SessionIdDelta)
    await gen.aclose()

    assert adapter.finally_ran is True
    # turn_done was written with state=cancelled.
    assert len(events.events) == 1
    e = events.events[0]
    assert e["state"] == "cancelled"
    assert e["outcome"] == "cancelled"
    # backend_session_id is preserved in the journal (forensic trail)
    # but NOT bound for resume — the next turn starts fresh.
    assert e["backend_session_id"] == "poisoned-sid"
    assert "conv-x" not in router._bindings


async def test_adapter_exception_drives_state_to_backend_error():
    class _BoomAdapter:
        async def send(
            self, prompt, *, conversation_id, session_id, resume_session_id=None
        ) -> AsyncIterator[Delta]:
            yield SessionIdDelta(session_id="b1")
            raise RuntimeError("kaboom")

    events = _CapturingEvents()
    router = Router(claude=_BoomAdapter(), events=events)
    with pytest.raises(RuntimeError, match="kaboom"):
        await _drain(router)
    assert events.events[0]["state"] == "backend_error"
    # On a mid-stream failure, the binding is not committed — the
    # post-loop block where binding is written never runs.
    assert "conv-test" not in router._bindings


async def test_journal_write_failure_logged_not_silent(caplog):
    # Step 2.2 exit criterion: a journal write failure must surface as
    # an observable signal, not a silent success.
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s5"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
        ]
    )
    router = Router(claude=adapter, events=_RaisingEvents())
    with caplog.at_level(logging.ERROR, logger="freeloader.router"):
        await _drain(router)
    matching = [r for r in caplog.records if "turn journal write failed" in r.message]
    assert matching, f"expected error log, got {[r.message for r in caplog.records]}"
    rec = matching[0]
    # The error log must carry the intended state so an operator can
    # reconstruct what was supposed to happen.
    assert rec.intended_state == "complete"
    assert rec.intended_outcome == "stop"
