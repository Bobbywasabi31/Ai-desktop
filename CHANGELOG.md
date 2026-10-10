# Changelog

All notable changes to ai-desktop. Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) conventions.

## [Unreleased]

### Added
- `tests/test_version.py`: guard asserting `aidesktop.__version__` matches
  `pyproject.toml` and is semver — the 0.2.0→0.3.0 drift is now a test
  failure instead of a silent mismatch (parses with a regex so it also
  runs on Python 3.10).
- `CHANGELOG.md` (this file).
- `examples/agui_client.py`: stdlib-only terminal SSE client for the AG-UI
  demo server — connects to `/agent`, prints each of the 15 event types as
  it arrives (streamed text inline), and exits cleanly on run end,
  mid-stream disconnects, unreachable servers, and Ctrl-C.
  Tests cover SSE frame parsing, per-type rendering, and a live
  server roundtrip.
- `examples/react_agent.py`: `ask_model()` now talks to any OpenAI-compatible
  chat-completions endpoint by default — bring your own key via
  `OPENAI_API_KEY`, point anywhere via `OPENAI_BASE_URL`/`OPENAI_MODEL`
  (stdlib only, no third-party client). Scripted offline demo kept as the
  no-key fallback; CLI flags `--model`/`--base-url`/`--max-steps`.

### Removed
- `archive/` scratch files (early tkinter experiments) — deleted from the repo.

### Changed
- CI now lints with ruff (`ruff check` + `ruff format --check`) and tests a
  Python 3.10–3.13 matrix.

## [0.3.0] — 2026-10-05

### Added
- AG-UI 1.0 upgrade is complete: the 15 remaining new events are implemented
  in `aidesktop.agui` and verified against `@ag-ui/core@1.0.1` schemas.

### Fixed
- `RUN_STARTED` no longer emits an `input.task` field (AG-UI 1.0
  `RunAgentInput` has no `task`).
- `TOOL_CALL_RESULT` no longer emits `"role": "tool"` (AG-UI 1.0 drift).

## [0.2.0] — 2026-10-02

### Added
- AG-UI live streaming layer (`aidesktop.agui`) — a spec-conformant SSE
  event producer for agent runs.
- `run_agent(..., stream=...)` in `examples/react_agent.py`.
- Stdlib-only SSE server `examples/agui_server.py` with a demo page at
  `/demo`.

## [0.1.0] — 2026-10-02

### Added
- Initial toolkit giving an AI agent its own desktop: `Desktop` facade over
  shell (foreground + background jobs), file primitives, text web access,
  and optional real-browser control (`ai-desktop[browser]`, playwright).
- Safety gate: operator-controlled approval policy with blocklist.

[Unreleased]: https://github.com/Bobbywasabi31/Ai-desktop/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Bobbywasabi31/Ai-desktop/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Bobbywasabi31/Ai-desktop/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Bobbywasabi31/Ai-desktop/releases/tag/v0.1.0
