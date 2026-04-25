# Step 4.2a: gemini/codex don't emit native rate_limit records, so
# the router INFERS pressure from a per-provider rolling token
# window and writes a synthetic quota_signal of the same canonical
# shape that 4.1's claude path uses. PLAN principle #5 (quota is an
# event stream) + PLAN decision #6 (events.jsonl carries
# quota_signal alongside turn_done).
#
# These tests scope strictly to 4.2a: token-window inference. 429
# detection lives in step 4.2b (deferred — needs adapter stderr
# work).
from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import AsyncIterator

import pytest

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    SessionIdDelta,
    TextDelta,
    UsageDelta,
)
from freeloader.canonical.messages import CanonicalMessage
from freeloader.core.quota import build_quota_signal_from_usage
from freeloader.router import Router


class _CapturingEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


class _RaisingEvents:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def write(self, event: dict) -> None:
        self.calls.append(event)
        raise OSError("disk full")


class _ScriptedAdapter:
    def __init__(self, deltas: list[Delta]) -> None:
        self._deltas = deltas

    async def send(
        self,
        prompt: str,
        *,
        conversation_id: str,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        for d in self._deltas:
            yield d


async def _drain(router: Router, conv_id: str = "conv-test") -> None:
    async for _ in router.dispatch(
        conversation_id=conv_id,
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hi")],
    ):
        pass


def _quota_signals(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("kind") == "quota_signal"]


def _turn_dones(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("kind") == "turn_done"]


def _inferred(events: list[dict]) -> list[dict]:
    return [
        e
        for e in _quota_signals(events)
        if e.get("rate_limit_type") == "inferred_window"
    ]


# ---------------- builder shape (pure unit tests) ----------------


def test_build_quota_signal_from_usage_under_threshold_is_allowed():
    out = build_quota_signal_from_usage(
        provider="codex",
        conversation_id="conv-x",
        window_seconds=300,
        window_tokens=42_000,
        tokens_threshold=1_000_000,
        ts="2026-04-25T12:00:00+00:00",
    )
    assert out == {
        "ts": "2026-04-25T12:00:00+00:00",
        "kind": "quota_signal",
        "provider": "codex",
        "conversation_id": "conv-x",
        "rate_limit_type": "inferred_window",
        "status": "allowed",
        "resets_at": None,
        "overage_status": None,
        "raw": {
            "window_seconds": 300,
            "window_tokens": 42_000,
            "tokens_threshold": 1_000_000,
        },
    }


def test_build_quota_signal_from_usage_at_threshold_is_exceeded():
    # Boundary: exactly at threshold counts as exceeded — pressure
    # has reached the configured cap, the next turn should switch.
    out = build_quota_signal_from_usage(
        provider="gemini",
        conversation_id="conv-y",
        window_seconds=600,
        window_tokens=500_000,
        tokens_threshold=500_000,
        ts="2026-04-25T12:00:00+00:00",
    )
    assert out["status"] == "exceeded"


def test_build_quota_signal_from_usage_over_threshold_is_exceeded():
    out = build_quota_signal_from_usage(
        provider="gemini",
        conversation_id="conv-z",
        window_seconds=300,
        window_tokens=2_000_000,
        tokens_threshold=1_000_000,
        ts="2026-04-25T12:00:00+00:00",
    )
    assert out["status"] == "exceeded"
    # raw carries the actual numbers so 4.3's Strategy can compute
    # decay forecasts without re-deriving from the event log.
    assert out["raw"]["window_tokens"] == 2_000_000


# ---------------- router emission: codex (single sub-model) ----------------


async def test_codex_usage_delta_emits_one_inferred_quota_signal_under_threshold():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-1"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={
                    "codex": ModelUsage(input_tokens=10_000, output_tokens=2_000),
                },
            ),
        ]
    )
    router = Router(
        codex=adapter,
        events=events,
        inference_tokens_threshold=1_000_000,
        inference_window_seconds=300,
    )
    await _drain(router, conv_id="conv-codex-1")

    qs = _inferred(events.events)
    assert len(qs) == 1
    assert qs[0]["provider"] == "codex"
    assert qs[0]["conversation_id"] == "conv-codex-1"
    assert qs[0]["status"] == "allowed"
    assert qs[0]["raw"]["window_tokens"] == 12_000
    assert qs[0]["raw"]["tokens_threshold"] == 1_000_000
    assert qs[0]["raw"]["window_seconds"] == 300
    assert len(_turn_dones(events.events)) == 1


async def test_codex_usage_delta_over_threshold_emits_exceeded():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-2"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={
                    "codex": ModelUsage(input_tokens=600_000, output_tokens=500_000),
                },
            ),
        ]
    )
    router = Router(
        codex=adapter,
        events=events,
        inference_tokens_threshold=1_000_000,
        inference_window_seconds=300,
    )
    await _drain(router, conv_id="conv-codex-2")

    qs = _inferred(events.events)
    assert len(qs) == 1
    assert qs[0]["status"] == "exceeded"
    assert qs[0]["raw"]["window_tokens"] == 1_100_000


# ---------------- router emission: gemini (compound sub-models) ----------------


async def test_gemini_compound_usage_sums_all_submodels_into_one_window_total():
    # Gemini reports per-sub-model stats; the inferred signal sums
    # them into ONE provider-level window total. Per-sub-model
    # accounting lives one level deeper than what 4.3 needs.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="gemini-uuid-1"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={
                    "gemini-2.5-pro": ModelUsage(
                        input_tokens=100_000, output_tokens=20_000
                    ),
                    "gemini-2.5-flash": ModelUsage(
                        input_tokens=50_000, output_tokens=5_000
                    ),
                },
            ),
        ]
    )
    router = Router(
        gemini=adapter,
        events=events,
        inference_tokens_threshold=1_000_000,
        inference_window_seconds=300,
    )
    await _drain(router, conv_id="conv-gemini-1")

    qs = _inferred(events.events)
    assert len(qs) == 1
    assert qs[0]["provider"] == "gemini"
    # 100k + 20k + 50k + 5k = 175k. cached_input_tokens (default 0
    # here) is excluded by design.
    assert qs[0]["raw"]["window_tokens"] == 175_000


async def test_cached_input_tokens_excluded_from_window():
    # Cached tokens typically don't count toward quota and would
    # distort the pressure signal upward. The builder/router must
    # exclude them.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-3"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={
                    "codex": ModelUsage(
                        input_tokens=10_000,
                        output_tokens=2_000,
                        cached_input_tokens=99_999_999,  # huge — must be ignored
                    ),
                },
            ),
        ]
    )
    router = Router(
        codex=adapter,
        events=events,
        inference_tokens_threshold=1_000_000,
        inference_window_seconds=300,
    )
    await _drain(router, conv_id="conv-codex-cached")

    qs = _inferred(events.events)
    assert len(qs) == 1
    assert qs[0]["raw"]["window_tokens"] == 12_000


# ---------------- router emission: claude is excluded ----------------


async def test_claude_usage_delta_emits_no_inferred_quota_signal():
    # Claude has native RateLimitDelta (4.1). Inference would
    # double-count, so the router must skip claude when emitting
    # inferred signals — even though UsageDelta arrives identically
    # shaped.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="claude-sid"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={
                    "claude-opus-4-6": ModelUsage(
                        input_tokens=999_999_999, output_tokens=999_999_999
                    ),
                },
            ),
        ]
    )
    router = Router(
        claude=adapter,
        events=events,
        inference_tokens_threshold=1_000_000,
        inference_window_seconds=300,
    )
    await _drain(router, conv_id="conv-claude")

    # No quota_signal at all — claude path emits one only when a
    # RateLimitDelta arrives, and this scripted stream has none.
    assert _quota_signals(events.events) == []
    assert len(_turn_dones(events.events)) == 1


# ---------------- window mechanics ----------------


async def test_window_evicts_entries_older_than_window_seconds():
    # Simulate two turns: the first lands at t=0, the second at
    # t=window+1. The first turn's tokens must NOT count toward the
    # second turn's window total.
    events = _CapturingEvents()
    fake_clock = {"now": 0.0}

    def now_monotonic() -> float:
        return fake_clock["now"]

    adapter1 = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-A"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"codex": ModelUsage(input_tokens=400_000, output_tokens=0)}
            ),
        ]
    )
    adapter2 = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-B"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"codex": ModelUsage(input_tokens=10_000, output_tokens=0)}
            ),
        ]
    )

    # First turn: t=0, 400k tokens. Window has 400k.
    router = Router(
        codex=adapter1,
        events=events,
        inference_tokens_threshold=1_000_000,
        inference_window_seconds=300,
        now_monotonic=now_monotonic,
    )
    await _drain(router, conv_id="conv-evict-A")

    qs1 = _inferred(events.events)
    assert len(qs1) == 1
    assert qs1[0]["raw"]["window_tokens"] == 400_000

    # Advance the clock past the window. The next turn should see a
    # window total of just its own 10k — the 400k aged out.
    fake_clock["now"] = 301.0  # 1s past window edge
    # Same router instance carries the window state across turns;
    # swap in a new scripted adapter via the registered slot.
    router._adapters["codex"] = adapter2
    await _drain(router, conv_id="conv-evict-A")

    qs_all = _inferred(events.events)
    assert len(qs_all) == 2
    assert qs_all[1]["raw"]["window_tokens"] == 10_000


async def test_window_accumulates_within_window():
    # Two turns inside the window: the second turn's signal should
    # carry the SUM of both turns.
    events = _CapturingEvents()
    fake_clock = {"now": 0.0}

    def now_monotonic() -> float:
        return fake_clock["now"]

    adapter1 = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="g-1"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={
                    "gemini-2.5-pro": ModelUsage(input_tokens=100_000, output_tokens=0)
                }
            ),
        ]
    )
    adapter2 = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="g-2"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={
                    "gemini-2.5-pro": ModelUsage(input_tokens=50_000, output_tokens=0)
                }
            ),
        ]
    )

    router = Router(
        gemini=adapter1,
        events=events,
        inference_tokens_threshold=1_000_000,
        inference_window_seconds=300,
        now_monotonic=now_monotonic,
    )
    await _drain(router, conv_id="conv-acc")

    fake_clock["now"] = 60.0  # well within the 300s window
    router._adapters["gemini"] = adapter2
    await _drain(router, conv_id="conv-acc")

    qs = _inferred(events.events)
    assert len(qs) == 2
    assert qs[0]["raw"]["window_tokens"] == 100_000
    assert qs[1]["raw"]["window_tokens"] == 150_000


# ---------------- ordering, ts shape, write-failure ----------------


async def test_inferred_quota_signal_precedes_turn_done():
    # 4.3's strategy reads records as they arrive; the inferred
    # signal must land before the turn_done so a "skip this provider
    # next turn" decision is observable from the event log alone,
    # without joining against per-turn state.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-X"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(models={"codex": ModelUsage(input_tokens=1, output_tokens=1)}),
        ]
    )
    router = Router(codex=adapter, events=events)
    await _drain(router, conv_id="conv-order")

    kinds = [e["kind"] for e in events.events]
    # Only two events expected; quota_signal must come first.
    assert kinds == ["quota_signal", "turn_done"]


async def test_inferred_quota_signal_ts_is_iso_utc():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-T"),
            FinishDelta(reason="stop"),
            UsageDelta(models={"codex": ModelUsage(input_tokens=1, output_tokens=1)}),
        ]
    )
    router = Router(codex=adapter, events=events)
    await _drain(router, conv_id="conv-ts")

    qs = _inferred(events.events)
    assert len(qs) == 1
    parsed = _dt.datetime.fromisoformat(qs[0]["ts"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == _dt.timedelta(0)


async def test_inferred_quota_signal_write_failure_logged_not_silent(caplog):
    # Same rule as 4.1's _emit_quota_signal: a failed journal write
    # is a forensic gap, not a turn failure. The turn must still
    # complete for the client; the error must surface via stdlib
    # logger.
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-W"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(models={"codex": ModelUsage(input_tokens=1, output_tokens=1)}),
        ]
    )
    raising = _RaisingEvents()
    router = Router(codex=adapter, events=raising)
    with caplog.at_level(logging.ERROR, logger="freeloader.router"):
        await _drain(router, conv_id="conv-write-fail")

    msgs = [r.message for r in caplog.records if "inferred quota_signal" in r.message]
    assert msgs, (
        f"expected inferred quota_signal error log, got "
        f"{[r.message for r in caplog.records]}"
    )


@pytest.fixture(autouse=True)
def _enable_pytest_asyncio():
    # No-op fixture; pytest-asyncio's mode is configured in
    # pyproject.toml. Present here only to keep the test module
    # explicit about its async-mode dependency.
    yield
