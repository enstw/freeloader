# Decision #16: each codex subprocess runs with CODEX_HOME redirected
# to <data_dir>/cli-state/<conversation_id>/codex/. Without isolation,
# `codex exec resume` could resolve to whichever thread was last
# written by *any* conversation.
#
# Verified by monkey-patching asyncio.create_subprocess_exec; no live
# codex. Mirror of test_claude_state_isolation.py.
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from freeloader.adapters.codex import CodexAdapter


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

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


async def test_codex_home_points_at_per_conversation_state(tmp_path, captured_spawn):
    adapter = CodexAdapter(data_dir=tmp_path)
    _ = [
        d
        async for d in adapter.send("hi", conversation_id="conv-A", session_id="sess-1")
    ]

    env = captured_spawn["env"]
    assert env is not None, "subprocess must run with explicit env"
    expected = tmp_path / "cli-state" / "conv-A" / "codex"
    assert env["CODEX_HOME"] == str(expected)
    assert expected.is_dir()


async def test_two_conversations_get_separate_codex_homes(tmp_path, monkeypatch):
    seen: list[dict] = []

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        seen.append(env)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = CodexAdapter(data_dir=tmp_path)
    _ = [d async for d in adapter.send("hi", conversation_id="conv-X", session_id="s1")]
    _ = [d async for d in adapter.send("hi", conversation_id="conv-Y", session_id="s2")]

    assert seen[0]["CODEX_HOME"] == str(tmp_path / "cli-state" / "conv-X" / "codex")
    assert seen[1]["CODEX_HOME"] == str(tmp_path / "cli-state" / "conv-Y" / "codex")
    assert seen[0]["CODEX_HOME"] != seen[1]["CODEX_HOME"]


async def test_env_merge_keeps_oauth_relevant_inherited_vars(
    tmp_path, captured_spawn, monkeypatch
):
    monkeypatch.setenv("FREELOADER_TEST_INHERITED", "still-here")

    adapter = CodexAdapter(data_dir=tmp_path)
    _ = [d async for d in adapter.send("hi", conversation_id="conv-Z", session_id="s9")]

    env = captured_spawn["env"]
    assert env["FREELOADER_TEST_INHERITED"] == "still-here"
    assert env.get("PATH") == os.environ.get("PATH")
    assert env["CODEX_HOME"] == str(tmp_path / "cli-state" / "conv-Z" / "codex")


async def test_resume_call_uses_same_codex_home_as_first_turn(tmp_path, monkeypatch):
    """`codex exec resume` reads its session store from CODEX_HOME; if
    turn 2 used a different CODEX_HOME than turn 1, resume would fail
    to find the thread."""
    seen_homes: list[str] = []
    seen_argvs: list[list[str]] = []

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        seen_homes.append(env["CODEX_HOME"])
        seen_argvs.append(list(argv))
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = CodexAdapter(data_dir=tmp_path)
    _ = [
        d
        async for d in adapter.send(
            "hi", conversation_id="conv-stable", session_id="rand-1"
        )
    ]
    _ = [
        d
        async for d in adapter.send(
            "again",
            conversation_id="conv-stable",
            session_id="thread-id-from-turn-1",
            resume_session_id="thread-id-from-turn-1",
        )
    ]

    assert (
        seen_homes[0]
        == seen_homes[1]
        == str(tmp_path / "cli-state" / "conv-stable" / "codex")
    )
    assert Path(seen_homes[0]).is_dir()

    # Sanity: turn 2 used the resume verb, turn 1 did not.
    assert "resume" not in seen_argvs[0]
    assert "resume" in seen_argvs[1]
