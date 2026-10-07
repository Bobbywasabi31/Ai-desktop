"""Approval hooks and a destructive-command blocklist.

An agent that can run shell commands needs a seatbelt. Two layers:

1. :func:`default_blocklist` — regexes for commands that are (almost)
   never what the agent meant (``rm -rf /``, fork bombs, credential dumps).
   These raise :class:`SafetyError` before anything runs.
2. :class:`Approver` — an optional human-in-the-loop callback. Anything the
   agent flags as irreversible (deletes, pushes, publishes, payments) goes
   through ``approver.confirm(action_description)`` first.
"""

from __future__ import annotations

import re
from collections.abc import Callable


class SafetyError(RuntimeError):
    pass


def default_blocklist() -> list[re.Pattern]:
    return [
        re.compile(r"(^|[\s;&|])rm\s+(-[rf]+\s+)*/(?:\s|$)"),  # rm -rf /
        re.compile(r"(^|[\s;&|])rm\s+-rf?\s+~(?:\s|$)"),  # rm -rf ~
        re.compile(r"(^|[\s;&|])rm\s+-rf?\s+\$HOME"),  # rm -rf $HOME
        re.compile(r":\(\)\s*\{\s*:\|\:&\s*\}\s*;:"),  # fork bomb
        re.compile(r"mkfs(\.|\\s)"),  # format a disk
        re.compile(r"dd\s+.*of=/dev/"),  # raw disk writes
        re.compile(r">\s*/dev/sd"),  # disk clobber
        re.compile(r"curl[^|]*\|\s*(ba)?sh"),  # pipe-to-shell
        re.compile(r"wget[^|]*\|\s*(ba)?sh"),
        re.compile(r"\.ssh/id_[a-z_]+\"?\s*$"),  # cat of private key
        re.compile(r"printenv|env\s*$"),  # env dumps (often leak tokens)
    ]


def check_command(command: str, blocklist: list[re.Pattern] | None = None) -> None:
    """Raise SafetyError if `command` matches the blocklist."""
    for pattern in blocklist if blocklist is not None else default_blocklist():
        if pattern.search(command):
            raise SafetyError(f"blocked by safety policy: {command[:120]!r}")


class Approver:
    """Human-in-the-loop gate for irreversible actions.

    Pass ``confirm=...`` — a callable taking a description string and
    returning True/False. The default approver denies everything, which is
    the safe choice for unattended agents.
    """

    def __init__(
        self,
        confirm: Callable[[str], bool] | None = None,
        auto_approve_read_only: bool = True,
    ):
        self._confirm = confirm or (lambda _desc: False)
        self.auto_approve_read_only = auto_approve_read_only
        self.history: list[tuple[str, bool]] = []

    def confirm(self, description: str) -> bool:
        allowed = bool(self._confirm(description))
        self.history.append((description, allowed))
        return allowed

    def gate(self, description: str, *, irreversible: bool) -> None:
        """Raise SafetyError unless the action is approved."""
        if (irreversible or not self.auto_approve_read_only) and not self.confirm(
            description
        ):
            raise SafetyError(f"action not approved: {description[:200]}")
