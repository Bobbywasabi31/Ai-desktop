"""Minimal ReAct-style agent loop built on ai-desktop.

This is the pattern the toolkit is for: the LLM reasons, picks one of the
Desktop's tools, observes the result, and repeats. Plug in any model by
replacing `ask_model` — it just needs to return either plain text or a
tool call shaped like {"tool": ..., "args": {...}}.

Run:  python examples/react_agent.py "list the files in the workspace"
"""

from __future__ import annotations

import json
import sys

from aidesktop import Desktop
from aidesktop.agui import AGUIStream

SYSTEM = """You are an agent with a desktop. Reply with EXACTLY ONE of:
1. A tool call as JSON: {"tool": "<name>", "args": {...}}
2. "DONE: <final answer>" when the task is complete.

Tools:
- shell {command, background=false, timeout=120} — run a shell command
- read {path} / write {path, content} / edit {path, old, new} / list {path="."}
- fetch {url} — page as text
- search {query} — web search
Keep commands safe and reversible. Prefer reading before writing.
"""


def ask_model(messages: list[dict]) -> str:
    """Replace this with a real LLM call. Demo: a tiny scripted stand-in."""
    last_user = messages[-1]["content"]
    if "list the files" in last_user.lower():
        return json.dumps({"tool": "list", "args": {"path": "."}})
    return "DONE: demo model — wire in your own LLM via ask_model()."


def run_agent(task: str, max_steps: int = 12,
              stream: AGUIStream | None = None) -> str:
    """Run the ReAct loop. Pass an ``AGUIStream`` to broadcast live
    AG-UI events (run/step lifecycle, text, tool calls, state) as it goes."""
    desk = Desktop()
    if stream:
        stream.run_started(task=task)
        stream.state_snapshot(stream.snapshot_desktop(desk))
    jobs_seen = 0

    def _emit_jobs_delta() -> None:
        nonlocal jobs_seen
        jobs = desk.jobs()
        if stream and len(jobs) != jobs_seen:
            jobs_seen = len(jobs)
            from aidesktop.agui import json_patch_replace
            stream.state_delta(json_patch_replace(
                "/jobs", stream.snapshot_desktop(desk)["jobs"]))

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task},
    ]
    try:
        for step in range(max_steps):
            if stream:
                stream.step_started(f"step-{step + 1}")
            reply = ask_model(messages).strip()
            messages.append({"role": "assistant", "content": reply})
            if reply.startswith("DONE:"):
                final = reply[len("DONE:"):].strip()
                if stream:
                    stream.text_message(final)
                    stream.step_finished(f"step-{step + 1}")
                    stream.run_finished(result=final)
                return final
            try:
                call = json.loads(reply)
                tool, args = call["tool"], call.get("args", {})
            except (json.JSONDecodeError, KeyError):
                bad = f"agent produced invalid output: {reply[:200]}"
                if stream:
                    stream.text_message(bad)
                    stream.run_finished(result=bad)
                return bad
            try:
                tc_id = stream.tool_call_start(tool) if stream else None
                if stream:
                    stream.tool_call_args(tc_id, args)
                if tool == "shell":
                    out = desk.shell(**args)
                    observation = str(out if not hasattr(out, "running") else out.log())
                elif tool in ("read", "write", "append", "edit", "list", "fetch", "search"):
                    observation = str(getattr(desk, tool)(**args))
                else:
                    observation = f"unknown tool: {tool}"
                if stream:
                    stream.tool_call_end(tc_id)
                    stream.tool_call_result(tc_id, observation[:4000])
                _emit_jobs_delta()
            except Exception as e:  # noqa: BLE001 — the agent must see its errors
                observation = f"error: {e}"
                if stream:
                    stream.tool_call_end(tc_id)
                    stream.tool_call_result(tc_id, observation)
            messages.append({"role": "user", "content": f"observation:\n{observation[:4000]}"})
            if stream:
                stream.step_finished(f"step-{step + 1}")
        final = "max steps reached without DONE"
        if stream:
            stream.text_message(final)
            stream.run_finished(result=final)
        return final
    finally:
        desk.close()


if __name__ == "__main__":
    print(run_agent(" ".join(sys.argv[1:]) or "list the files in the workspace"))
