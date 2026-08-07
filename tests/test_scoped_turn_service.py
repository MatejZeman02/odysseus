from pathlib import Path
from types import SimpleNamespace
import json
import subprocess

import pytest

from core.models import ChatMessage, Session
from src.continuity.contracts import ContextBundle, ProjectBriefV1, ResolvedScope, ThreadCheckpointV1
from src.scoped_turn_service import (
    QWEN_TURN_ERROR_CODES,
    ReadOnlyScopedTurnService,
    classify_turn_failure,
    render_context_bundle,
)
import src.scoped_turn_service as service_module
from src.project_patches import PATCH_END, PATCH_START, PatchError


def test_g2a_failure_codes_are_stable_even_while_inspection_is_unavailable():
    assert {
        "sandbox_unavailable", "command_denied", "command_timeout",
        "command_output_limited", "command_resource_limit", "provider_failed",
        "worker_died", "turn_timeout", "workspace_changed", "teardown_failed",
    }.issubset(QWEN_TURN_ERROR_CODES)
    assert classify_turn_failure(RuntimeError("command_output_limited: private output")) == "command_output_limited"


def test_context_bundle_render_order_is_stable():
    scope = ResolvedScope("alice", "s", "project", "p", "/project")
    checkpoint = ThreadCheckpointV1(session_id="s", project_id="p", source_hash="h")
    brief = ProjectBriefV1(project_id="p", summary="brief")
    bundle = ContextBundle("profile", scope, "request", checkpoint, brief, transcript_tail=({"role": "user", "content": "tail"},))
    rendered = render_context_bundle(bundle)
    headings = [
        "# Companion personality and policy", "# Conversation home",
        "# This thread checkpoint", "# Home project shared brief",
        "# Recent raw transcript tail", "# Current request",
    ]
    assert [rendered.index(heading) for heading in headings] == sorted(rendered.index(heading) for heading in headings)
    assert "use the supplied conversation and project context first" in rendered
    assert "do not invoke another tool after drafting the final answer" in rendered


@pytest.mark.asyncio
async def test_scoped_turn_persists_normal_messages_and_tears_down(monkeypatch, tmp_path):
    session = Session("s", "chat", "http://x", "m", owner="alice", scope_kind="project", project_id="p")

    class Manager:
        def get_session(self, session_id):
            return session

        def add_message(self, session_id, message):
            message.metadata = {"_db_id": f"m{len(session.history) + 1}"}
            session.history.append(message)

    class Store:
        def resolve_scope(self, **kwargs):
            return ResolvedScope("alice", "s", "project", "p", str(tmp_path))

        def latest_thread_checkpoint(self, **kwargs):
            return None

        def latest_project_brief(self, **kwargs):
            return None

        def write_thread_checkpoint(self, **kwargs):
            raise AssertionError("short transcript should not compact")

    class Bridge:
        app = object()

        def issue_route(self, **kwargs):
            return SimpleNamespace(token="ephemeral")

    stopped = []

    class BridgeRuntime:
        def __init__(self, bridge):
            pass

        async def start(self):
            return "http://127.0.0.1:1/v1"

        async def stop(self):
            stopped.append("bridge")

    class Supervisor:
        def __init__(self, **kwargs):
            pass

        async def start(self, **kwargs):
            return None

        async def run_prompt(self, prompt, **kwargs):
            assert "# Current request\nquestion" in prompt
            return "answer", {}

        async def stop(self):
            stopped.append("qwen")

    monkeypatch.setattr(service_module, "ModelBridgeRuntime", BridgeRuntime)
    monkeypatch.setattr(service_module, "snapshot_workspace", lambda path: (str(path), "same"))
    service = ReadOnlyScopedTurnService(
        Manager(), qwen_binary=Path("/qwen"), store=Store(),
        bridge_factory=Bridge, supervisor_factory=Supervisor,
    )
    result = await service.run(
        owner="alice", session_id="s", request="question", endpoint_id="e", model="m",
    )
    assert result.answer == "answer"
    assert [(message.role, message.content) for message in session.history] == [
        ("user", "question"), ("assistant", "answer"),
    ]
    assert stopped == ["qwen", "bridge"]


@pytest.mark.asyncio
async def test_patch_turn_extracts_envelope_before_persisting_assistant(monkeypatch, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("# Before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    subprocess.run([
        "git", "-C", str(tmp_path), "-c", "user.name=Test", "-c",
        "user.email=test@example.invalid", "commit", "-qm", "fixture",
    ], check=True)
    session = Session("s", "chat", "http://x", "m", owner="alice", scope_kind="project", project_id="p")

    class Manager:
        def get_session(self, _session_id): return session
        def add_message(self, _session_id, message):
            message.metadata = {**(message.metadata or {}), "_db_id": f"m{len(session.history) + 1}"}
            session.history.append(message)

    class Store:
        def resolve_scope(self, **_kwargs): return ResolvedScope("alice", "s", "project", "p", str(tmp_path))
        def latest_thread_checkpoint(self, **_kwargs): return None
        def latest_project_brief(self, **_kwargs): return None
        def write_thread_checkpoint(self, **_kwargs): pass

    class Bridge:
        app = object()
        def issue_route(self, **_kwargs): return SimpleNamespace(token="ephemeral")

    class BridgeRuntime:
        def __init__(self, _bridge): pass
        async def start(self): return "http://127.0.0.1:1/v1"
        async def stop(self): pass

    envelope = {
        "version": 1, "summary": "Update heading", "rationale": "Clearer wording",
        "changes": [{"operation": "update", "path": "README.md", "content": "# After\n"}],
    }

    class Supervisor:
        def __init__(self, **_kwargs): pass
        async def start(self, **_kwargs): pass
        async def run_prompt(self, prompt, **_kwargs):
            assert "# Reviewed patch proposal" in prompt
            return f"I prepared the change.\n{PATCH_START}\n{json.dumps(envelope)}\n{PATCH_END}", {}
        async def stop(self): pass

    monkeypatch.setattr(service_module, "ModelBridgeRuntime", BridgeRuntime)
    monkeypatch.setattr(service_module, "snapshot_workspace", lambda path: (str(path), "same"))
    result = await ReadOnlyScopedTurnService(
        Manager(), qwen_binary=Path("/qwen"), store=Store(),
        bridge_factory=Bridge, supervisor_factory=Supervisor,
    ).run(owner="alice", session_id="s", request="change it", endpoint_id="e", model="m", proposal_mode=True)

    assert result.answer == "I prepared the change."
    assert result.proposal.summary == "Update heading"
    assert result.proposal.public_files[0]["path"] == "README.md"
    assert PATCH_START not in session.history[-1].content


def test_patch_failures_have_stable_turn_codes():
    exc = PatchError("proposal_invalid", "unsafe internal detail")
    assert classify_turn_failure(exc) == "proposal_invalid"
    assert {"proposal_invalid", "proposal_too_large", "path_denied", "unsupported_file"}.issubset(
        QWEN_TURN_ERROR_CODES
    )


@pytest.mark.asyncio
async def test_scoped_turn_persists_sanitized_qwen_process(monkeypatch, tmp_path):
    session = Session("s", "chat", "http://x", "m", owner="alice", scope_kind="project", project_id="p")

    class Manager:
        def get_session(self, session_id):
            return session

        def add_message(self, session_id, message):
            message.metadata = {**(message.metadata or {}), "_db_id": f"m{len(session.history) + 1}"}
            session.history.append(message)

    class Store:
        def resolve_scope(self, **kwargs):
            return ResolvedScope("alice", "s", "project", "p", str(tmp_path))

        def latest_thread_checkpoint(self, **kwargs): return None
        def latest_project_brief(self, **kwargs): return None
        def write_thread_checkpoint(self, **kwargs): raise AssertionError("short transcript should not compact")

    class Bridge:
        app = object()
        def issue_route(self, **kwargs): return SimpleNamespace(token="ephemeral")

    class BridgeRuntime:
        def __init__(self, bridge): pass
        async def start(self): return "http://127.0.0.1:1/v1"
        async def stop(self): pass

    class Supervisor:
        def __init__(self, **kwargs): pass
        async def start(self, **kwargs): pass
        async def run_prompt(self, prompt, *, progress_callback=None):
            await progress_callback({"kind": "commentary", "text": "I found the project index; next I’ll search it.",
                                     "status": "completed"})
            await progress_callback({"operation": "Search project files", "tool": "search", "path": ".",
                                     "label": "Search: fish", "command": "Search: 'fish' .", "status": "running"})
            await progress_callback({"operation": "Search project files", "tool": "search", "path": ".",
                                     "label": "Search: fish", "command": "Search: 'fish' .", "status": "completed"})
            return "answer", {}
        async def stop(self): pass

    monkeypatch.setattr(service_module, "ModelBridgeRuntime", BridgeRuntime)
    monkeypatch.setattr(service_module, "snapshot_workspace", lambda path: (str(path), "same"))
    service = ReadOnlyScopedTurnService(Manager(), qwen_binary=Path("/qwen"), store=Store(),
                                        bridge_factory=Bridge, supervisor_factory=Supervisor)
    result = await service.run(owner="alice", session_id="s", request="question", endpoint_id="e", model="m")
    assert result.qwen_process["events"] == [
        {"kind": "commentary", "text": "I found the project index; next I’ll search it.",
         "status": "completed"},
        {"operation": "Search project files", "tool": "search", "path": ".", "label": "Search: fish",
         "command": "Search: 'fish' .", "status": "completed"},
    ]
    assert session.history[-1].metadata["qwen_process"] == result.qwen_process


@pytest.mark.asyncio
async def test_cancelled_scoped_turn_keeps_user_message_and_tears_down(monkeypatch, tmp_path):
    import asyncio

    session = Session("s", "chat", "http://x", "m", owner="alice", scope_kind="project", project_id="p")

    persisted_metadata = []

    class Manager:
        def get_session(self, session_id):
            return session

        def add_message(self, session_id, message):
            message.metadata = {"_db_id": f"m{len(session.history) + 1}"}
            session.history.append(message)

        def update_message_metadata(self, session_id, message_id, metadata):
            persisted_metadata.append((session_id, message_id, dict(metadata)))

    class Store:
        def resolve_scope(self, **kwargs):
            return ResolvedScope("alice", "s", "project", "p", str(tmp_path))

        def latest_thread_checkpoint(self, **kwargs):
            return None

        def latest_project_brief(self, **kwargs):
            return None

    class Bridge:
        app = object()

        def issue_route(self, **kwargs):
            return SimpleNamespace(token="ephemeral")

    stopped = []

    class BridgeRuntime:
        def __init__(self, bridge):
            pass

        async def start(self):
            return "http://127.0.0.1:1/v1"

        async def stop(self):
            stopped.append("bridge")

    class Supervisor:
        def __init__(self, **kwargs):
            pass

        async def start(self, **kwargs):
            pass

        async def run_prompt(self, prompt, **kwargs):
            raise asyncio.CancelledError

        async def stop(self):
            stopped.append("qwen")

    monkeypatch.setattr(service_module, "ModelBridgeRuntime", BridgeRuntime)
    monkeypatch.setattr(service_module, "snapshot_workspace", lambda path: (str(path), "same"))
    service = ReadOnlyScopedTurnService(
        Manager(), qwen_binary=Path("/qwen"), store=Store(),
        bridge_factory=Bridge, supervisor_factory=Supervisor,
    )
    with pytest.raises(asyncio.CancelledError):
        await service.run(
            owner="alice", session_id="s", request="question", endpoint_id="e", model="m",
        )
    assert [(message.role, message.content) for message in session.history] == [("user", "question")]
    assert stopped == ["qwen", "bridge"]
    assert persisted_metadata[0][0:2] == ("s", "m1")
    assert persisted_metadata[0][2]["qwen_process"]["outcome"] == "failed"
    assert persisted_metadata[0][2]["qwen_process"]["failure_code"] == "cancelled"
    assert persisted_metadata[0][2]["qwen_process"]["workspace_unchanged"] is True


@pytest.mark.asyncio
async def test_workspace_mutation_is_unsafe_and_never_persists_an_assistant(monkeypatch, tmp_path):
    session = Session("s", "chat", "http://x", "m", owner="alice", scope_kind="project", project_id="p")

    class Manager:
        def get_session(self, _session_id): return session
        def add_message(self, _session_id, message):
            message.metadata = {**(message.metadata or {}), "_db_id": f"m{len(session.history) + 1}"}
            session.history.append(message)

    class Store:
        def resolve_scope(self, **_kwargs): return ResolvedScope("alice", "s", "project", "p", str(tmp_path))
        def latest_thread_checkpoint(self, **_kwargs): return None
        def latest_project_brief(self, **_kwargs): return None

    class Bridge:
        app = object()
        def issue_route(self, **_kwargs): return SimpleNamespace(token="ephemeral")

    stopped = []
    class BridgeRuntime:
        def __init__(self, _bridge): pass
        async def start(self): return "http://127.0.0.1:1/v1"
        async def stop(self): stopped.append("bridge")

    class Supervisor:
        def __init__(self, **_kwargs): pass
        async def start(self, **_kwargs): pass
        async def run_prompt(self, _prompt, **_kwargs): return "unsafe answer", {}
        async def stop(self): stopped.append("qwen")

    snapshots = iter([("before",), ("changed",)])
    monkeypatch.setattr(service_module, "ModelBridgeRuntime", BridgeRuntime)
    monkeypatch.setattr(service_module, "snapshot_workspace", lambda _path: next(snapshots))
    service = ReadOnlyScopedTurnService(
        Manager(), qwen_binary=Path("/qwen"), store=Store(),
        bridge_factory=Bridge, supervisor_factory=Supervisor,
    )

    with pytest.raises(RuntimeError, match="protected project changed"):
        await service.run(owner="alice", session_id="s", request="question", endpoint_id="e", model="m")

    assert [(message.role, message.content) for message in session.history] == [("user", "question")]
    assert stopped == ["qwen", "bridge"]
