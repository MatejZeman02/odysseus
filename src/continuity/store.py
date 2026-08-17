"""Owner-scoped persistence for projects, bindings, and continuity artifacts."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

from core.database import ContinuityArtifact, Project, Session as DbSession, SessionLocal

from .contracts import PersonalBriefV1, ProjectBriefV1, ResolvedScope, SCOPES, ThreadCheckpointV1


class ContinuityError(RuntimeError):
    pass


class ScopeConflictError(ContinuityError):
    pass


class NotFoundError(ContinuityError):
    pass


@dataclass(frozen=True)
class ArtifactWrite:
    id: str
    revision: int
    created: bool


def _settings(project: Project) -> dict:
    try:
        value = json.loads(project.settings_json or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _direct_relations(project: Project) -> set[str]:
    raw = _settings(project).get("related_project_ids", [])
    return {value for value in raw if isinstance(value, str) and value}


class ContinuityStore:
    """Small transactional repository; it never owns or rewrites raw messages."""

    def create_project(self, *, owner: str, name: str, workspace_root: str) -> str:
        if not owner or not name.strip() or not workspace_root.strip():
            raise ValueError("owner, name, and workspace_root are required")
        db = SessionLocal()
        try:
            existing = db.query(Project).filter(Project.owner == owner, Project.name == name.strip()).first()
            if existing:
                raise ScopeConflictError("project name is already owned by this user")
            project = Project(
                id=uuid.uuid4().hex,
                owner=owner,
                name=name.strip(),
                workspace_root=workspace_root.strip(),
                settings_json="{}",
            )
            db.add(project)
            db.commit()
            return project.id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def set_related_projects(self, *, owner: str, project_id: str, related_project_ids: Iterable[str]) -> None:
        requested = {value for value in related_project_ids if isinstance(value, str) and value}
        if project_id in requested:
            raise ValueError("a project cannot be directly related to itself")
        db = SessionLocal()
        try:
            project = self._project(db, owner, project_id)
            if requested:
                count = db.query(Project).filter(Project.owner == owner, Project.id.in_(requested)).count()
                if count != len(requested):
                    raise NotFoundError("direct relations must be owner-owned projects")
            settings = _settings(project)
            settings["related_project_ids"] = sorted(requested)
            project.settings_json = json.dumps(settings, sort_keys=True, separators=(",", ":"))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def bind_session(
        self,
        *,
        owner: str,
        session_id: str,
        scope_kind: str,
        project_id: Optional[str] = None,
        force: bool = False,
    ) -> ResolvedScope:
        if scope_kind not in SCOPES:
            raise ValueError(f"unsupported scope_kind: {scope_kind}")
        if (scope_kind == "project") != bool(project_id):
            raise ValueError("project scope requires project_id and other scopes must not carry one")
        db = SessionLocal()
        try:
            session = self._session(db, owner, session_id)
            if project_id:
                self._project(db, owner, project_id)
            old_scope = getattr(session, "scope_kind", None) or "general"
            old_project = getattr(session, "project_id", None)
            unchanged = old_scope == scope_kind and old_project == project_id
            if not unchanged and old_scope != "general" and not force:
                raise ScopeConflictError("existing session binding can only move through an explicit forced action")
            session.scope_kind = scope_kind
            session.project_id = project_id
            db.commit()
            return self._resolved(db, session, owner)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def resolve_scope(self, *, owner: str, session_id: str) -> ResolvedScope:
        db = SessionLocal()
        try:
            return self._resolved(db, self._session(db, owner, session_id), owner)
        finally:
            db.close()

    def write_thread_checkpoint(self, *, owner: str, checkpoint: ThreadCheckpointV1) -> ArtifactWrite:
        db = SessionLocal()
        try:
            session = self._session(db, owner, checkpoint.session_id)
            scope = self._resolved(db, session, owner)
            if checkpoint.project_id != scope.project_id:
                raise ScopeConflictError("checkpoint project does not match stable session binding")
            return self._write(
                db,
                owner=owner,
                kind="thread_checkpoint_v1",
                session_id=checkpoint.session_id,
                project_id=checkpoint.project_id,
                payload=checkpoint.to_payload(),
                source_through_message_id=checkpoint.source_through_message_id,
                source_hash=checkpoint.source_hash,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def write_project_brief(
        self,
        *,
        owner: str,
        brief: ProjectBriefV1,
        source_hash: str,
        source_through_message_id: Optional[str] = None,
    ) -> ArtifactWrite:
        if not source_hash:
            raise ValueError("project brief requires source_hash")
        db = SessionLocal()
        try:
            self._project(db, owner, brief.project_id)
            return self._write(
                db,
                owner=owner,
                kind="project_brief_v1",
                session_id=None,
                project_id=brief.project_id,
                payload=brief.to_payload(),
                source_through_message_id=source_through_message_id,
                source_hash=source_hash,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def write_personal_brief(
        self, *, owner: str, session_id: str, brief: PersonalBriefV1, source_hash: str,
        source_through_message_id: Optional[str] = None,
    ) -> ArtifactWrite:
        if not source_hash or brief.owner_id != owner:
            raise ValueError("personal brief requires its owner and a source hash")
        db = SessionLocal()
        try:
            session = self._session(db, owner, session_id)
            if (session.scope_kind or "general") != "personal":
                raise ScopeConflictError("personal brief requires a Personal Advisor session")
            return self._write(db, owner=owner, kind="personal_brief_v1", session_id=session_id,
                               project_id=None, payload=brief.to_payload(),
                               source_through_message_id=source_through_message_id, source_hash=source_hash)
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def latest_thread_checkpoint(self, *, owner: str, session_id: str) -> Optional[ThreadCheckpointV1]:
        artifact = self._latest(owner=owner, kind="thread_checkpoint_v1", session_id=session_id)
        return ThreadCheckpointV1.from_payload(json.loads(artifact.payload_json)) if artifact else None

    def latest_project_brief(self, *, owner: str, project_id: str) -> Optional[ProjectBriefV1]:
        artifact = self._latest(owner=owner, kind="project_brief_v1", project_id=project_id)
        return ProjectBriefV1.from_payload(json.loads(artifact.payload_json)) if artifact else None

    def latest_personal_brief(self, *, owner: str, session_id: str) -> Optional[PersonalBriefV1]:
        artifact = self._latest(owner=owner, kind="personal_brief_v1", session_id=session_id)
        return PersonalBriefV1.from_payload(json.loads(artifact.payload_json)) if artifact else None

    def project_catalog(self, *, owner: str) -> list[dict[str, str]]:
        db = SessionLocal()
        try:
            return [{"id": row.id, "name": row.name} for row in db.query(Project).filter(Project.owner == owner).order_by(Project.name.asc())]
        finally:
            db.close()

    def related_project_brief(self, *, owner: str, home_project_id: str, requested_project_id: str) -> Optional[ProjectBriefV1]:
        db = SessionLocal()
        try:
            home = self._project(db, owner, home_project_id)
            if requested_project_id not in _direct_relations(home):
                raise ScopeConflictError("requested project is not directly related to this home project")
        finally:
            db.close()
        return self.latest_project_brief(owner=owner, project_id=requested_project_id)

    @staticmethod
    def _session(db, owner: str, session_id: str) -> DbSession:
        session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
        if session is None:
            raise NotFoundError("owner-scoped session was not found")
        return session

    @staticmethod
    def _project(db, owner: str, project_id: str) -> Project:
        project = db.query(Project).filter(Project.id == project_id, Project.owner == owner).first()
        if project is None:
            raise NotFoundError("owner-scoped project was not found")
        return project

    def _resolved(self, db, session: DbSession, owner: str) -> ResolvedScope:
        scope_kind = getattr(session, "scope_kind", None) or "general"
        if scope_kind not in SCOPES:
            raise ScopeConflictError("stored session scope is invalid")
        project_id = getattr(session, "project_id", None)
        if scope_kind != "project":
            if project_id:
                raise ScopeConflictError("non-project session carries a project binding")
            return ResolvedScope(owner, session.id, scope_kind)
        project = self._project(db, owner, project_id or "")
        return ResolvedScope(owner, session.id, "project", project.id, project.workspace_root)

    def _write(self, db, *, owner, kind, session_id, project_id, payload, source_through_message_id, source_hash) -> ArtifactWrite:
        query = db.query(ContinuityArtifact).filter(
            ContinuityArtifact.owner == owner,
            ContinuityArtifact.kind == kind,
            ContinuityArtifact.source_through_message_id == source_through_message_id,
            ContinuityArtifact.source_hash == source_hash,
        )
        query = query.filter(ContinuityArtifact.session_id == session_id) if session_id else query.filter(ContinuityArtifact.project_id == project_id)
        existing = query.order_by(ContinuityArtifact.revision.desc()).first()
        if existing:
            return ArtifactWrite(existing.id, existing.revision, False)

        current = db.query(ContinuityArtifact).filter(
            ContinuityArtifact.owner == owner,
            ContinuityArtifact.kind == kind,
        )
        current = current.filter(ContinuityArtifact.session_id == session_id) if session_id else current.filter(ContinuityArtifact.project_id == project_id)
        rows = current.all()
        revision = max((row.revision for row in rows), default=0) + 1
        for row in rows:
            if row.status == "active":
                row.status = "superseded"
        artifact = ContinuityArtifact(
            id=uuid.uuid4().hex,
            owner=owner,
            project_id=project_id,
            session_id=session_id,
            kind=kind,
            status="active",
            revision=revision,
            payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            source_through_message_id=source_through_message_id,
            source_hash=source_hash,
        )
        db.add(artifact)
        db.commit()
        return ArtifactWrite(artifact.id, artifact.revision, True)

    def _latest(self, *, owner: str, kind: str, session_id: Optional[str] = None, project_id: Optional[str] = None):
        if bool(session_id) == bool(project_id):
            raise ValueError("exactly one artifact scope is required")
        db = SessionLocal()
        try:
            query = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.kind == kind,
                ContinuityArtifact.status == "active",
            )
            query = query.filter(ContinuityArtifact.session_id == session_id) if session_id else query.filter(ContinuityArtifact.project_id == project_id)
            return query.order_by(ContinuityArtifact.revision.desc()).first()
        finally:
            db.close()
