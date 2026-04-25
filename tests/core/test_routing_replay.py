# Step 4.5: deterministic routing replay over fixture JOURNAL.
#
# ROADMAP § Phase 4: "given a fixture JOURNAL, the router makes
# deterministic routing decisions — no wall-clock dependency."
#
# What this test guards:
#   - Replay determinism: identical event history → identical
#     decision sequence. If a future strategy refinement quietly
#     introduces clock reads or randomness, the idempotence test
#     fails.
#   - Canonical-shape stability: fixtures use the same JSON shape
#     that `build_quota_signal` / `build_quota_signal_from_usage`
#     produce; if the shape ever drifts, the fixture stops folding
#     and this test fails. That's a desirable forcing function.
#   - Robustness to non-quota events: the journal contains turn_done
#     records too. `observe()` must ignore them silently so a
#     consumer can pipe the whole stream without filtering.
#
# Out of scope (deliberately):
#   - Router/adapter wiring — covered by tests/core/test_routing_
#     quota_aware.py § "through the Router" tests in 4.3.
#   - Binding replay (rebuild conversation→provider from turn_done)
#     — separate concern; not required by gate_4.
from __future__ import annotations

import json
from pathlib import Path

from freeloader.canonical.deltas import RateLimitDelta
from freeloader.core.quota import build_quota_signal, build_quota_signal_from_usage
from freeloader.core.routing import QuotaAwareStrategy

FIXTURES = Path(__file__).parent / "fixtures" / "routing_replay"


def _load(name: str) -> list[dict]:
    """Load a JOURNAL fixture as a list of records. The strategy
    consumes dicts; the file lives on disk to prove the consumer
    works against bytes, not Python identity."""
    text = (FIXTURES / name).read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _replay(events: list[dict]) -> QuotaAwareStrategy:
    s = QuotaAwareStrategy()
    for ev in events:
        s.observe(ev)
    return s


# ---------------- realistic_session checkpoints ----------------


def test_realistic_session_initial_state_picks_claude():
    # Empty replay: cursor=0, no pressure. Same as RoundRobinStrategy
    # at start. This is the "all backends healthy" baseline.
    s = _replay([])
    assert s.pick(["claude", "codex", "gemini"]) == "claude"


def test_realistic_session_after_claude_allowed_picks_claude():
    # Records [0..1]: turn_done + claude five_hour=allowed. No
    # pressure anywhere → cursor=0 wins, claude.
    events = _load("realistic_session.jsonl")
    s = _replay(events[:2])
    assert s.is_pressured("claude") is False
    assert s.pick(["claude", "codex", "gemini"]) == "claude"


def test_realistic_session_after_claude_exceeded_skips_to_codex():
    # Through record [7]: claude five_hour=exceeded; codex still
    # allowed (last codex event was [3]); gemini still allowed
    # (last gemini event was [5]). Strategy must skip claude.
    events = _load("realistic_session.jsonl")
    s = _replay(events[:8])
    assert s.is_pressured("claude") is True
    assert s.is_pressured("codex") is False
    assert s.is_pressured("gemini") is False
    assert s.pick(["claude", "codex", "gemini"]) == "codex"


def test_realistic_session_after_codex_also_exceeded_skips_to_gemini():
    # Through record [9]: claude exceeded + codex exceeded; gemini
    # still allowed. Only gemini is selectable.
    events = _load("realistic_session.jsonl")
    s = _replay(events[:10])
    assert s.is_pressured("claude") is True
    assert s.is_pressured("codex") is True
    assert s.is_pressured("gemini") is False
    assert s.pick(["claude", "codex", "gemini"]) == "gemini"


def test_realistic_session_after_full_replay_claude_recovered():
    # Full replay: claude five_hour returns to allowed at [13];
    # codex still exceeded (no later codex allowed event); gemini
    # allowed. Cursor=0 starts at claude → claude (recovered) wins.
    events = _load("realistic_session.jsonl")
    s = _replay(events)
    assert s.is_pressured("claude") is False  # recovered
    assert s.is_pressured("codex") is True  # never recovered in fixture
    assert s.is_pressured("gemini") is False
    assert s.pick(["claude", "codex", "gemini"]) == "claude"


# ---------------- all_pressured checkpoints ----------------


def test_all_pressured_falls_back_to_first_at_cursor():
    # Every provider exceeded. Strategy returns the provider at
    # the cursor (0 → claude). No starvation: a request still gets
    # served.
    events = _load("all_pressured.jsonl")
    s = _replay(events)
    assert s.is_pressured("claude") is True
    assert s.is_pressured("codex") is True
    assert s.is_pressured("gemini") is True
    order = ["claude", "codex", "gemini"]
    assert s.pick(order) == "claude"
    # Subsequent picks rotate the cursor among the pressured pool.
    assert s.pick(order) == "codex"
    assert s.pick(order) == "gemini"
    assert s.pick(order) == "claude"


# ---------------- determinism (the core 4.5 claim) ----------------


def test_replay_is_idempotent():
    # The whole point of 4.5: replaying the same fixture into two
    # fresh strategies must yield byte-identical decisions. If a
    # later refactor introduces a hidden clock read or random
    # source, this test fails first.
    events = _load("realistic_session.jsonl")
    order = ["claude", "codex", "gemini"]

    s1 = _replay(events)
    s2 = _replay(events)
    # Make 10 picks from each (cursor rotation makes the sequence
    # non-trivial). Both must produce the same sequence.
    picks1 = [s1.pick(order) for _ in range(10)]
    picks2 = [s2.pick(order) for _ in range(10)]
    assert picks1 == picks2


def test_replay_is_idempotent_for_all_pressured_too():
    # All-pressured path uses the fallback branch — separate code
    # path from the skip-pressured branch, deserves its own
    # idempotence check.
    events = _load("all_pressured.jsonl")
    order = ["claude", "codex", "gemini"]
    picks1 = [_replay(events).pick(order) for _ in range(5)]
    picks2 = [_replay(events).pick(order) for _ in range(5)]
    assert picks1 == picks2
    # All five picks return claude because each is a fresh
    # strategy with cursor=0 — proves no hidden global state leaks
    # between strategy instances.
    assert picks1 == ["claude"] * 5


# ---------------- non-quota events are folded silently ----------------


def test_stripping_turn_done_records_yields_equivalent_strategy_state():
    # Realistic JOURNAL has turn_done interleaved with quota_signal.
    # observe() must ignore the former; the strategy state after
    # replaying everything must equal the state after replaying
    # only the quota_signal records.
    full = _load("realistic_session.jsonl")
    quota_only = [ev for ev in full if ev.get("kind") == "quota_signal"]
    assert len(quota_only) < len(full)  # sanity: there are turn_dones

    s_full = _replay(full)
    s_quota = _replay(quota_only)

    order = ["claude", "codex", "gemini"]
    # Same pressure picture.
    for provider in order:
        assert s_full.is_pressured(provider) == s_quota.is_pressured(provider)
    # Same downstream pick decisions.
    picks_full = [s_full.pick(order) for _ in range(6)]
    picks_quota = [s_quota.pick(order) for _ in range(6)]
    assert picks_full == picks_quota


# ---------------- canonical-builder shape catches drift ----------------


def test_events_built_via_canonical_builders_replay_correctly():
    # The fixtures match what build_quota_signal* produces today.
    # If the canonical shape ever drifts (a field renamed, a key
    # dropped), the strategy would silently miss those events.
    # This test pins the contract: events synthesized via the
    # production builders must be valid replay input.
    native = build_quota_signal(
        provider="claude",
        conversation_id="conv-x",
        delta=RateLimitDelta(
            rate_limit_type="five_hour",
            status="exceeded",
            resets_at=1775408400,
            overage_status="active",
            raw={"status": "exceeded"},
        ),
        ts="2026-04-25T12:00:00+00:00",
    )
    inferred = build_quota_signal_from_usage(
        provider="codex",
        conversation_id="conv-y",
        window_seconds=300,
        window_tokens=1500000,
        tokens_threshold=1000000,
        ts="2026-04-25T12:00:01+00:00",
    )
    s = _replay([native, inferred])
    assert s.is_pressured("claude") is True
    assert s.is_pressured("codex") is True


def test_canonical_builder_output_round_trips_through_jsonl():
    # The same builder output that flows into the journal at
    # runtime must survive json.dumps → json.loads (no non-JSON
    # types snuck in). Together with the test above this proves
    # end-to-end: builder → bytes → fresh process → strategy state.
    event = build_quota_signal(
        provider="claude",
        conversation_id="conv-x",
        delta=RateLimitDelta(
            rate_limit_type="five_hour",
            status="exceeded",
            resets_at=1775408400,
            overage_status="active",
            raw={"status": "exceeded"},
        ),
        ts="2026-04-25T12:00:00+00:00",
    )
    serialized = json.dumps(event)
    parsed = json.loads(serialized)
    s = _replay([parsed])
    assert s.is_pressured("claude") is True
