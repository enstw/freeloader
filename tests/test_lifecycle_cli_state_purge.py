# Decision #16: on FreelOAder restart, the per-conversation cli-state
# tree is torn down. Verifies the contract precisely: cli-state goes,
# siblings stay.
from __future__ import annotations

from pathlib import Path

from freeloader.lifecycle import purge_cli_state


def _populate(data_dir: Path) -> None:
    cli_state = data_dir / "cli-state"
    (cli_state / "conv-a" / "claude").mkdir(parents=True)
    (cli_state / "conv-a" / "claude" / "config.json").write_text("{}")
    (cli_state / "conv-b" / "codex").mkdir(parents=True)
    (cli_state / "conv-b" / "codex" / "session.bin").write_text("blob")

    # Siblings that must survive the purge.
    (data_dir / "scratch").mkdir()
    (data_dir / "scratch" / "leftover").write_text("not deleted")
    (data_dir / "events.jsonl").write_text('{"kind":"turn_done"}\n')
    (data_dir / "conversations").mkdir()
    (data_dir / "conversations" / "conv-a.json").write_text("[]")


def test_purge_removes_cli_state_only(tmp_path):
    _populate(tmp_path)
    assert (tmp_path / "cli-state").is_dir()

    purge_cli_state(tmp_path)

    assert not (tmp_path / "cli-state").exists()
    # Siblings untouched.
    assert (tmp_path / "scratch" / "leftover").read_text() == "not deleted"
    assert (tmp_path / "events.jsonl").read_text() == '{"kind":"turn_done"}\n'
    assert (tmp_path / "conversations" / "conv-a.json").read_text() == "[]"


def test_purge_is_idempotent_when_cli_state_missing(tmp_path):
    # No cli-state dir at all — must not raise.
    assert not (tmp_path / "cli-state").exists()
    purge_cli_state(tmp_path)  # no-op
    assert not (tmp_path / "cli-state").exists()


def test_purge_handles_empty_cli_state(tmp_path):
    (tmp_path / "cli-state").mkdir()
    purge_cli_state(tmp_path)
    assert not (tmp_path / "cli-state").exists()
