# Step 3.5: provider switch mid-conversation. `Router.bind(conv,
# new_provider)` pins the binding without a session id; the next
# dispatch replays the canonical history into the new backend's
# first turn (PLAN principle #3); subsequent turns resume normally
# on the new backend.
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from freeloader.canonical.deltas import (
    Delta,
    FinishDelta,
    ModelUsage,
    SessionIdDelta,
    UsageDelta,
)
from freeloader.canonical.messages import CanonicalMessage
from freeloader.router import Router


class _RecordingAdapter:
    """Captures every send() call so the test can assert which prompt
    arrived (history-replay vs single-turn) and whether resume was
    requested."""

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
                "prompt": prompt,
                "conversation_id": conversation_id,
                "session_id": session_id,
                "resume_session_id": resume_session_id,
            }
        )
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


async def _drain(
    router: Router,
    conv_id: str,
    *,
    stored: list[CanonicalMessage] | None = None,
    new: list[CanonicalMessage] | None = None,
) -> None:
    async for _ in router.dispatch(
        conversation_id=conv_id,
        stored_messages=stored or [],
        new_messages=new or [CanonicalMessage(role="user", content="hi")],
    ):
        pass


def test_bind_to_unknown_provider_raises():
    router = Router(claude=_RecordingAdapter("claude"))
    with pytest.raises(ValueError, match="unknown provider"):
        router.bind("conv-x", "gemini")


def test_bind_pins_binding_without_session_id():
    claude = _RecordingAdapter("claude")
    codex = _RecordingAdapter("codex")
    router = Router(claude=claude, codex=codex)
    router.bind("conv-1", "codex")
    assert router._bindings["conv-1"] == ("codex", None)


def test_bind_does_not_advance_round_robin_index():
    """bind() is not a 'new conversation' event. The next *new*
    conversation should still get whichever provider the round-robin
    cycle would have given it had bind() never been called."""
    claude = _RecordingAdapter("claude")
    codex = _RecordingAdapter("codex")
    router = Router(claude=claude, codex=codex)
    before = router._next_provider_idx
    router.bind("conv-1", "codex")
    assert router._next_provider_idx == before


async def test_post_bind_dispatch_replays_full_history_to_new_provider():
    claude = _RecordingAdapter("claude")
    codex = _RecordingAdapter("codex")
    events = _CapturingEvents()
    router = Router(claude=claude, codex=codex, events=events)

    # Build a conversation on claude with two prior turns.
    stored = [
        CanonicalMessage(role="user", content="turn 1 user"),
        CanonicalMessage(role="assistant", content="turn 1 reply"),
        CanonicalMessage(role="user", content="turn 2 user"),
        CanonicalMessage(role="assistant", content="turn 2 reply"),
    ]

    # Switch to codex.
    router.bind("conv-X", "codex")

    new_msg = [CanonicalMessage(role="user", content="turn 3 on the new backend")]
    await _drain(router, "conv-X", stored=stored, new=new_msg)

    # Claude got nothing — provider was switched before any dispatch.
    assert claude.calls == []
    # Codex got exactly one call. It must contain the full canonical
    # history (replay), not just the new turn.
    assert len(codex.calls) == 1
    sent = codex.calls[0]["prompt"]
    assert "turn 1 user" in sent
    assert "turn 1 reply" in sent
    assert "turn 2 user" in sent
    assert "turn 2 reply" in sent
    assert "turn 3 on the new backend" in sent
    # No resume — first turn on the new backend.
    assert codex.calls[0]["resume_session_id"] is None
    # Journal records codex as the provider for this turn.
    assert events.events[-1]["provider"] == "codex"
    # Binding now contains the new backend's session id.
    assert router._bindings["conv-X"] == ("codex", "codex-sid-1")


async def test_subsequent_turns_after_bind_resume_via_new_backend_sid():
    """After the post-bind first turn captures a backend sid, the next
    turn must resume on the *new* provider via that sid — same logic
    as any normal resume."""
    claude = _RecordingAdapter("claude")
    codex = _RecordingAdapter("codex")
    router = Router(claude=claude, codex=codex)

    router.bind("conv-Y", "codex")
    # Post-bind turn 1.
    await _drain(router, "conv-Y", new=[CanonicalMessage(role="user", content="t1")])
    assert codex.calls[-1]["resume_session_id"] is None

    # Post-bind turn 2 — must resume.
    await _drain(router, "conv-Y", new=[CanonicalMessage(role="user", content="t2")])
    assert len(codex.calls) == 2
    assert codex.calls[-1]["resume_session_id"] == "codex-sid-1"
    # Prompt contains only the new turn, not the full history.
    assert codex.calls[-1]["prompt"] == "[USER]\nt2\n[/USER]"
    # Claude still untouched.
    assert claude.calls == []


async def test_bind_supersedes_existing_binding():
    """A conversation already bound to claude can be rebound to codex.
    Old claude session id is dropped (no resume on next turn)."""
    claude = _RecordingAdapter("claude")
    codex = _RecordingAdapter("codex")
    router = Router(claude=claude, codex=codex)

    # Conv-Z lands on claude via round-robin.
    await _drain(router, "conv-Z", new=[CanonicalMessage(role="user", content="t1")])
    assert router._bindings["conv-Z"] == ("claude", "claude-sid-1")

    # Switch to codex.
    router.bind("conv-Z", "codex")
    assert router._bindings["conv-Z"] == ("codex", None)

    # Next dispatch goes to codex with full history (here just t1 +
    # synthetic stored, but we'll pass stored explicitly to make the
    # replay obvious).
    stored = [
        CanonicalMessage(role="user", content="t1"),
        CanonicalMessage(role="assistant", content="reply on claude"),
    ]
    await _drain(
        router,
        "conv-Z",
        stored=stored,
        new=[CanonicalMessage(role="user", content="t2 on codex")],
    )
    assert len(codex.calls) == 1
    sent = codex.calls[0]["prompt"]
    assert "reply on claude" in sent
    assert "t2 on codex" in sent
    assert codex.calls[0]["resume_session_id"] is None


async def test_bind_to_currently_bound_provider_still_forces_replay():
    """Pinning a conversation to its current provider is unusual but
    valid — it forces a replay (e.g., after a backend session was
    poisoned by an out-of-band reset). The binding goes to (same,
    None), next turn replays full history, no resume."""
    claude = _RecordingAdapter("claude")
    router = Router(claude=claude)

    await _drain(router, "conv-A", new=[CanonicalMessage(role="user", content="t1")])
    assert router._bindings["conv-A"] == ("claude", "claude-sid-1")

    router.bind("conv-A", "claude")
    assert router._bindings["conv-A"] == ("claude", None)

    stored = [
        CanonicalMessage(role="user", content="t1"),
        CanonicalMessage(role="assistant", content="reply"),
    ]
    await _drain(
        router,
        "conv-A",
        stored=stored,
        new=[CanonicalMessage(role="user", content="t2")],
    )
    # Two claude calls total: t1 (single message), then full-history
    # replay including t1 + reply + t2.
    assert len(claude.calls) == 2
    replay = claude.calls[1]["prompt"]
    assert "t1" in replay
    assert "reply" in replay
    assert "t2" in replay
    assert claude.calls[1]["resume_session_id"] is None
    # New session id captured on completion.
    assert router._bindings["conv-A"] == ("claude", "claude-sid-2")
