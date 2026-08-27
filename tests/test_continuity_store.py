"""Regression tests for durable, owner-scoped continuity persistence."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ChatMessage as DbMessage, ContinuityArtifact, Session as DbSession
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


def test_heuristic_brief_cannot_replace_owner_accepted_project_home_state(store):
    continuity, local_session = store
    project_id = continuity.create_project(owner="alice", name="Odysseus", workspace_root="/work/odysseus")
    accepted = ProjectBriefV1(
        project_id=project_id, summary="Owner-approved release plan",
        derivation_status="accepted", derivation_version=1, derivation_method="owner_promotion_v1",
    )
    accepted_write = continuity.write_project_brief(owner="alice", brief=accepted, source_hash="accepted-source")
    heuristic = ProjectBriefV1(
        project_id=project_id, summary="Unreviewed latest chat claim",
        derivation_status="heuristic", derivation_version=1, derivation_method="local_heuristic_v1",
    )
    blocked = continuity.write_project_brief(owner="alice", brief=heuristic, source_hash="heuristic-source")

    assert (blocked.id, blocked.revision, blocked.created) == (
        accepted_write.id, accepted_write.revision, False,
    )
    assert continuity.latest_project_brief(owner="alice", project_id=project_id) == accepted

    # Simulate a historical row from the old bug, where a heuristic was
    # allowed to supersede an accepted brief. The reader recovers the latest
    # accepted state without rewriting source history.
    db = local_session()
    continuity._write(
        db, owner="alice", kind="project_brief_v1", session_id=None, project_id=project_id,
        payload=heuristic.to_payload(), source_through_message_id=None, source_hash="legacy-heuristic",
    )
    db.close()
    assert continuity.latest_project_brief(owner="alice", project_id=project_id) == accepted
    manifest = continuity.latest_artifact_manifest(
        owner="alice", kind="project_brief_v1", project_id=project_id,
    )
    assert manifest["artifact_revision"] == accepted_write.revision


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

    admitted, created = continuity.write_semantic_proposal_if_absent(owner="alice", proposal=proposal)
    assert created is False
    assert admitted.id == written.id


def test_checkpoint_mount_is_owner_scoped_read_only_and_detachable(store):
    continuity, local_session = store
    project_id = continuity.create_project(owner="alice", name="Dust", workspace_root="/work/dust")
    continuity.bind_session(owner="alice", session_id="alice-session", scope_kind="personal")
    db = local_session()
    db.add(DbSession(id="alice-destination", owner="alice", name="Dust fork", endpoint_url="http://a", model="m"))
    db.commit(); db.close()
    continuity.bind_session(
        owner="alice", session_id="alice-destination", scope_kind="project", project_id=project_id,
    )
    source = ThreadCheckpointV1(
        session_id="alice-session", objective="Compare release approaches",
        source_message_ids=["m1"], source_through_message_id="m1", source_hash="source-hash",
        derivation_status="heuristic", derivation_version=1, derivation_method="local_heuristic_v1",
    )
    source_write = continuity.write_thread_checkpoint(owner="alice", checkpoint=source)

    mount = continuity.attach_checkpoint(
        owner="alice", destination_session_id="alice-destination", source_checkpoint_id=source_write.id,
    )
    duplicate = continuity.attach_checkpoint(
        owner="alice", destination_session_id="alice-destination", source_checkpoint_id=source_write.id,
    )
    assert (mount.id, mount.revision, mount.checkpoint.objective) == (
        duplicate.id, duplicate.revision, "Compare release approaches",
    )
    assert [item.source_checkpoint_id for item in continuity.checkpoint_mounts(
        owner="alice", destination_session_id="alice-destination",
    )] == [source_write.id]

    continuity.detach_checkpoint_mount(
        owner="alice", destination_session_id="alice-destination", mount_id=mount.id,
        expected_revision=mount.revision,
    )
    assert continuity.checkpoint_mounts(owner="alice", destination_session_id="alice-destination") == []
    continuity.bind_session(owner="bob", session_id="bob-session", scope_kind="personal")
    with pytest.raises(NotFoundError):
        continuity.attach_checkpoint(
            owner="bob", destination_session_id="bob-session", source_checkpoint_id=source_write.id,
        )


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


def test_semantic_proposal_attempts_are_safe_revisioned_observability(store):
    continuity, _ = store
    continuity.bind_session(owner="alice", session_id="alice-session", scope_kind="personal")

    failed = continuity.write_semantic_proposal_attempt(
        owner="alice", session_id="alice-session", outcome="failed", code="provider_failed",
    )
    cancelled = continuity.write_semantic_proposal_attempt(
        owner="alice", session_id="alice-session", outcome="cancelled", code="newer_turn",
    )

    assert (failed.revision, failed.outcome, failed.code) == (1, "failed", "provider_failed")
    assert (cancelled.revision, cancelled.outcome, cancelled.code) == (2, "cancelled", "newer_turn")
    assert continuity.latest_semantic_proposal_attempt(
        owner="alice", session_id="alice-session",
    ) == cancelled


def _source_hash(local_session, session_id, source):
    db = local_session()
    for message_id, role, content in source:
        db.add(DbMessage(id=message_id, session_id=session_id, role=role, content=content, meta_data="{}"))
    db.commit()
    source_ids = [message_id for message_id, _role, _content in source]
    result = ContinuityStore._source_message_hash(db, session_id=session_id, source_ids=source_ids)
    db.close()
    return source_ids, result


def test_owner_selected_project_entries_promote_atomically_from_fresh_source(store):
    continuity, local_session = store
    project_id = continuity.create_project(owner="alice", name="Odysseus", workspace_root="/work/odysseus")
    continuity.bind_session(owner="alice", session_id="alice-session", scope_kind="project", project_id=project_id)
    source_ids, source_hash = _source_hash(local_session, "alice-session", [
        ("m1", "user", "Prepare the release"),
        ("m2", "assistant", "The release branch is frozen"),
    ])
    proposal = SemanticCheckpointProposalV1(
        session_id="alice-session", scope_kind="project", project_id=project_id,
        objective="Prepare the release", facts=["The branch is frozen"],
        decision_candidates=["Ship the small fix first"], proposals=["Canary first"],
        failed_approaches=["Previous rollout timed out"], open_questions=["Who reviews it?"],
        next_actions=["Run the canary"], artifact_refs=["plans/release.md"],
        source_message_ids=source_ids, source_through_message_id="m2", source_hash=source_hash,
        derivation_model="selected-model",
    )
    proposal_write = continuity.write_semantic_proposal(owner="alice", proposal=proposal)

    record, brief_write, brief = continuity.promote_semantic_proposal(
        owner="alice", proposal_id=proposal_write.id, expected_revision=proposal_write.revision,
        selections={"objective": [0], "facts": [0], "decision_candidates": [0], "next_actions": [0]},
    )

    assert record.status == "promoted"
    assert brief_write.created is True
    assert brief.derivation_status == "accepted"
    assert brief.derivation_method == "owner_promotion_v1"
    assert brief.summary == "Prepare the release"
    assert brief.confirmed_facts == ["The branch is frozen"]
    assert brief.accepted_decisions == ["Ship the small fix first"]
    assert brief.current_plans == ["Run the canary"]
    assert brief.proposals == []
    assert continuity.latest_project_brief(owner="alice", project_id=project_id) == brief
    with pytest.raises(ScopeConflictError, match="no longer available"):
        continuity.promote_semantic_proposal(
            owner="alice", proposal_id=proposal_write.id, expected_revision=proposal_write.revision,
            selections={"facts": [0]},
        )


def test_promotion_rejects_stale_source_without_changing_home_state(store):
    continuity, local_session = store
    continuity.bind_session(owner="alice", session_id="alice-session", scope_kind="personal")
    source_ids, source_hash = _source_hash(local_session, "alice-session", [
        ("m1", "user", "Remember that I prefer short plans"),
    ])
    proposal = SemanticCheckpointProposalV1(
        session_id="alice-session", scope_kind="personal", facts=["Prefers short plans"],
        source_message_ids=source_ids, source_through_message_id="m1", source_hash=source_hash,
        derivation_model="selected-model",
    )
    written = continuity.write_semantic_proposal(owner="alice", proposal=proposal)
    db = local_session()
    db.query(DbMessage).filter(DbMessage.id == "m1").update({"content": "Different current preference"})
    db.commit()
    db.close()

    with pytest.raises(ScopeConflictError, match="source is stale"):
        continuity.promote_semantic_proposal(
            owner="alice", proposal_id=written.id, expected_revision=written.revision,
            selections={"facts": [0]},
        )
    assert continuity.latest_personal_brief(owner="alice", session_id="alice-session") is None
    assert continuity.semantic_proposal(owner="alice", proposal_id=written.id).status == "active"
