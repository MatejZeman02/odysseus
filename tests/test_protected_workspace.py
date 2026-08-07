import subprocess

import pytest

from src.protected_workspace import WorkspaceMutationError, run_read_only, snapshot_workspace


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "dust"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "note.md").write_text("canonical note\n")
    subprocess.run(["git", "-C", str(root), "add", "note.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return root


def test_read_only_runner_returns_stable_snapshot(workspace):
    result, snapshot = run_read_only(workspace, lambda: (workspace / "note.md").read_text())
    assert result == "canonical note\n"
    assert snapshot == snapshot_workspace(workspace)


def test_snapshot_streams_large_files_without_read_bytes(workspace, monkeypatch):
    large = workspace / "large.bin"
    large.write_bytes(b"x" * (2 * 1024 * 1024 + 17))
    monkeypatch.setattr(type(large), "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("unbounded read")))
    snapshot = snapshot_workspace(workspace)
    assert len(snapshot.files["large.bin"].removeprefix("file:0:")) == 64


def test_snapshot_ignores_gitignored_runtime_changes(workspace):
    (workspace / ".gitignore").write_text("data/\n")
    (workspace / "data").mkdir()
    runtime_log = workspace / "data" / "app.log"
    runtime_log.write_text("started\n")
    before = snapshot_workspace(workspace)

    runtime_log.write_text("started\nrequest completed\n")

    assert snapshot_workspace(workspace) == before


def test_snapshot_includes_nonignored_untracked_files(workspace):
    before = snapshot_workspace(workspace)
    (workspace / "draft.md").write_text("new project content\n")

    after = snapshot_workspace(workspace)

    assert before != after
    assert "draft.md" in after.files


def test_read_only_runner_detects_a_file_mutation(workspace):
    with pytest.raises(WorkspaceMutationError):
        run_read_only(workspace, lambda: (workspace / "note.md").write_text("changed\n"))
