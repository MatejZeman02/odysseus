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


def test_read_only_runner_detects_a_file_mutation(workspace):
    with pytest.raises(WorkspaceMutationError):
        run_read_only(workspace, lambda: (workspace / "note.md").write_text("changed\n"))
