import freeloader
from freeloader.adapters import claude as _claude
from freeloader.canonical import history_diff as _history_diff
from freeloader.frontend import app as _app


def test_package_importable():
    assert freeloader.__version__ == "0.0.0"


def test_stub_modules_import():
    # Gate 1 checks these files exist; this test checks they're importable
    # (no syntax errors, no premature imports). They stay stubs until 1.2+.
    assert _claude is not None
    assert _history_diff is not None
    assert _app is not None
