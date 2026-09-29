import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import routes.companion_patch_routes as patch_routes
from core.database import Base, ChatMessage as DbMessage, Project, ProjectChangeSet, Session as DbSession
from src.project_patches import prepare_change_set


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "README.md").write_text("# Before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=Test", "-c",
        "user.email=test@example.invalid", "commit", "-qm", "fixture",
    ], check=True)
    return root


@pytest.fixture
def database(workspace):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Project(id="project", owner="alice", name="Project", workspace_root=str(workspace)))
    db.add(DbSession(
        id="session", owner="alice", name="Project", endpoint_url="http://model", model="model",
        project_id="project", scope_kind="project", endpoint_id="endpoint",
    ))
    db.add(DbMessage(
        id="assistant-message", session_id="session", role="assistant", content="I prepared a safe change.",
    ))
    db.commit()
    try:
        yield db
    finally:
        db.close()


def _request(owner="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=owner, api_token=False))


def _endpoint(router, path, method):
    return next(route.endpoint for route in router.routes if route.path == path and method in route.methods)


def test_patch_request_models_reject_browser_supplied_content_and_paths():
    with pytest.raises(ValidationError):
        patch_routes.PatchRevisionRequest(expected_revision=1, content="replacement")


def test_patch_details_are_owner_scoped(monkeypatch, database):
    monkeypatch.setattr(patch_routes, "SessionLocal", lambda: database)
    monkeypatch.setattr(patch_routes, "recover_applying_change_sets", lambda _db: 0)
    router = patch_routes.setup_companion_patch_routes()
    endpoint = _endpoint(router, "/api/companion/patches/{patch_id}", "GET")

    with pytest.raises(HTTPException) as raised:
        endpoint("missing", _request("bob"))
    assert raised.value.status_code == 404


def test_source_message_detaches_and_project_delete_cascades_patch_body(workspace, database):
    prepared = prepare_change_set(
        workspace, [{"operation": "update", "path": "README.md", "content": "# Proposed\n"}],
        summary="Update docs",
    )
    row = ProjectChangeSet(
        id="patch", owner="alice", project_id="project", session_id="session",
        source_message_id="assistant-message", model="model", endpoint_id="endpoint",
        revision=1, status="proposed", summary=prepared.summary, rationale="",
        base_git_revision=prepared.base_git_revision, proposal_json=json.dumps(prepared.payload), result_json="{}",
    )
    database.add(row); database.commit()

    database.delete(database.query(DbMessage).filter_by(id="assistant-message").one()); database.commit()
    database.refresh(row)
    assert row.source_message_id is None

    database.delete(database.query(Project).filter_by(id="project").one())
    database.commit()
    assert database.query(ProjectChangeSet).count() == 0
    assert workspace.exists()
