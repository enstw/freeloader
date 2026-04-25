# Canonical quota_signal event — PLAN principle #5.
#
# Quota is an event stream, not a counter. Each vendor delta that
# carries quota information becomes one quota_signal record in
# `<data_dir>/events.jsonl` (PLAN decision #6); phase 4.3's
# quota-aware Strategy derives pressure from this stream.
#
# Step 4.1 covers the only vendor with explicit quota telemetry —
# claude's rate_limit_event records, observed as RateLimitDelta.
# Step 4.2a adds a sibling builder for INFERRED signals from
# gemini/codex (cumulative tokens per rolling window). Both builders
# write to the same kind="quota_signal" slot so the strategy reads
# one stream, not three. Step 4.2b will add a third sibling for 429
# detection once adapters can surface it.
from __future__ import annotations

from typing import Any

from freeloader.canonical.deltas import RateLimitDelta


def build_quota_signal(
    *,
    provider: str,
    conversation_id: str,
    delta: RateLimitDelta,
    ts: str,
) -> dict[str, Any]:
    """Build the canonical quota_signal event dict for a vendor
    rate-limit observation. Pure function — caller supplies `ts`
    (ISO-8601 UTC) so the journal write site controls clock-reading
    and tests can pin a fixed timestamp."""
    return {
        "ts": ts,
        "kind": "quota_signal",
        "provider": provider,
        "conversation_id": conversation_id,
        "rate_limit_type": delta.rate_limit_type,
        "status": delta.status,
        "resets_at": delta.resets_at,
        "overage_status": delta.overage_status,
        "raw": delta.raw,
    }


def build_quota_signal_from_usage(
    *,
    provider: str,
    conversation_id: str,
    window_seconds: int,
    window_tokens: int,
    tokens_threshold: int,
    ts: str,
) -> dict[str, Any]:
    """Build a canonical quota_signal event from a rolling-window
    token total (the inference path for vendors without explicit
    rate-limit telemetry: gemini, codex).

    Same shape as `build_quota_signal` so phase 4.3's Strategy reads
    one stream. `rate_limit_type="inferred_window"` distinguishes
    inferred records from native ones (claude's "five_hour" /
    "seven_day"). `raw` carries the window summary so the Strategy
    can see what triggered the call without re-deriving it."""
    status = "exceeded" if window_tokens >= tokens_threshold else "allowed"
    return {
        "ts": ts,
        "kind": "quota_signal",
        "provider": provider,
        "conversation_id": conversation_id,
        "rate_limit_type": "inferred_window",
        "status": status,
        # No backend-provided reset timestamp for inferred signals.
        # The window is derived; pressure decays as old entries age
        # out, not at a discrete reset event. 4.3's Strategy can
        # compute its own decay forecast from the raw window summary.
        "resets_at": None,
        "overage_status": None,
        "raw": {
            "window_seconds": window_seconds,
            "window_tokens": window_tokens,
            "tokens_threshold": tokens_threshold,
        },
    }
