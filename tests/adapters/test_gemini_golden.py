# Principle #8: golden-fixture replay catches gemini stream-json shape
# drift early. Fixture was hand-derived from a live `gemini -p
# -o stream-json` capture on 2026-04-25 (gemini-cli 0.39.0); the
# subprocess path is exercised in the opt-in smoke harness later in
# phase 3.
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from freeloader.adapters.gemini import map_event, parse_stream
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
    text = (FIXTURES / "gemini_basic.jsonl").read_text()
    deltas = await _collect(text.splitlines())

    # The user-role echo message yields nothing — we already have the
    # prompt locally. So the canonical sequence is:
    assert [type(d).__name__ for d in deltas] == [
        "SessionIdDelta",
        "TextDelta",
        "FinishDelta",
        "UsageDelta",
    ]

    sid, txt, fin, usage = deltas
    assert isinstance(sid, SessionIdDelta)
    assert sid.session_id == "004e7cb9-10e2-44af-842c-1f1c59c9ab2f"
    assert isinstance(txt, TextDelta) and txt.text == "ok"
    assert isinstance(fin, FinishDelta) and fin.reason == "stop"
    assert isinstance(usage, UsageDelta)
    # The compound-provider invariant: stats.models had two entries,
    # so the UsageDelta carries one ModelUsage per sub-model.
    assert set(usage.models) == {"gemini-2.5-flash-lite", "gemini-3-flash-preview"}


async def test_init_without_session_id_yields_nothing():
    assert map_event({"type": "init"}) == []


async def test_user_message_is_skipped():
    # The user prompt is echoed back as a message event; we suppress
    # it because the OpenAI-shaped consumer never asked for it.
    assert map_event({"type": "message", "role": "user", "content": "hi"}) == []


async def test_assistant_message_yields_text_delta():
    [out] = map_event(
        {"type": "message", "role": "assistant", "content": "hello", "delta": True}
    )
    assert isinstance(out, TextDelta) and out.text == "hello"


async def test_assistant_message_empty_content_yields_nothing():
    assert map_event({"type": "message", "role": "assistant", "content": ""}) == []


async def test_message_with_unknown_role_becomes_raw_delta():
    event = {"type": "message", "role": "tool", "content": "stuff"}
    [raw] = map_event(event)
    assert isinstance(raw, RawDelta)
    assert raw.event_type == "message.tool"


async def test_result_status_error_maps_to_error_finish():
    event = {"type": "result", "status": "error_max_turns", "stats": {}}
    out = map_event(event)
    finish = out[0]
    assert isinstance(finish, FinishDelta) and finish.reason == "error"


async def test_result_without_models_falls_back_to_flat_stats():
    # Older gemini builds may not surface stats.models; exercise the
    # fallback path.
    event = {
        "type": "result",
        "status": "success",
        "stats": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cached": 30,
        },
    }
    out = map_event(event)
    assert len(out) == 2
    finish, usage = out
    assert isinstance(finish, FinishDelta) and finish.reason == "stop"
    assert isinstance(usage, UsageDelta)
    assert set(usage.models) == {"gemini"}
    mu = usage.models["gemini"]
    assert (mu.input_tokens, mu.output_tokens, mu.cached_input_tokens) == (100, 50, 30)


async def test_unknown_event_becomes_raw_delta():
    event = {"type": "some_future_gemini_event", "payload": {"k": 1}}
    [raw] = map_event(event)
    assert isinstance(raw, RawDelta)
    assert raw.event_type == "some_future_gemini_event"


async def test_malformed_jsonl_line_becomes_error_delta_stream_continues():
    lines = [
        '{"type":"init","session_id":"u1"}',
        "this is not json",
        '{"type":"result","status":"success","stats":{"input_tokens":1,"output_tokens":1}}',
    ]
    deltas = await _collect(lines)
    kinds = [d.kind for d in deltas]
    assert kinds == ["session_id", "error", "finish", "usage"]
    err = deltas[1]
    assert isinstance(err, ErrorDelta)
    assert err.source == "parse"


async def test_blank_lines_are_skipped():
    lines = ["", "   ", '{"type":"init","session_id":"u9"}', ""]
    deltas = await _collect(lines)
    assert len(deltas) == 1
    assert isinstance(deltas[0], SessionIdDelta)
    assert deltas[0].session_id == "u9"
