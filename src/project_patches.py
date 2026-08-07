"""Validated, owner-approved project patch transactions.

Qwen may propose bytes, but only this module can change a project checkout.
It deliberately supports a small surface: complete UTF-8 contents for an
existing regular file or a new regular file in an existing directory.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from core.database import ProjectChangeSet, utcnow_naive
from src.protected_workspace import WorkspaceSnapshot, snapshot_workspace


PATCH_VERSION = 1
MAX_PATCH_FILES = 20
MAX_FILE_BYTES = 512 * 1024
MAX_PATCH_BYTES = 1024 * 1024
MAX_PATH_LENGTH = 1024
PATCH_START = "<odysseus-change-set>"
PATCH_END = "</odysseus-change-set>"
_ENVELOPE = re.compile(
    re.escape(PATCH_START) + r"\s*(\{.*?\})\s*" + re.escape(PATCH_END),
    re.DOTALL,
)
_project_locks_guard = threading.Lock()
_project_locks: dict[str, threading.Lock] = {}


class PatchError(RuntimeError):
    def __init__(self, code: str, detail: str, status_code: int = 400):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class PreparedProposal:
    answer: str
    summary: str
    rationale: str
    base_git_revision: str
    payload: dict[str, Any]

    @property
    def public_files(self) -> list[dict[str, Any]]:
        return [
            {
                "path": item["path"],
                "operation": item["operation"],
                "added": item["added"],
                "removed": item["removed"],
            }
            for item in self.payload["changes"]
        ]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PatchError("proposal_invalid", "Project revision could not be read", 409) from exc
    return result.stdout.strip()


def _project_lock(project_id: str) -> threading.Lock:
    with _project_locks_guard:
        return _project_locks.setdefault(project_id, threading.Lock())


def _dirty_tracked_targets(root: Path, changes: list[dict[str, Any]]) -> bool:
    tracked = [change["path"] for change in changes if change["operation"] == "update"]
    if not tracked:
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--", *tracked],
            check=True, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PatchError("apply_failed", "Affected-file status could not be verified", 500) from exc
    return bool(result.stdout)


def _safe_target(root: Path, raw_path: object, *, must_exist: bool | None) -> tuple[str, Path]:
    if not isinstance(raw_path, str) or not raw_path or len(raw_path) > MAX_PATH_LENGTH:
        raise PatchError("path_denied", "Patch path is invalid")
    if "\\" in raw_path or "\x00" in raw_path:
        raise PatchError("path_denied", "Patch path is invalid")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise PatchError("path_denied", "Patch path is outside the project")
    if relative.parts[0].casefold() == ".git":
        raise PatchError("path_denied", "Git internals cannot be patched")

    root = root.resolve(strict=True)
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise PatchError("path_denied", "Patch parent directory does not exist") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PatchError("path_denied", "Patch path contains an unsafe component")
    target = current / relative.name
    try:
        info = target.lstat()
        exists = True
    except FileNotFoundError:
        info = None
        exists = False
    if must_exist is True and not exists:
        raise PatchError("patch_stale", "A patch target no longer exists", 409)
    if must_exist is False and exists:
        raise PatchError("patch_stale", "A new-file target now exists", 409)
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)):
        raise PatchError("unsupported_file", "Only regular text files can be patched")
    return relative.as_posix(), target


def _read_text_file(target: Path) -> tuple[bytes, str]:
    data = target.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise PatchError("proposal_too_large", "A patch target exceeds the file-size limit", 413)
    if b"\x00" in data:
        raise PatchError("unsupported_file", "Binary files cannot be patched")
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchError("unsupported_file", "Only UTF-8 text files can be patched") from exc


def _text_bytes(value: object) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise PatchError("proposal_invalid", "Proposed file content must be text")
    data = value.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise PatchError("proposal_too_large", "A proposed file exceeds the file-size limit", 413)
    if "\x00" in value:
        raise PatchError("unsupported_file", "Binary content cannot be proposed")
    return value, data


def _unified_diff(path: str, old: str, new: str, operation: str) -> tuple[str, int, int]:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    before = f"a/{path}" if operation == "update" else "/dev/null"
    after = f"b/{path}"
    lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=before, tofile=after, lineterm=""))
    rendered = "\n".join(line.rstrip("\n") for line in lines)
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return rendered, added, removed


def proposal_instructions() -> str:
    """Machine-readable response contract appended only to patch turns."""
    return f"""# Reviewed patch proposal
You remain read-only. Inspect every affected existing file before proposing a change. Do not call Edit, Write, Shell, web, MCP, memory, skills, or extensions.
End with exactly one envelope in this form:
{PATCH_START}
{{"version":1,"summary":"short summary","rationale":"why this is coherent","changes":[{{"operation":"update","path":"relative/file.md","content":"complete new UTF-8 contents"}}]}}
{PATCH_END}
Allowed operations are update and create. Include complete file contents, never a partial diff. Do not include absolute paths, deletion, rename, binary data, permissions, commands, or more than {MAX_PATCH_FILES} files. Put ordinary user-facing explanation before the envelope."""


def prepare_proposal(workspace: Path, raw_answer: str) -> PreparedProposal:
    matches = list(_ENVELOPE.finditer(raw_answer or ""))
    if len(matches) != 1:
        raise PatchError("proposal_invalid", "Qwen did not return one valid patch proposal")
    try:
        envelope = json.loads(matches[0].group(1))
    except (json.JSONDecodeError, TypeError) as exc:
        raise PatchError("proposal_invalid", "Qwen returned malformed patch data") from exc
    if not isinstance(envelope, dict) or envelope.get("version") != PATCH_VERSION:
        raise PatchError("proposal_invalid", "Patch proposal version is unsupported")
    summary = str(envelope.get("summary") or "").strip()
    rationale = str(envelope.get("rationale") or "").strip()
    changes = envelope.get("changes")
    if not summary or len(summary) > 500 or len(rationale) > 4000:
        raise PatchError("proposal_invalid", "Patch summary or rationale is invalid")
    if not isinstance(changes, list) or not changes or len(changes) > MAX_PATCH_FILES:
        code = "proposal_too_large" if isinstance(changes, list) and len(changes) > MAX_PATCH_FILES else "proposal_invalid"
        raise PatchError(code, "Patch must contain between 1 and 20 files", 413 if code == "proposal_too_large" else 400)

    root = workspace.resolve(strict=True)
    if not (root / ".git").exists():
        raise PatchError("proposal_invalid", "Project is not a Git checkout", 409)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for candidate in changes:
        if not isinstance(candidate, dict) or set(candidate) != {"operation", "path", "content"}:
            raise PatchError("proposal_invalid", "Patch operations contain unsupported fields")
        operation = candidate.get("operation")
        if operation not in {"update", "create"}:
            raise PatchError("proposal_invalid", "Patch operation is unsupported")
        path, target = _safe_target(root, candidate.get("path"), must_exist=(operation == "update"))
        if path in seen:
            raise PatchError("proposal_invalid", "Patch contains the same file more than once")
        seen.add(path)
        content, proposed_bytes = _text_bytes(candidate.get("content"))
        total += len(proposed_bytes)
        if total > MAX_PATCH_BYTES:
            raise PatchError("proposal_too_large", "Patch exceeds the total content limit", 413)
        if operation == "update":
            base_bytes, base_text = _read_text_file(target)
            base_sha = _sha256(base_bytes)
            if base_bytes == proposed_bytes:
                raise PatchError("proposal_invalid", "Patch contains an unchanged file")
        else:
            base_text, base_sha = "", None
        diff, added, removed = _unified_diff(path, base_text, content, operation)
        normalized.append({
            "operation": operation,
            "path": path,
            "content": content,
            "base_sha256": base_sha,
            "proposed_sha256": _sha256(proposed_bytes),
            "diff": diff,
            "added": added,
            "removed": removed,
        })
    answer = (raw_answer[:matches[0].start()] + raw_answer[matches[0].end():]).strip()
    return PreparedProposal(
        answer=answer or summary,
        summary=summary,
        rationale=rationale,
        base_git_revision=_git_head(root),
        payload={"version": PATCH_VERSION, "changes": normalized},
    )


def serialize_change_set(row: ProjectChangeSet, *, include_diff: bool = False) -> dict[str, Any]:
    proposal = json.loads(row.proposal_json)
    result = json.loads(row.result_json or "{}")
    files = []
    for change in proposal.get("changes", []):
        item = {
            "path": change["path"], "operation": change["operation"],
            "added": change.get("added", 0), "removed": change.get("removed", 0),
        }
        if include_diff:
            item["diff"] = change.get("diff", "")
        files.append(item)
    return {
        "id": row.id,
        "project_id": row.project_id,
        "session_id": row.session_id,
        "source_message_id": row.source_message_id,
        "model": row.model,
        "revision": row.revision,
        "status": row.status,
        "summary": row.summary,
        "rationale": row.rationale,
        "files": files,
        "failure_code": row.failure_code,
        "integrity": result.get("integrity"),
        "process": result.get("process", []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "rejected_at": row.rejected_at.isoformat() if row.rejected_at else None,
        "rolled_back_at": row.rolled_back_at.isoformat() if row.rolled_back_at else None,
    }


def _current_bytes(root: Path, change: dict[str, Any], *, proposed: bool = False) -> bytes | None:
    path, target = _safe_target(root, change["path"], must_exist=None)
    del path
    if not target.exists():
        return None
    data, _ = _read_text_file(target)
    expected = change["proposed_sha256"] if proposed else change.get("base_sha256")
    if _sha256(data) != expected:
        raise PatchError(
            "rollback_conflict" if proposed else "patch_stale",
            "A patch target changed after review",
            409,
        )
    return data


def _write_atomic(target: Path, content: bytes, *, mode: int = 0o600) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".odysseus-patch-", dir=str(target.parent))
    try:
        os.fchmod(fd, mode & 0o666)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _restore(root: Path, rollback: dict[str, Any], changes: list[dict[str, Any]]) -> None:
    by_path = {item["path"]: item for item in rollback["files"]}
    for change in reversed(changes):
        _, target = _safe_target(root, change["path"], must_exist=None)
        before = by_path[change["path"]]
        if before["existed"]:
            _write_atomic(target, before["content"].encode("utf-8"), mode=int(before["mode"]))
        elif target.exists():
            target.unlink()


def _verify_only_targets_changed(before: WorkspaceSnapshot, after: WorkspaceSnapshot, targets: set[str]) -> bool:
    if before.head != after.head:
        return False
    before_other = {key: value for key, value in before.files.items() if key not in targets}
    after_other = {key: value for key, value in after.files.items() if key not in targets}
    return before_other == after_other


def apply_change_set(db, row: ProjectChangeSet, workspace: Path, *, expected_revision: int) -> dict[str, Any]:
    if row.revision != expected_revision or row.status != "proposed":
        raise PatchError("apply_conflict", "Patch is no longer awaiting this approval", 409)
    root = workspace.resolve(strict=True)
    proposal = json.loads(row.proposal_json)
    changes = proposal["changes"]
    with _project_lock(row.project_id):
        before = snapshot_workspace(root)
        rollback_files = []
        try:
            for change in changes:
                base = _current_bytes(root, change)
                rollback_files.append({
                    "path": change["path"], "existed": base is not None,
                    "content": base.decode("utf-8") if base is not None else "",
                    "mode": stat.S_IMODE((root / change["path"]).stat().st_mode) if base is not None else 0o600,
                })
            if _dirty_tracked_targets(root, changes):
                raise PatchError("apply_conflict", "An affected file has uncommitted changes", 409)
        except PatchError as exc:
            row.status = "stale"
            row.failure_code = exc.code
            row.revision += 1
            row.result_json = json.dumps({"integrity": "unchanged", "process": [
                {"label": f"Validate: {len(changes)} proposed files", "status": "failed"},
            ]}, separators=(",", ":"))
            db.commit()
            raise
        rollback = {"version": PATCH_VERSION, "files": rollback_files}
        row.rollback_json = json.dumps(rollback, ensure_ascii=False, separators=(",", ":"))
        row.status = "applying"
        row.approved_at = utcnow_naive()
        row.failure_code = None
        row.revision += 1
        row.result_json = json.dumps({"process": [
            {"label": f"Validate: {len(changes)} proposed files", "status": "completed"},
            {"label": f"Apply approved patch: {len(changes)} files", "status": "running"},
        ]}, separators=(",", ":"))
        db.commit()

        written: list[dict[str, Any]] = []
        try:
            # Revalidate all targets after the durable journal exists and again
            # immediately before each replacement.
            for change in changes:
                _current_bytes(root, change)
            for change in changes:
                _current_bytes(root, change)
                _, target = _safe_target(root, change["path"], must_exist=(change["operation"] == "update"))
                mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
                _write_atomic(target, change["content"].encode("utf-8"), mode=mode)
                written.append(change)
            after = snapshot_workspace(root)
            targets = {change["path"] for change in changes}
            if not _verify_only_targets_changed(before, after, targets):
                raise PatchError("verification_failed", "Files outside the approved patch changed", 409)
            for change in changes:
                data = _current_bytes(root, change, proposed=True)
                if data is None:
                    raise PatchError("verification_failed", "An approved file was not written", 409)
        except BaseException as exc:
            try:
                _restore(root, rollback, written)
            except Exception:
                row.status = "apply_failed"
                row.failure_code = "rollback_failed"
                row.revision += 1
                db.commit()
                raise PatchError("rollback_failed", "Patch failed and automatic rollback could not be verified", 500) from exc
            row.status = "stale" if isinstance(exc, PatchError) and exc.code == "patch_stale" else "apply_failed"
            row.failure_code = exc.code if isinstance(exc, PatchError) else "apply_failed"
            row.revision += 1
            row.result_json = json.dumps({"integrity": "restored", "process": [
                {"label": f"Validate: {len(changes)} proposed files", "status": "completed"},
                {"label": f"Apply approved patch: {len(changes)} files", "status": "failed"},
                {"label": "Restore pre-approval contents", "status": "completed"},
            ]}, separators=(",", ":"))
            db.commit()
            if isinstance(exc, PatchError):
                raise
            raise PatchError("apply_failed", "Approved patch could not be applied", 500) from exc

        now = utcnow_naive()
        row.status = "applied"
        row.applied_at = now
        row.verified_at = now
        row.revision += 1
        row.result_json = json.dumps({"integrity": "verified", "process": [
            {"label": f"Validate: {len(changes)} proposed files", "status": "completed"},
            {"label": f"Apply approved patch: {len(changes)} files", "status": "completed"},
            {"label": "Verify: expected project diff", "status": "completed"},
        ]}, separators=(",", ":"))
        db.commit()
        db.refresh(row)
        return serialize_change_set(row, include_diff=True)


def reject_change_set(db, row: ProjectChangeSet, *, expected_revision: int) -> dict[str, Any]:
    if row.revision != expected_revision or row.status != "proposed":
        raise PatchError("apply_conflict", "Patch is no longer awaiting review", 409)
    row.status = "rejected"
    row.rejected_at = utcnow_naive()
    row.revision += 1
    db.commit()
    db.refresh(row)
    return serialize_change_set(row, include_diff=True)


def rollback_change_set(db, row: ProjectChangeSet, workspace: Path, *, expected_revision: int) -> dict[str, Any]:
    if row.revision != expected_revision or row.status != "applied" or not row.rollback_json:
        raise PatchError("rollback_conflict", "Patch is not eligible for rollback", 409)
    root = workspace.resolve(strict=True)
    proposal = json.loads(row.proposal_json)
    changes = proposal["changes"]
    rollback = json.loads(row.rollback_json)
    with _project_lock(row.project_id):
        for change in changes:
            _current_bytes(root, change, proposed=True)
        try:
            _restore(root, rollback, changes)
            for before in rollback["files"]:
                _, target = _safe_target(root, before["path"], must_exist=None)
                if before["existed"]:
                    data, _ = _read_text_file(target)
                    if _sha256(data) != _sha256(before["content"].encode("utf-8")):
                        raise PatchError("rollback_failed", "Rollback verification failed", 500)
                elif target.exists():
                    raise PatchError("rollback_failed", "Rollback verification failed", 500)
        except PatchError:
            raise
        except Exception as exc:
            raise PatchError("rollback_failed", "Patch rollback failed", 500) from exc
        row.status = "rolled_back"
        row.rolled_back_at = utcnow_naive()
        row.failure_code = None
        row.revision += 1
        result = json.loads(row.result_json or "{}")
        result["integrity"] = "restored"
        result.setdefault("process", []).append({"label": "Roll back approved patch", "status": "completed"})
        row.result_json = json.dumps(result, separators=(",", ":"))
        db.commit()
        db.refresh(row)
        return serialize_change_set(row, include_diff=True)


def recover_applying_change_sets(db) -> int:
    """Restore interrupted transactions when every target is recognizable."""
    recovered = 0
    rows = db.query(ProjectChangeSet).filter(ProjectChangeSet.status == "applying").all()
    from core.database import Project
    for row in rows:
        project = db.query(Project).filter(Project.id == row.project_id, Project.owner == row.owner).first()
        if not project or not row.rollback_json:
            row.status, row.failure_code = "apply_failed", "rollback_failed"
            row.revision += 1
            continue
        try:
            root = Path(project.workspace_root).resolve(strict=True)
            proposal = json.loads(row.proposal_json)
            rollback = json.loads(row.rollback_json)
            for change in proposal["changes"]:
                _, target = _safe_target(root, change["path"], must_exist=None)
                if target.exists():
                    data, _ = _read_text_file(target)
                    digest = _sha256(data)
                    if digest not in {change.get("base_sha256"), change["proposed_sha256"]}:
                        raise PatchError("rollback_conflict", "Interrupted patch target changed", 409)
            _restore(root, rollback, proposal["changes"])
            row.status, row.failure_code = "apply_failed", "apply_failed"
            row.revision += 1
            row.result_json = json.dumps({"integrity": "restored", "process": [
                {"label": "Recover interrupted patch transaction", "status": "completed"},
            ]}, separators=(",", ":"))
            recovered += 1
        except Exception:
            row.status, row.failure_code = "apply_failed", "rollback_failed"
            row.revision += 1
    db.commit()
    return recovered
