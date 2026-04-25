# Data-dir resolution + freeloader.toml loading. PLAN decision #10.
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

# Step 4.4 defaults. These match the placeholders that previously
# lived in router.py (4.2a). Round numbers, intentionally not tuned
# to any vendor's published limits — operators override via toml,
# tests inject their own.
_DEFAULT_INFERENCE_WINDOW_SECONDS: int = 300
_DEFAULT_INFERENCE_TOKENS_THRESHOLD: int = 1_000_000


class RouterConfigError(Exception):
    """Raised when freeloader.toml is malformed, unreadable, or an
    explicit FREELOADER_CONFIG path doesn't exist. Always carries
    the source path so the operator can grep their filesystem."""


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


def _resolve_config_path() -> Path | None:
    """Locate freeloader.toml. Returns None if no config exists.

    Order:
      1. FREELOADER_CONFIG env var — explicit; missing path RAISES.
      1. ./freeloader.toml in the current working directory.
      1. <data_dir>/freeloader.toml (user-global).

    Returns the first existing path. Returns None if neither cwd nor
    data_dir has one (and FREELOADER_CONFIG is unset). The env-var
    branch is the only one that fails loud on a missing file —
    setting the variable is an explicit operator action and a typo
    silently falling back to defaults would be a footgun.
    """
    env = os.environ.get("FREELOADER_CONFIG")
    if env:
        path = Path(env).expanduser()
        if not path.exists():
            raise RouterConfigError(
                f"FREELOADER_CONFIG points at {path} but the file does not exist"
            )
        return path

    cwd_config = Path.cwd() / "freeloader.toml"
    if cwd_config.exists():
        return cwd_config

    data_dir_config = resolve_data_dir() / "freeloader.toml"
    if data_dir_config.exists():
        return data_dir_config

    return None


def load_router_config() -> dict[str, Any]:
    """Return a kwargs dict for Router(...) — currently the two
    inference thresholds. Caller does Router(**load_router_config()).

    A missing config file is fine (returns defaults). A present but
    malformed config file raises RouterConfigError; defaults silently
    masking a typo is exactly the bug-class config files are
    supposed to prevent.
    """
    config_path = _resolve_config_path()

    cfg: dict[str, Any] = {}
    if config_path is not None:
        try:
            with config_path.open("rb") as f:
                parsed = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise RouterConfigError(
                f"freeloader.toml at {config_path} is not valid TOML: {e}"
            ) from e
        cfg = parsed.get("router", {}) or {}
        if not isinstance(cfg, dict):
            raise RouterConfigError(
                f"freeloader.toml at {config_path}: [router] must be a table"
            )

    return {
        "inference_window_seconds": cfg.get(
            "inference_window_seconds", _DEFAULT_INFERENCE_WINDOW_SECONDS
        ),
        "inference_tokens_threshold": cfg.get(
            "inference_tokens_threshold", _DEFAULT_INFERENCE_TOKENS_THRESHOLD
        ),
    }
