"""Tests for examples/agui_server.py (the AG-UI SSE demo server).

Only the argparse CLI is tested here — the live SSE roundtrip against the
server's Handler lives in tests/test_agui_client_example.py.

Run with: python -m pytest
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "examples" / "agui_server.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[name]
        raise
    return mod


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO))
    monkeypatch.syspath_prepend(str(REPO / "examples"))
    mod = _load("agui_server_example", SERVER)
    yield mod
    del sys.modules["agui_server_example"]


def test_defaults(server):
    args = server.parse_args([])
    assert args.port == 8765
    assert args.host == "127.0.0.1"


def test_long_port(server):
    args = server.parse_args(["--port", "9000"])
    assert args.port == 9000


def test_positional_port_kept_for_legacy_invocations(server):
    # the old `python agui_server.py 9000` form keeps working
    args = server.parse_args(["9000"])
    assert args.port == 9000


def test_long_port_wins_over_positional(server):
    args = server.parse_args(["--port", "9001", "9000"])
    assert args.port == 9001


def test_host(server):
    args = server.parse_args(["--host", "0.0.0.0"])
    assert args.host == "0.0.0.0"


@pytest.mark.parametrize(
    "argv", [["--port", "abc"], ["--port", "0"], ["--port", "65536"], ["abc"], ["-1"]]
)
def test_bad_port_exits_2(server, argv):
    with pytest.raises(SystemExit) as exc:
        server.parse_args(argv)
    assert exc.value.code == 2


def test_help_exits_0(server):
    with pytest.raises(SystemExit) as exc:
        server.parse_args(["--help"])
    assert exc.value.code == 0


def test_unknown_flag_exits_2(server):
    with pytest.raises(SystemExit) as exc:
        server.parse_args(["--bogus"])
    assert exc.value.code == 2
