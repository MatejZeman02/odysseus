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
from src.companion_runs import CompanionRunRegistry
from src.project_patches import prepare_proposal


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
        project_id="project", scope_kind="project", endpoint_id="endpoint", harness_kind="qwen",
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
    with pytest.raises(ValidationError):
        patch_routes.PatchTurnRequest(session_id="s", message="change it", endpoint_url="http://attacker")


@pytest.mark.asyncio
async def test_patch_stream_persists_trusted_proposal_and_uses_shared_admission(
    monkeypatch, workspace, database,
):
    prepared = prepare_proposal(workspace, (
        "I prepared a safe change.\n<odysseus-change-set>\n"
        + json.dumps({
            "version": 1, "summary": "Update docs", "rationale": "Keep docs current",
            "changes": [{"operation": "update", "path": "README.md", "content": "# Proposed\n"}],
        })
        + "\n</odysseus-change-set>"
    ))
    assistant = SimpleNamespace(metadata={"_db_id": "assistant-message"})
    manager = SimpleNamespace(sessions={"session": SimpleNamespace(history=[assistant])})
    registry = CompanionRunRegistry()

    class Service:
        def __init__(self, *_args, **_kwargs): pass
        async def run(self, **kwargs):
            assert kwargs["proposal_mode"] is True
            assert kwargs["capability_profile"] == "project_read"
            return SimpleNamespace(
                answer=prepared.answer, proposal=prepared, message_id="assistant-message",
                user_message_id="user-message", manifest={"scope": "project"},
                dust_unchanged=True, qwen_process={"outcome": "worked"},
            )

    monkeypatch.setattr(patch_routes, "SessionLocal", lambda: database)
    monkeypatch.setattr(patch_routes, "recover_applying_change_sets", lambda _db: 0)
    monkeypatch.setattr(patch_routes, "_require_qwen_ready", lambda: None)
    monkeypatch.setattr(patch_routes, "_patch_turn_binding", lambda *_args: (str(workspace), "endpoint", "model"))
    monkeypatch.setattr(patch_routes, "_stored_route", lambda *_args, **_kwargs: ("endpoint", "model"))
    monkeypatch.setattr(patch_routes, "_stored_capability", lambda *_args: ("project_read", "project_read"))
    monkeypatch.setattr(patch_routes, "_qwen_binary", lambda: workspace / "qwen")
    monkeypatch.setattr(patch_routes, "ReadOnlyScopedTurnService", Service)
    router = patch_routes.setup_companion_patch_routes(manager, registry)
    endpoint = _endpoint(router, "/api/companion/projects/{project_id}/patch-turn/stream", "POST")

    response = await endpoint(
        "project", patch_routes.PatchTurnRequest(session_id="session", message="change it"), _request(),
    )
    chunks = [chunk async for chunk in response.body_iterator]

    assert any("event: proposal" in chunk for chunk in chunks)
    assert any("event: done" in chunk for chunk in chunks)
    row = database.query(ProjectChangeSet).one()
    assert row.status == "proposed"
    assert row.summary == "Update docs"
    assert "content" not in next(chunk for chunk in chunks if "event: proposal" in chunk)
    assert assistant.metadata["project_patch"]["id"] == row.id
    assert not registry.admitted


def test_patch_details_are_owner_scoped(monkeypatch, database):
    monkeypatch.setattr(patch_routes, "SessionLocal", lambda: database)
    monkeypatch.setattr(patch_routes, "recover_applying_change_sets", lambda _db: 0)
    router = patch_routes.setup_companion_patch_routes(SimpleNamespace(), CompanionRunRegistry())
    endpoint = _endpoint(router, "/api/companion/patches/{patch_id}", "GET")

    with pytest.raises(HTTPException) as raised:
        endpoint("missing", _request("bob"))
    assert raised.value.status_code == 404


def test_source_message_detaches_and_project_delete_cascades_patch_body(workspace, database):
    prepared = prepare_proposal(workspace, (
        "Ready.\n<odysseus-change-set>\n"
        + json.dumps({
            "version": 1, "summary": "Update docs", "rationale": "Test lifecycle",
            "changes": [{"operation": "update", "path": "README.md", "content": "# Proposed\n"}],
        })
        + "\n</odysseus-change-set>"
    ))
    row = ProjectChangeSet(
        id="patch", owner="alice", project_id="project", session_id="session",
        source_message_id="assistant-message", model="model", endpoint_id="endpoint",
        revision=1, status="proposed", summary=prepared.summary, rationale=prepared.rationale,
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
