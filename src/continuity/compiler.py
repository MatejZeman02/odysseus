"""Deterministic, non-destructive checkpointing and context assembly."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Optional

from .contracts import ContextBundle, DeviceProfileV1, PersonalBriefV1, ProjectBriefV1, ThreadCheckpointV1
from .store import ContinuityStore


_MAX_EXPLICIT_ARTIFACTS = 2
_MAX_EXPLICIT_ARTIFACT_CHARS = 64 * 1024


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


def _explicit_artifact_paths(request: str, artifacts: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Select only scoped artifacts the owner explicitly named this turn.

    A path, filename, or readable stem (``love letter`` for
    ``love-letter.md``) selects an artifact. Generic wording such as "my
    draft" does not: the model sees the metadata index and can ask which one.
    """
    requested = re.sub(r"\s+", " ", request.casefold().replace("\\", "/"))
    selected: list[str] = []
    for artifact in artifacts:
        path = str(artifact.get("path") or "")
        if not path:
            continue
        normalized_path = path.casefold()
        filename = normalized_path.rsplit("/", 1)[-1]
        stem = filename.rsplit(".", 1)[0]
        title = re.sub(r"[_-]+", " ", stem).strip()
        if normalized_path in requested or filename in requested or (title and title in requested):
            selected.append(path)
        if len(selected) >= _MAX_EXPLICIT_ARTIFACTS:
            break
    return tuple(selected)


def transcript_fingerprint(messages: Iterable[Any]) -> str:
    normalized = [_message_dict(message) for message in messages]
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def derive_checkpoint_fields(source: tuple[dict[str, Any], ...]) -> Mapping[str, Any]:
    """Small availability-only checkpoint derivation.

    This intentionally does not call a provider while a user is waiting for a
    chat answer. It may only retain the user's current objective, questions,
    and requested actions. In particular, it must never infer that assistant
    prose represents an accepted decision.
    """
    users = [str(item.get("content") or "").strip() for item in source if item.get("role") == "user"]
    objective = users[-1][:500] if users else ""
    questions = [text[:300] for text in users[-3:] if "?" in text][-3:]
    actions = [text[:300] for text in users[-3:] if any(word in text.lower() for word in ("please", "create", "plan", "write", "review", "fix"))][-3:]
    return {
        "objective": objective,
        "open_questions": questions,
        "next_actions": actions,
        "derivation_status": "heuristic",
        "derivation_version": 1,
        "derivation_method": "local_heuristic_v1",
    }


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
        previous = self.store.latest_thread_checkpoint(owner=owner, session_id=session_id)
        source_start = 0
        if previous and previous.source_through_message_id:
            ids = [str(item["metadata"].get("_db_id") or "") for item in transcript]
            try:
                source_start = ids.index(previous.source_through_message_id) + 1
            except ValueError:
                # Histories may legitimately lose an older row when the owner
                # deletes it, or when a cold session has only the persisted
                # tail hydrated.  A checkpoint is an acceleration layer, not
                # authority to reject an otherwise successful turn.  Rebuild
                # from the currently available transcript in that case; the
                # new checkpoint carries only durable IDs that actually exist.
                source_start = 0
        source = transcript[source_start:start]
        if not source:
            return previous
        source_ids = [str(item["metadata"].get("_db_id") or "") for item in source]
        if not all(source_ids):
            raise ValueError("checkpoint source messages require durable _db_id metadata")
        values = dict(derive(tuple(source)) if derive else derive_checkpoint_fields(tuple(source)))
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
            derivation_status=str(values.get("derivation_status") or "heuristic"),
            derivation_version=values.get("derivation_version", 1),
            derivation_method=str(values.get("derivation_method") or "local_heuristic_v1"),
        )
        self.store.write_thread_checkpoint(owner=owner, checkpoint=checkpoint)
        # A heuristic checkpoint is helpful bounded context for *this* thread,
        # but it is not owner-approved episodic memory.  Do not index it: doing
        # so would make provisional model output retrievable by later turns as
        # if it had been accepted.  Semantic-promotion routes index only the
        # explicit accepted home brief instead.
        # Home briefs are compact shared state, never transcript replacement.
        # Projects can safely refresh their shared brief from a checkpoint;
        # Personal briefs are seeded once and remain owner-inspectable/editable
        # rather than silently overwriting an owner-curated summary.
        if scope.project_id:
            self.store.write_project_brief(
                owner=owner,
                brief=ProjectBriefV1(
                    project_id=scope.project_id, summary=checkpoint.objective,
                    derived_working_state=checkpoint.derived_working_state,
                    accepted_decisions=checkpoint.accepted_decisions,
                    proposals=checkpoint.proposals, open_questions=checkpoint.open_questions,
                    current_plans=checkpoint.next_actions, source_refs=checkpoint.artifact_refs,
                    source_session_ids=[session_id], source_message_ids=checkpoint.source_message_ids,
                    source_through_message_id=checkpoint.source_through_message_id,
                    source_revision=checkpoint.source_hash,
                    derivation_status=checkpoint.derivation_status,
                    derivation_version=checkpoint.derivation_version,
                    derivation_method=checkpoint.derivation_method,
                ), source_hash=checkpoint.source_hash,
                source_through_message_id=checkpoint.source_through_message_id,
            )
        elif scope.scope_kind == "personal" and not self.store.latest_personal_brief(owner=owner, session_id=session_id):
            self.store.write_personal_brief(
                owner=owner, session_id=session_id,
                brief=PersonalBriefV1(owner_id=owner, summary=checkpoint.objective,
                                      ongoing_goals=checkpoint.next_actions,
                                      open_questions=checkpoint.open_questions,
                                      artifact_refs=checkpoint.artifact_refs,
                                      source_refs=checkpoint.source_message_ids,
                                      source_message_ids=checkpoint.source_message_ids,
                                      source_through_message_id=checkpoint.source_through_message_id,
                                      derivation_status=checkpoint.derivation_status,
                                      derivation_version=checkpoint.derivation_version,
                                      derivation_method=checkpoint.derivation_method),
                source_hash=checkpoint.source_hash,
                source_through_message_id=checkpoint.source_through_message_id,
            )
        return checkpoint


class ContextCompiler:
    """Builds a transparent bounded context without cross-scope transcript reads."""

    def __init__(self, store: ContinuityStore, *, tail_count: int = 12, scoped_memory_provider: Any = None):
        self.store = store
        self.tail_count = tail_count
        # Tests and future provider integration may inject a registry.  The
        # product default is created lazily to avoid a module cycle with the
        # local provider implementation.
        self.scoped_memory_provider = scoped_memory_provider

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
        personal = self.store.latest_personal_brief(owner=owner, session_id=session_id) if scope.scope_kind == "personal" else None
        device_profile = self.store.latest_device_profile(owner=owner, session_id=session_id) if scope.scope_kind == "computer" else None
        mount_loader = getattr(self.store, "checkpoint_mounts", None)
        mounts = (
            mount_loader(owner=owner, destination_session_id=session_id)
            if scope.scope_kind in {"personal", "project"} and callable(mount_loader)
            else []
        )
        mounted_checkpoints = tuple({
            "mount_id": mount.id,
            "mount_revision": mount.revision,
            "source_checkpoint_id": mount.source_checkpoint_id,
            "source_session_id": mount.source_session_id,
            "sensitivity": mount.sensitivity,
            "expires_at": mount.expires_at,
            "checkpoint": mount.checkpoint.to_payload(),
        } for mount in mounts)
        related: list[ProjectBriefV1] = []
        if scope.project_id:
            # Direct relations are an owner-managed allowlist. A related
            # brief is still admitted only when the owner names that project
            # in the current request with ``@Project Name``. The optional
            # argument remains for trusted server-side callers and tests;
            # browser requests never submit raw project IDs into this path.
            relation_resolver = getattr(self.store, "explicit_related_project_ids", None)
            named_related = (
                relation_resolver(owner=owner, project_id=scope.project_id, request=request)
                if callable(relation_resolver) else []
            )
            requested_related = list(dict.fromkeys([*related_project_ids, *named_related]))[:3]
            for project_id in requested_related:
                brief = self.store.related_project_brief(
                    owner=owner, home_project_id=scope.project_id, requested_project_id=project_id
                )
                if brief:
                    related.append(brief)
        hits = tuple(dict(hit) for hit in episodic_hits)
        if not hits and scope.scope_kind in {"personal", "project"}:
            try:
                from src.memory_provider import (
                    ScopedMemoryProviderRegistry, ScopedMemoryQuery, ScopedMemoryScope,
                )
                from src.scoped_memory import LocalScopedMemoryProvider
                provider = self.scoped_memory_provider or ScopedMemoryProviderRegistry([
                    LocalScopedMemoryProvider(),
                ])
                query_scope = ScopedMemoryScope(
                    owner_id=owner, home_kind=scope.scope_kind, project_id=scope.project_id,
                    session_id=session_id, provenance_kind="context_recall", provenance_id=session_id,
                )
                provider_hits = provider.recall_scoped_sync(ScopedMemoryQuery(
                    request, query_scope, top_k=4,
                ))
                hits = tuple({
                    "id": hit.memory.id,
                    "source_kind": hit.memory.scope.provenance_kind,
                    "source_id": hit.memory.scope.provenance_id,
                    "text": hit.memory.text[:500],
                    "session_id": hit.memory.session_id,
                    "expires_at": hit.memory.scope.expires_at.isoformat() if hit.memory.scope.expires_at else None,
                    "sensitivity": hit.memory.scope.sensitivity,
                    "scope": {
                        "kind": hit.memory.scope.home_kind,
                        "project_id": hit.memory.scope.project_id,
                    },
                } for hit in provider_hits)
            except Exception:
                hits = ()
        working_artifacts: tuple[dict[str, Any], ...] = ()
        context_grants: tuple[dict[str, Any], ...] = ()
        if scope.scope_kind in {"personal", "project"}:
            # This service is DB-only and failure is non-fatal: exact
            # transcript/checkpoint continuity remains available.
            try:
                from src.companion_memory import CompanionMemoryStore
                memory = CompanionMemoryStore()
                working_artifacts = tuple(memory.list_artifacts(
                    owner=owner, scope_kind=scope.scope_kind, project_id=scope.project_id,
                ))
                requested_paths = _explicit_artifact_paths(request, working_artifacts)
                selected_bodies: dict[str, dict[str, Any]] = {}
                for path in requested_paths:
                    artifact = memory.get_scoped_artifact_by_path(
                        owner=owner,
                        scope_kind=scope.scope_kind,
                        project_id=scope.project_id,
                        path=path,
                    )
                    content = str(artifact.get("content") or "")
                    if len(content) > _MAX_EXPLICIT_ARTIFACT_CHARS:
                        artifact["content"] = content[:_MAX_EXPLICIT_ARTIFACT_CHARS]
                        artifact["content_truncated"] = True
                    selected_bodies[path] = artifact
                if selected_bodies:
                    working_artifacts = tuple(
                        selected_bodies.get(str(item.get("path")), item)
                        for item in working_artifacts
                    )
                if scope.scope_kind == "personal":
                    # The Personal Advisor gets an index of its own working
                    # drafts, but exact body text is mounted only when the
                    # owner names a draft in this request. This lets it revise
                    # a named letter without repeating that letter in every
                    # Personal turn or exposing unrelated drafts.
                    context_grants = tuple(memory.approved_grants(owner=owner, personal_session_id=session_id))
                    # An approved grant exposes only a project brief and its
                    # explicitly selected artifact identities, never raw chat.
                    for grant in context_grants:
                        brief = self.store.latest_project_brief(owner=owner, project_id=grant["project_id"])
                        if brief:
                            related.append(brief)
                        requested_paths = set(grant.get("artifact_paths") or [])
                        if requested_paths:
                            allowed = memory.list_artifacts(owner=owner, scope_kind="project", project_id=grant["project_id"])
                            working_artifacts += tuple(item for item in allowed if item.get("path") in requested_paths)
                    # A direct owner instruction such as "check Dust project
                    # memory" is itself approval for this request.  Match a
                    # project name only when the request explicitly asks for
                    # memory/brief/artifacts; casual mention never mounts it.
                    lowered = request.casefold()
                    for project in self.store.project_catalog(owner=owner):
                        name = project["name"].casefold()
                        direct = (name in lowered and re.search(r"\b(memory|brief|artifacts?|context)\b", lowered)
                                  and re.search(r"\b(check|consult|read|look|use|show)\b", lowered))
                        if direct and project["id"] not in {brief.project_id for brief in related}:
                            brief = self.store.latest_project_brief(owner=owner, project_id=project["id"])
                            if brief:
                                related.append(brief)
                                context_grants += ({"id": "direct-owner-request", "project_id": project["id"], "purpose": "Direct owner request for this turn"},)
                    # Approval is deliberately for one compiled request. The
                    # manifest keeps the audit reference for this turn even
                    # though the following Personal turn receives no mount.
                    memory.consume_grants(
                        owner=owner, personal_session_id=session_id,
                        grant_ids=[grant["id"] for grant in context_grants],
                    )
            except Exception:
                working_artifacts, context_grants = (), ()
        # The persisted manifest is an owner-facing audit trail, not another
        # copy of recalled memory. Keep only the contributing source classes;
        # never retain hit text, IDs, ranking scores, or provider metadata.
        episodic_hit_kinds: dict[str, int] = {}
        for hit in hits:
            kind = hit.get("source_kind") if isinstance(hit, Mapping) else None
            safe_kind = str(kind).strip().lower()[:64] if kind else "unknown"
            if not re.fullmatch(r"[a-z0-9_.-]+", safe_kind):
                safe_kind = "unknown"
            episodic_hit_kinds[safe_kind] = episodic_hit_kinds.get(safe_kind, 0) + 1
        manifest = {
            "scope": {"kind": scope.scope_kind, "project_id": scope.project_id},
            "thread_checkpoint": bool(checkpoint),
            "primary_project_brief": bool(primary),
            "personal_brief": bool(personal),
            "device_profile": bool(device_profile),
            "related_project_ids": [brief.project_id for brief in related],
            "episodic_hit_count": len(hits),
            "episodic_hit_kinds": dict(sorted(episodic_hit_kinds.items())),
            "working_artifacts": [{"id": item["id"], "path": item["path"], "revision": item["revision"]} for item in working_artifacts],
            "selected_working_artifact_paths": [
                item["path"] for item in working_artifacts if "content" in item
            ],
            "context_grants": [{"id": item["id"], "project_id": item["project_id"]} for item in context_grants],
            "project_catalog": self.store.project_catalog(owner=owner) if scope.scope_kind == "personal" else [],
            "transcript_tail_message_ids": [item["metadata"].get("_db_id") for item in tail],
            "continuity_provenance": {
                "thread_checkpoint": checkpoint.derivation_status if checkpoint else None,
                "primary_project_brief": primary.derivation_status if primary else None,
                "personal_brief": personal.derivation_status if personal else None,
                "device_profile": "verified_server_observation" if device_profile else None,
            },
            "continuity_records": {
                "thread_checkpoint": self.store.latest_artifact_manifest(
                    owner=owner, kind="thread_checkpoint_v1", session_id=session_id,
                ) if checkpoint else None,
                "primary_project_brief": self.store.latest_artifact_manifest(
                    owner=owner, kind="project_brief_v1", project_id=scope.project_id,
                ) if primary and scope.project_id else None,
                "personal_brief": self.store.latest_artifact_manifest(
                    owner=owner, kind="personal_brief_v1", session_id=session_id,
                ) if personal else None,
                "device_profile": self.store.latest_artifact_manifest(
                    owner=owner, kind="device_profile_v1", session_id=session_id,
                ) if device_profile else None,
                "related_project_briefs": [
                    self.store.latest_artifact_manifest(
                        owner=owner, kind="project_brief_v1", project_id=brief.project_id,
                    )
                    for brief in related
                ],
            },
            "checkpoint_mounts": [{
                "mount_id": mount["mount_id"],
                "mount_revision": mount["mount_revision"],
                "source_checkpoint_id": mount["source_checkpoint_id"],
                "source_session_id": mount["source_session_id"],
                "derivation_status": mount["checkpoint"]["derivation_status"],
                "sensitivity": mount["sensitivity"],
                "expires_at": mount["expires_at"],
                "source_through_message_id": mount["checkpoint"]["source_through_message_id"],
                "source_message_count": len(mount["checkpoint"]["source_message_ids"]),
            } for mount in mounted_checkpoints],
        }
        return ContextBundle(
            companion_profile=companion_profile, scope=scope, request=request,
            thread_checkpoint=checkpoint, primary_project_brief=primary,
            related_project_briefs=tuple(related), personal_brief=personal, device_profile=device_profile,
            mounted_checkpoints=mounted_checkpoints,
            working_artifacts=working_artifacts, context_grants=context_grants,
            episodic_hits=hits, transcript_tail=tail, manifest=manifest,
        )
