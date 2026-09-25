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
import src.project_patches as patches_module


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


def test_a_proposal_works_before_the_first_commit(workspace, database):
    import shutil

    shutil.rmtree(workspace / ".git")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    prepared = prepare_proposal(workspace, _answer([
        {"operation": "create", "path": "notes.md", "content": "New note\n"},
    ]))
    assert prepared.base_git_revision == ""
    row = _persist(database, prepared)

    assert apply_change_set(database, row, workspace, expected_revision=1)["status"] == "applied"
    assert (workspace / "notes.md").read_text() == "New note\n"
    assert rollback_change_set(database, row, workspace, expected_revision=3)["status"] == "rolled_back"
    assert not (workspace / "notes.md").exists()


@pytest.mark.parametrize("content", ["", "not-a-commit\n"])
def test_a_damaged_branch_ref_is_not_taken_for_a_new_repository(workspace, content):
    # Git reports an empty or corrupt branch ref the same way as a branch
    # with no commit yet. A checkout with history in that state is damaged,
    # and its checks must refuse it rather than compare two empty HEADs.
    from src.protected_workspace import snapshot_workspace

    branch = subprocess.run(
        ["git", "-C", str(workspace), "symbolic-ref", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    (workspace / ".git" / branch).write_text(content, encoding="utf-8")

    with pytest.raises(PatchError) as refused:
        prepare_proposal(workspace, _answer([
            {"operation": "update", "path": "README.md", "content": "# After\n"},
        ]))
    assert refused.value.code == "proposal_invalid"
    with pytest.raises(subprocess.CalledProcessError):
        snapshot_workspace(workspace)


def test_create_file_in_missing_directories_and_remove_them_on_rollback(workspace, database):
    prepared = prepare_proposal(workspace, _answer([
        {"operation": "create", "path": "documents/guides/g2b-test.md", "content": "Project summary\n"},
    ]))
    row = _persist(database, prepared)

    applied = apply_change_set(database, row, workspace, expected_revision=1)

    target = workspace / "documents" / "guides" / "g2b-test.md"
    assert applied["status"] == "applied"
    assert target.read_text() == "Project summary\n"
    assert stat_mode(target) == 0o600

    rolled_back = rollback_change_set(database, row, workspace, expected_revision=3)
    assert rolled_back["status"] == "rolled_back"
    assert not target.exists()
    assert not (workspace / "documents").exists()


def test_rollback_keeps_later_files_in_a_created_directory(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "create", "path": "documents/g2b-test.md", "content": "Project summary\n"},
    ])))
    apply_change_set(database, row, workspace, expected_revision=1)
    (workspace / "documents" / "user-note.md").write_text("later work\n")

    rollback_change_set(database, row, workspace, expected_revision=3)

    assert not (workspace / "documents" / "g2b-test.md").exists()
    assert (workspace / "documents" / "user-note.md").read_text() == "later work\n"


def test_proposal_and_rollback_material_are_encrypted_at_rest(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# Private proposal\n"},
    ])))
    apply_change_set(database, row, workspace, expected_revision=1)
    raw = database.connection().exec_driver_sql(
        "SELECT proposal_json, rollback_json FROM project_change_sets WHERE id = ?", (row.id,),
    ).one()
    assert raw[0].startswith("enc:") and "Private proposal" not in raw[0]
    assert raw[1].startswith("enc:") and "# Before" not in raw[1]


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
    assert row.status == "stale"
    assert row.failure_code == "patch_stale"


def test_dirty_affected_file_is_rejected_but_unrelated_dirty_file_is_allowed(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# Proposed\n"},
    ])))
    (workspace / "unrelated.txt").write_text("keep me\n", encoding="utf-8")
    applied = apply_change_set(database, row, workspace, expected_revision=1)
    assert applied["status"] == "applied"
    assert (workspace / "unrelated.txt").read_text() == "keep me\n"

    rollback_change_set(database, row, workspace, expected_revision=3)
    (workspace / "README.md").write_text("# User work\n", encoding="utf-8")
    second = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# Other proposal\n"},
    ])))
    with pytest.raises(PatchError) as raised:
        apply_change_set(database, second, workspace, expected_revision=1)
    assert raised.value.code == "apply_conflict"
    assert second.status == "stale"
    assert (workspace / "README.md").read_text() == "# User work\n"


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


def test_symlink_parent_for_new_file_is_rejected(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "documents").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PatchError) as raised:
        prepare_proposal(workspace, _answer([
            {"operation": "create", "path": "documents/g2b-test.md", "content": "blocked\n"},
        ]))

    assert raised.value.code == "path_denied"
    assert not (outside / "g2b-test.md").exists()


def test_project_root_symlink_is_rejected_without_touching_its_target(workspace, database, tmp_path):
    outside = tmp_path / "outside-project"
    outside.mkdir()
    (outside / "README.md").write_text("outside before\n", encoding="utf-8")
    root_link = tmp_path / "project-link"
    root_link.symlink_to(outside, target_is_directory=True)
    database.query(Project).filter(Project.id == "project").one().workspace_root = str(root_link)
    database.commit()

    with pytest.raises(PatchError) as raised:
        prepare_proposal(root_link, _answer([
            {"operation": "update", "path": "README.md", "content": "outside after\n"},
        ]))

    assert raised.value.code == "path_denied"
    assert (outside / "README.md").read_text() == "outside before\n"


def test_reject_is_revision_guarded(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# Proposed\n"},
    ])))
    result = reject_change_set(database, row, expected_revision=1)
    assert result["status"] == "rejected"
    with pytest.raises(PatchError) as raised:
        apply_change_set(database, row, workspace, expected_revision=2)
    assert raised.value.code == "apply_conflict"


def test_partial_apply_failure_restores_every_written_target(workspace, database, monkeypatch):
    (workspace / "SECOND.md").write_text("second before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "SECOND.md"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "second fixture"], check=True)
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "update", "path": "README.md", "content": "# First after\n"},
        {"operation": "update", "path": "SECOND.md", "content": "second after\n"},
    ])))
    real_write = patches_module._write_atomic
    writes = 0

    def fail_second_write(target, content, *, mode=0o600):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected replacement failure")
        return real_write(target, content, mode=mode)

    monkeypatch.setattr(patches_module, "_write_atomic", fail_second_write)
    with pytest.raises(PatchError) as raised:
        apply_change_set(database, row, workspace, expected_revision=1)
    assert raised.value.code == "apply_failed"
    assert (workspace / "README.md").read_text() == "# Before\n"
    assert (workspace / "SECOND.md").read_text() == "second before\n"
    assert json.loads(row.result_json)["integrity"] == "restored"


def test_apply_failure_removes_new_parent_directories(workspace, database, monkeypatch):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "create", "path": "documents/g2b-test.md", "content": "summary\n"},
    ])))

    def fail_write(*_args, **_kwargs):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(patches_module, "_write_atomic", fail_write)
    with pytest.raises(PatchError) as raised:
        apply_change_set(database, row, workspace, expected_revision=1)

    assert raised.value.code == "apply_failed"
    assert not (workspace / "documents").exists()


def test_proposal_limits_and_unsupported_shapes_are_rejected(workspace):
    with pytest.raises(PatchError) as raised:
        prepare_proposal(workspace, _answer([
            {"operation": "delete", "path": "README.md", "content": ""},
        ]))
    assert raised.value.code == "proposal_invalid"

    with pytest.raises(PatchError) as raised:
        prepare_proposal(workspace, _answer([
            {"operation": "create", "path": "binary.dat", "content": "bad\x00data"},
        ]))
    assert raised.value.code == "unsupported_file"

    with pytest.raises(PatchError) as raised:
        prepare_proposal(workspace, _answer([
            {"operation": "create", "path": f"file-{index}.md", "content": "x"}
            for index in range(21)
        ]))
    assert raised.value.code == "proposal_too_large"


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


def test_startup_recovery_removes_interrupted_nested_create(workspace, database):
    row = _persist(database, prepare_proposal(workspace, _answer([
        {"operation": "create", "path": "documents/g2b-test.md", "content": "summary\n"},
    ])))
    proposal = json.loads(row.proposal_json)
    row.rollback_json = json.dumps({
        "version": 1,
        "files": [{"path": "documents/g2b-test.md", "existed": False, "content": "", "mode": 0o600}],
        "created_directories": ["documents"],
    })
    row.status = "applying"
    row.revision = 2
    database.commit()
    (workspace / "documents").mkdir()
    (workspace / "documents" / "g2b-test.md").write_text(
        proposal["changes"][0]["content"], encoding="utf-8",
    )

    assert recover_applying_change_sets(database) == 1
    database.refresh(row)
    assert row.status == "apply_failed"
    assert not (workspace / "documents").exists()
