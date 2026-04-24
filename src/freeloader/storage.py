# Append-only JSONL storage — PLAN decision #6.
#
# ConversationStore writes to <data_dir>/conversations/<conv_id>.jsonl
# (one line per CanonicalMessage). EventWriter writes to
# <data_dir>/events.jsonl (runtime events; turn_done, rate_limit,
# spawn_error, ...). Both are append-only; rewrite() only exists on
# ConversationStore for the regenerate-last case.
#
# No asyncio lock for 1.7 — single-process single-test. Principle #5
# style concurrent-writer lock lands when a real server handles
# concurrent conversations.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from freeloader.canonical.messages import CanonicalMessage


class ConversationStore:
    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)

    def _path(self, conversation_id: str) -> Path:
        return self.data_dir / "conversations" / f"{conversation_id}.jsonl"

    def load(self, conversation_id: str) -> list[CanonicalMessage]:
        path = self._path(conversation_id)
        if not path.exists():
            return []
        out: list[CanonicalMessage] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(CanonicalMessage.model_validate_json(line))
        return out

    def append(self, conversation_id: str, messages: list[CanonicalMessage]) -> None:
        if not messages:
            return
        path = self._path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for m in messages:
                f.write(m.model_dump_json())
                f.write("\n")

    def rewrite(self, conversation_id: str, messages: list[CanonicalMessage]) -> None:
        path = self._path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for m in messages:
                f.write(m.model_dump_json())
                f.write("\n")


class EventWriter:
    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)

    def _path(self) -> Path:
        return self.data_dir / "events.jsonl"

    def write(self, event: dict[str, Any]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False))
            f.write("\n")


class _NoOpConversationStore:
    """Default ConversationStore when tests / embedded callers don't care
    about persistence. Acts like an empty store and drops writes."""

    def load(self, conversation_id: str) -> list[CanonicalMessage]:
        return []

    def append(self, conversation_id: str, messages: list[CanonicalMessage]) -> None:
        pass

    def rewrite(self, conversation_id: str, messages: list[CanonicalMessage]) -> None:
        pass


class _NoOpEventWriter:
    def write(self, event: dict[str, Any]) -> None:
        pass


def default_store() -> _NoOpConversationStore:
    return _NoOpConversationStore()


def default_events() -> _NoOpEventWriter:
    return _NoOpEventWriter()
