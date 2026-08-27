"""Operator CLI coverage for the fixed Computer Help qualification fixture."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "qualify_computer_sandbox", ROOT / "scripts" / "qualify_computer_sandbox.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qualification_cli_prints_only_safe_readiness(monkeypatch, capsys):
    module = _module()

    class State:
        def public_payload(self):
            return {"computer_assist_ready": False, "computer_assist_reason": "pinned_sandbox_image_required"}

    monkeypatch.setattr(module, "readiness", lambda: State())
    assert module.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "readiness": {"computer_assist_ready": False, "computer_assist_reason": "pinned_sandbox_image_required"},
    }


def test_qualification_cli_has_no_user_controlled_execution_inputs(monkeypatch, capsys):
    module = _module()

    class State:
        def public_payload(self):
            return {"computer_assist_ready": True}

    monkeypatch.setattr(module, "readiness", lambda: State())
    monkeypatch.setattr(module, "qualify_containment", lambda: {"passed": True, "checks": ["network_none"]})
    assert module.main(["--run"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "qualified": True,
        "result": {"passed": True, "checks": ["network_none"]},
        "readiness": {"computer_assist_ready": True},
    }
