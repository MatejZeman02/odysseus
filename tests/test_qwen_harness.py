import json
import stat

import httpx
import pytest

from src.qwen_harness import QwenHarnessError, QwenServeClient, create_disposable_config


def test_disposable_qwen_config_is_private_and_contains_only_bridge_contract(tmp_path):
    config = create_disposable_config(
        root=tmp_path / "worker", bridge_url="http://127.0.0.1:9191/v1", bridge_model="odysseus-bridge", ephemeral_bridge_token="secret",
    )
    payload = json.loads(config.path.read_text())
    assert stat.S_IMODE(config.path.stat().st_mode) == 0o600
    assert payload["modelProviders"]["openai"][0] == {
        "id": "odysseus-bridge", "baseUrl": "http://127.0.0.1:9191/v1", "envKey": "ODYSSEUS_QWEN_BRIDGE_TOKEN"
    }
    assert "secret" not in config.path.read_text()
    assert payload["tools"]["allowed"] == []
    assert payload["mcpServers"] == {}


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
            return httpx.Response(201, json={"id": "qwen-session"})
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(daemon)) as raw:
        client = QwenServeClient("http://127.0.0.1:4170", "daemon-token", client=raw)
        assert (await client.verify_capabilities())["features"][-1] == "require_auth"
        assert await client.create_session(cwd="/read-only", model_service_id="odysseus-bridge") == "qwen-session"
