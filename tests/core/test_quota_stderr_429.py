# Step 4.2b: detect upstream 429-style errors in CLI stderr and
# surface them as a synthetic RateLimitDelta. Pure-function tests
# for the matcher; adapter-level integration is in
# tests/adapters/test_codex_stderr_429.py and test_gemini_stderr_429.py.
from __future__ import annotations

from freeloader.canonical.deltas import RateLimitDelta
from freeloader.core.quota import match_stderr_quota_pressure


def test_no_match_when_exit_code_zero():
    # A successful turn that happens to log the words "rate limit" in
    # benign telemetry must NOT emit pressure. Quota_signal is for
    # actual rate-limit hits, not vocabulary.
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text="info: monitoring rate limit headers",
        exit_code=0,
    )
    assert deltas == []


def test_no_match_when_exit_code_none():
    # exit_code=None is the "process never finished" case — adapter
    # shouldn't be calling us in that state, but we defend.
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text="rate limit hit",
        exit_code=None,
    )
    assert deltas == []


def test_no_match_when_stderr_empty():
    deltas = match_stderr_quota_pressure(
        provider="gemini",
        stderr_text="",
        exit_code=1,
    )
    assert deltas == []


def test_no_match_when_no_pattern_present():
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text="error: connection refused\nfailed to dial endpoint",
        exit_code=1,
    )
    assert deltas == []


def test_match_429_substring():
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text="HTTP 429: Too many requests",
        exit_code=1,
    )
    assert len(deltas) == 1
    d = deltas[0]
    assert isinstance(d, RateLimitDelta)
    assert d.rate_limit_type == "429"
    assert d.status == "exceeded"
    assert d.raw["provider"] == "codex"
    assert d.raw["exit_code"] == 1
    assert d.raw["source"] == "stderr_scan"
    assert "429" in d.raw["stderr_excerpt"]


def test_match_too_many_requests_case_insensitive():
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text="API Error: Too Many Requests",
        exit_code=2,
    )
    assert len(deltas) == 1
    assert deltas[0].rate_limit_type == "429"


def test_match_rate_limit_phrase():
    deltas = match_stderr_quota_pressure(
        provider="gemini",
        stderr_text="error: rate limit exceeded for user",
        exit_code=1,
    )
    assert len(deltas) == 1


def test_match_resource_exhausted_gemini_api():
    # Google AI Platform surfaces quota errors as RESOURCE_EXHAUSTED.
    deltas = match_stderr_quota_pressure(
        provider="gemini",
        stderr_text="[GoogleGenerativeAI Error]: code: 8 RESOURCE_EXHAUSTED",
        exit_code=1,
    )
    assert len(deltas) == 1
    assert "RESOURCE_EXHAUSTED" in deltas[0].raw["stderr_excerpt"]


def test_match_insufficient_quota_openai_style():
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text='{"error": {"code": "insufficient_quota"}}',
        exit_code=1,
    )
    assert len(deltas) == 1


def test_only_first_matching_line_emits():
    # Multiple matching lines = same 429 reported twice; one delta
    # is enough — strategy treats one as pressure already, journal
    # doesn't need duplicates.
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text=(
            "HTTP 429: Too many requests\nretrying after rate limit window\nstill 429\n"
        ),
        exit_code=1,
    )
    assert len(deltas) == 1
    # First matching line is captured.
    assert "Too many requests" in deltas[0].raw["stderr_excerpt"]


def test_excerpt_truncated_to_500_chars():
    long = "X" * 1000 + " 429 in here"
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text=long,
        exit_code=1,
    )
    assert len(deltas) == 1
    assert len(deltas[0].raw["stderr_excerpt"]) <= 500


def test_match_provider_tag_propagates():
    # raw.provider lets the journal show which CLI's stderr surfaced
    # the signal even if downstream code only sees the delta's raw.
    g = match_stderr_quota_pressure(provider="gemini", stderr_text="429", exit_code=1)
    c = match_stderr_quota_pressure(provider="codex", stderr_text="429", exit_code=1)
    assert g[0].raw["provider"] == "gemini"
    assert c[0].raw["provider"] == "codex"


def test_blank_lines_are_ignored():
    deltas = match_stderr_quota_pressure(
        provider="codex",
        stderr_text="\n\n   \n",
        exit_code=1,
    )
    assert deltas == []
