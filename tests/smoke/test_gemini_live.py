# Live-gemini smoke test. Mirror of test_claude_live.py / test_codex_live.py
# — spawns the real gemini CLI through create_app() + a Router pinned to
# gemini, and asserts the response actually contains content.
#
# Why this exists: same forcing function as the other two smoke tests.
# Gemini's auth-coupling lesson (GEMINI_CLI_HOME redirects auth files,
# 2026-04-25) is already on file — the adapter does not touch
# GEMINI_CLI_HOME. This test guards that the workaround stays correct
# end-to-end, and that the gemini executable is actually findable from
# a Python subprocess (a real issue when gemini is installed via npx and
# only exposed as a shell alias — symlink into PATH required).
#
# Run policy: see tests/smoke/test_claude_live.py header.
from __future__ import annotations

import os
import shutil

import pytest
from fastapi.testclient import TestClient

from freeloader.adapters.gemini import GeminiAdapter
from freeloader.frontend.app import create_app
from freeloader.router import Router

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        shutil.which("gemini") is None,
        reason=(
            "gemini CLI not on PATH (npx-installed gemini is a shell alias; "
            "symlink ~/tools/gemini-cli/bin/gemini into a PATH dir)"
        ),
    ),
    pytest.mark.skipif(
        bool(os.environ.get("FREELOADER_SKIP_LIVE")),
        reason="FREELOADER_SKIP_LIVE set",
    ),
]


def test_real_gemini_returns_assistant_text(tmp_path, monkeypatch):
    monkeypatch.setenv("FREELOADER_DATA_DIR", str(tmp_path))

    client = TestClient(create_app(router=Router(gemini=GeminiAdapter())))

    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "freeloader/gemini",
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

    usage = body.get("usage") or {}
    assert usage.get("prompt_tokens", 0) > 0, f"zero prompt_tokens: {usage!r}"
    assert usage.get("completion_tokens", 0) > 0, f"zero completion_tokens: {usage!r}"

    assert "pong" in content.lower(), (
        f"unexpected content (gemini may have agent-looped): {content!r}"
    )

    assert body["choices"][0]["finish_reason"] == "stop"
