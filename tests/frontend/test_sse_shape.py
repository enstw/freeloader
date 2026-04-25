# Step 2.5 — SSE byte-diff vs OpenAI reference.
#
# tests/frontend/test_streaming.py covers chunk *structure*; this file
# pins the *byte sequence*. OpenAI SDK clients are strict about the
# wire shape: chunks separated by `\n\n`, terminator exactly
# `data: [DONE]\n\n`, no trailing whitespace, JSON keys in a stable
# order, usage chunk with `"choices":[]` (not `"choices":null`).
# A semantic match isn't enough — the bytes have to line up.
#
# Reference fixture is hand-written below as a Python string with
# `{ID}` and `{CREATED}` placeholders that the test substitutes from
# the actual response so the comparison is deterministic.
from __future__ import annotations

import json
import re
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


class _ScriptedAdapter:
    def __init__(self, deltas: list[Delta]) -> None:
        self._deltas = deltas

    async def send(
        self,
        prompt: str,
        *,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        for d in self._deltas:
            yield d


_DELTAS: list[Delta] = [
    SessionIdDelta(session_id="sess-shape-1"),
    TextDelta(text="Hello "),
    TextDelta(text="world."),
    FinishDelta(reason="stop"),
    UsageDelta(
        models={"claude-opus-4-6": ModelUsage(input_tokens=11, output_tokens=2)}
    ),
]


# Reference body for the no-include_usage case. Each chunk envelope:
#   id      → substituted from response (volatile per request)
#   created → substituted from response (volatile per request)
#   model   → echoed from request (stable: "freeloader/claude")
#
# Keys appear in the order Python's json.dumps produces from a dict
# constructed in the order our sse module builds it. If json key
# ordering ever changes (e.g. someone replaces our envelope helper
# with a different dict construction), this test catches it before
# clients do.
REFERENCE_NO_USAGE = (
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[{"index":0,"delta":{"content":"Hello "},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[{"index":0,"delta":{"content":"world."},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)

REFERENCE_WITH_USAGE = (
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[{"index":0,"delta":{"content":"Hello "},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[{"index":0,"delta":{"content":"world."},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    'data: {"id":"{ID}","object":"chat.completion.chunk",'
    '"created":{CREATED},"model":"freeloader/claude",'
    '"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":2,'
    '"total_tokens":13}}\n\n'
    "data: [DONE]\n\n"
)


def _client() -> TestClient:
    adapter = _ScriptedAdapter(_DELTAS)
    return TestClient(create_app(Router(claude=adapter)))


def _extract_id_and_created(body: str) -> tuple[str, str]:
    """Pull `id` and `created` out of the first chunk so the test can
    substitute them into the reference fixture without re-parsing."""
    first_data = body.split("\n\n", 1)[0]
    assert first_data.startswith("data: ")
    chunk = json.loads(first_data[len("data: ") :])
    return chunk["id"], str(chunk["created"])


def test_sse_bytes_match_openai_reference_no_usage():
    res = _client().post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert res.status_code == 200
    body = res.text

    chunk_id, created = _extract_id_and_created(body)
    # Our id is one chatcmpl-<32hex>; sanity-check format before substitution.
    assert re.fullmatch(r"chatcmpl-[0-9a-f]{32}", chunk_id), chunk_id
    assert re.fullmatch(r"\d+", created), created

    expected = REFERENCE_NO_USAGE.replace("{ID}", chunk_id).replace(
        "{CREATED}", created
    )
    assert body == expected, _diff(expected, body)


def test_sse_bytes_match_openai_reference_with_usage():
    res = _client().post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )
    assert res.status_code == 200
    body = res.text

    chunk_id, created = _extract_id_and_created(body)
    expected = REFERENCE_WITH_USAGE.replace("{ID}", chunk_id).replace(
        "{CREATED}", created
    )
    assert body == expected, _diff(expected, body)


def test_done_sentinel_is_exactly_terminal():
    """The `data: [DONE]\\n\\n` terminator is a hard contract — OpenAI
    SDK clients treat anything after it as a protocol violation."""
    res = _client().post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert res.text.endswith("data: [DONE]\n\n")
    # And nothing else after — the body ends there exactly.
    tail = res.text.split("data: [DONE]\n\n")
    assert tail[-1] == "", f"trailing bytes after [DONE]: {tail[-1]!r}"


def test_chunk_separator_is_double_newline():
    """OpenAI / EventSource spec: chunks are `\\n\\n`-separated. A
    single newline would silently merge two events on the client."""
    res = _client().post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    # Every non-empty fragment after splitting on \n\n must start with
    # `data: ` (no `event:` lines, no comments, no stray bytes).
    for fragment in res.text.split("\n\n"):
        if fragment == "":
            continue
        assert fragment.startswith("data: "), f"unexpected fragment: {fragment!r}"


def _diff(expected: str, actual: str) -> str:
    """Helper: produce a readable assertion message when the bytes
    don't line up. Walks character-by-character to point at the first
    divergence."""
    for i, (e, a) in enumerate(zip(expected, actual, strict=False)):
        if e != a:
            window = 60
            lo = max(0, i - window)
            return (
                f"\nfirst byte diff at offset {i}\n"
                f"  expected: …{expected[lo : i + window]!r}\n"
                f"  actual:   …{actual[lo : i + window]!r}\n"
            )
    if len(expected) != len(actual):
        return (
            f"\nlength mismatch: expected {len(expected)}, got {len(actual)}\n"
            f"  trailing expected: {expected[len(actual) :]!r}\n"
            f"  trailing actual:   {actual[len(expected) :]!r}\n"
        )
    return "(no diff but assertion failed?)"
