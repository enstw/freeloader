"""Test-suite setup. Runs before any test module imports.

We default `FREELOADER_SKIP_HOST_CHECKS=1` so that `create_app()` does not
emit the memory-inheritance warning during the test run. The dev machine
has those memory files pointed at the user's real `~/AGENTS.md` and the
warning would fire dozens of times across the suite, drowning out real
output. Tests that specifically exercise the warning path clear this env
var locally via `monkeypatch.delenv`.
"""

from __future__ import annotations

import os

os.environ.setdefault("FREELOADER_SKIP_HOST_CHECKS", "1")
