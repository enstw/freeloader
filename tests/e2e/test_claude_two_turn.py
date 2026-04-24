# End-to-end two-turn smoke test. Closes gate_1's last behavior line.
#
# No live claude: a fake adapter yields SessionIdDelta echoing the
# session_id it was called with, then canned text / finish / usage.
# The test verifies the full pipeline:
#   identity → stored-load → history_diff → router.dispatch →
#   adapter.send → conversation persistence → events.jsonl.
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
from freeloader.frontend.app import create_app
from freeloader.router import Router
from freeloader.storage import ConversationStore, EventWriter


class TwoTurnFakeAdapter:
    """Queues canned TextDeltas per turn; always emits SessionIdDelta
    echoing the passed session_id, FinishDelta(stop), and a UsageDelta."""

    def __init__(self, texts_per_turn: list[str]) -> None:
        self._queue = list(texts_per_turn)
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
        yield SessionIdDelta(session_id=session_id)
        yield TextDelta(text=self._queue.pop(0))
        yield FinishDelta(reason="stop")
        yield UsageDelta(
            models={"claude-opus-4-6": ModelUsage(input_tokens=10, output_tokens=5)}
        )


def _client(tmp_path: Path, adapter: TwoTurnFakeAdapter) -> TestClient:
    events = EventWriter(tmp_path)
    store = ConversationStore(tmp_path)
    router = Router(claude=adapter, events=events)
    return TestClient(create_app(router=router, store=store))


def test_two_turn_conversation_carries_context_via_resume(tmp_path):
    adapter = TwoTurnFakeAdapter(
        texts_per_turn=[
            "The answer is 42.",
            "It means the meaning of life.",
        ]
    )
    client = _client(tmp_path, adapter)

    r1 = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "what is the answer?"}],
        },
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["choices"][0]["message"]["content"] == "The answer is 42."

    conv_id = r1.headers["X-FreelOAder-Conversation-Id"]
    assert conv_id.startswith("cv_")

    # Turn 2: client sends the full history (OpenAI stateless convention).
    r2 = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [
                {"role": "user", "content": "what is the answer?"},
                {"role": "assistant", "content": "The answer is 42."},
                {"role": "user", "content": "what does it mean?"},
            ],
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["choices"][0]["message"]["content"] == "It means the meaning of life."
    # Prefix-hash identity: same leading prefix → same conversation id.
    assert r2.headers["X-FreelOAder-Conversation-Id"] == conv_id

    # Adapter saw two calls. Turn 2 resumes turn 1's backend session id.
    assert len(adapter.calls) == 2
    t1_call, t2_call = adapter.calls
    assert t1_call["resume_session_id"] is None
    assert t2_call["resume_session_id"] == t1_call["session_id"]

    # Turn 2's prompt carries only the new user turn, not the full history
    # (resume semantics — principle #3 on warm-cache discipline).
    assert "what does it mean?" in t2_call["prompt"]
    assert "The answer is 42." not in t2_call["prompt"]
    assert "what is the answer?" not in t2_call["prompt"]
    # ...but turn 1's prompt DID carry the whole history (full replay
    # into a new backend session).
    assert "what is the answer?" in t1_call["prompt"]

    # Conversation file: 4 canonical lines (u1, a1, u2, a2).
    conv_path = tmp_path / "conversations" / f"{conv_id}.jsonl"
    assert conv_path.exists()
    lines = [
        json.loads(line) for line in conv_path.read_text().splitlines() if line.strip()
    ]
    assert len(lines) == 4
    assert [line["role"] for line in lines] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert lines[0]["content"] == "what is the answer?"
    assert lines[1]["content"] == "The answer is 42."
    assert lines[2]["content"] == "what does it mean?"
    assert lines[3]["content"] == "It means the meaning of life."

    # events.jsonl: 2 turn_done records, both for the same conv, outcome
    # stop, usage populated with the fake model's numbers.
    events_path = tmp_path / "events.jsonl"
    assert events_path.exists()
    events = [
        json.loads(line)
        for line in events_path.read_text().splitlines()
        if line.strip()
    ]
    assert len(events) == 2
    for evt in events:
        assert evt["kind"] == "turn_done"
        assert evt["conversation_id"] == conv_id
        assert evt["provider"] == "claude"
        assert evt["outcome"] == "stop"
        assert evt["backend_session_id"]
        assert evt["usage"] == {
            "claude-opus-4-6": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_input_tokens": 0,
            }
        }


def test_conversation_id_header_override(tmp_path):
    adapter = TwoTurnFakeAdapter(texts_per_turn=["hi"])
    client = _client(tmp_path, adapter)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"X-FreelOAder-Conversation-Id": "my-custom-id"},
    )
    assert r.status_code == 200
    assert r.headers["X-FreelOAder-Conversation-Id"] == "my-custom-id"
    conv_path = tmp_path / "conversations" / "my-custom-id.jsonl"
    assert conv_path.exists()


def test_history_mismatch_returns_400(tmp_path):
    adapter = TwoTurnFakeAdapter(texts_per_turn=["first", "unused"])
    client = _client(tmp_path, adapter)

    r1 = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert r1.status_code == 200
    conv_id = r1.headers["X-FreelOAder-Conversation-Id"]

    # Mid-history edit: client edits the first user turn but pins the
    # same conversation id via header. Stored[0].content != incoming[0].
    # content, so stored is not a prefix of incoming → 400.
    r2 = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [
                {"role": "user", "content": "hello_edited"},
                {"role": "assistant", "content": "first"},
                {"role": "user", "content": "follow-up"},
            ],
        },
        headers={"X-FreelOAder-Conversation-Id": conv_id},
    )
    assert r2.status_code == 400
