"""Narrow G1 seam joining continuity, ModelBridge, and read-only Qwen."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.models import ChatMessage
from src.continuity.compiler import CheckpointCompactor, ContextCompiler
from src.continuity.contracts import ContextBundle
from src.continuity.store import ContinuityStore, ScopeConflictError
from src.model_bridge import ModelBridge, ModelBridgeRuntime
from src.protected_workspace import snapshot_workspace
from src.qwen_supervisor import QwenSupervisor


def render_context_bundle(bundle: ContextBundle) -> str:
    """Render sections in the product-contract order, with explicit labels."""
    parts = [
        "# Companion personality and policy\n" + (bundle.companion_profile or "Odysseus read-only project companion."),
        "# Conversation home\n" + json.dumps({
            "scope_kind": bundle.scope.scope_kind,
            "project_id": bundle.scope.project_id,
            "workspace_root": "/workspace" if bundle.scope.workspace_root else None,
        }, sort_keys=True),
    ]
    if bundle.thread_checkpoint:
        parts.append("# This thread checkpoint\n" + json.dumps(bundle.thread_checkpoint.to_payload(), sort_keys=True))
    if bundle.primary_project_brief:
        parts.append("# Home project shared brief\n" + json.dumps(bundle.primary_project_brief.to_payload(), sort_keys=True))
    for brief in bundle.related_project_briefs:
        parts.append("# Explicit direct-related project brief\n" + json.dumps(brief.to_payload(), sort_keys=True))
    if bundle.episodic_hits:
        parts.append("# Scope-verified episodic hits\n" + json.dumps(bundle.episodic_hits, sort_keys=True))
    parts.append("# Recent raw transcript tail\n" + json.dumps(bundle.transcript_tail, sort_keys=True))
    parts.append("# Current request\n" + bundle.request)
    parts.append("Use only read/search tools. Do not modify files, run shell commands, or invent missing evidence.")
    return "\n\n".join(parts)


@dataclass(frozen=True)
class ScopedTurnResult:
    answer: str
    manifest: dict
    dust_unchanged: bool


class ReadOnlyScopedTurnService:
    def __init__(
        self, session_manager, *, qwen_binary: Path,
        store: Optional[ContinuityStore] = None,
        bridge_factory: Callable[[], ModelBridge] = ModelBridge,
        supervisor_factory: Callable[..., QwenSupervisor] = QwenSupervisor,
    ):
        self.session_manager = session_manager
        self.qwen_binary = qwen_binary
        self.store = store or ContinuityStore()
        self.bridge_factory = bridge_factory
        self.supervisor_factory = supervisor_factory

    async def run(
        self, *, owner: str, session_id: str, request: str,
        endpoint_id: str, model: str, companion_profile: str = "",
    ) -> ScopedTurnResult:
        session = self.session_manager.get_session(session_id)
        if getattr(session, "owner", None) != owner:
            raise ScopeConflictError("session is not owned by the caller")
        scope = self.store.resolve_scope(owner=owner, session_id=session_id)
        if scope.scope_kind != "project" or not scope.workspace_root:
            raise ScopeConflictError("Qwen G1 turns require a project-scoped session")

        # Persist the admitted user turn first. The context tail intentionally
        # excludes it because ContextBundle carries the current request last.
        self.session_manager.add_message(session_id, ChatMessage("user", request))
        transcript_before_request = list(session.history[:-1])
        bundle = ContextCompiler(self.store).compile(
            owner=owner, session_id=session_id, request=request,
            transcript=transcript_before_request, companion_profile=companion_profile,
        )
        prompt = render_context_bundle(bundle)
        workspace = Path(scope.workspace_root)
        before = snapshot_workspace(workspace)

        bridge = self.bridge_factory()
        route = bridge.issue_route(
            owner=owner, endpoint_id=endpoint_id, model=model,
            run_id=session_id, ttl_seconds=600, request_budget=40,
        )
        bridge_runtime = ModelBridgeRuntime(bridge)
        supervisor = self.supervisor_factory(binary=self.qwen_binary)
        try:
            bridge_url = await bridge_runtime.start()
            await supervisor.start(
                workspace_root=workspace, bridge_url=bridge_url,
                bridge_model=model, bridge_token=route.token,
            )
            answer, _metadata = await supervisor.run_prompt(prompt)
        finally:
            await supervisor.stop()
            await bridge_runtime.stop()

        after = snapshot_workspace(workspace)
        if after != before:
            raise RuntimeError("protected project changed during Qwen turn")
        self.session_manager.add_message(session_id, ChatMessage("assistant", answer))
        CheckpointCompactor(self.store).checkpoint(
            owner=owner, session_id=session_id, messages=session.history,
        )
        return ScopedTurnResult(answer, bundle.manifest, True)
