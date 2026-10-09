"""Terminal SSE client for the AG-UI demo server.

Pairs with examples/agui_server.py: connects to its `/agent` endpoint and
prints each AG-UI event as it arrives — streamed text inline, tool calls
with args/results, run/step lifecycle, and shared-state patches:

    python examples/agui_server.py              # :8765, in one terminal
    python examples/agui_client.py "list the files in the workspace"

Cleanly handles the normal end of stream (server closes it when the run
finishes), mid-stream disconnects, unreachable servers, and Ctrl-C.

Stdlib only — no third-party SSE client needed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8765"

# The server closes the stream after one of these; stop reading there.
TERMINAL_TYPES = frozenset({"RUN_FINISHED", "RUN_ERROR"})


def iter_sse_events(stream):
    """Yield decoded JSON objects from a Server-Sent Events byte stream.

    ``stream`` is any file-like object with ``readline()`` (a
    ``urllib`` response, a socket file, or ``io.BytesIO`` in tests).
    Accumulates ``data:`` lines into frames, joins multi-line payloads,
    skips comments and unknown fields, and skips malformed frames instead
    of dying. A frame with no trailing blank line is still yielded at EOF.
    """
    data_lines: list[str] = []
    while True:
        raw = stream.readline()
        if raw == b"":
            break  # EOF — server closed the stream cleanly
        line = raw.decode("utf-8", errors="replace")
        if line in ("\n", "\r\n", "\r"):
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue  # skip the bad frame, keep listening
            continue
        if line.startswith(":"):
            continue  # comment / keepalive
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value.strip())
        # event:, id:, retry: — not used by this binding, ignored
    if data_lines:
        with contextlib.suppress(json.JSONDecodeError):
            yield json.loads("\n".join(data_lines))


def _short(value, limit: int = 160) -> str:
    """One-line preview of an arbitrary JSON value."""
    text = value if isinstance(value, str) else json.dumps(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render(event: dict) -> tuple[str, bool]:
    """Turn one AG-UI event into (line, inline).

    ``inline`` events are printed with no newline so streamed text and
    reasoning deltas assemble on one line. An empty line means "no output"
    (e.g. message/tool end markers, whose job is just to close the line).
    Unknown event types fall back to a compact JSON preview so new AG-UI
    versions never crash the client.
    """
    etype = event.get("type", "<no type>")
    if etype == "RUN_STARTED":
        return (
            f"run started threadId={event.get('threadId')} runId={event.get('runId')}",
            False,
        )
    if etype == "RUN_FINISHED":
        return f"run finished: {_short(event.get('result', ''))}", False
    if etype == "RUN_ERROR":
        return f"run error: {_short(event.get('message', ''))}", False
    if etype == "STEP_STARTED":
        return f"step started: {event.get('stepName')}", False
    if etype == "STEP_FINISHED":
        return f"step finished: {event.get('stepName')}", False
    if etype == "TEXT_MESSAGE_START":
        return f"assistant ({event.get('messageId')}): ", True
    if etype in ("TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CHUNK"):
        return str(event.get("delta", "")), True
    if etype == "TEXT_MESSAGE_END":
        return "", False  # closes the streamed line
    if etype == "TOOL_CALL_START":
        return (
            f"tool {event.get('toolCallName')} ({event.get('toolCallId')})",
            False,
        )
    if etype == "TOOL_CALL_ARGS":
        return f"  args: {_short(event.get('delta', ''))}", False
    if etype == "TOOL_CALL_CHUNK":
        return str(event.get("delta", "")), True
    if etype == "TOOL_CALL_END":
        return "", False
    if etype == "TOOL_CALL_RESULT":
        return f"  -> result: {_short(event.get('content', ''))}", False
    if etype == "STATE_SNAPSHOT":
        keys = (
            ",".join(sorted(event.get("snapshot", {}).keys()))
            if isinstance(event.get("snapshot"), dict)
            else ""
        )
        return f"state snapshot: {keys}", False
    if etype == "STATE_DELTA":
        ops = event.get("delta", [])
        desc = (
            ", ".join(f"{op.get('op')} {op.get('path')}" for op in ops)
            if isinstance(ops, list)
            else _short(ops)
        )
        return f"state delta: {desc}", False
    if etype == "MESSAGES_SNAPSHOT":
        msgs = event.get("messages", [])
        return (
            f"messages snapshot: {len(msgs) if isinstance(msgs, list) else '?'}",
            False,
        )
    if etype == "REASONING_START":
        return "reasoning:", True
    if etype in ("REASONING_MESSAGE_CONTENT", "REASONING_MESSAGE_CHUNK"):
        return str(event.get("delta", "")), True
    if etype in ("REASONING_END", "REASONING_MESSAGE_END"):
        return "", False
    if etype == "REASONING_ENCRYPTED_VALUE":
        return "[encrypted reasoning — opaque]", False
    if etype == "ACTIVITY_SNAPSHOT":
        return f"activity: {_short(event.get('activityType', ''))}", False
    if etype == "ACTIVITY_DELTA":
        return f"activity delta: {_short(event.get('delta', ''))}", False
    if etype == "SUBAGENT_STARTED":
        return f"subagent started: {event.get('subagentRunId')}", False
    if etype == "SUBAGENT_FINISHED":
        return f"subagent finished: {_short(event.get('result', ''))}", False
    if etype == "SUBAGENT_ERROR":
        return f"subagent error: {_short(event.get('message', ''))}", False
    if etype == "CUSTOM":
        return f"custom {event.get('name')}: {_short(event.get('value', ''))}", False
    if etype == "RAW":
        return "[raw provider event]", False
    return _short(event), False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Terminal SSE client for the ai-desktop AG-UI demo server "
        "(examples/agui_server.py). Prints each event as it arrives."
    )
    parser.add_argument(
        "task",
        nargs="?",
        default="list the files in the workspace",
        help="task to run on the server",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"demo server base URL (default {DEFAULT_BASE_URL})",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    url = (
        f"{args.base_url.rstrip('/')}/agent"
        f"?task={urllib.parse.quote(args.task, safe='')}"
    )
    print(f"connecting to {url} ...", flush=True)
    try:
        resp = urllib.request.urlopen(url, timeout=args.timeout)
    except urllib.error.HTTPError as e:
        print(f"error: server returned HTTP {e.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        print(f"error: could not reach {args.base_url}: {reason}", file=sys.stderr)
        return 1

    count = 0
    inline_open = False
    try:
        for event in iter_sse_events(resp):
            count += 1
            etype = event.get("type", "<no type>")
            text, inline = _render(event)
            if inline:
                print(text, end="", flush=True)
                inline_open = True
            else:
                if inline_open:
                    print()  # close the streamed line
                    inline_open = False
                if text:
                    print(f"{etype:>24}  {text}", flush=True)
            if etype in TERMINAL_TYPES:
                break
    except (ConnectionResetError, BrokenPipeError):
        if inline_open:
            print()
        print(
            f"error: connection lost after {count} events — "
            "the server may have crashed",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        if inline_open:
            print()
        print("interrupted")
        return 130
    finally:
        resp.close()

    if inline_open:
        print()
    print(f"stream ended — {count} event(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
