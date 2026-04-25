# GeminiAdapter — shell-out to `gemini -p -o stream-json`. Third
# concrete CLIAdapter; the compound-provider outlier (PLAN principle
# #1, decision #16).
#
# Structure mirrors adapters/{claude,codex}.py:
#   map_event(event)    — pure JSONL-event → Deltas mapper.
#   parse_stream(lines) — async generator over a line iterator.
#   GeminiAdapter.send  — spawns `gemini -p`, feeds stdout through
#                         parse_stream, yields Deltas.
#
# Three points where gemini differs from claude/codex:
#   - Compound provider. One turn touches multiple sub-models;
#     `result.stats.models` carries a per-sub-model breakdown.
#     UsageDelta is built with one ModelUsage per entry (PLAN
#     principle #1: per-sub-model quota tracking is the only shape
#     that doesn't discard information).
#   - State isolation is not done via env var. GEMINI_CLI_HOME exists
#     but redirects EVERYTHING — including oauth_creds.json and
#     google_accounts.json — so the "OAuth global, state isolated"
#     invariant from PLAN decision #16 cannot be honored. Falling
#     back to PLAN's documented alternative: a per-adapter mutex
#     that serializes `send()` calls. Personal-use proxy accepts
#     the throughput cost.
#   - session_id is a UUID string in current gemini-cli (0.39.0),
#     not the integer index PLAN's 2026-04-05 spike recorded. The
#     adapter normalizes anyway (opaque string).
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
    RawDelta,
    SessionIdDelta,
    TextDelta,
    UsageDelta,
)
from freeloader.config import resolve_data_dir


def map_event(event: dict) -> list[Delta]:
    """Map one gemini JSONL event to 0+ Deltas. Pure function."""
    etype = event.get("type")

    if etype == "init":
        sid = event.get("session_id")
        return [SessionIdDelta(session_id=sid)] if sid else []

    if etype == "message":
        role = event.get("role")
        if role == "user":
            # Gemini echoes the user prompt back as a `message` event.
            # We already have the prompt (we sent it); don't surface
            # this to the OpenAI-shaped consumer.
            return []
        if role == "assistant":
            content = event.get("content", "")
            if content:
                return [TextDelta(text=content)]
            return []
        # Other roles (system, tool) are not part of MVP scope.
        return [RawDelta(event_type=f"message.{role or 'unknown'}", payload=event)]

    if etype == "result":
        status = event.get("status") or "success"
        reason = "stop" if status == "success" else "error"
        out: list[Delta] = [FinishDelta(reason=reason)]
        models = _extract_compound_usage(event)
        if models:
            out.append(UsageDelta(models=models))
        return out

    return [RawDelta(event_type=etype or "unknown", payload=event)]


def _extract_compound_usage(result_event: dict) -> dict[str, ModelUsage]:
    """Build the multi-model UsageDelta map from `stats.models`.

    Falls back to the flat `stats` totals under a single "gemini" key
    if `stats.models` is absent — older gemini-cli builds may not have
    surfaced the per-sub-model breakdown.
    """
    stats = result_event.get("stats") or {}
    models = stats.get("models") or {}
    out: dict[str, ModelUsage] = {}
    for name, sub in models.items():
        if isinstance(sub, dict):
            out[name] = _usage_from_gemini_stats(sub)
    if out:
        return out
    if stats.get("input_tokens") is not None or stats.get("output_tokens") is not None:
        out["gemini"] = _usage_from_gemini_stats(stats)
    return out


def _usage_from_gemini_stats(stats: dict) -> ModelUsage:
    return ModelUsage(
        input_tokens=int(stats.get("input_tokens") or 0),
        output_tokens=int(stats.get("output_tokens") or 0),
        cached_input_tokens=int(stats.get("cached") or 0),
    )


async def parse_stream(lines: AsyncIterator[str]) -> AsyncIterator[Delta]:
    """Decode JSONL lines and yield Deltas. One malformed line becomes
    one ErrorDelta; the stream keeps flowing."""
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


class GeminiAdapter:
    """Thin wrapper over `gemini -p -o stream-json`.

    Per-adapter `_send_lock` serializes concurrent `send()` calls so
    two turns don't race on gemini's global `~/.gemini/` state. This
    is the PLAN-decision-#16 fallback for CLIs where the env-var
    redirect mechanism couples OAuth to session storage (gemini's
    GEMINI_CLI_HOME does exactly that).
    """

    def __init__(
        self,
        executable: str = "gemini",
        data_dir: Path | None = None,
    ) -> None:
        self.executable = executable
        self.data_dir = Path(data_dir) if data_dir else resolve_data_dir()
        # Instance-scoped, NOT class-scoped: two distinct GeminiAdapter
        # instances do NOT block each other. Tests rely on this for
        # the negative-case assertion.
        self._send_lock = asyncio.Lock()

    def _scratch_for(self, session_id: str) -> Path:
        turn_id = uuid.uuid4().hex[:8]
        return self.data_dir / "scratch" / session_id / turn_id

    async def send(
        self,
        prompt: str,
        *,
        conversation_id: str,
        session_id: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[Delta]:
        # Hold the lock for the lifetime of the subprocess: from
        # spawn through the last delta emission. Without this, two
        # in-flight gemini turns would both write into the same
        # ~/.gemini/projects.json and could clobber each other's
        # session records.
        async with self._send_lock:
            scratch = self._scratch_for(session_id)
            scratch.mkdir(parents=True, exist_ok=True)
            argv = [
                self.executable,
                "-p",
                prompt,
                "-o",
                "stream-json",
            ]
            if resume_session_id:
                argv += ["--resume", resume_session_id]

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
                if proc is not None and proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=3.0)
                    except TimeoutError:
                        proc.kill()
                        await proc.wait()
                shutil.rmtree(scratch, ignore_errors=True)
