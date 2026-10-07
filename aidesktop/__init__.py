"""ai-desktop: give an AI agent its own desktop.

A small, dependency-light toolkit that hands an agent the same primitives
a human developer has on a workstation: a shell (foreground + background
jobs), a filesystem, the web (fetch + search), and a real browser.

    from aidesktop import Desktop

    desk = Desktop()
    print(desk.shell("echo hello").stdout)
    desk.write("todo.txt", "- take over the world\\n")
"""

from .agui import AGUIStream, decode_sse, encode_sse
from .desktop import Desktop
from .safety import Approver, SafetyError, default_blocklist
from .shell import Job, ShellResult

__all__ = [
    "AGUIStream",
    "Approver",
    "Desktop",
    "Job",
    "SafetyError",
    "ShellResult",
    "decode_sse",
    "default_blocklist",
    "encode_sse",
]
__version__ = "0.3.0"
