"""Regression tests for durable, owner-scoped continuity persistence."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ContinuityArtifact, Session as DbSession
from src.continuity import (
    ProjectBriefV1,
    ScopeConflictError,
    SemanticCheckpointProposalV1,
    ThreadCheckpointV1,
)
from src.continuity.store import ContinuityStore, NotFoundError
import src.continuity.store as continuity_store_module


@pytest.fixture
def store(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    monkeypatch.setattr(continuity_store_module, "SessionLocal", local_session)

    db = local_session()
    db.add_all(
        [
            DbSession(id="alice-session", owner="alice", name="Alice", endpoint_url="http://a", model="m"),
            DbSession(id="bob-session", owner="bob", name="Bob", endpoint_url="http://b", model="m"),
        ]
    )
    db.commit()
    db.close()
    return ContinuityStore(), local_session


def test_project_binding_is_owner_scoped_and_stable(store):
    continuity, _ = store
    project_id = continuity.create_project(owner="alice", name="Odysseus", workspace_root="/work/odysseus")

    scope = continuity.bind_session(
        owner="alice", session_id="alice-session", scope_kind="project", project_id=project_id
    )
    assert scope.project_id == project_id
    assert scope.workspace_root == "/work/odysseus"

    with pytest.raises(ScopeConflictError):
        continuity.bind_session(owner="alice", session_id="alice-session", scope_kind="personal")
    with pytest.raises(NotFoundError):
        continuity.resolve_scope(owner="bob", session_id="alice-session")

    moved = continuity.bind_session(
        owner="alice", session_id="alice-session", scope_kind="personal", force=True
    )
    assert moved.scope_kind == "personal"
    assert moved.project_id is None


def test_artifacts_are_versioned_idempotent_and_never_replace_messages(store):
    continuity, local_session = store
    project_id = continuity.create_project(owner="alice", name="Odysseus", workspace_root="/work/odysseus")
    continuity.bind_session(
        owner="alice", session_id="alice-session", scope_kind="project", project_id=project_id
    )
    first = ThreadCheckpointV1(
        session_id="alice-session", project_id=project_id, objective="persist the plan",
        source_message_ids=["m1"], source_through_message_id="m1", source_hash="hash-one",
    )
    written = continuity.write_thread_checkpoint(owner="alice", checkpoint=first)
    repeated = continuity.write_thread_checkpoint(owner="alice", checkpoint=first)
    assert (written.revision, written.created) == (1, True)
    assert (repeated.revision, repeated.created) == (1, False)

    second = ThreadCheckpointV1(
        session_id="alice-session", project_id=project_id, objective="continue the plan",
        source_message_ids=["m1", "m2"], source_through_message_id="m2", source_hash="hash-two",
    )
    assert continuity.write_thread_checkpoint(owner="alice", checkpoint=second).revision == 2
    assert continuity.latest_thread_checkpoint(owner="alice", session_id="alice-session").objective == "continue the plan"

    db = local_session()
    rows = db.query(ContinuityArtifact).filter_by(session_id="alice-session").order_by(ContinuityArtifact.revision).all()
    assert [(row.revision, row.status) for row in rows] == [(1, "superseded"), (2, "active")]
    db.close()


def test_cross_project_context_requires_explicit_direct_relationship(store):
    continuity, _ = store
    home = continuity.create_project(owner="alice", name="Home", workspace_root="/work/home")
    related = continuity.create_project(owner="alice", name="Related", workspace_root="/work/related")
    unrelated = continuity.create_project(owner="alice", name="Unrelated", workspace_root="/work/unrelated")
    continuity.set_related_projects(owner="alice", project_id=home, related_project_ids=[related])
    brief = ProjectBriefV1(project_id=related, summary="Only the directly related brief is available.")
    continuity.write_project_brief(owner="alice", brief=brief, source_hash="related-hash")

    assert continuity.related_project_brief(owner="alice", home_project_id=home, requested_project_id=related) == brief
    with pytest.raises(ScopeConflictError):
        continuity.related_project_brief(owner="alice", home_project_id=home, requested_project_id=unrelated)
    assert continuity.latest_project_brief(owner="bob", project_id=related) is None


def test_semantic_proposal_is_immutable_source_linked_and_not_a_home_brief(store):
    continuity, local_session = store
    project_id = continuity.create_project(owner="alice", name="Odysseus", workspace_root="/work/odysseus")
    continuity.bind_session(
        owner="alice", session_id="alice-session", scope_kind="project", project_id=project_id
    )
    proposal = SemanticCheckpointProposalV1(
        session_id="alice-session", scope_kind="project", project_id=project_id,
        objective="Prepare a reliable release", facts=["The release branch is frozen"],
        decision_candidates=["Ship the small fix first"], proposals=["Run a canary"],
        failed_approaches=["The previous rollout timed out"], open_questions=["Which model is used?"],
        next_actions=["Review the canary"], artifact_refs=["plans/release.md"],
        source_message_ids=["m1", "m2"], source_through_message_id="m2", source_hash="proposal-source",
        derivation_model="selected-model",
    )

    written = continuity.write_semantic_proposal(owner="alice", proposal=proposal)
    repeated = continuity.write_semantic_proposal(owner="alice", proposal=proposal)
    loaded = continuity.latest_semantic_proposal(owner="alice", session_id="alice-session")

    assert (written.revision, written.created) == (1, True)
    assert (repeated.revision, repeated.created) == (1, False)
    assert loaded == proposal
    assert continuity.latest_project_brief(owner="alice", project_id=project_id) is None
    db = local_session()
    row = db.query(ContinuityArtifact).filter_by(id=written.id).one()
    db.close()
    assert row.kind == "semantic_checkpoint_proposal_v1"
    assert row.status == "active"
    assert row.source_hash == "proposal-source"


def test_semantic_proposal_rejects_invalid_scope_or_promotion_status(store):
    continuity, _ = store
    project_id = continuity.create_project(owner="alice", name="Odysseus", workspace_root="/work/odysseus")
    continuity.bind_session(owner="alice", session_id="alice-session", scope_kind="personal")

    with pytest.raises(ValueError, match="derivation_status"):
        SemanticCheckpointProposalV1(
            session_id="alice-session", scope_kind="personal", source_message_ids=["m1"],
            source_through_message_id="m1", source_hash="hash", derivation_model="model",
            derivation_status="accepted",
        )

    mismatch = SemanticCheckpointProposalV1(
        session_id="alice-session", scope_kind="project", project_id=project_id,
        source_message_ids=["m1"], source_through_message_id="m1", source_hash="hash",
        derivation_model="model",
    )
    with pytest.raises(ScopeConflictError, match="stable session binding"):
        continuity.write_semantic_proposal(owner="alice", proposal=mismatch)
