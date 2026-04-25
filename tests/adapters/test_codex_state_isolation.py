# Originally written to lock in PLAN decision #16's per-conversation
# CODEX_HOME redirection. Reverted 2026-04-25 alongside the analogous
# claude finding: codex reads OAuth from $CODEX_HOME, so redirecting
# strips auth and the CLI returns 401 Unauthorized. This file now guards
# the OPPOSITE invariant: codex must inherit the user's environment
# unchanged so OAuth from `codex login` resolves correctly.
#
# Concurrent-turn race on the user-global ~/.codex/ store is mitigated
# by codex's server-assigned thread_id keying — `exec resume <thread_id>`
# finds the right thread regardless of cross-conversation writes.
from __future__ import annotations

import asyncio
import os

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


async def test_codex_home_is_NOT_redirected(tmp_path, captured_spawn):
    # Regression: redirecting CODEX_HOME per-conversation strips OAuth
    # (codex reads its credentials from $CODEX_HOME). Adapter must NOT
    # touch CODEX_HOME — auth resolves from the user's global ~/.codex/
    # via the un-overridden default.
    adapter = CodexAdapter(data_dir=tmp_path)
    _ = [
        d
        async for d in adapter.send("hi", conversation_id="conv-A", session_id="sess-1")
    ]

    env = captured_spawn["env"]
    assert env is not None, "subprocess must run with explicit env"
    assert env.get("CODEX_HOME") == os.environ.get("CODEX_HOME")


async def test_two_conversations_share_user_global_codex_state(tmp_path, monkeypatch):
    seen_envs: list[dict] = []

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
        seen_envs.append(env)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = CodexAdapter(data_dir=tmp_path)
    _ = [d async for d in adapter.send("hi", conversation_id="conv-X", session_id="s1")]
    _ = [d async for d in adapter.send("hi", conversation_id="conv-Y", session_id="s2")]

    assert seen_envs[0].get("CODEX_HOME") == seen_envs[1].get("CODEX_HOME")


async def test_env_inherits_oauth_relevant_vars(tmp_path, captured_spawn, monkeypatch):
    monkeypatch.setenv("FREELOADER_TEST_INHERITED", "still-here")

    adapter = CodexAdapter(data_dir=tmp_path)
    _ = [d async for d in adapter.send("hi", conversation_id="conv-Z", session_id="s9")]

    env = captured_spawn["env"]
    assert env["FREELOADER_TEST_INHERITED"] == "still-here"
    assert env.get("PATH") == os.environ.get("PATH")
    assert env.get("HOME") == os.environ.get("HOME")


async def test_resume_argv_shape_unchanged(tmp_path, monkeypatch):
    """Sanity: the env change does not affect the resume-vs-first-turn argv
    shape (the load-bearing invariant for codex thread continuation)."""
    seen_argvs: list[list[str]] = []

    async def fake_exec(*argv, cwd=None, env=None, **kwargs):
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

    assert "resume" not in seen_argvs[0]
    assert "resume" in seen_argvs[1]
