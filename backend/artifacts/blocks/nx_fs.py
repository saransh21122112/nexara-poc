"""Read-only file access inside configured roots (vetted block): no traversal, no symlink escape, size caps."""
import os
from pathlib import Path


class FsDenied(Exception):
    """Message is safe to show to the model."""


class Roots:
    def __init__(self, roots):
        if not roots:
            raise FsDenied("No roots configured.")
        self.roots = [Path(os.path.realpath(r)) for r in roots]

    def _inside(self, p):
        return any(p == r or r in p.parents for r in self.roots)

    def resolve(self, path):
        if not isinstance(path, str) or not path or "\x00" in path or path.startswith("~"):
            raise FsDenied("Path not allowed.")
        cand = Path(path)
        options = [cand] if cand.is_absolute() else [r / cand for r in self.roots]
        for o in options:
            real = Path(os.path.realpath(o))
            if self._inside(real) and real.exists():
                return real
        raise FsDenied("Path not found inside the allowed folders.")

    def read_text(self, path, max_bytes=200_000):
        p = self.resolve(path)
        if not p.is_file():
            raise FsDenied("Not a file.")
        if p.stat().st_size > max_bytes:
            raise FsDenied("File is too large to read.")
        return p.read_text(encoding="utf-8", errors="replace")

    def walk(self, exts=(".md", ".txt"), max_files=2000):
        out = []
        for r in self.roots:
            for dirpath, dirs, files in os.walk(r, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in sorted(files):
                    p = Path(dirpath) / f
                    if f.lower().endswith(tuple(exts)) and not p.is_symlink() and self._inside(Path(os.path.realpath(p))):
                        out.append(p)
                        if len(out) >= max_files:
                            return out
        return out
