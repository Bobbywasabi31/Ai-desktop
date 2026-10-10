"""Version consistency: aidesktop.__version__ must match pyproject.toml.

Task-2 fixed a __version__ drift (0.2.0 vs 0.3.0); this guard makes a future
drift a test failure instead of a silent mismatch. Parses pyproject.toml with
a plain regex so the test also runs on Python 3.10 (no tomllib there).

Run with: python -m pytest
"""

import re
from pathlib import Path

import aidesktop

REPO = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text)
    assert match is not None, "no version = found in pyproject.toml"
    return match.group(1)


def test_version_matches_pyproject():
    assert aidesktop.__version__ == _pyproject_version(), (
        f"aidesktop.__version__ ({aidesktop.__version__}) != "
        f"pyproject.toml version ({_pyproject_version()})"
    )


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", aidesktop.__version__), (
        f"__version__ {aidesktop.__version__!r} is not semver X.Y.Z"
    )
