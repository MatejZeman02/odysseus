from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import ChatMessage, Session
from src.continuity.contracts import ContextBundle, ProjectBriefV1, ResolvedScope, ThreadCheckpointV1
from src.scoped_turn_service import ReadOnlyScopedTurnService, render_context_bundle
import src.scoped_turn_service as service_module


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

        async def run_prompt(self, prompt):
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
async def test_cancelled_scoped_turn_keeps_user_message_and_tears_down(monkeypatch, tmp_path):
    import asyncio

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

        async def run_prompt(self, prompt):
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
