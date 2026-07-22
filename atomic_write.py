"""Atomic write helper shared by every path that writes cities/*.json or
reports/*.md.

A crash or network drop mid-write must never leave a truncated/corrupt file
behind — for a city JSON that would break every subsequent lint/render/
backfill run against that city. Writing to a temp file in the same directory
and atomically renaming it over the target on success guarantees the target
is always either the old complete file or the new complete file, never a
partial one.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # atomic on POSIX and Windows
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
