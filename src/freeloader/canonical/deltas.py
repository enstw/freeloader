# Delta union — PLAN principle #1. Seven variants, each carrying exactly
# one kind of information. Adapters yield these; the frontend / router /
# journal pattern-match on `kind` to dispatch.
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


class TextDelta(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class FinishDelta(BaseModel):
    kind: Literal["finish"] = "finish"
    reason: Literal["stop", "length", "content_filter", "tool_calls", "error"]


class SessionIdDelta(BaseModel):
    kind: Literal["session_id"] = "session_id"
    session_id: str


class UsageDelta(BaseModel):
    # Keyed by sub-model even for single-model providers: gemini is a compound
    # provider (stats.models) and would otherwise discard information.
    kind: Literal["usage"] = "usage"
    models: dict[str, ModelUsage]


class RateLimitDelta(BaseModel):
    kind: Literal["rate_limit"] = "rate_limit"
    rate_limit_type: str
    status: str
    resets_at: int | None = None
    overage_status: str | None = None
    # Preserve the vendor payload so the router/journal can see fields the
    # canonical schema hasn't absorbed yet.
    raw: dict


class ErrorDelta(BaseModel):
    kind: Literal["error"] = "error"
    message: str
    source: Literal["parse", "process", "timeout"] = "parse"


class RawDelta(BaseModel):
    # Escape hatch for vendor events the canonical layer hasn't absorbed.
    # Written to the runtime event log for debugging; never reaches the client.
    kind: Literal["raw"] = "raw"
    event_type: str
    payload: dict


Delta = (
    TextDelta
    | FinishDelta
    | SessionIdDelta
    | UsageDelta
    | RateLimitDelta
    | ErrorDelta
    | RawDelta
)
