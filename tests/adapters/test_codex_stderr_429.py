# Step 4.2b: CodexAdapter.send must surface upstream 429s from
# stderr as a synthetic RateLimitDelta. The matcher itself is unit-
# tested in tests/core/test_quota_stderr_429.py; these tests prove
# the adapter wires stderr into the matcher correctly: concurrent
# drain (no pipe-buffer deadlock), exit-code gating, delta yielded
# alongside any stdout deltas the CLI managed to emit.
from __future__ import annotations

import asyncio
from collections.abc import Iterable

from freeloader.adapters.codex import CodexAdapter
from freeloader.canonical.deltas import RateLimitDelta


class _ChunkReader:
    """Async iterator over a fixed sequence of byte chunks, mimicking
    asyncio.StreamReader's `async for line in stream` shape."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeProc:
    def __init__(
        self,
        *,
        stdout_chunks: Iterable[bytes] = (),
        stderr_chunks: Iterable[bytes] = (),
        returncode: int = 0,
    ) -> None:
        self.stdout = _ChunkReader(stdout_chunks)
        self.stderr = _ChunkReader(stderr_chunks)
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


def _patch_proc(monkeypatch, proc: _FakeProc) -> None:
    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


async def test_codex_429_in_stderr_yields_rate_limit_delta(tmp_path, monkeypatch):
    # CLI writes nothing to stdout, exits non-zero, dumps a 429 to
    # stderr. Adapter must yield exactly one RateLimitDelta
    # (rate_limit_type="429", status="exceeded") before returning.
    proc = _FakeProc(
        stdout_chunks=(),
        stderr_chunks=(b"error: HTTP 429 Too Many Requests\n",),
        returncode=1,
    )
    _patch_proc(monkeypatch, proc)
    adapter = CodexAdapter(data_dir=tmp_path)

    deltas = [
        d
        async for d in adapter.send("hi", conversation_id="conv-A", session_id="sess-1")
    ]

    rate_limit_deltas = [d for d in deltas if isinstance(d, RateLimitDelta)]
    assert len(rate_limit_deltas) == 1
    rl = rate_limit_deltas[0]
    assert rl.rate_limit_type == "429"
    assert rl.status == "exceeded"
    assert rl.raw["provider"] == "codex"
    assert rl.raw["exit_code"] == 1
    assert "429" in rl.raw["stderr_excerpt"]


async def test_codex_clean_run_emits_no_rate_limit_delta(tmp_path, monkeypatch):
    # Successful turn: stderr may be empty or contain benign telemetry,
    # exit code zero. No RateLimitDelta must be yielded.
    proc = _FakeProc(
        stdout_chunks=(
            b'{"type":"thread.started","thread_id":"t1"}\n',
            b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n',
            b'{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":1}}\n',
        ),
        stderr_chunks=(b"info: warming cache\n",),
        returncode=0,
    )
    _patch_proc(monkeypatch, proc)
    adapter = CodexAdapter(data_dir=tmp_path)

    deltas = [
        d
        async for d in adapter.send("hi", conversation_id="conv-A", session_id="sess-1")
    ]

    assert [d for d in deltas if isinstance(d, RateLimitDelta)] == []


async def test_codex_nonzero_exit_no_quota_pattern_emits_no_rate_limit_delta(
    tmp_path, monkeypatch
):
    # Non-quota CLI failure (auth error, network error, etc.) must
    # NOT produce a 429 quota_signal — that would mis-label real
    # failures as rate-limit pressure and confuse the strategy.
    proc = _FakeProc(
        stdout_chunks=(),
        stderr_chunks=(b"error: 401 Unauthorized\n",),
        returncode=1,
    )
    _patch_proc(monkeypatch, proc)
    adapter = CodexAdapter(data_dir=tmp_path)

    deltas = [
        d
        async for d in adapter.send("hi", conversation_id="conv-B", session_id="sess-1")
    ]

    assert [d for d in deltas if isinstance(d, RateLimitDelta)] == []


async def test_codex_429_after_partial_stdout_still_yields_delta(tmp_path, monkeypatch):
    # CLI may emit some JSONL (e.g. thread.started) before hitting a
    # 429. The adapter must yield those deltas first, then the
    # RateLimitDelta — order matches when each was observed.
    proc = _FakeProc(
        stdout_chunks=(b'{"type":"thread.started","thread_id":"t-partial"}\n',),
        stderr_chunks=(b"error: rate limit exceeded\n",),
        returncode=1,
    )
    _patch_proc(monkeypatch, proc)
    adapter = CodexAdapter(data_dir=tmp_path)

    deltas = [
        d
        async for d in adapter.send("hi", conversation_id="conv-C", session_id="sess-1")
    ]

    rl_deltas = [d for d in deltas if isinstance(d, RateLimitDelta)]
    assert len(rl_deltas) == 1
    # SessionIdDelta arrives before the RateLimitDelta.
    rl_index = next(i for i, d in enumerate(deltas) if isinstance(d, RateLimitDelta))
    other_kinds_before = {type(d).__name__ for d in deltas[:rl_index]}
    assert "SessionIdDelta" in other_kinds_before
