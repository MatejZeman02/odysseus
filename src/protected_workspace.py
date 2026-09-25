"""Tamper-evident wrapper for external read-only evaluation workspaces."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from src.path_identity import pin_directory

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


def unborn_head(root: Path) -> bool:
    """Say whether HEAD names a branch that has no commit yet.

    ``rev-parse --verify --quiet HEAD`` exits 1 for that, but also for a ref
    file that is empty, corrupt or unreadable. Only a branch with neither a
    loose ref file nor a packed entry counts as unborn, so a damaged checkout
    still fails its checks.
    """
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)

    ref = git("symbolic-ref", "-q", "HEAD")
    name = ref.stdout.strip()
    if ref.returncode != 0 or not name.startswith("refs/heads/"):
        return False
    # A reftable repository keeps no ref files to look for.
    storage = git("config", "--get", "extensions.refStorage")
    if storage.returncode == 0 and storage.stdout.strip() not in {"", "files"}:
        return False
    loose = git("rev-parse", "--git-path", name)
    packed = git("rev-parse", "--git-path", "packed-refs")
    if loose.returncode != 0 or packed.returncode != 0:
        return False
    if os.path.lexists(root / loose.stdout.strip()):
        return False
    try:
        lines = (root / packed.stdout.strip()).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return True
    except (OSError, UnicodeDecodeError):
        return False
    return not any(line.split(" ", 1)[-1] == name for line in lines if not line.startswith(("#", "^")))


def snapshot_workspace(root: Path) -> WorkspaceSnapshot:
    # A project binding is a path grant, so the registered leaf must still be
    # the directory it was approved as; otherwise a symlink swap could make a
    # read-only Qwen worker inspect an unrelated tree.  ``PathIdentityError``
    # is a ``ValueError``, so existing callers keep their handling.
    root = pin_directory(root).path
    if not (root / ".git").exists():
        raise ValueError("protected workspace must be a Git checkout")
    # A new repository has no HEAD until its first commit. That is still a
    # state to compare against: a commit made meanwhile changes it.
    rev = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"], capture_output=True, text=True,
    )
    if rev.returncode != 0 and not (rev.returncode == 1 and unborn_head(root)):
        rev.check_returncode()
    head = rev.stdout.strip()
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
