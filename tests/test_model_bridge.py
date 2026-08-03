import json

import httpx
import pytest

from src.model_bridge import ModelBridge


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
    route = bridge.issue_route(owner="alice", endpoint_id="endpoint-a", model="fixed-model")
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
async def test_bridge_refuses_expired_or_model_drift_routes():
    bridge = ModelBridge(resolver=lambda *args, **kwargs: None)
    expired = bridge.issue_route(owner="alice", endpoint_id="endpoint-a", model="fixed-model", ttl_seconds=1)
    bridge._routes[expired.token] = expired.__class__(expired.token, expired.run_id, expired.owner, expired.endpoint_id, expired.model, 0, 1)
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
