from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.g1_continuity_routes as route_module


def _request(owner="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=owner, api_token=False))


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
