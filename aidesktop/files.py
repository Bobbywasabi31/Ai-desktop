"""Filesystem primitives, sandboxed to a working directory.

Every path is resolved against the Desktop's root so an agent cannot
wander off the reservation with ``../../`` tricks. Symlinks that escape
the root are rejected too.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class PathEscapeError(ValueError):
    pass


class FileSandbox:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str | os.PathLike) -> Path:
        """Resolve `path` inside the sandbox; raise PathEscapeError otherwise."""
        candidate = (self.root / Path(path)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise PathEscapeError(f"path escapes workspace root: {path}")
        return candidate

    # -- reads -----------------------------------------------------------
    def read(self, path: str, max_bytes: int = 200_000) -> str:
        p = self.resolve(path)
        data = p.read_bytes()[:max_bytes]
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary file: {len(data)} bytes>"

    def list(self, path: str = ".", max_entries: int = 500) -> list[dict]:
        p = self.resolve(path)
        entries = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            try:
                st = child.stat()
            except OSError:
                continue
            entries.append({
                "name": child.name + ("/" if child.is_dir() else ""),
                "size": st.st_size,
                "is_dir": child.is_dir(),
            })
            if len(entries) >= max_entries:
                break
        return entries

    def exists(self, path: str) -> bool:
        try:
            return self.resolve(path).exists()
        except PathEscapeError:
            return False

    # -- writes ----------------------------------------------------------
    def write(self, path: str, content: str | bytes) -> int:
        p = self.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        # atomic-ish: write temp file, then rename
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return len(data)

    def append(self, path: str, content: str) -> int:
        p = self.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        with p.open("ab") as f:
            f.write(data)
        return len(data)

    def edit(self, path: str, old: str, new: str, count: int = 1) -> int:
        """Replace `old` with `new` (first `count` occurrences). Returns replacements made."""
        p = self.resolve(path)
        text = p.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"text not found in {path}: {old[:60]!r}...")
        updated = text.replace(old, new, count)
        self.write(path, updated)
        return text.count(old) if count < 0 else min(count, text.count(old))

    def mkdir(self, path: str) -> None:
        self.resolve(path).mkdir(parents=True, exist_ok=True)

    def remove(self, path: str) -> None:
        p = self.resolve(path)
        if p == self.root:
            raise ValueError("refusing to delete the workspace root")
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)

    def move(self, src: str, dst: str) -> None:
        s, d = self.resolve(src), self.resolve(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        s.rename(d)
