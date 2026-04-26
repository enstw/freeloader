# CodexAdapter — shell-out to `codex exec --json`. Second concrete
# implementation of the CLIAdapter Protocol (PLAN principle #1, decision
# #16).
#
# Structure mirrors adapters/claude.py:
#   map_event(event)    — pure JSONL-event → Deltas mapper.
#   parse_stream(lines) — async generator over a line iterator.
#   CodexAdapter.send   — spawns `codex exec [resume]`, feeds stdout
#                         through parse_stream, yields Deltas. Lifecycle
#                         finally mirrors claude.py: SIGTERM → 3s wait →
#                         SIGKILL → rmtree(scratch).
#
# Codex differs from claude in three ways that affect this adapter:
#   - thread_id is server-assigned (claude accepts a client UUID).
#     PLAN decision #16: use the value echoed in `thread.started` as
#     the backend session id; the router-passed session_id is only used
#     to scope the scratch dir on first turn.
#   - resume verb is `codex exec resume <thread_id>`, not a -r flag.
#     Sandbox flag (-s) is rejected on resume — the original session's
#     sandbox policy persists.
#   - turn.completed carries `usage` but no per-model breakdown.
#     UsageDelta is keyed by "codex" (provider tag) until the CLI
#     surfaces sub-model identity.
from __future__ import annotations

import asyncio
import json
import os
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

# Single sentinel for the "model name" axis. Codex's turn.completed
# does not carry a model identifier; routing distinguishes provider
# pools (PLAN principle #5), and a stable provider tag is enough until
# codex starts emitting sub-model identity in its events.
_CODEX_MODEL_TAG = "codex"


def map_event(event: dict) -> list[Delta]:
    """Map one codex JSONL event to 0+ Deltas. Pure function."""
    etype = event.get("type")

    if etype == "thread.started":
        tid = event.get("thread_id")
        return [SessionIdDelta(session_id=tid)] if tid else []

    if etype == "turn.started":
        # No-op: codex emits this between thread.started and the first
        # item.completed. The router uses arrival of any delta to flip
        # the turn from SPAWNING → STREAMING (router.py); turn.started
        # itself carries no canonical information.
        return []

    if etype == "item.completed":
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            text = item.get("text", "")
            if text:
                return [TextDelta(text=text)]
            return []
        # Other item types (reasoning, tool_call, etc.) are not part of
        # MVP scope (PLAN: tool calls deferred to phase 5). Record them
        # for the journal but do not surface to the client.
        return [
            RawDelta(event_type=f"item.{item.get('type', 'unknown')}", payload=event)
        ]

    if etype == "turn.completed":
        usage = event.get("usage") or {}
        out: list[Delta] = [FinishDelta(reason="stop")]
        if usage:
            out.append(
                UsageDelta(models={_CODEX_MODEL_TAG: _usage_from_codex_stats(usage)})
            )
        return out

    # Unrecognized event shape: surface as RawDelta so the journal keeps
    # a record. Adding a canonical variant is a deliberate decision.
    return [RawDelta(event_type=etype or "unknown", payload=event)]


def _usage_from_codex_stats(stats: dict) -> ModelUsage:
    return ModelUsage(
        input_tokens=int(stats.get("input_tokens") or 0),
        output_tokens=int(stats.get("output_tokens") or 0),
        cached_input_tokens=int(stats.get("cached_input_tokens") or 0),
    )


async def parse_stream(lines: AsyncIterator[str]) -> AsyncIterator[Delta]:
    """Decode JSONL lines and yield Deltas. One malformed line becomes
    one ErrorDelta; the stream keeps flowing — a single corrupted line
    should not drop the whole turn."""
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


class CodexAdapter:
    """Thin wrapper over `codex exec --json`. One subprocess per turn;
    no persistent process. Per-conversation CODEX_HOME isolation lands
    at step 3.2."""

    def __init__(
        self,
        executable: str = "codex",
        data_dir: Path | None = None,
    ) -> None:
        self.executable = executable
        self.data_dir = Path(data_dir) if data_dir else resolve_data_dir()

    def _scratch_for(self, session_id: str) -> Path:
        # Short turn_id suffix so two in-flight turns on the same
        # conversation don't collide (mirrors claude.py).
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
        scratch = self._scratch_for(session_id)
        scratch.mkdir(parents=True, exist_ok=True)
        if resume_session_id:
            # Resume: codex exec resume <thread_id> <prompt>. Sandbox
            # flag is rejected on resume — the original session's
            # sandbox policy persists.
            #
            # Minimization flags (--ignore-user-config, --ignore-rules)
            # are documented to keep auth ($CODEX_HOME) intact while
            # skipping config.toml (mcp_servers, features, model_providers
            # all skipped) and execpolicy .rules. Memory inheritance via
            # ~/.codex/AGENTS.md is handled by scripts/setup-host.sh.
            argv = [
                self.executable,
                "exec",
                "resume",
                "--json",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                resume_session_id,
                prompt,
            ]
        else:
            # First turn: pin sandbox to read-only and confine the
            # agent's working dir to the per-turn scratch.
            argv = [
                self.executable,
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "-s",
                "read-only",
                "-C",
                str(scratch),
                prompt,
            ]

        # Inherit env unchanged. PLAN decision #16 originally redirected
        # CODEX_HOME per-conversation for state isolation, on the
        # assumption that OAuth would still resolve from user-global state.
        # In vivo (verified 2026-04-25 alongside the analogous claude
        # finding) that assumption fails: codex looks for OAuth under
        # $CODEX_HOME, so redirecting strips auth and the CLI returns
        # 401 Unauthorized. Concurrent-turn race on ~/.codex/ is mitigated
        # by codex's server-assigned thread_id keying — `exec resume
        # <thread_id>` finds the right thread regardless of which other
        # conversations have written to the same store.
        child_env = os.environ.copy()

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(scratch),
                env=child_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert proc.stdout is not None
            async for delta in parse_stream(_stream_lines(proc.stdout)):
                yield delta
        finally:
            # Mirror claude.py teardown discipline: SIGTERM → 3s →
            # SIGKILL. The router's outer try/finally calls aclose() on
            # this generator on cancellation/timeout (proven by step
            # 2.3); this finally is what fires when that happens.
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            shutil.rmtree(scratch, ignore_errors=True)
