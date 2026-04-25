# Step 5.3 — end-to-end exercise of `tools=[...]` through chat_only_strip.
#
# Phase-5 decision (JOURNAL kind:decision subject:tool_call_strategy
# choice:chat_only_strip): when an OpenAI client sends function-calling
# fields, FreelOAder strips them, signals the strip on the wire, and
# returns a normal chat response. This file is what gate_5.sh greps for
# (`tests/e2e/test_tool_calls.py`); its existence is gating, but its
# *behavior* is what makes the decision real — it proves the chosen
# strategy works through the production wire-up, not just inside the
# frontend handler.
#
# What this test guards (above and beyond the unit tests in
# tests/frontend/test_tools_stripped.py):
#   - The full pipeline survives a tool-bearing request: identity →
#     stored-load → history_diff → router.dispatch → adapter.send →
#     persistence — none of these layers see or store tool data.
#   - The strip is per-request: a tool-bearing turn followed by a
#     plain turn in the same conversation persists correctly and the
#     plain turn does NOT get a stale Tool-Mode header.
#   - Tool data does not leak into the canonical store (which has no
#     tool_calls role); the assistant's persisted reply is plain text.
#   - Streaming and non-streaming both honor the strip end-to-end.
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    SessionIdDelta,
    TextDelta,
    UsageDelta,
)
from freeloader.frontend.app import (
    TOOL_MODE_HEADER,
    TOOL_MODE_VALUE,
    create_app,
)
from freeloader.router import Router
from freeloader.storage import ConversationStore, EventWriter

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


class _FakeAdapter:
    """Echoes the passed session_id, returns canned text per turn."""

    def __init__(self, texts: list[str]) -> None:
        self._queue = list(texts)
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
        yield SessionIdDelta(session_id=session_id)
        yield TextDelta(text=self._queue.pop(0))
        yield FinishDelta(reason="stop")
        yield UsageDelta(
            models={"claude-opus-4-6": ModelUsage(input_tokens=10, output_tokens=4)}
        )


def _client(tmp_path: Path, texts: list[str]) -> tuple[TestClient, _FakeAdapter]:
    adapter = _FakeAdapter(texts)
    events = EventWriter(tmp_path)
    store = ConversationStore(tmp_path)
    return (
        TestClient(
            create_app(router=Router(claude=adapter, events=events), store=store)
        ),
        adapter,
    )


def test_e2e_nonstreaming_tools_request_returns_chat_only_strip_signal(tmp_path):
    client, adapter = _client(tmp_path, ["I cannot call functions; here is text."])

    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "What's the weather in Tokyo?"}],
            "tools": _TOOLS,
            "tool_choice": "auto",
        },
    )
    assert res.status_code == 200
    assert res.headers[TOOL_MODE_HEADER] == TOOL_MODE_VALUE

    body = res.json()
    msg = body["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "I cannot call functions; here is text."
    # Decision artifact: explicit empty list, not absent.
    assert msg["tool_calls"] == []
    assert body["choices"][0]["finish_reason"] == "stop"

    # Adapter saw a plain prompt — no tool schema, no tool_choice, no
    # function name leaked through.
    assert len(adapter.calls) == 1
    prompt = adapter.calls[0]["prompt"]
    assert "get_weather" not in prompt
    assert "tool_choice" not in prompt
    assert "function" not in prompt.lower() or "functions" in prompt.lower()
    # Sanity: the user's actual question did make it through.
    assert "Tokyo" in prompt


def test_e2e_streaming_tools_request_returns_header_and_clean_sse(tmp_path):
    client, adapter = _client(tmp_path, ["streamed reply"])

    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": _TOOLS,
            "stream": True,
        },
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    # Header is set on the StreamingResponse, before any chunk goes out.
    assert res.headers[TOOL_MODE_HEADER] == TOOL_MODE_VALUE

    # Parse the SSE body and verify the chunks are well-formed and that
    # NO chunk leaks tool_calls (we deliberately don't add it to the
    # streaming wire format — header alone is the signal there).
    body = res.text
    chunks: list[dict] = []
    saw_done = False
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: "), f"bad SSE frame: {frame!r}"
        payload = frame[len("data: ") :]
        if payload == "[DONE]":
            saw_done = True
            continue
        chunks.append(json.loads(payload))
    assert saw_done
    assert chunks, "no SSE chunks parsed"
    for ch in chunks:
        for choice in ch.get("choices", []):
            delta = choice.get("delta") or {}
            assert "tool_calls" not in delta

    # Adapter received a plain prompt; no tool data leaked.
    assert len(adapter.calls) == 1
    assert "get_weather" not in adapter.calls[0]["prompt"]


def test_e2e_strip_is_per_request_subsequent_plain_turn_has_no_signal(tmp_path):
    # A tool-bearing turn followed by a plain turn in the same conversation:
    # the second response must NOT carry the Tool-Mode header (the strip is
    # a property of the request, not of the conversation), and tool data
    # from the first turn must not have leaked into stored history.
    client, adapter = _client(
        tmp_path, ["first reply (after strip)", "second reply (plain)"]
    )

    r1 = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": _TOOLS,
        },
    )
    assert r1.status_code == 200
    assert r1.headers[TOOL_MODE_HEADER] == TOOL_MODE_VALUE
    conv_id = r1.headers["X-FreelOAder-Conversation-Id"]

    r2 = client.post(
        "/v1/chat/completions",
        headers={"X-FreelOAder-Conversation-Id": conv_id},
        json={
            "model": "freeloader/claude",
            "messages": [
                {"role": "user", "content": "weather?"},
                {"role": "assistant", "content": "first reply (after strip)"},
                {"role": "user", "content": "and tomorrow?"},
            ],
        },
    )
    assert r2.status_code == 200
    # No tools in the second request → no header, no tool_calls field.
    assert TOOL_MODE_HEADER not in r2.headers
    assert "tool_calls" not in r2.json()["choices"][0]["message"]

    # Both turns reached the adapter; neither prompt mentions tools.
    assert len(adapter.calls) == 2
    for call in adapter.calls:
        assert "get_weather" not in call["prompt"]
        assert "tool_choice" not in call["prompt"]


def test_e2e_persisted_history_has_no_tool_traces(tmp_path):
    # Canonical store has no tool_calls/tool role; verify nothing
    # tool-shaped landed in the on-disk conversation. The persisted
    # assistant turn is plain text; the persisted user turn is plain
    # text. If a future schema addition leaks tool data into storage,
    # this test catches it.
    client, _ = _client(tmp_path, ["plain reply"])

    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": _TOOLS,
            "tool_choice": "auto",
        },
    )
    assert res.status_code == 200
    conv_id = res.headers["X-FreelOAder-Conversation-Id"]

    store = ConversationStore(tmp_path)
    stored = store.load(conv_id)
    assert len(stored) == 2  # user + assistant
    for msg in stored:
        # CanonicalMessage role never includes "tool" or "tool_call".
        assert msg.role in {"user", "assistant", "system", "developer"}
        # Content is plain string; no nested tool-shape leak.
        assert isinstance(msg.content, str)
        assert "get_weather" not in msg.content
        assert "tool_choice" not in msg.content
