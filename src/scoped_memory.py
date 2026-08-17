"""Strictly scoped, rebuildable episodic recall for G2C.

This local provider-neutral index is deliberately small.  AgentMemory remains
an optional future backend; no existing native/AgentMemory writer is changed.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from core.database import ScopedMemoryRecord, SessionLocal


class ScopedMemoryIndex:
    def index(self, *, owner: str, scope_kind: str, project_id: str | None, session_id: str | None,
              source_kind: str, source_id: str, content: str, sensitivity: str = "normal") -> None:
        if not owner or scope_kind not in {"personal", "project"} or not source_id:
            return
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
            else:
                db.add(ScopedMemoryRecord(id=uuid.uuid4().hex, owner=owner, scope_kind=scope_kind,
                                          project_id=project_id, session_id=session_id, source_kind=source_kind,
                                          source_id=source_id, content=text, sensitivity=sensitivity))
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
