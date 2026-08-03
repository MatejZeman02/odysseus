"""Tamper-evident wrapper for external read-only evaluation workspaces."""

from __future__ import annotations

import hashlib
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


def snapshot_workspace(root: Path) -> WorkspaceSnapshot:
    root = root.resolve(strict=True)
    if not (root / ".git").exists():
        raise ValueError("protected workspace must be a Git checkout")
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1"], check=True, capture_output=True, text=True).stdout
    files = {}
    for path in root.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
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
