from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Session as DbSession
from src.continuity import ProjectBriefV1
from src.continuity.compiler import CheckpointCompactor, ContextCompiler
from src.continuity.store import ContinuityStore
import src.continuity.store as continuity_store_module


def _store(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    monkeypatch.setattr(continuity_store_module, "SessionLocal", local_session)
    db = local_session()
    db.add(DbSession(id="session", owner="alice", name="chat", endpoint_url="http://x", model="m"))
    db.commit()
    db.close()
    return ContinuityStore()


def _message(role, content, number, **metadata):
    return {"role": role, "content": content, "metadata": {"_db_id": f"m{number}", **metadata}}


def test_checkpoint_preserves_raw_transcript_and_keeps_tool_trace_atomic(monkeypatch):
    store = _store(monkeypatch)
    project_id = store.create_project(owner="alice", name="P", workspace_root="/p")
    store.bind_session(owner="alice", session_id="session", scope_kind="project", project_id=project_id)
    transcript = [
        _message("user", "old request", 1),
        _message("assistant", "old answer", 2),
        _message("assistant", "calling tool", 3, tool_calls=[{"id": "call-1"}]),
        _message("tool", "tool result", 4),
        _message("assistant", "final answer", 5),
    ]
    before = [dict(message) for message in transcript]
    checkpoint = CheckpointCompactor(store, tail_count=2).checkpoint(
        owner="alice", session_id="session", messages=transcript,
        derive=lambda _: {"objective": "keep the previous answer"},
    )

    assert [message["metadata"]["_db_id"] for message in transcript] == ["m1", "m2", "m3", "m4", "m5"]
    assert transcript == before
    assert checkpoint.source_message_ids == ["m1", "m2"]

    bundle = ContextCompiler(store, tail_count=2).compile(
        owner="alice", session_id="session", request="continue", transcript=transcript
    )
    assert [message["metadata"]["_db_id"] for message in bundle.transcript_tail] == ["m3", "m4", "m5"]
    assert bundle.thread_checkpoint.objective == "keep the previous answer"


def test_compiler_only_includes_explicit_related_project_briefs(monkeypatch):
    store = _store(monkeypatch)
    home = store.create_project(owner="alice", name="Home", workspace_root="/home")
    allowed = store.create_project(owner="alice", name="Allowed", workspace_root="/allowed")
    denied = store.create_project(owner="alice", name="Denied", workspace_root="/denied")
    store.bind_session(owner="alice", session_id="session", scope_kind="project", project_id=home)
    store.set_related_projects(owner="alice", project_id=home, related_project_ids=[allowed])
    store.write_project_brief(owner="alice", brief=ProjectBriefV1(project_id=allowed, summary="allowed"), source_hash="a")
    store.write_project_brief(owner="alice", brief=ProjectBriefV1(project_id=denied, summary="denied"), source_hash="d")

    bundle = ContextCompiler(store).compile(
        owner="alice", session_id="session", request="continue", transcript=[_message("user", "hello", 1)],
        related_project_ids=[allowed], episodic_hits=[{"id": "memory-1", "text": "a hit"}],
    )
    assert [brief.summary for brief in bundle.related_project_briefs] == ["allowed"]
    assert bundle.episodic_hits == ({"id": "memory-1", "text": "a hit"},)

    from src.continuity.store import ScopeConflictError
    import pytest
    with pytest.raises(ScopeConflictError):
        ContextCompiler(store).compile(
            owner="alice", session_id="session", request="continue", transcript=[], related_project_ids=[denied]
        )
