# History diff — PLAN principle #4.
#
# /v1/chat/completions is stateless: the client resends the full `messages`
# array every turn. Figuring out "what's new this turn" vs "what was
# already sent" lives here, not in the ~50-line frontend handler (#6) or
# the text-in/text-out adapter (#3).
#
# MVP scope (PLAN § principle #4): three outcomes.
#   (a) append-only: incoming == stored + [new messages]
#   (b) regenerate:  incoming == stored[:-1] [+ new messages], where
#       stored[-1] is an assistant turn being replaced.
#   (c) mismatch:    stored is not a prefix of incoming in either
#       shape → raise HistoryMismatchError.
# Mid-history edits (case 4) are out of scope for MVP — the frontend
# surfaces HistoryMismatchError as 400.
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from freeloader.canonical.messages import CanonicalMessage


class HistoryMismatchError(ValueError):
    """Stored conversation is not a prefix of `incoming` in either the
    append-only or regenerate-last shape."""


class DiffResult(BaseModel):
    action: Literal["append", "regenerate"]
    new_messages: list[CanonicalMessage]


def diff_against_stored(
    stored: list[CanonicalMessage],
    incoming: list[CanonicalMessage],
) -> DiffResult:
    # First turn of a conversation: nothing stored, so the full incoming
    # list is the new turn.
    if not stored:
        return DiffResult(action="append", new_messages=list(incoming))

    # (a) append-only: stored is a prefix of incoming.
    if len(incoming) >= len(stored) and list(incoming[: len(stored)]) == list(stored):
        return DiffResult(
            action="append",
            new_messages=list(incoming[len(stored) :]),
        )

    # (b) regenerate: only valid when the last stored turn is an assistant
    # message being replaced.
    if stored[-1].role == "assistant":
        prefix = stored[:-1]
        if len(incoming) >= len(prefix) and list(incoming[: len(prefix)]) == list(
            prefix
        ):
            return DiffResult(
                action="regenerate",
                new_messages=list(incoming[len(prefix) :]),
            )

    # (c) mismatch — mid-history edit, reordered system message, truncation,
    # or plain divergence. MVP raises; callers surface as 400.
    raise HistoryMismatchError(
        "stored conversation is not a prefix of incoming messages "
        "(neither append-only nor regenerate-last shape)"
    )
