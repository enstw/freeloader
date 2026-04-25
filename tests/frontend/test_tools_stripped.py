# Hard problem #1 (chat-only): tools=[...] and tool_choice get dropped
# with a structured warning. Step 1.6 made the strip explicit and
# observable — 1.3 already accepted the fields silently via extra="allow".
# Step 5.2 (phase-5 decision: chat_only_strip) lifts the silent log into
# an on-the-wire signal so OpenAI clients can detect the strip without
# having to infer it from "the model didn't call my function":
#   - response header `X-FreelOAder-Tool-Mode: chat-only-strip`
#   - non-streaming response message gets `tool_calls: []`
#   - warning log carries `mode` + `conversation_id`
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
from freeloader.frontend.app import (
    TOOL_MODE_HEADER,
    TOOL_MODE_VALUE,
    create_app,
)
from freeloader.router import Router


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send(
        self,
        prompt: str,
        *,
        conversation_id: str,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        self.calls.append(
            {
                "prompt": prompt,
                "conversation_id": conversation_id,
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
    # Empty tools is not a strip → no header, no tool_calls field.
    assert TOOL_MODE_HEADER not in res.headers
    assert "tool_calls" not in res.json()["choices"][0]["message"]


# ---- Step 5.2: structured wire-level signal ----


def _post_with_tools(client: TestClient, *, stream: bool = False, **extra_json):
    body = {
        "model": "freeloader/claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "get_weather", "parameters": {"type": "object"}},
            }
        ],
        "tool_choice": "auto",
        "stream": stream,
        **extra_json,
    }
    return client.post("/v1/chat/completions", json=body)


def test_tool_mode_header_set_on_nonstreaming_when_tools_present():
    client, _ = _client()
    res = _post_with_tools(client)
    assert res.status_code == 200
    assert res.headers[TOOL_MODE_HEADER] == TOOL_MODE_VALUE


def test_tool_calls_empty_list_in_nonstreaming_message_when_tools_present():
    # Explicit `tool_calls: []` (not absent) so OpenAI clients reading the
    # field get a deterministic "no calls" answer instead of KeyError or a
    # silent missing key — that's the whole point of the operator-visible
    # signal.
    client, _ = _client()
    res = _post_with_tools(client)
    msg = res.json()["choices"][0]["message"]
    assert msg["tool_calls"] == []
    assert msg["content"] == "ok"
    assert msg["role"] == "assistant"


def test_no_tool_mode_header_when_tools_absent():
    client, _ = _client()
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert res.status_code == 200
    assert TOOL_MODE_HEADER not in res.headers
    # Byte-for-byte unchanged: no tool_calls field at all (not even empty).
    assert "tool_calls" not in res.json()["choices"][0]["message"]


def test_tool_choice_alone_triggers_signal():
    # Even without `tools`, a `tool_choice` field is operator intent that
    # got dropped — the signal must fire so the client knows.
    client, _ = _client()
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": "auto",
        },
    )
    assert res.status_code == 200
    assert res.headers[TOOL_MODE_HEADER] == TOOL_MODE_VALUE
    assert res.json()["choices"][0]["message"]["tool_calls"] == []


def test_tool_mode_header_set_on_streaming_when_tools_present():
    # SSE: header is set on the StreamingResponse before the first event,
    # so a client that reads headers before bytes can detect the strip
    # immediately.
    client, _ = _client()
    res = _post_with_tools(client, stream=True)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers[TOOL_MODE_HEADER] == TOOL_MODE_VALUE


def test_no_tool_mode_header_on_streaming_when_tools_absent():
    client, _ = _client()
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert TOOL_MODE_HEADER not in res.headers


def test_warning_carries_mode_and_conversation_id(caplog):
    # The 5.2 log enrichment: `mode` lets ops grep for chat-only-strip
    # events globally; `conversation_id` lets them grep per-conversation
    # ("which user's prompt got tools stripped?").
    client, _ = _client()
    caplog.set_level(logging.WARNING, logger="freeloader.frontend.app")
    res = _post_with_tools(client)
    assert res.status_code == 200
    conv_id = res.headers["X-FreelOAder-Conversation-Id"]
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    rec = records[0]
    assert rec.mode == TOOL_MODE_VALUE
    assert rec.conversation_id == conv_id
