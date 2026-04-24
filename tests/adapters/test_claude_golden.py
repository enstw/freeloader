# Principle #8: golden-fixture replay gives early warning when claude's
# stream-json shape drifts. The subprocess path in ClaudeAdapter.send() is
# exercised at step 1.7 e2e; this test locks the JSONL→Delta mapping alone.
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from freeloader.adapters.claude import map_event, parse_stream
from freeloader.canonical.deltas import (
    Delta,
    ErrorDelta,
    FinishDelta,
    RateLimitDelta,
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
    text = (FIXTURES / "claude_basic.jsonl").read_text()
    deltas = await _collect(text.splitlines())

    assert [type(d).__name__ for d in deltas] == [
        "SessionIdDelta",
        "TextDelta",
        "RateLimitDelta",
        "FinishDelta",
        "UsageDelta",
    ]

    sid, txt, rl, fin, usage = deltas
    assert isinstance(sid, SessionIdDelta) and sid.session_id == "conv-abc-123"
    assert isinstance(txt, TextDelta) and txt.text == "Hello! The answer is 42."
    assert isinstance(rl, RateLimitDelta)
    assert rl.rate_limit_type == "five_hour"
    assert rl.status == "allowed"
    assert rl.resets_at == 1775408400
    assert rl.overage_status == "none"
    assert rl.raw["rateLimitType"] == "five_hour"
    assert isinstance(fin, FinishDelta) and fin.reason == "stop"
    assert isinstance(usage, UsageDelta)
    assert set(usage.models) == {"claude-opus-4-6"}
    mu = usage.models["claude-opus-4-6"]
    assert (mu.input_tokens, mu.output_tokens, mu.cached_input_tokens) == (12, 9, 0)


async def test_multiple_text_blocks_emit_multiple_text_deltas():
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "first "},
                {"type": "text", "text": "second"},
            ]
        },
    }
    out = map_event(event)
    assert [d.text for d in out if isinstance(d, TextDelta)] == ["first ", "second"]


async def test_unknown_event_becomes_raw_delta():
    event = {"type": "some_future_claude_event", "payload": {"foo": 1}}
    [raw] = map_event(event)
    assert isinstance(raw, RawDelta)
    assert raw.event_type == "some_future_claude_event"
    assert raw.payload == event


async def test_malformed_jsonl_line_becomes_error_delta_stream_continues():
    lines = [
        '{"type":"system","subtype":"init","session_id":"s1"}',
        "this is not json",
        '{"type":"result","subtype":"success","modelUsage":{"m":{"inputTokens":1,"outputTokens":1}}}',
    ]
    deltas = await _collect(lines)
    # SessionIdDelta, ErrorDelta(parse), FinishDelta, UsageDelta
    kinds = [d.kind for d in deltas]
    assert kinds == ["session_id", "error", "finish", "usage"]
    err = deltas[1]
    assert isinstance(err, ErrorDelta)
    assert err.source == "parse"


async def test_result_without_model_usage_falls_back_to_flat_usage():
    event = {
        "type": "result",
        "subtype": "success",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 30,
        },
    }
    out = map_event(event)
    finish, usage = out
    assert isinstance(finish, FinishDelta) and finish.reason == "stop"
    assert isinstance(usage, UsageDelta)
    assert set(usage.models) == {"unknown"}
    mu = usage.models["unknown"]
    assert (mu.input_tokens, mu.output_tokens, mu.cached_input_tokens) == (100, 50, 30)


async def test_result_error_subtype_maps_to_error_finish():
    event = {"type": "result", "subtype": "error_during_execution"}
    [finish] = map_event(event)
    assert isinstance(finish, FinishDelta) and finish.reason == "error"


async def test_blank_lines_are_skipped():
    lines = ["", "   ", '{"type":"system","subtype":"init","session_id":"s9"}', ""]
    deltas = await _collect(lines)
    assert len(deltas) == 1
    assert isinstance(deltas[0], SessionIdDelta)
    assert deltas[0].session_id == "s9"
