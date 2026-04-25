# Step 2.3 stress test (PLAN decision #5): client disconnect must
# terminate the backing claude subprocess. 50 sequential cycles produce
# zero leaked procs. Uses a mocked subprocess that records terminate /
# kill so the test stays offline.
from __future__ import annotations

import asyncio

import pytest

from freeloader.adapters.claude import ClaudeAdapter
from freeloader.canonical.deltas import SessionIdDelta
from freeloader.canonical.messages import CanonicalMessage
from freeloader.router import Router


class _NeverEndingStdout:
    """Async iterator that emits one init event line, then blocks
    forever — modelling a subprocess that is producing output but the
    consumer drops mid-stream."""

    def __init__(self, init_line: bytes) -> None:
        self._lines = [init_line]
        self._exhausted = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        # Block until cancelled / closed. parse_stream is awaiting us
        # here; aclose / cancel propagates here as CancelledError.
        await self._exhausted.wait()
        raise StopAsyncIteration


class _MockProc:
    """Records terminate / kill / wait. Simulates a CLI that responds
    to SIGTERM by exiting immediately (no SIGKILL escalation needed)."""

    def __init__(self, init_line: bytes) -> None:
        self.pid = 12345
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.stdout = _NeverEndingStdout(init_line)
        # stderr is unused by ClaudeAdapter; supply a safe iterator
        # so accidental access doesn't blow up.
        self.stderr = _NeverEndingStdout(b"")
        self._exited = asyncio.Event()

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15
        self._exited.set()
        # Unblock the never-ending stdout reader so parse_stream's
        # awaiting __anext__ unwinds cleanly into StopAsyncIteration.
        self.stdout._exhausted.set()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self._exited.set()
        self.stdout._exhausted.set()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode


@pytest.fixture
def spawn_recorder(monkeypatch):
    """Patches asyncio.create_subprocess_exec to hand out _MockProcs
    and records every spawn for the test to inspect afterwards."""
    procs: list[_MockProc] = []
    init_line = b'{"type":"system","subtype":"init","session_id":"backend-sid-XYZ"}\n'

    async def fake_exec(*argv, cwd=None, **kwargs):
        proc = _MockProc(init_line)
        procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return procs


async def test_50_disconnect_cycles_terminate_every_subprocess(
    tmp_path, spawn_recorder
):
    adapter = ClaudeAdapter(executable="claude", data_dir=tmp_path)
    for i in range(50):
        gen = adapter.send(prompt="hi", session_id=f"sess-{i}")
        # Pull the first delta — that's the SessionIdDelta from the
        # init line. After this, the generator is suspended awaiting
        # the next stdout line, which will never come.
        first = await gen.__anext__()
        assert isinstance(first, SessionIdDelta)
        # Simulate client disconnect by closing the generator. Python
        # injects GeneratorExit / CancelledError into the suspended
        # await, which runs the adapter's finally block (terminate +
        # rmtree).
        await gen.aclose()

    assert len(spawn_recorder) == 50
    # Every spawned proc must have been terminated.
    leaked = [p for p in spawn_recorder if p.terminate_calls == 0 and p.kill_calls == 0]
    assert leaked == [], f"leaked {len(leaked)} subprocesses"
    # No proc needed escalation to SIGKILL because the mock exits
    # immediately on SIGTERM. The 3-second wait_for ceiling is exercised
    # in step 2.4 (timeout) — here we just verify the SIGTERM path.
    assert all(p.kill_calls == 0 for p in spawn_recorder)


async def test_disconnect_through_router_drives_state_to_cancelled(
    tmp_path, spawn_recorder
):
    """End-to-end check: cancelling the router.dispatch consumer drives
    the turn to `cancelled`, writes the event, and crucially does NOT
    update the backend-session binding (PLAN decision #5)."""

    class _Capture:
        def __init__(self):
            self.events = []

        def write(self, e):
            self.events.append(e)

    events = _Capture()
    adapter = ClaudeAdapter(executable="claude", data_dir=tmp_path)
    router = Router(claude=adapter, events=events)

    gen = router.dispatch(
        conversation_id="conv-cancel-1",
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hello")],
    )
    first = await gen.__anext__()
    assert isinstance(first, SessionIdDelta)
    # Disconnect.
    await gen.aclose()

    # Subprocess was terminated.
    assert len(spawn_recorder) == 1
    assert spawn_recorder[0].terminate_calls == 1

    # Turn was journaled with state=cancelled.
    assert len(events.events) == 1
    e = events.events[0]
    assert e["state"] == "cancelled"
    assert e["outcome"] == "cancelled"

    # Critically: binding was NOT written, so the next turn on this
    # conversation will start a fresh backend session and replay history
    # (PLAN decision #5).
    assert "conv-cancel-1" not in router._bindings
