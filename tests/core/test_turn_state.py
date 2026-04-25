# Turn state machine — every legal edge + at least one illegal per state.
from __future__ import annotations

import pytest

from freeloader.core.turn_state import (
    TERMINAL_STATES,
    IllegalTransition,
    Turn,
    TurnState,
    is_terminal,
    legal_targets,
    transition,
)


def test_eight_states_named():
    # If a state is added, the test must be revisited intentionally —
    # the transition table downstream depends on every state being
    # accounted for.
    assert {s.value for s in TurnState} == {
        "queued",
        "spawning",
        "streaming",
        "complete",
        "cancelled",
        "backend_error",
        "rate_limited",
        "timed_out",
    }


def test_terminal_set_matches_principle_2():
    assert {
        TurnState.COMPLETE,
        TurnState.CANCELLED,
        TurnState.BACKEND_ERROR,
        TurnState.RATE_LIMITED,
        TurnState.TIMED_OUT,
    } == TERMINAL_STATES


def test_is_terminal_predicate():
    for s in TERMINAL_STATES:
        assert is_terminal(s) is True
    for s in (TurnState.QUEUED, TurnState.SPAWNING, TurnState.STREAMING):
        assert is_terminal(s) is False


# ---- legal transitions ---------------------------------------------------


@pytest.mark.parametrize(
    ("fr", "to"),
    [
        (TurnState.QUEUED, TurnState.SPAWNING),
        (TurnState.QUEUED, TurnState.CANCELLED),
        (TurnState.QUEUED, TurnState.TIMED_OUT),
        (TurnState.SPAWNING, TurnState.STREAMING),
        (TurnState.SPAWNING, TurnState.BACKEND_ERROR),
        (TurnState.SPAWNING, TurnState.CANCELLED),
        (TurnState.SPAWNING, TurnState.TIMED_OUT),
        (TurnState.STREAMING, TurnState.COMPLETE),
        (TurnState.STREAMING, TurnState.CANCELLED),
        (TurnState.STREAMING, TurnState.BACKEND_ERROR),
        (TurnState.STREAMING, TurnState.RATE_LIMITED),
        (TurnState.STREAMING, TurnState.TIMED_OUT),
    ],
)
def test_legal_transitions(fr: TurnState, to: TurnState):
    assert transition(fr, to) == to
    assert to in legal_targets(fr)


# ---- illegal transitions: at least one per state ------------------------


@pytest.mark.parametrize(
    ("fr", "to"),
    [
        # Cannot skip from queued straight to streaming/complete.
        (TurnState.QUEUED, TurnState.STREAMING),
        (TurnState.QUEUED, TurnState.COMPLETE),
        # Spawning cannot complete or rate-limit without streaming first.
        (TurnState.SPAWNING, TurnState.COMPLETE),
        (TurnState.SPAWNING, TurnState.RATE_LIMITED),
        # Streaming cannot return to a non-terminal earlier state.
        (TurnState.STREAMING, TurnState.SPAWNING),
        (TurnState.STREAMING, TurnState.QUEUED),
        # Terminals have no outgoing edges.
        (TurnState.COMPLETE, TurnState.CANCELLED),
        (TurnState.CANCELLED, TurnState.STREAMING),
        (TurnState.BACKEND_ERROR, TurnState.COMPLETE),
        (TurnState.RATE_LIMITED, TurnState.STREAMING),
        (TurnState.TIMED_OUT, TurnState.COMPLETE),
    ],
)
def test_illegal_transitions_raise(fr: TurnState, to: TurnState):
    with pytest.raises(IllegalTransition) as exc:
        transition(fr, to)
    assert exc.value.fr is fr
    assert exc.value.to is to


def test_terminals_have_no_outgoing_edges():
    for s in TERMINAL_STATES:
        assert legal_targets(s) == frozenset()


# ---- Turn driver --------------------------------------------------------


def test_turn_starts_in_queued():
    t = Turn()
    assert t.state == TurnState.QUEUED
    assert t.history == [TurnState.QUEUED]
    assert t.is_terminal is False


def test_turn_happy_path_history():
    t = Turn()
    t.goto(TurnState.SPAWNING)
    t.goto(TurnState.STREAMING)
    t.goto(TurnState.COMPLETE)
    assert t.history == [
        TurnState.QUEUED,
        TurnState.SPAWNING,
        TurnState.STREAMING,
        TurnState.COMPLETE,
    ]
    assert t.is_terminal is True


def test_turn_illegal_goto_raises_and_keeps_state():
    t = Turn()
    t.goto(TurnState.SPAWNING)
    with pytest.raises(IllegalTransition):
        t.goto(TurnState.COMPLETE)
    # State unchanged on failed transition.
    assert t.state == TurnState.SPAWNING
    assert t.history == [TurnState.QUEUED, TurnState.SPAWNING]


def test_turn_can_short_circuit_to_cancelled_from_queued():
    t = Turn()
    t.goto(TurnState.CANCELLED)
    assert t.is_terminal is True
    assert t.history == [TurnState.QUEUED, TurnState.CANCELLED]
