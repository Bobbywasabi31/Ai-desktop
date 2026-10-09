"""Tests for examples/agui_client.py (the AG-UI SSE demo client).

Run with: python -m pytest
"""

import importlib.util
import io
import json
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CLIENT = REPO / "examples" / "agui_client.py"
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
def client():
    mod = _load("agui_client_example", CLIENT)
    yield mod
    del sys.modules["agui_client_example"]


def _frame(event: dict) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


# -- SSE frame parsing ----------------------------------------------------


def test_iter_sse_events_multiple_frames(client):
    stream = io.BytesIO(
        _frame({"type": "RUN_STARTED"}) + _frame({"type": "RUN_FINISHED"})
    )
    assert [e["type"] for e in client.iter_sse_events(stream)] == [
        "RUN_STARTED",
        "RUN_FINISHED",
    ]


def test_iter_sse_events_multiline_data_joined(client):
    payload = b'data: {"type": "A",\ndata: "b": 1}\n\n'
    events = list(client.iter_sse_events(io.BytesIO(payload)))
    assert events == [{"type": "A", "b": 1}]


def test_iter_sse_events_ignores_comments_and_unknown_fields(client):
    stream = io.BytesIO(
        b": keepalive comment\n"
        + b"event: whatever\n"
        + b"id: 7\n"
        + _frame({"type": "RUN_STARTED"})
    )
    assert [e["type"] for e in client.iter_sse_events(stream)] == ["RUN_STARTED"]


def test_iter_sse_events_skips_malformed_frame_but_continues(client):
    stream = io.BytesIO(b"data: not json at all\n\n" + _frame({"type": "RUN_FINISHED"}))
    assert [e["type"] for e in client.iter_sse_events(stream)] == ["RUN_FINISHED"]


def test_iter_sse_events_final_frame_without_blank_line(client):
    stream = io.BytesIO(b"data: " + json.dumps({"type": "RUN_ERROR"}).encode())
    assert [e["type"] for e in client.iter_sse_events(stream)] == ["RUN_ERROR"]


def test_iter_sse_events_empty_stream(client):
    assert list(client.iter_sse_events(io.BytesIO(b""))) == []


# -- event rendering ------------------------------------------------------


def test_render_core_event_types(client):
    cases = {
        "RUN_STARTED": (
            {"type": "RUN_STARTED", "threadId": "t", "runId": "r"},
            ("run started threadId=t runId=r", False),
        ),
        "RUN_FINISHED": (
            {"type": "RUN_FINISHED", "result": "ok"},
            ("run finished: ok", False),
        ),
        "RUN_ERROR": (
            {"type": "RUN_ERROR", "message": "boom"},
            ("run error: boom", False),
        ),
        "STEP_STARTED": (
            {"type": "STEP_STARTED", "stepName": "step-1"},
            ("step started: step-1", False),
        ),
        "STEP_FINISHED": (
            {"type": "STEP_FINISHED", "stepName": "step-1"},
            ("step finished: step-1", False),
        ),
        "TEXT_MESSAGE_START": (
            {"type": "TEXT_MESSAGE_START", "messageId": "m1"},
            ("assistant (m1): ", True),
        ),
        "TEXT_MESSAGE_CONTENT": (
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"},
            ("hi", True),
        ),
        "TEXT_MESSAGE_END": ({"type": "TEXT_MESSAGE_END"}, ("", False)),
        "TOOL_CALL_START": (
            {"type": "TOOL_CALL_START", "toolCallName": "list", "toolCallId": "t1"},
            ("tool list (t1)", False),
        ),
        "TOOL_CALL_ARGS": (
            {"type": "TOOL_CALL_ARGS", "delta": '{"path": "."}'},
            ('  args: {"path": "."}', False),
        ),
        "TOOL_CALL_END": ({"type": "TOOL_CALL_END"}, ("", False)),
        "TOOL_CALL_RESULT": (
            {"type": "TOOL_CALL_RESULT", "content": "a.txt"},
            ("  -> result: a.txt", False),
        ),
        "STATE_SNAPSHOT": (
            {"type": "STATE_SNAPSHOT", "snapshot": {"cwd": "/x", "jobs": []}},
            ("state snapshot: cwd,jobs", False),
        ),
        "STATE_DELTA": (
            {
                "type": "STATE_DELTA",
                "delta": [{"op": "replace", "path": "/jobs", "value": []}],
            },
            ("state delta: replace /jobs", False),
        ),
        "MESSAGES_SNAPSHOT": (
            {"type": "MESSAGES_SNAPSHOT", "messages": [{}, {}]},
            ("messages snapshot: 2", False),
        ),
        "CUSTOM": (
            {"type": "CUSTOM", "name": "progress", "value": 0.5},
            ("custom progress: 0.5", False),
        ),
    }
    for name, (event, expected) in cases.items():
        assert client._render(event) == expected, name


def test_render_1_0_additions_do_not_crash(client):
    events = [
        {"type": "TEXT_MESSAGE_CHUNK", "delta": "frag"},
        {"type": "TOOL_CALL_CHUNK", "delta": "argfrag"},
        {"type": "REASONING_START"},
        {"type": "REASONING_MESSAGE_CONTENT", "delta": "thinking"},
        {"type": "REASONING_MESSAGE_END"},
        {"type": "REASONING_END"},
        {"type": "REASONING_ENCRYPTED_VALUE", "subtype": "message"},
        {"type": "ACTIVITY_SNAPSHOT", "activityType": "download"},
        {"type": "ACTIVITY_DELTA", "delta": [{"op": "add"}]},
        {"type": "SUBAGENT_STARTED", "subagentRunId": "s1"},
        {"type": "SUBAGENT_FINISHED", "result": "sub done"},
        {"type": "SUBAGENT_ERROR", "message": "sub failed"},
        {"type": "RAW", "event": {"whatever": True}},
    ]
    for event in events:
        text, inline = client._render(event)
        assert isinstance(text, str) and isinstance(inline, bool), event["type"]


def test_render_unknown_type_falls_back_to_json_preview(client):
    text, inline = client._render({"type": "FUTURE_EVENT", "x": 1})
    assert "FUTURE_EVENT" in text
    assert inline is False


# -- main() -----------------------------------------------------------------


def _fake_urlopen(monkeypatch, client, body: bytes):
    def fake(url, timeout=None):
        return io.BytesIO(body)

    monkeypatch.setattr(client.urllib.request, "urlopen", fake)


def test_main_prints_events_and_ends_cleanly(client, monkeypatch, capsys):
    body = b"".join(
        [
            _frame({"type": "RUN_STARTED", "threadId": "t", "runId": "r"}),
            _frame({"type": "TEXT_MESSAGE_START", "messageId": "m1"}),
            _frame({"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"}),
            _frame({"type": "TEXT_MESSAGE_END"}),
            _frame({"type": "RUN_FINISHED", "result": "done"}),
        ]
    )
    _fake_urlopen(monkeypatch, client, body)
    assert client.main(["my task"]) == 0
    out = capsys.readouterr().out
    assert "RUN_STARTED" in out and "run started" in out
    assert "hello" in out  # streamed inline, no type tag line
    assert "RUN_FINISHED" in out and "done" in out
    assert "stream ended" in out


def test_main_stops_at_terminal_event(client, monkeypatch, capsys):
    body = b"".join(
        [
            _frame({"type": "RUN_ERROR", "message": "bad"}),
            _frame({"type": "CUSTOM", "name": "late", "value": 1}),
        ]
    )
    _fake_urlopen(monkeypatch, client, body)
    assert client.main(["t"]) == 0
    out = capsys.readouterr().out
    assert "run error: bad" in out
    assert "late" not in out  # never read past RUN_ERROR


def test_main_unreachable_server_returns_1(client, monkeypatch, capsys):
    def fake(url, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(client.urllib.request, "urlopen", fake)
    assert client.main(["t", "--base-url", "http://127.0.0.1:9"]) == 1
    err = capsys.readouterr().err
    assert "could not reach" in err


def test_main_midstream_disconnect_returns_1(client, monkeypatch, capsys):
    class _Flaky(io.BytesIO):
        def __init__(self, body, fail_after):
            super().__init__(body)
            self._fail_after = fail_after
            self._reads = 0

        def readline(self, *a):
            self._reads += 1
            if self._reads > self._fail_after:
                raise ConnectionResetError("peer reset")
            return super().readline(*a)

    body = _frame({"type": "RUN_STARTED"}) + _frame({"type": "STEP_STARTED"})
    monkeypatch.setattr(
        client.urllib.request,
        "urlopen",
        lambda url, timeout=None: _Flaky(body, fail_after=2),
    )
    assert client.main(["t"]) == 1
    err = capsys.readouterr().err
    assert "connection lost" in err


# -- live server roundtrip ----------------------------------------------------


def test_live_server_roundtrip(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(REPO))
    monkeypatch.syspath_prepend(str(REPO / "examples"))
    server = _load("agui_server_example", SERVER)
    client = _load("agui_client_example", CLIENT)
    try:

        def fake_run_agent(task, stream=None, **kw):
            stream.run_started(task=task)
            stream.text_message("hello from the server")
            tc = stream.tool_call_start("list")
            stream.tool_call_args(tc, {"path": "."})
            stream.tool_call_end(tc)
            stream.tool_call_result(tc, "a.txt")
            stream.run_finished(result="all done")

        monkeypatch.setattr(server, "run_agent", fake_run_agent)
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            rc = client.main(
                ["list the files", "--base-url", f"http://127.0.0.1:{port}"]
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
    finally:
        del sys.modules["agui_server_example"]
        del sys.modules["agui_client_example"]

    out = capsys.readouterr().out
    assert rc == 0
    assert "hello from the server" in out
    assert "TOOL_CALL_START" in out and "tool list" in out
    assert "a.txt" in out
    assert "run finished: all done" in out
    assert "stream ended" in out


def test_no_third_party_deps():
    source = CLIENT.read_text()
    assert "import sseclient" not in source
    assert "import httpx" not in source
    assert "import requests" not in source
