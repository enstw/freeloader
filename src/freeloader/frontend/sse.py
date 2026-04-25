# OpenAI-shaped SSE chunk formatters for /v1/chat/completions stream=true.
#
# All chunks share the same envelope (id / object / created / model); only
# the `choices` + optional `usage` payload differs. Each helper returns a
# plain dict so tests can assert structure without re-parsing SSE bytes.
# `sse_encode` wraps a dict into the `data: ...\n\n` wire format.
from __future__ import annotations

import json
from typing import Any

DONE_SENTINEL = b"data: [DONE]\n\n"


def sse_encode(chunk: dict[str, Any]) -> bytes:
    # Compact separators match OpenAI's actual wire bytes — SDK
    # clients tolerate either form, but byte-level fixtures don't.
    payload = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n".encode()


def _envelope(chunk_id: str, created: int, model: str) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
    }


def role_chunk(chunk_id: str, created: int, model: str) -> dict[str, Any]:
    return {
        **_envelope(chunk_id, created, model),
        "choices": [
            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
        ],
    }


def text_chunk(chunk_id: str, created: int, model: str, text: str) -> dict[str, Any]:
    return {
        **_envelope(chunk_id, created, model),
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }


def finish_chunk(
    chunk_id: str, created: int, model: str, reason: str
) -> dict[str, Any]:
    return {
        **_envelope(chunk_id, created, model),
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
    }


def usage_chunk(
    chunk_id: str, created: int, model: str, usage: dict[str, int]
) -> dict[str, Any]:
    # OpenAI's include_usage chunk has empty choices and a top-level usage.
    return {
        **_envelope(chunk_id, created, model),
        "choices": [],
        "usage": usage,
    }
