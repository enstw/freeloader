# Decision #16: each subprocess runs with CLAUDE_CONFIG_DIR redirected
# to <data_dir>/cli-state/<conversation_id>/claude/. Isolates concurrent
# turns on different conversations from sharing global ~/.claude state.
#
# Verified by monkey-patching asyncio.create_subprocess_exec; no live
# claude. The env-merge invariant (OAuth + PATH inherit) is the load-
# bearing piece — replacing env entirely would strip auth.
from __future__ import annotations

import asyncio
import os
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

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


async def test_claude_config_dir_points_at_per_conversation_state(
    tmp_path, captured_spawn
):
    adapter = ClaudeAdapter(data_dir=tmp_path)
    _ = [
        d
        async for d in adapter.send("hi", conversation_id="conv-A", session_id="sess-1")
    ]

    env = captured_spawn["env"]
    assert env is not None, "subprocess must run with explicit env"
    expected = tmp_path / "cli-state" / "conv-A" / "claude"
    assert env["CLAUDE_CONFIG_DIR"] == str(expected)
    # The state dir must exist on disk at spawn time — the CLI may try
    # to read it before any of our subsequent code runs.
    assert expected.is_dir()


async def test_two_conversations_get_separate_state_dirs(tmp_path, monkeypatch):
    seen_envs: list[dict] = []

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        seen_envs.append(env)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = ClaudeAdapter(data_dir=tmp_path)
    _ = [d async for d in adapter.send("hi", conversation_id="conv-X", session_id="s1")]
    _ = [d async for d in adapter.send("hi", conversation_id="conv-Y", session_id="s2")]

    assert seen_envs[0]["CLAUDE_CONFIG_DIR"] == str(
        tmp_path / "cli-state" / "conv-X" / "claude"
    )
    assert seen_envs[1]["CLAUDE_CONFIG_DIR"] == str(
        tmp_path / "cli-state" / "conv-Y" / "claude"
    )
    assert seen_envs[0]["CLAUDE_CONFIG_DIR"] != seen_envs[1]["CLAUDE_CONFIG_DIR"]


async def test_env_merge_keeps_oauth_relevant_inherited_vars(
    tmp_path, captured_spawn, monkeypatch
):
    # Anything in os.environ that is NOT CLAUDE_CONFIG_DIR must
    # carry through. We seed a sentinel and prove it survives.
    monkeypatch.setenv("FREELOADER_TEST_INHERITED", "yes-please")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))  # touch to keep it

    adapter = ClaudeAdapter(data_dir=tmp_path)
    _ = [d async for d in adapter.send("hi", conversation_id="conv-Z", session_id="s9")]

    env = captured_spawn["env"]
    assert env["FREELOADER_TEST_INHERITED"] == "yes-please"
    assert env.get("PATH") == os.environ.get("PATH")
    # And CLAUDE_CONFIG_DIR is the only thing we override.
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cli-state" / "conv-Z" / "claude")


async def test_state_dir_persists_across_turns_of_same_conversation(
    tmp_path, monkeypatch
):
    """The state dir for conv-A must be the same path on turn 2 as on
    turn 1, even though session_id changes (router rotates on resume).
    Otherwise the codex thread or claude session store would be lost
    between turns."""
    seen_state: list[str] = []

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        seen_state.append(env["CLAUDE_CONFIG_DIR"])
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = ClaudeAdapter(data_dir=tmp_path)
    # Turn 1: random session_id (no resume).
    _ = [
        d
        async for d in adapter.send(
            "hi", conversation_id="conv-stable", session_id="rand-1"
        )
    ]
    # Turn 2: backend session id is now known and used.
    _ = [
        d
        async for d in adapter.send(
            "again",
            conversation_id="conv-stable",
            session_id="backend-sid-from-turn-1",
            resume_session_id="backend-sid-from-turn-1",
        )
    ]

    assert (
        seen_state[0]
        == seen_state[1]
        == str(tmp_path / "cli-state" / "conv-stable" / "claude")
    )
    assert (Path(seen_state[0])).is_dir()
