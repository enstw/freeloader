# Step 3.6: GET /v1/models returns the OpenAI-shaped discovery list.
# OpenAI clients call this on init; some refuse to operate when it
# returns an empty list. freeloader/auto only appears when 2+
# adapters are registered (no choice = no auto).
from __future__ import annotations

import time
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from freeloader.canonical.deltas import Delta
from freeloader.frontend.app import create_app
from freeloader.router import Router


class _NullAdapter:
    """Bare adapter for endpoint-shape tests; never actually invoked
    (the /v1/models handler doesn't call adapters)."""

    async def send(
        self,
        prompt: str,
        *,
        conversation_id: str,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        return
        yield  # pragma: no cover


def _client(**adapters) -> TestClient:
    return TestClient(create_app(router=Router(**adapters)))


def test_single_adapter_advertises_one_model_no_auto():
    res = _client(claude=_NullAdapter()).get("/v1/models")
    assert res.status_code == 200
    body = res.json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert ids == ["freeloader/claude"]


def test_multi_adapter_advertises_auto_first_then_each_provider():
    res = _client(
        claude=_NullAdapter(), codex=_NullAdapter(), gemini=_NullAdapter()
    ).get("/v1/models")
    assert res.status_code == 200
    ids = [m["id"] for m in res.json()["data"]]
    assert ids == [
        "freeloader/auto",
        "freeloader/claude",
        "freeloader/codex",
        "freeloader/gemini",
    ]


def test_two_adapters_also_get_auto():
    res = _client(claude=_NullAdapter(), codex=_NullAdapter()).get("/v1/models")
    assert res.status_code == 200
    ids = [m["id"] for m in res.json()["data"]]
    assert ids == ["freeloader/auto", "freeloader/claude", "freeloader/codex"]


def test_each_entry_has_openai_shape():
    res = _client(claude=_NullAdapter(), codex=_NullAdapter()).get("/v1/models")
    body = res.json()
    now = int(time.time())
    for entry in body["data"]:
        assert set(entry.keys()) == {"id", "object", "created", "owned_by"}
        assert entry["object"] == "model"
        assert entry["owned_by"] == "freeloader"
        # Created is a Unix timestamp generated at request time;
        # allow a small clock-drift window.
        assert isinstance(entry["created"], int)
        assert abs(entry["created"] - now) < 5


def test_provider_order_follows_constructor_kwarg_order():
    """Adapters in the response appear in the order they were passed
    to Router (claude, codex, gemini in the constructor signature),
    not insertion order of an arbitrary dict — this matches the
    round-robin cycle order so a user reading /v1/models sees the
    same order they'd be cycled through under freeloader/auto."""
    res = _client(gemini=_NullAdapter(), codex=_NullAdapter()).get("/v1/models")
    ids = [m["id"] for m in res.json()["data"]]
    # Even though we passed gemini first as a kwarg, the Router
    # constructor's fixed claude→codex→gemini ordering puts codex
    # ahead of gemini. /v1/models reflects this stable order.
    assert ids == ["freeloader/auto", "freeloader/codex", "freeloader/gemini"]
