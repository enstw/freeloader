# Hard problem #1 (chat-only): tools=[...] and tool_choice get dropped
# with a structured warning. Step 1.6 makes the strip explicit and
# observable — 1.3 already accepted the fields silently via extra="allow".
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    TextDelta,
    UsageDelta,
)
from freeloader.frontend.app import create_app
from freeloader.router import Router


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send(
        self,
        prompt: str,
        *,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        self.calls.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "resume_session_id": resume_session_id,
            }
        )
        yield TextDelta(text="ok")
        yield FinishDelta(reason="stop")
        yield UsageDelta(models={"m": ModelUsage(input_tokens=1, output_tokens=1)})


def _client() -> tuple[TestClient, FakeAdapter]:
    adapter = FakeAdapter()
    app = create_app(Router(claude=adapter))
    return TestClient(app), adapter


def test_tools_are_dropped_with_warning(caplog):
    client, adapter = _client()
    caplog.set_level(logging.WARNING, logger="freeloader.frontend.app")
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            "tool_choice": "auto",
        },
    )
    assert res.status_code == 200

    # Structured warning emitted naming both dropped fields.
    matching = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(matching) == 1
    rec = matching[0]
    assert "chat-only mode" in rec.message
    assert set(rec.dropped_fields) == {"tools", "tool_choice"}
    assert rec.model == "freeloader/claude"
    assert rec.path == "/v1/chat/completions"

    # Adapter received only a flattened prompt — no tool data leaks through.
    assert len(adapter.calls) == 1
    prompt = adapter.calls[0]["prompt"]
    assert "get_weather" not in prompt
    assert "tool_choice" not in prompt


def test_tools_only_still_warns(caplog):
    client, _ = _client()
    caplog.set_level(logging.WARNING, logger="freeloader.frontend.app")
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "f"}}],
        },
    )
    assert res.status_code == 200
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    assert records[0].dropped_fields == ["tools"]


def test_no_warning_when_tools_absent(caplog):
    client, _ = _client()
    caplog.set_level(logging.WARNING, logger="freeloader.frontend.app")
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert res.status_code == 200
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_empty_tools_list_is_not_a_strip(caplog):
    # `tools: []` is falsy — treating it as present would spam warnings for
    # clients that always send tools=[] out of habit. Only non-empty lists
    # count as a strip.
    client, _ = _client()
    caplog.set_level(logging.WARNING, logger="freeloader.frontend.app")
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [],
        },
    )
    assert res.status_code == 200
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
