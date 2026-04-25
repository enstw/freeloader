# Cross-adapter canonical contract suite (step 3.7). Per-adapter
# golden tests pin the exact event→delta mapping for each vendor;
# this suite proves the shared invariants hold uniformly across
# every CLIAdapter — the parts of the canonical contract that any
# fourth adapter must also honor on day one.
#
# Each invariant is parametrized over (claude, codex, gemini); add
# the adapter module to ADAPTERS to opt new vendors into the suite.
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import pytest

from freeloader.adapters import claude as claude_mod
from freeloader.adapters import codex as codex_mod
from freeloader.adapters import gemini as gemini_mod
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

# (provider_name, adapter_module, golden_fixture_path).
# Each module must expose `map_event(event: dict) -> list[Delta]` and
# `parse_stream(lines: AsyncIterator[str]) -> AsyncIterator[Delta]`.
ADAPTERS = [
    pytest.param("claude", claude_mod, FIXTURES / "claude_basic.jsonl", id="claude"),
    pytest.param("codex", codex_mod, FIXTURES / "codex_basic.jsonl", id="codex"),
    pytest.param("gemini", gemini_mod, FIXTURES / "gemini_basic.jsonl", id="gemini"),
]


async def _empty_async_iter() -> AsyncIterator[str]:
    return
    yield  # pragma: no cover


async def _async_iter(lines: Iterable[str]) -> AsyncIterator[str]:
    for line in lines:
        yield line


async def _collect(stream: AsyncIterator[Delta]) -> list[Delta]:
    return [d async for d in stream]


# ---------------- parse_stream invariants ----------------


@pytest.mark.parametrize("name,mod,fixture", ADAPTERS)
async def test_parse_stream_empty_yields_nothing(name: str, mod, fixture: Path):
    out = await _collect(mod.parse_stream(_empty_async_iter()))
    assert out == []


@pytest.mark.parametrize("name,mod,fixture", ADAPTERS)
async def test_parse_stream_blank_only_yields_nothing(name: str, mod, fixture: Path):
    out = await _collect(mod.parse_stream(_async_iter(["", "   ", "\t", ""])))
    assert out == []


@pytest.mark.parametrize("name,mod,fixture", ADAPTERS)
async def test_parse_stream_malformed_line_yields_one_error_delta_and_continues(
    name: str, mod, fixture: Path
):
    # Malformed line is sandwiched between two empty lines so we can
    # also confirm the stream resumes after the error.
    out = await _collect(mod.parse_stream(_async_iter(["", "{not json", ""])))
    assert len(out) == 1
    assert isinstance(out[0], ErrorDelta)
    assert out[0].source == "parse"


# ---------------- map_event invariants ----------------


@pytest.mark.parametrize("name,mod,fixture", ADAPTERS)
def test_unknown_event_yields_exactly_one_raw_delta(name: str, mod, fixture: Path):
    out = mod.map_event({"type": "some_unrecognized_event_99"})
    assert len(out) == 1
    assert isinstance(out[0], RawDelta)
    assert out[0].event_type == "some_unrecognized_event_99"


@pytest.mark.parametrize("name,mod,fixture", ADAPTERS)
def test_event_with_no_type_yields_one_raw_delta(name: str, mod, fixture: Path):
    out = mod.map_event({"some": "garbage"})
    assert len(out) == 1
    assert isinstance(out[0], RawDelta)
    assert out[0].event_type == "unknown"


# ---------------- canonical sequence invariants ----------------


@pytest.mark.parametrize("name,mod,fixture", ADAPTERS)
async def test_golden_fixture_produces_canonical_minimum_sequence(
    name: str, mod, fixture: Path
):
    """The canonical contract: any fixture representing a normal,
    successful turn must produce — in order — at least one
    SessionIdDelta, at least one TextDelta, exactly one FinishDelta,
    and exactly one UsageDelta. Adapters may add other Deltas
    (RateLimitDelta for claude, e.g.) but this minimum sequence is
    the load-bearing one for the OpenAI-shaped frontend."""
    text = fixture.read_text()
    out = await _collect(mod.parse_stream(_async_iter(text.splitlines())))

    # At least one of each load-bearing kind in the right relative order.
    sid_idx = next(
        (i for i, d in enumerate(out) if isinstance(d, SessionIdDelta)), None
    )
    text_idx = next((i for i, d in enumerate(out) if isinstance(d, TextDelta)), None)
    fin_idx = next((i for i, d in enumerate(out) if isinstance(d, FinishDelta)), None)
    usage_idx = next((i for i, d in enumerate(out) if isinstance(d, UsageDelta)), None)

    assert sid_idx is not None, f"{name}: no SessionIdDelta"
    assert text_idx is not None, f"{name}: no TextDelta"
    assert fin_idx is not None, f"{name}: no FinishDelta"
    assert usage_idx is not None, f"{name}: no UsageDelta"

    # Canonical ordering: session before text, text before finish,
    # finish at-or-before usage. Adapters that violate this would
    # break the streaming SSE contract.
    assert sid_idx < text_idx, f"{name}: SessionId must precede TextDelta"
    assert text_idx < fin_idx, f"{name}: TextDelta must precede FinishDelta"
    assert fin_idx <= usage_idx, f"{name}: FinishDelta must be at-or-before UsageDelta"

    # FinishDelta and UsageDelta must each appear exactly once per turn.
    assert sum(1 for d in out if isinstance(d, FinishDelta)) == 1, (
        f"{name}: FinishDelta must appear exactly once"
    )
    assert sum(1 for d in out if isinstance(d, UsageDelta)) == 1, (
        f"{name}: UsageDelta must appear exactly once"
    )

    # FinishDelta.reason must be one of the canonical OpenAI values.
    finish = next(d for d in out if isinstance(d, FinishDelta))
    assert finish.reason in (
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "error",
    )

    # UsageDelta must carry at least one model entry; gemini's
    # compound case may carry multiple.
    usage = next(d for d in out if isinstance(d, UsageDelta))
    assert len(usage.models) >= 1
    for mu in usage.models.values():
        assert mu.input_tokens >= 0
        assert mu.output_tokens >= 0


# ---------------- adapter-class API surface invariant ----------------


@pytest.mark.parametrize(
    "adapter_class",
    [claude_mod.ClaudeAdapter, codex_mod.CodexAdapter, gemini_mod.GeminiAdapter],
    ids=["ClaudeAdapter", "CodexAdapter", "GeminiAdapter"],
)
def test_adapter_send_signature_matches_protocol(adapter_class):
    """Every adapter's send() must accept (prompt, *, conversation_id,
    session_id, resume_session_id=None) — the Router reaches in via
    those exact keyword names. A signature mismatch would only show
    at first dispatch, after the test pyramid; this catches it at
    import time."""
    import inspect

    sig = inspect.signature(adapter_class.send)
    params = sig.parameters
    # `self` + `prompt` positional, then keyword-only kwargs.
    assert "prompt" in params
    assert "conversation_id" in params
    assert "session_id" in params
    assert "resume_session_id" in params
    assert params["conversation_id"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["session_id"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["resume_session_id"].kind == inspect.Parameter.KEYWORD_ONLY
