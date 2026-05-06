# Step 4.2b end-to-end: when codex/gemini adapter emits a
# RateLimitDelta(rate_limit_type="429"), the router writes a
# quota_signal event of the canonical shape and notifies the
# strategy. This proves the 4.2b pressure path connects to the
# 4.3 routing path via the existing build_quota_signal builder
# (no new sibling needed).
from __future__ import annotations

from collections.abc import AsyncIterator

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
from freeloader.router import Router


class _CapturingEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


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


class _CapturingStrategy:
    """Round-robin-shaped strategy that records every event passed to
    `observe`, so the test can verify the router fed the 429 to it."""

    def __init__(self, order: list[str]) -> None:
        self._order = order
        self._cursor = 0
        self.observed: list[dict] = []

    def pick(self, providers: list[str]) -> str:
        # Naive cycle — deterministic enough for the assertion here;
        # phase 4.3 has its own quota-aware strategy tests.
        choice = self._order[self._cursor % len(self._order)]
        self._cursor += 1
        return choice

    def observe(self, event: dict) -> None:
        self.observed.append(event)


async def _drain(router: Router, conv_id: str = "conv-test") -> None:
    async for _ in router.dispatch(
        conversation_id=conv_id,
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hi")],
    ):
        pass


async def test_codex_429_delta_writes_quota_signal_with_429_type():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="thread-1"),
            RateLimitDelta(
                rate_limit_type="429",
                status="exceeded",
                raw={
                    "stderr_excerpt": "HTTP 429 Too Many Requests",
                    "exit_code": 1,
                    "provider": "codex",
                    "source": "stderr_scan",
                },
            ),
            FinishDelta(reason="error"),
        ]
    )
    router = Router(codex=adapter, events=events)
    await _drain(router)

    quota_signals = [e for e in events.events if e.get("kind") == "quota_signal"]
    assert len(quota_signals) == 1
    qs = quota_signals[0]
    assert qs["rate_limit_type"] == "429"
    assert qs["status"] == "exceeded"
    assert qs["provider"] == "codex"
    assert qs["raw"]["source"] == "stderr_scan"


async def test_gemini_429_delta_writes_quota_signal_with_429_type():
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="s1"),
            RateLimitDelta(
                rate_limit_type="429",
                status="exceeded",
                raw={
                    "stderr_excerpt": "RESOURCE_EXHAUSTED",
                    "exit_code": 1,
                    "provider": "gemini",
                    "source": "stderr_scan",
                },
            ),
            FinishDelta(reason="error"),
        ]
    )
    router = Router(gemini=adapter, events=events)
    await _drain(router)

    quota_signals = [e for e in events.events if e.get("kind") == "quota_signal"]
    assert len(quota_signals) == 1
    assert quota_signals[0]["provider"] == "gemini"
    assert quota_signals[0]["rate_limit_type"] == "429"


async def test_429_delta_is_observed_by_strategy():
    # Strategy.observe must receive the same event the journal got —
    # the strategy's view of pressure must not diverge from the
    # durable record (lesson from step 4.3).
    events = _CapturingEvents()
    strategy = _CapturingStrategy(order=["codex", "gemini"])
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="thread-2"),
            RateLimitDelta(
                rate_limit_type="429",
                status="exceeded",
                raw={
                    "stderr_excerpt": "429",
                    "exit_code": 1,
                    "provider": "codex",
                    "source": "stderr_scan",
                },
            ),
            FinishDelta(reason="error"),
        ]
    )
    # Two adapters so the strategy has a real choice on the next pick.
    other = _ScriptedAdapter([])  # only the codex one is dispatched here
    router = Router(codex=adapter, gemini=other, events=events, strategy=strategy)
    await _drain(router)

    # exactly one observe() call carrying the 429 quota_signal.
    rate_observations = [
        e for e in strategy.observed if e.get("rate_limit_type") == "429"
    ]
    assert len(rate_observations) == 1
    assert rate_observations[0]["status"] == "exceeded"


async def test_429_delta_marks_turn_rate_limited_terminal():
    # rate_limit_exceeded supersedes a clean stop in the router's
    # terminal-decision logic (PLAN decision #4 + step 2.x). The
    # 429 path must reach the same terminal.
    events = _CapturingEvents()
    adapter = _ScriptedAdapter(
        [
            SessionIdDelta(session_id="thread-3"),
            RateLimitDelta(
                rate_limit_type="429",
                status="exceeded",
                raw={
                    "stderr_excerpt": "429",
                    "exit_code": 1,
                    "provider": "codex",
                    "source": "stderr_scan",
                },
            ),
            TextDelta(text="partial response before 429"),
            FinishDelta(reason="stop"),
            UsageDelta(models={"codex": ModelUsage(input_tokens=3, output_tokens=1)}),
        ]
    )
    router = Router(codex=adapter, events=events)
    await _drain(router)

    turn_dones = [e for e in events.events if e.get("kind") == "turn_done"]
    assert len(turn_dones) == 1
    assert turn_dones[0]["state"] == "rate_limited"
