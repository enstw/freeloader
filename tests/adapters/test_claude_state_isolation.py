# Originally written to lock in PLAN decision #16's per-conversation
# CLAUDE_CONFIG_DIR redirection. That decision was reverted post-MVP
# during the first live-claude run: claude reads OAuth credentials from
# $CLAUDE_CONFIG_DIR/.claude.json, so redirecting strips the user's
# auth (the same coupling the gemini lesson already documented for
# GEMINI_CLI_HOME). What this file now guards is the OPPOSITE invariant:
# claude must inherit the user's environment unchanged so OAuth from
# `claude /login` resolves correctly.
#
# Per-conversation isolation for claude is now implicit: each turn uses
# an explicit --session-id (claude has no `-r latest` equivalent that
# would race), and concurrent turns on different conversations don't
# need a shared mutex.
from __future__ import annotations

import asyncio
import os

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


async def test_claude_config_dir_is_NOT_redirected(tmp_path, captured_spawn):
    # Regression: redirecting CLAUDE_CONFIG_DIR per-conversation strips
    # OAuth (claude reads .claude.json from $CLAUDE_CONFIG_DIR if set).
    # The adapter must NOT touch CLAUDE_CONFIG_DIR — auth resolves from
    # the user's global ~/.claude/ via the un-overridden default.
    adapter = ClaudeAdapter(data_dir=tmp_path)
    _ = [
        d
        async for d in adapter.send("hi", conversation_id="conv-A", session_id="sess-1")
    ]

    env = captured_spawn["env"]
    assert env is not None, "subprocess must run with explicit env"
    # Inherit whatever was in os.environ at spawn time, no override.
    assert env.get("CLAUDE_CONFIG_DIR") == os.environ.get("CLAUDE_CONFIG_DIR")


async def test_two_conversations_share_user_global_claude_state(tmp_path, monkeypatch):
    # Without per-conversation CLAUDE_CONFIG_DIR isolation, both turns
    # use the user's global state. They are de-risked from racing by
    # the explicit --session-id per turn, not by config-dir isolation.
    seen_envs: list[dict] = []

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        seen_envs.append(env)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = ClaudeAdapter(data_dir=tmp_path)
    _ = [d async for d in adapter.send("hi", conversation_id="conv-X", session_id="s1")]
    _ = [d async for d in adapter.send("hi", conversation_id="conv-Y", session_id="s2")]

    assert seen_envs[0].get("CLAUDE_CONFIG_DIR") == seen_envs[1].get(
        "CLAUDE_CONFIG_DIR"
    )


async def test_env_inherits_oauth_relevant_vars(tmp_path, captured_spawn, monkeypatch):
    # The whole point of dropping the redirect: anything in os.environ
    # carries through unchanged so OAuth credentials (file path resolved
    # from HOME) and PATH (claude binary discovery) survive.
    monkeypatch.setenv("FREELOADER_TEST_INHERITED", "yes-please")

    adapter = ClaudeAdapter(data_dir=tmp_path)
    _ = [d async for d in adapter.send("hi", conversation_id="conv-Z", session_id="s9")]

    env = captured_spawn["env"]
    assert env["FREELOADER_TEST_INHERITED"] == "yes-please"
    assert env.get("PATH") == os.environ.get("PATH")
    assert env.get("HOME") == os.environ.get("HOME")
