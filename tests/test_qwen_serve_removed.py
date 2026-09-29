"""Qwen Serve stays removed, and nothing still reaches for it.

The owner removed the read-only Qwen worker on 2026-09-25. Project chats run
on the native agent with per-chat grants. These checks stop a later merge or a
stale edit from bringing back a module, a route or a browser call that belonged
to the worker.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]

_REMOVED_MODULES = (
    "src.qwen_harness",
    "src.qwen_supervisor",
    "src.qwen_inspection",
    "src.model_bridge",
    "src.scoped_turn_service",
    "src.companion_runs",
)
_REMOVED_ENDPOINTS = (
    "/api/g1/project-turn",
    "/patch-turn/",
    "/api/g1/status",
    "/harness",
    "/api/g1/feedback",
    "/evaluation",
)


@pytest.mark.parametrize("module", _REMOVED_MODULES)
def test_removed_worker_modules_stay_removed(module):
    assert importlib.util.find_spec(module) is None


def test_companion_routers_serve_no_worker_route(monkeypatch):
    import routes.companion_patch_routes as patch_routes
    from routes.g1_continuity_routes import setup_g1_continuity_routes

    monkeypatch.setattr(patch_routes, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(patch_routes, "recover_applying_change_sets", lambda _db: 0)
    paths = [route.path for route in setup_g1_continuity_routes(SimpleNamespace()).routes]
    paths += [route.path for route in patch_routes.setup_companion_patch_routes().routes]

    for path in paths:
        assert not any(removed in path for removed in _REMOVED_ENDPOINTS), path
    assert not any(path.endswith("/capability") for path in paths)
    # What replaced the worker is still served.
    assert "/api/g1/sessions/{session_id}/chat-capabilities" in paths
    assert "/api/companion/patches/{patch_id}/rollback" in paths


def test_the_browser_calls_no_removed_endpoint():
    static = ROOT / "static"
    sources = [static / "index.html", static / "app.js", static / "sw.js", *sorted((static / "js").rglob("*.js"))]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for removed in _REMOVED_ENDPOINTS:
            assert removed not in text, f"{source.relative_to(ROOT)} still calls {removed}"
