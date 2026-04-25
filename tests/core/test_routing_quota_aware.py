# Step 4.3: QuotaAwareStrategy turns the quota_signal event stream
# (4.1 + 4.2a) into routing decisions. PLAN principle #5 — quota is
# an event stream, not a counter — and this strategy is what makes
# the stream load-bearing for routing.
#
# These tests cover the strategy in isolation (pure: feed events
# via observe(), assert on pick()) and through the Router with
# scripted adapters (so we know the wiring actually fires).
from __future__ import annotations

from collections.abc import AsyncIterator

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    RateLimitDelta,
    SessionIdDelta,
    UsageDelta,
)
from freeloader.canonical.messages import CanonicalMessage
from freeloader.core.routing import QuotaAwareStrategy, RoundRobinStrategy
from freeloader.router import Router


class _CapturingEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


class _ScriptedAdapter:
    def __init__(self, name: str, deltas: list[Delta] | None = None) -> None:
        self.name = name
        self.calls: list[dict] = []
        self._deltas = deltas

    async def send(
        self,
        prompt: str,
        *,
        conversation_id: str,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "session_id": session_id,
                "resume_session_id": resume_session_id,
            }
        )
        if self._deltas is not None:
            for d in self._deltas:
                yield d
            return
        # Default benign turn: session id + finish + usage.
        yield SessionIdDelta(session_id=f"{self.name}-sid-{len(self.calls)}")
        yield FinishDelta(reason="stop")
        yield UsageDelta(
            models={self.name: ModelUsage(input_tokens=1, output_tokens=1)}
        )


async def _drain(router: Router, conv_id: str) -> None:
    async for _ in router.dispatch(
        conversation_id=conv_id,
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hi")],
    ):
        pass


def _qs(
    *,
    provider: str,
    rate_limit_type: str,
    status: str,
) -> dict:
    """Build a minimal quota_signal-shaped event for unit-testing
    observe(). Mirrors the canonical shape of build_quota_signal /
    build_quota_signal_from_usage so the strategy exercises the
    same code path it'd see from Router."""
    return {
        "ts": "2026-04-25T12:00:00+00:00",
        "kind": "quota_signal",
        "provider": provider,
        "conversation_id": "conv-test",
        "rate_limit_type": rate_limit_type,
        "status": status,
        "resets_at": None,
        "overage_status": None,
        "raw": {},
    }


# ---------------- strategy in isolation (pure) ----------------


def test_pick_with_no_pressure_starts_at_first_provider():
    s = QuotaAwareStrategy()
    assert s.pick(["claude", "codex", "gemini"]) == "claude"


def test_pick_advances_cursor_like_round_robin_when_no_pressure():
    # No pressure → behaves indistinguishably from RoundRobinStrategy.
    # Verifies the no-starvation property: the strategy doesn't pin
    # all traffic to the first provider when nothing's wrong.
    s = QuotaAwareStrategy()
    order = ["claude", "codex", "gemini"]
    assert [s.pick(order) for _ in range(5)] == [
        "claude",
        "codex",
        "gemini",
        "claude",
        "codex",
    ]


def test_pick_skips_pressured_provider():
    s = QuotaAwareStrategy()
    s.observe(_qs(provider="claude", rate_limit_type="five_hour", status="exceeded"))
    # First scan starts at cursor=0 (claude); claude is pressured →
    # skip to codex.
    assert s.pick(["claude", "codex", "gemini"]) == "codex"


def test_pick_skips_codex_when_inferred_window_exceeded():
    # The inferred path uses rate_limit_type="inferred_window";
    # strategy treats it identically to claude's native types.
    s = QuotaAwareStrategy()
    s.observe(
        _qs(provider="codex", rate_limit_type="inferred_window", status="exceeded")
    )
    # cursor=0 → claude (not pressured) → claude returned. Bump
    # cursor manually by another pick to verify codex gets skipped
    # when it'd be next.
    assert s.pick(["claude", "codex", "gemini"]) == "claude"
    # cursor is now 1 (codex). codex is pressured → skip to gemini.
    assert s.pick(["claude", "codex", "gemini"]) == "gemini"


def test_pick_falls_back_to_first_when_all_pressured():
    s = QuotaAwareStrategy()
    s.observe(_qs(provider="claude", rate_limit_type="five_hour", status="exceeded"))
    s.observe(
        _qs(provider="codex", rate_limit_type="inferred_window", status="exceeded")
    )
    s.observe(
        _qs(provider="gemini", rate_limit_type="inferred_window", status="exceeded")
    )
    # All pressured → deterministic fallback to provider at cursor.
    # cursor starts at 0 → claude.
    order = ["claude", "codex", "gemini"]
    assert s.pick(order) == "claude"
    # cursor advanced; next universal-pressure pick rotates.
    assert s.pick(order) == "codex"
    assert s.pick(order) == "gemini"
    assert s.pick(order) == "claude"


def test_recovery_clears_pressure_and_provider_becomes_selectable_again():
    s = QuotaAwareStrategy()
    s.observe(_qs(provider="claude", rate_limit_type="five_hour", status="exceeded"))
    assert s.is_pressured("claude") is True
    # Same rate_limit_type goes back to allowed → pressure clears.
    s.observe(_qs(provider="claude", rate_limit_type="five_hour", status="allowed"))
    assert s.is_pressured("claude") is False
    assert s.pick(["claude", "codex"]) == "claude"


def test_any_rate_limit_type_exceeded_means_pressured():
    # claude has multiple windows (five_hour, seven_day); if either
    # is exceeded, the provider is pressured. Verifies the any-rule.
    s = QuotaAwareStrategy()
    s.observe(_qs(provider="claude", rate_limit_type="five_hour", status="exceeded"))
    s.observe(_qs(provider="claude", rate_limit_type="seven_day", status="allowed"))
    assert s.is_pressured("claude") is True
    # Clearing the exceeded one (five_hour back to allowed) clears
    # pressure.
    s.observe(_qs(provider="claude", rate_limit_type="five_hour", status="allowed"))
    assert s.is_pressured("claude") is False


def test_observe_ignores_non_quota_signal_events():
    # Router pipes its whole event stream through observe(); the
    # strategy must not choke on turn_done / future event kinds.
    s = QuotaAwareStrategy()
    s.observe({"kind": "turn_done", "provider": "claude"})
    s.observe({"kind": "phase_done"})
    s.observe({})
    assert s.is_pressured("claude") is False


def test_observe_ignores_malformed_quota_signal():
    # Defensive: a record missing provider/type/status shouldn't
    # raise. Forensic events shouldn't crash routing.
    s = QuotaAwareStrategy()
    s.observe({"kind": "quota_signal"})  # missing all
    s.observe({"kind": "quota_signal", "provider": "claude"})  # missing type/status
    assert s.is_pressured("claude") is False


def test_pick_empty_order_raises():
    s = QuotaAwareStrategy()
    try:
        s.pick([])
    except ValueError:
        return
    raise AssertionError("expected ValueError on empty pool")


# ---------------- through the Router (integration) ----------------


async def test_router_with_quota_aware_strategy_skips_pressured_provider():
    # Scripted claude turn emits a RateLimitDelta(exceeded). After
    # that, a *fresh* conversation should dispatch to codex, not
    # claude.
    claude = _ScriptedAdapter(
        "claude",
        deltas=[
            SessionIdDelta(session_id="claude-sid-1"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="exceeded",
                raw={"status": "exceeded"},
            ),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=1, output_tokens=1)}
            ),
        ],
    )
    codex = _ScriptedAdapter("codex")
    events = _CapturingEvents()
    router = Router(
        claude=claude,
        codex=codex,
        events=events,
        strategy=QuotaAwareStrategy(),
    )

    await _drain(router, "conv-A")  # cursor starts at 0 → claude
    # Sanity: claude got the first turn.
    assert len(claude.calls) == 1
    # claude reported exceeded; the strategy observed it.
    # Next fresh conv must skip claude.
    await _drain(router, "conv-B")
    assert len(codex.calls) == 1
    # claude did NOT get the second turn.
    assert len(claude.calls) == 1


async def test_router_with_quota_aware_strategy_skips_codex_after_inferred_exceeded():
    # codex's UsageDelta over the threshold triggers an inferred
    # quota_signal with status="exceeded" — strategy must skip it
    # next time around.
    codex = _ScriptedAdapter(
        "codex",
        deltas=[
            SessionIdDelta(session_id="codex-thread-1"),
            FinishDelta(reason="stop"),
            # 1M+ tokens in one turn → over the 1000 threshold the
            # test sets via the constructor.
            UsageDelta(
                models={"codex": ModelUsage(input_tokens=2000, output_tokens=0)}
            ),
        ],
    )
    gemini = _ScriptedAdapter("gemini")
    events = _CapturingEvents()
    router = Router(
        codex=codex,
        gemini=gemini,
        events=events,
        strategy=QuotaAwareStrategy(),
        inference_tokens_threshold=1000,  # tiny so the test turn breaches
    )

    await _drain(router, "conv-A")  # cursor=0 → codex
    assert len(codex.calls) == 1
    # codex's inferred_window status is now "exceeded" — next fresh
    # conv must go to gemini.
    await _drain(router, "conv-B")
    assert len(gemini.calls) == 1
    assert len(codex.calls) == 1


async def test_bound_conversation_stays_on_pressured_provider():
    # Binding wins over strategy: a resumed conversation must
    # dispatch to its bound provider even if the strategy would
    # consider that provider pressured. Otherwise mid-conversation
    # state would silently jump backends, which is principle #3's
    # whole point.
    claude = _ScriptedAdapter(
        "claude",
        deltas=[
            SessionIdDelta(session_id="claude-sid-1"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="exceeded",
                raw={"status": "exceeded"},
            ),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=1, output_tokens=1)}
            ),
        ],
    )
    codex = _ScriptedAdapter("codex")
    events = _CapturingEvents()
    router = Router(
        claude=claude,
        codex=codex,
        events=events,
        strategy=QuotaAwareStrategy(),
    )

    # Turn 1 of conv-A → claude (cursor=0). Reports exceeded.
    await _drain(router, "conv-A")
    assert len(claude.calls) == 1
    # Turn 2 of conv-A: bound to claude. Strategy thinks claude is
    # pressured, but binding wins. claude gets it again.
    await _drain(router, "conv-A")
    assert len(claude.calls) == 2
    assert len(codex.calls) == 0
    # Resumed turn dispatched with the observed sid.
    assert claude.calls[1]["resume_session_id"] == "claude-sid-1"


async def test_router_default_round_robin_strategy_keeps_working():
    # RoundRobinStrategy has no `observe` method. Router must not
    # raise AttributeError when piping quota_signal events through
    # _notify_strategy. Asserts the duck-typing guard fires.
    claude = _ScriptedAdapter(
        "claude",
        deltas=[
            SessionIdDelta(session_id="claude-sid-1"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="exceeded",
                raw={"status": "exceeded"},
            ),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=1, output_tokens=1)}
            ),
        ],
    )
    events = _CapturingEvents()
    router = Router(
        claude=claude,
        events=events,
        strategy=RoundRobinStrategy(),
    )

    # Should not raise.
    await _drain(router, "conv-A")
    # The quota_signal still got written; round-robin just doesn't
    # consume it.
    qs = [e for e in events.events if e.get("kind") == "quota_signal"]
    assert len(qs) == 1


async def test_strategy_observe_called_for_inferred_signals_too():
    # Step 4.2a's inferred quota_signal must also be fed to the
    # strategy. End-to-end check: codex breach via inference (no
    # native RateLimitDelta) → next conv skips codex.
    strategy = QuotaAwareStrategy()
    codex = _ScriptedAdapter(
        "codex",
        deltas=[
            SessionIdDelta(session_id="codex-thread-1"),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"codex": ModelUsage(input_tokens=5000, output_tokens=0)}
            ),
        ],
    )
    events = _CapturingEvents()
    router = Router(
        codex=codex,
        gemini=_ScriptedAdapter("gemini"),
        events=events,
        strategy=strategy,
        inference_tokens_threshold=1000,
    )
    await _drain(router, "conv-A")
    # Strategy should now consider codex pressured.
    assert strategy.is_pressured("codex") is True


async def test_strategy_state_independent_of_journal_write_failure():
    # If the journal write fails, the strategy must NOT see the
    # event — its view should never diverge from what's durable.
    # _notify_strategy is called only after a successful write.
    class _RaisingEvents:
        def __init__(self) -> None:
            self.calls = 0

        def write(self, event: dict) -> None:
            self.calls += 1
            raise OSError("disk full")

    strategy = QuotaAwareStrategy()
    claude = _ScriptedAdapter(
        "claude",
        deltas=[
            SessionIdDelta(session_id="claude-sid-1"),
            RateLimitDelta(
                rate_limit_type="five_hour",
                status="exceeded",
                raw={"status": "exceeded"},
            ),
            FinishDelta(reason="stop"),
            UsageDelta(
                models={"claude-opus-4-6": ModelUsage(input_tokens=1, output_tokens=1)}
            ),
        ],
    )
    router = Router(
        claude=claude,
        events=_RaisingEvents(),
        strategy=strategy,
    )

    await _drain(router, "conv-A")
    # Journal couldn't record it → strategy didn't observe it →
    # claude is NOT marked pressured. Reading from JOURNAL on
    # restart would give the same view.
    assert strategy.is_pressured("claude") is False
