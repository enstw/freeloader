# Step 3.4: Router cycles through registered providers per *new*
# conversation. Resumed turns dispatch to the bound provider, not the
# next one in the cycle. PLAN principle #5 (selection by policy);
# round-robin is the simplest non-trivial policy.
from __future__ import annotations

from collections.abc import AsyncIterator

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    SessionIdDelta,
    UsageDelta,
)
from freeloader.canonical.messages import CanonicalMessage
from freeloader.router import Router


class _ScriptedAdapter:
    """One-turn fake. Records every call so the test can assert which
    provider got dispatched."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict] = []

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
        # Each backend reports its own session-id shape; make it
        # vendor-prefixed so the binding test can assert which adapter
        # actually ran.
        yield SessionIdDelta(session_id=f"{self.name}-sid-{len(self.calls)}")
        yield FinishDelta(reason="stop")
        yield UsageDelta(
            models={self.name: ModelUsage(input_tokens=1, output_tokens=1)}
        )


class _CapturingEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


async def _drain(router: Router, conv_id: str) -> None:
    async for _ in router.dispatch(
        conversation_id=conv_id,
        stored_messages=[],
        new_messages=[CanonicalMessage(role="user", content="hi")],
    ):
        pass


async def test_three_fresh_conversations_cycle_through_providers():
    claude = _ScriptedAdapter("claude")
    codex = _ScriptedAdapter("codex")
    gemini = _ScriptedAdapter("gemini")
    events = _CapturingEvents()
    router = Router(claude=claude, codex=codex, gemini=gemini, events=events)

    await _drain(router, "conv-1")
    await _drain(router, "conv-2")
    await _drain(router, "conv-3")

    # Each of the three providers got exactly one turn, in registration
    # order.
    assert len(claude.calls) == 1
    assert len(codex.calls) == 1
    assert len(gemini.calls) == 1
    # The journal records which provider handled each conversation.
    assert [e["provider"] for e in events.events] == ["claude", "codex", "gemini"]


async def test_cycle_wraps_after_exhausting_pool():
    claude = _ScriptedAdapter("claude")
    codex = _ScriptedAdapter("codex")
    events = _CapturingEvents()
    router = Router(claude=claude, codex=codex, events=events)

    for i in range(5):
        await _drain(router, f"conv-{i}")

    # 5 conversations, 2 providers → claude gets 3 turns, codex gets 2.
    assert len(claude.calls) == 3
    assert len(codex.calls) == 2
    assert [e["provider"] for e in events.events] == [
        "claude",
        "codex",
        "claude",
        "codex",
        "claude",
    ]


async def test_resumed_conversation_stays_on_same_provider():
    claude = _ScriptedAdapter("claude")
    codex = _ScriptedAdapter("codex")
    events = _CapturingEvents()
    router = Router(claude=claude, codex=codex, events=events)

    # Turn 1 of conv-A: claude (round-robin index = 0).
    await _drain(router, "conv-A")
    assert events.events[-1]["provider"] == "claude"
    assert router._bindings["conv-A"] == ("claude", "claude-sid-1")

    # Turn 1 of conv-B: codex (index = 1).
    await _drain(router, "conv-B")
    assert events.events[-1]["provider"] == "codex"

    # Turn 2 of conv-A: must stay on claude (binding wins over round-
    # robin), and dispatch with resume_session_id pointing at the
    # backend sid we observed on turn 1.
    await _drain(router, "conv-A")
    assert events.events[-1]["provider"] == "claude"
    assert claude.calls[-1]["resume_session_id"] == "claude-sid-1"
    # Round-robin index advanced to 2 (=0 mod 2) on conv-B; conv-A's
    # second turn did NOT advance it, since it wasn't a new conversation.
    assert router._next_provider_idx == 2


async def test_default_router_with_no_kwargs_uses_single_claude():
    # Phase-1 backward compat: many existing tests instantiate
    # Router() with no arguments and expect ClaudeAdapter as the
    # default. Round-robin still works (with a single-element pool
    # it's a no-op cycle).
    router = Router()
    assert list(router._adapters.keys()) == ["claude"]
    # No explicit ClaudeAdapter class assertion — tests in
    # test_claude_sandbox.py and elsewhere already cover that the
    # default is a real ClaudeAdapter; here we only check the routing
    # plumbing.


async def test_provider_field_in_journal_matches_dispatched_adapter():
    codex = _ScriptedAdapter("codex")
    gemini = _ScriptedAdapter("gemini")
    events = _CapturingEvents()
    # Note: no claude. Pool = [codex, gemini]. First turn must go to codex.
    router = Router(codex=codex, gemini=gemini, events=events)

    await _drain(router, "conv-only-codex-and-gemini")

    assert len(codex.calls) == 1
    assert len(gemini.calls) == 0
    assert events.events[-1]["provider"] == "codex"
