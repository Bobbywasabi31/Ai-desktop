"""Minimal ReAct-style agent loop built on ai-desktop.

This is the pattern the toolkit is for: the LLM reasons, picks one of the
Desktop's tools, observes the result, and repeats. The default `ask_model`
talks to any OpenAI-compatible chat-completions endpoint — bring your own
key and point it anywhere (OpenAI, OpenRouter, Azure, Ollama, LM Studio, …).

Run (with a key):
    export OPENAI_API_KEY=sk-...
    python examples/react_agent.py "list the files in the workspace"

    # any OpenAI-compatible endpoint:
    export OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_MODEL=llama3.1
    python examples/react_agent.py "summarize README.md"

With no key set, it falls back to a tiny scripted stand-in so the loop
still demonstrates itself offline (stdout notes that it is a demo).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

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


@dataclass
class LLMConfig:
    """OpenAI-compatible endpoint config, all overridable by environment."""

    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY") or None,
            base_url=os.environ.get("OPENAI_BASE_URL", cls.base_url).rstrip("/"),
            model=os.environ.get("OPENAI_MODEL", cls.model),
        )


def _strip_code_fences(text: str) -> str:
    """LLMs love wrapping JSON in ```json ... ``` — peel that off."""
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
        return text[3:-3].strip()
    return text


def _chat_completion(messages: list[dict], config: LLMConfig) -> str:
    """One chat-completions call, stdlib only (no third-party client needed)."""
    assert config.api_key, "OPENAI_API_KEY is required"
    payload = json.dumps(
        {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
        }
    ).encode()
    req = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"LLM endpoint returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"could not reach LLM endpoint {config.base_url}: {e.reason}"
        ) from e
    try:
        return _strip_code_fences(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(
            f"unexpected response shape from {config.base_url}: {str(body)[:300]}"
        ) from e


def _scripted_demo(messages: list[dict]) -> str:
    """Offline stand-in: handles the README example and ends any task."""
    print(
        "[demo] OPENAI_API_KEY not set — using the scripted stand-in. "
        "Set it to run against a real model.",
        file=sys.stderr,
    )
    last_user = messages[-1]["content"]
    if "list the files" in last_user.lower():
        return json.dumps({"tool": "list", "args": {"path": "."}})
    return "DONE: demo model — set OPENAI_API_KEY to run with a real LLM."


def ask_model(messages: list[dict], config: LLMConfig | None = None) -> str:
    """Return the model's next turn: a tool-call JSON blob or 'DONE: ...'.

    With no API key configured this uses the scripted offline demo so the
    example always runs; with a key it calls the configured endpoint.
    """
    config = config or LLMConfig.from_env()
    if config.api_key is None:
        return _scripted_demo(messages)
    return _chat_completion(messages, config)


def run_agent(
    task: str,
    max_steps: int = 12,
    stream: AGUIStream | None = None,
    config: LLMConfig | None = None,
) -> str:
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

            stream.state_delta(
                json_patch_replace("/jobs", stream.snapshot_desktop(desk)["jobs"])
            )

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task},
    ]
    try:
        for step in range(max_steps):
            if stream:
                stream.step_started(f"step-{step + 1}")
            reply = ask_model(messages, config).strip()
            messages.append({"role": "assistant", "content": reply})
            if reply.startswith("DONE:"):
                final = reply[len("DONE:") :].strip()
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
                elif tool in (
                    "read",
                    "write",
                    "append",
                    "edit",
                    "list",
                    "fetch",
                    "search",
                ):
                    observation = str(getattr(desk, tool)(**args))
                else:
                    observation = f"unknown tool: {tool}"
                if stream:
                    stream.tool_call_end(tc_id)
                    stream.tool_call_result(tc_id, observation[:4000])
                _emit_jobs_delta()
            except Exception as e:  # the agent must see its errors
                observation = f"error: {e}"
                if stream:
                    stream.tool_call_end(tc_id)
                    stream.tool_call_result(tc_id, observation)
            messages.append(
                {"role": "user", "content": f"observation:\n{observation[:4000]}"}
            )
            if stream:
                stream.step_finished(f"step-{step + 1}")
        final = "max steps reached without DONE"
        if stream:
            stream.text_message(final)
            stream.run_finished(result=final)
        return final
    finally:
        desk.close()


def main(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(
        description="ReAct agent over an ai-desktop Desktop, powered by any "
        "OpenAI-compatible endpoint (BYO key via OPENAI_API_KEY)."
    )
    parser.add_argument("task", nargs="?", help="task for the agent")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        help="model name (or OPENAI_MODEL)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI-compatible base URL (or OPENAI_BASE_URL)",
    )
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args(argv)
    task = args.task or "list the files in the workspace"
    config = LLMConfig(
        api_key=os.environ.get("OPENAI_API_KEY") or None,
        base_url=args.base_url.rstrip("/"),
        model=args.model,
    )
    return run_agent(task, max_steps=args.max_steps, config=config)


if __name__ == "__main__":
    print(main())
