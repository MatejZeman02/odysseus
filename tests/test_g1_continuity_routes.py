from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.g1_continuity_routes as route_module


def _endpoint(router):
    return next(route.endpoint for route in router.routes if route.path.endswith("/project-turn"))


def _payload():
    return route_module.G1TurnRequest(
        session_id="s", message="question", endpoint_id="e", model="m",
    )


def _request(owner="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=owner, api_token=False))


@pytest.mark.asyncio
async def test_g1_route_is_absent_by_default(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_QWEN_HARNESS", raising=False)
    with pytest.raises(HTTPException) as exc:
        await _endpoint(route_module.setup_g1_continuity_routes(object()))(_payload(), _request())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_g1_route_runs_explicitly_enabled_service(monkeypatch, tmp_path):
    binary = tmp_path / "qwen"
    binary.write_text("stub")
    monkeypatch.setenv("ODYSSEUS_QWEN_HARNESS", "1")
    monkeypatch.setenv("ODYSSEUS_QWEN_BINARY", str(binary))
    seen = {}

    class Service:
        def __init__(self, manager, *, qwen_binary: Path):
            seen["manager"] = manager
            seen["binary"] = qwen_binary

        async def run(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(answer="answer", manifest={"scope": "project"}, dust_unchanged=True)

    monkeypatch.setattr(route_module, "ReadOnlyScopedTurnService", Service)
    manager = object()
    result = await _endpoint(route_module.setup_g1_continuity_routes(manager))(_payload(), _request())
    assert result == {
        "answer": "answer",
        "context_manifest": {"scope": "project"},
        "workspace_unchanged": True,
        "requested_capability": "project_read",
        "effective_capability": "project_read",
        "qwen_process": None,
    }
    assert seen["manager"] is manager
    assert seen["binary"] == binary
    assert seen["owner"] == "alice"
    assert seen["session_id"] == "s"


def test_companion_owner_refuses_a_bearer_token():
    """A token resolves to the admin who minted it. Owner decisions need the owner."""
    token_request = SimpleNamespace(
        state=SimpleNamespace(current_user="admin", api_token=True, api_token_owner="admin"),
    )

    with pytest.raises(HTTPException) as exc:
        route_module._owner(token_request)

    assert exc.value.status_code == 403
    assert route_module._owner(_request("alice")) == "alice"


def test_every_companion_router_shares_the_token_refusing_owner():
    import routes.companion_memory_routes as memory_routes
    import routes.companion_patch_routes as patch_routes
    import routes.computer_help_routes as computer_routes

    for module in (memory_routes, patch_routes, computer_routes):
        assert module._owner is route_module._owner, module.__name__
