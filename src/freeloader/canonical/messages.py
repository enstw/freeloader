# CanonicalMessage — internal representation between frontend translators
# and adapters. PLAN principle #4.
#
# Minimal shape for phase 1: role + content (text) + metadata bag.
# content_blocks (multimodal) and tool_calls get added when a caller needs
# them — MVP scope rule.
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CanonicalMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    metadata: dict = {}


def openai_to_canonical(m: dict) -> CanonicalMessage:
    """Convert an OpenAI-shaped message dict → CanonicalMessage.

    Multimodal content blocks are reduced to their text parts
    (non-text blocks are dropped; multimodal support lands when a
    real caller needs it).
    """
    role = m.get("role") or "user"
    if role not in ("system", "user", "assistant", "tool"):
        role = "user"
    content = m.get("content")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        text = str(content)
    return CanonicalMessage(role=role, content=text)
