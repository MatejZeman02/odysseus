"""G1.5 Companion homes, projects and their per-chat grants."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.database import ContinuityArtifact, ModelEndpoint, Project, Session as DbSession, SessionLocal, utcnow_naive
from src.path_identity import PathIdentityError, pin_directory
from src.auth_helpers import effective_user, is_delegated_credential, owner_filter, require_user
from src.continuity.store import ContinuityStore, ScopeConflictError
from src.endpoint_resolver import build_chat_url, build_headers, normalize_base
from src.computer_sandbox import readiness as computer_sandbox_readiness
from src.companion_capabilities import ALL_CAPABILITIES, capability_payload, can_change, defaults_for_scope, normalize

_home_locks_guard = threading.Lock()
_home_locks: dict[tuple[str, str, str], threading.Lock] = {}
_LOCAL_COMPANION_OWNER = "__odysseus_local__"


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    workspace_root: str = Field(min_length=1, max_length=4096)
    endpoint_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=512)


class ChatCapabilityRequest(BaseModel):
    """One explicit owner-controlled UI capability update."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    enabled: bool


def _owner(request: Request) -> str:
    # Every Companion route is an owner decision: registering a checkout,
    # applying or rolling back a patch, granting context, promoting memory,
    # turning a capability on. A bearer token resolves to the admin who
    # minted it, which is right for attribution and wrong for authority, so
    # refuse it here before effective_user() can answer with that admin.
    if is_delegated_credential(request):
        raise HTTPException(403, "Companion homes accept the owner's browser session, not an API token")
    owner = effective_user(request)
    if owner:
        return owner
    # Companion routes are owner-scoped whenever a named browser/API identity
    # exists.  In the documented single-user and direct-loopback development
    # modes, though, the common auth helper intentionally authorizes an empty
    # owner.  Honor that same policy so the SPA's Companion fetches cannot turn a
    # valid local bypass into a client-side redirect to /login.
    # Continuity records require a concrete owner even in the app's
    # intentionally anonymous single-user mode. Keep that owner private and
    # stable so projects, homes, and endpoint routing remain internally
    # consistent without pretending there is an authenticated account.
    return require_user(request) or _LOCAL_COMPANION_OWNER


def _safe_workspace(value: str) -> str:
    """Turn an owner-named folder into the stored project path grant.

    This is where the grant is created, so it gets the same pin every later
    consumer uses.  The string persisted here becomes the root of the native
    project tools, the offline shell, protected snapshots and patch targets.
    """
    try:
        root = pin_directory(Path(value).expanduser()).path
    except (PathIdentityError, RuntimeError):
        raise HTTPException(400, "Workspace must be an existing Git checkout, not a symlink")
    if not (root / ".git").exists():
        raise HTTPException(400, "Workspace must be an existing Git checkout")
    return str(root)


def _endpoint(owner: str, endpoint_id: str, model: str) -> tuple[str, dict]:
    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id, ModelEndpoint.is_enabled == True)
        endpoint = owner_filter(query, ModelEndpoint, owner).first()
        if not endpoint:
            raise HTTPException(400, "Model endpoint no longer exists")
        base = endpoint.base_url or ""
        return build_chat_url(normalize_base(base)), build_headers(endpoint.api_key or "", base)
    finally:
        db.close()


def _capability_readiness() -> dict:
    """Readiness facts the capability payload needs, with safe reason codes."""
    from src.project_shell import readiness as shell_readiness

    sandbox = computer_sandbox_readiness()
    shell = shell_readiness()
    return {
        "sandbox_ready": sandbox.qualified,
        "sandbox_reason": sandbox.reason,
        "shell_ready": shell.ready,
        "shell_reason": shell.reason,
    }


def _session_payload(row: DbSession, project_name: str | None = None, workspace_root: str | None = None) -> dict:
    scope_kind = row.scope_kind or "general"
    resolved_workspace = workspace_root or ""
    return {
        "id": row.id, "name": row.name, "model": row.model, "endpoint_id": row.endpoint_id,
        "scope_kind": scope_kind, "project_id": row.project_id,
        "project_name": project_name, "workspace_root": workspace_root,
        "chat_capabilities": capability_payload(
            getattr(row, "capability_grants", None),
            scope_kind=scope_kind,
            workspace_attached=bool(resolved_workspace),
            **_capability_readiness(),
        ),
        "is_scope_primary": bool(row.is_scope_primary),
    }


def _get_or_create_home_locked(session_manager, *, owner: str, scope_kind: str, project: Project | None,
                               endpoint_id: str = "", model: str = "") -> dict:
    """Create homes through SessionManager so normal message persistence remains intact."""
    db = SessionLocal()
    try:
        query = db.query(DbSession).filter(DbSession.owner == owner, DbSession.scope_kind == scope_kind,
                                            DbSession.is_scope_primary == True, DbSession.archived == False)
        if project:
            query = query.filter(DbSession.project_id == project.id)
        else:
            query = query.filter(DbSession.project_id == None)
        existing = query.order_by(DbSession.created_at.asc()).first()
        if existing:
            return _session_payload(
                existing,
                project.name if project else None,
                project.workspace_root if project else None,
            )
    finally:
        db.close()
    if not endpoint_id or not model:
        raise HTTPException(400, "Choose a registered model before creating this Companion home")
    endpoint_url, headers = _endpoint(owner, endpoint_id, model)
    sid = str(uuid.uuid4())
    name = project.name if project else ("Personal Advisor" if scope_kind == "personal" else "Computer Help")
    session = session_manager.create_session(sid, name, endpoint_url, model, owner=owner)
    session.headers = headers
    session.scope_kind = scope_kind
    session.project_id = project.id if project else None
    session.endpoint_id = endpoint_id
    session.is_scope_primary = True
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == sid).one()
        row.headers = headers
        row.scope_kind, row.project_id = scope_kind, project.id if project else None
        row.endpoint_id, row.is_scope_primary = endpoint_id, True
        row.capability_grants = defaults_for_scope(scope_kind)
        row.updated_at = utcnow_naive()
        db.commit()
        return _session_payload(
            row,
            project.name if project else None,
            project.workspace_root if project else None,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _get_or_create_home(session_manager, *, owner: str, scope_kind: str, project: Project | None,
                        endpoint_id: str = "", model: str = "") -> dict:
    """Serialize deterministic home creation across FastAPI worker threads."""
    key = (owner, scope_kind, str(project.id) if project else "")
    with _home_locks_guard:
        lock = _home_locks.setdefault(key, threading.Lock())
    with lock:
        return _get_or_create_home_locked(
            session_manager,
            owner=owner,
            scope_kind=scope_kind,
            project=project,
            endpoint_id=endpoint_id,
            model=model,
        )


def setup_g1_continuity_routes(session_manager) -> APIRouter:
    router = APIRouter(prefix="/api/g1", tags=["g1-continuity"])

    @router.get("/projects")
    def list_projects(request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            projects = db.query(Project).filter(Project.owner == owner).order_by(Project.name.asc()).all()
            return [{"id": p.id, "name": p.name, "workspace_root": p.workspace_root,
                     "workspace_configured": bool(p.workspace_root)} for p in projects]
        finally:
            db.close()

    @router.delete("/projects/{project_id}")
    def delete_project(project_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id, Project.owner == owner).first()
            if not project:
                raise HTTPException(404, "Project not found")
            session_ids = [row.id for row in db.query(DbSession.id).filter(
                DbSession.owner == owner, DbSession.project_id == project_id,
            ).all()]
        finally:
            db.close()
        for sid in session_ids:
            session_manager.delete_session(sid)
        db = SessionLocal()
        try:
            db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.project_id == project_id,
            ).delete(synchronize_session=False)
            project = db.query(Project).filter(Project.id == project_id, Project.owner == owner).first()
            if project:
                db.delete(project)
            db.commit()
            return {"deleted": True, "project_id": project_id, "session_ids": session_ids}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.post("/projects")
    def create_project(payload: ProjectCreateRequest, request: Request):
        owner = _owner(request)
        workspace = _safe_workspace(payload.workspace_root)
        _endpoint(owner, payload.endpoint_id, payload.model)
        store = ContinuityStore()
        try:
            project_id = store.create_project(owner=owner, name=payload.name, workspace_root=workspace)
        except ScopeConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id, Project.owner == owner).one()
        finally:
            db.close()
        home = _get_or_create_home(session_manager, owner=owner, scope_kind="project", project=project,
                                   endpoint_id=payload.endpoint_id, model=payload.model)
        return {"project": {"id": project.id, "name": project.name}, "session": home}

    @router.post("/homes/{scope_kind}/open")
    def open_home(scope_kind: str, request: Request, endpoint_id: str = "", model: str = ""):
        if scope_kind not in {"personal", "computer"}:
            raise HTTPException(404, "Unknown Companion home")
        return _get_or_create_home(session_manager, owner=_owner(request), scope_kind=scope_kind, project=None,
                                   endpoint_id=endpoint_id, model=model)

    @router.post("/projects/{project_id}/open")
    def open_project(project_id: str, request: Request, endpoint_id: str = "", model: str = ""):
        owner = _owner(request)
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id, Project.owner == owner).first()
            if not project:
                raise HTTPException(404, "Project not found")
            db.expunge(project)
        finally:
            db.close()
        return _get_or_create_home(session_manager, owner=owner, scope_kind="project", project=project,
                                   endpoint_id=endpoint_id, model=model)

    @router.post("/projects/{project_id}/fork")
    def fork_project(project_id: str, request: Request, endpoint_id: str, model: str):
        owner = _owner(request)
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id, Project.owner == owner).first()
            if not project:
                raise HTTPException(404, "Project not found")
            db.expunge(project)
        finally:
            db.close()
        endpoint_url, headers = _endpoint(owner, endpoint_id, model)
        sid = str(uuid.uuid4())
        session = session_manager.create_session(sid, f"{project.name} — new thread", endpoint_url, model, owner=owner)
        session.headers, session.scope_kind, session.project_id = headers, "project", project.id
        session.endpoint_id = endpoint_id
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == sid).one()
            row.headers, row.scope_kind, row.project_id = headers, "project", project.id
            row.endpoint_id, row.is_scope_primary = endpoint_id, False
            db.commit()
            return _session_payload(row, project.name, project.workspace_root)
        finally:
            db.close()

    @router.get("/sessions/{session_id}/chat-capabilities")
    def get_chat_capabilities(session_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
            if not row:
                raise HTTPException(404, "Session not found")
            project = db.query(Project).filter(Project.id == row.project_id).first() if row.project_id else None
            workspace_attached = bool(project and project.workspace_root)
            return {
                "session_id": row.id,
                "scope_kind": row.scope_kind or "general",
                "capabilities": capability_payload(
                    getattr(row, "capability_grants", None),
                    scope_kind=row.scope_kind or "general",
                    workspace_attached=workspace_attached,
                    **_capability_readiness(),
                ),
            }
        finally:
            db.close()

    @router.put("/sessions/{session_id}/chat-capabilities")
    def set_chat_capability(session_id: str, payload: ChatCapabilityRequest, request: Request):
        """Persist a fixed capability grant; no commands or roots are accepted."""
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
            if not row:
                raise HTTPException(404, "Session not found")
            project = db.query(Project).filter(Project.id == row.project_id).first() if row.project_id else None
            workspace_attached = bool(project and project.workspace_root)
            readiness = _capability_readiness()
            allowed, reason = can_change(
                payload.name,
                scope_kind=row.scope_kind or "general",
                workspace_attached=workspace_attached,
                **readiness,
            )
            # Switching a grant off never needs the thing it grants to exist.
            if not allowed and (payload.enabled or payload.name not in ALL_CAPABILITIES):
                raise HTTPException(409, reason)
            grants = normalize(getattr(row, "capability_grants", None), scope_kind=row.scope_kind or "general")
            grants[payload.name] = payload.enabled
            row.capability_grants = grants
            row.updated_at = utcnow_naive()
            db.commit()
            live = getattr(session_manager, "sessions", {}).get(session_id)
            if live:
                live.capability_grants = dict(grants)
            return {
                "session_id": row.id,
                "capabilities": capability_payload(
                    grants, scope_kind=row.scope_kind or "general", workspace_attached=workspace_attached,
                    **readiness,
                ),
            }
        finally:
            db.close()

    return router
