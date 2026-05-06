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
# gemini/codex (cumulative tokens per rolling window). Step 4.2b
# adds `match_stderr_quota_pressure` (below) which detects upstream
# 429s in codex/gemini stderr and yields a synthetic
# `RateLimitDelta(rate_limit_type="429", status="exceeded")`; that
# delta then flows through the existing `build_quota_signal` path
# in router.py — no third sibling builder needed (the 4.2a comment
# anticipating one was speculative; reusing the existing builder
# keeps the quota_signal stream single-shape).
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


# Step 4.2b: substring patterns that flag an upstream rate-limit /
# quota-exhaustion error in CLI stderr. Case-insensitive, matched
# per-line. Not exhaustive — vendors invent new strings — but covers
# the HTTP-style errors we expect to surface from codex (OpenAI) and
# gemini (Google AI). Each pattern expands the false-positive
# surface: "rate limit" in a benign log line would emit a spurious
# quota_signal, so we restrict matching to runs where the CLI exited
# non-zero (a successful turn that mentions the words is not pressure).
_QUOTA_PRESSURE_PATTERNS: tuple[str, ...] = (
    "429",
    "too many requests",
    "rate limit",
    "ratelimit",
    "quota exceeded",
    "quota_exceeded",
    "resource_exhausted",
    "resource exhausted",
    "insufficient_quota",
)


def match_stderr_quota_pressure(
    *,
    provider: str,
    stderr_text: str,
    exit_code: int | None,
) -> list[RateLimitDelta]:
    """Step 4.2b: scan a CLI subprocess's stderr for upstream 429-style
    errors. Returns a single `RateLimitDelta(rate_limit_type="429",
    status="exceeded")` when any line matches a known pattern, else
    an empty list.

    Only fires when the CLI exited non-zero — a successful turn that
    happens to log the words "rate limit" in benign output is not
    pressure. We emit at most one delta per stderr buffer (multiple
    matching lines are usually the same 429 reported twice; the
    journal doesn't need duplicates and the strategy treats one as
    enough).
    """
    if exit_code in (0, None):
        return []
    if not stderr_text:
        return []
    for line in stderr_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(p in lower for p in _QUOTA_PRESSURE_PATTERNS):
            return [
                RateLimitDelta(
                    rate_limit_type="429",
                    status="exceeded",
                    raw={
                        "stderr_excerpt": stripped[:500],
                        "exit_code": exit_code,
                        "provider": provider,
                        "source": "stderr_scan",
                    },
                )
            ]
    return []
