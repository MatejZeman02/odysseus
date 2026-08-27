"""G2C APIs for scoped Companion memory and working artifacts."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from core.database import (
    ChatMessage as DbChatMessage, ContinuityArtifact, LegacyMemoryMigrationReview,
    Project, Session as DbSession, SessionLocal, utcnow_naive,
)
from routes.g1_continuity_routes import _owner
from src.companion_memory import ArtifactConflict, CompanionMemoryStore, MemoryScopeError
from src.companion_capabilities import defaults_for_scope
from src.memory import MemoryStoreUnreadable
from src.continuity.contracts import ContractError, PersonalBriefV1, ProjectBriefV1, ThreadCheckpointV1
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
            raw_hit_kinds = manifest.get("episodic_hit_kinds") if isinstance(manifest.get("episodic_hit_kinds"), dict) else {}
            hit_kinds: dict[str, int] = {}
            for kind, raw_count in raw_hit_kinds.items():
                if not isinstance(kind, str) or not re.fullmatch(r"[a-z0-9_.-]{1,64}", kind):
                    continue
                safe_count = count(raw_count, 20)
                if safe_count:
                    hit_kinds[kind] = safe_count
            return {
                "recorded_at": row.timestamp.isoformat() if row.timestamp else None,
                "scope_kind": scope.get("kind") if isinstance(scope.get("kind"), str) else None,
                "thread_checkpoint": bool(manifest.get("thread_checkpoint")),
                "project_brief": bool(manifest.get("primary_project_brief")),
                "personal_brief": bool(manifest.get("personal_brief")),
                "related_project_count": min(len(related), 3),
                "mounted_checkpoint_count": min(len(mounts), 2),
                "episodic_hit_count": count(manifest.get("episodic_hit_count"), 20),
                "episodic_hit_kinds": dict(sorted(hit_kinds.items())[:8]),
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


class LegacyMigrationDryRun(BaseModel):
    """Reference one explicit owner-private backup, never a browser path."""
    model_config = ConfigDict(extra="forbid")
    backup_id: str = Field(min_length=1, max_length=128)


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
    # ``None`` means leave the accepted field untouched. The Context editor
    # currently changes only the summary; empty defaults would otherwise erase
    # owner-approved lists it does not render.
    summary: str | None = Field(default=None, max_length=4000)
    preferences: list[str] | None = Field(default=None, max_length=30)
    ongoing_goals: list[str] | None = Field(default=None, max_length=30)
    commitments: list[str] | None = Field(default=None, max_length=30)
    recurring_themes: list[str] | None = Field(default=None, max_length=30)
    open_questions: list[str] | None = Field(default=None, max_length=30)
    artifact_refs: list[str] | None = Field(default=None, max_length=30)


class ProjectBriefWrite(BaseModel):
    """Owner edit of a project-home summary; omitted state remains intact."""
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    summary: str | None = Field(default=None, max_length=4000)


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


def _index_accepted_home_brief(
    *, owner: str, session_id: str, scope_kind: str, project_id: str | None,
    source_id: str, brief: PersonalBriefV1 | object,
) -> None:
    """Index only an owner-approved brief, never a heuristic checkpoint.

    Failure is deliberately non-fatal. The accepted brief remains exact context
    in the continuity store; episodic retrieval is merely an accelerator.
    """
    if scope_kind not in {"personal", "project"} or not source_id:
        return
    try:
        payload = brief.to_payload() if hasattr(brief, "to_payload") else {}
        values = [
            str(payload.get("summary") or ""),
            *[str(item) for item in payload.get("confirmed_facts", [])],
            *[str(item) for item in payload.get("ongoing_goals", [])],
            *[str(item) for item in payload.get("open_questions", [])],
            *[str(item) for item in payload.get("current_plans", [])],
        ]
        content = "\n".join(value for value in values if value.strip())
        if not content:
            return
        from src.scoped_memory import ScopedMemoryIndex
        ScopedMemoryIndex().replace_scope_source(
            owner=owner, scope_kind=scope_kind, project_id=project_id,
            session_id=session_id, source_kind="accepted_home_brief",
            source_id=source_id, content=content,
        )
    except Exception:
        logger.warning("Accepted home brief could not be added to episodic index", exc_info=True)


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
        manifest_path = backup_dir / f"{backup_id}.manifest"
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(backup_dir, 0o700)
        temporary = backup_dir / f".{backup_id}.tmp"
        temporary_manifest = backup_dir / f".{backup_id}.manifest.tmp"
        manifest = json.dumps({
            "format": "native-memory-owner-backup-manifest-v1",
            "backup_id": backup_id,
            "owner_key": owner_key,
            "entry_count": len(owner_entries),
            "sha256": digest,
        }, sort_keys=True, separators=(",", ":"))
        try:
            temporary.write_text(serialized, encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary_manifest.write_text(manifest, encoding="utf-8")
            os.chmod(temporary_manifest, 0o600)
            os.replace(temporary, backup_path)
            os.chmod(backup_path, 0o600)
            # The manifest is published last.  A crash between the two files
            # leaves an intentionally unusable backup rather than one whose
            # origin/integrity cannot later be proven.
            os.replace(temporary_manifest, manifest_path)
            os.chmod(manifest_path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
            if temporary_manifest.exists():
                temporary_manifest.unlink(missing_ok=True)
        return {
            "backup_id": backup_id,
            "format": "native-memory-owner-backup-v1",
            "entry_count": len(owner_entries),
            "sha256": digest,
            "next_step": (
                "Owner-private backup completed. Review its count with the inventory before separately approving any migration."
            ),
        }

    def _legacy_backup_path(*, owner: str, backup_id: str) -> Path:
        """Resolve only a server-created owner backup, never a client path."""
        if memory_manager is None or not getattr(memory_manager, "memory_file", None):
            raise RuntimeError("native_memory_unavailable")
        if not re.fullmatch(r"native-memory-\d{8}T\d{6}Z-[a-f0-9]{10}", backup_id):
            raise ValueError("backup reference is invalid")
        owner_key = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:20]
        root = (Path(memory_manager.memory_file).resolve().parent / "continuity-backups" / owner_key).resolve()
        raw_candidate = root / f"{backup_id}.json"
        candidate = raw_candidate.resolve()
        if raw_candidate.is_symlink() or candidate.parent != root or not candidate.is_file():
            raise FileNotFoundError("owner-private backup was not found")
        return candidate

    def _read_legacy_backup(*, owner: str, backup_id: str) -> list[dict]:
        """Read one server-created backup only if its immutable audit binds it.

        The returned list stays internal to the migration path.  The browser
        sees aggregate results only, while later migration stages can rely on
        this exact content digest rather than a stale UI response.
        """
        backup_path = _legacy_backup_path(owner=owner, backup_id=backup_id)
        manifest_path = backup_path.with_suffix(".manifest")
        try:
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise ValueError("owner-private backup integrity manifest was not found")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = json.loads(backup_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("owner-private backup could not be read safely") from exc
        if not isinstance(manifest, dict) or not isinstance(entries, list):
            raise ValueError("owner-private backup has an invalid format")
        owner_key = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:20]
        canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        expected = {
            "format": "native-memory-owner-backup-manifest-v1",
            "backup_id": backup_id,
            "owner_key": owner_key,
            "entry_count": len(entries),
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise ValueError("owner-private backup integrity check failed")
        return entries

    def _legacy_memory_dry_run(*, owner: str, backup_id: str) -> dict:
        """Classify a backed-up legacy store without migrating any record.

        Legacy native memory has owner/session data but no trustworthy home,
        project, provenance, sensitivity, or expiry binding.  A dry run may
        therefore identify *candidates* only; it never infers a target scope or
        writes to a provider.  Reports deliberately omit memory text, IDs, and
        individual fingerprints.
        """
        entries = _read_legacy_backup(owner=owner, backup_id=backup_id)

        db = SessionLocal()
        try:
            sessions = {
                row.id: row for row in db.query(DbSession).filter(DbSession.owner == owner).all()
            }
        finally:
            db.close()

        counts = {
            "entries_considered": 0,
            "eligible_scope_candidates": 0,
            "needs_owner_assignment": 0,
            "duplicate_candidates": 0,
        }
        by_scope = {"personal": 0, "project": 0}
        rejected: dict[str, int] = {}
        fingerprints: set[str] = set()
        review_candidates: list[dict] = []

        def reject(reason: str) -> None:
            rejected[reason] = rejected.get(reason, 0) + 1

        for ordinal, entry in enumerate(entries):
            counts["entries_considered"] += 1
            if not isinstance(entry, dict) or entry.get("owner") != owner:
                reject("invalid_owner_record")
                review_candidates.append({"ordinal": ordinal, "classification": "rejected", "reason": "invalid_owner_record"})
                continue
            text = entry.get("text")
            if not isinstance(text, str) or not text.strip():
                reject("missing_text")
                review_candidates.append({"ordinal": ordinal, "classification": "rejected", "reason": "missing_text"})
                continue
            if len(text.encode("utf-8")) > 512 * 1024:
                reject("text_too_large")
                review_candidates.append({"ordinal": ordinal, "classification": "rejected", "reason": "text_too_large"})
                continue
            category = entry.get("category", "fact")
            if not isinstance(category, str) or len(category) > 64:
                reject("invalid_category")
                review_candidates.append({"ordinal": ordinal, "classification": "rejected", "reason": "invalid_category"})
                continue
            fingerprint = hashlib.sha256(
                (category + "\x00" + text.strip()).encode("utf-8")
            ).hexdigest()
            duplicate = fingerprint in fingerprints
            if duplicate:
                counts["duplicate_candidates"] += 1
            else:
                fingerprints.add(fingerprint)

            session_id = entry.get("session_id")
            session = sessions.get(session_id) if isinstance(session_id, str) else None
            scope_kind = (session.scope_kind or "general") if session else "general"
            if scope_kind in by_scope:
                by_scope[scope_kind] += 1
                counts["eligible_scope_candidates"] += 1
                classification = "eligible"
            else:
                # Ownerless provenance does not become Personal memory by
                # default. A later owner review must select a scope/source.
                counts["needs_owner_assignment"] += 1
                classification = "needs_owner_assignment"
            # No memory text or legacy entry ID is persisted in this plan.  A
            # future explicit assignment step re-reads the digest-bound backup
            # and maps this opaque candidate token to its current entry.
            review_candidates.append({
                "ordinal": ordinal,
                "candidate_token": hashlib.sha256(
                    f"{backup_id}\x00{ordinal}\x00{fingerprint}".encode("utf-8")
                ).hexdigest()[:32],
                "classification": classification,
                "duplicate": duplicate,
                "category": category,
                "source_scope_kind": scope_kind if scope_kind in by_scope else None,
                "source_project_id": session.project_id if session and scope_kind == "project" else None,
            })

        canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        backup_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        review_id = _persist_legacy_migration_review(
            owner=owner, backup_id=backup_id, backup_sha256=backup_sha256,
            counts=counts, scope_candidates=by_scope, rejected=rejected,
            candidates=review_candidates,
        )
        return {
            "format": "native-memory-scoped-dry-run-v1",
            "backup_sha256": backup_sha256,
            "review_id": review_id,
            **counts,
            "scope_candidates": by_scope,
            "rejected": dict(sorted(rejected.items())),
            "migration_started": False,
            "next_step": (
                "Review candidate counts and unresolved legacy entries. A future migration must require "
                "explicit scope/source selection and keep a rollback journal; no record was indexed or changed."
            ),
        }

    def _persist_legacy_migration_review(
        *, owner: str, backup_id: str, backup_sha256: str, counts: dict,
        scope_candidates: dict, rejected: dict, candidates: list[dict],
    ) -> str:
        """Persist an owner-review record without staging memory text.

        Re-running a dry run against the same immutable backup returns the
        same review record.  This prevents a browser refresh from silently
        changing the candidate set between future owner assignment and apply.
        """
        db = SessionLocal()
        try:
            existing = db.query(LegacyMemoryMigrationReview).filter(
                LegacyMemoryMigrationReview.owner == owner,
                LegacyMemoryMigrationReview.backup_id == backup_id,
            ).first()
            if existing:
                if existing.backup_sha256 != backup_sha256:
                    raise ValueError("owner-private backup integrity check failed")
                return existing.id
            plan = {
                "schema_version": 1,
                "backup_id": backup_id,
                "backup_sha256": backup_sha256,
                "counts": dict(counts),
                "scope_candidates": dict(scope_candidates),
                "rejected": dict(sorted(rejected.items())),
                "candidates": candidates,
            }
            review = LegacyMemoryMigrationReview(
                id=uuid.uuid4().hex, owner=owner, backup_id=backup_id,
                backup_sha256=backup_sha256, status="review_ready", revision=1,
                plan_json=json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                journal_json="[]",
            )
            db.add(review)
            db.commit()
            return review.id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _legacy_migration_review_payload(review: LegacyMemoryMigrationReview) -> dict:
        """Project a private review row to its aggregate browser-safe surface."""
        try:
            plan = json.loads(review.plan_json)
            journal = json.loads(review.journal_json or "[]")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("migration review could not be read safely") from exc
        if (
            not isinstance(plan, dict)
            or plan.get("schema_version") != 1
            or not isinstance(journal, list)
        ):
            raise ValueError("migration review could not be read safely")
        counts = plan.get("counts") if isinstance(plan.get("counts"), dict) else {}
        scopes = plan.get("scope_candidates") if isinstance(plan.get("scope_candidates"), dict) else {}
        rejected = plan.get("rejected") if isinstance(plan.get("rejected"), dict) else {}
        candidates = plan.get("candidates") if isinstance(plan.get("candidates"), list) else []

        def bounded_count(value: object) -> int:
            try:
                return min(max(int(value or 0), 0), 1_000_000)
            except (TypeError, ValueError):
                return 0

        return {
            "review_id": review.id,
            "revision": review.revision,
            "status": review.status,
            "backup_sha256": review.backup_sha256,
            "entries_considered": bounded_count(counts.get("entries_considered")),
            "eligible_scope_candidates": bounded_count(counts.get("eligible_scope_candidates")),
            "needs_owner_assignment": bounded_count(counts.get("needs_owner_assignment")),
            "duplicate_candidates": bounded_count(counts.get("duplicate_candidates")),
            "scope_candidates": {
                "personal": bounded_count(scopes.get("personal")),
                "project": bounded_count(scopes.get("project")),
            },
            "rejected": {
                key: bounded_count(value)
                for key, value in rejected.items()
                if isinstance(key, str) and re.fullmatch(r"[a-z_]{1,64}", key)
            },
            "candidate_count": min(len(candidates), 1_000_000),
            "journal_entry_count": min(len(journal), 1_000_000),
            "migration_started": False,
        }

    def _legacy_migration_review_summary(*, owner: str, review_id: str) -> dict:
        """Return a browser-safe summary of a persisted migration review.

        The encrypted plan is intentionally richer than this response: it has
        opaque candidate tokens needed by a future owner-assignment flow.  The
        browser only needs the aggregate decision surface, never those tokens,
        historical session references, or legacy memory text.
        """
        if not re.fullmatch(r"[a-f0-9]{32}", review_id):
            raise FileNotFoundError("migration review was not found")
        db = SessionLocal()
        try:
            review = db.query(LegacyMemoryMigrationReview).filter(
                LegacyMemoryMigrationReview.id == review_id,
                LegacyMemoryMigrationReview.owner == owner,
            ).first()
            if not review:
                raise FileNotFoundError("migration review was not found")
            return _legacy_migration_review_payload(review)
        finally:
            db.close()

    def _resolve_synthesis_sources(*, owner: str, payload: CheckpointSynthesisCreate) -> dict:
        """Resolve exactly two owner checkpoints without exposing transcripts.

        Both preview and creation use this one validation path so a browser
        cannot review one pair and create a different, less-checked pair.
        """
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
            source_rows_by_id = {row.id: row for row in source_rows}
            sources = []
            for source_id in source_ids:
                row = source_rows_by_id[source_id]
                source_session = db.query(DbSession).filter(
                    DbSession.id == row.session_id, DbSession.owner == owner,
                ).first()
                if source_session is None or (source_session.scope_kind or "general") not in {"personal", "project"}:
                    raise HTTPException(409, "Checkpoint synthesis requires Companion-home sources")
                try:
                    checkpoint = ThreadCheckpointV1.from_payload(json.loads(row.payload_json))
                except (ContractError, TypeError, ValueError) as exc:
                    raise HTTPException(409, "A selected checkpoint is no longer valid") from exc
                sources.append({
                    "id": row.id,
                    "revision": row.revision,
                    "checkpoint": checkpoint,
                    "session_name": str(source_session.name or "Checkpoint")[:80],
                    "scope_kind": source_session.scope_kind or "general",
                })
            return {
                "source_ids": source_ids,
                "sources": sources,
                "scope_kind": scope_kind,
                "project_id": destination.project_id if scope_kind == "project" else None,
                "endpoint_url": destination.endpoint_url,
                "model": destination.model,
                "headers": destination.headers or {},
                "endpoint_id": destination.endpoint_id or "",
                "harness_kind": destination.harness_kind or "native",
            }
        finally:
            db.close()

    def _synthesis_preview_source(source: dict) -> dict:
        """Return a bounded compact checkpoint, never source IDs/hashes/tails."""
        checkpoint = source["checkpoint"]
        return {
            "session_name": source["session_name"],
            "scope_kind": source["scope_kind"],
            "derivation_status": checkpoint.derivation_status,
            "source_message_count": len(checkpoint.source_message_ids),
            "objective": checkpoint.objective,
            "accepted_decisions": list(checkpoint.accepted_decisions),
            "proposals": list(checkpoint.proposals),
            "failures": list(checkpoint.failures),
            "open_questions": list(checkpoint.open_questions),
            "next_actions": list(checkpoint.next_actions),
            "artifact_refs": list(checkpoint.artifact_refs),
        }

    def _synthesis_preview_comparison(sources: list[dict]) -> dict:
        """Compare compact checkpoint claims without reconciling them.

        This is deliberately an owner-review aid, not a provider judgment: an
        exact match is shown as shared, and every non-match remains attached to
        its selected source.  Raw messages, checkpoint identifiers, hashes,
        and route information never leave the existing compact-preview shape.
        """
        first, second = (_synthesis_preview_source(source) for source in sources)
        list_fields = (
            "accepted_decisions", "proposals", "failures", "open_questions",
            "next_actions", "artifact_refs",
        )
        fields: dict[str, dict] = {}
        for field in list_fields:
            first_values = list(first[field])
            second_values = list(second[field])
            second_set = set(second_values)
            first_set = set(first_values)
            fields[field] = {
                "shared": [value for value in first_values if value in second_set],
                "source_one_only": [value for value in first_values if value not in second_set],
                "source_two_only": [value for value in second_values if value not in first_set],
            }
        return {
            "objectives": {
                "shared": bool(first["objective"]) and first["objective"] == second["objective"],
                "source_one": first["objective"],
                "source_two": second["objective"],
            },
            "fields": fields,
            "policy": "Matching entries are only exact overlaps. Different entries remain source-attributed claims for the synthesis chat to reconcile.",
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
        resolved = _resolve_synthesis_sources(owner=owner, payload=payload)

        session_id = str(uuid.uuid4())
        source_names = [source["session_name"][:40] for source in resolved["sources"]]
        name = f"Synthesis — {source_names[0]} + {source_names[1]}"[:120]
        try:
            session = session_manager.create_session(session_id, name, resolved["endpoint_url"], resolved["model"], owner=owner)
            session.headers = resolved["headers"]
            db = SessionLocal()
            try:
                row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).one()
                row.headers = resolved["headers"]
                row.scope_kind, row.project_id = resolved["scope_kind"], resolved["project_id"]
                row.endpoint_id, row.harness_kind, row.is_scope_primary = resolved["endpoint_id"], resolved["harness_kind"], False
                row.capability_grants = defaults_for_scope(resolved["scope_kind"])
                row.updated_at = utcnow_naive()
                db.commit()
            finally:
                db.close()
            store = ContinuityStore()
            for source_id in resolved["source_ids"]:
                store.attach_checkpoint(
                    owner=owner, destination_session_id=session_id, source_checkpoint_id=source_id,
                )
            return {"id": session_id, "name": name, "scope_kind": resolved["scope_kind"], "project_id": resolved["project_id"],
                    "source_checkpoint_ids": resolved["source_ids"]}
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

    @router.post("/memory/legacy-dry-run")
    def legacy_memory_dry_run(payload: LegacyMigrationDryRun, request: Request):
        """Owner-requested classification of one already-created private backup."""
        try:
            return _legacy_memory_dry_run(owner=_owner(request), backup_id=payload.backup_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Owner-private backup was not found. Create a new backup before previewing migration.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception("Legacy memory dry run failed")
            raise HTTPException(503, "Legacy memory migration preview could not be completed safely. No data was changed.") from exc

    @router.get("/memory/legacy-migration-reviews/{review_id}")
    def legacy_memory_migration_review(review_id: str, request: Request):
        """Read aggregate migration-review state; no assignment or apply exists yet."""
        try:
            return _legacy_migration_review_summary(owner=_owner(request), review_id=review_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Migration review was not found.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception("Legacy memory migration review lookup failed")
            raise HTTPException(503, "Migration review could not be read safely. No data was changed.") from exc

    @router.get("/memory/legacy-migration-reviews")
    def legacy_memory_migration_reviews(request: Request):
        """List recent aggregate review states for the authenticated owner only."""
        owner = _owner(request)
        db = SessionLocal()
        try:
            rows = db.query(LegacyMemoryMigrationReview).filter(
                LegacyMemoryMigrationReview.owner == owner,
            ).order_by(LegacyMemoryMigrationReview.updated_at.desc()).limit(10).all()
            return {"reviews": [_legacy_migration_review_payload(row) for row in rows]}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception("Legacy memory migration review list failed")
            raise HTTPException(503, "Migration reviews could not be read safely. No data was changed.") from exc
        finally:
            db.close()

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
        # This is deliberate provenance for an owner-visible canary.  It is
        # constrained to the registered model identifier and the fixed
        # derivation profile; provider URLs, headers, raw output, and source
        # text remain server-only.
        return {
            "id": result.record.id,
            "revision": result.record.revision,
            "status": "proposed",
            "proposal": result.record.proposal.to_payload(),
            "derivation": {
                "mode": "no_tools",
                "model": result.record.proposal.derivation_model,
                "source_message_count": len(result.record.proposal.source_message_ids),
            },
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

    @router.post("/memory/checkpoint-synthesis/preview")
    def preview_checkpoint_synthesis(payload: CheckpointSynthesisCreate, request: Request):
        resolved = _resolve_synthesis_sources(owner=_owner(request), payload=payload)
        return {
            "destination_scope_kind": resolved["scope_kind"],
            "sources": [_synthesis_preview_source(source) for source in resolved["sources"]],
            "comparison": _synthesis_preview_comparison(resolved["sources"]),
            "policy": "These are compact owner-selected references, not transcripts. Keep disagreements attributed to their source.",
        }

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
            owner = _owner(request)
            record, write, brief = ContinuityStore().promote_semantic_proposal(
                owner=owner, proposal_id=proposal_id,
                expected_revision=payload.expected_revision, selections=payload.selections,
            )
            scope_kind, project_id = _scope(owner, record.proposal.session_id)
            _index_accepted_home_brief(
                owner=owner, session_id=record.proposal.session_id, scope_kind=scope_kind,
                project_id=project_id, source_id=write.id, brief=brief,
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
        values = payload.model_dump(exclude_none=True)
        session_id = values.pop("session_id")
        store = ContinuityStore()
        current = store.latest_personal_brief(owner=owner, session_id=session_id)
        current_values = current.to_payload() if current else {}
        current_values.update(values)
        brief = PersonalBriefV1(
            owner_id=owner,
            derivation_status="accepted",
            derivation_version=1,
            derivation_method="owner_edit_v1",
            summary=str(current_values.get("summary") or ""),
            confirmed_facts=list(current_values.get("confirmed_facts") or []),
            preferences=list(current_values.get("preferences") or []),
            ongoing_goals=list(current_values.get("ongoing_goals") or []),
            commitments=list(current_values.get("commitments") or []),
            proposals=list(current_values.get("proposals") or []),
            recurring_themes=list(current_values.get("recurring_themes") or []),
            failed_approaches=list(current_values.get("failed_approaches") or []),
            open_questions=list(current_values.get("open_questions") or []),
            artifact_refs=list(current_values.get("artifact_refs") or []),
            source_refs=list(current_values.get("source_refs") or []),
            source_message_ids=list(current_values.get("source_message_ids") or []),
            source_through_message_id=current_values.get("source_through_message_id") or None,
        )
        source_hash = hashlib.sha256(json.dumps(brief.to_payload(), sort_keys=True).encode()).hexdigest()
        try:
            result = store.write_personal_brief(owner=owner, session_id=session_id, brief=brief, source_hash=source_hash)
        except Exception as exc:
            raise _error(exc) from exc
        _index_accepted_home_brief(
            owner=owner, session_id=session_id, scope_kind="personal", project_id=None,
            source_id=result.id, brief=brief,
        )
        return {"revision": result.revision, "brief": brief.to_payload()}

    @router.post("/project-brief")
    def write_project_brief(payload: ProjectBriefWrite, request: Request):
        owner = _owner(request)
        scope_kind, project_id = _scope(owner, payload.session_id)
        if scope_kind != "project" or not project_id:
            raise HTTPException(409, "Project brief requires a project Companion session")
        store = ContinuityStore()
        current = store.latest_project_brief(owner=owner, project_id=project_id)
        current_values = current.to_payload() if current else {}
        values = payload.model_dump(exclude_none=True)
        values.pop("session_id")
        current_values.update(values)
        brief = ProjectBriefV1(
            project_id=project_id,
            summary=str(current_values.get("summary") or ""),
            derived_working_state=dict(current_values.get("derived_working_state") or {}),
            confirmed_facts=list(current_values.get("confirmed_facts") or []),
            accepted_decisions=list(current_values.get("accepted_decisions") or []),
            proposals=list(current_values.get("proposals") or []),
            failed_approaches=list(current_values.get("failed_approaches") or []),
            open_questions=list(current_values.get("open_questions") or []),
            current_plans=list(current_values.get("current_plans") or []),
            source_refs=list(current_values.get("source_refs") or []),
            source_session_ids=list(current_values.get("source_session_ids") or []),
            source_message_ids=list(current_values.get("source_message_ids") or []),
            source_through_message_id=current_values.get("source_through_message_id") or None,
            source_revision=current_values.get("source_revision") or None,
            derivation_status="accepted",
            derivation_version=1,
            derivation_method="owner_edit_v1",
        )
        source_hash = hashlib.sha256(json.dumps(brief.to_payload(), sort_keys=True).encode()).hexdigest()
        try:
            result = store.write_project_brief(owner=owner, brief=brief, source_hash=source_hash)
        except Exception as exc:
            raise _error(exc) from exc
        _index_accepted_home_brief(
            owner=owner, session_id=payload.session_id, scope_kind="project", project_id=project_id,
            source_id=result.id, brief=brief,
        )
        return {"revision": result.revision, "brief": brief.to_payload()}

    return router
