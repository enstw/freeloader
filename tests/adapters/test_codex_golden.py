# Principle #8: golden-fixture replay gives early warning when codex's
# JSONL shape drifts. The subprocess path in CodexAdapter.send() is
# exercised live in the opt-in smoke harness (later in phase 3); this
# test locks the JSONL→Delta mapping alone.
#
# Fixture was hand-derived from a live `codex exec --json` capture on
# 2026-04-25 (codex-cli 0.123.0); see PLAN.md § "codex" event-shape.
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from freeloader.adapters.codex import map_event, parse_stream
from freeloader.canonical.deltas import (
    Delta,
    ErrorDelta,
    FinishDelta,
    RawDelta,
    SessionIdDelta,
    TextDelta,
    UsageDelta,
)

FIXTURES = Path(__file__).parent / "fixtures"


async def _lines(raw: Iterable[str]) -> AsyncIterator[str]:
    for line in raw:
        yield line


async def _collect(lines: Iterable[str]) -> list[Delta]:
    return [d async for d in parse_stream(_lines(lines))]


async def test_golden_basic_turn_produces_expected_delta_sequence():
    text = (FIXTURES / "codex_basic.jsonl").read_text()
    deltas = await _collect(text.splitlines())

    # turn.started yields nothing — it sits between thread.started and
    # the first item.completed but carries no canonical information.
    assert [type(d).__name__ for d in deltas] == [
        "SessionIdDelta",
        "TextDelta",
        "FinishDelta",
        "UsageDelta",
    ]

    sid, txt, fin, usage = deltas
    assert isinstance(sid, SessionIdDelta)
    assert sid.session_id == "019dc284-eb77-7b42-9b16-17b99089cd17"
    assert isinstance(txt, TextDelta) and txt.text == "Hello! The answer is 42."
    assert isinstance(fin, FinishDelta) and fin.reason == "stop"
    assert isinstance(usage, UsageDelta)
    # Codex doesn't surface a per-model identifier in turn.completed; we
    # key under the provider tag "codex" until that changes.
    assert set(usage.models) == {"codex"}
    mu = usage.models["codex"]
    assert (mu.input_tokens, mu.output_tokens, mu.cached_input_tokens) == (
        15696,
        32,
        3456,
    )


async def test_thread_started_without_id_yields_nothing():
    # Defensive: malformed thread.started missing thread_id should not
    # crash the mapper. (Live codex always emits the id; this guards
    # against future shape drift the same way the claude adapter does.)
    assert map_event({"type": "thread.started"}) == []


async def test_turn_started_is_a_noop():
    assert map_event({"type": "turn.started"}) == []


async def test_item_completed_agent_message_yields_text_delta():
    [out] = map_event(
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": "hi"},
        }
    )
    assert isinstance(out, TextDelta) and out.text == "hi"


async def test_item_completed_empty_text_yields_nothing():
    assert (
        map_event(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": ""},
            }
        )
        == []
    )


async def test_item_completed_non_agent_message_becomes_raw_delta():
    # Reasoning, tool_call, etc. are not part of MVP scope. They should
    # land in the journal as RawDelta, not silently dropped (so future
    # work can decide whether to canonicalize them) and not surfaced as
    # text to the client.
    event = {
        "type": "item.completed",
        "item": {"id": "item_1", "type": "reasoning", "text": "thinking..."},
    }
    [raw] = map_event(event)
    assert isinstance(raw, RawDelta)
    assert raw.event_type == "item.reasoning"
    assert raw.payload == event


async def test_turn_completed_with_usage_emits_finish_then_usage():
    event = {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_input_tokens": 30,
        },
    }
    out = map_event(event)
    assert len(out) == 2
    finish, usage = out
    assert isinstance(finish, FinishDelta) and finish.reason == "stop"
    assert isinstance(usage, UsageDelta)
    mu = usage.models["codex"]
    assert (mu.input_tokens, mu.output_tokens, mu.cached_input_tokens) == (100, 50, 30)


async def test_turn_completed_without_usage_emits_finish_only():
    [finish] = map_event({"type": "turn.completed"})
    assert isinstance(finish, FinishDelta) and finish.reason == "stop"


async def test_unknown_event_becomes_raw_delta():
    event = {"type": "some_future_codex_event", "payload": {"foo": 1}}
    [raw] = map_event(event)
    assert isinstance(raw, RawDelta)
    assert raw.event_type == "some_future_codex_event"
    assert raw.payload == event


async def test_malformed_jsonl_line_becomes_error_delta_stream_continues():
    lines = [
        '{"type":"thread.started","thread_id":"t1"}',
        "this is not json",
        '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
    ]
    deltas = await _collect(lines)
    # SessionIdDelta, ErrorDelta(parse), FinishDelta, UsageDelta
    kinds = [d.kind for d in deltas]
    assert kinds == ["session_id", "error", "finish", "usage"]
    err = deltas[1]
    assert isinstance(err, ErrorDelta)
    assert err.source == "parse"


async def test_blank_lines_are_skipped():
    lines = [
        "",
        "   ",
        '{"type":"thread.started","thread_id":"t9"}',
        "",
    ]
    deltas = await _collect(lines)
    assert len(deltas) == 1
    assert isinstance(deltas[0], SessionIdDelta)
    assert deltas[0].session_id == "t9"
