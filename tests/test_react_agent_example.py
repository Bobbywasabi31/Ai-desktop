"""Tests for examples/react_agent.py's LLM wiring. Run with: python -m pytest"""

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "react_agent.py"


@pytest.fixture()
def react_agent():
    spec = importlib.util.spec_from_file_location("react_agent_example", EXAMPLES)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # dataclasses._is_type resolves field annotations via sys.modules[cls.__module__],
    # so the module must be registered before exec.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[spec.name]
        raise
    yield mod
    del sys.modules[spec.name]


def test_strip_code_fences(react_agent):
    assert (
        react_agent._strip_code_fences('```json\n{"tool": "list"}\n```')
        == '{"tool": "list"}'
    )
    assert react_agent._strip_code_fences("```\nplain\n```") == "plain"
    assert react_agent._strip_code_fences("no fences") == "no fences"
    assert react_agent._strip_code_fences("  DONE: ok  ") == "DONE: ok"


def test_config_from_env(react_agent, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1/")
    monkeypatch.setenv("OPENAI_MODEL", "llama3.1")
    cfg = react_agent.LLMConfig.from_env()
    assert cfg.api_key == "sk-test"
    assert cfg.base_url == "http://localhost:11434/v1"  # trailing slash stripped
    assert cfg.model == "llama3.1"


def test_ask_model_falls_back_without_key(react_agent, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reply = react_agent.ask_model(
        [{"role": "user", "content": "list the files in the workspace"}]
    )
    call = json.loads(reply)
    assert call == {"tool": "list", "args": {"path": "."}}
    assert "[demo]" in capsys.readouterr().err


def _fake_urlopen(captured, reply_text=None, raise_error=None):
    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["payload"] = json.loads(req.data.decode())
        if raise_error is not None:
            raise raise_error
        body = {
            "choices": [{"message": {"content": reply_text}}],
        }

        class Resp:
            def read(self):
                return json.dumps(body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Resp()

    return fake


def test_chat_completion_request_shape(react_agent, monkeypatch):
    captured = {}
    fake_reply = '```json\n{"tool":"list","args":{"path":"."}}\n```'
    monkeypatch.setattr(
        react_agent.urllib.request,
        "urlopen",
        _fake_urlopen(captured, reply_text=fake_reply),
    )
    cfg = react_agent.LLMConfig(
        api_key="sk-test", base_url="http://ollama:11434/v1", model="m"
    )
    out = react_agent._chat_completion([{"role": "user", "content": "hi"}], cfg)
    assert out == '{"tool":"list","args":{"path":"."}}'  # fences stripped
    assert captured["url"] == "http://ollama:11434/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["payload"]["model"] == "m"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_completion_http_error_is_readable(react_agent, monkeypatch):
    err = urllib.error.HTTPError(
        url="x",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b'{"error": "bad key"}'),
    )
    monkeypatch.setattr(
        react_agent.urllib.request, "urlopen", _fake_urlopen({}, raise_error=err)
    )
    cfg = react_agent.LLMConfig(api_key="sk-bad")
    with pytest.raises(RuntimeError, match="HTTP 401"):
        react_agent._chat_completion([{"role": "user", "content": "hi"}], cfg)


def test_chat_completion_bad_response_shape(react_agent, monkeypatch):
    body = {"choices": []}

    class Resp:
        def read(self):
            return json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        react_agent.urllib.request, "urlopen", lambda req, timeout=None: Resp()
    )
    cfg = react_agent.LLMConfig(api_key="sk-test")
    with pytest.raises(RuntimeError, match="unexpected response shape"):
        react_agent._chat_completion([{"role": "user", "content": "hi"}], cfg)


class _FakeDesktop:
    def __init__(self):
        self.tools = {"list": "a.txt\nb.txt"}

    def shell(self, **kwargs):
        raise AssertionError("shell should not be called here")

    def __getattr__(self, name):
        if name in self.tools:

            def tool(**kwargs):
                return self.tools[name]

            return tool
        raise AttributeError(name)

    def jobs(self):
        return []

    def close(self):
        pass


def test_run_agent_end_to_end_with_fake_model(react_agent, monkeypatch):
    monkeypatch.setattr(react_agent, "Desktop", _FakeDesktop)
    turns = iter(
        [
            json.dumps({"tool": "list", "args": {"path": "."}}),
            "DONE: two files",
        ]
    )
    monkeypatch.setattr(
        react_agent, "ask_model", lambda messages, config=None: next(turns)
    )
    assert react_agent.run_agent("list the files") == "two files"


def test_run_agent_rejects_invalid_output(react_agent, monkeypatch):
    monkeypatch.setattr(react_agent, "Desktop", _FakeDesktop)
    monkeypatch.setattr(
        react_agent, "ask_model", lambda messages, config=None: "not json at all"
    )
    result = react_agent.run_agent("do something")
    assert result.startswith("agent produced invalid output")


def test_main_cli_parses_task_and_flags(react_agent, monkeypatch, tmp_path):
    monkeypatch.setattr(react_agent, "Desktop", _FakeDesktop)
    seen = {}
    monkeypatch.setattr(
        react_agent,
        "run_agent",
        lambda task, max_steps=12, config=None, stream=None: (
            seen.update(task=task, max_steps=max_steps, model=config.model),
            "ok",
        )[1],
    )
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    result = react_agent.main(
        ["count things", "--model", "flag-model", "--max-steps", "3"]
    )
    assert result == "ok"
    assert seen == {"task": "count things", "max_steps": 3, "model": "flag-model"}


def test_no_third_party_deps(react_agent):
    # the BYO-key example must stay stdlib-only
    source = EXAMPLES.read_text()
    assert "import openai" not in source
    assert "import anthropic" not in source
