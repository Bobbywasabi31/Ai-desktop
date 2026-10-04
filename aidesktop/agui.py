"""AG-UI producer layer: stream what the agent is doing to any UI, live.

AG-UI (Agent-User Interaction protocol, https://ag-ui.com) is an open
standard for agent -> UI communication: one ordered stream of typed JSON
events over SSE. Any AG-UI consumer (CopilotKit, a custom React UI, a CLI
dashboard, a Slack bot) can render the agent's activity without knowing
anything about ai-desktop internals.

This module is dependency-light and stdlib-only: events are plain dicts
with the exact field names from the AG-UI spec (camelCase), encoded with
``encode_sse`` as ``data: {...}\\n\\n`` — the wire format the official
TypeScript/Python SDKs produce.

Typical use:

    from aidesktop.agui import AGUIStream

    stream = AGUIStream()                     # collect events into a list
    stream.run_started(task="list the workspace")
    stream.step_started("planning")
    stream.text_message("I'll start by listing the files.")
    stream.tool_call("list", {"path": "."}, result=[...])
    stream.step_finished("planning")
    stream.run_finished(result="3 files found")

    for chunk in stream.sse_chunks():         # or stream.events directly
        ...

For an actual HTTP server that streams a live agent run, see
``examples/agui_server.py``.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Iterable, Iterator


# -- Event type constants (from the AG-UI spec) -------------------------
RUN_STARTED = "RUN_STARTED"
RUN_FINISHED = "RUN_FINISHED"
RUN_ERROR = "RUN_ERROR"
STEP_STARTED = "STEP_STARTED"
STEP_FINISHED = "STEP_FINISHED"
TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
TOOL_CALL_START = "TOOL_CALL_START"
TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
TOOL_CALL_END = "TOOL_CALL_END"
TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
STATE_SNAPSHOT = "STATE_SNAPSHOT"
STATE_DELTA = "STATE_DELTA"
MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"
CUSTOM = "CUSTOM"


def encode_sse(event: dict) -> str:
    """Encode one event as a Server-Sent Events chunk.

    AG-UI's SSE binding carries the whole event (including its ``type``)
    as JSON in the ``data`` field, with no ``event:`` line.
    """
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def decode_sse(chunk: str) -> dict:
    """Inverse of ``encode_sse`` — handy for tests and debugging."""
    data = chunk.split("data:", 1)[1].strip()
    return json.loads(data)


def json_patch_replace(path: str, value: Any) -> dict:
    """One JSON Patch (RFC 6902) ``replace`` op, for STATE_DELTA events."""
    return {"op": "replace", "path": path, "value": value}


def json_patch_add(path: str, value: Any) -> dict:
    """One JSON Patch (RFC 6902) ``add`` op, for STATE_DELTA events."""
    return {"op": "add", "path": path, "value": value}


def json_patch_remove(path: str) -> dict:
    """One JSON Patch (RFC 6902) ``remove`` op, for STATE_DELTA events."""
    return {"op": "remove", "path": path}


def _now_ms() -> int:
    return int(time.time() * 1000)


class AGUIStream:
    """Collects AG-UI events for one agent run and emits them in order.

    Pass ``sink`` to push events somewhere live (e.g. a queue feeding an
    HTTP response); without it, events accumulate in ``.events`` and can
    be replayed via ``sse_chunks()``.
    """

    def __init__(self, sink: Callable[[dict], None] | None = None,
                 thread_id: str | None = None, run_id: str | None = None):
        self.sink = sink
        self.events: list[dict] = []
        self.thread_id = thread_id or f"thread-{uuid.uuid4().hex[:8]}"
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self._started = False

    # -- plumbing ------------------------------------------------------
    def emit(self, event: dict) -> dict:
        event.setdefault("timestamp", _now_ms())
        self.events.append(event)
        if self.sink:
            self.sink(event)
        return event

    def sse_chunks(self) -> Iterator[str]:
        """All buffered events encoded as SSE chunks, in order."""
        for event in self.events:
            yield encode_sse(event)

    def sse_body(self) -> str:
        return "".join(self.sse_chunks())

    # -- run lifecycle --------------------------------------------------
    def run_started(self, task: str | None = None,
                    state: dict | None = None) -> dict:
        self._started = True
        event: dict[str, Any] = {
            "type": RUN_STARTED,
            "threadId": self.thread_id,
            "runId": self.run_id,
        }
        # AG-UI 1.0: input must be a RunAgentInput (threadId, runId, messages,
        # state, tools...); there is no "task" field. Omit it (optional).
        # The task is kept as a Python-side convenience only.
        return self.emit(event)

    def run_finished(self, result: Any = None) -> dict:
        return self.emit({
            "type": RUN_FINISHED,
            "threadId": self.thread_id,
            "runId": self.run_id,
            "result": result if result is not None else {},
        })

    def run_error(self, message: str, code: str | None = None) -> dict:
        event: dict[str, Any] = {"type": RUN_ERROR, "message": message}
        if code:
            event["code"] = code
        return self.emit(event)

    # -- steps ----------------------------------------------------------
    def step_started(self, step_name: str) -> dict:
        return self.emit({"type": STEP_STARTED, "stepName": step_name})

    def step_finished(self, step_name: str) -> dict:
        return self.emit({"type": STEP_FINISHED, "stepName": step_name})

    # -- streamed text ---------------------------------------------------
    def text_message_start(self, message_id: str | None = None,
                           role: str = "assistant") -> str:
        message_id = message_id or f"msg-{uuid.uuid4().hex[:8]}"
        self.emit({"type": TEXT_MESSAGE_START, "messageId": message_id,
                   "role": role})
        return message_id

    def text_message_content(self, message_id: str, delta: str) -> dict:
        return self.emit({"type": TEXT_MESSAGE_CONTENT,
                          "messageId": message_id, "delta": delta})

    def text_message_end(self, message_id: str) -> dict:
        return self.emit({"type": TEXT_MESSAGE_END, "messageId": message_id})

    def text_message(self, content: str, role: str = "assistant") -> str:
        """Emit a whole text message as start/content/end (one chunk)."""
        message_id = self.text_message_start(role=role)
        if content:
            self.text_message_content(message_id, content)
        self.text_message_end(message_id)
        return message_id

    # -- tool calls ------------------------------------------------------
    def tool_call_start(self, tool_call_name: str,
                        tool_call_id: str | None = None,
                        parent_message_id: str | None = None) -> str:
        tool_call_id = tool_call_id or f"tc-{uuid.uuid4().hex[:8]}"
        event: dict[str, Any] = {"type": TOOL_CALL_START,
                                 "toolCallId": tool_call_id,
                                 "toolCallName": tool_call_name}
        if parent_message_id:
            event["parentMessageId"] = parent_message_id
        self.emit(event)
        return tool_call_id

    def tool_call_args(self, tool_call_id: str, args: Any) -> dict:
        delta = args if isinstance(args, str) else json.dumps(args)
        return self.emit({"type": TOOL_CALL_ARGS,
                          "toolCallId": tool_call_id, "delta": delta})

    def tool_call_end(self, tool_call_id: str) -> dict:
        return self.emit({"type": TOOL_CALL_END, "toolCallId": tool_call_id})

    def tool_call_result(self, tool_call_id: str, content: Any) -> dict:
        # AG-UI 1.0: TOOL_CALL_RESULT has no "role" field; the role is implied
        # by the event type. (Removed "role": "tool" which 1.0 strips.)
        return self.emit({
            "type": TOOL_CALL_RESULT,
            "messageId": f"msg-{uuid.uuid4().hex[:8]}",
            "toolCallId": tool_call_id,
            "content": content if isinstance(content, str) else json.dumps(content),
        })

    def tool_call(self, name: str, args: Any, result: Any = None) -> str:
        """Emit a complete tool call: start/args/end (+ result if given)."""
        tc_id = self.tool_call_start(name)
        self.tool_call_args(tc_id, args)
        self.tool_call_end(tc_id)
        if result is not None:
            self.tool_call_result(tc_id, result)
        return tc_id

    # -- shared state ----------------------------------------------------
    def state_snapshot(self, snapshot: dict) -> dict:
        """Full shared state (what the UI should render right now)."""
        return self.emit({"type": STATE_SNAPSHOT, "snapshot": snapshot})

    def state_delta(self, ops: list[dict] | dict) -> dict:
        """JSON Patch (RFC 6902) ops against the last snapshot."""
        return self.emit({"type": STATE_DELTA,
                          "delta": ops if isinstance(ops, list) else [ops]})

    def custom(self, name: str, value: Any) -> dict:
        return self.emit({"type": CUSTOM, "name": name, "value": value})

    # -- desktop helpers --------------------------------------------------
    def snapshot_desktop(self, desktop) -> dict:
        """Build a shared-state snapshot from a ``Desktop`` instance.

        Gives the UI a live view of what the agent is doing: the working
        directory, background jobs (pid, command, still running), and how
        many files it has touched this run.
        """
        jobs = []
        for job in desktop.jobs():
            jobs.append({
                "pid": job.pid,
                "command": job.command,
                "running": job.running(),
            })
        return {
            "workdir": desktop.workdir,
            "jobs": jobs,
            "jobCount": len(jobs),
        }

    def run(self, task: str, fn: Callable[["AGUIStream"], Any]) -> Any:
        """Convenience wrapper: run_started -> fn(self) -> run_finished.

        Any exception becomes a RUN_ERROR event and is re-raised.
        """
        self.run_started(task=task)
        try:
            result = fn(self)
        except Exception as e:  # noqa: BLE001 — surfaced to the UI
            self.run_error(f"{type(e).__name__}: {e}")
            raise
        self.run_finished(result=result)
        return result
