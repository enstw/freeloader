# PLAN decision #16 fallback: gemini's GEMINI_CLI_HOME couples OAuth
# to state, so per-conversation state isolation via env var is
# unusable. Instead, the GeminiAdapter holds a per-instance asyncio
# Lock that serializes concurrent send() calls — preventing two
# in-flight gemini turns from racing on the global ~/.gemini/
# session store.
#
# The lock is INSTANCE-scoped: two distinct GeminiAdapter instances
# do not block each other. That matters for testing and for any
# future world where two FreelOAder processes run side by side.
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from freeloader.adapters.gemini import GeminiAdapter


class _SlowReader:
    """Async iterator that yields one init line, then waits on an
    event the test controls. Lets the test hold a 'turn' open as
    long as it wants, to observe the lock blocking a second sender."""

    def __init__(self, init_line: bytes, release: asyncio.Event) -> None:
        self._lines = [init_line]
        self._release = release

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        await self._release.wait()
        raise StopAsyncIteration


class _MockProc:
    def __init__(self, init_line: bytes, release: asyncio.Event) -> None:
        self.stdout = _SlowReader(init_line, release)
        self.stderr = _SlowReader(b"", release)
        self.returncode: int | None = None
        self._exited = asyncio.Event()

    def terminate(self) -> None:
        self.returncode = -15
        self._exited.set()

    def kill(self) -> None:
        self.returncode = -9
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode


@pytest.fixture
def spawn_factory(monkeypatch):
    spawned: list[_MockProc] = []
    release_events: list[asyncio.Event] = []

    async def fake_exec(*argv, cwd=None, **kwargs):
        ev = asyncio.Event()
        release_events.append(ev)
        proc = _MockProc(b'{"type":"init","session_id":"u-1","model":"auto"}\n', ev)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return spawned, release_events


async def test_two_concurrent_sends_serialize_via_lock(tmp_path: Path, spawn_factory):
    spawned, releases = spawn_factory

    adapter = GeminiAdapter(data_dir=tmp_path)

    # First send: start it, pull the init delta, then suspend it
    # without finishing.
    gen_a = adapter.send("first", conversation_id="conv-A", session_id="sid-A")
    first_a = await gen_a.__anext__()
    assert first_a.session_id == "u-1"
    assert len(spawned) == 1, "first send must have spawned"

    # Second send while the first is mid-flight. Because of the lock,
    # this should NOT spawn a subprocess until the first releases.
    gen_b = adapter.send("second", conversation_id="conv-B", session_id="sid-B")
    second_task = asyncio.create_task(gen_b.__anext__())

    # Give the event loop several chances to run; if the lock didn't
    # hold, gen_b would have spawned by now.
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(spawned) == 1, "second send must wait on the lock"
    assert not second_task.done()

    # Release the first send: aclose triggers the finally → terminate()
    # → returncode set → wait() returns → lock released → gen_b acquires.
    await gen_a.aclose()

    # Now gen_b's task should be able to make progress.
    first_b = await second_task
    assert first_b.session_id == "u-1"
    assert len(spawned) == 2, "second send must spawn after lock release"

    await gen_b.aclose()


async def test_distinct_adapter_instances_do_not_block_each_other(
    tmp_path: Path, spawn_factory
):
    spawned, _ = spawn_factory

    adapter_a = GeminiAdapter(data_dir=tmp_path)
    adapter_b = GeminiAdapter(data_dir=tmp_path)

    gen_a = adapter_a.send("first", conversation_id="conv-X", session_id="sid-X")
    first_a = await gen_a.__anext__()
    assert first_a.session_id == "u-1"

    # Different adapter instance — its lock is not held by gen_a.
    gen_b = adapter_b.send("second", conversation_id="conv-Y", session_id="sid-Y")
    first_b = await gen_b.__anext__()
    assert first_b.session_id == "u-1"

    # Both spawned without serialization.
    assert len(spawned) == 2

    await gen_a.aclose()
    await gen_b.aclose()


async def test_send_does_not_set_gemini_cli_home(tmp_path: Path, monkeypatch):
    """Empirical finding 2026-04-25: GEMINI_CLI_HOME redirects the
    auth files (oauth_creds.json) too — using it would break OAuth.
    The adapter must NOT pass it; the parent env inherits unchanged.
    """
    captured: dict = {}

    class _EmptyReader:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _FakeProc:
        def __init__(self):
            self.stdout = _EmptyReader()
            self.stderr = _EmptyReader()
            self.returncode = 0

        async def wait(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = GeminiAdapter(data_dir=tmp_path)
    _ = [
        d
        async for d in adapter.send("hi", conversation_id="conv-Z", session_id="sid-Z")
    ]

    # env=None means "inherit parent env unchanged" — the explicit
    # contract that distinguishes gemini from claude/codex (which
    # pass env={**os.environ, "<CLI>_HOME": ...}).
    assert captured["env"] is None, (
        f"GeminiAdapter must not override env; got {captured['env']!r}"
    )
