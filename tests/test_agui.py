"""Tests for the AG-UI producer layer (aidesktop.agui)."""

import json

import pytest

from aidesktop import Desktop
from aidesktop.agui import (
    AGUIStream,
    decode_sse,
    encode_sse,
    json_patch_add,
    json_patch_remove,
    json_patch_replace,
)


def test_sse_framing_round_trip():
    event = {"type": "RUN_STARTED", "threadId": "t1", "runId": "r1"}
    chunk = encode_sse(event)
    assert chunk.startswith("data: ")
    assert chunk.endswith("\n\n")
    assert decode_sse(chunk) == event


def test_event_field_names_match_spec():
    s = AGUIStream()
    s.run_started(task="hello")
    s.state_snapshot({"workdir": "/tmp"})
    s.state_delta(json_patch_replace("/jobs", []))
    s.text_message("hi")
    s.tool_call("list", {"path": "."}, result="[]")
    s.custom("progress", {"pct": 50})
    s.run_finished(result="done")
    types = [e["type"] for e in s.events]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"

    by_type = {e["type"]: e for e in s.events}
    assert by_type["RUN_STARTED"]["threadId"] == s.thread_id
    assert by_type["RUN_STARTED"]["runId"] == s.run_id
    # AG-UI 1.0: RunAgentInput has no "task" field, so RUN_STARTED carries
    # no "input" at all (the task string stays Python-side only).
    assert "input" not in by_type["RUN_STARTED"]
    assert by_type["STATE_SNAPSHOT"]["snapshot"] == {"workdir": "/tmp"}
    assert by_type["STATE_DELTA"]["delta"] == [
        {"op": "replace", "path": "/jobs", "value": []}]
    assert by_type["TEXT_MESSAGE_START"]["messageId"]
    assert by_type["TEXT_MESSAGE_CONTENT"]["delta"] == "hi"
    tc = by_type["TOOL_CALL_START"]
    assert tc["toolCallId"] and tc["toolCallName"] == "list"
    assert json.loads(by_type["TOOL_CALL_ARGS"]["delta"]) == {"path": "."}
    assert by_type["TOOL_CALL_END"]["toolCallId"] == tc["toolCallId"]
    assert by_type["TOOL_CALL_RESULT"]["toolCallId"] == tc["toolCallId"]
    assert by_type["TOOL_CALL_RESULT"]["content"] == "[]"
    assert by_type["CUSTOM"]["name"] == "progress"
    assert by_type["RUN_FINISHED"]["result"] == "done"
    # every event carries the envelope
    for e in s.events:
        assert "type" in e and "timestamp" in e


def test_json_patch_helpers():
    assert json_patch_replace("/a", 1) == {"op": "replace", "path": "/a", "value": 1}
    assert json_patch_add("/b", [1]) == {"op": "add", "path": "/b", "value": [1]}
    assert json_patch_remove("/c") == {"op": "remove", "path": "/c"}


def test_run_wrapper_lifecycle():
    s = AGUIStream()
    result = s.run("task", lambda stream: "answer")
    assert result == "answer"
    assert s.events[0]["type"] == "RUN_STARTED"
    assert s.events[-1]["type"] == "RUN_FINISHED"

    s2 = AGUIStream()
    with pytest.raises(ValueError, match="boom"):
        s2.run("task", lambda stream: (_ for _ in ()).throw(ValueError("boom")))
    assert s2.events[-1]["type"] == "RUN_ERROR"
    assert "boom" in s2.events[-1]["message"]


def test_sink_gets_events_live():
    seen = []
    s = AGUIStream(sink=seen.append)
    s.text_message("ping")
    assert len(seen) == 3  # start, content, end
    assert all(e in s.events for e in seen)


def test_sse_body_is_parseable_stream():
    s = AGUIStream()
    s.run_started()
    s.text_message("hello")
    s.run_finished()
    body = s.sse_body()
    chunks = [c for c in body.split("\n\n") if c.strip()]
    assert len(chunks) == 5
    assert [decode_sse(c)["type"] for c in chunks] == [
        "RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END", "RUN_FINISHED"]


def test_snapshot_desktop(tmp_path):
    desk = Desktop(workdir=str(tmp_path))
    try:
        s = AGUIStream()
        snap = s.snapshot_desktop(desk)
        assert snap["workdir"] == str(tmp_path)
        assert snap["jobs"] == [] and snap["jobCount"] == 0
        job = desk.shell("sleep 30", background=True)
        try:
            snap2 = s.snapshot_desktop(desk)
            assert snap2["jobCount"] == 1
            assert snap2["jobs"][0]["command"] == "sleep 30"
            assert snap2["jobs"][0]["running"] is True
            assert isinstance(snap2["jobs"][0]["pid"], int)
        finally:
            job.kill()
    finally:
        desk.close()


def test_run_agent_emits_agui_events(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, "examples")
    import react_agent
    monkeypatch.chdir(tmp_path)

    stream = AGUIStream()
    result = react_agent.run_agent("list the files in the workspace",
                                   stream=stream)
    assert "demo model" in result
    types = [e["type"] for e in stream.events]
    assert types[0] == "RUN_STARTED"
    assert types[1] == "STATE_SNAPSHOT"
    assert "STEP_STARTED" in types
    assert "TOOL_CALL_START" in types
    assert "TOOL_CALL_ARGS" in types
    assert "TOOL_CALL_END" in types
    assert "TOOL_CALL_RESULT" in types
    assert "STEP_FINISHED" in types
    assert types[-1] == "RUN_FINISHED"
    # tool call trio shares one id
    tc_id = next(e for e in stream.events
                 if e["type"] == "TOOL_CALL_START")["toolCallId"]
    for t in ("TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"):
        ev = next(e for e in stream.events if e["type"] == t)
        assert ev["toolCallId"] == tc_id
    # the whole thing is a valid SSE stream
    body = stream.sse_body()
    assert all(decode_sse(c)["type"] for c in body.split("\n\n") if c.strip())
