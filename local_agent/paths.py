"""Single-writer workspace guard, NOT an OS sandbox for executed code."""
from __future__ import annotations
import os
from pathlib import Path, PureWindowsPath
import tempfile

class PolicyError(ValueError):
    pass

class Workspace:
    def __init__(self, root: str | Path, max_bytes: int = 1_048_576):
        p = Path(root).absolute()
        if p.is_symlink():
            raise PolicyError("Workspace root may not be a symlink")
        p.mkdir(parents=True, exist_ok=True)
        self.root = p.resolve(strict=True)
        self.max_bytes = max_bytes

    def path(self, relative: str, *, allow_root: bool = False) -> Path:
        if not isinstance(relative, str) or not relative or "\x00" in relative:
            raise PolicyError("A nonempty relative path is required")
        # Cross-platform rejection: absolute paths, UNC/drive paths, ADS and backslashes.
        if "\\" in relative or ":" in relative or Path(relative).is_absolute() or PureWindowsPath(relative).drive:
            raise PolicyError("Use workspace-relative paths with forward slashes")
        parts = Path(relative).parts
        if ".." in parts or len(parts) > 32:
            raise PolicyError("Parent traversal / excessive depth is forbidden")
        reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)}
        if any(x.endswith((" ", ".")) or x.split(".")[0].upper() in reserved for x in parts):
            raise PolicyError("Platform-reserved filename")
        if any(x.startswith(".agent") for x in parts):
            raise PolicyError("Agent internal names are reserved")
        p = self.root.joinpath(*parts)
        if p == self.root and not allow_root:
            raise PolicyError("An individual file path is required")
        cur = self.root
        for part in parts:
            cur = cur / part
            if cur.is_symlink():
                raise PolicyError("Symlinks are not allowed in workspace tools")
            if cur.exists() and cur.is_file() and cur.stat().st_nlink > 1:
                raise PolicyError("Hardlinked files are not allowed")
        resolved = p.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise PolicyError("Path escapes workspace")
        return p

    def read(self, path: str) -> str:
        p = self.path(path)
        if not p.is_file():
            raise PolicyError("Not a regular file")
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            data = f.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise PolicyError("File exceeds configured text size limit")
        return data.decode("utf-8")

    def write(self, path: str, content: str, *, overwrite: bool = False) -> dict:
        if not isinstance(content, str):
            raise PolicyError("Content must be text")
        data = content.encode("utf-8")
        if len(data) > self.max_bytes:
            raise PolicyError("File exceeds configured text size limit")
        p = self.path(path)
        if p.exists() and not overwrite:
            raise PolicyError("File already exists; explicit overwrite=true required")
        if p.exists() and not p.is_file():
            raise PolicyError("Not a regular file")
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path(path)  # Recheck after parent creation; still requires a single trusted writer.
        fd, tmp = tempfile.mkstemp(prefix=".agent-write-", dir=p.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {"path": path, "bytes": len(data)}

    def patch(self, path: str, old: str, new: str) -> dict:
        if not old:
            raise PolicyError("Empty search string is forbidden")
        text = self.read(path)
        if text.count(old) != 1:
            raise PolicyError("Patch requires exactly one matching occurrence")
        return self.write(path, text.replace(old, new, 1), overwrite=True)

    def list(self, path: str = ".") -> list[dict]:
        p = self.path(path, allow_root=True)
        if not p.is_dir():
            raise PolicyError("Not a directory")
        result = []
        for child in sorted(p.iterdir(), key=lambda x: x.name)[:1000]:
            if child.name.startswith(".agent"):
                continue
            if child.is_symlink():
                kind = "symlink-blocked"
            else:
                kind = "directory" if child.is_dir() else "file"
            result.append({"name": child.name, "type": kind})
        return result
