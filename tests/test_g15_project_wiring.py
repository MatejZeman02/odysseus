import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.helpers.js_modules import session_ui_source
from tests.helpers.stylesheets import app_css



def _browser_request():
    return SimpleNamespace(state=SimpleNamespace(api_token=False))


def _token_request():
    return SimpleNamespace(state=SimpleNamespace(api_token=True))

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
        _browser_request(), "project-session", "alice",
    )

    assert resolved == str(workspace.resolve())
    assert rejected == ""

    # A bearer token resolves to the admin who minted it. It must not inherit
    # that admin's checkout binding, and must not learn the stored path.
    assert chat_routes._resolve_stored_project_workspace(
        _token_request(), "project-session", "alice",
    ) == ("", "")


def test_native_project_workspace_rejects_a_swapped_symlink(monkeypatch, tmp_path):
    import routes.chat_routes as chat_routes
    import src.tool_security as tool_security

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    swapped = tmp_path / "workspace-link"
    swapped.symlink_to(workspace, target_is_directory=True)

    class Query:
        def join(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(workspace_root=str(swapped))

    class Db:
        def query(self, *_args, **_kwargs):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: Db())
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda _owner: True)

    assert chat_routes._resolve_stored_project_workspace(
        _browser_request(), "project-session", "alice",
    ) == ("", str(swapped))


def test_companion_owner_honors_established_loopback_bypass(monkeypatch):
    import routes.g1_continuity_routes as project_routes

    request = SimpleNamespace(
        state=SimpleNamespace(api_token=False, current_user=None),
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")

    assert project_routes._owner(request) == "__odysseus_local__"


def test_project_creation_rejects_a_symlinked_workspace_root(tmp_path):
    import routes.g1_continuity_routes as project_routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    swapped = tmp_path / "workspace-link"
    swapped.symlink_to(workspace, target_is_directory=True)

    with pytest.raises(HTTPException, match="not a symlink"):
        project_routes._safe_workspace(str(swapped))


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
        _browser_request(), "general-session", "alice",
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


def test_capability_request_rejects_browser_submitted_execution_fields():
    from pydantic import ValidationError

    import routes.g1_continuity_routes as project_routes

    with pytest.raises(ValidationError):
        project_routes.ChatCapabilityRequest(
            name="project_shell",
            enabled=True,
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
    source = session_ui_source()

    assert "/api/g1/projects/${encodeURIComponent(project.id)}/fork" in source
    assert "New project thread · shared brief, fresh transcript" in source
    assert "Export Qwen feedback" not in source


def test_chats_header_has_a_stable_regular_chat_action_and_project_creation_stays_in_projects():
    index = open("static/index.html", encoding="utf-8").read()
    app = open("static/app.js", encoding="utf-8").read()
    sessions = session_ui_source()
    styles = app_css()

    assert 'id="chats-new-chat-btn"' in index
    assert 'id="companion-new-project-btn"' not in index
    assert "[sidebarNewChatBtn, chatsNewChatBtn]" in app
    assert "await _handleNewChatAction()" in app
    assert "newProject.textContent = '+ New project'" in sessions
    assert "window.__odysseusCreateCompanionProject?.()" in sessions
    assert ".section-header-flex:hover .chats-manage-btn .list-item-plus-label" in styles
    # The manage button fades at rest and reveals on section hover. Upstream
    # owns the resting value, so assert the rule exists rather than its number.
    manage = re.search(r"#sessions-section \.chats-manage-btn \{([^}]*)\}", styles)
    assert manage and "opacity:" in manage.group(1)


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


def test_stateful_chat_and_session_modules_have_one_browser_identity():
    app = open("static/app.js", encoding="utf-8").read()
    index = open("static/index.html", encoding="utf-8").read()

    chat_import = re.search(r"from './js/chat\.js(?P<query>\?v=[^']+)?';", app)
    assert chat_import
    assert "from './js/sessions.js';" in app
    # A module is one instance per URL, query string included, so two URLs for
    # one file give each copy its own current-session and streaming state. The
    # fork used to forbid the top-level script tags outright. Upstream loads
    # these modules both ways on purpose, so the browser can start fetching
    # them before app.js has been parsed, and pins the keys together in
    # tests/test_stream_completion_scroll_stability.py. Check the identity the
    # fork depends on instead: every URL index.html gives a stateful module,
    # preload and tag alike, is the one app.js imports.
    chat_urls = set(re.findall(r'"/static/js/chat\.js([^"]*)"', index))
    assert chat_urls == {chat_import.group("query") or ""}
    session_urls = set(re.findall(r'"/static/js/sessions\.js([^"]*)"', index))
    assert session_urls == {""}
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


def test_companion_scope_is_disclosed_above_the_composer():
    index = open("static/index.html", encoding="utf-8").read()
    sessions = session_ui_source()

    assert index.index('id="companion-scope-banner"') < index.index('class="chat-input-bar"')
    assert "Project · ${projectName}" in sessions
    # A project states what its grants allow, not a fixed label.
    assert "_accessSummary(meta.chat_capabilities)" in sessions
    assert "Native · personal scope" in sessions
    assert "Native · diagnostics" in sessions


def test_sidebar_research_recovery_uses_quiet_idle_status():
    source = open("static/js/sessions.js", encoding="utf-8").read()

    polling = source[source.index("function _startResearchPolling"):source.index("export function markResearching")]
    assert "/api/research/status/${sid}?quiet=1" in polling
