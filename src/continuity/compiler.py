"""Deterministic, non-destructive checkpointing and context assembly."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Optional

from .contracts import ContextBundle, ProjectBriefV1, ThreadCheckpointV1
from .store import ContinuityStore


def _message_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, Mapping):
        raw = dict(message)
    else:
        raw = {
            "role": getattr(message, "role", ""),
            "content": getattr(message, "content", ""),
            "metadata": getattr(message, "metadata", None),
        }
    return {
        "role": str(raw.get("role") or ""),
        "content": raw.get("content") if raw.get("content") is not None else "",
        "metadata": dict(raw.get("metadata") or {}),
    }


def transcript_fingerprint(messages: Iterable[Any]) -> str:
    normalized = [_message_dict(message) for message in messages]
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _tail_start(messages: list[dict[str, Any]], tail_count: int) -> int:
    start = max(0, len(messages) - tail_count)
    # A tool result cannot be replayed without its initiating assistant call.
    # Move the tail boundary to that call so tool traces remain atomic.
    if start and messages[start]["role"] == "tool":
        for index in range(start - 1, -1, -1):
            if messages[index]["role"] == "assistant":
                return index
    return start


class CheckpointCompactor:
    """Derives checkpoints while leaving the authoritative transcript intact."""

    def __init__(self, store: ContinuityStore, *, tail_count: int = 12):
        if tail_count < 1:
            raise ValueError("tail_count must be positive")
        self.store = store
        self.tail_count = tail_count

    def checkpoint(
        self,
        *,
        owner: str,
        session_id: str,
        messages: Iterable[Any],
        derive: Optional[Callable[[tuple[dict[str, Any], ...]], Mapping[str, Any]]] = None,
    ) -> Optional[ThreadCheckpointV1]:
        transcript = [_message_dict(message) for message in messages]
        start = _tail_start(transcript, self.tail_count)
        source = transcript[:start]
        if not source:
            return None
        source_ids = [str(item["metadata"].get("_db_id") or "") for item in source]
        if not all(source_ids):
            raise ValueError("checkpoint source messages require durable _db_id metadata")
        values = dict(derive(tuple(source)) if derive else {})
        scope = self.store.resolve_scope(owner=owner, session_id=session_id)
        checkpoint = ThreadCheckpointV1(
            session_id=session_id,
            project_id=scope.project_id,
            objective=str(values.get("objective") or ""),
            derived_working_state=dict(values.get("derived_working_state") or {}),
            accepted_decisions=list(values.get("accepted_decisions") or []),
            proposals=list(values.get("proposals") or []),
            open_questions=list(values.get("open_questions") or []),
            next_actions=list(values.get("next_actions") or []),
            artifact_refs=list(values.get("artifact_refs") or []),
            failures=list(values.get("failures") or []),
            source_message_ids=source_ids,
            source_through_message_id=source_ids[-1],
            source_hash=transcript_fingerprint(source),
        )
        self.store.write_thread_checkpoint(owner=owner, checkpoint=checkpoint)
        return checkpoint


class ContextCompiler:
    """Builds a transparent bounded context without cross-scope transcript reads."""

    def __init__(self, store: ContinuityStore, *, tail_count: int = 12):
        self.store = store
        self.tail_count = tail_count

    def compile(
        self,
        *,
        owner: str,
        session_id: str,
        request: str,
        transcript: Iterable[Any],
        companion_profile: str = "",
        related_project_ids: Iterable[str] = (),
        episodic_hits: Iterable[Mapping[str, Any]] = (),
    ) -> ContextBundle:
        scope = self.store.resolve_scope(owner=owner, session_id=session_id)
        messages = [_message_dict(message) for message in transcript]
        tail = tuple(messages[_tail_start(messages, self.tail_count):])
        checkpoint = self.store.latest_thread_checkpoint(owner=owner, session_id=session_id)
        primary = self.store.latest_project_brief(owner=owner, project_id=scope.project_id) if scope.project_id else None
        related: list[ProjectBriefV1] = []
        if scope.project_id:
            for project_id in dict.fromkeys(related_project_ids):
                brief = self.store.related_project_brief(
                    owner=owner, home_project_id=scope.project_id, requested_project_id=project_id
                )
                if brief:
                    related.append(brief)
        hits = tuple(dict(hit) for hit in episodic_hits)
        manifest = {
            "scope": {"kind": scope.scope_kind, "project_id": scope.project_id},
            "thread_checkpoint": bool(checkpoint),
            "primary_project_brief": bool(primary),
            "related_project_ids": [brief.project_id for brief in related],
            "episodic_hit_count": len(hits),
            "transcript_tail_message_ids": [item["metadata"].get("_db_id") for item in tail],
        }
        return ContextBundle(companion_profile, scope, request, checkpoint, primary, tuple(related), hits, tail, manifest)
