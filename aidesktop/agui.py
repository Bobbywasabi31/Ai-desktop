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
TEXT_MESSAGE_CHUNK = "TEXT_MESSAGE_CHUNK"          # 1.0 chunk shorthand
TOOL_CALL_START = "TOOL_CALL_START"
TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
TOOL_CALL_END = "TOOL_CALL_END"
TOOL_CALL_CHUNK = "TOOL_CALL_CHUNK"                # 1.0 chunk shorthand
TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
STATE_SNAPSHOT = "STATE_SNAPSHOT"
STATE_DELTA = "STATE_DELTA"
MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"
CUSTOM = "CUSTOM"
# AG-UI 1.0 additions ---------------------------------------------------
REASONING_START = "REASONING_START"
REASONING_END = "REASONING_END"
REASONING_MESSAGE_START = "REASONING_MESSAGE_START"
REASONING_MESSAGE_CONTENT = "REASONING_MESSAGE_CONTENT"
REASONING_MESSAGE_END = "REASONING_MESSAGE_END"
REASONING_MESSAGE_CHUNK = "REASONING_MESSAGE_CHUNK"  # 1.0 chunk shorthand
REASONING_ENCRYPTED_VALUE = "REASONING_ENCRYPTED_VALUE"
ACTIVITY_SNAPSHOT = "ACTIVITY_SNAPSHOT"
ACTIVITY_DELTA = "ACTIVITY_DELTA"
SUBAGENT_STARTED = "SUBAGENT_STARTED"
SUBAGENT_FINISHED = "SUBAGENT_FINISHED"
SUBAGENT_ERROR = "SUBAGENT_ERROR"
RAW = "RAW"


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
        # 1.0 also allows content to be a list of multimodal content parts
        # (text/image/audio/video/document), so non-str content passes
        # through untouched rather than being JSON-encoded.
        return self.emit({
            "type": TOOL_CALL_RESULT,
            "messageId": f"msg-{uuid.uuid4().hex[:8]}",
            "toolCallId": tool_call_id,
            "content": content,
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

    # -- chunk helpers (AG-UI 1.0) -----------------------------------------
    # Shorthand events a producer can emit instead of start/content/end
    # triples; consumers normalize them back into triples. Every field is
    # optional — include only what you have.

    def text_message_chunk(self, message_id: str | None = None,
                           delta: str | None = None,
                           role: str | None = None,
                           subagent_run_id: str | None = None) -> dict:
        """TEXT_MESSAGE_CHUNK: shorthand for a text message update."""
        event: dict[str, Any] = {"type": TEXT_MESSAGE_CHUNK}
        if message_id:
            event["messageId"] = message_id
        if role:
            event["role"] = role
        if delta is not None:
            event["delta"] = delta
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    def tool_call_chunk(self, tool_call_id: str | None = None,
                        tool_call_name: str | None = None,
                        delta: str | None = None,
                        subagent_run_id: str | None = None) -> dict:
        """TOOL_CALL_CHUNK: shorthand for a tool call's start/args/end."""
        event: dict[str, Any] = {"type": TOOL_CALL_CHUNK}
        if tool_call_id:
            event["toolCallId"] = tool_call_id
        if tool_call_name:
            event["toolCallName"] = tool_call_name
        if delta is not None:
            event["delta"] = delta
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    def reasoning_message_chunk(self, message_id: str | None = None,
                                delta: str | None = None,
                                subagent_run_id: str | None = None) -> dict:
        """REASONING_MESSAGE_CHUNK: shorthand for a reasoning update."""
        event: dict[str, Any] = {"type": REASONING_MESSAGE_CHUNK}
        if message_id:
            event["messageId"] = message_id
        if delta is not None:
            event["delta"] = delta
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    # -- reasoning (AG-UI 1.0) ---------------------------------------------
    # The model's thinking, streamed separately from the user-facing reply.
    # A REASONING_START/END block wraps one or more reasoning messages.

    def reasoning_start(self, subagent_run_id: str | None = None) -> str:
        """Open a reasoning block; returns its id for reasoning_end()."""
        block_id = f"rsn-{uuid.uuid4().hex[:8]}"
        event: dict[str, Any] = {"type": REASONING_START,
                                 "messageId": block_id}
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        self.emit(event)
        return block_id

    def reasoning_end(self, block_id: str,
                      subagent_run_id: str | None = None) -> dict:
        """Close a reasoning block opened by reasoning_start()."""
        event: dict[str, Any] = {"type": REASONING_END,
                                 "messageId": block_id}
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    def reasoning_message_start(
            self, message_id: str | None = None,
            subagent_run_id: str | None = None) -> str:
        """Open a streamed reasoning message (role is always "reasoning")."""
        message_id = message_id or f"rms-{uuid.uuid4().hex[:8]}"
        event: dict[str, Any] = {"type": REASONING_MESSAGE_START,
                                 "messageId": message_id,
                                 "role": "reasoning"}
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        self.emit(event)
        return message_id

    def reasoning_message_content(self, message_id: str, delta: str,
                                  subagent_run_id: str | None = None) -> dict:
        """Append a fragment to a streamed reasoning message."""
        event: dict[str, Any] = {"type": REASONING_MESSAGE_CONTENT,
                                 "messageId": message_id, "delta": delta}
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    def reasoning_message_end(self, message_id: str,
                              subagent_run_id: str | None = None) -> dict:
        """Close a streamed reasoning message."""
        event: dict[str, Any] = {"type": REASONING_MESSAGE_END,
                                 "messageId": message_id}
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    def reasoning_message(self, content: str,
                          subagent_run_id: str | None = None) -> str:
        """Emit a whole reasoning message as start/content/end (one shot)."""
        message_id = self.reasoning_message_start(
            subagent_run_id=subagent_run_id)
        if content:
            self.reasoning_message_content(message_id, content,
                                           subagent_run_id=subagent_run_id)
        self.reasoning_message_end(message_id,
                                   subagent_run_id=subagent_run_id)
        return message_id

    def reasoning_encrypted_value(self, subtype: str, entity_id: str,
                                  encrypted_value: str,
                                  subagent_run_id: str | None = None) -> dict:
        """REASONING_ENCRYPTED_VALUE: a provider's opaque encrypted reasoning
        blob. Consumers store and return it on a later turn without reading
        it. subtype is "message" or "tool-call" (per the 1.0 schema)."""
        if subtype not in ("message", "tool-call"):
            raise ValueError(
                f'subtype must be "message" or "tool-call", got {subtype!r}')
        event: dict[str, Any] = {
            "type": REASONING_ENCRYPTED_VALUE,
            "subtype": subtype,
            "entityId": entity_id,
            "encryptedValue": encrypted_value,
        }
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    # -- activity (AG-UI 1.0) ----------------------------------------------
    # Structured progress that is not conversation content — what the agent
    # is *doing* (planning, searching, waiting), rendered by the UI as its
    # own widget.

    def activity_snapshot(self, message_id: str, activity_type: str,
                          content: dict, replace: bool | None = None,
                          subagent_run_id: str | None = None) -> dict:
        """ACTIVITY_SNAPSHOT: full activity state (replaces, like a snapshot).
        content is an arbitrary object the UI renders for activity_type."""
        event: dict[str, Any] = {"type": ACTIVITY_SNAPSHOT,
                                 "messageId": message_id,
                                 "activityType": activity_type,
                                 "content": content}
        if replace is not None:
            event["replace"] = replace
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    def activity_delta(self, message_id: str, activity_type: str,
                       patch: list[dict] | dict,
                       subagent_run_id: str | None = None) -> dict:
        """ACTIVITY_DELTA: JSON Patch (RFC 6902) ops against the last
        activity snapshot with the same message_id."""
        event: dict[str, Any] = {"type": ACTIVITY_DELTA,
                                 "messageId": message_id,
                                 "activityType": activity_type,
                                 "patch": patch if isinstance(patch, list)
                                 else [patch]}
        if subagent_run_id:
            event["subagentRunId"] = subagent_run_id
        return self.emit(event)

    # -- subagents (AG-UI 1.0) ---------------------------------------------
    # A child agent's lifecycle. Everything the subagent produces is tagged
    # with its subagentRunId (pass subagent_run_id to the other emit
    # methods), so the UI can group the work without replaying the stream.

    def subagent_started(self, name: str, description: str | None = None,
                         subagent_run_id: str | None = None,
                         parent_subagent_run_id: str | None = None,
                         parent_tool_call_id: str | None = None,
                         parent_message_id: str | None = None) -> str:
        """SUBAGENT_STARTED: announce a subagent invocation. Returns its run
        id — pass it back to subagent_finished/subagent_error and as
        subagent_run_id on the events the subagent produces."""
        run_id = subagent_run_id or f"sub-{uuid.uuid4().hex[:8]}"
        event: dict[str, Any] = {"type": SUBAGENT_STARTED,
                                 "subagentRunId": run_id, "name": name}
        if description:
            event["description"] = description
        if parent_subagent_run_id:
            event["parentSubagentRunId"] = parent_subagent_run_id
        if parent_tool_call_id:
            event["parentToolCallId"] = parent_tool_call_id
        if parent_message_id:
            event["parentMessageId"] = parent_message_id
        self.emit(event)
        return run_id

    def subagent_finished(self, subagent_run_id: str, result: Any = None,
                          outcome: dict | None = None) -> dict:
        """SUBAGENT_FINISHED: end a subagent's segment of the run.
        outcome is {"type": "success"} or
        {"type": "suspended", "interruptIds": [...]}; absent means success."""
        event: dict[str, Any] = {"type": SUBAGENT_FINISHED,
                                 "subagentRunId": subagent_run_id}
        if result is not None:
            event["result"] = result
        if outcome is not None:
            event["outcome"] = outcome
        return self.emit(event)

    def subagent_error(self, subagent_run_id: str, message: str,
                       code: str | None = None) -> dict:
        """SUBAGENT_ERROR: a subagent failed. The run may continue — the
        parent is free to handle it, which is why this is not RUN_ERROR."""
        event: dict[str, Any] = {"type": SUBAGENT_ERROR,
                                 "subagentRunId": subagent_run_id,
                                 "message": message}
        if code:
            event["code"] = code
        return self.emit(event)

    # -- escape hatch (AG-UI 1.0) ------------------------------------------

    def raw(self, event: Any, source: str | None = None,
            subagent_run_id: str | None = None) -> dict:
        """RAW: forward a provider event verbatim for consumers that know
        how to read it. `event` is the untouched payload."""
        payload: dict[str, Any] = {"type": RAW, "event": event}
        if source:
            payload["source"] = source
        if subagent_run_id:
            payload["subagentRunId"] = subagent_run_id
        return self.emit(payload)

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
