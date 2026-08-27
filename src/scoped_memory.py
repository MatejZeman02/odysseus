"""Strictly scoped, rebuildable episodic recall for G2C.

This local provider-neutral index is deliberately small.  AgentMemory remains
an optional future backend; no existing native/AgentMemory writer is changed.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_

from core.database import ScopedMemoryRecord, SessionLocal


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

    def recall(self, *, owner: str, scope_kind: str, project_id: str | None, query: str, limit: int = 4) -> list[dict]:
        """Exact owner/home/project filtering happens before keyword ranking."""
        if scope_kind not in {"personal", "project"}:
            return []
        terms = {term.casefold() for term in re.findall(r"[\w-]{3,}", query)}
        if not terms:
            return []
        db = SessionLocal()
        try:
            rows = db.query(ScopedMemoryRecord).filter(
                ScopedMemoryRecord.owner == owner, ScopedMemoryRecord.scope_kind == scope_kind,
                ScopedMemoryRecord.project_id == project_id, ScopedMemoryRecord.sensitivity == "normal",
                or_(ScopedMemoryRecord.expires_at.is_(None), ScopedMemoryRecord.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)),
            ).all()
            scored = []
            for row in rows:
                score = sum(term in row.content.casefold() for term in terms)
                if score:
                    scored.append((score, row))
            scored.sort(key=lambda item: (-item[0], item[1].updated_at), reverse=False)
            return [{"id": row.id, "source_kind": row.source_kind, "source_id": row.source_id,
                     "text": row.content[:500], "scope": {"kind": scope_kind, "project_id": project_id}}
                    for _score, row in scored[:limit]]
        finally:
            db.close()
