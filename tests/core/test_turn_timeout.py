# Step 2.4 — PLAN decision #8: per-turn 5-minute hard timeout. A hung
# CLI must not monopolize a conversation's serializing mutex.
#
# Tests run with a sub-second override so the suite stays fast; the
# 5-minute default is asserted as a separate constant check.
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from freeloader.canonical.deltas import Delta, SessionIdDelta
from freeloader.canonical.messages import CanonicalMessage
from freeloader.router import Router


class _CapturingEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


class _HungAdapter:
    """Yields one SessionIdDelta then blocks forever on an unset event.
    Tracks whether its finally block ran so the test can confirm the
    cleanup path fired."""

    def __init__(self, sid: str = "hung-sid") -> None:
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
            await asyncio.Event().wait()  # never set
        finally:
            self.finally_ran = True


def test_default_timeout_is_five_minutes():
    # PLAN decision #8 documents the cap. If the default changes, that
    # is an architectural decision and the test should be updated
    # deliberately.
    assert Router.DEFAULT_TURN_TIMEOUT_SECONDS == 300.0
    assert Router(turn_timeout_seconds=None).turn_timeout_seconds == 300.0


def test_custom_timeout_constructor_arg():
    r = Router(turn_timeout_seconds=0.5)
    assert r.turn_timeout_seconds == 0.5


async def test_hung_adapter_terminates_within_timeout_with_state_timed_out():
    events = _CapturingEvents()
    adapter = _HungAdapter()
    router = Router(claude=adapter, events=events, turn_timeout_seconds=0.1)

    deltas: list = []
    started = asyncio.get_event_loop().time()
    async for d in router.dispatch(
        conversation_id="conv-timeout-1",
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hello")],
    ):
        deltas.append(d)
    elapsed = asyncio.get_event_loop().time() - started

    # Should terminate within a small fraction over the timeout, well
    # under the test's safety ceiling.
    assert elapsed < 1.0, f"dispatch took {elapsed:.3f}s; timeout was 0.1s"

    # Consumer received the one delta the adapter produced before
    # blocking, then end-of-stream (no exception leaked).
    assert len(deltas) == 1
    assert isinstance(deltas[0], SessionIdDelta)

    # turn_done event recorded with state=timed_out.
    assert len(events.events) == 1
    e = events.events[0]
    assert e["state"] == "timed_out"
    assert e["outcome"] == "timed_out"

    # PLAN decision #5 logic extends to forcibly killed turns: binding
    # NOT written; the next turn will start a fresh backend session.
    assert "conv-timeout-1" not in router._bindings

    # The adapter's finally block ran (via aclose() in router's outer
    # finally), proving the cleanup path fires on timeout.
    assert adapter.finally_ran is True


async def test_normal_completion_under_timeout_unaffected():
    """Sanity check: a fast turn under the timeout still reaches
    state=complete and writes the binding."""

    class _FastAdapter:
        async def send(
            self, prompt, *, conversation_id, session_id, resume_session_id=None
        ) -> AsyncIterator[Delta]:
            from freeloader.canonical.deltas import (
                FinishDelta,
                ModelUsage,
                UsageDelta,
            )

            yield SessionIdDelta(session_id="ok-sid")
            yield FinishDelta(reason="stop")
            yield UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=1, output_tokens=1)}
            )

    events = _CapturingEvents()
    router = Router(claude=_FastAdapter(), events=events, turn_timeout_seconds=5.0)
    async for _ in router.dispatch(
        conversation_id="conv-fast",
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hi")],
    ):
        pass

    assert events.events[0]["state"] == "complete"
    assert router._bindings["conv-fast"] == ("claude", "ok-sid")
