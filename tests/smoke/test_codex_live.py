# Live-codex smoke test. Mirror of test_claude_live.py — spawns the
# real codex CLI through create_app() + a Router pinned to codex, and
# asserts the response actually contains content.
#
# Why this exists: same forcing function as the claude smoke test.
# Codex shipped with the analogous CODEX_HOME-strips-OAuth bug as
# claude (see JOURNAL `codex_state_isolation_revert`); a single live
# turn would have caught it before operating against a real client.
#
# Run policy: see tests/smoke/test_claude_live.py header. Default
# pytest excludes; opt in with `uv run pytest -m smoke`. Auto-skips
# if `codex` is not on PATH or `FREELOADER_SKIP_LIVE` is set.
from __future__ import annotations

import os
import shutil

import pytest
from fastapi.testclient import TestClient

from freeloader.adapters.codex import CodexAdapter
from freeloader.frontend.app import create_app
from freeloader.router import Router

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        shutil.which("codex") is None,
        reason="codex CLI not on PATH",
    ),
    pytest.mark.skipif(
        bool(os.environ.get("FREELOADER_SKIP_LIVE")),
        reason="FREELOADER_SKIP_LIVE set",
    ),
]


def test_real_codex_returns_assistant_text(tmp_path, monkeypatch):
    monkeypatch.setenv("FREELOADER_DATA_DIR", str(tmp_path))

    # Pin Router to codex only — default Router constructs claude.
    client = TestClient(create_app(router=Router(codex=CodexAdapter())))

    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/codex",
            "messages": [
                {"role": "user", "content": "reply with the single word: pong"}
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()

    msg = body["choices"][0]["message"]
    content = msg.get("content") or ""
    assert content, f"empty content (regression?). Body: {body!r}"
    assert "401" not in content and "Unauthorized" not in content, (
        f"OAuth stripped (regression of CODEX_HOME bug?). Body: {body!r}"
    )

    usage = body.get("usage") or {}
    assert usage.get("prompt_tokens", 0) > 0, f"zero prompt_tokens: {usage!r}"
    assert usage.get("completion_tokens", 0) > 0, f"zero completion_tokens: {usage!r}"

    assert "pong" in content.lower(), (
        f"unexpected content (codex may have agent-looped): {content!r}"
    )

    assert body["choices"][0]["finish_reason"] == "stop"
