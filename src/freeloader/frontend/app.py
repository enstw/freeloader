# FastAPI app — /v1/chat/completions (non-streaming + SSE streaming).
#
# Principle #6: the frontend is dumb. The handler
#   1. parses + strips tools/tool_choice (chat-only mode),
#   2. resolves conversation identity (decision #14),
#   3. loads the stored conversation,
#   4. diffs stored vs incoming (principle #4),
#   5. dispatches the new turn to the router,
#   6. persists the canonical turn + assistant reply,
#   7. wraps the Delta stream in an OpenAI response:
#        - stream=false: ChatCompletion JSON
#        - stream=true:  text/event-stream of chat.completion.chunk SSE
#          frames, terminating with `data: [DONE]\n\n`.
#
# Cross-phase invariant: imports freeloader.router, never adapters.* .
from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from freeloader import __version__
from freeloader.canonical.deltas import FinishDelta, TextDelta, UsageDelta
from freeloader.canonical.history_diff import (
    HistoryMismatchError,
    diff_against_stored,
)
from freeloader.canonical.identity import hash_of_prefix
from freeloader.canonical.messages import CanonicalMessage, openai_to_canonical
from freeloader.config import load_router_config
from freeloader.frontend.sse import (
    DONE_SENTINEL,
    finish_chunk,
    role_chunk,
    sse_encode,
    text_chunk,
    usage_chunk,
)
from freeloader.router import Router
from freeloader.storage import default_store

logger = logging.getLogger(__name__)

# Phase 5 (decision: chat_only_strip): the frontend is in chat-only mode.
# When a request carries `tools` or `tool_choice`, those fields are stripped
# before reaching the router and the response carries an explicit, on-the-
# wire signal so the client can detect the strip without inferring it from
# "the model didn't call my function." Header is set on both streaming and
# non-streaming responses; non-streaming additionally surfaces `tool_calls=[]`
# in the message body (OpenAI clients reading `tool_calls` get a deterministic
# empty list, not a missing field).
TOOL_MODE_HEADER = "X-FreelOAder-Tool-Mode"
TOOL_MODE_VALUE = "chat-only-strip"

# Memory inheritance check: scripts/setup-host.sh symlinks these three files
# to /dev/null on dedicated FreelOAder hosts. If they're not nullified at
# startup we WARN once — operator drift is otherwise silent and shows up
# only as a higher-than-expected token bill.
_MEMORY_FILES: tuple[Path, ...] = (
    Path.home() / ".claude" / "CLAUDE.md",
    Path.home() / ".codex" / "AGENTS.md",
    Path.home() / ".gemini" / "GEMINI.md",
)
# Tests / dev that don't want the warning can set this to "1".
_SKIP_HOST_CHECKS_ENV = "FREELOADER_SKIP_HOST_CHECKS"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: str | list[dict] | None = None


class StreamOptions(BaseModel):
    model_config = ConfigDict(extra="allow")
    include_usage: bool = False


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    stream_options: StreamOptions | None = None
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None


def create_app(
    router: Router | None = None,
    store: object | None = None,
) -> FastAPI:
    if not os.environ.get(_SKIP_HOST_CHECKS_ENV):
        _warn_if_memory_inheritance_active(_MEMORY_FILES)
    app = FastAPI(title="FreelOAder", version=__version__)
    r = router or Router(**load_router_config())
    s = store or default_store()

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        # OpenAI-shaped models discovery. One entry per registered
        # adapter; freeloader/auto only when 2+ adapters exist (no
        # choice = no auto). req.model is currently echoed in
        # responses but does not steer routing — see STATUS.md
        # 3.6 out-of-scope.
        created = int(time.time())
        provider_names = list(r._adapters.keys())
        ids: list[str] = []
        if len(provider_names) >= 2:
            ids.append("freeloader/auto")
        ids.extend(f"freeloader/{name}" for name in provider_names)
        return {
            "object": "list",
            "data": [
                {
                    "id": mid,
                    "object": "model",
                    "created": created,
                    "owned_by": "freeloader",
                }
                for mid in ids
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(
        req: ChatCompletionRequest,
        response: Response,
        x_freeloader_conversation_id: str | None = Header(
            default=None, alias="X-FreelOAder-Conversation-Id"
        ),
    ) -> Any:
        openai_messages = [m.model_dump() for m in req.messages]
        incoming = [openai_to_canonical(m) for m in openai_messages]

        conv_id = x_freeloader_conversation_id or hash_of_prefix(openai_messages)
        tools_dropped = _warn_if_tools_dropped(req, conv_id)

        stored = s.load(conv_id)

        try:
            diff = diff_against_stored(stored=stored, incoming=incoming)
        except HistoryMismatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        effective_stored = stored[:-1] if diff.action == "regenerate" else stored

        if req.stream:
            include_usage = bool(
                req.stream_options and req.stream_options.include_usage
            )
            headers = {"X-FreelOAder-Conversation-Id": conv_id}
            if tools_dropped:
                headers[TOOL_MODE_HEADER] = TOOL_MODE_VALUE
            return StreamingResponse(
                _stream_chat_completion(
                    router=r,
                    store=s,
                    model=req.model,
                    conversation_id=conv_id,
                    effective_stored=effective_stored,
                    diff_action=diff.action,
                    new_messages=diff.new_messages,
                    include_usage=include_usage,
                ),
                media_type="text/event-stream",
                headers=headers,
            )

        text_parts: list[str] = []
        finish_reason = "stop"
        usage: UsageDelta | None = None
        async for delta in r.dispatch(
            conversation_id=conv_id,
            stored_messages=effective_stored,
            new_messages=diff.new_messages,
        ):
            if isinstance(delta, TextDelta):
                text_parts.append(delta.text)
            elif isinstance(delta, FinishDelta):
                finish_reason = delta.reason
            elif isinstance(delta, UsageDelta):
                usage = delta

        assistant_text = "".join(text_parts)
        _persist(
            s, conv_id, diff.action, effective_stored, diff.new_messages, assistant_text
        )

        response.headers["X-FreelOAder-Conversation-Id"] = conv_id
        if tools_dropped:
            response.headers[TOOL_MODE_HEADER] = TOOL_MODE_VALUE
        return _build_chat_completion(
            req.model, assistant_text, finish_reason, usage, tools_dropped=tools_dropped
        )

    return app


async def _stream_chat_completion(
    *,
    router: Router,
    store: Any,
    model: str,
    conversation_id: str,
    effective_stored: list[CanonicalMessage],
    diff_action: str,
    new_messages: list[CanonicalMessage],
    include_usage: bool,
) -> AsyncIterator[bytes]:
    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    yield sse_encode(role_chunk(chunk_id, created, model))

    text_parts: list[str] = []
    finish_reason = "stop"
    usage: UsageDelta | None = None
    finish_emitted = False
    async for delta in router.dispatch(
        conversation_id=conversation_id,
        stored_messages=effective_stored,
        new_messages=new_messages,
    ):
        if isinstance(delta, TextDelta):
            text_parts.append(delta.text)
            yield sse_encode(text_chunk(chunk_id, created, model, delta.text))
        elif isinstance(delta, FinishDelta):
            finish_reason = delta.reason
            yield sse_encode(finish_chunk(chunk_id, created, model, finish_reason))
            finish_emitted = True
        elif isinstance(delta, UsageDelta):
            usage = delta

    if not finish_emitted:
        # Adapter exited without a FinishDelta (unexpected but possible):
        # emit a synthetic stop so clients don't hang waiting for terminality.
        yield sse_encode(finish_chunk(chunk_id, created, model, finish_reason))

    assistant_text = "".join(text_parts)
    _persist(
        store,
        conversation_id,
        diff_action,
        effective_stored,
        new_messages,
        assistant_text,
    )

    if include_usage:
        yield sse_encode(usage_chunk(chunk_id, created, model, _usage_dict(usage)))

    yield DONE_SENTINEL


def _persist(
    store: Any,
    conversation_id: str,
    diff_action: str,
    effective_stored: list[CanonicalMessage],
    new_messages: list[CanonicalMessage],
    assistant_text: str,
) -> None:
    assistant_msg = CanonicalMessage(role="assistant", content=assistant_text)
    if diff_action == "regenerate":
        store.rewrite(
            conversation_id,
            effective_stored + new_messages + [assistant_msg],
        )
    else:
        store.append(conversation_id, new_messages + [assistant_msg])


def _usage_dict(usage: UsageDelta | None) -> dict[str, int]:
    prompt_tokens = sum(
        m.input_tokens for m in (usage.models.values() if usage else [])
    )
    completion_tokens = sum(
        m.output_tokens for m in (usage.models.values() if usage else [])
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _warn_if_memory_inheritance_active(paths: tuple[Path, ...]) -> None:
    """One-shot startup check: log WARNING for each memory file that isn't
    a symlink to /dev/null. scripts/setup-host.sh nullifies these on
    dedicated hosts; if it wasn't run, the spawned CLI subprocesses still
    inherit the user's AGENTS.md content and burn input tokens on it.
    Silent operator drift; warning at startup makes it visible.
    """
    for p in paths:
        # Path doesn't exist at all: CLI loads nothing. Fine.
        if not p.is_symlink() and not p.exists():
            continue
        if p.is_symlink():
            try:
                target = os.readlink(p)
            except OSError:
                target = "<unreadable>"
            if target == "/dev/null":
                continue
            kind = f"symlink to {target!r}"
        else:
            kind = "regular file"
        logger.warning(
            "agent memory inheritance ACTIVE for %s (%s) — spawned CLI "
            "subprocesses will load it. Run scripts/setup-host.sh --yes on "
            "dedicated hosts to nullify, or set %s=1 to silence this warning.",
            p,
            kind,
            _SKIP_HOST_CHECKS_ENV,
            extra={"memory_path": str(p), "memory_kind": kind},
        )


def _warn_if_tools_dropped(req: ChatCompletionRequest, conversation_id: str) -> bool:
    # Chat-only mode (phase 5 decision: chat_only_strip). Returns True iff
    # tool fields were present and stripped — caller uses it to set the
    # X-FreelOAder-Tool-Mode header and the tool_calls=[] message field.
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
                "mode": TOOL_MODE_VALUE,
                "conversation_id": conversation_id,
            },
        )
        return True
    return False


def _build_chat_completion(
    model: str,
    text: str,
    finish_reason: str,
    usage: UsageDelta | None,
    *,
    tools_dropped: bool = False,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tools_dropped:
        message["tool_calls"] = []
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": _usage_dict(usage),
    }
