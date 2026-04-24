# ClaudeAdapter — shell-out to `claude -p --output-format stream-json --verbose`.
# PLAN principle #1 materialized for the first vendor.
#
# Structure:
#   map_event(event)    — pure JSONL-event → Deltas mapper; exercised by the
#                         golden-fixture replay test.
#   parse_stream(lines) — async generator over a line iterator; decodes each
#                         JSONL line and fans out map_event.
#   ClaudeAdapter.send  — spawns `claude -p`, feeds its stdout through
#                         parse_stream, yields Deltas. Subprocess path is
#                         untested in 1.2; e2e coverage lands at step 1.7.
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from freeloader.canonical.deltas import (
    Delta,
    ErrorDelta,
    FinishDelta,
    ModelUsage,
    RateLimitDelta,
    RawDelta,
    SessionIdDelta,
    TextDelta,
    UsageDelta,
)
from freeloader.config import resolve_data_dir

# claude result.subtype → OpenAI finish_reason. Spike observed "success" and
# (rarely) "error_max_turns" / "error_during_execution". Anything unknown
# falls through to "error" so the client sees a terminated stream.
_FINISH_MAP: dict[str, str] = {
    "success": "stop",
    "error_max_turns": "length",
    "error_during_execution": "error",
}


def map_event(event: dict) -> list[Delta]:
    """Map one claude JSONL event to 0+ Deltas. Pure function."""
    etype = event.get("type")

    if etype == "system" and event.get("subtype") == "init":
        sid = event.get("session_id")
        return [SessionIdDelta(session_id=sid)] if sid else []

    if etype == "assistant":
        msg = event.get("message") or {}
        out: list[Delta] = []
        for block in msg.get("content") or []:
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    out.append(TextDelta(text=text))
        return out

    if etype == "rate_limit_event":
        info = event.get("rate_limit_info") or {}
        return [
            RateLimitDelta(
                rate_limit_type=info.get("rateLimitType", "unknown"),
                status=info.get("status", "unknown"),
                resets_at=info.get("resetsAt"),
                overage_status=info.get("overageStatus"),
                raw=info,
            )
        ]

    if etype == "result":
        subtype = event.get("subtype") or "success"
        reason = _FINISH_MAP.get(subtype, "error")
        out = [FinishDelta(reason=reason)]
        models = _extract_model_usage(event)
        if models:
            out.append(UsageDelta(models=models))
        return out

    # Unrecognized event shape: surface it as RawDelta so the journal keeps
    # a record; adding a canonical variant is a deliberate decision, not an
    # accident of whatever claude just shipped.
    return [RawDelta(event_type=etype or "unknown", payload=event)]


def _extract_model_usage(result_event: dict) -> dict[str, ModelUsage]:
    # Spike observed top-level `modelUsage: {model_name: {...}}` on the
    # result event. Fallback to flat `usage` when modelUsage is absent —
    # older claude-code builds only emitted the flat shape.
    model_usage = result_event.get("modelUsage") or {}
    out: dict[str, ModelUsage] = {}
    for name, stats in model_usage.items():
        out[name] = _usage_from_claude_stats(stats)
    if out:
        return out
    usage = result_event.get("usage") or {}
    if usage:
        out["unknown"] = _usage_from_claude_stats(usage)
    return out


def _usage_from_claude_stats(stats: dict) -> ModelUsage:
    # claude uses camelCase in modelUsage (inputTokens, outputTokens,
    # cacheReadInputTokens) and snake_case in the top-level usage block
    # (input_tokens, output_tokens, cache_read_input_tokens). Accept both.
    def pick(*keys: str, default: int = 0) -> int:
        for k in keys:
            if k in stats and stats[k] is not None:
                return int(stats[k])
        return default

    return ModelUsage(
        input_tokens=pick("inputTokens", "input_tokens"),
        output_tokens=pick("outputTokens", "output_tokens"),
        cached_input_tokens=pick(
            "cacheReadInputTokens",
            "cache_read_input_tokens",
            "cached_input_tokens",
        ),
    )


def flatten_messages(messages: list[dict]) -> str:
    """Role-tagged plaintext flatten (PLAN decision #3, minimal form).

    CLIs take a single text prompt, not a structured role/content array.
    Flattening preserves turn boundaries with explicit delimiters so a
    prior assistant reply can't become prompt-injection fuel. Each role
    opens `[ROLE]` and closes `[/ROLE]`; blocks are separated by a blank
    line. Multimodal content blocks are reduced to their `text` parts
    (non-text blocks are dropped for 1.3 — multimodal lands later).
    """
    parts: list[str] = []
    for m in messages:
        role = (m.get("role") or "user").upper()
        content = m.get("content")
        if isinstance(content, list):
            text = "".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            )
        elif content is None:
            text = ""
        else:
            text = str(content)
        parts.append(f"[{role}]\n{text}\n[/{role}]")
    return "\n\n".join(parts)


async def parse_stream(lines: AsyncIterator[str]) -> AsyncIterator[Delta]:
    """Decode JSONL lines and yield Deltas. One malformed line becomes one
    ErrorDelta; the stream keeps flowing — a single corrupted line should
    not drop the whole turn."""
    async for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            yield ErrorDelta(message=f"invalid JSONL: {exc}", source="parse")
            continue
        for delta in map_event(event):
            yield delta


async def _stream_lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    async for raw in stream:
        yield raw.decode("utf-8", errors="replace")


class ClaudeAdapter:
    """Thin wrapper over `claude -p`. One subprocess per turn; no persistent
    process. Subprocess path is exercised end-to-end at step 1.7."""

    def __init__(
        self,
        executable: str = "claude",
        data_dir: Path | None = None,
    ) -> None:
        self.executable = executable
        self.data_dir = Path(data_dir) if data_dir else resolve_data_dir()

    def _scratch_for(self, session_id: str) -> Path:
        # Short turn_id suffix so two in-flight turns on the same
        # conversation (regenerate while retry is in-flight) don't collide.
        turn_id = uuid.uuid4().hex[:8]
        return self.data_dir / "scratch" / session_id / turn_id

    async def send(
        self,
        prompt: str,
        *,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        scratch = self._scratch_for(session_id)
        scratch.mkdir(parents=True, exist_ok=True)
        argv = [
            self.executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--session-id",
            session_id,
            "--add-dir",
            str(scratch),
        ]
        if resume_session_id:
            argv += ["-r", resume_session_id]
        argv.append(prompt)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(scratch),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert proc.stdout is not None
            async for delta in parse_stream(_stream_lines(proc.stdout)):
                yield delta
        finally:
            # Best-effort teardown so an aborted caller doesn't leak zombies
            # in local dev. Proper cancellation / SIGKILL-after-3s is phase 2.
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            # Per-turn scratch: remove the directory after the turn ends
            # (ROADMAP phase 1 specifies per-turn, not per-session).
            shutil.rmtree(scratch, ignore_errors=True)
