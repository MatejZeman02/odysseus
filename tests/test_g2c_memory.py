"""G2C scoped-memory/artifact regression coverage."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Document, Project, ScopedMemoryRecord, Session as DbSession
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
    db = memory_module.SessionLocal()
    try:
        assert db.query(ScopedMemoryRecord).filter(ScopedMemoryRecord.source_id == first["id"]).count() == 0
    finally:
        db.close()
    assert memory.restore_personal_artifact(owner="alice", artifact_id=first["id"], expected_revision=4)["content"].endswith("hello")
    db = memory_module.SessionLocal()
    try:
        recalled = db.query(ScopedMemoryRecord).filter(ScopedMemoryRecord.source_id == first["id"]).one()
        assert "drafts/love-letter.md" in recalled.content
        assert "hello" in recalled.content
    finally:
        db.close()


def test_personal_artifact_opens_in_native_document_and_editor_save_stays_scoped(store):
    _continuity, memory = store
    artifact = memory.write_personal_artifact(
        owner="alice", session_id="personal", path="drafts/love-letter.md", content="# Dear you\nhello",
    )
    document = memory.open_personal_artifact_document(owner="alice", session_id="personal", artifact_id=artifact["id"])
    assert document["title"] == "Artifact · drafts/love-letter.md"
    db = memory_module.SessionLocal()
    try:
        assert db.query(Document).filter(Document.id == document["id"]).one().current_content.endswith("hello")
        updated = CompanionMemoryStore.sync_personal_artifact_from_document(
            db, owner="alice", document_id=document["id"], content="# Dear you\nupdated",
        )
        db.commit()
        assert updated and updated.revision == 2
    finally:
        db.close()
    assert memory.get_personal_artifact(owner="alice", artifact_id=artifact["id"])["content"].endswith("updated")


def test_personal_artifact_and_document_link_survive_a_file_database_restart(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'g2c-restart.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    monkeypatch.setattr(memory_module, "SessionLocal", local)
    db = local()
    try:
        db.add(DbSession(
            id="personal-restart", owner="alice", name="Personal", endpoint_url="http://x", model="m",
            scope_kind="personal",
        ))
        db.commit()
    finally:
        db.close()

    memory = CompanionMemoryStore()
    artifact = memory.write_personal_artifact(
        owner="alice", session_id="personal-restart", path="drafts/restart.md", content="# Persisted\nText",
    )
    opened = memory.open_personal_artifact_document(
        owner="alice", session_id="personal-restart", artifact_id=artifact["id"],
    )
    engine.dispose()

    restarted_engine = create_engine(database_url)
    restarted_local = sessionmaker(bind=restarted_engine)
    monkeypatch.setattr(memory_module, "SessionLocal", restarted_local)
    after_restart = CompanionMemoryStore().get_personal_artifact(owner="alice", artifact_id=artifact["id"])
    assert after_restart["content"] == "# Persisted\nText"
    db = restarted_local()
    try:
        assert db.query(Document).filter(Document.id == opened["id"]).one().current_content == "# Persisted\nText"
    finally:
        db.close()
    restarted_engine.dispose()


def test_personal_advisor_gets_only_an_explicitly_named_artifact_body(store):
    continuity, memory = store
    memory.write_personal_artifact(
        owner="alice", session_id="personal", path="drafts/love-letter.md",
        content="# Love letter\nThe actual private draft.",
    )
    memory.write_personal_artifact(
        owner="alice", session_id="personal", path="notes/private-plan.md",
        content="# Private plan\nThis must stay metadata-only.",
    )

    bundle = ContextCompiler(continuity).compile(
        owner="alice", session_id="personal",
        request="Please improve my love letter without changing its meaning.", transcript=[],
    )
    indexed = {item["path"]: item for item in bundle.working_artifacts}
    assert indexed["drafts/love-letter.md"]["content"] == "# Love letter\nThe actual private draft."
    assert "content" not in indexed["notes/private-plan.md"]
    assert bundle.manifest["selected_working_artifact_paths"] == ["drafts/love-letter.md"]


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
    assert memory.approved_grants(owner="alice", personal_session_id="personal") == []

    denied_grant = memory.create_grant_request(
        owner="alice", personal_session_id="personal", project_id="dust", purpose="Should not be visible",
    )
    memory.decide_grant(owner="alice", grant_id=denied_grant["id"], allow=False)
    denied = ContextCompiler(continuity).compile(
        owner="alice", session_id="personal", request="use the denied request", transcript=[],
    )
    assert denied.related_project_briefs == ()


def test_project_artifacts_are_shared_by_project_sessions_and_survive_store_recreation(store, tmp_path):
    continuity, memory = store
    workspace = tmp_path / "dust"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".artifacts" / "plans").mkdir(parents=True)
    (workspace / ".artifacts" / "plans" / "release.md").write_text("# Release\nShip safely.", encoding="utf-8")
    db = memory_module.SessionLocal()
    try:
        db.query(Project).filter(Project.id == "dust").update({Project.workspace_root: str(workspace)})
        db.add(DbSession(
            id="project-fork", owner="alice", name="Dust fork", endpoint_url="http://x", model="m",
            scope_kind="project", project_id="dust",
        ))
        db.commit()
    finally:
        db.close()

    primary = memory.list_artifacts(owner="alice", scope_kind="project", project_id="dust")
    restarted_store = CompanionMemoryStore()
    fork = restarted_store.list_artifacts(owner="alice", scope_kind="project", project_id="dust")
    assert [item["path"] for item in primary] == [".artifacts/plans/release.md"]
    assert primary == fork

    primary_bundle = ContextCompiler(continuity).compile(
        owner="alice", session_id="project", request="review release", transcript=[],
    )
    fork_bundle = ContextCompiler(continuity).compile(
        owner="alice", session_id="project-fork", request="review release", transcript=[],
    )
    assert [item["path"] for item in primary_bundle.working_artifacts] == [".artifacts/plans/release.md"]
    assert [item["path"] for item in fork_bundle.working_artifacts] == [".artifacts/plans/release.md"]


def test_scoped_recall_outage_does_not_break_exact_artifact_or_checkpoint_context(store, monkeypatch):
    continuity, memory = store
    memory.write_personal_artifact(
        owner="alice", session_id="personal", path="drafts/resilient.md", content="# Resilient\nKeep this draft.",
    )

    class BrokenRecall:
        def recall(self, **_kwargs):
            raise RuntimeError("retrieval provider is offline")

    monkeypatch.setattr(scoped_memory_module, "ScopedMemoryIndex", BrokenRecall)
    bundle = ContextCompiler(continuity).compile(
        owner="alice", session_id="personal", request="continue resilient", transcript=[],
    )
    assert bundle.episodic_hits == ()
    assert [item["path"] for item in bundle.working_artifacts] == ["drafts/resilient.md"]


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
    assert "/artifacts/personal/{artifact_id}/document" in routes
    assert "overflow-companion-context-btn" in page
    assert "overflow-companion-artifacts-btn" in page
    assert 'id="companion-context-btn"' not in page
    assert 'id="companion-artifacts-btn"' not in page
    assert "Allow once" in ui
    assert "project material is never searched automatically" in ui.lower()


def test_companion_memory_routes_execute_the_owner_scoped_artifact_bridge(store, monkeypatch):
    """Exercise the registered endpoints without depending on browser auth."""
    import routes.companion_memory_routes as routes

    _continuity, _memory = store
    monkeypatch.setattr(routes, "SessionLocal", memory_module.SessionLocal)
    monkeypatch.setattr(routes, "_owner", lambda _request: "alice")
    router = routes.setup_companion_memory_routes()
    endpoint = lambda path: next(item.endpoint for item in router.routes if item.path == path)
    request = SimpleNamespace()

    created = endpoint("/api/companion/artifacts/personal")(routes.PersonalArtifactWrite(
        session_id="personal", path="drafts/route-test.md", content="# Route test\nFirst version",
    ), request)
    context = endpoint("/api/companion/memory/sessions/{session_id}")("personal", request)
    opened = endpoint("/api/companion/artifacts/personal/{artifact_id}/document")(
        created["id"], routes.ArtifactDocumentOpen(session_id="personal"), request,
    )
    assert context["scope_kind"] == "personal"
    assert context["artifacts"][0]["path"] == "drafts/route-test.md"
    assert opened["title"] == "Artifact · drafts/route-test.md"
    assert opened["current_content"].endswith("First version")


def test_personal_artifact_ui_uses_bridge_payload_without_a_second_load_race():
    sessions_js = open("static/js/sessions.js", encoding="utf-8").read()
    assert "documentApi.injectFreshDoc(documentRecord)" in sessions_js
    assert "racing a second GET" in sessions_js


def test_personal_artifact_editor_has_a_document_only_agent_path():
    from src.agent_loop import (
        _is_personal_artifact_document_obj,
        _turn_requests_active_document_edit,
        _turn_targets_active_document,
    )

    agent = open("src/agent_loop.py", encoding="utf-8").read()
    assert "PERSONAL WORKING ARTIFACT MODE" in agent
    assert "Do NOT call manage_memory, search_chats" in agent
    assert "if _active_personal_artifact_document:" in agent
    artifact = SimpleNamespace(
        title="Artifact · drafts/love-letter.md", language="markdown", current_content="# Letter",
    )
    assert _is_personal_artifact_document_obj(artifact)
    assert _turn_targets_active_document(
        {"domains": set()}, "It is opened on the side now; you can start editing.", artifact,
    )
    assert _turn_requests_active_document_edit("It is opened on the side now; you can start editing.")
    assert _turn_requests_active_document_edit("Rewrite the poem in the artifact.")
    assert not _turn_requests_active_document_edit("What is the title of this artifact?")


def test_personal_artifact_edit_does_not_arm_the_external_write_approval_gate():
    """An owner-selected artifact is editable through its sealed doc route.

    Its text is still labelled untrusted for the model; only the blanket
    approval gate is bypassed. A normal open document remains gated.
    """
    from src.agent_loop import _build_system_prompt
    from src.tool_capabilities import ToolRunSecurityContext

    artifact = SimpleNamespace(
        title="Artifact · drafts/love-letter.md",
        language="markdown",
        current_content="# Dear Tortellina\nA private draft.",
        id="artifact-document",
    )
    prompt, _schemas = _build_system_prompt(
        [{"role": "user", "content": "Please revise the artifact in place."}],
        "deepseek/deepseek-v4-flash-0731",
        artifact,
        None,
        relevant_tools={"edit_document", "update_document"},
        owner="alice",
    )
    active = next(
        message for message in prompt
        if message.get("metadata", {}).get("source") == "active editor document"
    )
    assert active["metadata"]["trusted"] is False
    assert active["metadata"]["tool_gate_untrusted"] is False
    security = ToolRunSecurityContext()
    security.observe_messages(prompt)
    assert security.decision_for("edit_document").allowed

    ordinary_document = SimpleNamespace(
        title="Untrusted notes",
        language="markdown",
        current_content="# Notes\nImported content.",
        id="ordinary-document",
    )
    ordinary_prompt, _schemas = _build_system_prompt(
        [{"role": "user", "content": "Please revise the open document."}],
        "deepseek/deepseek-v4-flash-0731",
        ordinary_document,
        None,
        relevant_tools={"edit_document", "update_document"},
        owner="alice",
    )
    ordinary_security = ToolRunSecurityContext()
    ordinary_security.observe_messages(ordinary_prompt)
    assert not ordinary_security.decision_for("edit_document").allowed
