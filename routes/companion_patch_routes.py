"""Version-neutral APIs for reviewing, applying and undoing project change sets."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.database import Project, ProjectChangeSet, SessionLocal
from routes.g1_continuity_routes import _owner
from src.project_patches import (
    PatchError,
    apply_change_set,
    recover_applying_change_sets,
    reject_change_set,
    rollback_change_set,
    serialize_change_set,
)


logger = logging.getLogger(__name__)


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


def setup_companion_patch_routes() -> APIRouter:
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
