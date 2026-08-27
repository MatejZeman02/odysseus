from src.companion_capabilities import (
    SANDBOX_READ,
    SYSTEM_OBSERVE,
    WEB_SEARCH,
    WORKSPACE_READ,
    can_change,
    capability_payload,
    defaults_for_scope,
    normalize,
)


def test_scope_defaults_keep_host_and_sandbox_authority_offside_by_default():
    personal = defaults_for_scope("personal")
    project = defaults_for_scope("project")
    assert personal[WEB_SEARCH] is False
    assert personal[SYSTEM_OBSERVE] is False
    assert personal[SANDBOX_READ] is False
    assert project[WEB_SEARCH] is True
    assert project[WORKSPACE_READ] is True
    assert project[SYSTEM_OBSERVE] is False


def test_payload_distinguishes_requested_from_effective_readiness():
    payload = capability_payload(
        {SANDBOX_READ: True, WORKSPACE_READ: True},
        scope_kind="personal", workspace_attached=False, sandbox_ready=False,
    )
    assert payload[SANDBOX_READ]["requested"] is True
    assert payload[SANDBOX_READ]["effective"] is False
    assert "Podman" in payload[SANDBOX_READ]["reason"]
    assert payload[WORKSPACE_READ]["requested"] is True
    assert payload[WORKSPACE_READ]["effective"] is False


def test_malformed_grants_are_discarded_and_changes_are_server_validated():
    grants = normalize({WEB_SEARCH: 1, "made_up": True}, scope_kind="general")
    assert grants[WEB_SEARCH] is False
    assert "made_up" not in grants
    assert can_change("made_up", scope_kind="general", workspace_attached=False, sandbox_ready=False)[0] is False
    assert can_change(WORKSPACE_READ, scope_kind="general", workspace_attached=False, sandbox_ready=True)[0] is False
    assert can_change(SYSTEM_OBSERVE, scope_kind="general", workspace_attached=False, sandbox_ready=False)[0] is True


def test_companion_ui_uses_one_capability_drawer_and_native_documents_for_artifacts():
    index = open("static/index.html", encoding="utf-8").read()
    sessions = open("static/js/sessions.js", encoding="utf-8").read()
    app = open("static/app.js", encoding="utf-8").read()
    assert 'id="overflow-chat-capabilities-btn"' in index
    assert "Chat capabilities" in index
    assert "Companion memory" in index
    assert "/chat-capabilities" in sessions
    assert "odysseus:tool-toggle" in sessions
    assert "companion-artifact-editor" not in sessions
    assert "companion-open-documents" in sessions
    assert "documentApi.openLibrary({tab: 'documents'})" in sessions
    assert "odysseus:tool-toggle" in app
