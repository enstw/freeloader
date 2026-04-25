# Step 4.4: thresholds come from freeloader.toml.
#
# ROADMAP § Phase 4: "Thresholds and weights come from
# `freeloader.toml`." (Weights deferred — strategy has none today.)
#
# What this test guards:
#   - Operator-facing knob actually reaches the Router: a value
#     written in freeloader.toml lands in
#     Router._inference_window_seconds /
#     Router._inference_tokens_threshold.
#   - Resolution order: FREELOADER_CONFIG env > $cwd > <data_dir>.
#   - Fail-loud on bad input: a malformed toml or a FREELOADER_CONFIG
#     pointing at a missing path raises RouterConfigError. Silent
#     fallback to defaults would be exactly the bug-class config
#     files are supposed to prevent.
#   - Backward compat: when no toml exists anywhere, defaults match
#     the placeholder constants from 4.2a so behavior is unchanged.
#   - Integration: create_app() wires load_router_config() into the
#     default Router so a running server picks up the toml.
from __future__ import annotations

import os
from pathlib import Path

import pytest

from freeloader.config import RouterConfigError, load_router_config
from freeloader.frontend.app import create_app
from freeloader.router import Router


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Strip every env var that load_router_config() consults and pin
    cwd + data_dir to empty tmp dirs. Without this, a stray
    FREELOADER_CONFIG in the developer's shell or a real
    ~/.local/share/freeloader/freeloader.toml would silently steer
    the test."""
    monkeypatch.delenv("FREELOADER_CONFIG", raising=False)
    monkeypatch.delenv("FREELOADER_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("FREELOADER_DATA_DIR", str(data))
    return {"cwd": cwd, "data": data}


# ---------------- defaults ----------------


def test_defaults_when_no_toml_anywhere(isolated_env):
    # Backward-compat baseline: with no config in env / cwd / data,
    # the loader returns the same numbers the 4.2a placeholders had.
    cfg = load_router_config()
    assert cfg == {
        "inference_window_seconds": 300,
        "inference_tokens_threshold": 1_000_000,
    }


# ---------------- toml override ----------------


def test_toml_in_cwd_overrides_defaults(isolated_env):
    # Drop a freeloader.toml in cwd → both knobs reflect it.
    (isolated_env["cwd"] / "freeloader.toml").write_text(
        "[router]\ninference_window_seconds = 60\ninference_tokens_threshold = 250000\n"
    )
    cfg = load_router_config()
    assert cfg["inference_window_seconds"] == 60
    assert cfg["inference_tokens_threshold"] == 250000


def test_toml_in_data_dir_overrides_defaults(isolated_env):
    # User-global config (data_dir) is consulted when cwd has none.
    (isolated_env["data"] / "freeloader.toml").write_text(
        "[router]\n"
        "inference_window_seconds = 120\n"
        "inference_tokens_threshold = 500000\n"
    )
    cfg = load_router_config()
    assert cfg["inference_window_seconds"] == 120
    assert cfg["inference_tokens_threshold"] == 500000


def test_partial_toml_falls_back_to_default_for_missing_key(isolated_env):
    # An operator who only cares about one knob should not have to
    # restate the default for the other. Only `inference_window_seconds`
    # is set; `inference_tokens_threshold` falls back to its default.
    (isolated_env["cwd"] / "freeloader.toml").write_text(
        "[router]\ninference_window_seconds = 90\n"
    )
    cfg = load_router_config()
    assert cfg["inference_window_seconds"] == 90
    assert cfg["inference_tokens_threshold"] == 1_000_000  # default


def test_empty_router_section_yields_defaults(isolated_env):
    # `[router]` present but with no keys → defaults for both knobs.
    # Catches a regression where presence of the section short-
    # circuited the default fallback.
    (isolated_env["cwd"] / "freeloader.toml").write_text("[router]\n")
    cfg = load_router_config()
    assert cfg["inference_window_seconds"] == 300
    assert cfg["inference_tokens_threshold"] == 1_000_000


def test_toml_with_no_router_section_yields_defaults(isolated_env):
    # File exists, parses, but has no `[router]` table → defaults.
    # (This shape leaves room for unrelated future config sections
    # without breaking the loader.)
    (isolated_env["cwd"] / "freeloader.toml").write_text("[other]\nirrelevant = true\n")
    cfg = load_router_config()
    assert cfg == {
        "inference_window_seconds": 300,
        "inference_tokens_threshold": 1_000_000,
    }


# ---------------- resolution order ----------------


def test_freeloader_config_env_wins_over_cwd_and_data_dir(isolated_env, tmp_path):
    # All three sources have a toml; FREELOADER_CONFIG must win.
    (isolated_env["cwd"] / "freeloader.toml").write_text(
        "[router]\ninference_window_seconds = 100\n"
    )
    (isolated_env["data"] / "freeloader.toml").write_text(
        "[router]\ninference_window_seconds = 200\n"
    )
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[router]\ninference_window_seconds = 999\n")
    os.environ["FREELOADER_CONFIG"] = str(explicit)
    try:
        cfg = load_router_config()
    finally:
        del os.environ["FREELOADER_CONFIG"]
    assert cfg["inference_window_seconds"] == 999


def test_cwd_wins_over_data_dir(isolated_env):
    # Project-local beats user-global. (A repo committing a
    # freeloader.toml should override whatever the developer has
    # installed globally.)
    (isolated_env["cwd"] / "freeloader.toml").write_text(
        "[router]\ninference_window_seconds = 11\n"
    )
    (isolated_env["data"] / "freeloader.toml").write_text(
        "[router]\ninference_window_seconds = 22\n"
    )
    cfg = load_router_config()
    assert cfg["inference_window_seconds"] == 11


# ---------------- fail-loud ----------------


def test_freeloader_config_pointing_at_missing_path_raises(isolated_env, tmp_path):
    # Setting the env var is an explicit operator action; a typo
    # silently falling back to defaults would be a footgun. Loud
    # failure forces the operator to fix the path.
    missing = tmp_path / "does_not_exist.toml"
    os.environ["FREELOADER_CONFIG"] = str(missing)
    try:
        with pytest.raises(RouterConfigError, match="does not exist"):
            load_router_config()
    finally:
        del os.environ["FREELOADER_CONFIG"]


def test_malformed_toml_raises_with_path(isolated_env):
    # A typo in the toml has the same blast radius as a missing
    # config: silently using defaults would mean the operator's
    # threshold tweak just doesn't take effect. Raise loudly with
    # the path so they can grep for it.
    bad = isolated_env["cwd"] / "freeloader.toml"
    bad.write_text("[router\nthis is not valid toml")
    with pytest.raises(RouterConfigError, match="not valid TOML"):
        load_router_config()


def test_router_section_not_a_table_raises(isolated_env):
    # `router = "string"` would make `cfg.get(...)` blow up at use
    # site with a confusing AttributeError. Catch it at load time
    # with a message that points at freeloader.toml.
    bad = isolated_env["cwd"] / "freeloader.toml"
    bad.write_text('router = "this should be a table not a string"\n')
    with pytest.raises(RouterConfigError, match=r"\[router\] must be a table"):
        load_router_config()


# ---------------- integration with Router + create_app ----------------


def test_load_router_config_kwargs_construct_a_router_directly(isolated_env):
    # The contract: load_router_config() returns a dict that splats
    # cleanly into Router(**...). If a future refactor renames the
    # Router kwargs without updating config.py, this test fails.
    (isolated_env["cwd"] / "freeloader.toml").write_text(
        "[router]\ninference_window_seconds = 77\ninference_tokens_threshold = 333333\n"
    )
    cfg = load_router_config()
    r = Router(**cfg)
    assert r._inference_window_seconds == 77
    assert r._inference_tokens_threshold == 333333


def test_create_app_default_branch_loads_toml_into_router(isolated_env, monkeypatch):
    # End-to-end through production wire-up. `r = router or
    # Router(**load_router_config())` is the only place the default
    # Router gets constructed, so:
    #   1. spy on load_router_config to prove create_app() actually
    #      calls it (a regression to `Router()` would silently
    #      bypass the toml),
    #   1. capture the Router instance create_app constructs to
    #      prove the kwargs reach _inference_window_seconds /
    #      _inference_tokens_threshold.
    (isolated_env["cwd"] / "freeloader.toml").write_text(
        "[router]\ninference_window_seconds = 42\ninference_tokens_threshold = 777777\n"
    )

    loader_calls: list[None] = []
    real_loader = load_router_config

    def spy_loader() -> dict:
        loader_calls.append(None)
        return real_loader()

    constructed: list[Router] = []
    real_router = Router

    def spy_router(*args, **kwargs):
        instance = real_router(*args, **kwargs)
        constructed.append(instance)
        return instance

    monkeypatch.setattr("freeloader.frontend.app.load_router_config", spy_loader)
    monkeypatch.setattr("freeloader.frontend.app.Router", spy_router)

    create_app()

    assert loader_calls, "create_app() did not call load_router_config()"
    assert constructed, "create_app() did not construct a Router"
    r = constructed[-1]
    assert r._inference_window_seconds == 42
    assert r._inference_tokens_threshold == 777777


# ---------------- edge: ~ expansion in FREELOADER_CONFIG ----------------


def test_freeloader_config_expands_user_home(isolated_env, tmp_path, monkeypatch):
    # Operators often write ~/freeloader.toml in shell rc files;
    # leaving expansion to the loader avoids surprising "file not
    # found" errors when the path is technically valid.
    monkeypatch.setenv("HOME", str(tmp_path))
    explicit = tmp_path / "freeloader.toml"
    explicit.write_text("[router]\ninference_window_seconds = 31\n")
    os.environ["FREELOADER_CONFIG"] = "~/freeloader.toml"
    try:
        cfg = load_router_config()
    finally:
        del os.environ["FREELOADER_CONFIG"]
    assert cfg["inference_window_seconds"] == 31
    # Sanity: confirm the path actually went through expansion (i.e.
    # that the test isn't passing because of a literal ~ directory).
    assert Path("~/freeloader.toml").expanduser() == explicit
