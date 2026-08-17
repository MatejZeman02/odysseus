import json

import httpx
import pytest

from src.model_bridge import ModelBridge, ModelBridgeRuntime


@pytest.mark.asyncio
async def test_bridge_uses_exact_owner_route_and_hides_provider_credentials():
    seen = {}

    def upstream(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "completion", "choices": []})

    bridge = ModelBridge(
        resolver=lambda endpoint_id, model, owner: ("https://provider.test/v1/chat/completions", model, {"Authorization": "Bearer secret"}),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kwargs),
    )
    route = bridge.issue_route(owner="alice", endpoint_id="endpoint-a", model="fixed-model", run_id="run-1")
    assert route.run_id == "run-1"
    assert route.token != route.run_id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url="http://bridge") as client:
        response = await client.post(
            "/v1/chat/completions", headers={"Authorization": f"Bearer {route.token}", "X-Provider-Key": "leak"},
            json={"model": "attacker-model", "messages": [{"role": "user", "content": "hello"}]},
        )
        replay = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {route.token}"}, json={})

    assert response.status_code == 200
    assert replay.status_code == 401
    assert seen["url"] == "https://provider.test/v1/chat/completions"
    assert seen["body"]["model"] == "fixed-model"
    assert seen["headers"]["authorization"] == "Bearer secret"
    assert "x-provider-key" not in seen["headers"]


@pytest.mark.asyncio
async def test_bridge_adds_server_owned_cache_affinity_for_local_upstream():
    seen = {}

    def upstream(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    bridge = ModelBridge(
        resolver=lambda endpoint_id, model, owner: (
            "http://127.0.0.1:8080/v1/chat/completions", model, {}
        ),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kwargs),
    )
    route = bridge.issue_route(
        owner="alice", endpoint_id="endpoint-a", model="fixed-model", run_id="session-a"
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url="http://bridge") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {route.token}"},
            json={"messages": [{"role": "user", "content": "hello"}], "session_id": "worker-choice"},
        )

    assert response.status_code == 200
    assert seen["session_id"] == "session-a"
    assert seen["cache_prompt"] is True


@pytest.mark.asyncio
async def test_bridge_strips_worker_cache_fields_for_cloud_upstream():
    seen = {}

    def upstream(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    bridge = ModelBridge(
        resolver=lambda endpoint_id, model, owner: (
            "https://provider.test/v1/chat/completions", model, {}
        ),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kwargs),
    )
    route = bridge.issue_route(owner="alice", endpoint_id="endpoint-a", model="fixed-model", run_id="session-a")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url="http://bridge") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {route.token}"},
            json={"messages": [], "session_id": "worker-choice", "cache_prompt": True},
        )

    assert response.status_code == 200
    assert "session_id" not in seen
    assert "cache_prompt" not in seen


@pytest.mark.asyncio
async def test_bridge_refuses_expired_or_model_drift_routes():
    bridge = ModelBridge(resolver=lambda *args, **kwargs: None)
    expired = bridge.issue_route(owner="alice", endpoint_id="endpoint-a", model="fixed-model", ttl_seconds=1)
    bridge._routes[expired.token] = expired.__class__(expired.run_id, expired.token, expired.owner, expired.endpoint_id, expired.model, 0, 1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url="http://bridge") as client:
        response = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {expired.token}"}, json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bridge_rejects_non_loopback_and_oversized_requests():
    bridge = ModelBridge(max_body_bytes=8)
    route = bridge.issue_route(owner="alice", endpoint_id="endpoint-a", model="fixed-model")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app, client=("10.0.0.8", 1234)), base_url="http://bridge") as client:
        response = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {route.token}"}, json={})
    assert response.status_code == 403

    route = bridge.issue_route(owner="alice", endpoint_id="endpoint-a", model="fixed-model")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url="http://bridge") as client:
        response = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {route.token}"}, content=b'{"long":true}')
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_bridge_returns_a_safe_openai_error_when_the_provider_is_offline():
    async def offline_handler(_request):
        raise httpx.ConnectError("http://provider.internal:9999 refused; token=secret")

    bridge = ModelBridge(
        resolver=lambda endpoint_id, model, owner: ("https://provider.test/v1/chat/completions", model, {"Authorization": "Bearer secret"}),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(offline_handler), **kwargs),
    )
    route = bridge.issue_route(owner="alice", endpoint_id="endpoint-a", model="fixed-model")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url="http://bridge") as client:
        response = await client.post(
            "/v1/chat/completions", headers={"Authorization": f"Bearer {route.token}"}, json={"messages": []},
        )

    assert response.status_code == 502
    assert response.json() == {"error": {
        "message": "Model provider request failed", "type": "provider_error", "code": "provider_failed",
    }}
    assert "provider.internal" not in response.text
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_bridge_runtime_owns_a_dedicated_loopback_listener():
    runtime = ModelBridgeRuntime(ModelBridge())
    base_url = await runtime.start()
    try:
        assert base_url.startswith("http://127.0.0.1:")
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{base_url}/chat/completions", json={})
        assert response.status_code == 401
    finally:
        await runtime.stop()
