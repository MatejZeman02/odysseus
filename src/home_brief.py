"""Accepted home-brief writes shared by the owner editor and the agent.

A home brief is the durable memory of one Personal Advisor or project home.
Two writers exist: the owner's Context editor, and the ``update_memory`` tool
when the owner has switched on "Update memory" for the chat. Both produce an
``accepted`` revision in the continuity store, so every change is kept as a
revision and the owner can see who made it from ``derivation_method``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from src.continuity.contracts import PersonalBriefV1, ProjectBriefV1
from src.continuity.store import ContinuityStore

logger = logging.getLogger(__name__)

OWNER_EDIT = "owner_edit_v1"
AGENT_EDIT = "agent_edit_v1"

MAX_ITEMS = 30
MAX_ITEM_CHARS = 500
MAX_SUMMARY_CHARS = 4000

# Sections the agent may change. Provenance lists (source ids, refs) and the
# derived working state stay server-owned.
AGENT_SECTIONS = {
    "project": ("summary", "confirmed_facts", "accepted_decisions", "open_questions",
                "current_plans", "failed_approaches"),
    "personal": ("summary", "confirmed_facts", "preferences", "ongoing_goals",
                 "commitments", "open_questions", "failed_approaches"),
}


class BriefEditError(ValueError):
    """A memory edit the caller can correct. The message is safe to show."""


class BriefAlreadyHolds(BriefEditError):
    """The item is already saved, so the edit changes nothing."""


@dataclass(frozen=True)
class BriefWrite:
    revision: int
    brief: dict[str, Any]


def _project_brief(project_id: str, values: dict[str, Any], method: str) -> ProjectBriefV1:
    return ProjectBriefV1(
        project_id=project_id,
        summary=str(values.get("summary") or ""),
        derived_working_state=dict(values.get("derived_working_state") or {}),
        confirmed_facts=list(values.get("confirmed_facts") or []),
        accepted_decisions=list(values.get("accepted_decisions") or []),
        proposals=list(values.get("proposals") or []),
        failed_approaches=list(values.get("failed_approaches") or []),
        open_questions=list(values.get("open_questions") or []),
        current_plans=list(values.get("current_plans") or []),
        source_refs=list(values.get("source_refs") or []),
        source_session_ids=list(values.get("source_session_ids") or []),
        source_message_ids=list(values.get("source_message_ids") or []),
        source_through_message_id=values.get("source_through_message_id") or None,
        source_revision=values.get("source_revision") or None,
        derivation_status="accepted",
        derivation_version=1,
        derivation_method=method,
    )


def _personal_brief(owner: str, values: dict[str, Any], method: str) -> PersonalBriefV1:
    return PersonalBriefV1(
        owner_id=owner,
        derivation_status="accepted",
        derivation_version=1,
        derivation_method=method,
        summary=str(values.get("summary") or ""),
        confirmed_facts=list(values.get("confirmed_facts") or []),
        preferences=list(values.get("preferences") or []),
        ongoing_goals=list(values.get("ongoing_goals") or []),
        commitments=list(values.get("commitments") or []),
        proposals=list(values.get("proposals") or []),
        recurring_themes=list(values.get("recurring_themes") or []),
        failed_approaches=list(values.get("failed_approaches") or []),
        open_questions=list(values.get("open_questions") or []),
        artifact_refs=list(values.get("artifact_refs") or []),
        source_refs=list(values.get("source_refs") or []),
        source_message_ids=list(values.get("source_message_ids") or []),
        source_through_message_id=values.get("source_through_message_id") or None,
    )


def current_values(*, owner: str, scope_kind: str, session_id: str, project_id: str | None) -> dict[str, Any]:
    store = ContinuityStore()
    if scope_kind == "project":
        current = store.latest_project_brief(owner=owner, project_id=str(project_id or ""))
    else:
        current = store.latest_personal_brief(owner=owner, session_id=session_id)
    return current.to_payload() if current else {}


def write_home_brief(
    *,
    owner: str,
    scope_kind: str,
    session_id: str,
    project_id: str | None,
    updates: dict[str, Any],
    method: str,
) -> BriefWrite:
    """Merge ``updates`` into the current brief and store an accepted revision.

    Fields missing from ``updates`` keep their accepted value, so an editor
    that renders only the summary cannot erase lists it never showed.
    """
    if scope_kind not in {"personal", "project"}:
        raise BriefEditError("Only Personal Advisor and project homes have a memory brief.")
    if scope_kind == "project" and not project_id:
        raise BriefEditError("This chat is not bound to a project.")
    values = current_values(owner=owner, scope_kind=scope_kind, session_id=session_id, project_id=project_id)
    values.update(updates)
    store = ContinuityStore()
    if scope_kind == "project":
        brief = _project_brief(str(project_id), values, method)
        source_hash = hashlib.sha256(json.dumps(brief.to_payload(), sort_keys=True).encode()).hexdigest()
        result = store.write_project_brief(owner=owner, brief=brief, source_hash=source_hash)
    else:
        brief = _personal_brief(owner, values, method)
        source_hash = hashlib.sha256(json.dumps(brief.to_payload(), sort_keys=True).encode()).hexdigest()
        result = store.write_personal_brief(
            owner=owner, session_id=session_id, brief=brief, source_hash=source_hash,
        )
    index_accepted_home_brief(
        owner=owner, session_id=session_id, scope_kind=scope_kind,
        project_id=project_id if scope_kind == "project" else None,
        source_id=result.id, brief=brief,
    )
    return BriefWrite(revision=result.revision, brief=brief.to_payload())


def _clean_item(text: Any) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        raise BriefEditError("`text` must not be empty.")
    if len(value) > MAX_ITEM_CHARS:
        raise BriefEditError(f"Keep one memory item under {MAX_ITEM_CHARS} characters.")
    return value


def agent_edit(
    *,
    owner: str,
    scope_kind: str,
    session_id: str,
    project_id: str | None,
    action: str,
    section: str,
    text: Any = "",
    old_text: Any = "",
) -> BriefWrite:
    """Apply one small agent edit: add, remove or replace one item."""
    sections = AGENT_SECTIONS.get(scope_kind)
    if not sections:
        raise BriefEditError("This chat has no memory brief to update.")
    if section not in sections:
        raise BriefEditError(f"`section` must be one of: {', '.join(sections)}.")
    if action not in {"add", "remove", "replace"}:
        raise BriefEditError("`action` must be add, remove or replace.")
    values = current_values(owner=owner, scope_kind=scope_kind, session_id=session_id, project_id=project_id)
    if section == "summary":
        if action == "remove":
            summary = ""
        else:
            summary = str(text or "").strip()
            if not summary:
                raise BriefEditError("`text` must hold the new summary.")
            if len(summary) > MAX_SUMMARY_CHARS:
                raise BriefEditError(f"Keep the summary under {MAX_SUMMARY_CHARS} characters.")
        return write_home_brief(
            owner=owner, scope_kind=scope_kind, session_id=session_id, project_id=project_id,
            updates={"summary": summary}, method=AGENT_EDIT,
        )
    items = [str(item) for item in values.get(section) or []]
    if action == "add":
        item = _clean_item(text)
        if item in items:
            raise BriefAlreadyHolds("That item is already in memory.")
        if len(items) >= MAX_ITEMS:
            raise BriefEditError(f"`{section}` already holds {MAX_ITEMS} items. Remove or merge one first.")
        items.append(item)
    else:
        target = _clean_item(old_text if action == "replace" else (old_text or text))
        matches = [index for index, item in enumerate(items) if " ".join(item.split()) == target]
        if not matches:
            raise BriefEditError("No item in that section matches exactly. Quote it as it appears in memory.")
        if action == "remove":
            items.pop(matches[0])
        else:
            items[matches[0]] = _clean_item(text)
    return write_home_brief(
        owner=owner, scope_kind=scope_kind, session_id=session_id, project_id=project_id,
        updates={section: items}, method=AGENT_EDIT,
    )


def index_accepted_home_brief(
    *, owner: str, session_id: str, scope_kind: str, project_id: str | None,
    source_id: str, brief: PersonalBriefV1 | object,
) -> None:
    """Index only an owner-approved brief, never a heuristic checkpoint.

    Failure is deliberately non-fatal. The accepted brief remains exact context
    in the continuity store, episodic retrieval is merely an accelerator.
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
