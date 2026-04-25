# Decision #3: CLI filesystem access is sandboxed and invisible.
# Per-turn scratch dir under data_dir; spawned with --add-dir <scratch>
# and cwd=<scratch>. Verified here by monkeypatching
# asyncio.create_subprocess_exec — no live claude.
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from freeloader.adapters.claude import ClaudeAdapter


class _EmptyReader:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeProc:
    def __init__(self) -> None:
        self.stdout = _EmptyReader()
        self.stderr = _EmptyReader()
        self.returncode = 0

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


@pytest.fixture
def captured_spawn(monkeypatch):
    captured: dict = {}

    async def fake_exec(*argv, cwd=None, **kwargs):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        captured["cwd_exists_at_spawn"] = Path(cwd).is_dir() if cwd else False
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


async def test_send_creates_scratch_and_spawns_with_add_dir(tmp_path, captured_spawn):
    adapter = ClaudeAdapter(data_dir=tmp_path)
    deltas = [
        d
        async for d in adapter.send("hi", conversation_id="conv-1", session_id="conv-1")
    ]
    assert deltas == []  # empty stream

    argv = captured_spawn["argv"]
    cwd = captured_spawn["cwd"]
    assert captured_spawn["cwd_exists_at_spawn"]

    scratch = Path(cwd)
    assert scratch.is_relative_to(tmp_path / "scratch" / "conv-1")

    # argv contains --add-dir <scratch> in order
    assert "--add-dir" in argv
    i = argv.index("--add-dir")
    assert argv[i + 1] == str(scratch)

    # argv also still contains the rest of the baseline flags
    for flag in (
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--session-id",
    ):
        assert flag in argv

    # Per-turn scratch is cleaned up after send() returns
    assert not scratch.exists()


async def test_resume_session_id_emits_r_flag(tmp_path, captured_spawn):
    adapter = ClaudeAdapter(data_dir=tmp_path)
    deltas = [
        d
        async for d in adapter.send(
            "hi",
            conversation_id="conv-2",
            session_id="conv-2",
            resume_session_id="backend-42",
        )
    ]
    assert deltas == []
    argv = captured_spawn["argv"]
    assert "-r" in argv
    assert argv[argv.index("-r") + 1] == "backend-42"


async def test_scratch_path_under_session_id_directory(tmp_path, captured_spawn):
    adapter = ClaudeAdapter(data_dir=tmp_path)
    _ = [
        d
        async for d in adapter.send(
            "hi", conversation_id="conv-xyz", session_id="conv-xyz"
        )
    ]
    scratch = Path(captured_spawn["cwd"])
    assert scratch.parent == tmp_path / "scratch" / "conv-xyz"


def test_resolve_data_dir_prefers_env(monkeypatch, tmp_path):
    from freeloader.config import resolve_data_dir

    monkeypatch.setenv("FREELOADER_DATA_DIR", str(tmp_path / "explicit"))
    assert resolve_data_dir() == tmp_path / "explicit"


def test_resolve_data_dir_falls_back_to_xdg(monkeypatch, tmp_path):
    from freeloader.config import resolve_data_dir

    monkeypatch.delenv("FREELOADER_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert resolve_data_dir() == tmp_path / "xdg" / "freeloader"


def test_resolve_data_dir_final_fallback(monkeypatch):
    from freeloader.config import resolve_data_dir

    monkeypatch.delenv("FREELOADER_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert resolve_data_dir() == Path.home() / ".local" / "share" / "freeloader"
