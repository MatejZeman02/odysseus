"""Version-neutral APIs for reviewed project patch proposals and approval."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.database import Project, ProjectChangeSet, Session as DbSession, SessionLocal
from routes.g1_continuity_routes import (
    _owner,
    _qwen_binary,
    _qwen_error_payload,
    _require_qwen_ready,
    _sse,
    _stored_capability,
    _stored_route,
)
from src.companion_runs import CompanionRunRegistry
from src.project_patches import (
    PatchError,
    apply_change_set,
    recover_applying_change_sets,
    reject_change_set,
    rollback_change_set,
    serialize_change_set,
)
from src.scoped_turn_service import ReadOnlyScopedTurnService


logger = logging.getLogger(__name__)


class PatchTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=20000)


class PatchRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


def _patch_error(exc: PatchError) -> HTTPException:
    return HTTPException(exc.status_code, {"code": exc.code, "detail": exc.detail})


def _owned_patch(db, owner: str, patch_id: str) -> ProjectChangeSet:
    row = db.query(ProjectChangeSet).filter(
        ProjectChangeSet.id == patch_id,
        ProjectChangeSet.owner == owner,
    ).first()
    if not row:
        raise HTTPException(404, "Patch not found")
    return row


def _owned_project(db, owner: str, project_id: str) -> Project:
    row = db.query(Project).filter(Project.id == project_id, Project.owner == owner).first()
    if not row:
        raise HTTPException(404, "Project not found")
    return row


def _patch_turn_binding(owner: str, project_id: str, session_id: str) -> tuple[str, str, str]:
    db = SessionLocal()
    try:
        project = _owned_project(db, owner, project_id)
        session = db.query(DbSession).filter(
            DbSession.id == session_id,
            DbSession.owner == owner,
            DbSession.project_id == project_id,
            DbSession.scope_kind == "project",
            DbSession.harness_kind == "qwen",
        ).first()
        if not session:
            raise HTTPException(409, "Patch proposals require this project's Qwen session")
        return project.workspace_root, session.endpoint_id or "", session.model
    finally:
        db.close()


def _attach_patch_metadata(session_manager, session_id: str, message_id: str, patch: dict) -> None:
    session = getattr(session_manager, "sessions", {}).get(session_id)
    if not session:
        return
    for message in getattr(session, "history", ()):
        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, dict) and metadata.get("_db_id") == message_id:
            metadata["project_patch"] = patch
            if hasattr(session_manager, "update_message_metadata"):
                session_manager.update_message_metadata(session_id, message_id, metadata)
            return


def setup_companion_patch_routes(session_manager, run_registry: CompanionRunRegistry) -> APIRouter:
    router = APIRouter(prefix="/api/companion", tags=["companion-patches"])

    # A durable `applying` journal means startup can safely repair a process
    # crash between replacements before accepting another approval.
    db = SessionLocal()
    try:
        recovered = recover_applying_change_sets(db)
        if recovered:
            logger.warning("Recovered %s interrupted project patch transaction(s)", recovered)
    finally:
        db.close()

    @router.post("/projects/{project_id}/patch-turn/stream")
    async def patch_turn(project_id: str, payload: PatchTurnRequest, request: Request):
        _require_qwen_ready()
        owner = _owner(request)
        _workspace, endpoint_id, model = _patch_turn_binding(owner, project_id, payload.session_id)
        stored_endpoint, stored_model = _stored_route(owner, payload.session_id, require_qwen=True)
        if (endpoint_id, model) != (stored_endpoint, stored_model):
            raise HTTPException(409, "Project model route changed")
        _requested_capability, effective_capability = _stored_capability(owner, payload.session_id)
        if effective_capability != "project_read":
            raise HTTPException(409, "Patch proposals require the read-only Qwen profile")
        if payload.session_id in run_registry.admitted:
            raise HTTPException(409, "Project session already has an active Qwen turn")
        run_registry.admitted.add(payload.session_id)

        async def generate():
            task: asyncio.Task | None = None
            try:
                if payload.session_id in run_registry.cancel_requested:
                    yield _sse("error", {"code": "cancelled", "detail": "Qwen turn stopped"})
                    return
                yield _sse("status", {"message": "Starting read-only Qwen patch proposal"})
                progress: asyncio.Queue[dict] = asyncio.Queue()

                async def report_progress(update: dict) -> None:
                    await progress.put(update)

                service = ReadOnlyScopedTurnService(session_manager, qwen_binary=_qwen_binary())
                task = asyncio.create_task(service.run(
                    owner=owner,
                    session_id=payload.session_id,
                    request=payload.message,
                    endpoint_id=endpoint_id,
                    model=model,
                    capability_profile="project_read",
                    proposal_mode=True,
                    progress_callback=report_progress,
                ))
                run_registry.runs[payload.session_id] = task
                while not task.done() or not progress.empty():
                    try:
                        update = await asyncio.wait_for(progress.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                    yield _sse("commentary" if update.get("kind") == "commentary" else "tool", update)
                result = await task
                if result.proposal is None:
                    raise PatchError("proposal_invalid", "Qwen returned no patch proposal")

                db = SessionLocal()
                try:
                    # Re-check ownership and project binding after the model
                    # finishes; a deleted project cannot gain a detached patch.
                    _owned_project(db, owner, project_id)
                    row = ProjectChangeSet(
                        id=uuid.uuid4().hex,
                        owner=owner,
                        project_id=project_id,
                        session_id=payload.session_id,
                        source_message_id=result.message_id or None,
                        model=model,
                        endpoint_id=endpoint_id,
                        revision=1,
                        status="proposed",
                        summary=result.proposal.summary,
                        rationale=result.proposal.rationale,
                        base_git_revision=result.proposal.base_git_revision,
                        proposal_json=json.dumps(result.proposal.payload, ensure_ascii=False, separators=(",", ":")),
                        result_json=json.dumps({"process": [
                            {"label": f"Validate proposal: {len(result.proposal.public_files)} files", "status": "completed"},
                        ]}, separators=(",", ":")),
                    )
                    db.add(row)
                    db.commit()
                    db.refresh(row)
                    public_patch = serialize_change_set(row, include_diff=False)
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()
                _attach_patch_metadata(session_manager, payload.session_id, result.message_id, public_patch)
                yield _sse("delta", {"text": result.answer})
                yield _sse("proposal", public_patch)
                yield _sse("done", {
                    "message_id": result.message_id,
                    "user_message_id": result.user_message_id,
                    "context_manifest": result.manifest,
                    "workspace_unchanged": result.dust_unchanged,
                    "harness": "qwen",
                    "read_only": True,
                    "model": model,
                    "effective_capability": "project_read",
                    "qwen_process": result.qwen_process,
                    "patch_id": public_patch["id"],
                })
            except asyncio.CancelledError:
                if task is not None and task.cancelled():
                    yield _sse("error", {"code": "cancelled", "detail": "Qwen turn stopped"})
                else:
                    raise
            except PatchError as exc:
                yield _sse("error", {"code": exc.code, "detail": exc.detail})
            except Exception as exc:
                logger.exception("Qwen patch proposal failed for session %s", payload.session_id)
                yield _sse("error", _qwen_error_payload(exc))
            finally:
                if task is not None and not task.done():
                    task.cancel()
                if task is not None:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                run_registry.runs.pop(payload.session_id, None)
                run_registry.admitted.discard(payload.session_id)
                run_registry.cancel_requested.discard(payload.session_id)

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @router.get("/projects/{project_id}/patches")
    def list_patches(project_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            _owned_project(db, owner, project_id)
            rows = db.query(ProjectChangeSet).filter(
                ProjectChangeSet.owner == owner,
                ProjectChangeSet.project_id == project_id,
            ).order_by(ProjectChangeSet.created_at.desc()).all()
            return [serialize_change_set(row) for row in rows]
        finally:
            db.close()

    @router.get("/patches/{patch_id}")
    def get_patch(patch_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            return serialize_change_set(_owned_patch(db, owner, patch_id), include_diff=True)
        finally:
            db.close()

    @router.post("/patches/{patch_id}/apply")
    def apply_patch(patch_id: str, payload: PatchRevisionRequest, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = _owned_patch(db, owner, patch_id)
            project = _owned_project(db, owner, row.project_id)
            try:
                return apply_change_set(db, row, Path(project.workspace_root), expected_revision=payload.expected_revision)
            except PatchError as exc:
                raise _patch_error(exc) from exc
        finally:
            db.close()

    @router.post("/patches/{patch_id}/reject")
    def reject_patch(patch_id: str, payload: PatchRevisionRequest, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = _owned_patch(db, owner, patch_id)
            try:
                return reject_change_set(db, row, expected_revision=payload.expected_revision)
            except PatchError as exc:
                raise _patch_error(exc) from exc
        finally:
            db.close()

    @router.post("/patches/{patch_id}/rollback")
    def rollback_patch(patch_id: str, payload: PatchRevisionRequest, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = _owned_patch(db, owner, patch_id)
            project = _owned_project(db, owner, row.project_id)
            try:
                return rollback_change_set(db, row, Path(project.workspace_root), expected_revision=payload.expected_revision)
            except PatchError as exc:
                if exc.code == "rollback_conflict" and row.status == "applied":
                    row.status = "rollback_conflict"
                    row.failure_code = exc.code
                    row.revision += 1
                    db.commit()
                raise _patch_error(exc) from exc
        finally:
            db.close()

    return router
