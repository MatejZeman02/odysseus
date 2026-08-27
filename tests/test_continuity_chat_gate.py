import json

from routes.chat_helpers import _continuity_context_enabled, _continuity_prompt_message
from src.continuity.contracts import ContextBundle, DeviceProfileV1, ResolvedScope, ThreadCheckpointV1


def test_continuity_gate_is_default_off_and_opt_in(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_CONTINUITY_CONTEXT", raising=False)
    assert _continuity_context_enabled() is False
    monkeypatch.setenv("ODYSSEUS_CONTINUITY_CONTEXT", "true")
    assert _continuity_context_enabled() is True


def test_companion_scopes_are_durably_enabled(monkeypatch):
    from types import SimpleNamespace
    from routes.chat_helpers import _continuity_enabled_for_session
    monkeypatch.delenv("ODYSSEUS_CONTINUITY_CONTEXT", raising=False)
    assert _continuity_enabled_for_session(SimpleNamespace(scope_kind="project")) is True
    assert _continuity_enabled_for_session(SimpleNamespace(scope_kind="personal")) is True
    assert _continuity_enabled_for_session(SimpleNamespace(scope_kind="computer")) is True
    assert _continuity_enabled_for_session(SimpleNamespace(scope_kind="general")) is False


def test_continuity_prompt_marks_derived_context_without_raw_transcript():
    checkpoint = ThreadCheckpointV1(session_id="s", source_hash="hash", source_message_ids=["m1"], source_through_message_id="m1")
    bundle = ContextBundle("", ResolvedScope("alice", "s", "general"), "continue", checkpoint, transcript_tail=({"role": "user", "content": "raw secret"},))
    message = _continuity_prompt_message(bundle)
    payload = json.loads(message["content"].split("\n", 1)[1])
    assert message["role"] == "user"
    assert message["metadata"]["continuity_artifact"] is True
    assert "raw secret" not in message["content"]
    checkpoint_payload = next(item["thread_checkpoint"] for item in payload if "thread_checkpoint" in item)
    provenance = next(item["continuity_provenance_policy"] for item in payload if "continuity_provenance_policy" in item)
    assert checkpoint_payload["source_hash"] == "hash"
    assert checkpoint_payload["derivation_status"] == "legacy_unclassified"
    assert "owner-approved" in provenance


def test_native_computer_prompt_receives_only_verified_device_profile():
    profile = DeviceProfileV1(
        session_id="computer", facts=["Graphics: GPU: Example"], source_hash="safe-hash",
        collected_at="2026-08-27T14:00:00",
    )
    bundle = ContextBundle(
        "", ResolvedScope("alice", "computer", "computer"), "diagnose graphics",
        device_profile=profile,
    )
    payload = json.loads(_continuity_prompt_message(bundle)["content"].split("\n", 1)[1])
    record = next(item for item in payload if "verified_device_profile" in item)
    assert record["verified_device_profile"]["facts"] == ["Graphics: GPU: Example"]
    assert "not model-authored memory" in record["verified_device_profile_policy"]


def test_named_personal_artifact_adds_reviewed_revision_protocol():
    bundle = ContextBundle(
        "", ResolvedScope("alice", "s", "personal"), "improve my love letter",
        manifest={"selected_working_artifact_paths": ["drafts/love-letter.md"]},
    )
    message = _continuity_prompt_message(bundle)
    payload = json.loads(message["content"].split("\n", 1)[1])
    protocol = next(item["artifact_revision_protocol"] for item in payload if "artifact_revision_protocol" in item)
    assert protocol["selected_paths"] == ["drafts/love-letter.md"]
    assert "odysseus-artifact-revision" in protocol["instruction"]


def test_automatic_paste_is_source_material_not_revision_target():
    path = "pastes/2026-08-23-140401-deadbeef.md"
    bundle = ContextBundle(
        "", ResolvedScope("alice", "s", "personal"),
        "create an artifact document from this paste",
        manifest={"selected_working_artifact_paths": [path]},
    )
    message = _continuity_prompt_message(bundle)
    payload = json.loads(message["content"].split("\n", 1)[1])

    assert not any("artifact_revision_protocol" in item for item in payload)
    policy = next(item["source_paste_policy"] for item in payload if "source_paste_policy" in item)
    assert policy["source_paths"] == [path]
    assert "create_document" in policy["instruction"]
