"""G2C APIs for scoped Companion memory and working artifacts."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from core.database import ChatMessage as DbChatMessage, ContinuityArtifact, Project, Session as DbSession, SessionLocal, utcnow_naive
from routes.g1_continuity_routes import _owner
from src.companion_memory import ArtifactConflict, CompanionMemoryStore, MemoryScopeError
from src.companion_capabilities import defaults_for_scope
from src.memory import MemoryStoreUnreadable
from src.continuity.contracts import ContractError, PersonalBriefV1
from src.continuity.semantic_deriver import SemanticDerivationError, derive_semantic_proposal
from src.continuity.store import ContinuityStore, NotFoundError, ScopeConflictError


logger = logging.getLogger(__name__)


def _last_compiled_context(*, owner: str, session_id: str) -> dict | None:
    """Return a deliberately small audit record for the latest scoped turn.

    Context manifests are persisted with assistant messages so a restart cannot
    erase the answer to the useful owner question, "what did this reply use?".
    The inspector must not turn that metadata into another transcript reader:
    it exposes only boolean/count/path summaries already safe for this chat's
    owner, never message tails, provider data, grant IDs, or recalled text.
    """
    def count(value: object, maximum: int) -> int:
        try:
            return min(max(int(value or 0), 0), maximum)
        except (TypeError, ValueError):
            return 0

    db = SessionLocal()
    try:
        session = db.query(DbSession).filter(
            DbSession.id == session_id, DbSession.owner == owner,
        ).first()
        if not session:
            return None
        rows = db.query(DbChatMessage).filter(
            DbChatMessage.session_id == session_id,
            DbChatMessage.role == "assistant",
        ).order_by(DbChatMessage.timestamp.desc()).limit(50).all()
        for row in rows:
            try:
                metadata = json.loads(row.meta_data) if row.meta_data else {}
            except (TypeError, ValueError):
                continue
            manifest = metadata.get("context_manifest") if isinstance(metadata, dict) else None
            if not isinstance(manifest, dict):
                continue
            scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
            artifacts = manifest.get("working_artifacts") if isinstance(manifest.get("working_artifacts"), list) else []
            selected = manifest.get("selected_working_artifact_paths")
            mounts = manifest.get("checkpoint_mounts") if isinstance(manifest.get("checkpoint_mounts"), list) else []
            related = manifest.get("related_project_ids") if isinstance(manifest.get("related_project_ids"), list) else []
            grants = manifest.get("context_grants") if isinstance(manifest.get("context_grants"), list) else []
            tail = manifest.get("transcript_tail_message_ids") if isinstance(manifest.get("transcript_tail_message_ids"), list) else []
            return {
                "recorded_at": row.timestamp.isoformat() if row.timestamp else None,
                "scope_kind": scope.get("kind") if isinstance(scope.get("kind"), str) else None,
                "thread_checkpoint": bool(manifest.get("thread_checkpoint")),
                "project_brief": bool(manifest.get("primary_project_brief")),
                "personal_brief": bool(manifest.get("personal_brief")),
                "related_project_count": min(len(related), 3),
                "mounted_checkpoint_count": min(len(mounts), 2),
                "episodic_hit_count": count(manifest.get("episodic_hit_count"), 20),
                "working_artifact_paths": [
                    item.get("path") for item in artifacts[:20]
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                ],
                "selected_working_artifact_paths": [
                    path for path in (selected if isinstance(selected, list) else [])[:20]
                    if isinstance(path, str)
                ],
                "context_grant_count": min(len(grants), 3),
                "transcript_tail_count": min(len(tail), 80),
            }
        return None
    finally:
        db.close()


class PersonalArtifactWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=512 * 1024)
    expected_revision: int | None = Field(default=None, ge=1)
    source_message_id: str | None = Field(default=None, max_length=128)


class LongPasteCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=3000, max_length=512 * 1024)


class ArtifactUndo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class ArtifactDocumentOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personal_session_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=1000)
    artifact_paths: list[str] = Field(default_factory=list, max_length=20)
    request_message_id: str | None = Field(default=None, max_length=128)


class GrantDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow: bool


class PersonalBriefWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=4000)
    preferences: list[str] = Field(default_factory=list, max_length=30)
    ongoing_goals: list[str] = Field(default_factory=list, max_length=30)
    commitments: list[str] = Field(default_factory=list, max_length=30)
    recurring_themes: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=30)
    artifact_refs: list[str] = Field(default_factory=list, max_length=30)


class SemanticProposalPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    selections: dict[str, list[int]] = Field(min_length=1, max_length=8)


class CheckpointMountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_checkpoint_id: str = Field(min_length=1, max_length=128)
    expires_in_days: int = Field(default=14, ge=1, le=30)
    sensitivity: str = Field(default="standard", pattern="^(standard|sensitive)$")
    acknowledge_sensitive: bool = False


class CheckpointMountDetach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class CheckpointMountPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    selections: dict[str, list[int]] = Field(min_length=1, max_length=7)


class CheckpointSynthesisCreate(BaseModel):
    """Create a fresh scoped chat with exactly two immutable checkpoint mounts."""
    model_config = ConfigDict(extra="forbid")
    destination_session_id: str = Field(min_length=1, max_length=128)
    source_checkpoint_ids: list[str] = Field(min_length=2, max_length=2)


class ProjectRelationWrite(BaseModel):
    """Owner-selected direct relations; IDs are validated against project rows."""
    model_config = ConfigDict(extra="forbid")
    related_project_ids: list[str] = Field(default_factory=list, max_length=3)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ArtifactConflict):
        return HTTPException(409, str(exc))
    return HTTPException(400, str(exc))


def _scope(owner: str, session_id: str) -> tuple[str, str | None]:
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
        if not row:
            raise HTTPException(404, "Companion session was not found")
        return row.scope_kind or "general", row.project_id
    finally:
        db.close()


def setup_companion_memory_routes(session_manager=None, *, memory_manager=None, memory_vector=None) -> APIRouter:
    router = APIRouter(prefix="/api/companion", tags=["companion-memory"])
    memory = CompanionMemoryStore()

    def _legacy_memory_inventory(*, owner: str) -> dict:
        """Return a consent-triggered migration preflight with no memory text.

        This intentionally takes the strict read path. A lenient empty list
        would make a broken JSON store look like a successful empty inventory.
        Nothing in this helper writes, migrates, indexes, or backs up data.
        """
        if memory_manager is None:
            return {
                "available": False,
                "readable": False,
                "reason": "native_memory_unavailable",
                "next_step": "Native memory is unavailable in this Odysseus process; no migration action is available.",
            }
        try:
            entries = memory_manager.load_all_for_update()
        except MemoryStoreUnreadable:
            return {
                "available": True,
                "readable": False,
                "reason": "native_memory_unreadable",
                "next_step": "The legacy memory store could not be read safely. Repair or restore it before any inventory, export, or migration.",
            }
        own_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("owner") == owner]
        ownerless_entries = [entry for entry in entries if isinstance(entry, dict) and not entry.get("owner")]
        foreign_present = any(isinstance(entry, dict) and entry.get("owner") not in {None, "", owner} for entry in entries)
        categories: dict[str, int] = {}
        with_session_provenance = 0
        for entry in own_entries:
            category = str(entry.get("category") or "fact")[:64]
            categories[category] = categories.get(category, 0) + 1
            if entry.get("session_id"):
                with_session_provenance += 1
        return {
            "available": True,
            "readable": True,
            "native_memory": {
                "owner_entry_count": len(own_entries),
                "ownerless_entry_count": len(ownerless_entries),
                "foreign_owner_entries_present": foreign_present,
                "entries_with_session_provenance": with_session_provenance,
                "category_counts": dict(sorted(categories.items())),
            },
            "vector_memory": {
                "configured": memory_vector is not None,
                "healthy": bool(getattr(memory_vector, "healthy", False)),
            },
            "agentmemory": {
                "configured": False,
                "migration_enabled": False,
            },
            "next_step": "Review this inventory, then explicitly request an owner-scoped export/backup before any provider dry run. No records were changed.",
        }

    def _write_legacy_memory_backup(*, owner: str) -> dict:
        """Create an owner-private backup only after an explicit owner action.

        Ownerless legacy entries stay unclaimed: inventory reports their count,
        but this backup never silently copies potentially shared records.  The
        API response exposes only an audit count and digest, not text or paths.
        """
        if memory_manager is None or not getattr(memory_manager, "memory_file", None):
            raise RuntimeError("native_memory_unavailable")
        entries = memory_manager.load_all_for_update()
        owner_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("owner") == owner]
        serialized = json.dumps(owner_entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        owner_key = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:20]
        backup_id = f"native-memory-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"
        backup_dir = Path(memory_manager.memory_file).resolve().parent / "continuity-backups" / owner_key
        backup_path = backup_dir / f"{backup_id}.json"
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(backup_dir, 0o700)
        temporary = backup_dir / f".{backup_id}.tmp"
        try:
            temporary.write_text(serialized, encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, backup_path)
            os.chmod(backup_path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return {
            "backup_id": backup_id,
            "format": "native-memory-owner-backup-v1",
            "entry_count": len(owner_entries),
            "sha256": digest,
            "next_step": (
                "Owner-private backup completed. Review its count with the inventory before separately approving any migration."
            ),
        }

    def _create_synthesis_session(*, owner: str, payload: CheckpointSynthesisCreate) -> dict:
        """Create the user-visible alternative to transcript merging.

        It is intentionally not a provider call. The owner starts a normal turn
        in the fresh chat after seeing which two immutable checkpoints were
        attached. This keeps provider routing, message persistence, and all
        tool/capability policy in the existing chat path.
        """
        if session_manager is None:
            raise HTTPException(503, "Checkpoint synthesis needs the running session service")
        source_ids = [str(value) for value in payload.source_checkpoint_ids]
        if len(set(source_ids)) != 2 or any(not value for value in source_ids):
            raise HTTPException(422, "Choose two different checkpoints to synthesize")
        db = SessionLocal()
        try:
            destination = db.query(DbSession).filter(
                DbSession.id == payload.destination_session_id, DbSession.owner == owner,
            ).first()
            if not destination:
                raise HTTPException(404, "Companion destination session was not found")
            scope_kind = destination.scope_kind or "general"
            if scope_kind not in {"personal", "project"}:
                raise HTTPException(409, "Checkpoint synthesis requires a Personal or project destination")
            if not destination.endpoint_url or not destination.model:
                raise HTTPException(409, "Choose a registered model before creating a synthesis chat")
            source_rows = db.query(ContinuityArtifact).filter(
                ContinuityArtifact.owner == owner,
                ContinuityArtifact.id.in_(source_ids),
                ContinuityArtifact.kind == "thread_checkpoint_v1",
                ContinuityArtifact.status == "active",
            ).all()
            if len(source_rows) != 2:
                raise HTTPException(404, "One or both selected checkpoints are not available")
            source_sessions = {
                row.id: db.query(DbSession).filter(
                    DbSession.id == row.session_id, DbSession.owner == owner,
                ).first() for row in source_rows
            }
            if any(session is None or (session.scope_kind or "general") not in {"personal", "project"}
                   for session in source_sessions.values()):
                raise HTTPException(409, "Checkpoint synthesis requires Companion-home sources")
            source_names = [str(source_sessions[source_id].name or "Checkpoint")[:40] for source_id in source_ids]
            endpoint_url, model = destination.endpoint_url, destination.model
            headers = destination.headers or {}
            endpoint_id = destination.endpoint_id or ""
            harness_kind = destination.harness_kind or "native"
            project_id = destination.project_id if scope_kind == "project" else None
        finally:
            db.close()

        session_id = str(uuid.uuid4())
        name = f"Synthesis — {source_names[0]} + {source_names[1]}"[:120]
        try:
            session = session_manager.create_session(session_id, name, endpoint_url, model, owner=owner)
            session.headers = headers
            db = SessionLocal()
            try:
                row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).one()
                row.headers = headers
                row.scope_kind, row.project_id = scope_kind, project_id
                row.endpoint_id, row.harness_kind, row.is_scope_primary = endpoint_id, harness_kind, False
                row.capability_grants = defaults_for_scope(scope_kind)
                row.updated_at = utcnow_naive()
                db.commit()
            finally:
                db.close()
            store = ContinuityStore()
            for source_id in source_ids:
                store.attach_checkpoint(
                    owner=owner, destination_session_id=session_id, source_checkpoint_id=source_id,
                )
            return {"id": session_id, "name": name, "scope_kind": scope_kind, "project_id": project_id,
                    "source_checkpoint_ids": source_ids}
        except HTTPException:
            try:
                session_manager.delete_session(session_id)
            except Exception:
                logger.exception("Could not clean up incomplete checkpoint synthesis session %s", session_id)
            raise
        except Exception as exc:
            try:
                session_manager.delete_session(session_id)
            except Exception:
                logger.exception("Could not clean up incomplete checkpoint synthesis session %s", session_id)
            logger.exception("Checkpoint synthesis creation failed")
            raise HTTPException(500, "Could not create the checkpoint synthesis chat") from exc

    @router.get("/memory/sessions/{session_id}")
    def memory_context(session_id: str, request: Request):
        try:
            owner = _owner(request); scope_kind, project_id = _scope(owner, session_id)
            if scope_kind not in {"personal", "project", "computer"}:
                raise HTTPException(409, "Memory context is available only for Companion homes")
            store = ContinuityStore()
            checkpoint = store.latest_thread_checkpoint(owner=owner, session_id=session_id)
            project_brief = store.latest_project_brief(owner=owner, project_id=project_id) if project_id else None
            personal_brief = store.latest_personal_brief(owner=owner, session_id=session_id) if scope_kind == "personal" else None
            proposal = store.latest_semantic_proposal_record(owner=owner, session_id=session_id)
            proposal_history = store.semantic_proposal_history(owner=owner, session_id=session_id)
            proposal_attempt = store.latest_semantic_proposal_attempt(owner=owner, session_id=session_id)
            mounts = store.checkpoint_mounts(owner=owner, destination_session_id=session_id) if scope_kind in {"personal", "project"} else []
            return {
                "scope_kind": scope_kind, "project_id": project_id,
                "thread_checkpoint": checkpoint.to_payload() if checkpoint else None,
                "project_brief": project_brief.to_payload() if project_brief else None,
                "personal_brief": personal_brief.to_payload() if personal_brief else None,
                "semantic_proposal": ({
                    "id": proposal.id,
                    "revision": proposal.revision,
                    "status": proposal.status,
                    "proposal": proposal.proposal.to_payload(),
                } if proposal else None),
                "semantic_proposal_history": [
                    {
                        "id": record.id,
                        "revision": record.revision,
                        "status": record.status,
                        "proposal": record.proposal.to_payload(),
                    }
                    for record in proposal_history
                ],
                "semantic_proposal_attempt": ({
                    "revision": proposal_attempt.revision,
                    "outcome": proposal_attempt.outcome,
                    "code": proposal_attempt.code,
                } if proposal_attempt else None),
                "checkpoint_mounts": [
                    {
                        "id": mount.id,
                        "revision": mount.revision,
                        "source_checkpoint_id": mount.source_checkpoint_id,
                        "source_session_id": mount.source_session_id,
                        "objective": mount.checkpoint.objective,
                        "derivation_status": mount.checkpoint.derivation_status,
                        "source_through_message_id": mount.checkpoint.source_through_message_id,
                        "source_message_count": len(mount.checkpoint.source_message_ids),
                        "sensitivity": mount.sensitivity,
                        "expires_at": mount.expires_at,
                        "status": mount.status,
                        "checkpoint": mount.checkpoint.to_payload(),
                    }
                    for mount in mounts
                ],
                "checkpoint_catalog": store.checkpoint_catalog(owner=owner) if scope_kind in {"personal", "project"} else [],
                "last_compiled_context": _last_compiled_context(owner=owner, session_id=session_id),
                "related_project_catalog": (
                    store.related_project_catalog(owner=owner, project_id=project_id)
                    if scope_kind == "project" and project_id else []
                ),
                "artifacts": memory.list_artifacts(owner=owner, scope_kind=scope_kind, project_id=project_id),
                "grants": memory.approved_grants(owner=owner, personal_session_id=session_id) if scope_kind == "personal" else [],
                "pending_grants": memory.pending_grants(owner=owner, personal_session_id=session_id) if scope_kind == "personal" else [],
                "project_catalog": store.project_catalog(owner=owner) if scope_kind == "personal" else [],
            }
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Companion memory storage failed for session %s", session_id)
            raise HTTPException(
                503,
                "Companion memory storage needs a one-time update. Restart Odysseus, then try again.",
            ) from exc

    @router.get("/memory/legacy-inventory")
    def legacy_memory_inventory(request: Request):
        """Owner-triggered C4 preflight; aggregate-only and strictly read-only."""
        try:
            return _legacy_memory_inventory(owner=_owner(request))
        except Exception as exc:
            logger.exception("Legacy memory inventory failed")
            raise HTTPException(503, "Legacy memory inventory could not be completed safely. No data was changed.") from exc

    @router.post("/memory/legacy-backup")
    def legacy_memory_backup(request: Request):
        """Explicit C4 backup gate; it never indexes, recalls, or migrates."""
        try:
            return _write_legacy_memory_backup(owner=_owner(request))
        except MemoryStoreUnreadable as exc:
            logger.exception("Legacy memory backup was blocked by an unreadable source")
            raise HTTPException(503, "Legacy memory could not be backed up safely. No migration was started.") from exc
        except Exception as exc:
            logger.exception("Legacy memory backup failed")
            raise HTTPException(503, "Legacy memory backup could not be completed. No migration was started.") from exc

    @router.put("/memory/projects/{project_id}/relations")
    def update_project_relations(project_id: str, payload: ProjectRelationWrite, request: Request):
        try:
            owner = _owner(request)
            store = ContinuityStore()
            store.set_related_projects(
                owner=owner, project_id=project_id, related_project_ids=payload.related_project_ids,
            )
            return {"project_id": project_id, "related_projects": store.related_project_catalog(
                owner=owner, project_id=project_id,
            )}
        except NotFoundError as exc:
            raise HTTPException(404, "Project was not found") from exc
        except (ScopeConflictError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception("Companion project relation update failed for project %s", project_id)
            raise HTTPException(
                500,
                "Related project access could not be updated. No project context was shared.",
            ) from exc

    @router.post("/memory/sessions/{session_id}/semantic-proposals")
    async def create_semantic_proposal(session_id: str, request: Request):
        """Derive one bounded, tool-free proposal from an owner session."""
        try:
            result = await derive_semantic_proposal(owner=_owner(request), session_id=session_id)
        except SemanticDerivationError as exc:
            status = {
                "session_not_found": 404,
                "scope_denied": 409,
                "model_unavailable": 409,
                "source_stale": 409,
                "source_unavailable": 422,
                "proposal_invalid": 422,
                "provider_failed": 502,
            }.get(exc.code, 500)
            if status >= 500:
                logger.exception("Semantic proposal derivation failed for session %s", session_id, exc_info=exc)
            raise HTTPException(status, str(exc)) from exc
        return {
            "id": result.record.id,
            "revision": result.record.revision,
            "status": "proposed",
            "proposal": result.record.proposal.to_payload(),
        }

    @router.get("/memory/semantic-proposals/{proposal_id}")
    def read_semantic_proposal(proposal_id: str, request: Request):
        try:
            record = ContinuityStore().semantic_proposal(owner=_owner(request), proposal_id=proposal_id)
            return {"id": record.id, "revision": record.revision, "status": record.status, "proposal": record.proposal.to_payload()}
        except NotFoundError as exc:
            raise HTTPException(404, "Semantic proposal was not found") from exc

    @router.post("/memory/sessions/{session_id}/checkpoint-mounts")
    def attach_checkpoint_mount(session_id: str, payload: CheckpointMountCreate, request: Request):
        try:
            record = ContinuityStore().attach_checkpoint(
                owner=_owner(request), destination_session_id=session_id,
                source_checkpoint_id=payload.source_checkpoint_id,
                expires_in_days=payload.expires_in_days,
                sensitivity=payload.sensitivity,
                acknowledge_sensitive=payload.acknowledge_sensitive,
            )
            return {
                "id": record.id, "revision": record.revision,
                "source_checkpoint_id": record.source_checkpoint_id,
                "source_session_id": record.source_session_id,
                "objective": record.checkpoint.objective,
                "derivation_status": record.checkpoint.derivation_status,
                "sensitivity": record.sensitivity,
                "expires_at": record.expires_at,
                "status": record.status,
            }
        except NotFoundError as exc:
            raise HTTPException(404, "Checkpoint or Companion session was not found") from exc
        except ScopeConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/memory/checkpoint-synthesis")
    def create_checkpoint_synthesis(payload: CheckpointSynthesisCreate, request: Request):
        return _create_synthesis_session(owner=_owner(request), payload=payload)

    @router.delete("/memory/sessions/{session_id}/checkpoint-mounts/{mount_id}")
    def detach_checkpoint_mount(session_id: str, mount_id: str, payload: CheckpointMountDetach, request: Request):
        try:
            ContinuityStore().detach_checkpoint_mount(
                owner=_owner(request), destination_session_id=session_id,
                mount_id=mount_id, expected_revision=payload.expected_revision,
            )
            return {"detached": True}
        except NotFoundError as exc:
            raise HTTPException(404, "Checkpoint mount was not found") from exc
        except ScopeConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/memory/sessions/{session_id}/checkpoint-mounts/{mount_id}/promote")
    def promote_checkpoint_mount(session_id: str, mount_id: str, payload: CheckpointMountPromotion, request: Request):
        try:
            write = ContinuityStore().promote_checkpoint_mount(
                owner=_owner(request), destination_session_id=session_id, mount_id=mount_id,
                expected_revision=payload.expected_revision, selections=payload.selections,
            )
            return {"brief_revision": write.revision, "promoted": True}
        except NotFoundError as exc:
            raise HTTPException(404, "Checkpoint mount was not found") from exc
        except (ContractError, ValueError) as exc:
            raise HTTPException(422, "The selected checkpoint entries are invalid") from exc
        except ScopeConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/memory/sessions/{session_id}/semantic-proposals")
    def list_semantic_proposals(session_id: str, request: Request):
        try:
            owner = _owner(request)
            scope_kind, _project_id = _scope(owner, session_id)
            if scope_kind not in {"personal", "project"}:
                raise HTTPException(409, "Semantic proposals are available only for Personal and project homes")
            records = ContinuityStore().semantic_proposal_history(owner=owner, session_id=session_id)
            return {
                "proposals": [
                    {
                        "id": record.id,
                        "revision": record.revision,
                        "status": record.status,
                        "proposal": record.proposal.to_payload(),
                    }
                    for record in records
                ]
            }
        except NotFoundError as exc:
            raise HTTPException(404, "Companion session was not found") from exc

    @router.post("/memory/semantic-proposals/{proposal_id}/promote")
    def promote_semantic_proposal(proposal_id: str, payload: SemanticProposalPromotion, request: Request):
        try:
            record, write, brief = ContinuityStore().promote_semantic_proposal(
                owner=_owner(request), proposal_id=proposal_id,
                expected_revision=payload.expected_revision, selections=payload.selections,
            )
            return {
                "proposal": {"id": record.id, "revision": record.revision, "status": record.status},
                "brief": brief.to_payload(),
                "brief_revision": write.revision,
            }
        except NotFoundError as exc:
            raise HTTPException(404, "Semantic proposal was not found") from exc
        except (ContractError, ValueError) as exc:
            raise HTTPException(422, "The selected proposal entries are invalid") from exc
        except ScopeConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            logger.exception("Semantic proposal promotion failed for %s", proposal_id)
            raise HTTPException(500, "The semantic proposal could not be promoted. No memory was changed.") from exc

    @router.post("/artefacts/personal", include_in_schema=False)
    @router.post("/artifacts/personal")
    def write_personal_artifact(payload: PersonalArtifactWrite, request: Request):
        try:
            return memory.write_personal_artifact(owner=_owner(request), **payload.model_dump())
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Companion artifact storage failed for session %s", payload.session_id)
            raise HTTPException(
                503,
                "Artifact storage needs a one-time update. Restart Odysseus, then try again.",
            ) from exc

    @router.post("/artifacts/computer")
    def write_computer_artifact(payload: PersonalArtifactWrite, request: Request):
        try:
            return memory.write_computer_artifact(owner=_owner(request), **payload.model_dump())
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Computer artifact storage failed for session %s", payload.session_id)
            raise HTTPException(
                503,
                "Computer artifact storage needs a one-time update. Restart Odysseus, then try again.",
            ) from exc

    @router.post("/artifacts/capture-paste")
    def capture_long_paste(payload: LongPasteCapture, request: Request):
        try:
            return memory.capture_long_paste(owner=_owner(request), **payload.model_dump())
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Long paste capture failed for session %s", payload.session_id)
            raise HTTPException(503, "The pasted text could not be stored. It remains in the composer.") from exc

    @router.get("/artefacts/personal/{artifact_id}", include_in_schema=False)
    @router.get("/artifacts/personal/{artifact_id}")
    def read_personal_artifact(artifact_id: str, request: Request):
        try:
            return memory.get_personal_artifact(owner=_owner(request), artifact_id=artifact_id)
        except MemoryScopeError as exc:
            raise _error(exc) from exc

    @router.get("/artifacts/computer/{artifact_id}")
    def read_computer_artifact(artifact_id: str, request: Request):
        try:
            return memory.get_computer_artifact(owner=_owner(request), artifact_id=artifact_id)
        except MemoryScopeError as exc:
            raise _error(exc) from exc

    @router.post("/artifacts/personal/{artifact_id}/document")
    def open_personal_artifact_document(artifact_id: str, payload: ArtifactDocumentOpen, request: Request):
        try:
            return memory.open_personal_artifact_document(
                owner=_owner(request), session_id=payload.session_id, artifact_id=artifact_id,
            )
        except MemoryScopeError as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Companion artifact document bridge failed for %s", artifact_id)
            raise HTTPException(503, "Artifact editor is temporarily unavailable. Restart Odysseus, then try again.") from exc

    @router.post("/artifacts/computer/{artifact_id}/document")
    def open_computer_artifact_document(artifact_id: str, payload: ArtifactDocumentOpen, request: Request):
        try:
            return memory.open_computer_artifact_document(
                owner=_owner(request), session_id=payload.session_id, artifact_id=artifact_id,
            )
        except MemoryScopeError as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Computer artifact document bridge failed for %s", artifact_id)
            raise HTTPException(503, "Artifact editor is temporarily unavailable. Restart Odysseus, then try again.") from exc

    @router.post("/artefacts/personal/{artifact_id}/undo", include_in_schema=False)
    @router.post("/artifacts/personal/{artifact_id}/undo")
    def undo_personal_artifact(artifact_id: str, payload: ArtifactUndo, request: Request):
        try:
            return memory.undo_personal_artifact(owner=_owner(request), artifact_id=artifact_id,
                                                  expected_revision=payload.expected_revision)
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc

    @router.post("/artefacts/personal/{artifact_id}/delete", include_in_schema=False)
    @router.post("/artifacts/personal/{artifact_id}/delete")
    def delete_personal_artifact(artifact_id: str, payload: ArtifactUndo, request: Request):
        try:
            return memory.delete_personal_artifact(owner=_owner(request), artifact_id=artifact_id,
                                                   expected_revision=payload.expected_revision)
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc

    @router.post("/artefacts/personal/{artifact_id}/restore", include_in_schema=False)
    @router.post("/artifacts/personal/{artifact_id}/restore")
    def restore_personal_artifact(artifact_id: str, payload: ArtifactUndo, request: Request):
        try:
            return memory.restore_personal_artifact(owner=_owner(request), artifact_id=artifact_id,
                                                    expected_revision=payload.expected_revision)
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc

    @router.post("/context-grants")
    def create_grant(payload: GrantCreate, request: Request):
        try:
            return memory.create_grant_request(owner=_owner(request), **payload.model_dump())
        except MemoryScopeError as exc:
            raise _error(exc) from exc

    @router.post("/context-grants/{grant_id}/decision")
    def decide_grant(grant_id: str, payload: GrantDecision, request: Request):
        try:
            return memory.decide_grant(owner=_owner(request), grant_id=grant_id, allow=payload.allow)
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc

    @router.post("/personal-brief")
    def write_personal_brief(payload: PersonalBriefWrite, request: Request):
        owner = _owner(request)
        scope_kind, _project_id = _scope(owner, payload.session_id)
        if scope_kind != "personal":
            raise HTTPException(409, "Personal brief requires a Personal Advisor session")
        values = payload.model_dump()
        session_id = values.pop("session_id")
        brief = PersonalBriefV1(
            owner_id=owner,
            derivation_status="accepted",
            derivation_version=1,
            derivation_method="owner_edit_v1",
            **values,
        )
        source_hash = hashlib.sha256(json.dumps(brief.to_payload(), sort_keys=True).encode()).hexdigest()
        try:
            result = ContinuityStore().write_personal_brief(owner=owner, session_id=session_id, brief=brief, source_hash=source_hash)
        except Exception as exc:
            raise _error(exc) from exc
        return {"revision": result.revision, "brief": brief.to_payload()}

    return router
