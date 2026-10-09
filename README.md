# ai-desktop

Give an AI agent its own desktop — the same primitives a personal AI agent
works with every day: a shell (foreground + background jobs), a sandboxed
filesystem, text-mode web access, and a real Chromium browser.

```python
from aidesktop import Desktop

desk = Desktop("~/agent-work")

# shell
print(desk.shell("ls").stdout)

# long work stays responsive: poll it, read its log, kill it
job = desk.shell("python train.py", background=True)
print(job.poll(timeout=30) or job.log(tail=20))
job.kill()

# files (sandboxed to the workspace)
desk.write("notes.md", "# plan\n")
desk.edit("notes.md", old="# plan", new="# final plan")

# web, no keys needed
results = desk.search("latest python release")
text = desk.fetch(results[0]["url"])

# real browser (optional extra)
title = desk.browser.open("https://example.com")
desk.browser.fill("#search", "ai agents")
desk.browser.screenshot("shot.png")
```

## Install

```bash
pip install ai-desktop            # core: shell, files, web
pip install "ai-desktop[browser]" # + real Chromium via playwright
# then: playwright install chromium
```

## Safety

An agent with a shell needs a seatbelt. Two layers, both on by default:

1. **Blocklist** — `rm -rf /`, fork bombs, disk writes, pipe-to-shell
   downloads, and credential dumps raise `SafetyError` before running.
2. **Approver** — a human-in-the-loop callback. Irreversible actions
   (deletes outside the workspace, `git push`, publishes…) call
   `approver.confirm(description)` first; the default approver denies.

```python
from aidesktop import Desktop, Approver

# ask the human on the console
desk = Desktop(approver=Approver(confirm=lambda d: input(f"Allow? {d} [y/N]") == "y"))
desk.remove("old-report.pdf")  # prompts first
```

The filesystem is sandboxed to the workspace root — `../../` escapes and
symlink breakouts raise `PathEscapeError`.

## The agent loop

`examples/react_agent.py` shows the intended pattern: the model reasons,
emits one tool call per turn (`{"tool": "shell", "args": {...}}`), reads
the observation, and repeats until `DONE:`. Its `ask_model()` talks to
any OpenAI-compatible chat-completions endpoint — bring your own key,
point it anywhere (OpenAI, OpenRouter, Azure, Ollama, LM Studio):

```bash
export OPENAI_API_KEY=sk-...
python examples/react_agent.py "list the files in the workspace"

# local model, e.g. Ollama:
export OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_MODEL=llama3.1
python examples/react_agent.py "summarize README.md"
```

Stdlib only — no `openai` client needed. With no key set it falls back
to a scripted demo so the loop still runs offline. Pass an
`AGUIStream` to `run_agent(..., stream=stream)` to broadcast the run as
live AG-UI events.

## Live UI with AG-UI

`aidesktop.agui` turns an agent run into an
[AG-UI](https://ag-ui.com) event stream — the open standard for agent→UI
communication (adopted by CopilotKit, Microsoft Agent Framework, LangGraph,
CrewAI, and others). Any AG-UI consumer can render what the agent is doing
in real time: streamed text, tool calls with args/results, run/step
lifecycle, and shared state (working directory, background jobs).

```python
from aidesktop import AGUIStream

stream = AGUIStream()
stream.run("summarize the repo", lambda s: my_agent(s))
for chunk in stream.sse_chunks():      # data: {...}\n\n over SSE
    ...
```

Or run the demo as a real server (stdlib only, no web framework):

```bash
python examples/agui_server.py
# curl -N "http://localhost:8765/agent?task=list the files"
# or open http://localhost:8765/demo in a browser
```

Or watch the stream from a second terminal with the SSE client
(stdlib only — prints each event type as it arrives, exits cleanly when
the run finishes or the connection drops):

```bash
python examples/agui_client.py "list the files in the workspace"
```

The event shapes (`RUN_STARTED`, `TEXT_MESSAGE_START/CONTENT/END`,
`TOOL_CALL_START/ARGS/END/RESULT`, `STATE_SNAPSHOT`, `STATE_DELTA` as
JSON Patch, `RUN_FINISHED`/`RUN_ERROR`, `CUSTOM`) follow the AG-UI spec's
camelCase field names, so events validate against the official schema.
AG-UI 1.0 additions are covered too: the `REASONING_*` family (streamed
thinking, kept separate from the reply), `ACTIVITY_SNAPSHOT`/`DELTA`
(structured progress widgets), `SUBAGENT_STARTED`/`FINISHED`/`ERROR`
with `subagentRunId` attribution, `RAW` provider passthrough, and the
`TEXT_MESSAGE_CHUNK`/`TOOL_CALL_CHUNK`/`REASONING_MESSAGE_CHUNK`
shorthands. Per 1.0, `RUN_STARTED` carries no `input.task` and
`TOOL_CALL_RESULT` carries no `role`; result `content` may be a string
or a list of multimodal content parts.

## API sketch

| Method | What it does |
|---|---|
| `desk.shell(cmd, timeout, background)` | Run shell; `Job` if background |
| `job.poll / job.log / job.kill / job.wait` | Manage background work |
| `desk.read / write / edit / list / mkdir / remove / exists` | Sandboxed files |
| `desk.fetch(url)` / `desk.search(query)` | Page text / web search |
| `desk.browser` | Persistent Chromium page (lazy) |
| `desk.jobs()` | All background jobs this session |

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

## License

MIT — see [LICENSE](LICENSE).
