"""Typed, versioned payload contracts for the lean continuity MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


SCOPES = frozenset({"project", "personal", "computer", "general"})


class ContractError(ValueError):
    """A persisted continuity payload does not meet its versioned contract."""


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContractError(f"{field_name} must be a list of strings")
    return list(value)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(f"{field_name} must be an object")
    return dict(value)


@dataclass(frozen=True)
class ResolvedScope:
    owner_id: str
    session_id: str
    scope_kind: str
    project_id: Optional[str] = None
    workspace_root: Optional[str] = None

    def __post_init__(self) -> None:
        if self.scope_kind not in SCOPES:
            raise ContractError(f"unsupported scope_kind: {self.scope_kind!r}")
        if self.scope_kind == "project" and not self.project_id:
            raise ContractError("project scope requires project_id")
        if self.scope_kind != "project" and self.project_id:
            raise ContractError("only project scope may carry project_id")


@dataclass(frozen=True)
class ThreadCheckpointV1:
    session_id: str
    project_id: Optional[str] = None
    objective: str = ""
    derived_working_state: dict[str, Any] = field(default_factory=dict)
    accepted_decisions: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    source_message_ids: list[str] = field(default_factory=list)
    source_through_message_id: Optional[str] = None
    source_hash: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ContractError("unsupported ThreadCheckpoint schema_version")
        if not self.session_id or not self.source_hash:
            raise ContractError("checkpoint requires session_id and source_hash")
        if self.source_through_message_id and self.source_through_message_id not in self.source_message_ids:
            raise ContractError("source_through_message_id must be among source_message_ids")

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Any) -> "ThreadCheckpointV1":
        data = _mapping(payload, "checkpoint payload")
        return cls(
            session_id=str(data.get("session_id") or ""),
            project_id=data.get("project_id") or None,
            objective=str(data.get("objective") or ""),
            derived_working_state=_mapping(data.get("derived_working_state"), "derived_working_state"),
            accepted_decisions=_text_list(data.get("accepted_decisions"), "accepted_decisions"),
            proposals=_text_list(data.get("proposals"), "proposals"),
            open_questions=_text_list(data.get("open_questions"), "open_questions"),
            next_actions=_text_list(data.get("next_actions"), "next_actions"),
            artifact_refs=_text_list(data.get("artifact_refs"), "artifact_refs"),
            failures=_text_list(data.get("failures"), "failures"),
            source_message_ids=_text_list(data.get("source_message_ids"), "source_message_ids"),
            source_through_message_id=data.get("source_through_message_id") or None,
            source_hash=str(data.get("source_hash") or ""),
            schema_version=data.get("schema_version", 1),
        )


@dataclass(frozen=True)
class ProjectBriefV1:
    project_id: str
    summary: str = ""
    derived_working_state: dict[str, Any] = field(default_factory=dict)
    accepted_decisions: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    current_plans: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    source_session_ids: list[str] = field(default_factory=list)
    source_revision: Optional[str] = None
    updated_at: Optional[str] = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ContractError("unsupported ProjectBrief schema_version")
        if not self.project_id:
            raise ContractError("project brief requires project_id")

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Any) -> "ProjectBriefV1":
        data = _mapping(payload, "project brief payload")
        return cls(
            project_id=str(data.get("project_id") or ""),
            summary=str(data.get("summary") or ""),
            derived_working_state=_mapping(data.get("derived_working_state"), "derived_working_state"),
            accepted_decisions=_text_list(data.get("accepted_decisions"), "accepted_decisions"),
            proposals=_text_list(data.get("proposals"), "proposals"),
            open_questions=_text_list(data.get("open_questions"), "open_questions"),
            current_plans=_text_list(data.get("current_plans"), "current_plans"),
            source_refs=_text_list(data.get("source_refs"), "source_refs"),
            source_session_ids=_text_list(data.get("source_session_ids"), "source_session_ids"),
            source_revision=data.get("source_revision") or None,
            updated_at=data.get("updated_at") or None,
            schema_version=data.get("schema_version", 1),
        )


@dataclass(frozen=True)
class ContextBundle:
    """Deterministic input set shared by native and worker harnesses."""
    companion_profile: str
    scope: ResolvedScope
    request: str
    thread_checkpoint: Optional[ThreadCheckpointV1] = None
    primary_project_brief: Optional[ProjectBriefV1] = None
    related_project_briefs: tuple[ProjectBriefV1, ...] = ()
    episodic_hits: tuple[dict[str, Any], ...] = ()
    transcript_tail: tuple[dict[str, Any], ...] = ()
    manifest: dict[str, Any] = field(default_factory=dict)
