from pathlib import Path
from types import SimpleNamespace

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
