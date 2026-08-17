"""G2C owner-scoped briefs, working artifacts, and context grants.

The service is intentionally provider-free.  It does not change ``memory.json``
or any vector/AgentMemory writer; exact records remain useful when a retrieval
provider is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Iterable

from core.database import (
    ContextGrant, Project, Session as DbSession, SessionLocal, WorkingArtifact,
    WorkingArtifactRevision, utcnow_naive,
)


class MemoryScopeError(RuntimeError):
    pass


class ArtifactConflict(MemoryScopeError):
    pass


_SECRET_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*[^\s]{8,})",
    re.IGNORECASE,
)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalise_artifact_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if not value or len(value) > 240 or value.startswith("/"):
        raise MemoryScopeError("Artifact path must be a relative Markdown path")
    candidate = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in candidate.parts) or candidate.name.startswith("."):
        raise MemoryScopeError("Artifact path is not allowed")
    if candidate.suffix.lower() not in {".md", ".markdown"}:
        raise MemoryScopeError("Working artifacts must be Markdown files")
    return candidate.as_posix()


def _summary(content: str) -> str:
    compact = " ".join(content.strip().split())
    return compact[:280] + ("…" if len(compact) > 280 else "")


def _json_list(value: str) -> list[str]:
    try:
        data = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in data if isinstance(item, str)] if isinstance(data, list) else []


class CompanionMemoryStore:
    """Small transaction boundary for G2C memory objects."""

    def _personal(self, db, owner: str, session_id: str) -> DbSession:
        row = db.query(DbSession).filter(
            DbSession.id == session_id, DbSession.owner == owner,
            DbSession.scope_kind == "personal",
        ).first()
        if not row:
            raise MemoryScopeError("Personal Advisor session was not found")
        return row

    def _project(self, db, owner: str, project_id: str) -> Project:
        row = db.query(Project).filter(Project.id == project_id, Project.owner == owner).first()
        if not row:
            raise MemoryScopeError("Project was not found")
        return row

    def list_artifacts(self, *, owner: str, scope_kind: str, project_id: str | None = None) -> list[dict]:
        if scope_kind == "project":
            if not project_id:
                return []
            # Project artifacts are source-controlled workspace files.  Qwen
            # never writes them directly; G2B's transaction can create them
            # under .artifacts and this is a read-only index for G2C context.
            db = SessionLocal()
            try:
                project = self._project(db, owner, project_id)
                root = Path(project.workspace_root).resolve(strict=True)
            except (OSError, RuntimeError, MemoryScopeError):
                return []
            finally:
                db.close()
            artifact_root = root / ".artifacts"
            if not artifact_root.is_dir() or artifact_root.is_symlink():
                return []
            records = []
            try:
                for candidate in sorted(artifact_root.rglob("*.md")):
                    if not candidate.is_file() or candidate.is_symlink():
                        continue
                    relative = candidate.relative_to(root).as_posix()
                    data = candidate.read_text(encoding="utf-8")
                    records.append({"id": f"project:{project_id}:{relative}", "scope_kind": "project", "project_id": project_id,
                                    "path": relative, "title": candidate.stem, "summary": _summary(data), "revision": 1,
                                    "content_hash": _hash(data), "updated_at": None, "source_message_id": None})
            except (OSError, UnicodeDecodeError):
                return records
            return records
        db = SessionLocal()
        try:
            query = db.query(WorkingArtifact).filter(
                WorkingArtifact.owner == owner, WorkingArtifact.scope_kind == scope_kind,
                WorkingArtifact.status == "active",
            )
            query = query.filter(WorkingArtifact.project_id == project_id) if project_id else query.filter(WorkingArtifact.project_id == None)
            return [self.serialise_artifact(row) for row in query.order_by(WorkingArtifact.path.asc()).all()]
        finally:
            db.close()

    @staticmethod
    def serialise_artifact(row: WorkingArtifact, *, include_content: bool = False) -> dict:
        value = {
            "id": row.id, "scope_kind": row.scope_kind, "project_id": row.project_id,
            "path": row.path, "title": row.title, "summary": row.summary,
            "revision": row.revision, "content_hash": row.content_hash,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "source_message_id": row.source_message_id,
        }
        if include_content:
            value["content"] = row.content
        return value

    def write_personal_artifact(self, *, owner: str, session_id: str, path: str, content: str,
                                expected_revision: int | None = None, source_message_id: str | None = None) -> dict:
        path = normalise_artifact_path(path)
        if not isinstance(content, str) or len(content.encode("utf-8")) > 512 * 1024:
            raise MemoryScopeError("Artifact content must be UTF-8 Markdown up to 512 KiB")
        if _SECRET_RE.search(content):
            raise MemoryScopeError("Artifact contains credential-like material and was not saved")
        db = SessionLocal()
        try:
            self._personal(db, owner, session_id)
            row = db.query(WorkingArtifact).filter(
                WorkingArtifact.owner == owner, WorkingArtifact.scope_kind == "personal",
                WorkingArtifact.project_id == None, WorkingArtifact.path == path,
                WorkingArtifact.status == "active",
            ).first()
            if row and expected_revision is not None and row.revision != expected_revision:
                raise ArtifactConflict("Artifact changed elsewhere; reload it before saving")
            digest = _hash(content)
            if row:
                db.add(WorkingArtifactRevision(
                    id=uuid.uuid4().hex, artifact_id=row.id, revision=row.revision,
                    content=row.content, content_hash=row.content_hash,
                    source_message_id=row.source_message_id, action="replace",
                ))
                row.content, row.content_hash = content, digest
                row.summary, row.title = _summary(content), PurePosixPath(path).stem
                row.revision += 1
                row.source_message_id = source_message_id
            else:
                row = WorkingArtifact(
                    id=uuid.uuid4().hex, owner=owner, scope_kind="personal", project_id=None,
                    path=path, title=PurePosixPath(path).stem, summary=_summary(content),
                    content=content, content_hash=digest, revision=1, source_message_id=source_message_id,
                )
                db.add(row)
            db.commit(); db.refresh(row)
            result = self.serialise_artifact(row, include_content=True)
            try:
                from src.scoped_memory import ScopedMemoryIndex
                ScopedMemoryIndex().index(owner=owner, scope_kind="personal", project_id=None, session_id=session_id,
                                          source_kind="working_artifact", source_id=row.id,
                                          content=f"{row.path}\n{row.summary}")
            except Exception:
                pass
            return result
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def get_personal_artifact(self, *, owner: str, artifact_id: str) -> dict:
        db = SessionLocal()
        try:
            row = db.query(WorkingArtifact).filter(
                WorkingArtifact.id == artifact_id, WorkingArtifact.owner == owner,
                WorkingArtifact.scope_kind == "personal", WorkingArtifact.status == "active",
            ).first()
            if not row:
                raise MemoryScopeError("Artifact was not found")
            return self.serialise_artifact(row, include_content=True)
        finally:
            db.close()

    def get_personal_artifact_by_path(self, *, owner: str, path: str) -> dict:
        """Return one active Personal artifact's exact content by safe path.

        This is deliberately separate from ``list_artifacts``. The latter is
        the normal metadata-only index; callers must make an explicit
        selection before a draft's body enters a model prompt.
        """
        path = normalise_artifact_path(path)
        db = SessionLocal()
        try:
            row = db.query(WorkingArtifact).filter(
                WorkingArtifact.owner == owner,
                WorkingArtifact.scope_kind == "personal",
                WorkingArtifact.project_id == None,
                WorkingArtifact.path == path,
                WorkingArtifact.status == "active",
            ).first()
            if not row:
                raise MemoryScopeError("Artifact was not found")
            return self.serialise_artifact(row, include_content=True)
        finally:
            db.close()

    def undo_personal_artifact(self, *, owner: str, artifact_id: str, expected_revision: int) -> dict:
        db = SessionLocal()
        try:
            row = db.query(WorkingArtifact).filter(WorkingArtifact.id == artifact_id, WorkingArtifact.owner == owner,
                                                   WorkingArtifact.scope_kind == "personal", WorkingArtifact.status == "active").first()
            if not row:
                raise MemoryScopeError("Artifact was not found")
            if row.revision != expected_revision:
                raise ArtifactConflict("Artifact changed elsewhere; reload it before Undo")
            prior = db.query(WorkingArtifactRevision).filter(WorkingArtifactRevision.artifact_id == row.id).order_by(WorkingArtifactRevision.revision.desc()).first()
            if not prior:
                raise MemoryScopeError("This artifact has no prior revision")
            db.add(WorkingArtifactRevision(id=uuid.uuid4().hex, artifact_id=row.id, revision=row.revision,
                                           content=row.content, content_hash=row.content_hash, action="undo"))
            row.content, row.content_hash, row.revision = prior.content, prior.content_hash, row.revision + 1
            row.summary = _summary(row.content)
            db.commit(); db.refresh(row)
            return self.serialise_artifact(row, include_content=True)
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def delete_personal_artifact(self, *, owner: str, artifact_id: str, expected_revision: int) -> dict:
        db = SessionLocal()
        try:
            row = db.query(WorkingArtifact).filter(WorkingArtifact.id == artifact_id, WorkingArtifact.owner == owner,
                                                   WorkingArtifact.scope_kind == "personal", WorkingArtifact.status == "active").first()
            if not row:
                raise MemoryScopeError("Artifact was not found")
            if row.revision != expected_revision:
                raise ArtifactConflict("Artifact changed elsewhere; reload it before deleting")
            db.add(WorkingArtifactRevision(id=uuid.uuid4().hex, artifact_id=row.id, revision=row.revision,
                                           content=row.content, content_hash=row.content_hash, action="delete"))
            row.status, row.revision = "deleted", row.revision + 1
            db.commit()
            return {"id": row.id, "deleted": True, "revision": row.revision}
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def restore_personal_artifact(self, *, owner: str, artifact_id: str, expected_revision: int) -> dict:
        db = SessionLocal()
        try:
            row = db.query(WorkingArtifact).filter(WorkingArtifact.id == artifact_id, WorkingArtifact.owner == owner,
                                                   WorkingArtifact.scope_kind == "personal", WorkingArtifact.status == "deleted").first()
            if not row:
                raise MemoryScopeError("Deleted artifact was not found")
            if row.revision != expected_revision:
                raise ArtifactConflict("Artifact changed elsewhere; reload it before restoring")
            row.status, row.revision = "active", row.revision + 1
            db.commit(); db.refresh(row)
            return self.serialise_artifact(row, include_content=True)
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def create_grant_request(self, *, owner: str, personal_session_id: str, project_id: str, purpose: str,
                             artifact_paths: Iterable[str] = (), request_message_id: str | None = None) -> dict:
        paths = [normalise_artifact_path(item) for item in artifact_paths]
        if not purpose.strip():
            raise MemoryScopeError("Explain why project context is needed")
        db = SessionLocal()
        try:
            self._personal(db, owner, personal_session_id); self._project(db, owner, project_id)
            row = ContextGrant(id=uuid.uuid4().hex, owner=owner, personal_session_id=personal_session_id,
                               project_id=project_id, purpose=purpose.strip()[:1000],
                               information_classes_json=json.dumps(["project_brief", "artifacts"]),
                               artifact_paths_json=json.dumps(paths), request_message_id=request_message_id,
                               status="pending")
            db.add(row); db.commit(); db.refresh(row)
            return self.serialise_grant(row)
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    @staticmethod
    def serialise_grant(row: ContextGrant) -> dict:
        return {"id": row.id, "project_id": row.project_id, "purpose": row.purpose,
                "artifact_paths": _json_list(row.artifact_paths_json), "status": row.status,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "used_at": row.used_at.isoformat() if row.used_at else None}

    def decide_grant(self, *, owner: str, grant_id: str, allow: bool) -> dict:
        db = SessionLocal()
        try:
            row = db.query(ContextGrant).filter(ContextGrant.id == grant_id, ContextGrant.owner == owner).first()
            if not row:
                raise MemoryScopeError("Project access request was not found")
            if row.status != "pending":
                raise ArtifactConflict("Project access request was already decided")
            row.status = "approved" if allow else "denied"; row.decision_at = utcnow_naive()
            row.expires_at = utcnow_naive() + timedelta(minutes=10) if allow else None
            db.commit(); db.refresh(row)
            return self.serialise_grant(row)
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def approved_grants(self, *, owner: str, personal_session_id: str) -> list[dict]:
        now = utcnow_naive(); db = SessionLocal()
        try:
            rows = db.query(ContextGrant).filter(ContextGrant.owner == owner, ContextGrant.personal_session_id == personal_session_id,
                ContextGrant.status == "approved", ContextGrant.expires_at > now).all()
            return [self.serialise_grant(row) for row in rows]
        finally:
            db.close()

    def pending_grants(self, *, owner: str, personal_session_id: str) -> list[dict]:
        db = SessionLocal()
        try:
            rows = db.query(ContextGrant).filter(
                ContextGrant.owner == owner, ContextGrant.personal_session_id == personal_session_id,
                ContextGrant.status == "pending",
            ).order_by(ContextGrant.created_at.desc()).all()
            return [self.serialise_grant(row) for row in rows]
        finally:
            db.close()

    def consume_grants(self, *, owner: str, personal_session_id: str, grant_ids: Iterable[str]) -> None:
        """Consume Allow-once grants after their labelled context is assembled."""
        ids = [item for item in grant_ids if item and item != "direct-owner-request"]
        if not ids:
            return
        db = SessionLocal()
        try:
            now = utcnow_naive()
            db.query(ContextGrant).filter(
                ContextGrant.owner == owner, ContextGrant.personal_session_id == personal_session_id,
                ContextGrant.id.in_(ids), ContextGrant.status == "approved",
            ).update({ContextGrant.status: "expired", ContextGrant.used_at: now}, synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback(); raise
        finally:
            db.close()
