"""Shell execution with foreground and background jobs.

Mirrors how an agent should treat long-running work: start it, stay
responsive, poll for output, kill when it is no longer needed.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ShellResult:
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def __str__(self) -> str:  # convenient for stuffing into a prompt
        head = f"$ {self.command}\n"
        if self.timed_out:
            return head + f"<timed out, exit={self.exit_code}>"
        return head + self.stdout + (self.stderr or "")


class Job:
    """A background process the agent can poll, read, and kill."""

    def __init__(self, command: str, proc: subprocess.Popen, workdir: str):
        self.command = command
        self._proc = proc
        self.workdir = workdir
        self._lock = threading.Lock()
        self._stdout_chunks: list[str] = []
        self._stderr_chunks: list[str] = []
        self._reader_threads = [
            threading.Thread(target=self._drain, args=(proc.stdout, self._stdout_chunks), daemon=True),
            threading.Thread(target=self._drain, args=(proc.stderr, self._stderr_chunks), daemon=True),
        ]
        for t in self._reader_threads:
            t.start()

    @staticmethod
    def _drain(stream, chunks: list[str]) -> None:
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
        except ValueError:
            pass  # stream closed by kill()

    @property
    def pid(self) -> int:
        return self._proc.pid

    def running(self) -> bool:
        return self._proc.poll() is None

    def poll(self, timeout: float = 0) -> ShellResult | None:
        """Wait up to `timeout` seconds; return a result if finished, else None."""
        try:
            rc = self._proc.wait(timeout=timeout if timeout > 0 else None)
        except subprocess.TimeoutExpired:
            return None
        return self.result(rc)

    def result(self, exit_code: int | None = None) -> ShellResult:
        with self._lock:
            out = "".join(self._stdout_chunks)
            err = "".join(self._stderr_chunks)
        rc = exit_code if exit_code is not None else self._proc.poll()
        return ShellResult(self.command, rc, out, err)

    def log(self, tail: int = 50) -> str:
        """Last `tail` lines of combined output without blocking."""
        with self._lock:
            lines = ("".join(self._stdout_chunks) + "".join(self._stderr_chunks)).splitlines()
        return "\n".join(lines[-tail:])

    def kill(self) -> ShellResult:
        """Terminate the whole process tree and return what it produced."""
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            self._proc.wait()
        return self.result()

    def wait(self, timeout: float | None = None) -> ShellResult:
        try:
            rc = self._proc.wait(timeout=timeout)
            return self.result(rc)
        except subprocess.TimeoutExpired:
            return ShellResult(self.command, None, "", "", timed_out=True)


def run(command: str, workdir: str = ".", timeout: float = 120,
        env: dict | None = None, background: bool = False) -> ShellResult | Job:
    """Run `command` via the system shell.

    Foreground by default (with `timeout`). Pass ``background=True`` to get
    a :class:`Job` instead — poll it, read its log, kill it when done.
    """
    if background:
        proc = subprocess.Popen(
            command, shell=True, cwd=workdir, env={**os.environ, **(env or {})},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, start_new_session=True,
        )
        return Job(command, proc, workdir)
    try:
        completed = subprocess.run(
            command, shell=True, cwd=workdir, timeout=timeout,
            env={**os.environ, **(env or {})},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        return ShellResult(command, completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as e:
        return ShellResult(
            command, None,
            (e.stdout or "") if isinstance(e.stdout, str) else "",
            (e.stderr or "") if isinstance(e.stderr, str) else "",
            timed_out=True,
        )


def quote(arg: str) -> str:
    """Shell-quote one argument (use instead of string interpolation)."""
    return shlex.quote(arg)
