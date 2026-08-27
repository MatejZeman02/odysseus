"""G2D-1 Computer Help routes: safe host observations, not host execution."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.database import Session as DbSession, SessionLocal
from core.models import ChatMessage
from routes.g1_continuity_routes import _owner
from src.computer_observe import ObservationError, collect_observations
from src.computer_sandbox import SandboxQualificationError, qualify_containment, readiness
from src.companion_memory import CompanionMemoryStore, MemoryScopeError


class ObserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    categories: list[str] = Field(default_factory=list, max_length=5)


def _computer_session(owner: str, session_id: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
        if not row:
            raise HTTPException(404, "Computer Help session was not found")
        if row.scope_kind != "computer":
            raise HTTPException(409, "Computer diagnostics are available only in Computer Help")
    finally:
        db.close()


def _observation_message(observations: list[dict]) -> tuple[str, dict]:
    lines = ["**Computer diagnostic snapshot**", ""]
    events = []
    for item in observations:
        summary = str(item.get("summary") or "Diagnostic completed")
        source = str((item.get("process") or {}).get("operation") or "Inspect: computer")
        events.append({"kind": "tool", "tool": "inspect", "command": source, "status": "completed"})
        lines.append(f"- **{summary}**")
        lines.extend(f"  - {str(fact)}" for fact in list(item.get("facts") or [])[:8])
    lines.extend(["", "No system settings were changed."])
    return "\n".join(lines), {"events": events, "outcome": "worked", "elapsed_seconds": 0}


def _device_profile_markdown(observations: list[dict]) -> str:
    """Render only server-sanitized, verified observation facts.

    This is a durable starting point for later incidents; it deliberately
    excludes raw logs, model conclusions, credentials, and transient output.
    """
    lines = [
        "# Device profile", "",
        "Verified, non-secret facts collected by Computer Help.",
        "",
    ]
    for item in observations:
        category = str(item.get("category") or "system").replace("_", " ").title()
        lines.extend([f"## {category}", ""])
        lines.extend(f"- {str(fact)}" for fact in list(item.get("facts") or [])[:8])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def setup_computer_help_routes(session_manager) -> APIRouter:
    router = APIRouter(prefix="/api/companion/computer", tags=["computer-help"])

    @router.get("/status")
    def status(request: Request):
        _owner(request)
        sandbox = readiness()
        return {
            "computer_observe_ready": True,
            "computer_assist_ready": sandbox.qualified,
            "sandbox": sandbox.public_payload(),
            "allowed_categories": ["system", "hardware", "storage", "graphics", "network"],
        }

    @router.post("/observe")
    def observe(payload: ObserveRequest, request: Request):
        owner = _owner(request)
        _computer_session(owner, payload.session_id)
        started = time.monotonic()
        try:
            observations = collect_observations(payload.categories or None)
        except ObservationError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            CompanionMemoryStore().write_computer_artifact(
                owner=owner,
                session_id=payload.session_id,
                path="computer/device-profile.md",
                content=_device_profile_markdown(observations),
            )
        except MemoryScopeError as exc:
            raise HTTPException(409, "The verified device profile could not be updated") from exc
        content, process = _observation_message(observations)
        process["elapsed_seconds"] = max(0, round(time.monotonic() - started, 2))
        message = ChatMessage("assistant", content, metadata={
            "computer_process": process,
            "computer_profile": "computer_observe",
            "model": "Computer Help",
        })
        session_manager.add_message(payload.session_id, message)
        return {
            "profile": "computer_observe",
            "observations": observations,
            "summary": "Read-only computer diagnostics completed. No system settings were changed.",
            "message": {"role": "assistant", "content": content, "metadata": message.metadata},
        }

    @router.post("/sandbox/qualify")
    def qualify(request: Request):
        """Run only the fixed G2D-0 fixture; no browser input reaches Podman."""
        _owner(request)
        try:
            result = qualify_containment()
        except SandboxQualificationError as exc:
            raise HTTPException(409, str(exc)) from exc
        return result

    return router
