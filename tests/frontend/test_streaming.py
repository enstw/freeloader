# SSE streaming path for /v1/chat/completions.
#
# Exercises the step 2.1 exit criteria: stream=true returns
# text/event-stream, chunks are OpenAI-shaped in the right order,
# stream_options.include_usage gates the usage chunk, and the
# conversation is persisted once the stream drains.
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    SessionIdDelta,
    TextDelta,
    UsageDelta,
)
from freeloader.frontend.app import create_app
from freeloader.router import Router
from freeloader.storage import ConversationStore


class FakeAdapter:
    def __init__(self, deltas: list[Delta]) -> None:
        self._deltas = deltas
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
        for d in self._deltas:
            yield d


def _client(
    deltas: list[Delta], *, store: ConversationStore | None = None
) -> tuple[TestClient, FakeAdapter]:
    adapter = FakeAdapter(deltas)
    app = create_app(Router(claude=adapter), store=store)
    return TestClient(app), adapter


def _parse_sse(body: str) -> tuple[list[dict], bool]:
    """Split an SSE body into parsed chunk dicts + a bool for `[DONE]` seen."""
    chunks: list[dict] = []
    done = False
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: "), f"bad SSE frame: {frame!r}"
        payload = frame[len("data: ") :]
        if payload == "[DONE]":
            done = True
            continue
        chunks.append(json.loads(payload))
    return chunks, done


_DELTAS: list[Delta] = [
    SessionIdDelta(session_id="sess-stream-1"),
    TextDelta(text="Hello "),
    TextDelta(text="world."),
    FinishDelta(reason="stop"),
    UsageDelta(
        models={"claude-opus-4-6": ModelUsage(input_tokens=11, output_tokens=2)}
    ),
]


def test_stream_true_returns_sse_content_type():
    client, _ = _client(_DELTAS)
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
    assert res.headers["X-FreelOAder-Conversation-Id"].startswith("cv_")


def test_stream_emits_role_content_finish_done_in_order():
    client, _ = _client(_DELTAS)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    chunks, done = _parse_sse(res.text)
    assert done is True
    # 1 role + 2 text + 1 finish = 4 chunks (no usage without include_usage).
    assert len(chunks) == 4
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert all(c["model"] == "freeloader/claude" for c in chunks)
    # All four share the same id (one turn = one chunk id).
    assert len({c["id"] for c in chunks}) == 1
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[0]["choices"][0]["finish_reason"] is None
    assert chunks[1]["choices"][0]["delta"] == {"content": "Hello "}
    assert chunks[2]["choices"][0]["delta"] == {"content": "world."}
    # Finish chunk: empty delta, finish_reason set.
    assert chunks[3]["choices"][0]["delta"] == {}
    assert chunks[3]["choices"][0]["finish_reason"] == "stop"


def test_stream_usage_chunk_included_when_requested():
    client, _ = _client(_DELTAS)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )
    chunks, done = _parse_sse(res.text)
    assert done is True
    # role + 2 text + finish + usage = 5.
    assert len(chunks) == 5
    usage = chunks[-1]
    assert usage["choices"] == []
    assert usage["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 2,
        "total_tokens": 13,
    }


def test_stream_usage_chunk_omitted_by_default():
    client, _ = _client(_DELTAS)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            # No stream_options at all — usage chunk must not leak.
        },
    )
    chunks, _ = _parse_sse(res.text)
    for c in chunks:
        assert "usage" not in c


def test_stream_include_usage_false_still_omits_usage():
    client, _ = _client(_DELTAS)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": False},
        },
    )
    chunks, _ = _parse_sse(res.text)
    for c in chunks:
        assert "usage" not in c


def test_stream_error_finish_reason_propagates():
    deltas: list[Delta] = [
        TextDelta(text="partial"),
        FinishDelta(reason="error"),
    ]
    client, _ = _client(deltas)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        },
    )
    chunks, done = _parse_sse(res.text)
    assert done is True
    # role + text + finish
    assert len(chunks) == 3
    assert chunks[-1]["choices"][0]["finish_reason"] == "error"


def test_stream_persists_conversation(tmp_path):
    store = ConversationStore(tmp_path)
    client, _ = _client(_DELTAS, store=store)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert res.status_code == 200
    # Drain the body so the StreamingResponse generator runs to completion
    # (persistence is a side effect inside the generator).
    _ = res.text
    conv_id = res.headers["X-FreelOAder-Conversation-Id"]

    conv_path = tmp_path / "conversations" / f"{conv_id}.jsonl"
    assert conv_path.exists()
    lines = [
        json.loads(line) for line in conv_path.read_text().splitlines() if line.strip()
    ]
    assert [line["role"] for line in lines] == ["user", "assistant"]
    assert lines[0]["content"] == "hi"
    assert lines[1]["content"] == "Hello world."


def test_stream_history_mismatch_returns_400(tmp_path):
    store = ConversationStore(tmp_path)
    client, _ = _client(_DELTAS, store=store)
    # Prime conversation with a non-streaming turn.
    r1 = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert r1.status_code == 200
    conv_id = r1.headers["X-FreelOAder-Conversation-Id"]

    # Streamed follow-up whose first user turn diverges from stored.
    r2 = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [
                {"role": "user", "content": "hello_edited"},
                {"role": "assistant", "content": "Hello world."},
                {"role": "user", "content": "follow-up"},
            ],
            "stream": True,
        },
        headers={"X-FreelOAder-Conversation-Id": conv_id},
    )
    assert r2.status_code == 400
