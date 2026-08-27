"""Strictly scoped, rebuildable episodic recall for G2C.

This local provider-neutral index is deliberately small.  AgentMemory remains
an optional future backend; no existing native/AgentMemory writer is changed.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_

from core.database import ScopedMemoryRecord, SessionLocal
from src.memory_provider import MemoryRecord, MemorySearchHit, ScopedMemoryProvider, ScopedMemoryQuery, ScopedMemoryScope


class ScopedMemoryIndex:
    def index(self, *, owner: str, scope_kind: str, project_id: str | None, session_id: str | None,
              source_kind: str, source_id: str, content: str, sensitivity: str = "normal",
              expires_at: datetime | None = None) -> None:
        """Upsert an already-validated scoped retrieval record.

        This is intentionally not a general ``remember`` API.  A caller must
        supply a concrete Companion home and source provenance.  Expiry may be
        shortened on refresh but cannot be silently cleared, which prevents a
        routine source update from extending a previously time-bounded record.
        """
        if not owner or scope_kind not in {"personal", "project"} or not source_id:
            raise ValueError("scoped memory requires an owner, supported scope, and source")
        if (scope_kind == "personal" and project_id is not None) or (scope_kind == "project" and not project_id):
            raise ValueError("scoped memory project binding does not match its scope")
        if sensitivity not in {"normal", "sensitive"}:
            raise ValueError("scoped memory sensitivity is invalid")
        text = " ".join(str(content or "").split())[:4000]
        if not text:
            return
        db = SessionLocal()
        try:
            row = db.query(ScopedMemoryRecord).filter(
                ScopedMemoryRecord.owner == owner, ScopedMemoryRecord.scope_kind == scope_kind,
                ScopedMemoryRecord.project_id == project_id, ScopedMemoryRecord.source_kind == source_kind,
                ScopedMemoryRecord.source_id == source_id,
            ).first()
            if row:
                row.content, row.session_id, row.sensitivity = text, session_id, sensitivity
                if expires_at is not None:
                    row.expires_at = expires_at
            else:
                db.add(ScopedMemoryRecord(id=uuid.uuid4().hex, owner=owner, scope_kind=scope_kind,
                                          project_id=project_id, session_id=session_id, source_kind=source_kind,
                                          source_id=source_id, content=text, sensitivity=sensitivity,
                                          expires_at=expires_at))
            db.commit()
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def recall(self, *, owner: str, scope_kind: str, project_id: str | None, query: str, limit: int = 4,
               sensitivity: str = "normal") -> list[dict]:
        """Exact owner/home/project filtering happens before keyword ranking."""
        self._validate_scope(owner=owner, scope_kind=scope_kind, project_id=project_id, sensitivity=sensitivity)
        terms = {term.casefold() for term in re.findall(r"[\w-]{3,}", query)}
        if not terms:
            return []
        db = SessionLocal()
        try:
            rows = db.query(ScopedMemoryRecord).filter(
                ScopedMemoryRecord.owner == owner, ScopedMemoryRecord.scope_kind == scope_kind,
                ScopedMemoryRecord.project_id == project_id, ScopedMemoryRecord.sensitivity == sensitivity,
                or_(ScopedMemoryRecord.expires_at.is_(None), ScopedMemoryRecord.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)),
            ).all()
            scored = []
            for row in rows:
                score = sum(term in row.content.casefold() for term in terms)
                if score:
                    scored.append((score, row))
            scored.sort(key=lambda item: (-item[0], item[1].updated_at), reverse=False)
            return [{"id": row.id, "source_kind": row.source_kind, "source_id": row.source_id,
                     "text": row.content[:500], "session_id": row.session_id,
                     "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                     "sensitivity": row.sensitivity,
                     "scope": {"kind": scope_kind, "project_id": project_id}}
                    for _score, row in scored[:limit]]
        finally:
            db.close()

    def list(self, *, owner: str, scope_kind: str, project_id: str | None, limit: int = 100,
             sensitivity: str = "normal") -> list[dict]:
        """List non-expired records within one exact scope for provider adapters."""
        self._validate_scope(owner=owner, scope_kind=scope_kind, project_id=project_id, sensitivity=sensitivity)
        if limit < 1 or limit > 1000:
            raise ValueError("scoped memory list limit is invalid")
        db = SessionLocal()
        try:
            rows = db.query(ScopedMemoryRecord).filter(
                ScopedMemoryRecord.owner == owner, ScopedMemoryRecord.scope_kind == scope_kind,
                ScopedMemoryRecord.project_id == project_id, ScopedMemoryRecord.sensitivity == sensitivity,
                or_(ScopedMemoryRecord.expires_at.is_(None), ScopedMemoryRecord.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)),
            ).order_by(ScopedMemoryRecord.updated_at.desc()).limit(limit).all()
            return [self._row_payload(row) for row in rows]
        finally:
            db.close()

    def delete(self, *, memory_id: str, owner: str, scope_kind: str, project_id: str | None,
               sensitivity: str = "normal") -> bool:
        """Delete a record only when the caller fixes its whole stored scope."""
        self._validate_scope(owner=owner, scope_kind=scope_kind, project_id=project_id, sensitivity=sensitivity)
        if not memory_id:
            raise ValueError("scoped memory ID is required")
        db = SessionLocal()
        try:
            row = db.query(ScopedMemoryRecord).filter(
                ScopedMemoryRecord.id == memory_id, ScopedMemoryRecord.owner == owner,
                ScopedMemoryRecord.scope_kind == scope_kind, ScopedMemoryRecord.project_id == project_id,
                ScopedMemoryRecord.sensitivity == sensitivity,
            ).first()
            if not row:
                return False
            db.delete(row)
            db.commit()
            return True
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    @staticmethod
    def _validate_scope(*, owner: str, scope_kind: str, project_id: str | None, sensitivity: str) -> None:
        if not owner or scope_kind not in {"personal", "project"}:
            raise ValueError("scoped memory requires an owner and supported scope")
        if (scope_kind == "personal" and project_id is not None) or (scope_kind == "project" and not project_id):
            raise ValueError("scoped memory project binding does not match its scope")
        if sensitivity not in {"normal", "sensitive"}:
            raise ValueError("scoped memory sensitivity is invalid")

    @staticmethod
    def _row_payload(row: ScopedMemoryRecord) -> dict[str, Any]:
        return {
            "id": row.id, "source_kind": row.source_kind, "source_id": row.source_id,
            "text": row.content, "session_id": row.session_id, "sensitivity": row.sensitivity,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "scope": {"kind": row.scope_kind, "project_id": row.project_id},
        }


class LocalScopedMemoryProvider(ScopedMemoryProvider):
    """Strict adapter over the rebuildable local scoped-memory index.

    It is intentionally not registered as an AgentMemory replacement.  The
    adapter exists so every future provider is judged against the same exact
    scope, provenance, sensitivity, expiry, and deletion contract first.
    """

    scoped_provider_id = "local-scoped-index"

    def __init__(self, index: ScopedMemoryIndex | None = None):
        self.index = index or ScopedMemoryIndex()

    async def remember_scoped(
        self, text: str, *, scope: ScopedMemoryScope, category: str = "fact",
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        if not scope.provenance_kind or not scope.provenance_id:
            raise ValueError("scoped memory writes require provenance")
        if scope.expires_at is not None:
            expiry = scope.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                raise ValueError("scoped memory expiry must be in the future")
        self.index.index(
            owner=scope.owner_id, scope_kind=scope.home_kind, project_id=scope.project_id,
            session_id=scope.session_id, source_kind=scope.provenance_kind,
            source_id=scope.provenance_id, content=text, sensitivity=scope.sensitivity,
            expires_at=scope.expires_at,
        )
        records = self.index.list(
            owner=scope.owner_id, scope_kind=scope.home_kind, project_id=scope.project_id,
            sensitivity=scope.sensitivity,
        )
        record = next((item for item in records if item["source_kind"] == scope.provenance_kind
                       and item["source_id"] == scope.provenance_id), None)
        if not record:
            raise RuntimeError("scoped memory write was not persisted")
        return self._to_record(record, scope=scope, category=category, metadata=metadata)

    async def recall_scoped(self, query: ScopedMemoryQuery) -> list[MemorySearchHit]:
        scope = query.scope
        rows = self.index.recall(
            owner=scope.owner_id, scope_kind=scope.home_kind, project_id=scope.project_id,
            query=query.text, limit=query.top_k, sensitivity=scope.sensitivity,
        )
        return [MemorySearchHit(
            memory=self._to_record(row, scope=self._scope_for_row(scope, row)),
            provider_id=self.scoped_provider_id, score=None,
        ) for row in rows]

    async def list_scoped(self, *, scope: ScopedMemoryScope, limit: int = 100) -> list[MemoryRecord]:
        return [self._to_record(row, scope=self._scope_for_row(scope, row)) for row in self.index.list(
            owner=scope.owner_id, scope_kind=scope.home_kind, project_id=scope.project_id,
            limit=limit, sensitivity=scope.sensitivity,
        )]

    async def delete_scoped(self, memory_id: str, *, scope: ScopedMemoryScope) -> bool:
        return self.index.delete(
            memory_id=memory_id, owner=scope.owner_id, scope_kind=scope.home_kind,
            project_id=scope.project_id, sensitivity=scope.sensitivity,
        )

    @staticmethod
    def _scope_for_row(scope: ScopedMemoryScope, row: dict[str, Any]) -> ScopedMemoryScope:
        raw_expiry = row.get("expires_at")
        try:
            stored_expiry = datetime.fromisoformat(raw_expiry) if isinstance(raw_expiry, str) and raw_expiry else None
        except ValueError:
            stored_expiry = None
        return ScopedMemoryScope(
            owner_id=scope.owner_id, home_kind=scope.home_kind, project_id=scope.project_id,
            session_id=row.get("session_id"), sensitivity=scope.sensitivity,
            expires_at=stored_expiry, provenance_kind=str(row.get("source_kind") or ""),
            provenance_id=str(row.get("source_id") or ""), grant_ids=scope.grant_ids,
        )

    @staticmethod
    def _to_record(row: dict[str, Any], *, scope: ScopedMemoryScope, category: str = "fact",
                   metadata: dict[str, Any] | None = None) -> MemoryRecord:
        return MemoryRecord(
            id=str(row["id"]), text=str(row["text"]), category=category,
            source=f"scoped:{scope.provenance_kind}", owner=scope.owner_id,
            session_id=scope.session_id, scope=scope, metadata=dict(metadata or {}),
        )
