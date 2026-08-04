"""Default-off API seam for the G1 read-only project companion."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.auth_helpers import effective_user
from src.continuity.store import ScopeConflictError
from src.scoped_turn_service import ReadOnlyScopedTurnService


class G1TurnRequest(BaseModel):
    session_id: str
    message: str
    endpoint_id: str
    model: str
    companion_profile: str = ""


def _enabled() -> bool:
    return os.getenv("ODYSSEUS_QWEN_HARNESS", "").strip().lower() in {"1", "true", "yes", "on"}


def setup_g1_continuity_routes(session_manager) -> APIRouter:
    router = APIRouter(prefix="/api/g1", tags=["g1-continuity"])

    @router.post("/project-turn")
    async def project_turn(payload: G1TurnRequest, request: Request):
        # Behave as an absent endpoint unless the operator explicitly enables
        # this experimental local harness.
        if not _enabled():
            raise HTTPException(404, "Not found")

        owner = effective_user(request)
        if owner is None:
            raise HTTPException(401, "Authentication required")
        binary_value = os.getenv("ODYSSEUS_QWEN_BINARY", "").strip()
        if not binary_value:
            raise HTTPException(503, "ODYSSEUS_QWEN_BINARY is not configured")
        binary = Path(binary_value)
        if not binary.is_file():
            raise HTTPException(503, "Configured Qwen binary is unavailable")

        service = ReadOnlyScopedTurnService(session_manager, qwen_binary=binary)
        try:
            result = await service.run(
                owner=owner,
                session_id=payload.session_id,
                request=payload.message,
                endpoint_id=payload.endpoint_id,
                model=payload.model,
                companion_profile=payload.companion_profile,
            )
        except ScopeConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "answer": result.answer,
            "context_manifest": result.manifest,
            "workspace_unchanged": result.dust_unchanged,
        }

    return router
