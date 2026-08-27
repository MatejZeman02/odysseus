"""Brokered, workspace-only read commands for native companion chats.

The browser and model never provide shell text, mounts, or a host path.  The
current server-bound workspace is copied into a disposable, symlink-free input
tree, then the Podman broker receives already-tokenised argv vectors.
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Iterable

from src.computer_sandbox import ReadOnlyCommand, SandboxRunError, run_admitted_readonly_pipeline
from src.tool_execution import get_active_workspace, vet_workspace


_SKIP_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next",
})
# Source-control metadata is deliberately absent from the disposable input:
# project-controlled Git configuration, hooks, and filters must never become
# part of the command runtime.  Do not re-enable Git in the command broker
# until it has a dedicated, inert repository-metadata snapshot design.
_MAX_SNAPSHOT_FILES = 5_000
_MAX_SNAPSHOT_FILE_BYTES = 2 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_MAX_MODEL_OUTPUT = 48 * 1024


class _SnapshotLimit(RuntimeError):
    pass


class _SnapshotUnsafe(RuntimeError):
    pass


def _copy_regular_file(
    source: Path,
    target: Path,
    *,
    workspace_root: Path,
    remaining_bytes: int,
) -> int | None:
    """Copy one source descriptor without following a final-path symlink.

    A project is untrusted input to the snapshotter.  Checking
    ``Path.is_symlink()`` and subsequently copying by pathname leaves a small
    replacement window in which a file can become a symlink.  Open the source
    once with ``O_NOFOLLOW``, verify the opened descriptor is still a regular
    file inside the bound root, then copy bytes to a newly-created private
    destination.  The destination is never chmod'ed by pathname.
    """
    source_fd = target_fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            source_fd = os.open(source, flags)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise _SnapshotUnsafe("snapshot_symlink") from None
            raise
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise _SnapshotUnsafe("snapshot_non_regular")
        if source_stat.st_size > _MAX_SNAPSHOT_FILE_BYTES:
            return None
        if source_stat.st_size > remaining_bytes:
            raise _SnapshotLimit("snapshot_limited")
        # Fedora exposes the opened descriptor here.  Check the descriptor,
        # not the potentially changed source pathname, so an intermediate
        # directory swap cannot pull a sibling/home file into the snapshot.
        actual = os.path.realpath(f"/proc/self/fd/{source_fd}")
        root = os.path.realpath(workspace_root)
        try:
            inside_root = os.path.commonpath([actual, root]) == root
        except ValueError:
            inside_root = False
        if not inside_root:
            raise _SnapshotUnsafe("snapshot_outside_workspace")
        target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        bytes_left = source_stat.st_size
        while bytes_left:
            chunk = os.read(source_fd, min(1024 * 1024, bytes_left))
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                offset += os.write(target_fd, chunk[offset:])
            bytes_left -= len(chunk)
        return source_stat.st_size - bytes_left
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        if source_fd >= 0:
            os.close(source_fd)


def _copy_workspace_input(workspace: Path, destination: Path) -> tuple[int, int]:
    """Copy ordinary, bounded workspace files without following symlinks."""
    root_fd = -1
    try:
        root_info = workspace.lstat()
    except OSError as exc:
        raise ValueError("workspace_unavailable") from exc
    # ``resolve`` would erase a late replacement of the bound project leaf by
    # a symlink.  Reject it first; otherwise a snapshot could copy files from
    # an unintended directory before the contained read-only broker starts.
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("workspace_unavailable")
    try:
        root_fd = os.open(
            workspace,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_info = os.fstat(root_fd)
        # Check the pathname again after the no-follow directory descriptor is
        # held.  If an attacker swapped the workspace for a symlink between
        # lstat and open, this rejects the input rather than resolving the new
        # target by pathname.  Later walking uses the stable descriptor path.
        current_info = workspace.lstat()
        if (
            stat.S_ISLNK(current_info.st_mode)
            or not stat.S_ISDIR(opened_info.st_mode)
            or (current_info.st_dev, current_info.st_ino) != (opened_info.st_dev, opened_info.st_ino)
        ):
            raise ValueError("workspace_unavailable")
        root = Path(f"/proc/self/fd/{root_fd}")
        # The parent temporary directory is 0700 on the host. The mounted input
        # itself must be traversable by the image's unprivileged user, however,
        # otherwise direct reads appear to work while find/rg/ls mysteriously
        # fail. This visibility exists only inside the already-private temp tree
        # and the networkless container mount.
        destination.mkdir(mode=0o755)
        files = total = 0
        for current, dirs, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            dirs[:] = [
                name for name in dirs
                if name not in _SKIP_DIRECTORIES and not (current_path / name).is_symlink()
            ]
            for name in names:
                source = current_path / name
                if source.is_symlink() or not source.is_file():
                    continue
                try:
                    size = source.stat().st_size
                except OSError:
                    continue
                if size > _MAX_SNAPSHOT_FILE_BYTES:
                    continue
                if files >= _MAX_SNAPSHOT_FILES or total + size > _MAX_SNAPSHOT_BYTES:
                    raise _SnapshotLimit("snapshot_limited")
                relative = source.relative_to(root)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(target.parent, 0o755)
                try:
                    copied = _copy_regular_file(
                        source,
                        target,
                        workspace_root=root,
                        remaining_bytes=_MAX_SNAPSHOT_BYTES - total,
                    )
                except FileNotFoundError:
                    # The project changed while a snapshot was being prepared.
                    # Omitting a disappeared file is safe; a replacement with a
                    # link or an out-of-root descriptor is not.
                    continue
                if copied is None:
                    continue
                files += 1
                total += copied
        return files, total
    except OSError as exc:
        raise ValueError("workspace_unavailable") from exc
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _commands(content: str) -> tuple[ReadOnlyCommand, ...]:
    try:
        payload = json.loads((content or "").strip())
    except (TypeError, ValueError):
        raise SandboxRunError("command_denied") from None
    raw_commands = payload.get("commands") if isinstance(payload, dict) else None
    if not isinstance(raw_commands, list) or not raw_commands or len(raw_commands) > 4:
        raise SandboxRunError("command_denied")
    commands: list[ReadOnlyCommand] = []
    for raw in raw_commands:
        if not isinstance(raw, list) or not raw:
            raise SandboxRunError("command_denied")
        commands.append(ReadOnlyCommand(tuple(raw)))
    return tuple(commands)


def _display(commands: Iterable[ReadOnlyCommand]) -> str:
    """Return an unambiguous bounded description of the admitted pipeline.

    This is diagnostic display text rather than the shell transport.  It must
    still reflect the argv vectors accurately: a bare ``" ".join`` turns a
    single argument containing spaces into something that looks like several
    arguments.  Use the same POSIX quoting convention as the broker and make
    UI truncation explicit, so a reader never mistakes a preview for the full
    command.  The execution transport is deliberately *not* truncated.
    """
    preview = " | ".join(shlex.join(item.argv) for item in commands)
    return preview if len(preview) <= 512 else f"{preview[:511]}✂"


class SandboxedReadTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            commands = _commands(content)
        except SandboxRunError as error:
            return {"error": error.code, "exit_code": 1}
        workspace = get_active_workspace()
        if not workspace or vet_workspace(workspace) != workspace:
            return {
                "error": "Sandboxed read-only commands need an attached, approved working directory.",
                "exit_code": 1,
            }
        try:
            with tempfile.TemporaryDirectory(prefix="odysseus-sandbox-input-") as temporary:
                inputs = Path(temporary) / "inputs"
                files, size = await asyncio.to_thread(_copy_workspace_input, Path(workspace), inputs)
                result = await asyncio.to_thread(
                    run_admitted_readonly_pipeline, commands, input_dir=inputs,
                )
        except _SnapshotLimit:
            return {
                "error": "The working directory is too large for a safe sandbox snapshot. Narrow the task or use the structured file tools.",
                "exit_code": 1,
            }
        except _SnapshotUnsafe:
            return {
                "error": "The working directory changed unsafely while its sandbox snapshot was being prepared. Try again after it is stable.",
                "exit_code": 1,
            }
        except SandboxRunError as error:
            return {"error": error.code, "exit_code": 1}
        except (OSError, ValueError):
            return {"error": "The working directory could not be prepared for sandboxed inspection.", "exit_code": 1}
        output = result.output[:_MAX_MODEL_OUTPUT]
        if len(result.output) > _MAX_MODEL_OUTPUT:
            output += "\n… [sandbox output truncated]"
        return {
            "output": output,
            "exit_code": 0,
            "sandboxed": True,
            "snapshot_files": files,
            "snapshot_bytes": size,
            "duration_ms": result.duration_ms,
            "command_preview": _display(commands),
        }
