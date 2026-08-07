"""Narrow G1 seam joining continuity, ModelBridge, and read-only Qwen."""

from __future__ import annotations

import asyncio
import json
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.models import ChatMessage
from src.continuity.compiler import CheckpointCompactor, ContextCompiler
from src.continuity.contracts import ContextBundle
from src.continuity.store import ContinuityStore, ScopeConflictError
from src.model_bridge import ModelBridge, ModelBridgeRuntime
from src.protected_workspace import snapshot_workspace
from src.project_patches import PatchError, PreparedProposal, prepare_proposal, proposal_instructions
from src.qwen_supervisor import QwenSupervisor


QWEN_TURN_ERROR_CODES = frozenset({
    "sandbox_unavailable",
    "command_denied",
    "command_timeout",
    "command_output_limited",
    "command_resource_limit",
    "provider_failed",
    "worker_died",
    "turn_timeout",
    "workspace_changed",
    "teardown_failed",
    "cancelled",
    "proposal_invalid",
    "proposal_too_large",
    "path_denied",
    "unsupported_file",
})


def classify_turn_failure(exc: BaseException) -> str:
    if isinstance(exc, PatchError):
        return exc.code
    text = str(exc).lower()
    for stable_code in (
        "command_denied",
        "command_timeout",
        "command_output_limited",
        "command_resource_limit",
    ):
        if stable_code in text:
            return stable_code
    if isinstance(exc, TimeoutError) or "prompt_deadline_exceeded" in text:
        return "turn_timeout"
    if "protected project changed" in text:
        return "workspace_changed"
    if "teardown" in text:
        return "teardown_failed"
    if "sandbox" in text and "unavailable" in text:
        return "sandbox_unavailable"
    if "provider" in text or "modelbridge" in text:
        return "provider_failed"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "worker_died"


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
    if bundle.scope.scope_kind == "project" and bundle.scope.workspace_root:
        parts.append(
            "# Project workspace access\n"
            "The checkout for this conversation is already mounted at /workspace. "
            "Treat references such as 'this project', 'the repo', or the project name as /workspace. "
            "For questions about what the project contains or does, inspect README files and relevant "
            "source/docs with the available read/search tools before answering. Never ask the user to "
            "set a workspace or provide the project path."
        )
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
    parts.append(
        "# User-facing progress updates\n"
        "Before the first meaningful read/search batch, briefly tell the user what you will inspect and why. "
        "During longer work, add another brief update only after a material finding, a change of approach, or an error. "
        "Say what you learned and what comes next in one or two sentences. Do not narrate every tool call and do not "
        "reveal private chain-of-thought."
    )
    parts.append(
        "Use only read/search tools. For discussion follow-ups, use the supplied conversation and project context first, "
        "and inspect files only when the answer needs additional evidence. Once you have enough evidence, give the final "
        "answer and end the turn; do not invoke another tool after drafting the final answer. Do not modify files, run "
        "shell commands, or invent missing evidence."
    )
    return "\n\n".join(parts)


@dataclass(frozen=True)
class ScopedTurnResult:
    answer: str
    manifest: dict
    dust_unchanged: bool
    message_id: str = ""
    user_message_id: str = ""
    qwen_process: dict | None = None
    proposal: PreparedProposal | None = None


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
        capability_profile: str = "project_read",
        proposal_mode: bool = False,
        progress_callback: Optional[Callable[[dict], object]] = None,
    ) -> ScopedTurnResult:
        if capability_profile != "project_read":
            raise RuntimeError("sandboxed project inspection is unavailable")
        session = self.session_manager.get_session(session_id)
        if getattr(session, "owner", None) != owner:
            raise ScopeConflictError("session is not owned by the caller")
        scope = self.store.resolve_scope(owner=owner, session_id=session_id)
        if scope.scope_kind != "project" or not scope.workspace_root:
            raise ScopeConflictError("Qwen G1 turns require a project-scoped session")

        # Persist the admitted user turn first. The context tail intentionally
        # excludes it because ContextBundle carries the current request last.
        user_message = ChatMessage("user", request)
        self.session_manager.add_message(session_id, user_message)
        transcript_before_request = list(session.history[:-1])
        bundle = ContextCompiler(self.store).compile(
            owner=owner, session_id=session_id, request=request,
            transcript=transcript_before_request, companion_profile=companion_profile,
        )
        prompt = render_context_bundle(bundle)
        if proposal_mode:
            prompt = f"{prompt}\n\n{proposal_instructions()}"
        workspace = Path(scope.workspace_root)
        before = snapshot_workspace(workspace)

        bridge = self.bridge_factory()
        route = bridge.issue_route(
            owner=owner, endpoint_id=endpoint_id, model=model,
            run_id=session_id, ttl_seconds=600, request_budget=40,
        )
        bridge_runtime = ModelBridgeRuntime(bridge)
        supervisor = self.supervisor_factory(binary=self.qwen_binary)
        answer = ""
        worker_error = None
        turn_started = time.monotonic()
        process_events: list[dict] = []

        async def record_progress(event: dict) -> None:
            """Persist only the already-sanitized, display-safe process trace."""
            if not isinstance(event, dict):
                return
            if event.get("kind") == "commentary":
                text = str(event.get("text", "")).strip()[:800]
                if not text:
                    return
                safe_event = {
                    "kind": "commentary",
                    "text": text,
                    "status": "completed",
                }
                if process_events and process_events[-1].get("kind") == "commentary" and process_events[-1].get("text") == text:
                    return
                process_events.append(safe_event)
                if progress_callback:
                    result = progress_callback(safe_event)
                    if inspect.isawaitable(result):
                        await result
                return
            safe_event = {
                key: str(event.get(key, ""))[:2100]
                for key in ("operation", "tool", "path", "label", "command", "status")
            }
            # Qwen commonly emits a call and a later call-update.  They are
            # one visible operation, not two separate transcript rows.
            identity = tuple(safe_event[key] for key in ("operation", "tool", "path", "command"))
            for prior in reversed(process_events):
                if prior.get("kind") == "commentary":
                    continue
                prior_identity = tuple(prior[key] for key in ("operation", "tool", "path", "command"))
                if prior_identity == identity:
                    prior["status"] = safe_event["status"]
                    break
            else:
                process_events.append(safe_event)
            if progress_callback:
                result = progress_callback(safe_event)
                if inspect.isawaitable(result):
                    await result
        try:
            bridge_url = await bridge_runtime.start()
            await supervisor.start(
                workspace_root=workspace, bridge_url=bridge_url,
                bridge_model=model, bridge_token=route.token,
            )
            answer, _metadata = await supervisor.run_prompt(prompt, progress_callback=record_progress)
            prepared_proposal = prepare_proposal(workspace, answer) if proposal_mode else None
            if prepared_proposal is not None:
                answer = prepared_proposal.answer
        except BaseException as exc:
            worker_error = exc
            prepared_proposal = None
        finally:
            teardown_error = None
            try:
                await supervisor.stop()
            except BaseException as exc:
                teardown_error = exc
            try:
                await bridge_runtime.stop()
            except BaseException as exc:
                teardown_error = teardown_error or exc
            # Check even on cancellation/failure: a failure must never hide a
            # workspace mutation behind the original worker exception.
            after = snapshot_workspace(workspace)
            workspace_unchanged = after == before
            if not workspace_unchanged:
                worker_error = RuntimeError("protected project changed during Qwen turn")
            elif teardown_error is not None and worker_error is None:
                worker_error = RuntimeError("Qwen teardown failed")
        qwen_process = {
            "elapsed_seconds": max(0, round(time.monotonic() - turn_started)),
            "events": process_events,
            "capability_profile": capability_profile,
            "workspace_unchanged": workspace_unchanged,
        }
        if worker_error is not None:
            failure_code = classify_turn_failure(worker_error)
            qwen_process.update({"outcome": "failed", "failure_code": failure_code})
            if user_message.metadata is None:
                user_message.metadata = {}
            user_message.metadata.update({
                "harness": "qwen",
                "capability_profile": capability_profile,
                "qwen_process": qwen_process,
                "qwen_error": {"code": failure_code},
            })
            message_id = user_message.metadata.get("_db_id", "")
            if message_id and hasattr(self.session_manager, "update_message_metadata"):
                self.session_manager.update_message_metadata(session_id, message_id, user_message.metadata)
            raise worker_error
        qwen_process["outcome"] = "worked"
        assistant_message = ChatMessage("assistant", answer, metadata={
            "harness": "qwen", "qwen_read_only": True,
            "capability_profile": capability_profile,
            "workspace_unchanged": True, "context_manifest": bundle.manifest,
            "model": model,
            "qwen_process": qwen_process,
        })
        self.session_manager.add_message(session_id, assistant_message)
        CheckpointCompactor(self.store).checkpoint(
            owner=owner, session_id=session_id, messages=session.history,
        )
        return ScopedTurnResult(
            answer, bundle.manifest, True,
            assistant_message.metadata.get("_db_id", ""),
            user_message.metadata.get("_db_id", ""),
            qwen_process,
            prepared_proposal,
        )
