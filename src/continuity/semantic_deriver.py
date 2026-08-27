"""Server-owned semantic proposal derivation for foreground and queued use."""

from __future__ import annotations

from dataclasses import dataclass

from core.database import ChatMessage as DbMessage, Session as DbSession, SessionLocal
from src.endpoint_resolver import resolve_endpoint_by_id
from src.llm_core import llm_call_async

from .contracts import ContractError, SemanticCheckpointProposalV1
from .semantic_proposals import bounded_source, derivation_messages, parse_semantic_proposal
from .store import ContinuityStore, SemanticProposalRecord


class SemanticDerivationError(RuntimeError):
    """A safe, stable reason a proposal could not be derived."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SemanticDerivationResult:
    record: SemanticProposalRecord
    created: bool


async def derive_semantic_proposal(
    *, owner: str, session_id: str, only_if_absent: bool = False, workload: str = "foreground",
) -> SemanticDerivationResult:
    """Derive a bounded no-tools proposal from the server-owned session route.

    Neither callers nor the model can select an endpoint, source transcript,
    scope, or storage target. ``workload`` is server-selected: the explicit
    Context action is foreground while the scheduler is background.
    ``only_if_absent`` is for the background path:
    it makes an active owner-review item win over a late queued derivation.
    """
    if workload not in {"foreground", "background"}:
        raise ValueError("unsupported semantic proposal workload")
    db = SessionLocal()
    try:
        session = db.query(DbSession).filter(
            DbSession.id == session_id, DbSession.owner == owner,
        ).first()
        if not session:
            raise SemanticDerivationError("session_not_found", "Companion session was not found")
        scope_kind = session.scope_kind or "general"
        if scope_kind not in {"personal", "project"}:
            raise SemanticDerivationError("scope_denied", "Semantic proposals are available only for Personal and project homes")
        endpoint_id = str(session.endpoint_id or "")
        model = str(session.model or "")
        if not endpoint_id or not model:
            raise SemanticDerivationError("model_unavailable", "Select a registered model before creating a semantic proposal")
        if only_if_absent:
            active = ContinuityStore().latest_semantic_proposal_record(owner=owner, session_id=session_id)
            if active:
                return SemanticDerivationResult(active, False)
        rows = db.query(DbMessage).filter(DbMessage.session_id == session_id).order_by(
            DbMessage.timestamp.asc(), DbMessage.id.asc(),
        ).all()
        source = bounded_source([
            {"id": row.id, "role": row.role, "content": row.content}
            for row in rows
        ])
        source_ids = [item["id"] for item in source]
        source_hash = ContinuityStore._source_message_hash(db, session_id=session_id, source_ids=source_ids)
        if not source_hash:
            raise SemanticDerivationError("source_stale", "The selected source messages are no longer available")
        project_id = session.project_id
    except ContractError as exc:
        raise SemanticDerivationError(
            "source_unavailable", "This conversation does not contain a bounded source span for a semantic proposal",
        ) from exc
    finally:
        db.close()

    route = resolve_endpoint_by_id(endpoint_id, model=model, owner=owner, require_exact_model=True)
    if not route:
        raise SemanticDerivationError(
            "model_unavailable", "The selected model route is unavailable; choose a registered model and try again",
        )
    url, resolved_model, headers = route
    try:
        response = await llm_call_async(
            url, resolved_model, derivation_messages(source), temperature=0, max_tokens=3_000,
            headers=headers, timeout=90, max_retries=0, session_id=session_id,
            workload=workload,
        )
        proposal: SemanticCheckpointProposalV1 = parse_semantic_proposal(
            response, session_id=session_id, scope_kind=scope_kind, project_id=project_id,
            source_message_ids=source_ids, source_hash=source_hash, derivation_model=resolved_model,
        )
    except ContractError as exc:
        raise SemanticDerivationError(
            "proposal_invalid", "The selected model did not return a valid semantic proposal. No memory was changed.",
        ) from exc
    except Exception as exc:
        raise SemanticDerivationError(
            "provider_failed", "The selected model could not create a semantic proposal. No memory was changed.",
        ) from exc

    try:
        if only_if_absent:
            record, created = ContinuityStore().write_semantic_proposal_if_absent(owner=owner, proposal=proposal)
            return SemanticDerivationResult(record, created)
        write = ContinuityStore().write_semantic_proposal(owner=owner, proposal=proposal)
        return SemanticDerivationResult(
            SemanticProposalRecord(write.id, write.revision, "active", proposal), write.created,
        )
    except Exception as exc:
        raise SemanticDerivationError(
            "storage_failed", "The semantic proposal could not be stored. No memory was changed.",
        ) from exc
