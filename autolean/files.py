"""Deterministic file discovery with explicit directory exclusions."""

from __future__ import annotations

import os
from collections.abc import Collection, Iterator
from pathlib import Path


def read_source(path: Path) -> str:
    """Read UTF-8 source while preserving its line-ending bytes."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def walk_files(root: Path, *, excluded: Collection[str] = ()) -> Iterator[Path]:
    """Visit files without traversing excluded or symbolic directories.

    Symbolic directory entries reach the caller so its source policy can
    reject them. An unreadable directory fails enumeration.
    """

    def unreadable(error: OSError) -> None:
        raise error

    for directory, children, names in os.walk(root, onerror=unreadable):
        children[:] = sorted(name for name in children if name not in excluded)
        parent = Path(directory)
        for name in children:
            path = parent / name
            if path.is_symlink():
                yield path
        for name in sorted(names):
            if name not in excluded:
                yield parent / name
