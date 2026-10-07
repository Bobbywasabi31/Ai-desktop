"""The Desktop: one object that hands an agent a whole computer.

This is the piece that mirrors how a personal AI agent works day to day:
a persistent working directory, a shell with background jobs, file
primitives, text web access, and (optionally) a real browser — all behind
a safety policy the operator controls.

    from aidesktop import Desktop

    desk = Desktop("~/agent-work")          # everything happens in here
    desk.shell("pip install -q requests")  # foreground, with timeout

    job = desk.shell("python crawl.py", background=True)
    ...
    print(job.poll(timeout=30) or job.log(tail=20))
    job.kill()

    desk.write("report.md", "# findings\\n...")
    desk.edit("report.md", old="draft", new="final")
"""

from __future__ import annotations

import os
from collections.abc import Callable

from . import files, safety, shell, web


class Desktop:
    def __init__(
        self,
        workdir: str = "~/agent-desktop",
        approver: safety.Approver | None = None,
        blocklist: list | None = None,
        confirm_shell: Callable[[str], bool] | None = None,
    ):
        """Create (or attach to) an agent workspace at `workdir`.

        - `approver`: human-in-the-loop gate for irreversible actions.
        - `blocklist`: extra regexes (or ``[]`` to disable) on top of the default.
        - `confirm_shell`: shortcut — called with the command string before
          every shell invocation; return False to refuse it.
        """
        self.workdir = os.path.expanduser(workdir)
        self.files = files.FileSandbox(self.workdir)
        self.approver = approver or safety.Approver()
        self._extra_blocklist = blocklist
        self._confirm_shell = confirm_shell
        self._browser = None
        self._jobs: list[shell.Job] = []

    # -- shell ---------------------------------------------------------
    def shell(
        self,
        command: str,
        timeout: float = 120,
        background: bool = False,
        env: dict | None = None,
        allow_irreversible: bool = False,
    ) -> shell.ShellResult | shell.Job:
        """Run a shell command. Foreground by default; `background=True`
        returns a Job you can poll/kill. Irreversible-looking commands
        (deletes outside the workspace, pushes, publishes) need approval
        unless `allow_irreversible=True` was decided by the operator."""
        safety.check_command(
            command, safety.default_blocklist() + (self._extra_blocklist or [])
        )
        if self._confirm_shell and not self._confirm_shell(command):
            raise safety.SafetyError(
                f"shell command refused by operator: {command[:120]!r}"
            )
        if not allow_irreversible and _looks_irreversible(command):
            self.approver.gate(f"shell: {command}", irreversible=True)
        job_or_result = shell.run(
            command,
            workdir=self.workdir,
            timeout=timeout,
            env=env,
            background=background,
        )
        if isinstance(job_or_result, shell.Job):
            self._jobs.append(job_or_result)
        return job_or_result

    def jobs(self) -> list[shell.Job]:
        """Background jobs started through this Desktop (running or finished)."""
        return list(self._jobs)

    # -- files (thin, sandbox-enforcing wrappers) ----------------------
    def read(self, path: str, max_bytes: int = 200_000) -> str:
        return self.files.read(path, max_bytes)

    def write(self, path: str, content) -> int:
        return self.files.write(path, content)

    def append(self, path: str, content: str) -> int:
        return self.files.append(path, content)

    def edit(self, path: str, old: str, new: str) -> int:
        return self.files.edit(path, old, new)

    def list(self, path: str = ".") -> list[dict]:
        return self.files.list(path)

    def mkdir(self, path: str) -> None:
        return self.files.mkdir(path)

    def remove(self, path: str) -> None:
        self.approver.gate(f"delete {path}", irreversible=True)
        return self.files.remove(path)

    def exists(self, path: str) -> bool:
        return self.files.exists(path)

    # -- web -----------------------------------------------------------
    def fetch(self, url: str, max_chars: int = 30_000) -> str:
        """Fetch a page as readable text (no login, no JS)."""
        return web.fetch(url, max_chars)

    def search(self, query: str, max_results: int = 8) -> list[dict]:
        """Web search. Returns [{title, url, snippet}]."""
        return web.search(query, max_results)

    # -- real browser (lazy; needs the [browser] extra) ----------------
    @property
    def browser(self):
        """A persistent Chromium page. First access launches it."""
        if self._browser is None:
            from .browser import Browser

            self._browser = Browser()
        return self._browser

    def close(self) -> None:
        for job in self._jobs:
            if job.running():
                job.kill()
        if self._browser is not None:
            self._browser.close()
            self._browser = None

    def __enter__(self) -> Desktop:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _looks_irreversible(command: str) -> bool:
    lowered = command.lower()
    markers = [
        "rm -rf",
        "rm -r",
        ":(){",
        "mkfs",
        "dd ",
        "git push",
        "gh release",
        "npm publish",
        "pip upload",
        "twine upload",
        "shutdown",
        "reboot",
        ">/dev/sd",
    ]
    return any(m in lowered for m in markers)
