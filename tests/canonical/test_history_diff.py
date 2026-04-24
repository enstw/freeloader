# Three MVP cases per PLAN principle #4 + edge cases. Covers the gate_1
# "history_diff unit test exists" check.
from __future__ import annotations

import pytest

from freeloader.canonical.history_diff import (
    HistoryMismatchError,
    diff_against_stored,
)
from freeloader.canonical.messages import CanonicalMessage


def _u(text: str) -> CanonicalMessage:
    return CanonicalMessage(role="user", content=text)


def _a(text: str) -> CanonicalMessage:
    return CanonicalMessage(role="assistant", content=text)


def _s(text: str) -> CanonicalMessage:
    return CanonicalMessage(role="system", content=text)


# ── case (a) append-only ─────────────────────────────────────────────


def test_first_turn_returns_full_incoming_as_append():
    result = diff_against_stored(stored=[], incoming=[_u("hi")])
    assert result.action == "append"
    assert result.new_messages == [_u("hi")]


def test_append_new_user_turn_after_assistant():
    stored = [_u("q1"), _a("a1")]
    incoming = [_u("q1"), _a("a1"), _u("q2")]
    result = diff_against_stored(stored=stored, incoming=incoming)
    assert result.action == "append"
    assert result.new_messages == [_u("q2")]


def test_append_preserves_system_prefix():
    stored = [_s("be helpful"), _u("q1"), _a("a1")]
    incoming = [_s("be helpful"), _u("q1"), _a("a1"), _u("q2")]
    result = diff_against_stored(stored=stored, incoming=incoming)
    assert result.action == "append"
    assert result.new_messages == [_u("q2")]


def test_incoming_equal_to_stored_returns_empty_append():
    stored = [_u("q1"), _a("a1")]
    result = diff_against_stored(stored=stored, incoming=stored)
    assert result.action == "append"
    assert result.new_messages == []


# ── case (b) regenerate-last ─────────────────────────────────────────


def test_regenerate_drops_last_assistant_empty_new_turn():
    # Client clicks "regenerate" without editing — sends stored[:-1].
    stored = [_u("q1"), _a("a1")]
    incoming = [_u("q1")]
    result = diff_against_stored(stored=stored, incoming=incoming)
    assert result.action == "regenerate"
    assert result.new_messages == []


def test_regenerate_with_new_user_turn_appended():
    # Client drops the prior assistant, sends a new user turn in its place.
    stored = [_u("q1"), _a("a1")]
    incoming = [_u("q1"), _u("q1_v2")]
    result = diff_against_stored(stored=stored, incoming=incoming)
    assert result.action == "regenerate"
    assert result.new_messages == [_u("q1_v2")]


# ── case (c) mismatch ────────────────────────────────────────────────


def test_mismatch_when_mid_history_edit():
    # Client edits an earlier user turn. stored = [u1, a1, u2, a2]; incoming
    # = [u1_v2, a1, u2, a2] — u1 was changed, rest is the same suffix.
    stored = [_u("q1"), _a("a1"), _u("q2"), _a("a2")]
    incoming = [_u("q1_v2"), _a("a1"), _u("q2"), _a("a2")]
    with pytest.raises(HistoryMismatchError):
        diff_against_stored(stored=stored, incoming=incoming)


def test_mismatch_when_incoming_shorter_and_last_is_user():
    # Stored ends in user, incoming drops it — not a regenerate (which
    # requires the dropped turn to be assistant).
    stored = [_u("q1"), _a("a1"), _u("q2")]
    incoming = [_u("q1"), _a("a1")]
    with pytest.raises(HistoryMismatchError):
        diff_against_stored(stored=stored, incoming=incoming)


def test_mismatch_when_reordered_system_message():
    stored = [_s("S1"), _u("q1"), _a("a1")]
    incoming = [_u("q1"), _s("S1"), _a("a1"), _u("q2")]
    with pytest.raises(HistoryMismatchError):
        diff_against_stored(stored=stored, incoming=incoming)
