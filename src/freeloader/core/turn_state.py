# Turn state machine — PLAN principle #2.
#
# A turn is the unit of work between "client asked something" and "we
# stopped working on it." Each turn moves through one of these eight
# states. Reaching any terminal state is the same event as writing the
# turn's runtime journal entry — there is no separate "logged" state,
# because a window where the turn exists in conversation history but
# not the journal is a consistency gap (process crash → the two
# disagree on the outcome).
#
# This module is pure: just an enum, a transition table, and a tiny
# Turn driver. The driver lives in Router.dispatch and decides which
# terminal to enter based on the Delta stream it observes.
from __future__ import annotations

from enum import StrEnum


class TurnState(StrEnum):
    QUEUED = "queued"
    SPAWNING = "spawning"
    STREAMING = "streaming"
    # Terminal states.
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    BACKEND_ERROR = "backend_error"
    RATE_LIMITED = "rate_limited"
    TIMED_OUT = "timed_out"


TERMINAL_STATES: frozenset[TurnState] = frozenset(
    {
        TurnState.COMPLETE,
        TurnState.CANCELLED,
        TurnState.BACKEND_ERROR,
        TurnState.RATE_LIMITED,
        TurnState.TIMED_OUT,
    }
)


# from -> set of legal targets. Terminals have no outgoing edges.
_TRANSITIONS: dict[TurnState, frozenset[TurnState]] = {
    TurnState.QUEUED: frozenset(
        {
            TurnState.SPAWNING,
            TurnState.CANCELLED,
            TurnState.TIMED_OUT,
        }
    ),
    TurnState.SPAWNING: frozenset(
        {
            TurnState.STREAMING,
            TurnState.BACKEND_ERROR,
            TurnState.CANCELLED,
            TurnState.TIMED_OUT,
        }
    ),
    TurnState.STREAMING: frozenset(
        {
            TurnState.COMPLETE,
            TurnState.CANCELLED,
            TurnState.BACKEND_ERROR,
            TurnState.RATE_LIMITED,
            TurnState.TIMED_OUT,
        }
    ),
    # Terminals — no outgoing edges.
    TurnState.COMPLETE: frozenset(),
    TurnState.CANCELLED: frozenset(),
    TurnState.BACKEND_ERROR: frozenset(),
    TurnState.RATE_LIMITED: frozenset(),
    TurnState.TIMED_OUT: frozenset(),
}


class IllegalTransition(ValueError):
    def __init__(self, fr: TurnState, to: TurnState) -> None:
        super().__init__(f"illegal transition {fr.value} -> {to.value}")
        self.fr = fr
        self.to = to


def transition(current: TurnState, target: TurnState) -> TurnState:
    """Return target if current → target is legal; raise IllegalTransition."""
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition(current, target)
    return target


def is_terminal(state: TurnState) -> bool:
    return state in TERMINAL_STATES


def legal_targets(state: TurnState) -> frozenset[TurnState]:
    """Set of states reachable in one step from `state` (frozen empty for terminals)."""
    return _TRANSITIONS.get(state, frozenset())


class Turn:
    """Tiny stateful driver over the transition table.

    Lives for the duration of one turn (one Router.dispatch call).
    Not thread-safe; one Turn per dispatch coroutine.
    """

    def __init__(self) -> None:
        self.state: TurnState = TurnState.QUEUED
        self.history: list[TurnState] = [TurnState.QUEUED]

    def goto(self, target: TurnState) -> None:
        self.state = transition(self.state, target)
        self.history.append(self.state)

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self.state)
