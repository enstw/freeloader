# FastAPI app — /v1/chat/completions (non-streaming for phase 1).
#
# Principle #6: the frontend is dumb. This handler parses, calls
# router.dispatch(), pattern-matches the Delta stream, wraps an OpenAI
# ChatCompletion response. Anything else belongs in the router or adapter.
#
# Cross-phase invariant: this module imports from freeloader.router only;
# never from freeloader.adapters.* directly.
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from freeloader import __version__
from freeloader.canonical.deltas import FinishDelta, TextDelta, UsageDelta
from freeloader.router import Router

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: str | list[dict] | None = None


class ChatCompletionRequest(BaseModel):
    # extra="allow" for temperature / top_p / etc that we ignore silently.
    # tools + tool_choice are recognized so we can log a structured warning
    # when stripped (PLAN hard-problem #1, chat-only mode).
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None


def create_app(router: Router | None = None) -> FastAPI:
    app = FastAPI(title="FreelOAder", version=__version__)
    r = router or Router()

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest) -> dict[str, Any]:
        if req.stream:
            raise HTTPException(
                status_code=400,
                detail="stream=true not yet supported (phase 2)",
            )
        _warn_if_tools_dropped(req)
        messages = [m.model_dump() for m in req.messages]

        text_parts: list[str] = []
        finish_reason = "stop"
        usage: UsageDelta | None = None
        async for delta in r.dispatch(messages):
            if isinstance(delta, TextDelta):
                text_parts.append(delta.text)
            elif isinstance(delta, FinishDelta):
                finish_reason = delta.reason
            elif isinstance(delta, UsageDelta):
                usage = delta

        return _build_chat_completion(
            req.model, "".join(text_parts), finish_reason, usage
        )

    return app


def _warn_if_tools_dropped(req: ChatCompletionRequest) -> None:
    # Chat-only mode: drop tools + tool_choice with a structured warning.
    # Option 3 of PLAN hard problem #1 (shim / passthrough) is phase 5.
    dropped: list[str] = []
    if req.tools:
        dropped.append("tools")
    if req.tool_choice is not None:
        dropped.append("tool_choice")
    if dropped:
        logger.warning(
            "dropped client function-calling fields (chat-only mode)",
            extra={
                "dropped_fields": dropped,
                "model": req.model,
                "path": "/v1/chat/completions",
            },
        )


def _build_chat_completion(
    model: str, text: str, finish_reason: str, usage: UsageDelta | None
) -> dict[str, Any]:
    prompt_tokens = sum(
        m.input_tokens for m in (usage.models.values() if usage else [])
    )
    completion_tokens = sum(
        m.output_tokens for m in (usage.models.values() if usage else [])
    )
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
