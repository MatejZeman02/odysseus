"""G2C APIs for scoped Companion memory and working artifacts."""
from __future__ import annotations

import hashlib
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from core.database import ChatMessage as DbMessage, Project, Session as DbSession, SessionLocal
from routes.g1_continuity_routes import _owner
from src.companion_memory import ArtifactConflict, CompanionMemoryStore, MemoryScopeError
from src.continuity.contracts import ContractError, PersonalBriefV1
from src.continuity.semantic_proposals import bounded_source, derivation_messages, parse_semantic_proposal
from src.continuity.store import ContinuityStore, NotFoundError, ScopeConflictError
from src.endpoint_resolver import resolve_endpoint_by_id
from src.llm_core import llm_call_async


logger = logging.getLogger(__name__)


class PersonalArtifactWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=512 * 1024)
    expected_revision: int | None = Field(default=None, ge=1)
    source_message_id: str | None = Field(default=None, max_length=128)


class LongPasteCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=3000, max_length=512 * 1024)


class ArtifactUndo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class ArtifactDocumentOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personal_session_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=1000)
    artifact_paths: list[str] = Field(default_factory=list, max_length=20)
    request_message_id: str | None = Field(default=None, max_length=128)


class GrantDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow: bool


class PersonalBriefWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=4000)
    preferences: list[str] = Field(default_factory=list, max_length=30)
    ongoing_goals: list[str] = Field(default_factory=list, max_length=30)
    commitments: list[str] = Field(default_factory=list, max_length=30)
    recurring_themes: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=30)
    artifact_refs: list[str] = Field(default_factory=list, max_length=30)


class SemanticProposalPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    selections: dict[str, list[int]] = Field(min_length=1, max_length=8)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ArtifactConflict):
        return HTTPException(409, str(exc))
    return HTTPException(400, str(exc))


def _scope(owner: str, session_id: str) -> tuple[str, str | None]:
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
        if not row:
            raise HTTPException(404, "Companion session was not found")
        return row.scope_kind or "general", row.project_id
    finally:
        db.close()


def setup_companion_memory_routes() -> APIRouter:
    router = APIRouter(prefix="/api/companion", tags=["companion-memory"])
    memory = CompanionMemoryStore()

    @router.get("/memory/sessions/{session_id}")
    def memory_context(session_id: str, request: Request):
        try:
            owner = _owner(request); scope_kind, project_id = _scope(owner, session_id)
            if scope_kind not in {"personal", "project", "computer"}:
                raise HTTPException(409, "Memory context is available only for Companion homes")
            store = ContinuityStore()
            checkpoint = store.latest_thread_checkpoint(owner=owner, session_id=session_id)
            project_brief = store.latest_project_brief(owner=owner, project_id=project_id) if project_id else None
            personal_brief = store.latest_personal_brief(owner=owner, session_id=session_id) if scope_kind == "personal" else None
            proposal = store.latest_semantic_proposal_record(owner=owner, session_id=session_id)
            return {
                "scope_kind": scope_kind, "project_id": project_id,
                "thread_checkpoint": checkpoint.to_payload() if checkpoint else None,
                "project_brief": project_brief.to_payload() if project_brief else None,
                "personal_brief": personal_brief.to_payload() if personal_brief else None,
                "semantic_proposal": ({
                    "id": proposal.id,
                    "revision": proposal.revision,
                    "status": proposal.status,
                    "proposal": proposal.proposal.to_payload(),
                } if proposal else None),
                "artifacts": memory.list_artifacts(owner=owner, scope_kind=scope_kind, project_id=project_id),
                "grants": memory.approved_grants(owner=owner, personal_session_id=session_id) if scope_kind == "personal" else [],
                "pending_grants": memory.pending_grants(owner=owner, personal_session_id=session_id) if scope_kind == "personal" else [],
                "project_catalog": store.project_catalog(owner=owner) if scope_kind == "personal" else [],
            }
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Companion memory storage failed for session %s", session_id)
            raise HTTPException(
                503,
                "Companion memory storage needs a one-time update. Restart Odysseus, then try again.",
            ) from exc
        except Exception as exc:
            logger.exception("Companion memory could not load for session %s", session_id)
            raise HTTPException(
                500,
                "Companion memory could not be loaded. Check the server log for details.",
            ) from exc

    @router.post("/memory/sessions/{session_id}/semantic-proposals")
    async def create_semantic_proposal(session_id: str, request: Request):
        """Derive one bounded, tool-free proposal from an owner session."""
        owner = _owner(request)
        db = SessionLocal()
        try:
            session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
            if not session:
                raise HTTPException(404, "Companion session was not found")
            scope_kind = session.scope_kind or "general"
            if scope_kind not in {"personal", "project"}:
                raise HTTPException(409, "Semantic proposals are available only for Personal and project homes")
            endpoint_id = str(session.endpoint_id or "")
            model = str(session.model or "")
            if not endpoint_id or not model:
                raise HTTPException(409, "Select a registered model before creating a semantic proposal")
            rows = db.query(DbMessage).filter(DbMessage.session_id == session_id).order_by(
                DbMessage.timestamp.asc(), DbMessage.id.asc()
            ).all()
            source = bounded_source([
                {"id": row.id, "role": row.role, "content": row.content}
                for row in rows
            ])
            source_ids = [item["id"] for item in source]
            source_hash = ContinuityStore._source_message_hash(db, session_id=session_id, source_ids=source_ids)
            if not source_hash:
                raise HTTPException(409, "The selected source messages are no longer available")
            project_id = session.project_id
        except ContractError as exc:
            raise HTTPException(422, "This conversation does not contain a bounded source span for a semantic proposal") from exc
        finally:
            db.close()

        route = resolve_endpoint_by_id(endpoint_id, model=model, owner=owner, require_exact_model=True)
        if not route:
            raise HTTPException(409, "The selected model route is unavailable; choose a registered model and try again")
        url, resolved_model, headers = route
        try:
            response = await llm_call_async(
                url, resolved_model, derivation_messages(source), temperature=0, max_tokens=3_000,
                headers=headers, timeout=90, max_retries=0, session_id=session_id,
                workload="foreground",
            )
            proposal = parse_semantic_proposal(
                response, session_id=session_id, scope_kind=scope_kind, project_id=project_id,
                source_message_ids=source_ids, source_hash=source_hash, derivation_model=resolved_model,
            )
            result = ContinuityStore().write_semantic_proposal(owner=owner, proposal=proposal)
        except ContractError as exc:
            logger.info("Semantic proposal validation failed for session %s: %s", session_id, exc)
            raise HTTPException(422, "The selected model did not return a valid semantic proposal. No memory was changed.") from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Semantic proposal derivation failed for session %s", session_id)
            raise HTTPException(502, "The selected model could not create a semantic proposal. No memory was changed.") from exc
        return {"id": result.id, "revision": result.revision, "status": "proposed", "proposal": proposal.to_payload()}

    @router.get("/memory/semantic-proposals/{proposal_id}")
    def read_semantic_proposal(proposal_id: str, request: Request):
        try:
            record = ContinuityStore().semantic_proposal(owner=_owner(request), proposal_id=proposal_id)
            return {"id": record.id, "revision": record.revision, "status": record.status, "proposal": record.proposal.to_payload()}
        except NotFoundError as exc:
            raise HTTPException(404, "Semantic proposal was not found") from exc

    @router.post("/memory/semantic-proposals/{proposal_id}/promote")
    def promote_semantic_proposal(proposal_id: str, payload: SemanticProposalPromotion, request: Request):
        try:
            record, write, brief = ContinuityStore().promote_semantic_proposal(
                owner=_owner(request), proposal_id=proposal_id,
                expected_revision=payload.expected_revision, selections=payload.selections,
            )
            return {
                "proposal": {"id": record.id, "revision": record.revision, "status": record.status},
                "brief": brief.to_payload(),
                "brief_revision": write.revision,
            }
        except NotFoundError as exc:
            raise HTTPException(404, "Semantic proposal was not found") from exc
        except (ContractError, ValueError) as exc:
            raise HTTPException(422, "The selected proposal entries are invalid") from exc
        except ScopeConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            logger.exception("Semantic proposal promotion failed for %s", proposal_id)
            raise HTTPException(500, "The semantic proposal could not be promoted. No memory was changed.") from exc

    @router.post("/artefacts/personal", include_in_schema=False)
    @router.post("/artifacts/personal")
    def write_personal_artifact(payload: PersonalArtifactWrite, request: Request):
        try:
            return memory.write_personal_artifact(owner=_owner(request), **payload.model_dump())
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Companion artifact storage failed for session %s", payload.session_id)
            raise HTTPException(
                503,
                "Artifact storage needs a one-time update. Restart Odysseus, then try again.",
            ) from exc

    @router.post("/artifacts/computer")
    def write_computer_artifact(payload: PersonalArtifactWrite, request: Request):
        try:
            return memory.write_computer_artifact(owner=_owner(request), **payload.model_dump())
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Computer artifact storage failed for session %s", payload.session_id)
            raise HTTPException(
                503,
                "Computer artifact storage needs a one-time update. Restart Odysseus, then try again.",
            ) from exc

    @router.post("/artifacts/capture-paste")
    def capture_long_paste(payload: LongPasteCapture, request: Request):
        try:
            return memory.capture_long_paste(owner=_owner(request), **payload.model_dump())
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Long paste capture failed for session %s", payload.session_id)
            raise HTTPException(503, "The pasted text could not be stored. It remains in the composer.") from exc

    @router.get("/artefacts/personal/{artifact_id}", include_in_schema=False)
    @router.get("/artifacts/personal/{artifact_id}")
    def read_personal_artifact(artifact_id: str, request: Request):
        try:
            return memory.get_personal_artifact(owner=_owner(request), artifact_id=artifact_id)
        except MemoryScopeError as exc:
            raise _error(exc) from exc

    @router.get("/artifacts/computer/{artifact_id}")
    def read_computer_artifact(artifact_id: str, request: Request):
        try:
            return memory.get_computer_artifact(owner=_owner(request), artifact_id=artifact_id)
        except MemoryScopeError as exc:
            raise _error(exc) from exc

    @router.post("/artifacts/personal/{artifact_id}/document")
    def open_personal_artifact_document(artifact_id: str, payload: ArtifactDocumentOpen, request: Request):
        try:
            return memory.open_personal_artifact_document(
                owner=_owner(request), session_id=payload.session_id, artifact_id=artifact_id,
            )
        except MemoryScopeError as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Companion artifact document bridge failed for %s", artifact_id)
            raise HTTPException(503, "Artifact editor is temporarily unavailable. Restart Odysseus, then try again.") from exc

    @router.post("/artifacts/computer/{artifact_id}/document")
    def open_computer_artifact_document(artifact_id: str, payload: ArtifactDocumentOpen, request: Request):
        try:
            return memory.open_computer_artifact_document(
                owner=_owner(request), session_id=payload.session_id, artifact_id=artifact_id,
            )
        except MemoryScopeError as exc:
            raise _error(exc) from exc
        except SQLAlchemyError as exc:
            logger.exception("Computer artifact document bridge failed for %s", artifact_id)
            raise HTTPException(503, "Artifact editor is temporarily unavailable. Restart Odysseus, then try again.") from exc

    @router.post("/artefacts/personal/{artifact_id}/undo", include_in_schema=False)
    @router.post("/artifacts/personal/{artifact_id}/undo")
    def undo_personal_artifact(artifact_id: str, payload: ArtifactUndo, request: Request):
        try:
            return memory.undo_personal_artifact(owner=_owner(request), artifact_id=artifact_id,
                                                  expected_revision=payload.expected_revision)
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc

    @router.post("/artefacts/personal/{artifact_id}/delete", include_in_schema=False)
    @router.post("/artifacts/personal/{artifact_id}/delete")
    def delete_personal_artifact(artifact_id: str, payload: ArtifactUndo, request: Request):
        try:
            return memory.delete_personal_artifact(owner=_owner(request), artifact_id=artifact_id,
                                                   expected_revision=payload.expected_revision)
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc

    @router.post("/artefacts/personal/{artifact_id}/restore", include_in_schema=False)
    @router.post("/artifacts/personal/{artifact_id}/restore")
    def restore_personal_artifact(artifact_id: str, payload: ArtifactUndo, request: Request):
        try:
            return memory.restore_personal_artifact(owner=_owner(request), artifact_id=artifact_id,
                                                    expected_revision=payload.expected_revision)
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc

    @router.post("/context-grants")
    def create_grant(payload: GrantCreate, request: Request):
        try:
            return memory.create_grant_request(owner=_owner(request), **payload.model_dump())
        except MemoryScopeError as exc:
            raise _error(exc) from exc

    @router.post("/context-grants/{grant_id}/decision")
    def decide_grant(grant_id: str, payload: GrantDecision, request: Request):
        try:
            return memory.decide_grant(owner=_owner(request), grant_id=grant_id, allow=payload.allow)
        except (MemoryScopeError, ArtifactConflict) as exc:
            raise _error(exc) from exc

    @router.post("/personal-brief")
    def write_personal_brief(payload: PersonalBriefWrite, request: Request):
        owner = _owner(request)
        scope_kind, _project_id = _scope(owner, payload.session_id)
        if scope_kind != "personal":
            raise HTTPException(409, "Personal brief requires a Personal Advisor session")
        values = payload.model_dump()
        session_id = values.pop("session_id")
        brief = PersonalBriefV1(
            owner_id=owner,
            derivation_status="accepted",
            derivation_version=1,
            derivation_method="owner_edit_v1",
            **values,
        )
        source_hash = hashlib.sha256(json.dumps(brief.to_payload(), sort_keys=True).encode()).hexdigest()
        try:
            result = ContinuityStore().write_personal_brief(owner=owner, session_id=session_id, brief=brief, source_hash=source_hash)
        except Exception as exc:
            raise _error(exc) from exc
        return {"revision": result.revision, "brief": brief.to_payload()}

    return router
