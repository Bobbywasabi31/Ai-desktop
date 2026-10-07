"""Live AG-UI server for the ai-desktop demo agent.

Streams a real agent run as AG-UI events over Server-Sent Events, so any
AG-UI consumer (CopilotKit, a custom React UI, curl) can watch the agent
work its desktop in real time:

    python examples/agui_server.py                 # :8765
    curl -N "http://localhost:8765/agent?task=list the files in the workspace"
    # or open http://localhost:8765/demo in a browser

Stdlib only — no web framework needed.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "examples")
from react_agent import run_agent

from aidesktop.agui import AGUIStream

DEMO_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>ai-desktop · AG-UI live</title>
<style>body{font-family:monospace;max-width:70ch;margin:2em auto;background:#0d1117;color:#c9d1d9}
.ev{border-left:3px solid #30363d;padding:.3em .6em;margin:.4em 0;background:#161b22}
.ev.tool{border-color:#d29922}.ev.text{border-color:#58a6ff}.ev.run{border-color:#3fb950}
input{width:60ch;padding:.5em;background:#161b22;color:#c9d1d9;border:1px solid #30363d}
button{padding:.5em 1em;background:#238636;color:#fff;border:0;cursor:pointer}</style></head>
<body><h1>ai-desktop · live agent events (AG-UI)</h1>
<p><input id="task" value="list the files in the workspace">
<button onclick="start()">Run</button></p><div id="log"></div>
<script>
function start(){
  document.getElementById('log').innerHTML='';
  const es = new EventSource('/agent?task='+encodeURIComponent(document.getElementById('task').value));
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    const d = document.createElement('div');
    d.className = 'ev ' + (ev.type.includes('TOOL_CALL') ? 'tool' : ev.type.includes('TEXT') ? 'text' : ev.type.startsWith('RUN') ? 'run' : '');
    d.textContent = ev.type + ' ' + (ev.delta || ev.toolCallName || ev.message || ev.stepName || ev.result || '');
    document.getElementById('log').appendChild(d);
    if (ev.type === 'RUN_FINISHED' || ev.type === 'RUN_ERROR') es.close();
  };
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "agui-server/0.2.0"

    def log_message(self, *args):  # keep the console quiet
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/demo":
            body = DEMO_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path != "/agent":
            self.send_error(404)
            return
        task = parse_qs(parsed.query).get("task", [""])[0]
        if not task:
            self.send_error(400, "pass ?task=...")
            return

        q: queue.Queue = queue.Queue()

        def _sink(event: dict) -> None:
            q.put(event)

        def _run() -> None:
            try:
                run_agent(task, stream=AGUIStream(sink=_sink))
            except Exception as e:  # surfaced to the UI as RUN_ERROR
                q.put({"type": "RUN_ERROR", "message": f"{type(e).__name__}: {e}"})
            finally:
                q.put(None)

        threading.Thread(target=_run, daemon=True).start()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                event = q.get()
                if event is None:
                    break
                self.wfile.write(
                    f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode()
                )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(
        f"AG-UI SSE server on http://127.0.0.1:{port}/agent?task=...  "
        f"(demo UI at /demo)"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
