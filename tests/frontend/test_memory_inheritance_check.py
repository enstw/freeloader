# Startup check that warns when ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md,
# or ~/.gemini/GEMINI.md is not symlinked to /dev/null. Operator runs
# scripts/setup-host.sh on a dedicated host to nullify these; the check
# surfaces drift (forgot the script, accidentally wrote a real file
# back, etc.) which would otherwise show up only as a higher-than-
# expected token bill.
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from freeloader.frontend.app import _warn_if_memory_inheritance_active


def _make(tmp_path: Path, name: str, kind: str) -> Path:
    """Build a test memory file of the requested shape under tmp_path."""
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if kind == "absent":
        return p
    if kind == "regular":
        p.write_text("you are a helpful assistant…")
        return p
    if kind == "null_link":
        os.symlink("/dev/null", p)
        return p
    if kind == "other_link":
        target = tmp_path / "real_target.md"
        target.write_text("inherited memory")
        os.symlink(str(target), p)
        return p
    raise ValueError(f"unknown kind: {kind}")


def test_no_warning_when_all_paths_nullified(tmp_path, caplog):
    paths = (
        _make(tmp_path, "claude/CLAUDE.md", "null_link"),
        _make(tmp_path, "codex/AGENTS.md", "null_link"),
        _make(tmp_path, "gemini/GEMINI.md", "null_link"),
    )
    with caplog.at_level(logging.WARNING, logger="freeloader.frontend.app"):
        _warn_if_memory_inheritance_active(paths)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_no_warning_when_paths_absent(tmp_path, caplog):
    paths = (
        _make(tmp_path, "claude/CLAUDE.md", "absent"),
        _make(tmp_path, "codex/AGENTS.md", "absent"),
        _make(tmp_path, "gemini/GEMINI.md", "absent"),
    )
    with caplog.at_level(logging.WARNING, logger="freeloader.frontend.app"):
        _warn_if_memory_inheritance_active(paths)
    # CLI just won't load anything; no operator action needed.
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_warning_for_regular_file(tmp_path, caplog):
    path = _make(tmp_path, "claude/CLAUDE.md", "regular")
    with caplog.at_level(logging.WARNING, logger="freeloader.frontend.app"):
        _warn_if_memory_inheritance_active((path,))
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "ACTIVE" in msg
    assert str(path) in msg
    assert "regular file" in msg
    assert "scripts/setup-host.sh" in msg


def test_warning_for_symlink_to_other_target(tmp_path, caplog):
    path = _make(tmp_path, "gemini/GEMINI.md", "other_link")
    with caplog.at_level(logging.WARNING, logger="freeloader.frontend.app"):
        _warn_if_memory_inheritance_active((path,))
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "ACTIVE" in msg
    assert "real_target.md" in msg
    assert "symlink" in msg


def test_mixed_paths_emit_one_warning_each(tmp_path, caplog):
    paths = (
        _make(tmp_path, "claude/CLAUDE.md", "regular"),     # warn
        _make(tmp_path, "codex/AGENTS.md", "null_link"),    # ok
        _make(tmp_path, "gemini/GEMINI.md", "other_link"),  # warn
    )
    with caplog.at_level(logging.WARNING, logger="freeloader.frontend.app"):
        _warn_if_memory_inheritance_active(paths)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 2
    msgs = " ".join(r.getMessage() for r in warnings)
    assert "CLAUDE.md" in msgs
    assert "GEMINI.md" in msgs
    assert "AGENTS.md" not in msgs


def test_skip_env_var_silences_check_in_create_app(tmp_path, monkeypatch, caplog):
    # The conftest defaults FREELOADER_SKIP_HOST_CHECKS=1 for the whole
    # suite; here we explicitly delenv to verify create_app() WOULD have
    # warned, then re-set to verify it stays silent.
    from freeloader.frontend.app import create_app

    fake_path = _make(tmp_path, "claude/CLAUDE.md", "regular")
    monkeypatch.setattr(
        "freeloader.frontend.app._MEMORY_FILES",
        (fake_path,),
    )

    monkeypatch.setenv("FREELOADER_SKIP_HOST_CHECKS", "1")
    with caplog.at_level(logging.WARNING, logger="freeloader.frontend.app"):
        create_app()
    silent = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert silent == [], "env var must silence the check"

    caplog.clear()
    monkeypatch.delenv("FREELOADER_SKIP_HOST_CHECKS", raising=False)
    with caplog.at_level(logging.WARNING, logger="freeloader.frontend.app"):
        create_app()
    not_silent = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(not_silent) == 1, "env unset must let the check fire"
