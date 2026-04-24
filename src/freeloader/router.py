# Minimal router — dispatches to ClaudeAdapter.
#
# Phase 1 scope: no quota logic, no round-robin, one backend. Generates
# a fresh session UUID per request; conversation identity (decision #14
# hash-of-prefix) and resume lands at step 1.7. Quota-aware routing is
# phase 4 (PLAN principle #5).
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Protocol

from freeloader.adapters.claude import ClaudeAdapter, flatten_messages
from freeloader.canonical.deltas import Delta


class _Adapter(Protocol):
    def send(
        self,
        prompt: str,
        *,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]: ...


class Router:
    def __init__(self, claude: _Adapter | None = None) -> None:
        self.claude: _Adapter = claude or ClaudeAdapter()

    async def dispatch(
        self,
        messages: list[dict],
        *,
        session_id: str | None = None,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        sid = session_id or str(uuid.uuid4())
        prompt = flatten_messages(messages)
        async for delta in self.claude.send(
            prompt,
            session_id=sid,
            resume_session_id=resume_session_id,
        ):
            yield delta
