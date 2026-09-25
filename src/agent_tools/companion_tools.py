"""Tools that exist only inside Companion homes.

``project_shell`` runs one command in the offline project sandbox. ``update_memory``
changes one item of the home brief. The file-edit tools (``write_file``,
``edit_file``, ``apply_patch``) keep their ordinary names and arguments, but in
a project home the executor sends them here, so every change is recorded as a
project change set that the owner can undo.

The executor has already checked the owner's grant for each tool before any
of this runs. These handlers check what only they can know: that the chat is
still bound to a usable project and that the sandbox works.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from src.tool_execution import get_active_workspace, vet_workspace


def _session_row(session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    from core.database import Project, Session as DbSession, SessionLocal

    db = SessionLocal()
    try:
        row = db.query(
            DbSession.owner, DbSession.scope_kind, DbSession.project_id,
            DbSession.model, DbSession.endpoint_id,
        ).filter(DbSession.id == session_id).first()
        if not row:
            return None
        owner, scope_kind, project_id, model, endpoint_id = row
        workspace_root = ""
        if project_id:
            project = db.query(Project.workspace_root).filter(
                Project.id == project_id, Project.owner == owner,
            ).first()
            workspace_root = str(project[0] or "") if project else ""
        return {
            "owner": owner, "scope_kind": scope_kind or "general", "project_id": project_id,
            "model": model or "", "endpoint_id": endpoint_id or "",
            "workspace_root": workspace_root,
        }
    finally:
        db.close()


def _owned_session(session_id: str | None, owner: str | None) -> dict[str, Any] | None:
    """Return the chat's row only when the run belongs to its owner.

    Without sign-in a run carries no owner and its chats are stored under the
    reserved local owner, so both sides go through the same resolution. With
    sign-in on, a run without an owner matches nothing.
    """
    from src.owner_identity import effective_storage_owner

    session = _session_row(session_id)
    expected = effective_storage_owner(owner)
    if not session or expected is None or session["owner"] != expected:
        return None
    return session


def _project_workspace(session: dict[str, Any] | None) -> str | None:
    """Return the turn's workspace only when it is this chat's own project.

    The chat route binds the stored checkout, but the executor is shared.
    Comparing inodes means no other caller can point a project tool at a
    different directory, even one with the same spelling.
    """
    from src.path_identity import PathIdentityError, pin_directory

    workspace = get_active_workspace()
    if not session or session.get("scope_kind") != "project" or not session.get("workspace_root"):
        return None
    if not workspace or vet_workspace(workspace) != workspace:
        return None
    try:
        bound = pin_directory(Path(workspace))
        stored = pin_directory(Path(session["workspace_root"]).expanduser())
    except PathIdentityError:
        return None
    if (bound.st_dev, bound.st_ino) != (stored.st_dev, stored.st_ino):
        return None
    return str(bound.path)


def _args(content: str) -> dict[str, Any]:
    stripped = (content or "").strip()
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


class ShellTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src import project_shell

        args = _args(content)
        command = str(args.get("command") if args else content or "").strip()
        if not command:
            return {"error": "project_shell: command required", "exit_code": 1}
        if len(command) > project_shell.MAX_COMMAND_CHARS:
            return {"error": "project_shell: command is too long. Write a script with edit tools, then run it.", "exit_code": 1}
        try:
            timeout = int(args.get("timeout") or project_shell.DEFAULT_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            timeout = project_shell.DEFAULT_TIMEOUT_SECONDS
        session = await asyncio.to_thread(_owned_session, ctx.get("session_id"), ctx.get("owner"))
        workspace = _project_workspace(session)
        if not workspace:
            return {"error": "project_shell: this chat has no usable project checkout.", "exit_code": 1}
        try:
            result = await asyncio.to_thread(project_shell.run, workspace, command, timeout=timeout)
        except project_shell.ShellUnavailable as error:
            return {"error": f"project_shell: {project_shell.unavailable_reason(error.code)}", "exit_code": 1}
        output = result.output
        if result.timed_out:
            output += f"\n[command stopped after {timeout} s]"
        return {
            "output": output or "(no output)",
            "exit_code": result.exit_code,
            "sandboxed": True,
            "duration_ms": result.duration_ms,
        }


class UpdateMemoryTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.home_brief import BriefAlreadyHolds, BriefEditError, agent_edit

        args = _args(content)
        if not args:
            return {"error": "update_memory: arguments must be a JSON object", "exit_code": 1}
        session = await asyncio.to_thread(_owned_session, ctx.get("session_id"), ctx.get("owner"))
        if not session:
            return {"error": "update_memory: this chat has no memory brief.", "exit_code": 1}
        try:
            result = await asyncio.to_thread(
                agent_edit,
                owner=session["owner"],
                scope_kind=session["scope_kind"],
                session_id=ctx.get("session_id"),
                project_id=session["project_id"],
                action=str(args.get("action") or "").strip(),
                section=str(args.get("section") or "").strip(),
                text=args.get("text", ""),
                old_text=args.get("old_text", ""),
            )
        except BriefAlreadyHolds:
            # Not a failure: the owner's fact is saved, and a red result
            # would only invite the model to try again.
            return {"output": "Already in memory, nothing changed.", "exit_code": 0}
        except BriefEditError as error:
            return {"error": f"update_memory: {error}", "exit_code": 1}
        except Exception:
            return {"error": "update_memory: memory could not be saved. Tell the owner.", "exit_code": 1}
        return {
            "output": f"Memory updated ({args.get('action')} in {args.get('section')}). Saved as revision {result.revision}.",
            "exit_code": 0,
        }


def _project_path(raw: Any, workspace: str) -> str:
    """Turn a model-supplied path into a project-relative POSIX path.

    The model sees the checkout as ``/workspace`` inside ``project_shell`` and as its
    real absolute path elsewhere, so both prefixes are accepted. The
    transaction re-validates the result, this only normalizes spelling.
    """
    value = str(raw or "").strip()
    if not value:
        raise ValueError("path required")
    for prefix in ("/workspace/", workspace.rstrip(os.sep) + os.sep):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.startswith("./"):
        value = value[2:]
    if os.path.isabs(value):
        raise ValueError("path must be inside the project")
    return PurePosixPath(value).as_posix()


def _write_file_changes(content: str, workspace: str, read) -> tuple[list[dict], str]:
    stripped = (content or "").strip()
    path, body = "", ""
    if stripped.startswith("{"):
        args = _args(stripped)
        path, body = args.get("path", ""), str(args.get("content", ""))
    if not path:
        lines = (content or "").split("\n", 1)
        path, body = lines[0].strip(), (lines[1] if len(lines) > 1 else "")
    relative = _project_path(path, workspace)
    current = read(relative)
    operation = "create" if current is None else "update"
    return [{"operation": operation, "path": relative, "content": body}], f"Write {relative}"


def _edit_file_changes(content: str, workspace: str, read) -> tuple[list[dict], str]:
    args = _args(content)
    relative = _project_path(args.get("path"), workspace)
    old, new = args.get("old_string", ""), args.get("new_string", "")
    if not isinstance(old, str) or not isinstance(new, str) or old == "":
        raise ValueError("old_string required (use write_file to create a file)")
    if old == new:
        raise ValueError("old_string and new_string are identical")
    current = read(relative)
    if current is None:
        raise ValueError(f"{relative}: not found (use write_file to create it)")
    count = current.count(old)
    if count == 0:
        raise ValueError(f"old_string not found in {relative}. Read the file and match it exactly.")
    if count > 1 and not args.get("replace_all"):
        raise ValueError(
            f"old_string is not unique in {relative} ({count} matches). Add surrounding context or set replace_all=true."
        )
    updated = current.replace(old, new) if args.get("replace_all") else current.replace(old, new, 1)
    return [{"operation": "update", "path": relative, "content": updated}], f"Edit {relative}"


def _apply_patch_changes(content: str, workspace: str, read) -> tuple[list[dict], str]:
    from src.agent_tools.filesystem_tools import _apply_patch_hunks, _parse_agent_patch

    patch_text = content or ""
    stripped = patch_text.strip()
    if stripped.startswith("{"):
        args = _args(stripped)
        patch_text = str(args.get("patch_text") or args.get("patchText") or args.get("patch") or "")
    if not patch_text.strip():
        raise ValueError("patch_text required")
    changes = []
    for op in _parse_agent_patch(patch_text):
        relative = _project_path(op["path"], workspace)
        current = read(relative)
        if op["kind"] == "delete":
            raise ValueError(
                f"{relative}: deleting files is not supported by recorded edits. Ask the owner to delete it."
            )
        if op["kind"] == "add":
            if current is not None:
                raise ValueError(f"{relative}: already exists")
            changes.append({"operation": "create", "path": relative, "content": op["content"]})
            continue
        if current is None:
            raise ValueError(f"{relative}: not found")
        changes.append({
            "operation": "update", "path": relative,
            "content": _apply_patch_hunks(current, op["hunks"], relative),
        })
    names = ", ".join(change["path"] for change in changes[:3])
    more = f" and {len(changes) - 3} more" if len(changes) > 3 else ""
    return changes, f"Patch {names}{more}"


_EDIT_PARSERS = {
    "write_file": _write_file_changes,
    "edit_file": _edit_file_changes,
    "apply_patch": _apply_patch_changes,
}


def _apply(tool: str, content: str, session: dict, session_id: str, workspace: str) -> dict:
    from core.database import SessionLocal
    from src.project_patches import PatchError, apply_agent_edit, read_project_text

    root = Path(workspace)
    try:
        changes, summary = _EDIT_PARSERS[tool](content, workspace, lambda path: read_project_text(root, path))
    except PatchError as error:
        return {"error": f"{tool}: {error.detail}", "exit_code": 1}
    except (ValueError, UnicodeDecodeError) as error:
        return {"error": f"{tool}: {error}", "exit_code": 1}
    changes = [change for change in changes if not (
        change["operation"] == "update" and read_project_text(root, change["path"]) == change["content"]
    )]
    if not changes:
        return {"output": f"{tool}: nothing to change, the file already has that content.", "exit_code": 0}
    db = SessionLocal()
    try:
        record = apply_agent_edit(
            db,
            owner=session["owner"],
            project_id=session["project_id"],
            session_id=session_id,
            model=session["model"],
            endpoint_id=session["endpoint_id"],
            workspace=root,
            summary=summary,
            changes=changes,
        )
    except PatchError as error:
        return {"error": f"{tool}: {error.detail}", "exit_code": 1}
    finally:
        db.close()
    files = record.get("files") or []
    added = sum(int(item.get("added") or 0) for item in files)
    removed = sum(int(item.get("removed") or 0) for item in files)
    diff_text = "\n".join(str(item.get("diff") or "") for item in files if item.get("diff"))
    names = ", ".join(str(item.get("path")) for item in files)
    return {
        "output": (
            f"{summary}: applied and verified ({len(files)} file{'s' if len(files) != 1 else ''}, "
            f"+{added}/-{removed}). Recorded as a project change the owner can undo: {names}"
        ),
        "exit_code": 0,
        "diff": {
            "text": diff_text,
            "added": added,
            "removed": removed,
            "new_file": any(item.get("operation") == "create" for item in files),
            "file": names if len(files) == 1 else "patch",
        },
        "project_change_set": {"id": record.get("id"), "revision": record.get("revision")},
    }


async def run_project_edit(tool: str, content: str, *, session_id: str | None, owner: str | None) -> dict:
    """Route one native file edit in a project home through the transaction."""
    session = await asyncio.to_thread(_session_row, session_id)
    if not session or session["scope_kind"] != "project" or not session["project_id"]:
        return {"error": f"{tool}: edits need a project chat.", "exit_code": 1}
    if not await asyncio.to_thread(_owned_session, session_id, owner):
        return {"error": f"{tool}: this chat belongs to another owner.", "exit_code": 1}
    workspace = _project_workspace(session)
    if not workspace:
        return {"error": f"{tool}: this chat has no usable project checkout.", "exit_code": 1}
    return await asyncio.to_thread(_apply, tool, content, session, session_id, workspace)
