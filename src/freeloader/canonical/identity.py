# Conversation identity — PLAN decision #14.
#
# /v1/chat/completions is stateless; OpenAI has no conversation_id
# concept. We compute a stable fingerprint over the "preceding system
# messages + first user message" so the same client that resends the
# same leading turns produces the same conversation key. Clients that
# want explicit control send X-FreelOAder-Conversation-Id; that header
# wins over the hash in the handler.
#
# Decision #14 is provisional — revisit when a real Chat-Completions
# client surfaces a case this doesn't cover.
from __future__ import annotations

import hashlib
import json


def hash_of_prefix(messages: list[dict]) -> str:
    """Stable SHA-256 over the message prefix up to and including the
    first user turn. Returns `cv_<16hex>`.

    Accepts OpenAI-shaped dicts (role/content). Normalizes content to
    a plain string when it is a list of blocks — multimodal blocks are
    reduced to their text parts for hashing purposes, matching the
    canonical flatten (see adapters/claude.flatten_messages).
    """
    prefix: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = _normalize_content(m.get("content"))
        if role == "system":
            # preceding system messages accumulate until the first user
            if not any(p.get("role") == "user" for p in prefix):
                prefix.append({"role": "system", "content": content})
            continue
        if role == "user":
            prefix.append({"role": "user", "content": content})
            break
        # assistant / tool messages before a user turn are unusual; skip
        # them rather than folding into the key (would make the key
        # unstable if the client later reorders leading messages).
    if not prefix:
        # Degenerate case: no user turn at all. Hash the empty prefix so
        # the conversation key is still deterministic; the handler will
        # reject the request (a chat request without a user turn is a
        # 400 in any sane frontend).
        prefix = []
    payload = json.dumps(prefix, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"cv_{digest}"


def _normalize_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)
