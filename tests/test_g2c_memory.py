"""G2C scoped-memory/artifact regression coverage."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
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
        DbSession(id="computer", owner="alice", name="Computer", endpoint_url="http://x", model="m", scope_kind="computer"),
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


def test_linked_document_cannot_bypass_private_artifact_secret_policy(store):
    _continuity, memory = store
    artifact = memory.write_personal_artifact(
        owner="alice", session_id="personal", path="drafts/safe.md", content="# Safe\noriginal",
    )
    document = memory.open_personal_artifact_document(
        owner="alice", session_id="personal", artifact_id=artifact["id"],
    )
    db = memory_module.SessionLocal()
    try:
        with pytest.raises(MemoryScopeError, match="credential-like"):
            CompanionMemoryStore.sync_personal_artifact_from_document(
                db, owner="alice", document_id=document["id"], content="api_key=supersecretvalue",
            )
        db.rollback()
    finally:
        db.close()
    persisted = memory.get_personal_artifact(owner="alice", artifact_id=artifact["id"])
    assert persisted["revision"] == 1
    assert persisted["content"] == "# Safe\noriginal"


def test_computer_artifacts_are_private_revisioned_records_and_sync_from_documents(store):
    _continuity, memory = store
    artifact = memory.write_computer_artifact(
        owner="alice", session_id="computer", path="computer/incidents/nvidia.md",
        content="# NVIDIA issue\n\n- [ ] Capture the error",
    )
    assert artifact["scope_kind"] == "computer"
    document = memory.open_computer_artifact_document(
        owner="alice", session_id="computer", artifact_id=artifact["id"],
    )
    db = memory_module.SessionLocal()
    try:
        updated = CompanionMemoryStore.sync_personal_artifact_from_document(
            db, owner="alice", document_id=document["id"], content="# NVIDIA issue\n\n- [x] Capture the error",
        )
        db.commit()
        assert updated and updated.scope_kind == "computer" and updated.revision == 2
    finally:
        db.close()
    assert memory.get_computer_artifact(owner="alice", artifact_id=artifact["id"])["content"].endswith("Capture the error")
    assert memory.list_artifacts(owner="alice", scope_kind="computer")[0]["path"] == "computer/incidents/nvidia.md"


def test_computer_long_paste_is_kept_in_owner_private_artifacts_and_mounted_by_reference(store):
    continuity, memory = store
    paste = memory.capture_long_paste(owner="alice", session_id="computer", content="x" * 3000)
    assert paste["scope_kind"] == "computer"
    assert paste["path"].startswith("pastes/")
    bundle = ContextCompiler(continuity).compile(
        owner="alice", session_id="computer",
        request=f"Read the complete paste in `{paste['path']}` and summarize it.", transcript=[],
    )
    selected = next(item for item in bundle.working_artifacts if item["id"] == paste["id"])
    assert selected["content"] == "x" * 3000
    assert bundle.manifest["selected_working_artifact_paths"] == [paste["path"]]


def test_computer_incident_index_stays_metadata_only_until_explicitly_named(store):
    continuity, memory = store
    incident = memory.write_computer_artifact(
        owner="alice", session_id="computer", path="computer/incidents/nvidia.md",
        content="# NVIDIA issue\n\n- [ ] Inspect the loaded driver",
    )

    index_only = ContextCompiler(continuity).compile(
        owner="alice", session_id="computer", request="What incident records do I have?", transcript=[],
    )
    indexed = next(item for item in index_only.working_artifacts if item["id"] == incident["id"])
    assert "content" not in indexed

    named = ContextCompiler(continuity).compile(
        owner="alice", session_id="computer", request="Continue the nvidia incident from where we left off.", transcript=[],
    )
    selected = next(item for item in named.working_artifacts if item["id"] == incident["id"])
    assert selected["content"].endswith("Inspect the loaded driver")


def test_agent_document_writes_keep_a_linked_personal_artifact_authoritative(store, monkeypatch):
    """Agent tools and manual editor saves share the same artifact transaction."""
    import src.database as agent_database
    from src.agent_tools.document_tools import EditDocumentTool, UpdateDocumentTool

    _continuity, memory = store
    artifact = memory.write_personal_artifact(
        owner="alice", session_id="personal", path="drafts/agent.md", content="# Draft\nplaceholder",
    )
    document = memory.open_personal_artifact_document(
        owner="alice", session_id="personal", artifact_id=artifact["id"],
    )
    monkeypatch.setattr(agent_database, "SessionLocal", memory_module.SessionLocal)

    edited = asyncio.run(EditDocumentTool().execute(
        "<<<FIND>>>\nplaceholder\n<<<REPLACE>>>\nfirst agent revision\n<<<END>>>",
        {"owner": "alice", "doc_id": document["id"]},
    ))
    assert edited["action"] == "edit"
    first = memory.get_personal_artifact(owner="alice", artifact_id=artifact["id"])
    assert first["revision"] == 2
    assert first["content"].endswith("first agent revision")

    updated = asyncio.run(UpdateDocumentTool().execute(
        "# Draft\nsecond agent revision", {"owner": "alice", "doc_id": document["id"]},
    ))
    assert updated["action"] == "update"
    second = memory.get_personal_artifact(owner="alice", artifact_id=artifact["id"])
    assert second["revision"] == 3
    assert second["content"].endswith("second agent revision")
    db = memory_module.SessionLocal()
    try:
        recalled = db.query(ScopedMemoryRecord).filter(ScopedMemoryRecord.source_id == artifact["id"]).one()
        assert "second agent revision" in recalled.content
    finally:
        db.close()


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


def test_long_personal_paste_is_stored_once_and_mounted_by_reference(store):
    continuity, memory = store
    pasted = "Please summarize this log.\n" + ("diagnostic line\n" * 500)
    artifact = memory.capture_long_paste(owner="alice", session_id="personal", content=pasted)

    assert artifact["scope_kind"] == "personal"
    assert artifact["path"].startswith("pastes/")
    assert artifact["character_count"] == len(pasted)
    request = f"Read the complete pasted request in `{artifact['path']}` and answer it."
    bundle = ContextCompiler(continuity).compile(
        owner="alice", session_id="personal", request=request, transcript=[],
    )
    selected = next(item for item in bundle.working_artifacts if item["id"] == artifact["id"])
    assert selected["content"] == pasted
    assert bundle.manifest["selected_working_artifact_paths"] == [artifact["path"]]


def test_long_paste_threshold_matches_real_companion_pastes(store):
    _continuity, memory = store
    captured = memory.capture_long_paste(
        owner="alice", session_id="personal", content="x" * 3000,
    )
    assert captured["character_count"] == 3000
    with pytest.raises(MemoryScopeError, match="at least 3000"):
        memory.capture_long_paste(
            owner="alice", session_id="personal", content="x" * 2999,
        )


def test_long_project_paste_stays_out_of_workspace_and_is_qwen_context(store, tmp_path):
    continuity, memory = store
    workspace = tmp_path / "dust"
    workspace.mkdir()
    db = memory_module.SessionLocal()
    try:
        db.query(Project).filter(Project.id == "dust").update({Project.workspace_root: str(workspace)})
        db.commit()
    finally:
        db.close()
    pasted = "Investigate this build output.\n" + ("compiler diagnostic\n" * 400)
    artifact = memory.capture_long_paste(owner="alice", session_id="project", content=pasted)

    assert artifact["scope_kind"] == "project"
    assert artifact["project_id"] == "dust"
    assert list(workspace.iterdir()) == []
    indexed = memory.list_artifacts(owner="alice", scope_kind="project", project_id="dust")
    assert [item["path"] for item in indexed] == [artifact["path"]]
    bundle = ContextCompiler(continuity).compile(
        owner="alice", session_id="project",
        request=f"Use `{artifact['path']}` as my complete request.", transcript=[],
    )
    assert bundle.working_artifacts[0]["content"] == pasted
    assert bundle.manifest["selected_working_artifact_paths"] == [artifact["path"]]


def test_long_paste_capture_rejects_secrets_and_non_companion_sessions(store):
    _continuity, memory = store
    with pytest.raises(MemoryScopeError, match="credential-like"):
        memory.capture_long_paste(
            owner="alice", session_id="personal",
            content=("ordinary log line\n" * 400) + "api_key=supersecretvalue",
        )
    db = memory_module.SessionLocal()
    try:
        db.add(DbSession(
            id="general", owner="alice", name="General", endpoint_url="http://x", model="m",
            scope_kind="general",
        ))
        db.commit()
    finally:
        db.close()
    with pytest.raises(MemoryScopeError, match="Personal or project"):
        memory.capture_long_paste(owner="alice", session_id="general", content="x" * 6000)


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


def test_context_compiler_uses_scoped_provider_contract_for_normal_recall(store):
    """Prompt assembly must not bypass the scoped provider with raw index I/O."""
    from src.memory_provider import MemoryRecord, MemorySearchHit, ScopedMemoryScope

    continuity, _memory = store
    seen = {}

    class Provider:
        def recall_scoped_sync(self, query):
            seen["query"] = query
            record_scope = ScopedMemoryScope(
                owner_id="alice", home_kind="project", project_id="dust", session_id="project",
                provenance_kind="accepted_home_brief", provenance_id="brief-current",
            )
            return [MemorySearchHit(
                MemoryRecord(
                    id="episodic-1", text="Dust uses a fish motif", session_id="project", scope=record_scope,
                ),
                "fake-scoped",
            )]

    bundle = ContextCompiler(continuity, scoped_memory_provider=Provider()).compile(
        owner="alice", session_id="project", request="Which motif is used?", transcript=[],
    )

    assert seen["query"].scope.owner_id == "alice"
    assert seen["query"].scope.home_kind == "project"
    assert seen["query"].scope.project_id == "dust"
    assert bundle.episodic_hits == ({
        "id": "episodic-1", "source_kind": "accepted_home_brief", "source_id": "brief-current",
        "text": "Dust uses a fish motif", "session_id": "project", "expires_at": None,
        "sensitivity": "normal", "scope": {"kind": "project", "project_id": "dust"},
    },)


def test_personal_brief_is_separate_from_project_and_checkpoint(store):
    continuity, _memory = store
    brief = PersonalBriefV1(owner_id="alice", summary="Prefers clear concise plans", preferences=["concise"])
    continuity.write_personal_brief(owner="alice", session_id="personal", brief=brief, source_hash="brief")
    bundle = ContextCompiler(continuity).compile(owner="alice", session_id="personal", request="hello", transcript=[])
    assert bundle.personal_brief.summary == "Prefers clear concise plans"
    assert bundle.primary_project_brief is None


def test_replacing_an_accepted_home_brief_removes_its_stale_recall_entry(store):
    _continuity, _memory = store
    from routes.companion_memory_routes import _index_accepted_home_brief

    first = PersonalBriefV1(owner_id="alice", summary="Old fish preference")
    second = PersonalBriefV1(owner_id="alice", summary="Current orchid preference")
    _index_accepted_home_brief(
        owner="alice", session_id="personal", scope_kind="personal", project_id=None,
        source_id="brief-one", brief=first,
    )
    _index_accepted_home_brief(
        owner="alice", session_id="personal", scope_kind="personal", project_id=None,
        source_id="brief-two", brief=second,
    )
    db = memory_module.SessionLocal()
    try:
        records = db.query(ScopedMemoryRecord).filter(
            ScopedMemoryRecord.owner == "alice",
            ScopedMemoryRecord.source_kind == "accepted_home_brief",
        ).all()
        assert len(records) == 1
        assert records[0].source_id == "brief-two"
        assert "orchid" in records[0].content
        assert "fish" not in records[0].content
    finally:
        db.close()


def test_episodic_recall_filters_owner_home_and_project(store):
    from src.scoped_memory import ScopedMemoryIndex
    index = ScopedMemoryIndex()
    index.index(owner="alice", scope_kind="project", project_id="dust", session_id="project", source_kind="brief", source_id="dust", content="Fish enemy design")
    index.index(owner="alice", scope_kind="project", project_id="other", session_id="other", source_kind="brief", source_id="other", content="Fish secret from other project")
    index.index(owner="alice", scope_kind="personal", project_id=None, session_id="personal", source_kind="brief", source_id="personal", content="Fish dinner preference")
    assert [item["source_id"] for item in index.recall(owner="alice", scope_kind="project", project_id="dust", query="fish")] == ["dust"]
    assert [item["source_id"] for item in index.recall(owner="alice", scope_kind="personal", project_id=None, query="fish")] == ["personal"]


def test_context_manifest_audits_episodic_source_kinds_without_recalled_text(store):
    continuity, _memory = store
    bundle = ContextCompiler(continuity).compile(
        owner="alice", session_id="project", request="fish", transcript=[],
        episodic_hits=[
            {"source_kind": "artifact", "source_id": "private-artifact", "content": "must not be in manifest"},
            {"source_kind": "brief", "source_id": "private-brief", "content": "must not be in manifest"},
        ],
    )

    assert bundle.manifest["episodic_hit_count"] == 2
    assert bundle.manifest["episodic_hit_kinds"] == {"artifact": 1, "brief": 1}
    assert "must not be in manifest" not in json.dumps(bundle.manifest)
    assert "private-artifact" not in json.dumps(bundle.manifest)


def test_episodic_recall_excludes_expired_records_and_rejects_ambiguous_scope(store):
    from src.scoped_memory import ScopedMemoryIndex

    index = ScopedMemoryIndex()
    index.index(
        owner="alice", scope_kind="project", project_id="dust", session_id="project",
        source_kind="brief", source_id="expired", content="Fish enemy design",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
    )
    index.index(
        owner="alice", scope_kind="project", project_id="dust", session_id="project",
        source_kind="brief", source_id="current", content="Fish enemy design",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    )

    assert [item["source_id"] for item in index.recall(
        owner="alice", scope_kind="project", project_id="dust", query="fish",
    )] == ["current"]
    with pytest.raises(ValueError, match="project binding"):
        index.index(
            owner="alice", scope_kind="project", project_id=None, session_id="project",
            source_kind="brief", source_id="bad", content="must not persist",
        )
    with pytest.raises(ValueError, match="project binding"):
        index.index(
            owner="alice", scope_kind="personal", project_id="dust", session_id="personal",
            source_kind="brief", source_id="bad-personal", content="must not persist",
        )


def test_local_scoped_provider_enforces_complete_scope_and_provenance(store):
    from src.memory_provider import ScopedMemoryQuery, ScopedMemoryScope
    from src.scoped_memory import LocalScopedMemoryProvider

    provider = LocalScopedMemoryProvider()
    project_scope = ScopedMemoryScope(
        owner_id="alice", home_kind="project", project_id="dust", session_id="project",
        provenance_kind="thread_checkpoint", provenance_id="checkpoint-1",
    )
    stored = asyncio.run(provider.remember_scoped("Fish enemy design", scope=project_scope))
    assert stored.scope == project_scope
    assert stored.owner == "alice"

    hits = asyncio.run(provider.recall_scoped(ScopedMemoryQuery("fish enemy", project_scope)))
    assert [hit.memory.id for hit in hits] == [stored.id]
    assert hits[0].memory.scope.provenance_id == "checkpoint-1"

    personal_scope = ScopedMemoryScope(
        owner_id="alice", home_kind="personal", project_id=None, session_id="personal",
        provenance_kind="thread_checkpoint", provenance_id="personal-checkpoint",
    )
    assert asyncio.run(provider.recall_scoped(ScopedMemoryQuery("fish enemy", personal_scope))) == []
    assert asyncio.run(provider.delete_scoped(stored.id, scope=personal_scope)) is False
    assert asyncio.run(provider.delete_scoped(stored.id, scope=project_scope)) is True

    with pytest.raises(ValueError, match="provenance"):
        asyncio.run(provider.remember_scoped(
            "must fail", scope=ScopedMemoryScope(
                owner_id="alice", home_kind="project", project_id="dust", session_id="project",
            ),
        ))


def test_local_scoped_provider_keeps_stored_expiry_and_rejects_expired_writes(store):
    from src.memory_provider import ScopedMemoryQuery, ScopedMemoryScope
    from src.scoped_memory import LocalScopedMemoryProvider

    provider = LocalScopedMemoryProvider()
    expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    scope = ScopedMemoryScope(
        owner_id="alice", home_kind="project", project_id="dust", session_id="project",
        provenance_kind="approved_brief", provenance_id="brief-1", expires_at=expires_at,
    )
    asyncio.run(provider.remember_scoped("A fish enemy", scope=scope))
    hit = asyncio.run(provider.recall_scoped(ScopedMemoryQuery("fish", scope)))[0]
    assert hit.memory.scope is not None
    assert hit.memory.scope.expires_at == expires_at.replace(tzinfo=None)

    expired_scope = ScopedMemoryScope(
        owner_id="alice", home_kind="project", project_id="dust", session_id="project",
        provenance_kind="approved_brief", provenance_id="expired-brief",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="future"):
        asyncio.run(provider.remember_scoped("Must not persist", scope=expired_scope))


def test_g2c_routes_and_ui_keep_scopes_explicit():
    routes = open("routes/companion_memory_routes.py", encoding="utf-8").read()
    ui = open("static/js/sessions.js", encoding="utf-8").read()
    page = open("static/index.html", encoding="utf-8").read()
    assert "/context-grants" in routes
    assert "/artifacts/personal" in routes
    assert "/artifacts/personal/{artifact_id}/document" in routes
    assert "/artifacts/capture-paste" in routes
    assert "/semantic-proposals" in routes
    assert "/semantic-proposals/{proposal_id}/promote" in routes
    assert "overflow-companion-context-btn" in page
    assert "overflow-companion-artifacts-btn" not in page
    assert "deprecated-companion-artifacts-btn" in page
    assert 'id="companion-context-btn"' not in page
    assert 'id="companion-artifacts-btn"' not in page
    assert "Allow once" in ui
    assert "project material is never searched automatically" in ui.lower()
    chat = open("static/js/chat.js", encoding="utf-8").read()
    assert "LONG_PASTE_ARTIFACT_THRESHOLD = 3000" in chat
    assert "Task excerpt:" in chat
    assert "Long paste saved as artifact" in chat
    assert "Create semantic proposal" in ui
    assert "selected model ${model}" in ui
    assert "no tools" in ui
    assert "Background proposal paused" in ui
    assert '"mode": "no_tools"' in routes
    assert "/api/companion/project-brief" in ui
    assert "Edit ${payload.scope_kind === 'personal' ? 'Personal' : 'Project'} brief" in ui
    assert "Promote selected entries" in ui
    assert "data-proposal-index" in ui
    assert "checkpoint-mounts" in routes
    assert "Read-only checkpoint mounts" in ui
    assert "companion-attach-checkpoint" in ui
    assert "checkpointSourceLabel" in ui
    assert "disagreements remain source-attributed" in ui
    assert "/api/companion/memory/checkpoint-synthesis/preview" in ui
    assert "Review selected checkpoints" in ui
    assert "Agreement and differences" in ui
    assert "Personal threads" in ui
    assert "personalThreads" in ui
    assert "companion-run-legacy-inventory" in ui
    assert "/api/companion/memory/legacy-inventory" in ui
    assert "companion-run-legacy-dry-run" in ui
    assert "/api/companion/memory/legacy-dry-run" in ui
    assert "history-load-older-btn" in ui
    assert "Load older messages" in ui
    assert "Related project access" in ui
    assert "@Project Name" in ui
    assert "companion-save-related-projects" in ui
    assert "Last compiled context" in ui
    assert "Scope-verified episodic hits" in ui


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
    captured = endpoint("/api/companion/artifacts/capture-paste")(
        routes.LongPasteCapture(session_id="personal", content="pasted line\n" * 600), request,
    )
    assert captured["path"].startswith("pastes/")
    assert captured["character_count"] == len("pasted line\n" * 600)


def test_personal_memory_ui_opens_the_shared_documents_library():
    sessions_js = open("static/js/sessions.js", encoding="utf-8").read()
    assert "companion-open-documents" in sessions_js
    assert "documentApi.openLibrary({tab: 'documents'})" in sessions_js
    assert "companion-artifact-editor" not in sessions_js


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
    assert not _turn_targets_active_document(
        {"domains": {"documents"}},
        "Create a new artifact markdown document from the pasted source.",
        artifact,
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
