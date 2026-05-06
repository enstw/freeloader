# Shared subprocess helpers for adapters.
#
# CLI adapters spawn a process per turn and iterate its stdout as a
# JSONL stream while leaving stderr=PIPE. If stderr fills its 64 KiB
# pipe buffer while we're consuming stdout, the subprocess deadlocks
# waiting for the buffer to drain. Concurrent draining via
# asyncio.Task prevents that. Step 4.2b additionally uses the
# captured text to scan for upstream 429s in codex/gemini stderr;
# claude.py drains defensively even though its rate_limit_event lives
# on stdout.
from __future__ import annotations

import asyncio


async def drain_stream_to_str(stream: asyncio.StreamReader | None) -> str:
    """Read a subprocess pipe to EOF and return the decoded text.

    Schedule as `asyncio.create_task(drain_stream_to_str(proc.stderr))`
    so the subprocess never blocks on a full pipe buffer while the
    main coroutine is consuming proc.stdout.
    """
    if stream is None:
        return ""
    chunks: list[bytes] = []
    async for chunk in stream:
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")
