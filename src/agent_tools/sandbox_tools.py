"""Brokered, workspace-only read commands for native companion chats.

The browser and model never provide shell text, mounts, or a host path.  The
current server-bound workspace is copied into a disposable, symlink-free input
tree, then the Podman broker receives already-tokenised argv vectors.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from src.computer_sandbox import ReadOnlyCommand, SandboxRunError, run_admitted_readonly_pipeline
from src.tool_execution import get_active_workspace, vet_workspace


_SKIP_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next",
})
_MAX_SNAPSHOT_FILES = 5_000
_MAX_SNAPSHOT_FILE_BYTES = 2 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_MAX_MODEL_OUTPUT = 48 * 1024


class _SnapshotLimit(RuntimeError):
    pass


def _copy_workspace_input(workspace: Path, destination: Path) -> tuple[int, int]:
    """Copy ordinary, bounded workspace files without following symlinks."""
    root = workspace.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("workspace_unavailable")
    destination.mkdir(mode=0o700)
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
            # copyfile follows no symlink because source was checked directly
            # above and the destination is a private new tree.
            shutil.copyfile(source, target, follow_symlinks=False)
            os.chmod(target, 0o600)
            files += 1
            total += size
    return files, total


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
    # Only a UI label. Validation happens again in the Podman broker.
    return " | ".join(" ".join(item.argv) for item in commands)[:512]


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
