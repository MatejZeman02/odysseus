from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ChatMessage as DbMessage, Session as DbSession
from src.continuity import ProjectBriefV1, SemanticCheckpointProposalV1, ThreadCheckpointV1
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


def _add_session(local_session, session_id):
    db = local_session()
    db.add(DbSession(id=session_id, owner="alice", name=session_id, endpoint_url="http://x", model="m"))
    db.commit()
    db.close()


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

    repeated = CheckpointCompactor(store, tail_count=2).checkpoint(
        owner="alice", session_id="session", messages=transcript,
    )
    assert repeated.source_hash == checkpoint.source_hash

    extended = transcript + [_message("user", "new work", 6), _message("assistant", "new result", 7)]
    next_checkpoint = CheckpointCompactor(store, tail_count=2).checkpoint(
        owner="alice", session_id="session", messages=extended,
        derive=lambda _: {"objective": "only newly eligible messages"},
    )
    assert next_checkpoint.source_message_ids == ["m3", "m4", "m5"]
    assert next_checkpoint.objective == "only newly eligible messages"

    bundle = ContextCompiler(store, tail_count=2).compile(
        owner="alice", session_id="session", request="continue", transcript=transcript
    )
    assert [message["metadata"]["_db_id"] for message in bundle.transcript_tail] == ["m3", "m4", "m5"]
    assert bundle.thread_checkpoint.objective == "only newly eligible messages"


def test_checkpoint_recovers_when_prior_cursor_was_deleted(monkeypatch):
    store = _store(monkeypatch)
    project_id = store.create_project(owner="alice", name="P", workspace_root="/p")
    store.bind_session(owner="alice", session_id="session", scope_kind="project", project_id=project_id)
    old = [_message("user", "old request", 1), _message("assistant", "old answer", 2)]
    CheckpointCompactor(store, tail_count=1).checkpoint(owner="alice", session_id="session", messages=old)

    # The previous cursor no longer occurs in the material retained by a cold
    # client (or after an owner deletes earlier messages).  The new turn must
    # still complete and establish a current checkpoint/project brief.
    retained = [_message("user", "new request", 3), _message("assistant", "new answer", 4)]
    checkpoint = CheckpointCompactor(store, tail_count=1).checkpoint(
        owner="alice", session_id="session", messages=retained,
        derive=lambda _: {"objective": "current objective"},
    )
    assert checkpoint.source_message_ids == ["m3"]
    assert store.latest_project_brief(owner="alice", project_id=project_id).summary == "current objective"


def test_automatic_checkpoint_is_explicitly_heuristic_and_never_promotes_assistant_prose(monkeypatch):
    store = _store(monkeypatch)
    project_id = store.create_project(owner="alice", name="P", workspace_root="/p")
    store.bind_session(owner="alice", session_id="session", scope_kind="project", project_id=project_id)
    transcript = [
        _message("user", "Please decide how to structure the release plan", 1),
        _message("assistant", "I have decided that a risky deployment is safe.", 2),
        _message("user", "continue", 3),
    ]

    checkpoint = CheckpointCompactor(store, tail_count=1).checkpoint(
        owner="alice", session_id="session", messages=transcript,
    )
    brief = store.latest_project_brief(owner="alice", project_id=project_id)
    bundle = ContextCompiler(store, tail_count=1).compile(
        owner="alice", session_id="session", request="continue", transcript=transcript,
    )

    assert checkpoint.derivation_status == "heuristic"
    assert checkpoint.derivation_method == "local_heuristic_v1"
    assert checkpoint.accepted_decisions == []
    assert checkpoint.source_message_ids == ["m1", "m2"]
    assert brief.derivation_status == "heuristic"
    assert brief.source_message_ids == checkpoint.source_message_ids
    assert brief.source_through_message_id == checkpoint.source_through_message_id
    assert bundle.manifest["continuity_provenance"]["thread_checkpoint"] == "heuristic"


def test_legacy_checkpoint_payload_is_unclassified_not_accepted():
    checkpoint = ThreadCheckpointV1.from_payload({
        "session_id": "s", "source_hash": "h", "source_message_ids": ["m1"],
        "source_through_message_id": "m1", "accepted_decisions": ["old assistant prose"],
    })

    assert checkpoint.derivation_status == "legacy_unclassified"
    assert checkpoint.derivation_version == 0
    assert checkpoint.to_payload()["derivation_method"] == "legacy_unclassified"


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


def test_project_fork_receives_shared_brief_but_not_primary_raw_tail_or_checkpoint(monkeypatch):
    store = _store(monkeypatch)
    # Reuse the patched SQLAlchemy factory from the store fixture setup.
    _add_session(continuity_store_module.SessionLocal, "fork")
    _add_session(continuity_store_module.SessionLocal, "personal")
    _add_session(continuity_store_module.SessionLocal, "computer")
    project = store.create_project(owner="alice", name="Dust", workspace_root="/dust")
    store.bind_session(owner="alice", session_id="session", scope_kind="project", project_id=project)
    store.bind_session(owner="alice", session_id="fork", scope_kind="project", project_id=project)
    store.bind_session(owner="alice", session_id="personal", scope_kind="personal")
    store.bind_session(owner="alice", session_id="computer", scope_kind="computer")
    primary = [_message("user", "Dust final crystal", 1), _message("assistant", "conflict", 2), _message("user", "continue", 3)]
    CheckpointCompactor(store, tail_count=1).checkpoint(owner="alice", session_id="session", messages=primary)
    store.write_project_brief(owner="alice", brief=ProjectBriefV1(project_id=project, summary="Dust conflict is unresolved"), source_hash="brief-1")

    fork = ContextCompiler(store, tail_count=1).compile(
        owner="alice", session_id="fork", request="new fork", transcript=[_message("user", "fork question", 4)]
    )
    personal = ContextCompiler(store, tail_count=1).compile(
        owner="alice", session_id="personal", request="why is lemon acidic?", transcript=[_message("user", "lemon", 5)]
    )
    computer = ContextCompiler(store, tail_count=1).compile(
        owner="alice", session_id="computer", request="diagnose display", transcript=[_message("user", "display", 6)]
    )
    assert fork.thread_checkpoint is None
    assert fork.primary_project_brief.summary == "Dust conflict is unresolved"
    assert "Dust final crystal" not in str(fork.transcript_tail)
    assert personal.primary_project_brief is None
    assert personal.related_project_briefs == ()
    # G2C exposes only an owner-scoped project *catalog* (name/ID) so the
    # Personal Advisor can ask for access. No project brief or transcript is
    # mounted without a grant.
    assert personal.manifest["project_catalog"] == [{"id": project, "name": "Dust"}]
    assert "Dust conflict" not in str(personal.manifest)
    assert computer.primary_project_brief is None
    assert computer.related_project_briefs == ()
    assert "Dust" not in str(computer.manifest)


def test_project_fork_receives_owner_promoted_semantic_brief_not_primary_transcript(monkeypatch):
    store = _store(monkeypatch)
    _add_session(continuity_store_module.SessionLocal, "fork")
    project = store.create_project(owner="alice", name="Dust", workspace_root="/dust")
    store.bind_session(owner="alice", session_id="session", scope_kind="project", project_id=project)
    store.bind_session(owner="alice", session_id="fork", scope_kind="project", project_id=project)
    db = continuity_store_module.SessionLocal()
    db.add_all([
        DbMessage(id="semantic-1", session_id="session", role="user", content="Keep the crystal conflict unresolved", meta_data="{}"),
        DbMessage(id="semantic-2", session_id="session", role="assistant", content="The documents disagree about its origin", meta_data="{}"),
    ])
    db.commit()
    source_hash = ContinuityStore._source_message_hash(
        db, session_id="session", source_ids=["semantic-1", "semantic-2"],
    )
    db.close()
    proposal = SemanticCheckpointProposalV1(
        session_id="session", scope_kind="project", project_id=project,
        objective="Resolve the crystal conflict", facts=["The documents disagree about its origin"],
        source_message_ids=["semantic-1", "semantic-2"], source_through_message_id="semantic-2",
        source_hash=source_hash, derivation_model="model-a",
    )
    written = store.write_semantic_proposal(owner="alice", proposal=proposal)
    store.promote_semantic_proposal(
        owner="alice", proposal_id=written.id, expected_revision=written.revision,
        selections={"objective": [0], "facts": [0]},
    )

    fork = ContextCompiler(store, tail_count=1).compile(
        owner="alice", session_id="fork", request="what remains?", transcript=[_message("user", "fork question", 9)],
    )
    assert fork.thread_checkpoint is None
    assert fork.primary_project_brief.derivation_status == "accepted"
    assert fork.primary_project_brief.summary == "Resolve the crystal conflict"
    assert fork.primary_project_brief.confirmed_facts == ["The documents disagree about its origin"]
    assert "crystal conflict" not in str(fork.transcript_tail)
