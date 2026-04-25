# FreelOAder restart hooks. Decision #16 mandates: "On FreelOAder
# restart, all <data_dir>/cli-state/ subdirs are torn down" — matches
# the "no session persistence across FreelOAder restarts" rule
# (PLAN § Things to not do) and removes orphaned CLI threads from
# previous runs.
#
# This module exposes the purge as a plain function. Wiring it into a
# uvicorn/serve entry point lands when one exists; until then, the
# function is callable from a future startup hook or one-off script.
from __future__ import annotations

import shutil
from pathlib import Path


def purge_cli_state(data_dir: Path) -> None:
    """Remove `<data_dir>/cli-state/` and everything under it.

    Idempotent: missing path is a no-op. Sibling directories
    (`scratch/`, `events.jsonl`, `conversations/`) are NOT touched —
    those have different lifecycles.
    """
    target = Path(data_dir) / "cli-state"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
