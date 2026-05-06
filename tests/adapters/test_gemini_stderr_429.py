# Step 4.2b: GeminiAdapter.send must surface upstream 429s from
# stderr as a synthetic RateLimitDelta. Mirrors test_codex_stderr_429.py
# — same matcher, same wiring discipline; gemini-specific patterns
# (RESOURCE_EXHAUSTED) are exercised here.
from __future__ import annotations

import asyncio
from collections.abc import Iterable

from freeloader.adapters.gemini import GeminiAdapter
from freeloader.canonical.deltas import RateLimitDelta


class _ChunkReader:
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


async def test_gemini_resource_exhausted_yields_rate_limit_delta(tmp_path, monkeypatch):
    # Google AI Platform surfaces quota errors as RESOURCE_EXHAUSTED;
    # gemini-cli forwards that to stderr.
    proc = _FakeProc(
        stdout_chunks=(),
        stderr_chunks=(b"[GoogleGenerativeAI Error]: code: 8 RESOURCE_EXHAUSTED\n",),
        returncode=1,
    )
    _patch_proc(monkeypatch, proc)
    adapter = GeminiAdapter(data_dir=tmp_path)

    deltas = [
        d
        async for d in adapter.send("hi", conversation_id="conv-A", session_id="sess-1")
    ]

    rl = [d for d in deltas if isinstance(d, RateLimitDelta)]
    assert len(rl) == 1
    assert rl[0].rate_limit_type == "429"
    assert rl[0].status == "exceeded"
    assert rl[0].raw["provider"] == "gemini"
    assert "RESOURCE_EXHAUSTED" in rl[0].raw["stderr_excerpt"]


async def test_gemini_clean_run_emits_no_rate_limit_delta(tmp_path, monkeypatch):
    proc = _FakeProc(
        stdout_chunks=(
            b'{"type":"init","session_id":"s1"}\n',
            b'{"type":"message","role":"assistant","content":"ok"}\n',
            b'{"type":"result","status":"success","stats":{"input_tokens":3,"output_tokens":1}}\n',
        ),
        stderr_chunks=(),
        returncode=0,
    )
    _patch_proc(monkeypatch, proc)
    adapter = GeminiAdapter(data_dir=tmp_path)

    deltas = [
        d
        async for d in adapter.send("hi", conversation_id="conv-A", session_id="sess-1")
    ]

    assert [d for d in deltas if isinstance(d, RateLimitDelta)] == []


async def test_gemini_429_http_style_pattern(tmp_path, monkeypatch):
    proc = _FakeProc(
        stdout_chunks=(),
        stderr_chunks=(b"Error: 429 Too Many Requests\n",),
        returncode=1,
    )
    _patch_proc(monkeypatch, proc)
    adapter = GeminiAdapter(data_dir=tmp_path)

    deltas = [
        d
        async for d in adapter.send("hi", conversation_id="conv-D", session_id="sess-1")
    ]

    rl = [d for d in deltas if isinstance(d, RateLimitDelta)]
    assert len(rl) == 1
    assert rl[0].rate_limit_type == "429"
