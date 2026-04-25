# Step 4.1: claude rate_limit_event records (observed as
# RateLimitDelta) become canonical quota_signal events in the journal.
# PLAN principle #5 (quota is an event stream, not a counter) +
# PLAN decision #6 (events.jsonl carries quota_signal alongside
# turn_done).
#
# These tests exercise the Router with scripted adapters; the live
# claude CLI path is covered by the cross-adapter contract suite and
# the carry-forward smoke harness.
from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import AsyncIterator

import pytest

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    RateLimitDelta,
    SessionIdDelta,
    TextDelta,
    UsageDelta,
)
from freeloader.canonical.messages import CanonicalMessage
from freeloader.core.quota import build_quota_signal
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


# ---------------- builder shape (pure unit test) ----------------


def test_build_quota_signal_carries_all_canonical_fields():
    delta = RateLimitDelta(
        rate_limit_type="five_hour",
        status="exceeded",
        resets_at=1775408400,
        overage_status="warning",
        raw={"status": "exceeded", "rateLimitType": "five_hour"},
    )
    out = build_quota_signal(
        provider="claude",
        conversation_id="conv-x",
        delta=delta,
        ts="2026-04-25T12:00:00+00:00",
    )
    assert out == {
        "ts": "2026-04-25T12:00:00+00:00",
        "kind": "quota_signal",
        "provider": "claude",
        "conversation_id": "conv-x",
        "rate_limit_type": "five_hour",
        "status": "exceeded",
        "resets_at": 1775408400,
        "overage_status": "warning",
        "raw": {"status": "exceeded", "rateLimitType": "five_hour"},
    }


def test_build_quota_signal_handles_optional_fields_as_none():
    # claude omits resetsAt / overageStatus on some statuses; the
    # canonical event must still serialize.
    delta = RateLimitDelta(
        rate_limit_type="seven_day",
        status="allowed",
        raw={"status": "allowed"},
    )
    out = build_quota_signal(
        provider="claude",
        conversation_id="conv-y",
        delta=delta,
        ts="2026-04-25T12:00:00+00:00",
    )
    assert out["resets_at"] is None
    assert out["overage_status"] is None


# ---------------- router emission ----------------


async def test_allowed_rate_limit_delta_still_emits_quota_signal():
    # Stream-as-truth: even an allowed observation carries info
    # (resets_at, the fact we polled). Phase 4.3 needs the "still
    # allowed" signals for trend computation.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s1"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="allowed",
                resets_at=1775408400,
                raw={"status": "allowed"},
            ),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=3, output_tokens=1)}
            ),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)

    qs = _quota_signals(events.events)
    assert len(qs) == 1
    assert qs[0]["status"] == "allowed"
    assert qs[0]["provider"] == "claude"
    assert qs[0]["conversation_id"] == "conv-test"
    assert qs[0]["rate_limit_type"] == "five_hour"
    assert qs[0]["resets_at"] == 1775408400


async def test_exceeded_rate_limit_delta_emits_quota_signal_and_marks_turn():
    # Both effects co-exist: the journal carries the quota_signal AND
    # the turn terminal flips to rate_limited (existing behaviour from
    # 2.x).
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s2"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="exceeded",
                resets_at=1775408400,
                overage_status="active",
                raw={"status": "exceeded"},
            ),
            TextDelta(text="still streamed"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=3, output_tokens=1)}
            ),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)

    qs = _quota_signals(events.events)
    td = _turn_dones(events.events)
    assert len(qs) == 1
    assert qs[0]["status"] == "exceeded"
    assert qs[0]["overage_status"] == "active"
    assert len(td) == 1
    assert td[0]["state"] == "rate_limited"


async def test_multiple_rate_limit_deltas_in_one_turn_emit_multiple_signals():
    # claude can emit several rate_limit_event records in a single
    # turn (e.g., five_hour and seven_day status updates). One
    # quota_signal per delta — never deduped, never collapsed.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s3"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="allowed",
                resets_at=1775408400,
                raw={"status": "allowed", "rateLimitType": "five_hour"},
            ),
            RateLimitDelta(
                rate_limit_type="seven_day",
                status="allowed",
                resets_at=1775901200,
                raw={"status": "allowed", "rateLimitType": "seven_day"},
            ),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=3, output_tokens=1)}
            ),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)

    qs = _quota_signals(events.events)
    assert len(qs) == 2
    types = {e["rate_limit_type"] for e in qs}
    assert types == {"five_hour", "seven_day"}


async def test_no_rate_limit_delta_means_no_quota_signal():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s4"),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=3, output_tokens=1)}
            ),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)

    assert _quota_signals(events.events) == []
    assert len(_turn_dones(events.events)) == 1


async def test_quota_signal_precedes_turn_done_in_event_order():
    # A phase-4.3 strategy reading the log reacts as records arrive;
    # the quota_signal must land before the turn_done so a "skip this
    # provider next turn" decision is observable from the event log
    # alone, without joining against per-turn state.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s5"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="exceeded",
                raw={"status": "exceeded"},
            ),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=3, output_tokens=1)}
            ),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)

    kinds = [e["kind"] for e in events.events]
    assert kinds == ["quota_signal", "turn_done"]


async def test_quota_signal_ts_is_iso_utc():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s6"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="allowed",
                raw={"status": "allowed"},
            ),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=1, output_tokens=1)}
            ),
        ]
    )
    router = Router(claude=adapter, events=events)
    await _drain(router)

    qs = _quota_signals(events.events)
    assert len(qs) == 1
    # Round-trip parse: must be a tz-aware UTC ISO-8601 string.
    parsed = _dt.datetime.fromisoformat(qs[0]["ts"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == _dt.timedelta(0)


async def test_quota_signal_carries_dispatched_provider_name():
    # Provider field comes from the router's selection, not the
    # delta — phase 4.3's strategy keys pressure by provider, so
    # round-robin dispatching codex must produce a quota_signal with
    # provider="codex" even though the delta shape is identical
    # across vendors.
    events = _CapturingEvents()
    codex_adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="codex-thread-1"),
            RateLimitDelta(
                rate_limit_type="hypothetical",
                status="allowed",
                raw={"status": "allowed"},
            ),
            FinishDelta(reason="stop"),
            UsageDelta(models={"codex": ModelUsage(input_tokens=1, output_tokens=1)}),
        ]
    )
    # No claude → codex is the only provider in the pool.
    router = Router(codex=codex_adapter, events=events)
    await _drain(router, conv_id="conv-codex-only")

    qs = _quota_signals(events.events)
    assert len(qs) == 1
    assert qs[0]["provider"] == "codex"
    assert qs[0]["conversation_id"] == "conv-codex-only"


async def test_quota_signal_write_failure_logged_not_silent(caplog):
    # _emit_quota_signal must not break the turn on a journal write
    # failure — same rule as _record_terminal: surface via stdlib
    # logger so operators see the gap.
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s7"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="exceeded",
                raw={"status": "exceeded"},
            ),
            TextDelta(text="ok"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=1, output_tokens=1)}
            ),
        ]
    )
    raising = _RaisingEvents()
    router = Router(claude=adapter, events=raising)
    with caplog.at_level(logging.ERROR, logger="freeloader.router"):
        await _drain(router)
    msgs = [r.message for r in caplog.records if "quota_signal" in r.message]
    assert msgs, (
        f"expected quota_signal error log, got {[r.message for r in caplog.records]}"
    )
    # Both writes were attempted (quota_signal + turn_done) — both
    # raised, both logged. Turn still completes for the client.
    assert len(raising.calls) >= 1


@pytest.fixture(autouse=True)
def _enable_pytest_asyncio():
    # No-op fixture; pytest-asyncio's mode is configured in
    # pyproject.toml. Present here only to keep the test module
    # explicit about its async-mode dependency.
    yield
