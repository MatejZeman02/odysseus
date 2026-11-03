"""Tables the Companion layer adds to the Odysseus schema.

They live here rather than in ``core/database.py`` for one reason: every
upstream merge used to conflict in that file, in ten places, because the fork
had threaded nine models and five migrations through it. The names stay
importable from ``core.database``, which is where the rest of the code asks
for them, through a module ``__getattr__`` there.

``init_db`` imports this module for its side effect only, and touches no name
in it, so importing this module first still works: ``core.database`` finishes
its own startup before the classes below exist, and the ``create_all`` at the
bottom of this file covers that order.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from core.database import Base, EncryptedText, TimestampMixin, engine, utcnow_naive

MODEL_NAMES = (
    "Project",
    "ContinuityArtifact",
    "WorkingArtifact",
    "WorkingArtifactRevision",
    "ContextGrant",
    "ScopedMemoryRecord",
    "LegacyMemoryMigrationReview",
    "ComputerTaskRoot",
    "ProjectChangeSet",
)


class Project(TimestampMixin, Base):
    """Owner-scoped project identity for durable conversation continuity.

    Workspace files remain the source of truth. This row only owns the stable
    project identity, configured root, and direct related-project allowlist.
    """
    __tablename__ = "projects"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    workspace_root = Column(String, nullable=False)
    settings_json = Column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_projects_owner_name", "owner", "name", unique=True),
    )

class ContinuityArtifact(TimestampMixin, Base):
    """Versioned derived context for one session or one project.

    Artifacts deliberately live outside ``chat_messages``. They may summarize
    messages, but are never a destructive replacement for raw transcript rows.
    """
    __tablename__ = "continuity_artifacts"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=False, index=True)
    project_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    kind = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active")
    revision = Column(Integer, nullable=False, default=1)
    payload_json = Column(Text, nullable=False)
    source_through_message_id = Column(String, nullable=True)
    source_hash = Column(String, nullable=False)

    __table_args__ = (
        Index("ix_continuity_artifacts_session_kind_revision", "session_id", "kind", "revision"),
        Index("ix_continuity_artifacts_project_kind_revision", "project_id", "kind", "revision"),
        Index("ix_continuity_artifacts_source", "session_id", "kind", "source_through_message_id", "source_hash"),
    )


class WorkingArtifact(TimestampMixin, Base):
    """Revisioned Markdown working content for one Companion scope.

    Personal artifacts are stored in Odysseus private data. Project artifacts
    are metadata mirrors for the project's ``.artifacts`` namespace and are
    only mutated through the reviewed patch boundary.
    """
    __tablename__ = "working_artifacts"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=False, index=True)
    scope_kind = Column(String, nullable=False, index=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    path = Column(String, nullable=False)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=False, default="")
    content = Column(EncryptedText, nullable=False)
    content_hash = Column(String, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="active", index=True)
    source_message_id = Column(String, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    # Personal artifacts can be edited in the established Documents pane.
    # This is a bridge identifier rather than a foreign key so an owner may
    # delete a document independently, and opening the artifact then makes a new
    # editor document without losing the private artifact itself.
    document_id = Column(String, nullable=True, index=True)

    __table_args__ = (
        Index("ix_working_artifacts_owner_scope_path", "owner", "scope_kind", "project_id", "path"),
    )


class WorkingArtifactRevision(Base):
    """Immutable prior bytes for diff/history/Undo."""
    __tablename__ = "working_artifact_revisions"

    id = Column(String, primary_key=True, index=True)
    artifact_id = Column(String, ForeignKey("working_artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    content = Column(EncryptedText, nullable=False)
    content_hash = Column(String, nullable=False)
    source_message_id = Column(String, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    action = Column(String, nullable=False, default="write")
    created_at = Column(DateTime, default=utcnow_naive, nullable=False)

    __table_args__ = (Index("ix_working_artifact_revisions_artifact_revision", "artifact_id", "revision", unique=True),)


class ContextGrant(TimestampMixin, Base):
    """Explicit, auditable Personal-to-project context authorization."""
    __tablename__ = "context_grants"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=False, index=True)
    personal_session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    purpose = Column(Text, nullable=False)
    information_classes_json = Column(Text, nullable=False, default="[]")
    artifact_paths_json = Column(Text, nullable=False, default="[]")
    request_message_id = Column(String, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="pending", index=True)
    expires_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)
    decision_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_context_grants_owner_personal_status", "owner", "personal_session_id", "status"),)


class ScopedMemoryRecord(TimestampMixin, Base):
    """Rebuildable, provider-neutral episodic retrieval record.

    It indexes only validated compact artifacts, never raw transcripts or a
    provider's free-form ``remember`` input.
    """
    __tablename__ = "scoped_memory_records"
    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=False, index=True)
    scope_kind = Column(String, nullable=False, index=True)
    project_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    source_kind = Column(String, nullable=False)
    source_id = Column(String, nullable=False)
    content = Column(EncryptedText, nullable=False)
    sensitivity = Column(String, nullable=False, default="normal")
    expires_at = Column(DateTime, nullable=True)
    __table_args__ = (Index("ix_scoped_memory_scope_source", "owner", "scope_kind", "project_id", "source_kind", "source_id", unique=True),)


class LegacyMemoryMigrationReview(TimestampMixin, Base):
    """Encrypted, owner-reviewed staging record for legacy-memory migration.

    This is deliberately *not* a memory provider and cannot make legacy data
    retrievable.  It binds a future owner assignment/apply transaction to one
    immutable owner-private backup digest and records a blank rollback journal
    up front.  Candidate metadata is encrypted because even categories and
    historical session references are private context.
    """
    __tablename__ = "legacy_memory_migration_reviews"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=False, index=True)
    backup_id = Column(String, nullable=False)
    backup_sha256 = Column(String(64), nullable=False)
    status = Column(String, nullable=False, default="review_ready", index=True)
    revision = Column(Integer, nullable=False, default=1)
    plan_json = Column(EncryptedText, nullable=False)
    journal_json = Column(EncryptedText, nullable=False, default="[]")
    failure_code = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_legacy_memory_migration_review_owner_backup", "owner", "backup_id", unique=True),
        Index("ix_legacy_memory_migration_review_owner_status", "owner", "status"),
    )


class ComputerTaskRoot(TimestampMixin, Base):
    """One owner-selected future Computer Help write boundary.

    Registering a root neither creates nor modifies a filesystem path.  The
    encrypted resolved path is intentionally server-only: current UI/API
    payloads expose just the owner label and final directory name.  A later
    transaction broker must revalidate this record at use time, and it is never a
    blanket filesystem grant.
    """
    __tablename__ = "computer_task_roots"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=False, index=True)
    label = Column(String, nullable=False)
    root_path = Column(EncryptedText, nullable=False)
    status = Column(String, nullable=False, default="active", index=True)
    revision = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_computer_task_roots_owner_label", "owner", "label", unique=True),
        Index("ix_computer_task_roots_owner_status", "owner", "status"),
    )

class ProjectChangeSet(TimestampMixin, Base):
    """Owner-approved, auditable project patch transaction."""
    __tablename__ = "project_change_sets"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=False, index=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    source_message_id = Column(String, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    model = Column(String, nullable=False)
    endpoint_id = Column(String, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="proposed", index=True)
    summary = Column(Text, nullable=False)
    rationale = Column(Text, nullable=False, default="")
    base_git_revision = Column(String, nullable=False)
    # Patch bodies can contain unpublished project material. Keep both the
    # proposal and rollback preimages encrypted at rest.
    proposal_json = Column(EncryptedText, nullable=False)
    rollback_json = Column(EncryptedText, nullable=True)
    result_json = Column(Text, nullable=False, default="{}")
    failure_code = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rolled_back_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_project_change_sets_project_created", "project_id", "created_at"),
        Index("ix_project_change_sets_owner_status", "owner", "status"),
    )


# Idempotent, and only these tables. ``init_db`` already ran ``create_all``
# when this module is the one that triggered it, which is before the classes
# above existed. Either import order ends with the same schema.
Base.metadata.create_all(
    bind=engine,
    tables=[globals()[name].__table__ for name in MODEL_NAMES],
    checkfirst=True,
)
