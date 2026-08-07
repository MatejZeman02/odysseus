from types import SimpleNamespace


def test_inspection_is_disabled_and_fail_closed_by_default(monkeypatch):
    import src.qwen_inspection as inspection

    monkeypatch.delenv("ODYSSEUS_QWEN_PROJECT_INSPECT", raising=False)
    monkeypatch.delenv("ODYSSEUS_QWEN_SANDBOX_IMAGE", raising=False)
    monkeypatch.setattr(inspection.shutil, "which", lambda _name: None)

    readiness = inspection.inspection_readiness()

    assert readiness.enabled is False
    assert readiness.ready is False
    assert readiness.sandbox_probe_passed is False
    assert inspection.effective_capability("project_inspect") == "project_read"


def test_unpinned_image_is_never_probed_or_pulled(monkeypatch):
    import src.qwen_inspection as inspection

    called = []
    monkeypatch.setenv("ODYSSEUS_QWEN_PROJECT_INSPECT", "1")
    monkeypatch.setenv("ODYSSEUS_QWEN_SANDBOX_IMAGE", "ghcr.io/qwenlm/qwen-code:0.21.3")
    monkeypatch.setattr(inspection.shutil, "which", lambda _name: "/usr/bin/podman")
    monkeypatch.setattr(inspection, "_podman_image_exists", lambda *_args: called.append(True) or True)

    readiness = inspection.inspection_readiness()

    assert readiness.image_configured is False
    assert readiness.image_ready is False
    assert called == []
    assert readiness.failure_code == "sandbox_unavailable"


def test_reviewed_local_image_still_requires_a_passing_containment_probe(monkeypatch):
    import src.qwen_inspection as inspection

    monkeypatch.setenv("ODYSSEUS_QWEN_PROJECT_INSPECT", "1")
    monkeypatch.setenv("ODYSSEUS_QWEN_SANDBOX_IMAGE", inspection.PINNED_QWEN_SANDBOX_IMAGE)
    monkeypatch.setattr(inspection.shutil, "which", lambda _name: "/usr/bin/podman")
    monkeypatch.setattr(inspection, "_podman_image_exists", lambda *_args: True)

    readiness = inspection.inspection_readiness()

    assert readiness.image_configured is True
    assert readiness.image_ready is True
    assert readiness.sandbox_probe_passed is False
    assert readiness.ready is False
    assert inspection.effective_capability("project_inspect") == "project_read"


def test_public_readiness_contains_only_safe_booleans(monkeypatch):
    import src.qwen_inspection as inspection

    monkeypatch.setenv("ODYSSEUS_QWEN_PROJECT_INSPECT", "1")
    monkeypatch.setenv("ODYSSEUS_QWEN_SANDBOX_IMAGE", inspection.PINNED_QWEN_SANDBOX_IMAGE)
    monkeypatch.setattr(inspection.shutil, "which", lambda _name: "/private/podman")
    monkeypatch.setattr(inspection, "_podman_image_exists", lambda *_args: True)

    payload = inspection.inspection_readiness().public_payload()

    assert set(payload) == {
        "enabled", "podman", "pinned_image", "image_ready", "sandbox_probe", "ready",
    }
    assert all(type(value) is bool for value in payload.values())
    assert "/private" not in repr(payload)
    assert "sha256" not in repr(payload)
