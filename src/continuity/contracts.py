"""Typed, versioned payload contracts for the lean continuity MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional


SCOPES = frozenset({"project", "personal", "computer", "general"})
DERIVATION_STATUSES = frozenset({"legacy_unclassified", "heuristic", "proposed", "accepted", "rejected"})
SEMANTIC_PROPOSAL_SCOPES = frozenset({"project", "personal"})
_MAX_PROPOSAL_TEXT = 4_000
_MAX_PROPOSAL_ITEMS = 30


class ContractError(ValueError):
    """A persisted continuity payload does not meet its versioned contract."""


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContractError(f"{field_name} must be a list of strings")
    return list(value)


def _bounded_text(value: Any, field_name: str, *, maximum: int = _MAX_PROPOSAL_TEXT) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field_name} must be a string")
    text = value.strip()
    if len(text) > maximum:
        raise ContractError(f"{field_name} exceeds {maximum} characters")
    return text


def _bounded_text_list(value: Any, field_name: str, *, maximum: int = _MAX_PROPOSAL_ITEMS) -> list[str]:
    values = _text_list(value, field_name)
    if len(values) > maximum:
        raise ContractError(f"{field_name} exceeds {maximum} entries")
    return [_bounded_text(item, f"{field_name} item") for item in values]


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(f"{field_name} must be an object")
    return dict(value)


def _derivation_status(value: Any) -> str:
    status = str(value or "legacy_unclassified")
    if status not in DERIVATION_STATUSES:
        raise ContractError(f"unsupported derivation_status: {status!r}")
    return status


def _derivation_version(value: Any) -> int:
    try:
        version = int(value if value is not None else 0)
    except (TypeError, ValueError) as exc:
        raise ContractError("derivation_version must be a non-negative integer") from exc
    if version < 0:
        raise ContractError("derivation_version must be a non-negative integer")
    return version


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
    derivation_status: str = "legacy_unclassified"
    derivation_version: int = 0
    derivation_method: str = "legacy_unclassified"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ContractError("unsupported ThreadCheckpoint schema_version")
        if not self.session_id or not self.source_hash:
            raise ContractError("checkpoint requires session_id and source_hash")
        if self.source_through_message_id and self.source_through_message_id not in self.source_message_ids:
            raise ContractError("source_through_message_id must be among source_message_ids")
        _derivation_status(self.derivation_status)
        _derivation_version(self.derivation_version)

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
            derivation_status=_derivation_status(data.get("derivation_status")),
            derivation_version=_derivation_version(data.get("derivation_version")),
            derivation_method=str(data.get("derivation_method") or "legacy_unclassified"),
            schema_version=data.get("schema_version", 1),
        )


@dataclass(frozen=True)
class ProjectBriefV1:
    project_id: str
    summary: str = ""
    derived_working_state: dict[str, Any] = field(default_factory=dict)
    confirmed_facts: list[str] = field(default_factory=list)
    accepted_decisions: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    failed_approaches: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    current_plans: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    source_session_ids: list[str] = field(default_factory=list)
    source_message_ids: list[str] = field(default_factory=list)
    source_through_message_id: Optional[str] = None
    source_revision: Optional[str] = None
    updated_at: Optional[str] = None
    derivation_status: str = "legacy_unclassified"
    derivation_version: int = 0
    derivation_method: str = "legacy_unclassified"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ContractError("unsupported ProjectBrief schema_version")
        if not self.project_id:
            raise ContractError("project brief requires project_id")
        _derivation_status(self.derivation_status)
        _derivation_version(self.derivation_version)
        if self.source_through_message_id and self.source_through_message_id not in self.source_message_ids:
            raise ContractError("source_through_message_id must be among source_message_ids")

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Any) -> "ProjectBriefV1":
        data = _mapping(payload, "project brief payload")
        return cls(
            project_id=str(data.get("project_id") or ""),
            summary=str(data.get("summary") or ""),
            derived_working_state=_mapping(data.get("derived_working_state"), "derived_working_state"),
            confirmed_facts=_text_list(data.get("confirmed_facts"), "confirmed_facts"),
            accepted_decisions=_text_list(data.get("accepted_decisions"), "accepted_decisions"),
            proposals=_text_list(data.get("proposals"), "proposals"),
            failed_approaches=_text_list(data.get("failed_approaches"), "failed_approaches"),
            open_questions=_text_list(data.get("open_questions"), "open_questions"),
            current_plans=_text_list(data.get("current_plans"), "current_plans"),
            source_refs=_text_list(data.get("source_refs"), "source_refs"),
            source_session_ids=_text_list(data.get("source_session_ids"), "source_session_ids"),
            source_message_ids=_text_list(data.get("source_message_ids"), "source_message_ids"),
            source_through_message_id=data.get("source_through_message_id") or None,
            source_revision=data.get("source_revision") or None,
            updated_at=data.get("updated_at") or None,
            derivation_status=_derivation_status(data.get("derivation_status")),
            derivation_version=_derivation_version(data.get("derivation_version")),
            derivation_method=str(data.get("derivation_method") or "legacy_unclassified"),
            schema_version=data.get("schema_version", 1),
        )


@dataclass(frozen=True)
class PersonalBriefV1:
    """Inspectable, owner-scoped Personal Advisor home memory.

    This deliberately contains only owner-approved/derived compact state.  It
    is not a second copy of the Personal transcript and it is never used as a
    cross-project index.
    """
    owner_id: str
    summary: str = ""
    confirmed_facts: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    ongoing_goals: list[str] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    recurring_themes: list[str] = field(default_factory=list)
    failed_approaches: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    source_message_ids: list[str] = field(default_factory=list)
    source_through_message_id: Optional[str] = None
    derivation_status: str = "legacy_unclassified"
    derivation_version: int = 0
    derivation_method: str = "legacy_unclassified"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ContractError("unsupported PersonalBrief schema_version")
        if not self.owner_id:
            raise ContractError("personal brief requires owner_id")
        _derivation_status(self.derivation_status)
        _derivation_version(self.derivation_version)
        if self.source_through_message_id and self.source_through_message_id not in self.source_message_ids:
            raise ContractError("source_through_message_id must be among source_message_ids")

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Any) -> "PersonalBriefV1":
        data = _mapping(payload, "personal brief payload")
        return cls(
            owner_id=str(data.get("owner_id") or ""),
            summary=str(data.get("summary") or ""),
            confirmed_facts=_text_list(data.get("confirmed_facts"), "confirmed_facts"),
            preferences=_text_list(data.get("preferences"), "preferences"),
            ongoing_goals=_text_list(data.get("ongoing_goals"), "ongoing_goals"),
            commitments=_text_list(data.get("commitments"), "commitments"),
            proposals=_text_list(data.get("proposals"), "proposals"),
            recurring_themes=_text_list(data.get("recurring_themes"), "recurring_themes"),
            failed_approaches=_text_list(data.get("failed_approaches"), "failed_approaches"),
            open_questions=_text_list(data.get("open_questions"), "open_questions"),
            artifact_refs=_text_list(data.get("artifact_refs"), "artifact_refs"),
            source_refs=_text_list(data.get("source_refs"), "source_refs"),
            source_message_ids=_text_list(data.get("source_message_ids"), "source_message_ids"),
            source_through_message_id=data.get("source_through_message_id") or None,
            derivation_status=_derivation_status(data.get("derivation_status")),
            derivation_version=_derivation_version(data.get("derivation_version")),
            derivation_method=str(data.get("derivation_method") or "legacy_unclassified"),
            schema_version=data.get("schema_version", 1),
        )


@dataclass(frozen=True)
class SemanticCheckpointProposalV1:
    """A source-linked candidate memory extraction awaiting owner promotion.

    This is deliberately distinct from a checkpoint and home brief: model
    output remains a proposal until an owner selects individual entries for a
    new accepted home-state revision.
    """
    session_id: str
    scope_kind: str
    project_id: Optional[str] = None
    objective: str = ""
    facts: list[str] = field(default_factory=list)
    decision_candidates: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    failed_approaches: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    source_message_ids: list[str] = field(default_factory=list)
    source_through_message_id: Optional[str] = None
    source_hash: str = ""
    derivation_model: str = ""
    derivation_route: str = "session_model_no_tools_v1"
    derivation_status: str = "proposed"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ContractError("unsupported SemanticCheckpointProposal schema_version")
        if not self.session_id or not self.source_hash:
            raise ContractError("semantic proposal requires session_id and source_hash")
        if self.scope_kind not in SEMANTIC_PROPOSAL_SCOPES:
            raise ContractError("semantic proposal requires a project or personal scope")
        if (self.scope_kind == "project") != bool(self.project_id):
            raise ContractError("semantic proposal project binding does not match its scope")
        if not self.source_message_ids:
            raise ContractError("semantic proposal requires source_message_ids")
        if self.source_through_message_id and self.source_through_message_id not in self.source_message_ids:
            raise ContractError("source_through_message_id must be among source_message_ids")
        if self.derivation_status != "proposed":
            raise ContractError("semantic proposal derivation_status must be 'proposed'")
        _bounded_text(self.objective, "objective")
        _bounded_text_list(self.facts, "facts")
        _bounded_text_list(self.decision_candidates, "decision_candidates")
        _bounded_text_list(self.proposals, "proposals")
        _bounded_text_list(self.failed_approaches, "failed_approaches")
        _bounded_text_list(self.open_questions, "open_questions")
        _bounded_text_list(self.next_actions, "next_actions")
        _bounded_text_list(self.artifact_refs, "artifact_refs")
        _bounded_text(self.derivation_model, "derivation_model", maximum=512)
        _bounded_text(self.derivation_route, "derivation_route", maximum=128)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Any) -> "SemanticCheckpointProposalV1":
        data = _mapping(payload, "semantic proposal payload")
        return cls(
            session_id=str(data.get("session_id") or ""),
            scope_kind=str(data.get("scope_kind") or ""),
            project_id=data.get("project_id") or None,
            objective=_bounded_text(str(data.get("objective") or ""), "objective"),
            facts=_bounded_text_list(data.get("facts"), "facts"),
            decision_candidates=_bounded_text_list(data.get("decision_candidates"), "decision_candidates"),
            proposals=_bounded_text_list(data.get("proposals"), "proposals"),
            failed_approaches=_bounded_text_list(data.get("failed_approaches"), "failed_approaches"),
            open_questions=_bounded_text_list(data.get("open_questions"), "open_questions"),
            next_actions=_bounded_text_list(data.get("next_actions"), "next_actions"),
            artifact_refs=_bounded_text_list(data.get("artifact_refs"), "artifact_refs"),
            source_message_ids=_text_list(data.get("source_message_ids"), "source_message_ids"),
            source_through_message_id=data.get("source_through_message_id") or None,
            source_hash=str(data.get("source_hash") or ""),
            derivation_model=_bounded_text(str(data.get("derivation_model") or ""), "derivation_model", maximum=512),
            derivation_route=_bounded_text(str(data.get("derivation_route") or "session_model_no_tools_v1"), "derivation_route", maximum=128),
            derivation_status=str(data.get("derivation_status") or "proposed"),
            schema_version=data.get("schema_version", 1),
        )


SEMANTIC_PROPOSAL_ENTRY_FIELDS = frozenset({
    "objective",
    "facts",
    "decision_candidates",
    "proposals",
    "failed_approaches",
    "open_questions",
    "next_actions",
    "artifact_refs",
})
CHECKPOINT_PROMOTION_FIELDS = frozenset({
    "objective", "accepted_decisions", "proposals", "failures",
    "open_questions", "next_actions", "artifact_refs",
})


def selected_proposal_entries(
    proposal: SemanticCheckpointProposalV1,
    selections: dict[str, list[int]],
) -> dict[str, list[str]]:
    """Resolve an owner selection without accepting browser-provided text."""
    if not isinstance(selections, dict) or not selections:
        raise ContractError("semantic proposal promotion requires selected entries")
    selected: dict[str, list[str]] = {}
    seen: set[tuple[str, int]] = set()
    total = 0
    for field_name, indexes in selections.items():
        if field_name not in SEMANTIC_PROPOSAL_ENTRY_FIELDS:
            raise ContractError(f"unsupported semantic proposal field: {field_name}")
        if not isinstance(indexes, list) or not indexes:
            raise ContractError(f"selection for {field_name} must be a non-empty index list")
        values = [proposal.objective] if field_name == "objective" else list(getattr(proposal, field_name))
        for index in indexes:
            if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(values):
                raise ContractError(f"selection index for {field_name} is out of range")
            key = (field_name, index)
            if key in seen:
                raise ContractError("semantic proposal selection contains a duplicate entry")
            seen.add(key)
            selected.setdefault(field_name, []).append(values[index])
            total += 1
            if total > _MAX_PROPOSAL_ITEMS:
                raise ContractError(f"semantic proposal selection exceeds {_MAX_PROPOSAL_ITEMS} entries")
    return selected


def selected_checkpoint_entries(
    checkpoint: ThreadCheckpointV1, selections: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Resolve owner-selected source-checkpoint entries; never accept text."""
    if not isinstance(selections, Mapping) or not selections:
        raise ContractError("checkpoint promotion requires selected entries")
    if set(selections) - CHECKPOINT_PROMOTION_FIELDS:
        raise ContractError("checkpoint promotion contains unsupported fields")
    values_by_field: dict[str, list[str]] = {
        "objective": [checkpoint.objective] if checkpoint.objective else [],
        "accepted_decisions": list(checkpoint.accepted_decisions),
        "proposals": list(checkpoint.proposals),
        "failures": list(checkpoint.failures),
        "open_questions": list(checkpoint.open_questions),
        "next_actions": list(checkpoint.next_actions),
        "artifact_refs": list(checkpoint.artifact_refs),
    }
    selected: dict[str, list[str]] = {}
    total = 0
    for field_name, indexes in selections.items():
        if not isinstance(indexes, list) or not indexes:
            raise ContractError("checkpoint promotion selections must be non-empty index lists")
        values = values_by_field[field_name]
        seen: set[int] = set()
        for index in indexes:
            if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(values):
                raise ContractError("checkpoint promotion selection index is out of range")
            if index in seen:
                raise ContractError("checkpoint promotion selection contains a duplicate entry")
            seen.add(index)
            selected.setdefault(field_name, []).append(values[index])
            total += 1
            if total > _MAX_PROPOSAL_ITEMS:
                raise ContractError(f"checkpoint promotion selection exceeds {_MAX_PROPOSAL_ITEMS} entries")
    return selected


@dataclass(frozen=True)
class ContextBundle:
    """Deterministic input set shared by native and worker harnesses."""
    companion_profile: str
    scope: ResolvedScope
    request: str
    thread_checkpoint: Optional[ThreadCheckpointV1] = None
    primary_project_brief: Optional[ProjectBriefV1] = None
    related_project_briefs: tuple[ProjectBriefV1, ...] = ()
    personal_brief: Optional[PersonalBriefV1] = None
    mounted_checkpoints: tuple[dict[str, Any], ...] = ()
    working_artifacts: tuple[dict[str, Any], ...] = ()
    context_grants: tuple[dict[str, Any], ...] = ()
    episodic_hits: tuple[dict[str, Any], ...] = ()
    transcript_tail: tuple[dict[str, Any], ...] = ()
    manifest: dict[str, Any] = field(default_factory=dict)
