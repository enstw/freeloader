# Principle #6 check: the handler wraps a Delta stream into an OpenAI
# ChatCompletion response. Uses a fake adapter so the subprocess path
# stays out of the test (live-claude coverage lands at 1.7 e2e).
from __future__ import annotations

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


def _client(deltas: list[Delta]) -> tuple[TestClient, FakeAdapter]:
    adapter = FakeAdapter(deltas)
    app = create_app(Router(claude=adapter))
    return TestClient(app), adapter


def test_happy_path_returns_openai_chatcompletion_shape():
    deltas: list[Delta] = [
        SessionIdDelta(session_id="sess-1"),
        TextDelta(text="Hello "),
        TextDelta(text="world."),
        FinishDelta(reason="stop"),
        UsageDelta(
            models={"claude-opus-4-6": ModelUsage(input_tokens=7, output_tokens=3)}
        ),
    ]
    client, adapter = _client(deltas)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["model"] == "freeloader/claude"
    assert isinstance(body["created"], int)
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["message"] == {"role": "assistant", "content": "Hello world."}
    assert choice["finish_reason"] == "stop"
    assert body["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }
    # Router flattened into a role-tagged prompt for the adapter.
    assert "[USER]" in adapter.calls[0]["prompt"]
    assert "hi" in adapter.calls[0]["prompt"]
    assert adapter.calls[0]["session_id"]  # fresh UUID generated


def test_stream_true_returns_400():
    client, _ = _client([])
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert res.status_code == 400
    assert "stream" in res.json()["detail"].lower()


def test_error_finish_reason_propagates():
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
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["choices"][0]["finish_reason"] == "error"
    assert body["choices"][0]["message"]["content"] == "partial"
    # No usage → zeros, not an error.
    assert body["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_unknown_request_fields_allowed():
    deltas: list[Delta] = [
        TextDelta(text="ok"),
        FinishDelta(reason="stop"),
        UsageDelta(models={"m": ModelUsage(input_tokens=1, output_tokens=1)}),
    ]
    client, _ = _client(deltas)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            # Client sends tools= and temperature — must not 422.
            "tools": [{"type": "function", "function": {"name": "foo"}}],
            "temperature": 0.7,
        },
    )
    assert res.status_code == 200
