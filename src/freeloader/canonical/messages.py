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
