"""G2C scoped-memory/artifact regression coverage."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Project, Session as DbSession
from src.companion_memory import ArtifactConflict, CompanionMemoryStore, MemoryScopeError
from src.continuity.compiler import ContextCompiler
from src.continuity.contracts import PersonalBriefV1, ProjectBriefV1
from src.continuity.store import ContinuityStore
import src.companion_memory as memory_module
import src.scoped_memory as scoped_memory_module
import src.continuity.store as store_module


@pytest.fixture()
def store(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    monkeypatch.setattr(store_module, "SessionLocal", local)
    monkeypatch.setattr(memory_module, "SessionLocal", local)
    monkeypatch.setattr(scoped_memory_module, "SessionLocal", local)
    db = local()
    db.add_all([
        DbSession(id="personal", owner="alice", name="Personal", endpoint_url="http://x", model="m", scope_kind="personal"),
        DbSession(id="project", owner="alice", name="Dust", endpoint_url="http://x", model="m", scope_kind="project", project_id="dust"),
        DbSession(id="other", owner="alice", name="Other", endpoint_url="http://x", model="m", scope_kind="project", project_id="other"),
        Project(id="dust", owner="alice", name="Dust", workspace_root="/dust"),
        Project(id="other", owner="alice", name="Other", workspace_root="/other"),
    ])
    db.commit(); db.close()
    return ContinuityStore(), CompanionMemoryStore()


def test_personal_artifact_is_revisioned_and_owner_scoped(store):
    _continuity, memory = store
    first = memory.write_personal_artifact(owner="alice", session_id="personal", path="drafts/love-letter.md", content="# Dear you\nhello")
    assert first["revision"] == 1
    second = memory.write_personal_artifact(owner="alice", session_id="personal", path="drafts/love-letter.md", content="# Dear you\nupdated", expected_revision=1)
    assert second["revision"] == 2
    assert memory.undo_personal_artifact(owner="alice", artifact_id=first["id"], expected_revision=2)["content"].endswith("hello")
    with pytest.raises(ArtifactConflict):
        memory.write_personal_artifact(owner="alice", session_id="personal", path="drafts/love-letter.md", content="new", expected_revision=1)
    with pytest.raises(MemoryScopeError):
        memory.write_personal_artifact(owner="alice", session_id="personal", path="../secrets.md", content="x")
    with pytest.raises(MemoryScopeError):
        memory.write_personal_artifact(owner="alice", session_id="personal", path="notes/key.md", content="api_key=supersecretvalue")
    deleted = memory.delete_personal_artifact(owner="alice", artifact_id=first["id"], expected_revision=3)
    assert deleted["deleted"] is True
    assert memory.restore_personal_artifact(owner="alice", artifact_id=first["id"], expected_revision=4)["content"].endswith("hello")


def test_project_memory_needs_grant_or_explicit_owner_request(store):
    continuity, memory = store
    continuity.write_project_brief(owner="alice", brief=ProjectBriefV1(project_id="dust", summary="Dust private brief"), source_hash="dust")
    base = ContextCompiler(continuity).compile(owner="alice", session_id="personal", request="help me decide", transcript=[])
    assert base.related_project_briefs == ()
    direct = ContextCompiler(continuity).compile(owner="alice", session_id="personal", request="Please consult Dust project memory", transcript=[])
    assert [brief.summary for brief in direct.related_project_briefs] == ["Dust private brief"]
    grant = memory.create_grant_request(owner="alice", personal_session_id="personal", project_id="dust", purpose="Compare the project brief")
    memory.decide_grant(owner="alice", grant_id=grant["id"], allow=True)
    granted = ContextCompiler(continuity).compile(owner="alice", session_id="personal", request="use the approved context", transcript=[])
    assert [brief.project_id for brief in granted.related_project_briefs] == ["dust"]
    following = ContextCompiler(continuity).compile(owner="alice", session_id="personal", request="continue without mentioning a project", transcript=[])
    assert following.related_project_briefs == ()


def test_personal_brief_is_separate_from_project_and_checkpoint(store):
    continuity, _memory = store
    brief = PersonalBriefV1(owner_id="alice", summary="Prefers clear concise plans", preferences=["concise"])
    continuity.write_personal_brief(owner="alice", session_id="personal", brief=brief, source_hash="brief")
    bundle = ContextCompiler(continuity).compile(owner="alice", session_id="personal", request="hello", transcript=[])
    assert bundle.personal_brief.summary == "Prefers clear concise plans"
    assert bundle.primary_project_brief is None


def test_episodic_recall_filters_owner_home_and_project(store):
    from src.scoped_memory import ScopedMemoryIndex
    index = ScopedMemoryIndex()
    index.index(owner="alice", scope_kind="project", project_id="dust", session_id="project", source_kind="brief", source_id="dust", content="Fish enemy design")
    index.index(owner="alice", scope_kind="project", project_id="other", session_id="other", source_kind="brief", source_id="other", content="Fish secret from other project")
    index.index(owner="alice", scope_kind="personal", project_id=None, session_id="personal", source_kind="brief", source_id="personal", content="Fish dinner preference")
    assert [item["source_id"] for item in index.recall(owner="alice", scope_kind="project", project_id="dust", query="fish")] == ["dust"]
    assert [item["source_id"] for item in index.recall(owner="alice", scope_kind="personal", project_id=None, query="fish")] == ["personal"]


def test_g2c_routes_and_ui_keep_scopes_explicit():
    routes = open("routes/companion_memory_routes.py", encoding="utf-8").read()
    ui = open("static/js/sessions.js", encoding="utf-8").read()
    page = open("static/index.html", encoding="utf-8").read()
    assert "/context-grants" in routes
    assert "/artifacts/personal" in routes
    assert "companion-context-btn" in page
    assert "companion-artifacts-btn" in page
    assert "Allow once" in ui
    assert "project material is never searched automatically" in ui.lower()
