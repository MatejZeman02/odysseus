"""Tamper-evident wrapper for external read-only evaluation workspaces."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")


class WorkspaceMutationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    head: str
    status: str
    files: dict[str, str]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git_visible_paths(root: Path) -> list[Path]:
    """Return tracked and non-ignored untracked paths from Git's own view.

    Runtime state inside a checkout (for example Odysseus ``data/`` and log
    files) is commonly ignored. Hashing it made the application mistake its
    own database/log writes for a Qwen workspace mutation. The integrity
    boundary should cover the project's Git-visible working tree instead.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    )
    return [root / os.fsdecode(raw) for raw in result.stdout.split(b"\0") if raw]


def _path_fingerprint(path: Path) -> str:
    """Fingerprint a Git-visible path without following project symlinks."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(mode):
        return f"symlink:{os.readlink(path)}"
    if stat.S_ISREG(mode):
        return f"file:{mode & 0o111:o}:{_file_sha256(path)}"
    return f"special:{stat.S_IFMT(mode):o}"


def snapshot_workspace(root: Path) -> WorkspaceSnapshot:
    # A project binding is a path grant.  Resolve only after proving that its
    # registered leaf remains a real directory; otherwise a later symlink swap
    # could make a read-only Qwen worker inspect an unrelated tree.
    candidate = Path(root)
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise ValueError("protected workspace is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("protected workspace must be a regular directory")
    root = candidate.resolve(strict=True)
    if not (root / ".git").exists():
        raise ValueError("protected workspace must be a Git checkout")
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1"], check=True, capture_output=True, text=True).stdout
    files = {
        str(path.relative_to(root)): _path_fingerprint(path)
        for path in _git_visible_paths(root)
    }
    return WorkspaceSnapshot(str(root), head, status, files)


def run_read_only(root: Path, operation: Callable[[], T]) -> tuple[T, WorkspaceSnapshot]:
    """Run caller-owned read work and prove checkout state did not change."""
    before = snapshot_workspace(root)
    try:
        result = operation()
    finally:
        after = snapshot_workspace(root)
        if after != before:
            raise WorkspaceMutationError("protected workspace changed during evaluation")
    return result, before
