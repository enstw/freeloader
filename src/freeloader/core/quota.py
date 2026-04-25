# Canonical quota_signal event — PLAN principle #5.
#
# Quota is an event stream, not a counter. Each vendor delta that
# carries quota information becomes one quota_signal record in
# `<data_dir>/events.jsonl` (PLAN decision #6); phase 4.3's
# quota-aware Strategy derives pressure from this stream.
#
# Step 4.1 covers the only vendor with explicit quota telemetry —
# claude's rate_limit_event records, observed as RateLimitDelta. Step
# 4.2 adds a sibling builder for inferred signals from gemini/codex
# (cumulative tokens + 429). Both write to the same kind="quota_signal"
# slot so the strategy reads one stream, not three.
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
