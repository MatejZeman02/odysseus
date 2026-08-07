import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Project, ProjectChangeSet, Session as DbSession
from src.project_patches import (
    PATCH_END,
    PATCH_START,
    PatchError,
    apply_change_set,
    prepare_proposal,
    recover_applying_change_sets,
    reject_change_set,
    rollback_change_set,
)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "README.md").write_text("# Before\n", encoding="utf-8")
    os.chmod(root / "README.md", 0o640)
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return root


@pytest.fixture
def database(workspace):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    db = local()
    db.add(Project(id="project", owner="alice", name="Project", workspace_root=str(workspace)))
    db.add(DbSession(id="session", owner="alice", name="Project", endpoint_url="http://model", model="model",
                     project_id="project", scope_kind="project", endpoint_id="endpoint", harness_kind="qwen"))
    db.commit()
    try:
        yield db
    finally:
        db.close()


def _answer(changes, summary="Update docs"):
    payload = {"version": 1, "summary": summary, "rationale": "Keep the docs current", "changes": changes}
    return f"I prepared a reviewable change.\n{PATCH_START}\n{json.dumps(payload)}\n{PATCH_END}"


def _persist(database, prepared):
    row = ProjectChangeSet(
        id=uuid.uuid4().hex, owner="alice", project_id="project", session_id="session",
        model="model", endpoint_id="endpoint", revision=1, status="proposed",
        summary=prepared.summary, rationale=prepared.rationale,
        base_git_revision=prepared.base_git_revision,
        proposal_json=json.dumps(prepared.payload, ensure_ascii=False), result_json="{}",
    )
    database.add(row)
    database.commit()
    database.refresh(row)
    return row


def test_prepare_apply_and_rollback_atomic_text_patch(workspace, database):
    prepared = prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# After\n"},
        {"operation": "create", "path": "notes.md", "content": "New note\n"},
    ]))
    assert prepared.answer == "I prepared a reviewable change."
    assert [item["path"] for item in prepared.public_files] == ["README.md", "notes.md"]
    row = _persist(database, prepared)

    applied = apply_change_set(database, row, workspace, expected_revision=1)

    assert applied["status"] == "applied"
    assert applied["integrity"] == "verified"
    assert (workspace / "README.md").read_text() == "# After\n"
    assert stat_mode(workspace / "README.md") == 0o640
    assert (workspace / "notes.md").read_text() == "New note\n"
    assert stat_mode(workspace / "notes.md") == 0o600

    rolled_back = rollback_change_set(database, row, workspace, expected_revision=3)
    assert rolled_back["status"] == "rolled_back"
    assert (workspace / "README.md").read_text() == "# Before\n"
    assert stat_mode(workspace / "README.md") == 0o640
    assert not (workspace / "notes.md").exists()


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_stale_target_fails_without_overwriting_external_change(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# Proposed\n"},
    ])))
    (workspace / "README.md").write_text("# External\n", encoding="utf-8")

    with pytest.raises(PatchError) as raised:
        apply_change_set(database, row, workspace, expected_revision=1)

    assert raised.value.code == "patch_stale"
    assert (workspace / "README.md").read_text() == "# External\n"


def test_rollback_refuses_to_overwrite_later_work(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# Proposed\n"},
    ])))
    apply_change_set(database, row, workspace, expected_revision=1)
    (workspace / "README.md").write_text("# Later work\n", encoding="utf-8")

    with pytest.raises(PatchError) as raised:
        rollback_change_set(database, row, workspace, expected_revision=3)

    assert raised.value.code == "rollback_conflict"
    assert (workspace / "README.md").read_text() == "# Later work\n"


@pytest.mark.parametrize("path", ["../escape.md", "/tmp/escape.md", ".git/config", "dir\\file.md"])
def test_unsafe_paths_are_rejected(workspace, path):
    with pytest.raises(PatchError) as raised:
        prepare_proposal(workspace, _answer([{"operation": "create", "path": path, "content": "x"}]))
    assert raised.value.code == "path_denied"


def test_symlink_target_is_rejected(workspace, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    (workspace / "link.md").symlink_to(outside)
    with pytest.raises(PatchError) as raised:
        prepare_proposal(workspace, _answer([{"operation": "update", "path": "link.md", "content": "changed"}]))
    assert raised.value.code == "unsupported_file"
    assert outside.read_text() == "private"


def test_reject_is_revision_guarded(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# Proposed\n"},
    ])))
    result = reject_change_set(database, row, expected_revision=1)
    assert result["status"] == "rejected"
    with pytest.raises(PatchError) as raised:
        apply_change_set(database, row, workspace, expected_revision=2)
    assert raised.value.code == "apply_conflict"


def test_startup_recovery_restores_interrupted_applied_content(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# Proposed\n"},
    ])))
    proposal = json.loads(row.proposal_json)
    row.rollback_json = json.dumps({"version": 1, "files": [
        {"path": "README.md", "existed": True, "content": "# Before\n", "mode": 0o640},
    ]})
    row.status = "applying"
    row.revision = 2
    database.commit()
    (workspace / "README.md").write_text(proposal["changes"][0]["content"], encoding="utf-8")

    assert recover_applying_change_sets(database) == 1
    database.refresh(row)
    assert row.status == "apply_failed"
    assert json.loads(row.result_json)["integrity"] == "restored"
    assert (workspace / "README.md").read_text() == "# Before\n"
