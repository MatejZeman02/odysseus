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
    WorkingArtifactRevision, Document, DocumentVersion, ScopedMemoryRecord,
    utcnow_naive,
)


class MemoryScopeError(RuntimeError):
    pass


class ArtifactConflict(MemoryScopeError):
    pass


LONG_PASTE_ARTIFACT_THRESHOLD = 3000
MAX_ARTIFACT_BYTES = 512 * 1024


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

    @staticmethod
    def _sync_artifact_recall(
        db,
        artifact: WorkingArtifact,
        *,
        session_id: str | None = None,
    ) -> None:
        """Keep the rebuildable recall record aligned with artifact lifecycle.

        The working artifact remains the source of truth.  Its lightweight
        recall entry must disappear when the artifact is deleted and must not
        retain old text after a document save, Undo, or Restore.
        """
        query = db.query(ScopedMemoryRecord).filter(
            ScopedMemoryRecord.owner == artifact.owner,
            ScopedMemoryRecord.scope_kind == artifact.scope_kind,
            ScopedMemoryRecord.source_kind == "working_artifact",
            ScopedMemoryRecord.source_id == artifact.id,
        )
        query = (
            query.filter(ScopedMemoryRecord.project_id == artifact.project_id)
            if artifact.project_id
            else query.filter(ScopedMemoryRecord.project_id == None)
        )
        if artifact.status != "active":
            query.delete(synchronize_session=False)
            return
        content = f"{artifact.path}\n{artifact.summary}"
        record = query.first()
        if record:
            record.content = content
            if session_id:
                record.session_id = session_id
            return
        db.add(ScopedMemoryRecord(
            id=uuid.uuid4().hex,
            owner=artifact.owner,
            scope_kind=artifact.scope_kind,
            project_id=artifact.project_id,
            session_id=session_id,
            source_kind="working_artifact",
            source_id=artifact.id,
            content=content,
            sensitivity="normal",
        ))

    def _personal(self, db, owner: str, session_id: str) -> DbSession:
        row = db.query(DbSession).filter(
            DbSession.id == session_id, DbSession.owner == owner,
            DbSession.scope_kind == "personal",
        ).first()
        if not row:
            raise MemoryScopeError("Personal Advisor session was not found")
        return row

    def _computer(self, db, owner: str, session_id: str) -> DbSession:
        """Resolve the one owner-owned Computer Help home.

        Computer incident records are private Odysseus artifacts, just like
        Personal drafts.  They are deliberately not project workspace files
        and may not be created from an arbitrary general chat.
        """
        row = db.query(DbSession).filter(
            DbSession.id == session_id, DbSession.owner == owner,
            DbSession.scope_kind == "computer",
        ).first()
        if not row:
            raise MemoryScopeError("Computer Help session was not found")
        return row

    def _project(self, db, owner: str, project_id: str) -> Project:
        row = db.query(Project).filter(Project.id == project_id, Project.owner == owner).first()
        if not row:
            raise MemoryScopeError("Project was not found")
        return row

    @staticmethod
    def _document_payload(document: Document) -> dict:
        return {
            "id": document.id,
            "session_id": document.session_id,
            "title": document.title,
            "language": document.language,
            "current_content": document.current_content,
            "version_count": document.version_count,
        }

    @staticmethod
    def _sync_document_from_artifact(db, artifact: WorkingArtifact, session_id: str) -> None:
        """Make an already-open native document reflect an artifact revision."""
        if not artifact.document_id:
            return
        document = db.query(Document).filter(
            Document.id == artifact.document_id,
            Document.owner == artifact.owner,
        ).first()
        if not document:
            artifact.document_id = None
            return
        if document.current_content == artifact.content:
            return
        document.version_count = max(int(document.version_count or 1), 1) + 1
        document.current_content = artifact.content
        document.session_id = session_id
        db.add(DocumentVersion(
            id=str(uuid.uuid4()), document_id=document.id,
            version_number=document.version_count, content=artifact.content,
            summary=f"Synced artifact revision {artifact.revision}", source="system",
        ))

    def open_private_artifact_document(
        self, *, owner: str, session_id: str, artifact_id: str, scope_kind: str,
    ) -> dict:
        """Open a private Companion artifact through the standard editor.

        The artifact remains authoritative for scope and prompt inclusion. The
        linked native Document is the editor surface and receives the same
        content and version history.
        """
        if scope_kind not in {"personal", "computer"}:
            raise MemoryScopeError("This artifact scope is not editable here")
        db = SessionLocal()
        try:
            (self._personal if scope_kind == "personal" else self._computer)(db, owner, session_id)
            artifact = db.query(WorkingArtifact).filter(
                WorkingArtifact.id == artifact_id,
                WorkingArtifact.owner == owner,
                WorkingArtifact.scope_kind == scope_kind,
                WorkingArtifact.project_id == None,
                WorkingArtifact.status == "active",
            ).first()
            if not artifact:
                raise MemoryScopeError("Artifact was not found")
            document = None
            if artifact.document_id:
                document = db.query(Document).filter(
                    Document.id == artifact.document_id,
                    Document.owner == owner,
                ).first()
            if not document:
                document = Document(
                    id=str(uuid.uuid4()), session_id=session_id,
                    title=f"Artifact · {artifact.path}", language="markdown",
                    current_content=artifact.content, version_count=1,
                    is_active=True, owner=owner,
                )
                artifact.document_id = document.id
                db.add(document)
                db.add(DocumentVersion(
                    id=str(uuid.uuid4()), document_id=document.id,
                    version_number=1, content=artifact.content,
                    summary=f"Opened working artifact: {artifact.path}", source="system",
                ))
            else:
                self._sync_document_from_artifact(db, artifact, session_id)
            db.commit()
            db.refresh(document)
            return self._document_payload(document)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def open_personal_artifact_document(self, *, owner: str, session_id: str, artifact_id: str) -> dict:
        return self.open_private_artifact_document(
            owner=owner, session_id=session_id, artifact_id=artifact_id, scope_kind="personal",
        )

    def open_computer_artifact_document(self, *, owner: str, session_id: str, artifact_id: str) -> dict:
        return self.open_private_artifact_document(
            owner=owner, session_id=session_id, artifact_id=artifact_id, scope_kind="computer",
        )

    @staticmethod
    def sync_private_artifact_from_document(db, *, owner: str, document_id: str, content: str) -> WorkingArtifact | None:
        """Stage a native editor save into its private artifact transaction."""
        artifact = db.query(WorkingArtifact).filter(
            WorkingArtifact.document_id == document_id,
            WorkingArtifact.owner == owner,
            WorkingArtifact.scope_kind.in_(("personal", "computer")),
            WorkingArtifact.project_id == None,
            WorkingArtifact.status == "active",
        ).first()
        if not artifact or artifact.content == content:
            return artifact
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise MemoryScopeError("Artifact content must be UTF-8 Markdown up to 512 KiB")
        if _SECRET_RE.search(content):
            # Native Documents are an editor surface for the same private
            # artifact, not a second route around its content policy.  Keep
            # the document and recall record on their prior revision when a
            # save is rejected by raising inside the caller's transaction.
            raise MemoryScopeError("Artifact contains credential-like material and was not saved")
        db.add(WorkingArtifactRevision(
            id=uuid.uuid4().hex, artifact_id=artifact.id, revision=artifact.revision,
            content=artifact.content, content_hash=artifact.content_hash,
            source_message_id=artifact.source_message_id, action="document_edit",
        ))
        artifact.content = content
        artifact.content_hash = _hash(content)
        artifact.summary = _summary(content)
        artifact.revision += 1
        CompanionMemoryStore._sync_artifact_recall(db, artifact)
        return artifact

    @staticmethod
    def sync_personal_artifact_from_document(db, *, owner: str, document_id: str, content: str) -> WorkingArtifact | None:
        """Backward-compatible name for private artifact document saves."""
        return CompanionMemoryStore.sync_private_artifact_from_document(
            db, owner=owner, document_id=document_id, content=content,
        )

    def list_artifacts(self, *, owner: str, scope_kind: str, project_id: str | None = None) -> list[dict]:
        if scope_kind == "project":
            if not project_id:
                return []
            db = SessionLocal()
            try:
                project = self._project(db, owner, project_id)
                captured = [
                    self.serialise_artifact(row)
                    for row in db.query(WorkingArtifact).filter(
                        WorkingArtifact.owner == owner,
                        WorkingArtifact.scope_kind == "project",
                        WorkingArtifact.project_id == project_id,
                        WorkingArtifact.status == "active",
                    ).order_by(WorkingArtifact.path.asc()).all()
                ]
                workspace_root = project.workspace_root
            finally:
                db.close()
            # Project artifacts are source-controlled workspace files.  Qwen
            # never writes them directly; G2B's transaction can create them
            # under .artifacts. Captured user pastes remain in owner-private
            # Odysseus storage and are merged into this read-only index.
            try:
                root = Path(workspace_root).resolve(strict=True)
            except (OSError, RuntimeError):
                return captured
            artifact_root = root / ".artifacts"
            if not artifact_root.is_dir() or artifact_root.is_symlink():
                return captured
            records = list(captured)
            captured_paths = {item["path"] for item in captured}
            try:
                for candidate in sorted(artifact_root.rglob("*.md")):
                    if not candidate.is_file() or candidate.is_symlink():
                        continue
                    relative = candidate.relative_to(root).as_posix()
                    if relative in captured_paths:
                        continue
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

    def _write_private_artifact(
        self, *, owner: str, session_id: str, scope_kind: str, path: str, content: str,
        expected_revision: int | None = None, source_message_id: str | None = None,
    ) -> dict:
        path = normalise_artifact_path(path)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise MemoryScopeError("Artifact content must be UTF-8 Markdown up to 512 KiB")
        if _SECRET_RE.search(content):
            raise MemoryScopeError("Artifact contains credential-like material and was not saved")
        if scope_kind not in {"personal", "computer"}:
            raise MemoryScopeError("This artifact scope is not writable here")
        db = SessionLocal()
        try:
            (self._personal if scope_kind == "personal" else self._computer)(db, owner, session_id)
            row = db.query(WorkingArtifact).filter(
                WorkingArtifact.owner == owner, WorkingArtifact.scope_kind == scope_kind,
                WorkingArtifact.project_id == None, WorkingArtifact.path == path,
                WorkingArtifact.status == "active",
            ).first()
            if row and expected_revision is not None and row.revision != expected_revision:
                raise ArtifactConflict("Artifact changed elsewhere; reload it before saving")
            digest = _hash(content)
            if row:
                if row.content == content:
                    return self.serialise_artifact(row, include_content=True)
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
                    id=uuid.uuid4().hex, owner=owner, scope_kind=scope_kind, project_id=None,
                    path=path, title=PurePosixPath(path).stem, summary=_summary(content),
                    content=content, content_hash=digest, revision=1, source_message_id=source_message_id,
                )
                db.add(row)
            self._sync_artifact_recall(db, row, session_id=session_id)
            self._sync_document_from_artifact(db, row, session_id)
            db.commit(); db.refresh(row)
            result = self.serialise_artifact(row, include_content=True)
            return result
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def write_personal_artifact(self, *, owner: str, session_id: str, path: str, content: str,
                                expected_revision: int | None = None, source_message_id: str | None = None) -> dict:
        return self._write_private_artifact(
            owner=owner, session_id=session_id, scope_kind="personal", path=path, content=content,
            expected_revision=expected_revision, source_message_id=source_message_id,
        )

    def write_computer_artifact(self, *, owner: str, session_id: str, path: str, content: str,
                                expected_revision: int | None = None, source_message_id: str | None = None) -> dict:
        return self._write_private_artifact(
            owner=owner, session_id=session_id, scope_kind="computer", path=path, content=content,
            expected_revision=expected_revision, source_message_id=source_message_id,
        )

    def capture_long_paste(self, *, owner: str, session_id: str, content: str) -> dict:
        """Persist one large user paste without adding it to the raw transcript.

        The server chooses the path and scope. Project captures live in
        Odysseus data rather than the checkout, so this does not weaken the
        read-only Qwen workspace boundary.
        """
        if not isinstance(content, str) or len(content) < LONG_PASTE_ARTIFACT_THRESHOLD:
            raise MemoryScopeError(
                f"Automatic paste artifacts require at least {LONG_PASTE_ARTIFACT_THRESHOLD} characters"
            )
        if len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise MemoryScopeError("Pasted text must be UTF-8 and no larger than 512 KiB")
        if _SECRET_RE.search(content):
            raise MemoryScopeError("Pasted text contains credential-like material and was not saved or sent")
        db = SessionLocal()
        try:
            session = db.query(DbSession).filter(
                DbSession.id == session_id,
                DbSession.owner == owner,
            ).first()
            if not session or session.scope_kind not in {"personal", "project", "computer"}:
                raise MemoryScopeError("Automatic paste artifacts require a Personal or project or Computer Help home")
            project_id = session.project_id if session.scope_kind == "project" else None
            if session.scope_kind == "project":
                if not project_id:
                    raise MemoryScopeError("Project session is missing its project scope")
                self._project(db, owner, project_id)
            stamp = utcnow_naive().strftime("%Y-%m-%d-%H%M%S")
            path = f"pastes/{stamp}-{uuid.uuid4().hex[:8]}.md"
            row = WorkingArtifact(
                id=uuid.uuid4().hex,
                owner=owner,
                scope_kind=session.scope_kind,
                project_id=project_id,
                path=path,
                title=f"Pasted text {stamp}",
                summary=_summary(content),
                content=content,
                content_hash=_hash(content),
                revision=1,
            )
            db.add(row)
            self._sync_artifact_recall(db, row, session_id=session_id)
            db.commit()
            db.refresh(row)
            result = self.serialise_artifact(row)
            result["character_count"] = len(content)
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_personal_artifact(self, *, owner: str, artifact_id: str) -> dict:
        return self.get_private_artifact(owner=owner, artifact_id=artifact_id, scope_kind="personal")

    def get_computer_artifact(self, *, owner: str, artifact_id: str) -> dict:
        return self.get_private_artifact(owner=owner, artifact_id=artifact_id, scope_kind="computer")

    def get_private_artifact(self, *, owner: str, artifact_id: str, scope_kind: str) -> dict:
        db = SessionLocal()
        try:
            row = db.query(WorkingArtifact).filter(
                WorkingArtifact.id == artifact_id, WorkingArtifact.owner == owner,
                WorkingArtifact.scope_kind == scope_kind, WorkingArtifact.status == "active",
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

    def get_scoped_artifact_by_path(
        self, *, owner: str, scope_kind: str, project_id: str | None, path: str,
    ) -> dict:
        """Read an explicitly named artifact after its scope was resolved."""
        path = normalise_artifact_path(path)
        db = SessionLocal()
        try:
            query = db.query(WorkingArtifact).filter(
                WorkingArtifact.owner == owner,
                WorkingArtifact.scope_kind == scope_kind,
                WorkingArtifact.path == path,
                WorkingArtifact.status == "active",
            )
            query = query.filter(WorkingArtifact.project_id == project_id) if project_id else query.filter(WorkingArtifact.project_id == None)
            row = query.first()
            if row:
                return self.serialise_artifact(row, include_content=True)
            if scope_kind != "project" or not project_id or not path.startswith(".artifacts/"):
                raise MemoryScopeError("Artifact was not found")
            project = self._project(db, owner, project_id)
            root = Path(project.workspace_root).resolve(strict=True)
            candidate = (root / path).resolve(strict=True)
            artifact_root = (root / ".artifacts").resolve(strict=True)
            if candidate.is_symlink() or not candidate.is_file() or not candidate.is_relative_to(artifact_root):
                raise MemoryScopeError("Artifact was not found")
            content = candidate.read_text(encoding="utf-8")
            return {
                "id": f"project:{project_id}:{path}",
                "scope_kind": "project",
                "project_id": project_id,
                "path": path,
                "title": candidate.stem,
                "summary": _summary(content),
                "revision": 1,
                "content_hash": _hash(content),
                "updated_at": None,
                "source_message_id": None,
                "content": content,
            }
        except (OSError, UnicodeDecodeError) as exc:
            raise MemoryScopeError("Artifact was not found") from exc
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
            self._sync_artifact_recall(db, row)
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
            self._sync_artifact_recall(db, row)
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
            self._sync_artifact_recall(db, row)
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
