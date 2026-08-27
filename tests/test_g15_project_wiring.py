import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_project_workspace_comes_from_server_owned_binding(monkeypatch, tmp_path):
    import routes.chat_routes as chat_routes
    import src.tool_security as tool_security

    workspace = tmp_path / "Dust"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    class Query:
        def join(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(workspace_root=str(workspace))

    class Db:
        def query(self, *_args, **_kwargs):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: Db())
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda _owner: True)

    resolved, rejected = chat_routes._resolve_stored_project_workspace(
        object(), "project-session", "alice",
    )

    assert resolved == str(workspace.resolve())
    assert rejected == ""


def test_companion_owner_honors_established_loopback_bypass(monkeypatch):
    import routes.g1_continuity_routes as project_routes

    request = SimpleNamespace(
        state=SimpleNamespace(api_token=False, current_user=None),
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")

    assert project_routes._owner(request) == "__odysseus_local__"


def test_missing_stored_project_workspace_does_not_override_ordinary_chat(monkeypatch):
    import routes.chat_routes as chat_routes

    class Query:
        def join(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class Db:
        def query(self, *_args, **_kwargs):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: Db())

    assert chat_routes._resolve_stored_project_workspace(
        object(), "general-session", "alice",
    ) == ("", "")


def test_project_continuity_does_not_depend_on_global_flag(monkeypatch):
    import routes.chat_helpers as chat_helpers

    monkeypatch.setattr(chat_helpers, "_continuity_context_enabled", lambda: False)

    assert chat_helpers._continuity_enabled_for_session(SimpleNamespace(scope_kind="project")) is True
    assert chat_helpers._continuity_enabled_for_session(SimpleNamespace(scope_kind="general")) is False


def test_project_delete_route_is_registered():
    from routes.g1_continuity_routes import setup_g1_continuity_routes

    router = setup_g1_continuity_routes(SimpleNamespace())
    routes = {(route.path, method) for route in router.routes for method in route.methods}

    assert ("/api/g1/projects/{project_id}", "DELETE") in routes
    assert ("/api/g1/sessions/{session_id}/harness", "GET") in routes
    assert ("/api/g1/sessions/{session_id}/capability", "PATCH") in routes


def test_project_inspection_capability_is_rejected_without_qualified_sandbox(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import routes.g1_continuity_routes as project_routes
    from core.database import Base, Session as DbSession

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    db = local()
    try:
        db.add(DbSession(
            id="project-session", name="Dust", endpoint_url="http://model", model="m",
            owner="alice", scope_kind="project", project_id="dust", endpoint_id="endpoint",
            harness_kind="qwen", capability_profile="project_read",
        ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(project_routes, "SessionLocal", local)
    monkeypatch.setattr(project_routes, "_owner", lambda _request: "alice")
    monkeypatch.setattr(
        project_routes,
        "inspection_readiness",
        lambda: SimpleNamespace(ready=False),
    )
    manager = SimpleNamespace(sessions={})
    router = project_routes.setup_g1_continuity_routes(manager)
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/g1/sessions/{session_id}/capability"
    )

    with pytest.raises(HTTPException) as raised:
        endpoint(
            "project-session",
            project_routes.CapabilityRequest(capability_profile="project_inspect"),
            object(),
        )

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "sandbox_unavailable"
    db = local()
    try:
        assert db.get(DbSession, "project-session").capability_profile == "project_read"
    finally:
        db.close()


def test_project_read_capability_update_is_owner_scoped_and_qwen_only(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import routes.g1_continuity_routes as project_routes
    from core.database import Base, Session as DbSession

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    db = local()
    try:
        db.add_all([
            DbSession(
                id="qwen-project", name="Dust", endpoint_url="http://model", model="m",
                owner="alice", scope_kind="project", project_id="dust", endpoint_id="endpoint",
                harness_kind="qwen", capability_profile="project_read",
            ),
            DbSession(
                id="native-project", name="Native", endpoint_url="http://model", model="m",
                owner="alice", scope_kind="project", project_id="native", endpoint_id="endpoint",
                harness_kind="native", capability_profile="project_read",
            ),
            DbSession(
                id="bob-project", name="Bob", endpoint_url="http://model", model="m",
                owner="bob", scope_kind="project", project_id="bob", endpoint_id="endpoint",
                harness_kind="qwen", capability_profile="project_read",
            ),
        ])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(project_routes, "SessionLocal", local)
    monkeypatch.setattr(project_routes, "_owner", lambda _request: "alice")
    router = project_routes.setup_g1_continuity_routes(SimpleNamespace(sessions={}))
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/g1/sessions/{session_id}/capability"
    )
    payload = project_routes.CapabilityRequest(capability_profile="project_read")

    result = endpoint("qwen-project", payload, object())
    assert result["requested_capability"] == "project_read"
    assert result["effective_capability"] == "project_read"

    with pytest.raises(HTTPException) as native_error:
        endpoint("native-project", payload, object())
    assert native_error.value.status_code == 409

    with pytest.raises(HTTPException) as owner_error:
        endpoint("bob-project", payload, object())
    assert owner_error.value.status_code == 404


def test_capability_request_rejects_browser_submitted_execution_fields():
    from pydantic import ValidationError

    import routes.g1_continuity_routes as project_routes

    with pytest.raises(ValidationError):
        project_routes.CapabilityRequest(
            capability_profile="project_read",
            command="find .",
            workspace_root="/tmp/other",
            endpoint_id="other-owner-endpoint",
        )


def test_delete_project_without_a_chat_removes_artifacts_too(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import routes.g1_continuity_routes as project_routes
    from core.database import Base, ContinuityArtifact, Project

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    project_id = "orphan-project"
    db = local()
    try:
        db.add(Project(
            id=project_id, owner="alice", name="Dust", workspace_root="/work/dust",
        ))
        db.add(ContinuityArtifact(
            id="artifact", owner="alice", project_id=project_id,
            kind="project_brief_v1", status="active", revision=1,
            payload_json="{}", source_hash="hash",
        ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(project_routes, "SessionLocal", local)
    monkeypatch.setattr(project_routes, "_owner", lambda _request: "alice")
    router = project_routes.setup_g1_continuity_routes(SimpleNamespace(delete_session=lambda _sid: True))
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/g1/projects/{project_id}" and "DELETE" in route.methods
    )

    result = endpoint(project_id, object())

    assert result["deleted"] is True
    db = local()
    try:
        assert db.query(Project).filter(Project.id == project_id).first() is None
        assert db.query(ContinuityArtifact).filter(ContinuityArtifact.project_id == project_id).count() == 0
    finally:
        db.close()


def test_project_creation_response_carries_workspace_for_immediate_ui_binding():
    source = open("routes/g1_continuity_routes.py", encoding="utf-8").read()
    frontend = open("static/app.js", encoding="utf-8").read()

    assert '"workspace_root": workspace_root' in source
    assert "result.session?.workspace_root || workspace_root" in frontend


def test_project_creation_uses_folder_browser_modal_not_window_prompts():
    app = open("static/app.js", encoding="utf-8").read()
    creation = app[app.index("const createCompanionProject"):app.index("window.__odysseusCreateCompanionProject")]

    assert "window.prompt" not in creation
    assert "companion-project-modal" in creation
    assert "workspaceModule.openWorkspaceBrowser({ mode: 'project'" in creation
    assert "name=\"project_name\"" in creation
    assert "name=\"workspace_root\"" in creation


def test_project_sidebar_exposes_fresh_thread_without_feedback_export_clutter():
    source = open("static/js/sessions.js", encoding="utf-8").read()

    assert "/api/g1/projects/${encodeURIComponent(project.id)}/fork" in source
    assert "New project thread · shared brief, fresh transcript" in source
    assert "Export Qwen feedback" not in source


def test_chats_header_has_a_stable_regular_chat_action_and_project_creation_stays_in_projects():
    index = open("static/index.html", encoding="utf-8").read()
    app = open("static/app.js", encoding="utf-8").read()
    sessions = open("static/js/sessions.js", encoding="utf-8").read()
    styles = open("static/style.css", encoding="utf-8").read()

    assert 'id="chats-new-chat-btn"' in index
    assert 'id="companion-new-project-btn"' not in index
    assert "[sidebarNewChatBtn, chatsNewChatBtn]" in app
    assert "await _handleNewChatAction()" in app
    assert "newProject.textContent = '+ New project'" in sessions
    assert "window.__odysseusCreateCompanionProject?.()" in sessions
    assert ".section-header-flex:hover .chats-manage-btn .list-item-plus-label" in styles
    assert "opacity: 0.58 !important" in styles


def test_qwen_feedback_api_is_not_rendered_as_persistent_message_clutter():
    renderer = open("static/js/chatRenderer.js", encoding="utf-8").read()

    assert "function appendQwenFeedback" not in renderer
    assert "qwen-feedback" not in renderer


def test_message_fork_uses_fresh_project_thread_for_project_scopes():
    source = open("static/js/chat.js", encoding="utf-8").read()
    fork = source[source.index("export async function forkFrom"):source.index("export async function checkPendingResearch")]

    assert "session?.scope_kind === 'project'" in fork
    assert "/api/g1/projects/${encodeURIComponent(session.project_id)}/fork" in fork
    assert "shared brief, fresh transcript" in fork


def test_normal_chat_has_no_legacy_shell_controls_or_slash_executor():
    index = open("static/index.html", encoding="utf-8").read()
    app = open("static/app.js", encoding="utf-8").read()
    slash = open("static/js/slashCommands.js", encoding="utf-8").read()

    assert 'id="bash-toggle"' not in index
    assert 'data-ui-key="bash-toggle-btn"' not in index
    assert "bash-toggle-btn" not in app
    assert "/api/shell/exec" not in slash
    assert "_cmdShell" not in slash
    assert "sub: 'bash'" not in slash


def test_json_false_is_preserved_before_backend_intent_detection():
    source = open("routes/chat_routes.py", encoding="utf-8").read()
    parse = source[source.index('allow_bash = form_data.get("allow_bash")'):source.index('use_rag = form_data.get("use_rag")')]

    assert '"allow_bash" in body' in parse
    assert 'allow_bash = body["allow_bash"]' in parse
    assert 'form_data.get("allow_bash") or' not in parse


def test_qwen_project_prompt_treats_this_project_as_mounted_workspace():
    from src.continuity.contracts import ContextBundle, ResolvedScope
    from src.scoped_turn_service import render_context_bundle

    bundle = ContextBundle(
        scope=ResolvedScope(
            owner_id="alice", session_id="session", scope_kind="project",
            project_id="dust", workspace_root="/host/dust",
        ),
        request="What is this project for?",
        companion_profile="",
        transcript_tail=(),
        thread_checkpoint=None,
        primary_project_brief=None,
        related_project_briefs=(),
        episodic_hits=(),
        manifest={},
    )

    prompt = render_context_bundle(bundle)

    assert "already mounted at /workspace" in prompt
    assert "inspect README files" in prompt
    assert "Never ask the user to set a workspace" in prompt


def test_reopening_project_does_not_force_qwen_or_race_native_selection():
    source = open("static/js/sessions.js", encoding="utf-8").read()
    select_session = source[source.index("export async function selectSession"):]

    assert "body: JSON.stringify({ harness_kind: 'qwen' })" not in select_session
    posture = select_session.index("const qwenActive")
    first_yield = select_session.index("await import('./presets.js')")
    assert posture < first_yield


def test_explicit_qwen_toggle_updates_cached_route_before_reloading_sessions():
    source = open("static/app.js", encoding="utf-8").read()
    toggle = source[source.index("const qwenBtn = el('qwen-toggle-btn')"):source.index("const createCompanionProject")]

    assert "cached.harness_kind = result.harness_kind" in toggle
    assert toggle.index("cached.harness_kind = result.harness_kind") < toggle.index("await window.sessionModule?.loadSessions?.()")


def test_qwen_turn_endpoint_rejects_a_native_project_session(monkeypatch):
    import routes.g1_continuity_routes as project_routes

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(
                harness_kind="native", endpoint_id="endpoint", model="model",
            )

    class Db:
        def query(self, *_args, **_kwargs):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(project_routes, "SessionLocal", lambda: Db())

    with pytest.raises(HTTPException) as raised:
        project_routes._stored_route("alice", "session", require_qwen=True)

    assert raised.value.status_code == 409
    assert "disabled" in raised.value.detail


def test_native_agent_route_uses_durable_qwen_harness_fence(monkeypatch):
    import routes.chat_routes as chat_routes

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(scope_kind="project", harness_kind="qwen")

    class Db:
        def query(self, *_args, **_kwargs):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: Db())

    assert chat_routes._qwen_project_harness_enabled("alice", "project-session") is True


def test_project_send_refreshes_harness_from_the_server_before_dispatch():
    source = open("static/js/chat.js", encoding="utf-8").read()
    qwen_branch = source[source.index("let _g15Session ="):source.index("if (_g15AgentMode")]

    assert "/api/g1/sessions/${encodeURIComponent(streamSessionId)}/harness" in qwen_branch
    assert "Object.assign(cached, harness)" in qwen_branch


def test_stateful_chat_and_session_modules_have_one_browser_identity():
    app = open("static/app.js", encoding="utf-8").read()
    index = open("static/index.html", encoding="utf-8").read()

    chat_import = re.search(r"from './js/chat\.js(?P<query>\?v=[^']+)?';", app)
    chat_preload = re.search(r'href="/static/js/chat\.js(?P<query>\?v=[^"]+)?"', index)
    assert chat_import and chat_preload
    assert chat_import.group("query") == chat_preload.group("query")
    assert "from './js/sessions.js';" in app
    assert "sessions.js?v=" not in index
    assert 'type="module" src="/static/js/chat.js' not in index
    assert 'type="module" src="/static/js/sessions.js' not in index
    assert "from './js/workspace.js';" in app
    assert "from './workspace.js';" in open("static/js/sessions.js", encoding="utf-8").read()
    assert "workspace.js?v=" not in app
    assert "workspace.js?v=" not in open("static/js/sessions.js", encoding="utf-8").read()


def test_service_worker_fetches_fresh_app_shell_before_cached_fallback():
    worker = open("static/sw.js", encoding="utf-8").read()
    navigation = worker[worker.index("if (e.request.mode === 'navigate'"):worker.index("// JS/CSS:")]

    assert "return fetch(e.request)" in navigation
    assert ".catch(() => cached)" in navigation
    assert "return cached || network" not in navigation


def test_session_refresh_cannot_jump_back_to_a_stale_hash():
    source = open("static/js/sessions.js", encoding="utf-8").read()
    chooser_start = source.index("const hasPendingChat")
    chooser = source[chooser_start:source.index("_skipAutoSelect = false", chooser_start)]
    selection = source[source.index("export async function selectSession"):]

    assert chooser.index("currentSessionId && activeSessions.some") < chooser.index("hashId && activeSessions.some")
    assert "history.replaceState(null, '', `${window.location.pathname}${window.location.search}${nextHash}`)" in selection


def test_deterministic_home_creation_is_serialized_per_scope(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    import routes.g1_continuity_routes as project_routes

    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_create(*_args, **_kwargs):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with state_lock:
            active -= 1
        return {"id": "same-home"}

    monkeypatch.setattr(project_routes, "_get_or_create_home_locked", fake_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _n: project_routes._get_or_create_home(
                object(), owner="concurrent-home-owner", scope_kind="personal", project=None,
                endpoint_id="endpoint", model="model",
            ),
            range(2),
        ))

    assert results == [{"id": "same-home"}, {"id": "same-home"}]
    assert max_active == 1


def test_builtin_home_is_created_once_then_reopened(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import routes.g1_continuity_routes as project_routes
    from core.database import Base, Session as DbSession

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)

    class Manager:
        def __init__(self):
            self.sessions = {}

        def create_session(self, session_id, name, endpoint_url, model, owner=None, **_kwargs):
            db = local()
            try:
                db.add(DbSession(
                    id=session_id, name=name, endpoint_url=endpoint_url,
                    model=model, headers={}, owner=owner,
                ))
                db.commit()
            finally:
                db.close()
            session = SimpleNamespace(
                id=session_id, name=name, endpoint_url=endpoint_url,
                model=model, owner=owner, headers={},
            )
            self.sessions[session_id] = session
            return session

    monkeypatch.setattr(project_routes, "SessionLocal", local)
    monkeypatch.setattr(project_routes, "_endpoint", lambda *_args: ("http://model.test/v1", {}))
    manager = Manager()

    first = project_routes._get_or_create_home(
        manager, owner="new-home-owner", scope_kind="personal", project=None,
        endpoint_id="endpoint", model="model",
    )
    second = project_routes._get_or_create_home(
        manager, owner="new-home-owner", scope_kind="personal", project=None,
        endpoint_id="endpoint", model="model",
    )

    assert first["id"] == second["id"]
    assert first["scope_kind"] == "personal"
    assert first["is_scope_primary"] is True
    db = local()
    try:
        assert db.query(DbSession).filter(DbSession.owner == "new-home-owner").count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_feedback_updates_hydrated_history_as_well_as_sqlite(monkeypatch):
    import json

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import routes.g1_continuity_routes as project_routes
    from core.database import Base, ChatMessage as DbMessage, Session as DbSession

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    db = local()
    try:
        db.add(DbSession(
            id="feedback-session", name="Project", endpoint_url="http://model.test/v1",
            model="model", headers={}, owner="alice", scope_kind="project",
        ))
        db.add(DbMessage(
            id="feedback-message", session_id="feedback-session", role="assistant",
            content="Answer", meta_data=json.dumps({"harness": "qwen"}),
        ))
        db.commit()
    finally:
        db.close()

    cached_message = SimpleNamespace(
        metadata={"_db_id": "feedback-message", "harness": "qwen"},
    )
    manager = SimpleNamespace(sessions={
        "feedback-session": SimpleNamespace(history=[cached_message]),
    })
    monkeypatch.setattr(project_routes, "SessionLocal", local)
    monkeypatch.setattr(project_routes, "_owner", lambda _request: "alice")
    router = project_routes.setup_g1_continuity_routes(manager)
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/g1/feedback")

    result = await endpoint(
        project_routes.FeedbackRequest(
            message_id="feedback-message", rating="helpful", note="It helped",
        ),
        object(),
    )

    assert result == {"ok": True}
    assert cached_message.metadata["g1_feedback"] == {
        "rating": "helpful", "note": "It helped", "harness": "qwen",
    }
    db = local()
    try:
        saved = db.query(DbMessage).filter(DbMessage.id == "feedback-message").one()
        assert json.loads(saved.meta_data)["g1_feedback"] == cached_message.metadata["g1_feedback"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_stream_admission_rejects_a_second_request_before_generator_starts(monkeypatch, tmp_path):
    import routes.g1_continuity_routes as project_routes

    binary = tmp_path / "qwen"
    binary.write_text("stub")
    monkeypatch.setenv("ODYSSEUS_QWEN_HARNESS", "1")
    monkeypatch.setenv("ODYSSEUS_QWEN_BINARY", str(binary))
    monkeypatch.setattr(project_routes.shutil, "which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    monkeypatch.setattr(project_routes, "_stored_route", lambda *_args, **_kwargs: ("endpoint", "model"))
    monkeypatch.setattr(project_routes, "_stored_capability", lambda *_args, **_kwargs: ("project_read", "project_read"))
    monkeypatch.setattr(project_routes, "_qwen_binary", lambda: binary)
    router = project_routes.setup_g1_continuity_routes(SimpleNamespace())
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/g1/project-turn/stream"
    )
    payload = project_routes.G1TurnRequest(session_id="same-session", message="question")
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice", api_token=False))

    first = await endpoint(payload, request)
    assert first.media_type == "text/event-stream"
    with pytest.raises(HTTPException) as raised:
        await endpoint(payload, request)

    assert raised.value.status_code == 409


@pytest.mark.asyncio
async def test_stop_cancels_an_admitted_turn_before_its_generator_starts(monkeypatch, tmp_path):
    import routes.g1_continuity_routes as project_routes

    binary = tmp_path / "qwen"
    binary.write_text("stub")
    monkeypatch.setenv("ODYSSEUS_QWEN_HARNESS", "1")
    monkeypatch.setenv("ODYSSEUS_QWEN_BINARY", str(binary))
    monkeypatch.setattr(project_routes.shutil, "which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    monkeypatch.setattr(project_routes, "_stored_route", lambda *_args, **_kwargs: ("endpoint", "model"))
    monkeypatch.setattr(project_routes, "_stored_capability", lambda *_args, **_kwargs: ("project_read", "project_read"))
    monkeypatch.setattr(project_routes, "_qwen_binary", lambda: binary)

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return ("project-session",)

    class Db:
        def query(self, *_args, **_kwargs):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(project_routes, "SessionLocal", lambda: Db())
    router = project_routes.setup_g1_continuity_routes(SimpleNamespace())
    stream = next(route.endpoint for route in router.routes if route.path == "/api/g1/project-turn/stream")
    stop = next(route.endpoint for route in router.routes if route.path == "/api/g1/project-turn/{session_id}/stop")
    payload = project_routes.G1TurnRequest(session_id="project-session", message="question")
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice", api_token=False))

    response = await stream(payload, request)
    stopped = await stop("project-session", request)

    assert stopped == {"stopped": True}
    chunks = [chunk async for chunk in response.body_iterator]
    assert any('"code":"cancelled"' in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_stop_endpoint_cannot_target_another_owners_session(monkeypatch):
    import routes.g1_continuity_routes as project_routes

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class Db:
        def query(self, *_args, **_kwargs):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(project_routes, "SessionLocal", lambda: Db())
    router = project_routes.setup_g1_continuity_routes(SimpleNamespace())
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/g1/project-turn/{session_id}/stop"
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice", api_token=False))

    with pytest.raises(HTTPException) as raised:
        await endpoint("bob-session", request)

    assert raised.value.status_code == 404


def test_readiness_reports_components_without_exposing_paths(monkeypatch, tmp_path):
    import routes.g1_continuity_routes as project_routes

    binary = tmp_path / "qwen"
    binary.write_text("stub")
    monkeypatch.setenv("ODYSSEUS_QWEN_HARNESS", "1")
    monkeypatch.setenv("ODYSSEUS_QWEN_BINARY", str(binary))
    monkeypatch.setattr(project_routes.shutil, "which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    router = project_routes.setup_g1_continuity_routes(SimpleNamespace())
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/g1/status")
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice", api_token=False))

    result = endpoint(request)

    assert result["qwen_ready"] is True
    assert result["components"]["qwen_binary"] is True
    assert result["components"]["bubblewrap"] is True
    assert result["components"]["podman"] in {True, False}
    assert result["components"]["sandbox_image"] is False
    assert result["inspection"]["pinned_image"] is False
    assert result["inspection"]["ready"] is False
    assert result["inspection"]["sandbox_probe"] is False
    assert str(binary) not in repr(result)


@pytest.mark.asyncio
async def test_qwen_turn_is_rejected_before_admission_when_containment_is_not_ready(monkeypatch, tmp_path):
    import routes.g1_continuity_routes as project_routes

    monkeypatch.setenv("ODYSSEUS_QWEN_HARNESS", "1")
    monkeypatch.setenv("ODYSSEUS_QWEN_BINARY", str(tmp_path / "missing-qwen"))
    monkeypatch.setattr(project_routes.shutil, "which", lambda _name: None)
    monkeypatch.setattr(project_routes, "_stored_route", lambda *_args, **_kwargs: ("endpoint", "model"))
    monkeypatch.setattr(project_routes, "_stored_capability", lambda *_args, **_kwargs: ("project_read", "project_read"))
    router = project_routes.setup_g1_continuity_routes(SimpleNamespace())
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/g1/project-turn/stream")
    payload = project_routes.G1TurnRequest(session_id="project-session", message="question")
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice", api_token=False))

    with pytest.raises(HTTPException) as raised:
        await endpoint(payload, request)

    assert raised.value.status_code == 503
    assert "Qwen binary" in raised.value.detail
    assert "Bubblewrap" in raised.value.detail
    assert str(tmp_path) not in raised.value.detail


def test_companion_sidebar_renders_safe_qwen_readiness_status():
    source = open("static/js/sessions.js", encoding="utf-8").read()

    assert "/api/g1/status" in source
    assert "Qwen ready · project read-only" in source
    assert "inspection unavailable" in source
    assert "Qwen setup needed" in source
    assert "components?.qwen_binary" in source
    assert "components?.bubblewrap" in source


def test_qwen_timeout_error_is_specific_and_safe():
    import routes.g1_continuity_routes as project_routes

    payload = project_routes._qwen_error_payload(
        RuntimeError("Qwen turn failed: turn_error:prompt_deadline_exceeded provider-secret")
    )
    assert payload == {
        "code": "turn_timeout",
        "detail": (
            "Qwen kept inspecting but did not produce a final answer before the turn limit. "
            "The request was not necessarily too large, and no project files were changed."
        ),
    }
    assert "provider-secret" not in repr(payload)


def test_qwen_patch_errors_are_actionable_and_safe():
    import routes.g1_continuity_routes as project_routes
    from src.project_patches import PatchError

    payload = project_routes._qwen_error_payload(
        PatchError("proposal_invalid", "raw model output with provider-secret")
    )

    assert payload["code"] == "proposal_invalid"
    assert "valid structured patch proposal" in payload["detail"]
    assert "No files were changed" in payload["detail"]
    assert "provider-secret" not in repr(payload)


def test_companion_scope_is_disclosed_above_the_composer():
    index = open("static/index.html", encoding="utf-8").read()
    sessions = open("static/js/sessions.js", encoding="utf-8").read()

    assert index.index('id="companion-scope-banner"') < index.index('class="chat-input-bar"')
    assert "Project · ${projectName}" in sessions
    assert "effectiveCapability === 'project_inspect'" in sessions
    assert "'sandboxed inspection' : 'read-only'" in sessions
    assert "Native · continuity context · workspace tools off" in sessions
    assert "Native · personal scope" in sessions
    assert "Native · Qwen coming next" in sessions


def test_sidebar_research_recovery_uses_quiet_idle_status():
    source = open("static/js/sessions.js", encoding="utf-8").read()

    polling = source[source.index("function _startResearchPolling"):source.index("export function markResearching")]
    assert "/api/research/status/${sid}?quiet=1" in polling
