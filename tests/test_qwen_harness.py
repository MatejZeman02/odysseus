import json
import stat

import httpx
import pytest

from src.qwen_harness import QwenHarnessError, QwenServeClient, build_read_only_launch, create_disposable_config, normalize_event


def test_disposable_qwen_config_is_private_and_contains_only_bridge_contract(tmp_path):
    config = create_disposable_config(
        root=tmp_path / "worker", bridge_url="http://127.0.0.1:9191/v1", bridge_model="odysseus-bridge", ephemeral_bridge_token="secret",
    )
    payload = json.loads(config.path.read_text())
    assert stat.S_IMODE(config.path.stat().st_mode) == 0o600
    assert config.path == tmp_path / "worker" / "home" / ".qwen" / "settings.json"
    assert payload["modelProviders"]["openai"][0] == {
        "id": "odysseus-bridge", "baseUrl": "http://127.0.0.1:9191/v1", "envKey": "ODYSSEUS_QWEN_BRIDGE_TOKEN"
    }
    assert "secret" not in config.path.read_text()
    assert payload["tools"]["approvalMode"] == "plan"
    assert payload["tools"]["core"] == ["read_file", "grep_search", "glob", "list_directory"]
    assert payload["tools"]["computerUse"]["enabled"] is False
    assert "Shell" in payload["permissions"]["deny"]
    assert "Edit" in payload["permissions"]["deny"]
    assert payload["memory"]["enableManagedAutoMemory"] is False
    assert payload["mcpServers"] == {}


def test_launch_spec_is_loopback_private_and_only_exposes_ephemeral_bridge_token(tmp_path):
    config = create_disposable_config(
        root=tmp_path / "worker", bridge_url="http://127.0.0.1:9191/v1", bridge_model="odysseus-bridge", ephemeral_bridge_token="unused",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = build_read_only_launch(binary="/opt/qwen/bin/qwen", config=config, workspace_root=workspace, bridge_token="ephemeral", port=4170)
    assert spec.command[:6] == ("/opt/qwen/bin/qwen", "serve", "--hostname", "127.0.0.1", "--port", "4170")
    assert "--no-web" in spec.command and "--require-auth" in spec.command
    assert "--safe-mode" in spec.command
    assert spec.command[spec.command.index("--max-sessions") + 1] == "1"
    assert spec.command[spec.command.index("--max-pending-prompts-per-session") + 1] == "1"
    assert spec.environment["HOME"] == str(config.home)
    assert spec.environment[config.token_env_key] == "ephemeral"
    assert spec.environment["OPENAI_API_KEY"] == "ephemeral"
    assert spec.environment["OPENAI_BASE_URL"] == "http://127.0.0.1:9191/v1"
    assert spec.environment["QWEN_CODE_SAFE_MODE"] == "true"
    assert "provider.example" not in " ".join(spec.command)


@pytest.mark.asyncio
async def test_qwen_client_fails_closed_when_safe_capabilities_are_missing():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"features": ["health", "capabilities"]}))
    async with httpx.AsyncClient(transport=transport) as raw:
        client = QwenServeClient("http://127.0.0.1:4170", "token", client=raw)
        with pytest.raises(QwenHarnessError, match="session_create"):
            await client.verify_capabilities()


@pytest.mark.asyncio
async def test_qwen_client_authenticates_capabilities_and_creates_session():
    def daemon(request):
        assert request.headers["authorization"] == "Bearer daemon-token"
        if request.url.path == "/capabilities":
            return httpx.Response(200, json={"features": ["health", "capabilities", "session_create", "session_events", "require_auth"]})
        if request.url.path == "/session":
            assert json.loads(request.content) == {"cwd": "/read-only", "sessionScope": "thread"}
            return httpx.Response(201, json={"sessionId": "qwen-session"})
        if request.url.path.endswith("/prompt"):
            assert json.loads(request.content) == {"prompt": [{"type": "text", "text": "hello"}]}
            return httpx.Response(202, json={"promptId": "p1", "lastEventId": 7})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(204)
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(daemon)) as raw:
        client = QwenServeClient("http://127.0.0.1:4170", "daemon-token", client=raw)
        assert (await client.verify_capabilities())["features"][-1] == "require_auth"
        assert await client.create_session(cwd="/read-only") == "qwen-session"
        assert await client.prompt("qwen-session", "hello") == ("p1", "7")
        await client.cancel("qwen-session")


@pytest.mark.asyncio
async def test_qwen_event_stream_sends_and_advances_replay_cursor():
    def daemon(request):
        assert request.headers["last-event-id"] == "7"
        return httpx.Response(200, text=(
            'id: 8\n'
            'data: {"type":"session_update","data":{}}\n\n'
            'id: 9\n'
            'data: {"type":"turn_complete","data":{"stopReason":"end_turn"}}\n\n'
        ), headers={"content-type": "text/event-stream"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(daemon)) as raw:
        client = QwenServeClient("http://127.0.0.1:4170", "token", client=raw)
        events = [event async for event in client.events("session", last_event_id="7")]
    assert [event["_sse_id"] for event in events] == ["8", "9"]


def test_unknown_qwen_events_are_safe_and_terminal_events_are_normalized():
    assert normalize_event({"type": "turn_complete", "data": {"stopReason": "end_turn"}})["kind"] == "completion"
    assert normalize_event({"type": "new_future_event", "data": {"x": 1}}) == {
        "kind": "unknown", "event_type": "new_future_event", "data": {"x": 1},
    }
