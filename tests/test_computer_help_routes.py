import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.database import Base, ComputerTaskRoot, Session as DbSession
import routes.computer_help_routes as route_module
from routes.computer_help_routes import _device_profile_markdown, _observation_message


def _endpoint(router, path, method="POST"):
    return next(
        route.endpoint for route in router.routes
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set())
    )


def _task_root_router(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    monkeypatch.setattr(route_module, "SessionLocal", local)
    monkeypatch.setattr(route_module, "_owner", lambda _request: "alice")
    monkeypatch.setattr(route_module.Path, "home", classmethod(lambda _cls: tmp_path))
    task_dir = tmp_path / "computer-tasks"; task_dir.mkdir()
    db = local()
    db.add(DbSession(
        id="computer", owner="alice", name="Computer Help", endpoint_url="http://x", model="m",
        scope_kind="computer", is_scope_primary=True,
    ))
    db.commit(); db.close()
    return route_module.setup_computer_help_routes(SimpleNamespace(add_message=lambda *_args: None)), task_dir, local


def test_observation_message_is_a_persistent_process_trace_without_raw_output():
    content, process = _observation_message([{
        "summary": "Fedora · kernel",
        "facts": ["OS: Fedora", "Kernel: 6.x"],
        "process": {"operation": "Inspect: operating system and kernel"},
    }])
    assert "Computer diagnostic snapshot" in content
    assert "OS: Fedora" in content
    assert process["events"] == [{
        "kind": "tool", "tool": "inspect",
        "command": "Inspect: operating system and kernel", "status": "completed",
    }]


def test_device_profile_is_markdown_from_verified_sanitized_facts_only():
    profile = _device_profile_markdown([{
        "category": "graphics",
        "summary": "ignored summary",
        "facts": ["GPU: Example", "Driver: example"],
    }])
    assert profile.startswith("# Device profile")
    assert "## Graphics" in profile
    assert "GPU: Example" in profile
    assert "ignored summary" not in profile


def test_task_root_registration_is_owner_scoped_private_and_non_executable(monkeypatch, tmp_path):
    router, task_dir, local = _task_root_router(monkeypatch, tmp_path)
    create = _endpoint(router, "/api/companion/computer/task-roots")
    result = create(route_module.TaskRootCreate(
        session_id="computer", label="Build scratch", path=str(task_dir),
    ), SimpleNamespace())

    assert result["task_root"]["label"] == "Build scratch"
    assert result["task_root"]["directory_name"] == "computer-tasks"
    assert str(task_dir) not in json.dumps(result)
    assert task_dir.is_dir()
    db = local()
    try:
        stored = db.query(ComputerTaskRoot).one()
        assert stored.root_path == str(task_dir)
        assert stored.status == "active"
    finally:
        db.close()

    listed = _endpoint(router, "/api/companion/computer/task-roots", "GET")("computer", SimpleNamespace())
    assert [row["id"] for row in listed["task_roots"]] == [result["task_root"]["id"]]
    assert listed["execution_ready"] is False

    retire = _endpoint(router, "/api/companion/computer/task-roots/{task_root_id}", "DELETE")
    retired = retire(result["task_root"]["id"], route_module.TaskRootRetire(
        session_id="computer", expected_revision=1,
    ), SimpleNamespace())
    assert retired["retired"] is True
    assert task_dir.is_dir()


def test_task_root_rejects_non_computer_scope_and_sensitive_or_broad_paths(monkeypatch, tmp_path):
    router, _task_dir, local = _task_root_router(monkeypatch, tmp_path)
    db = local()
    db.add(DbSession(id="personal", owner="alice", name="Personal", endpoint_url="http://x", model="m", scope_kind="personal"))
    db.commit(); db.close()
    create = _endpoint(router, "/api/companion/computer/task-roots")
    with pytest.raises(HTTPException) as wrong_scope:
        create(route_module.TaskRootCreate(session_id="personal", label="No", path=str(tmp_path / "computer-tasks")), SimpleNamespace())
    assert wrong_scope.value.status_code == 409

    protected = tmp_path / ".ssh"; protected.mkdir()
    with pytest.raises(HTTPException) as denied:
        create(route_module.TaskRootCreate(session_id="computer", label="Credentials", path=str(protected)), SimpleNamespace())
    assert denied.value.status_code == 422

    hidden = tmp_path / ".local" / "share" / "keyrings" / "task"; hidden.mkdir(parents=True)
    with pytest.raises(HTTPException) as hidden_denied:
        create(route_module.TaskRootCreate(session_id="computer", label="Hidden", path=str(hidden)), SimpleNamespace())
    assert hidden_denied.value.status_code == 422

    with pytest.raises(HTTPException) as broad:
        create(route_module.TaskRootCreate(session_id="computer", label="Home", path=str(tmp_path)), SimpleNamespace())
    assert broad.value.status_code == 422


def test_task_root_rejects_a_symlink_even_when_its_target_is_inside_home(monkeypatch, tmp_path):
    router, task_dir, _local = _task_root_router(monkeypatch, tmp_path)
    link = tmp_path / "linked-task-root"
    try:
        link.symlink_to(task_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    create = _endpoint(router, "/api/companion/computer/task-roots")
    with pytest.raises(HTTPException) as denied:
        create(route_module.TaskRootCreate(
            session_id="computer", label="Linked", path=str(link),
        ), SimpleNamespace())
    assert denied.value.status_code == 422


def test_task_root_duplicate_insert_race_returns_a_conflict(monkeypatch, tmp_path):
    router, task_dir, local = _task_root_router(monkeypatch, tmp_path)
    create = _endpoint(router, "/api/companion/computer/task-roots")
    original_local = route_module.SessionLocal

    class _RacingSession:
        def __init__(self, db):
            self._db = db
        def __getattr__(self, name):
            return getattr(self._db, name)
        def commit(self):
            raise IntegrityError("INSERT", {}, RuntimeError("unique owner/label"))

    monkeypatch.setattr(route_module, "SessionLocal", lambda: _RacingSession(original_local()))
    with pytest.raises(HTTPException) as conflict:
        create(route_module.TaskRootCreate(
            session_id="computer", label="Build scratch", path=str(task_dir),
        ), SimpleNamespace())
    assert conflict.value.status_code == 409
    assert "already exists" in str(conflict.value.detail)
