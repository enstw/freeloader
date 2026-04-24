# Data-dir resolution. PLAN decision #10 (freeloader.toml + env vars)
# lands fully at phase 2; 1.5 only needs the data_dir lookup.
from __future__ import annotations

import os
from pathlib import Path


def resolve_data_dir() -> Path:
    """Pick the directory for scratch/, events.jsonl, conversations/.

    Order: FREELOADER_DATA_DIR → $XDG_DATA_HOME/freeloader →
    ~/.local/share/freeloader.
    """
    env = os.environ.get("FREELOADER_DATA_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "freeloader"
    return Path.home() / ".local" / "share" / "freeloader"
