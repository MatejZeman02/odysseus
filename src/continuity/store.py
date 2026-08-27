"""Owner-scoped persistence for projects, bindings, and continuity artifacts."""

from __future__ import annotations

import json
import uuid
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import text

from core.database import ChatMessage as DbMessage, ContinuityArtifact, Project, Session as DbSession, SessionLocal

from .contracts import (
    PersonalBriefV1,
    ProjectBriefV1,
    ResolvedScope,
    SCOPES,
    SemanticCheckpointProposalV1,
    ThreadCheckpointV1,
    selected_checkpoint_entries,
    selected_proposal_entries,
)


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


@dataclass(frozen=True)
class SemanticProposalRecord:
    id: str
    revision: int
    status: str
    proposal: SemanticCheckpointProposalV1


@dataclass(frozen=True)
class SemanticProposalAttemptRecord:
    id: str
    revision: int
    outcome: str
    code: str


@dataclass(frozen=True)
class CheckpointMountRecord:
    id: str
    revision: int
    source_checkpoint_id: str
    source_session_id: str
    checkpoint: ThreadCheckpointV1
    sensitivity: str = "standard"
    expires_at: str | None = None
    status: str = "active"


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

    _CHECKPOINT_MOUNT_SENSITIVITIES = {"standard", "sensitive"}
    _DEFAULT_CHECKPOINT_MOUNT_DAYS = 14
    _MAX_CHECKPOINT_MOUNT_DAYS = 30

    @staticmethod
    def _utcnow() -> datetime:
        """Match the application's existing naive-UTC database convention."""
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @classmethod
    def _checkpoint_mount_payload(cls, row: ContinuityArtifact) -> tuple[str, str, str | None]:
        """Return validated source/lifecycle data without trusting browser input."""
        try:
            payload = json.loads(row.payload_json)
            source_id = str(payload["source_checkpoint_id"])
            sensitivity = str(payload.get("sensitivity", "standard"))
            expires_at = payload.get("expires_at")
            if sensitivity not in cls._CHECKPOINT_MOUNT_SENSITIVITIES:
                raise ValueError("unsupported checkpoint mount sensitivity")
            if expires_at is not None:
                expires_at = str(expires_at)
                datetime.fromisoformat(expires_at)
            return source_id, sensitivity, expires_at
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ScopeConflictError("checkpoint mount metadata is invalid") from exc

    @classmethod
    def _expire_checkpoint_mounts(cls, db, *, owner: str, destination_session_id: str) -> bool:
        """Make expiration durable before a read, compile, or promotion can use it."""
        changed = False
        now = cls._utcnow()
        rows = db.query(ContinuityArtifact).filter(
            ContinuityArtifact.owner == owner,
            ContinuityArtifact.session_id == destination_session_id,
            ContinuityArtifact.kind == "checkpoint_mount_v1",
            ContinuityArtifact.status == "active",
        ).all()
        for row in rows:
            try:
                _source_id, _sensitivity, expires_at = cls._checkpoint_mount_payload(row)
            except ScopeConflictError:
                # Corrupt legacy mount payloads are never admitted as context.
                row.status = "expired"
                changed = True
                continue
            if expires_at is not None and datetime.fromisoformat(expires_at) <= now:
                row.status = "expired"
                changed = True
        return changed

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

    def related_project_catalog(self, *, owner: str, project_id: str) -> list[dict[str, object]]:
        """Return owner-selected direct-relation metadata, never project content."""
        db = SessionLocal()
        try:
            project = self._project(db, owner, project_id)
            selected = _direct_relations(project)
            rows = db.query(Project).filter(
                Project.owner == owner,
                Project.id != project_id,
            ).order_by(Project.name.asc()).all()
            return [
                {"id": row.id, "name": row.name, "related": row.id in selected}
                for row in rows
            ]
        finally:
            db.close()

    def explicit_related_project_ids(self, *, owner: str, project_id: str, request: str, limit: int = 3) -> list[str]:
        """Resolve explicit ``@Project Name`` mentions against direct relations.

        Relation storage is an allowlist, not an automatic sharing rule. This
        parser only returns directly related owner projects that the owner
        named in this exact request. It never accepts IDs from the browser.
        """
        if not 1 <= limit <= 10:
            raise ValueError("related project request limit must be between 1 and 10")
        folded_request = str(request or "").casefold()
        if "@" not in folded_request:
            return []
        related = self.related_project_catalog(owner=owner, project_id=project_id)
        matches = [
            str(row["id"]) for row in related
            if row["related"] and re.search(
                rf"(?<![\w])@{re.escape(str(row['name']).casefold())}(?![\w])", folded_request,
            )
        ]
        return matches[:limit]

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
            accepted = self._preferred_brief_artifact(
                db, owner=owner, kind="project_brief_v1", project_id=brief.project_id,
            )
            if accepted and self._artifact_derivation_status(accepted) == "accepted" and brief.derivation_status != "accepted":
                # A local heuristic is an availability aid, never authority to
                # replace an owner-approved project home brief.
                return ArtifactWrite(accepted.id, accepted.revision, False)
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
            accepted = self._preferred_brief_artifact(
                db, owner=owner, kind="personal_brief_v1", session_id=session_id,
            )
            if accepted and self._artifact_derivation_status(accepted) == "accepted" and brief.derivation_status != "accepted":
                return ArtifactWrite(accepted.id, accepted.revision, False)
            return self._write(db, owner=owner, kind="personal_brief_v1", session_id=session_id,
                               project_id=None, payload=brief.to_payload(),
                               source_through_message_id=source_through_message_id, source_hash=source_hash)
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def write_semantic_proposal(
        self, *, owner: str, proposal: SemanticCheckpointProposalV1,
    ) -> ArtifactWrite:
        """Persist a validated proposal without promoting it into home state."""
        db = SessionLocal()
        try:
            session = self._session(db, owner, proposal.session_id)
            scope = self._resolved(db, session, owner)
            if scope.scope_kind != proposal.scope_kind or scope.project_id != proposal.project_id:
                raise ScopeConflictError("semantic proposal does not match stable session binding")
            return self._write(
                db,
                owner=owner,
                kind="semantic_checkpoint_proposal_v1",
                session_id=proposal.session_id,
                project_id=proposal.project_id,
                payload=proposal.to_payload(),
                source_through_message_id=proposal.source_through_message_id,
                source_hash=proposal.source_hash,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def write_semantic_proposal_if_absent(
        self, *, owner: str, proposal: SemanticCheckpointProposalV1,
    ) -> tuple[SemanticProposalRecord, bool]:
        """Write a proposal only if its session has no active review item.

        Background derivation may finish after a user has manually created a
        proposal.  It must not supersede that item merely because it started
        first.  SQLite's immediate transaction gives the same admission rule
        to concurrent local workers; other databases use a row lock when one
        is available.
        """
        db = SessionLocal()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            session = self._session(db, owner, proposal.session_id)
            scope = self._resolved(db, session, owner)
            if scope.scope_kind != proposal.scope_kind or scope.project_id != proposal.project_id:
                raise ScopeConflictError("semantic proposal does not match stable session binding")
            query = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.session_id == proposal.session_id,
                ContinuityArtifact.kind == "semantic_checkpoint_proposal_v1",
                ContinuityArtifact.status == "active",
            )
            if db.get_bind().dialect.name != "sqlite":
                query = query.with_for_update()
            existing = query.order_by(ContinuityArtifact.revision.desc()).first()
            if existing is not None:
                db.commit()
                return SemanticProposalRecord(
                    id=existing.id,
                    revision=existing.revision,
                    status=existing.status,
                    proposal=SemanticCheckpointProposalV1.from_payload(json.loads(existing.payload_json)),
                ), False
            write = self._write(
                db,
                owner=owner,
                kind="semantic_checkpoint_proposal_v1",
                session_id=proposal.session_id,
                project_id=proposal.project_id,
                payload=proposal.to_payload(),
                source_through_message_id=proposal.source_through_message_id,
                source_hash=proposal.source_hash,
                commit=False,
            )
            db.commit()
            return SemanticProposalRecord(write.id, write.revision, "active", proposal), True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def write_semantic_proposal_attempt(
        self, *, owner: str, session_id: str, outcome: str, code: str,
    ) -> SemanticProposalAttemptRecord:
        """Persist one safe background outcome, never provider output or text.

        This record is observability for the owner, not semantic context. Its
        payload has only a stable code; diagnostics and raw model output stay
        in server logs. A new attempt supersedes the previous status for the
        same session while revision history remains available to operators.
        """
        if outcome not in {"ready", "failed", "cancelled"}:
            raise ValueError("invalid semantic proposal attempt outcome")
        if not code or len(code) > 64 or not code.replace("_", "").isalnum():
            raise ValueError("invalid semantic proposal attempt code")
        db = SessionLocal()
        try:
            session = self._session(db, owner, session_id)
            scope = self._resolved(db, session, owner)
            if scope.scope_kind not in {"personal", "project"}:
                raise ScopeConflictError("semantic proposal attempts require a Companion home")
            payload = {"schema_version": 1, "outcome": outcome, "code": code}
            source_hash = hashlib.sha256(
                f"semantic-proposal-attempt:{session_id}:{outcome}:{code}:{uuid.uuid4().hex}".encode("utf-8")
            ).hexdigest()
            write = self._write(
                db,
                owner=owner,
                kind="semantic_checkpoint_attempt_v1",
                session_id=session_id,
                project_id=scope.project_id,
                payload=payload,
                source_through_message_id=None,
                source_hash=source_hash,
            )
            return SemanticProposalAttemptRecord(write.id, write.revision, outcome, code)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def latest_thread_checkpoint(self, *, owner: str, session_id: str) -> Optional[ThreadCheckpointV1]:
        artifact = self._latest(owner=owner, kind="thread_checkpoint_v1", session_id=session_id)
        return ThreadCheckpointV1.from_payload(json.loads(artifact.payload_json)) if artifact else None

    def latest_project_brief(self, *, owner: str, project_id: str) -> Optional[ProjectBriefV1]:
        db = SessionLocal()
        try:
            artifact = self._preferred_brief_artifact(
                db, owner=owner, kind="project_brief_v1", project_id=project_id,
            )
        finally:
            db.close()
        return ProjectBriefV1.from_payload(json.loads(artifact.payload_json)) if artifact else None

    def latest_personal_brief(self, *, owner: str, session_id: str) -> Optional[PersonalBriefV1]:
        db = SessionLocal()
        try:
            artifact = self._preferred_brief_artifact(
                db, owner=owner, kind="personal_brief_v1", session_id=session_id,
            )
        finally:
            db.close()
        return PersonalBriefV1.from_payload(json.loads(artifact.payload_json)) if artifact else None

    def latest_artifact_manifest(
        self,
        *,
        owner: str,
        kind: str,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Optional[dict[str, object]]:
        """Return safe provenance for one active artifact, never its content."""
        if kind in {"project_brief_v1", "personal_brief_v1"}:
            db = SessionLocal()
            try:
                artifact = self._preferred_brief_artifact(
                    db, owner=owner, kind=kind, session_id=session_id, project_id=project_id,
                )
            finally:
                db.close()
        else:
            artifact = self._latest(
                owner=owner, kind=kind, session_id=session_id, project_id=project_id,
            )
        if artifact is None:
            return None
        try:
            payload = json.loads(artifact.payload_json)
            source_ids = payload.get("source_message_ids") or []
        except (TypeError, json.JSONDecodeError):
            source_ids = []
        return {
            "artifact_revision": artifact.revision,
            "artifact_status": artifact.status,
            "source_through_message_id": artifact.source_through_message_id,
            "source_message_count": len(source_ids) if isinstance(source_ids, list) else 0,
            "source_hash": artifact.source_hash,
        }

    def latest_semantic_proposal(self, *, owner: str, session_id: str) -> Optional[SemanticCheckpointProposalV1]:
        record = self.latest_semantic_proposal_record(owner=owner, session_id=session_id)
        return record.proposal if record else None

    def latest_semantic_proposal_record(self, *, owner: str, session_id: str) -> Optional[SemanticProposalRecord]:
        artifact = self._latest(owner=owner, kind="semantic_checkpoint_proposal_v1", session_id=session_id)
        if artifact is None:
            return None
        return SemanticProposalRecord(
            id=artifact.id,
            revision=artifact.revision,
            status=artifact.status,
            proposal=SemanticCheckpointProposalV1.from_payload(json.loads(artifact.payload_json)),
        )

    def latest_semantic_proposal_attempt(
        self, *, owner: str, session_id: str,
    ) -> Optional[SemanticProposalAttemptRecord]:
        artifact = self._latest(owner=owner, kind="semantic_checkpoint_attempt_v1", session_id=session_id)
        if artifact is None:
            return None
        try:
            payload = json.loads(artifact.payload_json)
            outcome = str(payload["outcome"])
            code = str(payload["code"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ContinuityError("stored semantic proposal attempt is invalid") from exc
        return SemanticProposalAttemptRecord(artifact.id, artifact.revision, outcome, code)

    def semantic_proposal(self, *, owner: str, proposal_id: str) -> SemanticProposalRecord:
        db = SessionLocal()
        try:
            artifact = self._semantic_proposal_artifact(db, owner=owner, proposal_id=proposal_id)
            return SemanticProposalRecord(
                id=artifact.id,
                revision=artifact.revision,
                status=artifact.status,
                proposal=SemanticCheckpointProposalV1.from_payload(json.loads(artifact.payload_json)),
            )
        finally:
            db.close()

    def semantic_proposal_history(
        self, *, owner: str, session_id: str, limit: int = 20,
    ) -> list[SemanticProposalRecord]:
        """Return the immutable, owner-scoped review history for one home.

        Proposals carry compact extracted fields and source fingerprints, not
        raw transcript text.  Keeping superseded and promoted records visible
        lets an owner audit what was considered without making an old proposal
        part of active context.
        """
        if not 1 <= limit <= 100:
            raise ValueError("semantic proposal history limit must be between 1 and 100")
        db = SessionLocal()
        try:
            self._session(db, owner, session_id)
            rows = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.session_id == session_id,
                ContinuityArtifact.kind == "semantic_checkpoint_proposal_v1",
            ).order_by(ContinuityArtifact.revision.desc()).limit(limit).all()
            return [
                SemanticProposalRecord(
                    id=row.id,
                    revision=row.revision,
                    status=row.status,
                    proposal=SemanticCheckpointProposalV1.from_payload(json.loads(row.payload_json)),
                )
                for row in rows
            ]
        finally:
            db.close()

    def checkpoint_catalog(self, *, owner: str, limit: int = 50) -> list[dict[str, object]]:
        """List owner-owned active checkpoints without exposing transcripts."""
        if not 1 <= limit <= 100:
            raise ValueError("checkpoint catalog limit must be between 1 and 100")
        db = SessionLocal()
        try:
            rows = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.kind == "thread_checkpoint_v1",
                ContinuityArtifact.status == "active",
            ).order_by(ContinuityArtifact.updated_at.desc()).limit(limit).all()
            sessions = {
                row.id: row for row in db.query(DbSession).filter(
                    DbSession.owner == owner,
                    DbSession.id.in_([row.session_id for row in rows if row.session_id]),
                ).all()
            }
            catalog: list[dict[str, object]] = []
            for row in rows:
                session = sessions.get(row.session_id)
                if session is None or (session.scope_kind or "general") not in {"personal", "project"}:
                    continue
                checkpoint = ThreadCheckpointV1.from_payload(json.loads(row.payload_json))
                catalog.append({
                    "id": row.id,
                    "revision": row.revision,
                    "session_id": checkpoint.session_id,
                    "session_name": session.name,
                    "scope_kind": session.scope_kind or "general",
                    "project_id": session.project_id,
                    "objective": checkpoint.objective,
                    "derivation_status": checkpoint.derivation_status,
                    "source_through_message_id": checkpoint.source_through_message_id,
                    "source_message_count": len(checkpoint.source_message_ids),
                })
            return catalog
        finally:
            db.close()

    def attach_checkpoint(
        self,
        *,
        owner: str,
        destination_session_id: str,
        source_checkpoint_id: str,
        expires_in_days: int = _DEFAULT_CHECKPOINT_MOUNT_DAYS,
        sensitivity: str = "standard",
        acknowledge_sensitive: bool = False,
    ) -> CheckpointMountRecord:
        """Attach one immutable owner checkpoint as read-only destination context."""
        if isinstance(expires_in_days, bool) or not isinstance(expires_in_days, int) or not 1 <= expires_in_days <= self._MAX_CHECKPOINT_MOUNT_DAYS:
            raise ValueError(f"checkpoint mount expiry must be between 1 and {self._MAX_CHECKPOINT_MOUNT_DAYS} days")
        if sensitivity not in self._CHECKPOINT_MOUNT_SENSITIVITIES:
            raise ValueError("checkpoint mount sensitivity is invalid")
        if sensitivity == "sensitive" and not acknowledge_sensitive:
            raise ScopeConflictError("acknowledge sensitive checkpoint context before attaching it")
        db = SessionLocal()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            destination = self._session(db, owner, destination_session_id)
            destination_scope = self._resolved(db, destination, owner)
            if destination_scope.scope_kind not in {"personal", "project"}:
                raise ScopeConflictError("checkpoint mounts require a Personal or project destination")
            source = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.id == source_checkpoint_id,
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.kind == "thread_checkpoint_v1",
                ContinuityArtifact.status == "active",
            ).first()
            if source is None:
                raise NotFoundError("owner-scoped active checkpoint was not found")
            checkpoint = ThreadCheckpointV1.from_payload(json.loads(source.payload_json))
            source_session = self._session(db, owner, checkpoint.session_id)
            if (source_session.scope_kind or "general") not in {"personal", "project"}:
                raise ScopeConflictError("checkpoint source is not a Companion home")
            existing_rows = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.session_id == destination_session_id,
                ContinuityArtifact.kind == "checkpoint_mount_v1",
                ContinuityArtifact.status == "active",
            ).order_by(ContinuityArtifact.revision.desc()).all()
            for row in existing_rows:
                try:
                    existing_source_id, existing_sensitivity, existing_expires_at = self._checkpoint_mount_payload(row)
                    if existing_source_id == source_checkpoint_id:
                        db.commit()
                        return CheckpointMountRecord(
                            row.id, row.revision, source_checkpoint_id, checkpoint.session_id, checkpoint,
                            existing_sensitivity, existing_expires_at, row.status,
                        )
                except ScopeConflictError:
                    continue
            if len(existing_rows) >= 2:
                raise ScopeConflictError("a Companion home may mount at most two checkpoints")
            revision = max((row.revision for row in db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.session_id == destination_session_id,
                ContinuityArtifact.kind == "checkpoint_mount_v1",
            ).all()), default=0) + 1
            mount = ContinuityArtifact(
                id=uuid.uuid4().hex,
                owner=owner,
                project_id=destination_scope.project_id,
                session_id=destination_session_id,
                kind="checkpoint_mount_v1",
                status="active",
                revision=revision,
                payload_json=json.dumps({
                    "schema_version": 1,
                    "source_checkpoint_id": source_checkpoint_id,
                    "source_session_id": checkpoint.session_id,
                    "sensitivity": sensitivity,
                    "expires_at": (self._utcnow() + timedelta(days=expires_in_days)).isoformat(timespec="seconds"),
                }, sort_keys=True, separators=(",", ":")),
                source_through_message_id=checkpoint.source_through_message_id,
                source_hash=hashlib.sha256(
                    f"checkpoint-mount:{destination_session_id}:{source_checkpoint_id}".encode("utf-8")
                ).hexdigest(),
            )
            db.add(mount)
            db.commit()
            return CheckpointMountRecord(
                mount.id, mount.revision, source_checkpoint_id, checkpoint.session_id, checkpoint,
                sensitivity, json.loads(mount.payload_json)["expires_at"], mount.status,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def checkpoint_mounts(self, *, owner: str, destination_session_id: str) -> list[CheckpointMountRecord]:
        db = SessionLocal()
        try:
            self._session(db, owner, destination_session_id)
            changed = self._expire_checkpoint_mounts(
                db, owner=owner, destination_session_id=destination_session_id,
            )
            rows = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.session_id == destination_session_id,
                ContinuityArtifact.kind == "checkpoint_mount_v1",
                ContinuityArtifact.status == "active",
            ).order_by(ContinuityArtifact.revision.asc()).all()
            mounts: list[CheckpointMountRecord] = []
            for row in rows:
                try:
                    source_id, sensitivity, expires_at = self._checkpoint_mount_payload(row)
                    source = db.query(ContinuityArtifact).filter(
                        ContinuityArtifact.id == source_id,
                        ContinuityArtifact.owner == owner,
                        ContinuityArtifact.kind == "thread_checkpoint_v1",
                    ).first()
                    if source is None:
                        continue
                    checkpoint = ThreadCheckpointV1.from_payload(json.loads(source.payload_json))
                except ScopeConflictError:
                    continue
                mounts.append(CheckpointMountRecord(
                    row.id, row.revision, source_id, checkpoint.session_id, checkpoint,
                    sensitivity, expires_at, row.status,
                ))
            if changed:
                db.commit()
            return mounts
        finally:
            db.close()

    def detach_checkpoint_mount(
        self, *, owner: str, destination_session_id: str, mount_id: str, expected_revision: int,
    ) -> None:
        if expected_revision < 1:
            raise ValueError("checkpoint mount revision must be positive")
        db = SessionLocal()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            self._session(db, owner, destination_session_id)
            self._expire_checkpoint_mounts(db, owner=owner, destination_session_id=destination_session_id)
            mount = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.id == mount_id,
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.session_id == destination_session_id,
                ContinuityArtifact.kind == "checkpoint_mount_v1",
                ContinuityArtifact.status == "active",
            ).first()
            if mount is None:
                raise NotFoundError("active checkpoint mount was not found")
            if mount.revision != expected_revision:
                raise ScopeConflictError("checkpoint mount revision is stale")
            mount.status = "detached"
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def promote_checkpoint_mount(
        self,
        *,
        owner: str,
        destination_session_id: str,
        mount_id: str,
        expected_revision: int,
        selections: dict[str, list[int]],
    ) -> ArtifactWrite:
        """Promote owner-selected mounted checkpoint entries into home state."""
        if expected_revision < 1:
            raise ValueError("checkpoint mount revision must be positive")
        db = SessionLocal()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            destination = self._session(db, owner, destination_session_id)
            scope = self._resolved(db, destination, owner)
            if scope.scope_kind not in {"personal", "project"}:
                raise ScopeConflictError("checkpoint promotion requires a Personal or project destination")
            self._expire_checkpoint_mounts(db, owner=owner, destination_session_id=destination_session_id)
            mount = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.id == mount_id,
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.session_id == destination_session_id,
                ContinuityArtifact.kind == "checkpoint_mount_v1",
                ContinuityArtifact.status == "active",
            ).first()
            if mount is None:
                raise NotFoundError("active checkpoint mount was not found")
            if mount.revision != expected_revision:
                raise ScopeConflictError("checkpoint mount revision is stale")
            source_id, _sensitivity, _expires_at = self._checkpoint_mount_payload(mount)
            source = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.id == source_id,
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.kind == "thread_checkpoint_v1",
            ).first()
            if source is None:
                raise NotFoundError("mounted source checkpoint was not found")
            checkpoint = ThreadCheckpointV1.from_payload(json.loads(source.payload_json))
            selected = selected_checkpoint_entries(checkpoint, selections)
            source_hash = self._promotion_source_hash(mount_id, mount.revision, selected)
            if scope.scope_kind == "project":
                prior_row = self._active_brief(db, owner=owner, kind="project_brief_v1", project_id=scope.project_id)
                prior = ProjectBriefV1.from_payload(json.loads(prior_row.payload_json)) if prior_row else None
                preserved = prior if prior and prior.derivation_status == "accepted" else None
                brief = ProjectBriefV1(
                    project_id=scope.project_id or "",
                    summary=(selected.get("objective") or [preserved.summary if preserved else ""])[0],
                    confirmed_facts=list(preserved.confirmed_facts if preserved else []),
                    accepted_decisions=self._merge(preserved.accepted_decisions if preserved else [], selected.get("accepted_decisions", [])),
                    proposals=self._merge(preserved.proposals if preserved else [], selected.get("proposals", [])),
                    failed_approaches=self._merge(preserved.failed_approaches if preserved else [], selected.get("failures", [])),
                    open_questions=self._merge(preserved.open_questions if preserved else [], selected.get("open_questions", [])),
                    current_plans=self._merge(preserved.current_plans if preserved else [], selected.get("next_actions", [])),
                    source_refs=self._merge(preserved.source_refs if preserved else [], selected.get("artifact_refs", [])),
                    source_session_ids=self._merge(preserved.source_session_ids if preserved else [], [checkpoint.session_id]),
                    source_message_ids=self._merge(preserved.source_message_ids if preserved else [], checkpoint.source_message_ids),
                    source_through_message_id=checkpoint.source_through_message_id,
                    source_revision=source_hash,
                    derivation_status="accepted", derivation_version=1,
                    derivation_method="owner_checkpoint_mount_promotion_v1",
                )
                write = self._write(
                    db, owner=owner, kind="project_brief_v1", session_id=None, project_id=scope.project_id,
                    payload=brief.to_payload(), source_through_message_id=checkpoint.source_through_message_id,
                    source_hash=source_hash, commit=False,
                )
            else:
                prior_row = self._active_brief(db, owner=owner, kind="personal_brief_v1", session_id=destination_session_id)
                prior = PersonalBriefV1.from_payload(json.loads(prior_row.payload_json)) if prior_row else None
                preserved = prior if prior and prior.derivation_status == "accepted" else None
                brief = PersonalBriefV1(
                    owner_id=owner,
                    summary=(selected.get("objective") or [preserved.summary if preserved else ""])[0],
                    confirmed_facts=list(preserved.confirmed_facts if preserved else []),
                    preferences=list(preserved.preferences if preserved else []),
                    ongoing_goals=self._merge(preserved.ongoing_goals if preserved else [], selected.get("next_actions", [])),
                    commitments=self._merge(preserved.commitments if preserved else [], selected.get("accepted_decisions", [])),
                    proposals=self._merge(preserved.proposals if preserved else [], selected.get("proposals", [])),
                    recurring_themes=list(preserved.recurring_themes if preserved else []),
                    failed_approaches=self._merge(preserved.failed_approaches if preserved else [], selected.get("failures", [])),
                    open_questions=self._merge(preserved.open_questions if preserved else [], selected.get("open_questions", [])),
                    artifact_refs=self._merge(preserved.artifact_refs if preserved else [], selected.get("artifact_refs", [])),
                    source_refs=self._merge(preserved.source_refs if preserved else [], checkpoint.source_message_ids),
                    source_message_ids=self._merge(preserved.source_message_ids if preserved else [], checkpoint.source_message_ids),
                    source_through_message_id=checkpoint.source_through_message_id,
                    derivation_status="accepted", derivation_version=1,
                    derivation_method="owner_checkpoint_mount_promotion_v1",
                )
                write = self._write(
                    db, owner=owner, kind="personal_brief_v1", session_id=destination_session_id, project_id=None,
                    payload=brief.to_payload(), source_through_message_id=checkpoint.source_through_message_id,
                    source_hash=source_hash, commit=False,
                )
            db.commit()
            return write
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def promote_semantic_proposal(
        self,
        *,
        owner: str,
        proposal_id: str,
        expected_revision: int,
        selections: dict[str, list[int]],
    ) -> tuple[SemanticProposalRecord, ArtifactWrite, ProjectBriefV1 | PersonalBriefV1]:
        """Promote owner-selected proposal entries into one accepted home brief.

        The proposal remains immutable and the browser supplies only indexes.
        Rehashing the cited DB messages and updating the proposal/brief happen
        in one transaction, so stale source material cannot become memory.
        """
        if expected_revision < 1:
            raise ValueError("expected proposal revision must be positive")
        db = SessionLocal()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            artifact = self._semantic_proposal_artifact(db, owner=owner, proposal_id=proposal_id, lock=True)
            if artifact.revision != expected_revision:
                raise ScopeConflictError("semantic proposal revision is stale")
            if artifact.status != "active":
                raise ScopeConflictError("semantic proposal is no longer available for promotion")
            proposal = SemanticCheckpointProposalV1.from_payload(json.loads(artifact.payload_json))
            session = self._session(db, owner, proposal.session_id)
            scope = self._resolved(db, session, owner)
            if scope.scope_kind != proposal.scope_kind or scope.project_id != proposal.project_id:
                raise ScopeConflictError("semantic proposal no longer matches its session scope")
            if self._source_message_hash(db, session_id=proposal.session_id, source_ids=proposal.source_message_ids) != proposal.source_hash:
                raise ScopeConflictError("semantic proposal source is stale or no longer available")
            selected = selected_proposal_entries(proposal, selections)
            home_source_hash = self._promotion_source_hash(proposal_id, artifact.revision, selected)
            if proposal.scope_kind == "project":
                brief = self._promote_project_brief(db, owner=owner, proposal=proposal, selected=selected)
                write = self._write(
                    db, owner=owner, kind="project_brief_v1", session_id=None, project_id=proposal.project_id,
                    payload=brief.to_payload(), source_through_message_id=proposal.source_through_message_id,
                    source_hash=home_source_hash, commit=False,
                )
            else:
                brief = self._promote_personal_brief(db, owner=owner, proposal=proposal, selected=selected)
                write = self._write(
                    db, owner=owner, kind="personal_brief_v1", session_id=proposal.session_id, project_id=None,
                    payload=brief.to_payload(), source_through_message_id=proposal.source_through_message_id,
                    source_hash=home_source_hash, commit=False,
                )
            artifact.status = "promoted"
            db.commit()
            record = SemanticProposalRecord(artifact.id, artifact.revision, artifact.status, proposal)
            return record, write, brief
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

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

    @staticmethod
    def _merge(existing: list[str], incoming: list[str]) -> list[str]:
        return list(dict.fromkeys([*existing, *incoming]))[:30]

    def _active_brief(self, db, *, owner: str, kind: str, session_id: Optional[str] = None, project_id: Optional[str] = None):
        return self._preferred_brief_artifact(
            db, owner=owner, kind=kind, session_id=session_id, project_id=project_id,
        )

    @staticmethod
    def _artifact_derivation_status(artifact: ContinuityArtifact) -> str:
        try:
            payload = json.loads(artifact.payload_json)
            return str(payload.get("derivation_status") or "legacy_unclassified")
        except (TypeError, json.JSONDecodeError, AttributeError):
            return "legacy_unclassified"

    def _preferred_brief_artifact(
        self,
        db,
        *,
        owner: str,
        kind: str,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Optional[ContinuityArtifact]:
        """Prefer accepted home state, including records superseded by C0 bugs.

        A brief is special: an owner promotion establishes home state while a
        heuristic checkpoint is only a convenience view. Older releases let
        the latter supersede the former. Reading the newest accepted revision
        repairs that presentation safely without rewriting raw history.
        """
        if bool(session_id) == bool(project_id):
            raise ValueError("exactly one artifact scope is required")
        query = db.query(ContinuityArtifact).filter(
            ContinuityArtifact.owner == owner,
            ContinuityArtifact.kind == kind,
        )
        query = query.filter(ContinuityArtifact.session_id == session_id) if session_id else query.filter(ContinuityArtifact.project_id == project_id)
        rows = query.order_by(ContinuityArtifact.revision.desc()).all()
        active = next((row for row in rows if row.status == "active"), None)
        if active and self._artifact_derivation_status(active) == "accepted":
            return active
        return next((row for row in rows if self._artifact_derivation_status(row) == "accepted"), active)

    def _promote_project_brief(self, db, *, owner: str, proposal: SemanticCheckpointProposalV1, selected: dict[str, list[str]]) -> ProjectBriefV1:
        prior_row = self._active_brief(db, owner=owner, kind="project_brief_v1", project_id=proposal.project_id)
        prior = ProjectBriefV1.from_payload(json.loads(prior_row.payload_json)) if prior_row else None
        preserved = prior if prior and prior.derivation_status == "accepted" else None
        return ProjectBriefV1(
            project_id=proposal.project_id or "",
            summary=(selected.get("objective") or [preserved.summary if preserved else ""])[0],
            confirmed_facts=self._merge(preserved.confirmed_facts if preserved else [], selected.get("facts", [])),
            accepted_decisions=self._merge(preserved.accepted_decisions if preserved else [], selected.get("decision_candidates", [])),
            proposals=self._merge(preserved.proposals if preserved else [], selected.get("proposals", [])),
            failed_approaches=self._merge(preserved.failed_approaches if preserved else [], selected.get("failed_approaches", [])),
            open_questions=self._merge(preserved.open_questions if preserved else [], selected.get("open_questions", [])),
            current_plans=self._merge(preserved.current_plans if preserved else [], selected.get("next_actions", [])),
            source_refs=self._merge(preserved.source_refs if preserved else [], selected.get("artifact_refs", [])),
            source_session_ids=self._merge(preserved.source_session_ids if preserved else [], [proposal.session_id]),
            source_message_ids=self._merge(preserved.source_message_ids if preserved else [], proposal.source_message_ids),
            source_through_message_id=proposal.source_through_message_id,
            source_revision=proposal.source_hash,
            derivation_status="accepted", derivation_version=1, derivation_method="owner_promotion_v1",
        )

    def _promote_personal_brief(self, db, *, owner: str, proposal: SemanticCheckpointProposalV1, selected: dict[str, list[str]]) -> PersonalBriefV1:
        prior_row = self._active_brief(db, owner=owner, kind="personal_brief_v1", session_id=proposal.session_id)
        prior = PersonalBriefV1.from_payload(json.loads(prior_row.payload_json)) if prior_row else None
        preserved = prior if prior and prior.derivation_status == "accepted" else None
        return PersonalBriefV1(
            owner_id=owner,
            summary=(selected.get("objective") or [preserved.summary if preserved else ""])[0],
            confirmed_facts=self._merge(preserved.confirmed_facts if preserved else [], selected.get("facts", [])),
            commitments=self._merge(preserved.commitments if preserved else [], selected.get("decision_candidates", [])),
            proposals=self._merge(preserved.proposals if preserved else [], selected.get("proposals", [])),
            failed_approaches=self._merge(preserved.failed_approaches if preserved else [], selected.get("failed_approaches", [])),
            open_questions=self._merge(preserved.open_questions if preserved else [], selected.get("open_questions", [])),
            ongoing_goals=self._merge(preserved.ongoing_goals if preserved else [], selected.get("next_actions", [])),
            artifact_refs=self._merge(preserved.artifact_refs if preserved else [], selected.get("artifact_refs", [])),
            source_refs=self._merge(preserved.source_refs if preserved else [], proposal.source_message_ids),
            source_message_ids=self._merge(preserved.source_message_ids if preserved else [], proposal.source_message_ids),
            source_through_message_id=proposal.source_through_message_id,
            derivation_status="accepted", derivation_version=1, derivation_method="owner_promotion_v1",
        )

    @staticmethod
    def _promotion_source_hash(proposal_id: str, revision: int, selected: dict[str, list[str]]) -> str:
        material = json.dumps({"proposal_id": proposal_id, "revision": revision, "selected": selected}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _source_message_hash(db, *, session_id: str, source_ids: list[str]) -> str:
        rows = db.query(DbMessage).filter(
            DbMessage.session_id == session_id,
            DbMessage.id.in_(source_ids),
        ).all()
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(source_ids):
            return ""
        normalized = []
        for source_id in source_ids:
            row = by_id[source_id]
            try:
                metadata = json.loads(row.meta_data or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["_db_id"] = row.id
            normalized.append({"role": row.role or "", "content": row.content or "", "metadata": metadata})
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _semantic_proposal_artifact(db, *, owner: str, proposal_id: str, lock: bool = False) -> ContinuityArtifact:
        query = db.query(ContinuityArtifact).filter(
            ContinuityArtifact.id == proposal_id,
            ContinuityArtifact.owner == owner,
            ContinuityArtifact.kind == "semantic_checkpoint_proposal_v1",
        )
        if lock and db.get_bind().dialect.name != "sqlite":
            query = query.with_for_update()
        artifact = query.first()
        if artifact is None:
            raise NotFoundError("owner-scoped semantic proposal was not found")
        return artifact

    def _write(self, db, *, owner, kind, session_id, project_id, payload, source_through_message_id, source_hash, commit: bool = True) -> ArtifactWrite:
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
        if commit:
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
