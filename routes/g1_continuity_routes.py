"""G1.5 companion homes and the explicitly-enabled read-only Qwen seam."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.database import ChatMessage as DbMessage, ContinuityArtifact, ModelEndpoint, Project, Session as DbSession, SessionLocal, utcnow_naive
from src.auth_helpers import effective_user, owner_filter, require_user
from src.continuity.store import ContinuityStore, ScopeConflictError
from src.endpoint_resolver import build_chat_url, build_headers, normalize_base
from src.qwen_inspection import effective_capability, inspection_readiness
from src.scoped_turn_service import ReadOnlyScopedTurnService, classify_turn_failure

logger = logging.getLogger(__name__)

_home_locks_guard = threading.Lock()
_home_locks: dict[tuple[str, str, str], threading.Lock] = {}
_LOCAL_COMPANION_OWNER = "__odysseus_local__"


class G1TurnRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=20000)
    # Kept optional for compatibility with the original headless endpoint.
    endpoint_id: str = ""
    model: str = ""
    companion_profile: str = Field(default="", max_length=10000)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    workspace_root: str = Field(min_length=1, max_length=4096)
    endpoint_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=512)


class HarnessRequest(BaseModel):
    harness_kind: str


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_profile: str


class FeedbackRequest(BaseModel):
    message_id: str
    rating: str
    note: str = Field(default="", max_length=2000)


def _enabled() -> bool:
    return os.getenv("ODYSSEUS_QWEN_HARNESS", "").strip().lower() in {"1", "true", "yes", "on"}


def _owner(request: Request) -> str:
    owner = effective_user(request)
    if owner:
        return owner
    # Companion routes are owner-scoped whenever a named browser/API identity
    # exists.  In the documented single-user and direct-loopback development
    # modes, though, the common auth helper intentionally authorizes an empty
    # owner.  Honor that same policy so the SPA's readiness fetch cannot turn a
    # valid local bypass into a client-side redirect to /login.
    # Continuity records require a concrete owner even in the app's
    # intentionally anonymous single-user mode. Keep that owner private and
    # stable so projects, homes, and endpoint routing remain internally
    # consistent without pretending there is an authenticated account.
    return require_user(request) or _LOCAL_COMPANION_OWNER


def _qwen_binary() -> Path:
    value = os.getenv("ODYSSEUS_QWEN_BINARY", "").strip()
    binary = Path(value) if value else None
    if not binary or not binary.is_file():
        raise HTTPException(503, "Read-only Qwen companion is not configured")
    return binary


def _qwen_error_payload(exc: Exception) -> dict:
    """Map worker failures to stable, credential-free browser errors."""
    code = classify_turn_failure(exc)
    details = {
        "turn_timeout": "Qwen reached the read-only turn limit before finishing",
        "workspace_changed": "The protected workspace integrity check failed",
        "teardown_failed": "Qwen finished but its isolated worker did not shut down cleanly",
        "sandbox_unavailable": "Sandboxed project inspection is unavailable",
        "command_denied": "The inspection command is not permitted",
        "command_timeout": "The inspection command exceeded its time limit",
        "command_output_limited": "The inspection command exceeded its output limit",
        "command_resource_limit": "The inspection command exceeded its resource limit",
        "provider_failed": "The selected model provider failed during the Qwen turn",
        "worker_died": "The isolated Qwen worker stopped unexpectedly",
        "cancelled": "Qwen turn stopped",
    }
    return {"code": code, "detail": details.get(code, "Read-only Qwen turn failed")}


def _safe_workspace(value: str) -> str:
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(400, "Workspace folder is unavailable")
    if not root.is_dir() or not (root / ".git").exists():
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


def _session_payload(row: DbSession, project_name: str | None = None, workspace_root: str | None = None) -> dict:
    requested = getattr(row, "capability_profile", None) or "project_read"
    return {
        "id": row.id, "name": row.name, "model": row.model, "endpoint_id": row.endpoint_id,
        "scope_kind": row.scope_kind or "general", "project_id": row.project_id,
        "project_name": project_name, "workspace_root": workspace_root,
        "harness_kind": row.harness_kind or "native",
        "capability_profile": requested,
        "requested_capability": requested,
        "effective_capability": effective_capability(requested),
        "is_scope_primary": bool(row.is_scope_primary),
    }


def _get_or_create_home_locked(session_manager, *, owner: str, scope_kind: str, project: Project | None,
                               endpoint_id: str = "", model: str = "", harness: str = "native") -> dict:
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
    session.harness_kind = harness
    session.is_scope_primary = True
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == sid).one()
        row.headers = headers
        row.scope_kind, row.project_id = scope_kind, project.id if project else None
        row.endpoint_id, row.harness_kind, row.is_scope_primary = endpoint_id, harness, True
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
                        endpoint_id: str = "", model: str = "", harness: str = "native") -> dict:
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
            harness=harness,
        )


def _stored_route(owner: str, session_id: str, *, require_qwen: bool = False) -> tuple[str, str]:
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
        if not row:
            raise HTTPException(404, "Session not found")
        if require_qwen and row.harness_kind != "qwen":
            raise HTTPException(409, "Qwen Companion is disabled for this project session")
        if not row.endpoint_id or not row.model:
            raise HTTPException(409, "Select a registered model before enabling Qwen")
        _endpoint(owner, row.endpoint_id, row.model)
        return row.endpoint_id, row.model
    finally:
        db.close()


def _stored_capability(owner: str, session_id: str) -> tuple[str, str]:
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
        if not row:
            raise HTTPException(404, "Session not found")
        requested = getattr(row, "capability_profile", None) or "project_read"
        return requested, effective_capability(requested)
    finally:
        db.close()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _qwen_readiness() -> tuple[bool, bool, bool]:
    """Return only safe component booleans; never expose configured paths."""
    configured_value = os.getenv("ODYSSEUS_QWEN_BINARY", "").strip()
    binary_ready = bool(configured_value and Path(configured_value).is_file())
    bubblewrap_ready = bool(shutil.which("bwrap"))
    return _enabled() and binary_ready and bubblewrap_ready, binary_ready, bubblewrap_ready


def _require_qwen_ready() -> None:
    ready, binary_ready, bubblewrap_ready = _qwen_readiness()
    if ready:
        return
    missing = []
    if not _enabled():
        missing.append("Qwen harness")
    if not binary_ready:
        missing.append("Qwen binary")
    if not bubblewrap_ready:
        missing.append("Bubblewrap")
    raise HTTPException(503, f"Qwen Companion is not ready ({', '.join(missing)} missing)")


def setup_g1_continuity_routes(session_manager) -> APIRouter:
    router = APIRouter(prefix="/api/g1", tags=["g1-continuity"])
    runs: dict[str, asyncio.Task] = {}
    admitted: set[str] = set()
    cancel_requested: set[str] = set()

    @router.get("/status")
    def status(request: Request):
        _owner(request)
        qwen_ready, binary_ready, bubblewrap_ready = _qwen_readiness()
        inspection = inspection_readiness()
        return {"enabled": _enabled(), "qwen_ready": qwen_ready,
                "containment": "bubblewrap-read-only", "scopes": {"project": "available", "personal": "native", "computer": "coming_soon"},
                "native_chat_shell": False,
                "components": {"qwen_binary": binary_ready, "bubblewrap": bubblewrap_ready,
                               "podman": inspection.podman_ready,
                               "sandbox_image": inspection.image_ready},
                "inspection": inspection.public_payload()}

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
        if any(sid in runs and not runs[sid].done() for sid in session_ids):
            raise HTTPException(409, "Stop the active Qwen turn before deleting this project")
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
                                   endpoint_id=payload.endpoint_id, model=payload.model, harness="qwen")
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
                                   endpoint_id=endpoint_id, model=model, harness="qwen")

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
        session.endpoint_id, session.harness_kind = endpoint_id, "qwen"
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == sid).one()
            row.headers, row.scope_kind, row.project_id = headers, "project", project.id
            row.endpoint_id, row.harness_kind, row.is_scope_primary = endpoint_id, "qwen", False
            db.commit()
            return _session_payload(row, project.name, project.workspace_root)
        finally:
            db.close()

    @router.patch("/sessions/{session_id}/harness")
    def set_harness(session_id: str, payload: HarnessRequest, request: Request):
        if payload.harness_kind not in {"native", "qwen"}:
            raise HTTPException(400, "Unknown harness")
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
            if not row:
                raise HTTPException(404, "Session not found")
            if payload.harness_kind == "qwen":
                if row.scope_kind != "project":
                    raise HTTPException(409, "Qwen is available only in project homes during G1.5")
                _require_qwen_ready()
                _stored_route(owner, session_id)
            row.harness_kind = payload.harness_kind
            row.updated_at = utcnow_naive()
            db.commit()
            live = session_manager.sessions.get(session_id)
            if live:
                live.harness_kind = payload.harness_kind
            return _session_payload(row)
        finally:
            db.close()

    @router.get("/sessions/{session_id}/harness")
    def get_harness(session_id: str, request: Request):
        """Small authoritative read used immediately before project dispatch."""
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(
                DbSession.id == session_id,
                DbSession.owner == owner,
            ).first()
            if not row:
                raise HTTPException(404, "Session not found")
            requested = getattr(row, "capability_profile", None) or "project_read"
            return {
                "scope_kind": row.scope_kind or "general",
                "harness_kind": row.harness_kind or "native",
                "requested_capability": requested,
                "effective_capability": effective_capability(requested),
            }
        finally:
            db.close()

    @router.patch("/sessions/{session_id}/capability")
    def set_capability(session_id: str, payload: CapabilityRequest, request: Request):
        if payload.capability_profile not in {"project_read", "project_inspect"}:
            raise HTTPException(400, "Unknown project capability")
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(
                DbSession.id == session_id,
                DbSession.owner == owner,
            ).first()
            if not row:
                raise HTTPException(404, "Session not found")
            if row.scope_kind != "project" or row.harness_kind != "qwen":
                raise HTTPException(409, "Project capabilities require a Qwen project session")
            if payload.capability_profile == "project_inspect":
                readiness = inspection_readiness()
                if not readiness.ready:
                    raise HTTPException(503, {
                        "code": "sandbox_unavailable",
                        "detail": "Sandboxed project inspection is unavailable; read-only project tools remain active",
                    })
            row.capability_profile = payload.capability_profile
            row.updated_at = utcnow_naive()
            db.commit()
            live = getattr(session_manager, "sessions", {}).get(session_id)
            if live:
                live.capability_profile = payload.capability_profile
            return _session_payload(row)
        finally:
            db.close()

    @router.post("/project-turn")
    async def project_turn(payload: G1TurnRequest, request: Request):
        if not _enabled():
            raise HTTPException(404, "Not found")
        _require_qwen_ready()
        owner = _owner(request)
        if not hasattr(session_manager, "get_session"):  # narrow compatibility seam for isolated route tests
            endpoint_id, model = payload.endpoint_id, payload.model
            requested_capability, admitted_capability = "project_read", "project_read"
        else:
            endpoint_id, model = _stored_route(owner, payload.session_id, require_qwen=True)
            requested_capability, admitted_capability = _stored_capability(owner, payload.session_id)
        # Headless clients may repeat the stored route but cannot override it.
        if (payload.endpoint_id and payload.endpoint_id != endpoint_id) or (payload.model and payload.model != model):
            raise HTTPException(409, "Session model route does not match stored selection")
        service = ReadOnlyScopedTurnService(session_manager, qwen_binary=_qwen_binary())
        try:
            result = await service.run(owner=owner, session_id=payload.session_id, request=payload.message,
                                       endpoint_id=endpoint_id, model=model, companion_profile=payload.companion_profile,
                                       capability_profile=admitted_capability)
        except ScopeConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"answer": result.answer, "context_manifest": result.manifest,
                "workspace_unchanged": result.dust_unchanged,
                "requested_capability": requested_capability,
                "effective_capability": admitted_capability,
                "qwen_process": getattr(result, "qwen_process", None)}

    @router.post("/project-turn/stream")
    async def stream_project_turn(payload: G1TurnRequest, request: Request):
        if not _enabled():
            raise HTTPException(404, "Not found")
        _require_qwen_ready()
        owner = _owner(request)
        endpoint_id, model = _stored_route(owner, payload.session_id, require_qwen=True)
        requested_capability, admitted_capability = _stored_capability(owner, payload.session_id)
        qwen_binary = _qwen_binary()
        # Admit before returning StreamingResponse. Two simultaneous requests
        # can otherwise both pass the runs lookup before either generator has
        # created and registered its worker task.
        if payload.session_id in admitted:
            raise HTTPException(409, "Project session already has an active Qwen turn")
        admitted.add(payload.session_id)

        async def generate():
            task: asyncio.Task | None = None
            try:
                if payload.session_id in cancel_requested:
                    yield _sse("error", {"code": "cancelled", "detail": "Qwen turn stopped"})
                    return
                yield _sse("status", {"message": "Starting read-only Qwen companion"})
                service = ReadOnlyScopedTurnService(session_manager, qwen_binary=qwen_binary)
                progress: asyncio.Queue[dict] = asyncio.Queue()

                async def report_progress(update: dict) -> None:
                    # The supervisor has already reduced this to a safe operation,
                    # workspace-relative path, and status. Never forward raw Qwen
                    # envelopes, tool arguments, or output.
                    await progress.put(update)

                task = asyncio.create_task(service.run(owner=owner, session_id=payload.session_id, request=payload.message,
                                                       endpoint_id=endpoint_id, model=model, companion_profile=payload.companion_profile,
                                                       capability_profile=admitted_capability,
                                                       progress_callback=report_progress))
                runs[payload.session_id] = task
                while not task.done() or not progress.empty():
                    try:
                        update = await asyncio.wait_for(progress.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                    event_name = "commentary" if update.get("kind") == "commentary" else "tool"
                    yield _sse(event_name, update)
                result = await task
                yield _sse("delta", {"text": result.answer})
                yield _sse("done", {"message_id": result.message_id, "user_message_id": result.user_message_id,
                                     "context_manifest": result.manifest, "workspace_unchanged": result.dust_unchanged,
                                     "harness": "qwen", "read_only": True, "model": model,
                                     "requested_capability": requested_capability,
                                     "effective_capability": admitted_capability,
                                     "qwen_process": getattr(result, "qwen_process", None)})
            except asyncio.CancelledError:
                # An explicit Stop cancels the worker task. A disconnected
                # browser cancels this generator instead; propagate that
                # cancellation after teardown rather than trying to write to a
                # closed SSE response.
                if task is not None and task.cancelled():
                    yield _sse("error", {"code": "cancelled", "detail": "Qwen turn stopped"})
                else:
                    raise
            except Exception as exc:
                logger.exception("G1.5 Qwen turn failed for session %s: %s", payload.session_id, exc)
                yield _sse("error", _qwen_error_payload(exc))
            finally:
                # The service owns a Bubblewrap/Qwen process. Never leave it
                # running when the browser disconnects or the response is
                # cancelled before a terminal event is produced.
                if task is not None and not task.done():
                    task.cancel()
                if task is not None:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        # The error was already represented as a safe SSE event
                        # above; only ensure cleanup here.
                        pass
                runs.pop(payload.session_id, None)
                admitted.discard(payload.session_id)
                cancel_requested.discard(payload.session_id)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @router.post("/project-turn/{session_id}/stop")
    async def stop_project_turn(session_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            owned = db.query(DbSession.id).filter(
                DbSession.id == session_id,
                DbSession.owner == owner,
            ).first()
            if not owned:
                raise HTTPException(404, "Session not found")
        finally:
            db.close()
        task = runs.get(session_id)
        if task and not task.done():
            task.cancel()
            return {"stopped": True}
        if session_id in admitted:
            cancel_requested.add(session_id)
            return {"stopped": True}
        return {"stopped": False}

    @router.post("/feedback")
    async def feedback(payload: FeedbackRequest, request: Request):
        owner = _owner(request)
        if payload.rating not in {"helpful", "wrong", "unsafe"}:
            raise HTTPException(400, "Unknown feedback rating")
        db = SessionLocal()
        try:
            row = db.query(DbMessage).join(DbSession).filter(DbMessage.id == payload.message_id, DbSession.owner == owner).first()
            if not row or row.role != "assistant":
                raise HTTPException(404, "Assistant message not found")
            metadata = json.loads(row.meta_data or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
            saved_feedback = {"rating": payload.rating, "note": payload.note.strip(), "harness": "qwen"}
            metadata["g1_feedback"] = saved_feedback
            row.meta_data = json.dumps(metadata, separators=(",", ":"))
            db.commit()

            # SessionManager deliberately keeps hydrated transcripts in RAM.
            # Writing only SQLite makes a browser reload replay stale metadata
            # until the whole server restarts. Keep the authoritative cached
            # message in sync so the selected rating and note survive an
            # ordinary page reload immediately.
            cached = getattr(session_manager, "sessions", {}).get(row.session_id)
            if cached is not None:
                for message in getattr(cached, "history", ()):
                    message_metadata = getattr(message, "metadata", None)
                    if isinstance(message_metadata, dict) and message_metadata.get("_db_id") == row.id:
                        message_metadata["g1_feedback"] = dict(saved_feedback)
                        break
            return {"ok": True}
        finally:
            db.close()

    @router.get("/projects/{project_id}/evaluation")
    def evaluation(project_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            rows = db.query(DbMessage, DbSession).join(DbSession).filter(DbSession.owner == owner, DbSession.project_id == project_id,
                                                                          DbMessage.role == "assistant").all()
            entries = []
            for message, session in rows:
                metadata = json.loads(message.meta_data or "{}")
                feedback = metadata.get("g1_feedback")
                if feedback:
                    entries.append({"message_id": message.id, "session_id": session.id, "timestamp": message.timestamp.isoformat() if message.timestamp else None,
                                    "rating": feedback.get("rating"), "note": feedback.get("note", ""),
                                    "effective_capability": metadata.get("capability_profile", "project_read"),
                                    "workspace_unchanged": bool(metadata.get("workspace_unchanged", False))})
            counts = {key: sum(1 for entry in entries if entry["rating"] == key) for key in ("helpful", "wrong", "unsafe")}
            return {"project_id": project_id, "counts": counts, "entries": entries}
        finally:
            db.close()

    return router
