"""G2D-1 Computer Help routes: safe host observations, not host execution."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from core.database import ComputerTaskRoot, Session as DbSession, SessionLocal, utcnow_naive
from core.models import ChatMessage
from routes.g1_continuity_routes import _owner
from src.companion_capabilities import SYSTEM_OBSERVE, normalize
from src.computer_observe import ObservationError, collect_observations
from src.computer_sandbox import SandboxQualificationError, qualify_containment, readiness
from src.companion_memory import CompanionMemoryStore, MemoryScopeError
from src.continuity.contracts import DeviceProfileV1
from src.continuity.store import ScopeConflictError


class ObserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    categories: list[str] = Field(default_factory=list, max_length=5)


class TaskRootCreate(BaseModel):
    """Browser requests a label/path; the server owns validation and storage."""
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)


class TaskRootRetire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)


_TASK_ROOT_DENIED_NAMES = frozenset({
    ".aws", ".config", ".docker", ".gnupg", ".kube", ".pki", ".ssh", ".password-store",
})


def _observation_session(owner: str, session_id: str) -> str:
    """Return the scope of a session allowed to inspect safe host facts.

    Computer Help retains the capability by default.  Personal, project, and
    ordinary chats may use the same broker only after their owner explicitly
    enables the fixed ``system_observe`` grant.  This is deliberately not a
    generic host-shell exception.
    """
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
        if not row:
            raise HTTPException(404, "Session was not found")
        scope_kind = row.scope_kind or "general"
        grants = normalize(getattr(row, "capability_grants", None), scope_kind=scope_kind)
        if not grants[SYSTEM_OBSERVE]:
            raise HTTPException(409, "Enable System inspection in Chat capabilities first")
        return scope_kind
    finally:
        db.close()


def _computer_session(owner: str, session_id: str) -> None:
    """Keep root registration bound to the owner's Computer Help home."""
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
        if not row:
            raise HTTPException(404, "Computer Help session was not found")
        if (row.scope_kind or "general") != "computer":
            raise HTTPException(409, "Task roots can be managed only from Computer Help")
    finally:
        db.close()


def _safe_task_root(path: str) -> Path:
    """Resolve a user-owned, dedicated directory without creating it.

    The task executor is intentionally not implemented here.  Its future use
    must repeat this validation and reject symlink changes, so this registry
    can never turn a stale path into a persistent authority grant.
    """
    raw = Path(str(path or "").strip()).expanduser()
    if not raw.is_absolute() or raw.is_symlink():
        raise ValueError("Choose an existing dedicated folder inside your home directory")
    try:
        resolved = raw.resolve(strict=True)
        home = Path.home().resolve(strict=True)
        relative = resolved.relative_to(home)
        metadata = resolved.stat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Choose an existing dedicated folder inside your home directory") from exc
    if relative == Path(".") or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Choose an existing dedicated folder inside your home directory")
    # A future task broker must never inherit a root that lives below a hidden
    # user-data directory.  The short explicit list documents the most common
    # credential/config locations, while the general hidden-directory rule
    # also closes paths such as ``~/.mozilla`` and ``~/.local/share/keyrings``
    # that are not safe write targets for Computer Help.
    if any(part in _TASK_ROOT_DENIED_NAMES or part.startswith(".") for part in relative.parts):
        raise ValueError("Choose a visible task folder outside protected credential and configuration directories")
    current_uid = getattr(os, "getuid", lambda: None)()
    if current_uid is not None and metadata.st_uid != current_uid:
        raise ValueError("Choose a folder owned by the current desktop user")
    return resolved


def _task_root_payload(row: ComputerTaskRoot) -> dict:
    """Never return an absolute host path to the browser."""
    return {
        "id": row.id,
        "label": row.label,
        "directory_name": Path(str(row.root_path)).name,
        "revision": row.revision,
        "status": row.status,
    }


def _observation_message(observations: list[dict]) -> tuple[str, dict]:
    lines = ["**Computer diagnostic snapshot**", ""]
    events = []
    for item in observations:
        summary = str(item.get("summary") or "Diagnostic completed")
        source = str((item.get("process") or {}).get("operation") or "Inspect: computer")
        events.append({"kind": "tool", "tool": "inspect", "command": source, "status": "completed"})
        lines.append(f"- **{summary}**")
        lines.extend(f"  - {str(fact)}" for fact in list(item.get("facts") or [])[:8])
    lines.extend(["", "No system settings were changed."])
    return "\n".join(lines), {"events": events, "outcome": "worked", "elapsed_seconds": 0}


def _device_profile_markdown(observations: list[dict]) -> str:
    """Render only server-sanitized, verified observation facts.

    This is a durable starting point for later incidents; it deliberately
    excludes raw logs, model conclusions, credentials, and transient output.
    """
    lines = [
        "# Device profile", "",
        "Verified, non-secret facts collected by Computer Help.",
        "",
    ]
    for item in observations:
        category = str(item.get("category") or "system").replace("_", " ").title()
        lines.extend([f"## {category}", ""])
        lines.extend(f"- {str(fact)}" for fact in list(item.get("facts") or [])[:8])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _device_profile(owner: str, session_id: str, observations: list[dict]) -> DeviceProfileV1:
    """Translate already-sanitized fixed observations into verified context.

    The profile is intentionally derived before any model sees it.  Categories
    preserve the narrow source class, while the source hash permits a durable
    record to be traced to one exact sanitized observation snapshot.
    """
    facts: list[str] = []
    for item in observations:
        category = str(item.get("category") or "system").replace("_", " ").title()
        for fact in list(item.get("facts") or [])[:24]:
            facts.append(f"{category}: {str(fact)}")
            if len(facts) >= 120:
                break
        if len(facts) >= 120:
            break
    encoded = json.dumps(observations, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return DeviceProfileV1(
        session_id=session_id, facts=facts,
        source_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        collected_at=utcnow_naive().isoformat(),
    )


def setup_computer_help_routes(session_manager) -> APIRouter:
    router = APIRouter(prefix="/api/companion/computer", tags=["computer-help"])

    @router.get("/status")
    def status(request: Request):
        _owner(request)
        sandbox = readiness()
        return {
            "computer_observe_ready": True,
            "computer_assist_ready": sandbox.qualified,
            "sandbox": sandbox.public_payload(),
            "allowed_categories": ["system", "hardware", "storage", "graphics", "network"],
        }

    @router.post("/observe")
    def observe(payload: ObserveRequest, request: Request):
        owner = _owner(request)
        scope_kind = _observation_session(owner, payload.session_id)
        started = time.monotonic()
        try:
            observations = collect_observations(payload.categories or None)
        except ObservationError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            if scope_kind == "computer":
                CompanionMemoryStore().write_computer_device_profile(
                    owner=owner,
                    session_id=payload.session_id,
                    path="computer/device-profile.md",
                    content=_device_profile_markdown(observations),
                    profile=_device_profile(owner, payload.session_id, observations),
                )
        except (MemoryScopeError, ScopeConflictError) as exc:
            raise HTTPException(409, "The verified device profile could not be updated") from exc
        except Exception as exc:
            raise HTTPException(503, "The verified device profile could not be refreshed safely") from exc
        content, process = _observation_message(observations)
        process["elapsed_seconds"] = max(0, round(time.monotonic() - started, 2))
        message = ChatMessage("assistant", content, metadata={
            "computer_process": process,
            "computer_profile": "computer_observe",
            "model": "Computer Help" if scope_kind == "computer" else "Odysseus · safe inspection",
        })
        session_manager.add_message(payload.session_id, message)
        return {
            "profile": "computer_observe",
            "observations": observations,
            "summary": "Read-only system inspection completed. No system settings were changed.",
            "message": {"role": "assistant", "content": content, "metadata": message.metadata},
        }

    @router.post("/sandbox/qualify")
    def qualify(request: Request):
        """Run only the fixed G2D-0 fixture; no browser input reaches Podman."""
        _owner(request)
        try:
            result = qualify_containment()
        except SandboxQualificationError as exc:
            raise HTTPException(409, str(exc)) from exc
        return result

    @router.get("/task-roots")
    def list_task_roots(session_id: str, request: Request):
        owner = _owner(request)
        _computer_session(owner, session_id)
        db = SessionLocal()
        try:
            rows = db.query(ComputerTaskRoot).filter(
                ComputerTaskRoot.owner == owner,
                ComputerTaskRoot.status == "active",
            ).order_by(ComputerTaskRoot.created_at.asc()).all()
            return {
                "task_roots": [_task_root_payload(row) for row in rows],
                "execution_ready": readiness().qualified,
                "notice": "Registering a task root does not create files or enable command execution.",
            }
        finally:
            db.close()

    @router.post("/task-roots")
    def create_task_root(payload: TaskRootCreate, request: Request):
        owner = _owner(request)
        _computer_session(owner, payload.session_id)
        try:
            root = _safe_task_root(payload.path)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        label = " ".join(payload.label.split())
        if not label:
            raise HTTPException(422, "Give this task folder a label")
        db = SessionLocal()
        try:
            existing = db.query(ComputerTaskRoot).filter(
                ComputerTaskRoot.owner == owner, ComputerTaskRoot.label == label,
            ).first()
            if existing:
                raise HTTPException(409, "A task root with that label already exists")
            row = ComputerTaskRoot(
                id=str(uuid.uuid4()), owner=owner, label=label, root_path=str(root),
                status="active", revision=1,
            )
            db.add(row); db.commit(); db.refresh(row)
            return {
                "task_root": _task_root_payload(row),
                "notice": "Task root saved. It is not yet an execution permission.",
            }
        except IntegrityError as exc:
            # The pre-insert label lookup gives a friendly normal-path error,
            # but two browser requests can still race it.  The database's
            # owner/label constraint is authoritative, and a duplicate must
            # remain a recoverable conflict rather than look like a server
            # failure to the capability UI.
            db.rollback()
            raise HTTPException(409, "A task root with that label already exists") from exc
        except HTTPException:
            db.rollback(); raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(503, "Task root could not be saved safely") from exc
        finally:
            db.close()

    @router.delete("/task-roots/{task_root_id}")
    def retire_task_root(task_root_id: str, payload: TaskRootRetire, request: Request):
        owner = _owner(request)
        _computer_session(owner, payload.session_id)
        db = SessionLocal()
        try:
            row = db.query(ComputerTaskRoot).filter(
                ComputerTaskRoot.id == task_root_id, ComputerTaskRoot.owner == owner,
            ).first()
            if not row:
                raise HTTPException(404, "Task root was not found")
            if row.revision != payload.expected_revision:
                raise HTTPException(409, "Task root changed; reload before removing it")
            if row.status != "active":
                raise HTTPException(409, "Task root is no longer active")
            row.status, row.revision, row.updated_at = "retired", row.revision + 1, utcnow_naive()
            db.commit()
            return {"retired": True, "id": task_root_id, "revision": row.revision}
        except HTTPException:
            db.rollback(); raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(503, "Task root could not be removed safely") from exc
        finally:
            db.close()

    return router
