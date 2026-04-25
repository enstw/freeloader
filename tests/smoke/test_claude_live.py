# Live-claude smoke test. Spawns the REAL claude CLI through the
# production wire-up (create_app + default Router → ClaudeAdapter) and
# asserts the response actually contains content.
#
# Why this exists: the MVP shipped with two claude-adapter bugs that no
# unit/e2e test caught because every test in the suite uses FakeAdapter.
# The bugs surfaced on first live run via Hermes Agent (see JOURNAL
# 2026-04-25 entries `claude_argv_variadic` and
# `claude_state_isolation_revert`). The shape of both bugs was the same:
# FreelOAder returned 200 with empty content because the adapter's
# subprocess invocation was broken in a way the parser could not detect.
# A single live turn would have caught both before they reached the
# operator.
#
# Run policy:
#   - Excluded from the default `pytest` run (see pyproject `addopts =
#     "-ra -m 'not smoke'"`). Costs real money — each turn eats 6k–14k
#     input tokens of claude's cold-cache agent prompt (~$0.10+).
#   - Run explicitly: `uv run pytest -m smoke`.
#   - Auto-skips if `claude` is not on PATH or `FREELOADER_SKIP_LIVE`
#     env var is set (escape hatch for CI / restricted environments).
#
# What it guards (post-mortem of the two bugs):
#   - `--add-dir` does not swallow the prompt arg → claude sees the
#     prompt and produces output (catches bug 1).
#   - The adapter does not strip OAuth → claude authenticates and the
#     response is real assistant text, not "Not logged in" (catches
#     bug 2 indirectly: the assertion is "non-empty content from the
#     model", which is also what the operator actually wants).
from __future__ import annotations

import os
import shutil

import pytest
from fastapi.testclient import TestClient

from freeloader.frontend.app import create_app

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude CLI not on PATH",
    ),
    pytest.mark.skipif(
        bool(os.environ.get("FREELOADER_SKIP_LIVE")),
        reason="FREELOADER_SKIP_LIVE set",
    ),
]


def test_real_claude_returns_assistant_text(tmp_path, monkeypatch):
    # Pin data_dir so this run doesn't pollute ~/.local/share/freeloader/.
    monkeypatch.setenv("FREELOADER_DATA_DIR", str(tmp_path))

    client = TestClient(create_app())

    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/claude",
            "messages": [
                {"role": "user", "content": "reply with the single word: pong"}
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()

    msg = body["choices"][0]["message"]
    content = msg.get("content") or ""
    # Either bug returned "" (bug 1) or "Not logged in · Please run /login"
    # (bug 2). Both are non-real model output.
    assert content, f"empty content (regression of bug 1?). Body: {body!r}"
    assert "Not logged in" not in content, (
        f"OAuth stripped (regression of bug 2?). Body: {body!r}"
    )

    # Real turns report token usage; the original bug-1 response had zeros.
    usage = body.get("usage") or {}
    assert usage.get("prompt_tokens", 0) > 0, f"zero prompt_tokens: {usage!r}"
    assert usage.get("completion_tokens", 0) > 0, f"zero completion_tokens: {usage!r}"

    # Loose content check: claude is non-deterministic but a "reply pong"
    # prompt that returns something other than 'pong'-ish text is a useful
    # signal that the wire-up routed text through correctly.
    assert "pong" in content.lower(), (
        f"unexpected content (claude may have agent-looped): {content!r}"
    )

    assert body["choices"][0]["finish_reason"] == "stop"
