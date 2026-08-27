import json
from types import SimpleNamespace

import pytest

from routes.chat_helpers import (
    _continuity_context_enabled,
    _continuity_prompt_message,
    _legacy_background_extraction_allowed,
    run_post_response_tasks,
)
from src.continuity.contracts import ContextBundle, DeviceProfileV1, ResolvedScope, ThreadCheckpointV1


def test_continuity_gate_is_default_off_and_opt_in(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_CONTINUITY_CONTEXT", raising=False)
    assert _continuity_context_enabled() is False
    monkeypatch.setenv("ODYSSEUS_CONTINUITY_CONTEXT", "true")
    assert _continuity_context_enabled() is True


def test_companion_scopes_are_durably_enabled(monkeypatch):
    from routes.chat_helpers import _continuity_enabled_for_session
    monkeypatch.delenv("ODYSSEUS_CONTINUITY_CONTEXT", raising=False)
    assert _continuity_enabled_for_session(SimpleNamespace(scope_kind="project")) is True
    assert _continuity_enabled_for_session(SimpleNamespace(scope_kind="personal")) is True
    assert _continuity_enabled_for_session(SimpleNamespace(scope_kind="computer")) is True
    assert _continuity_enabled_for_session(SimpleNamespace(scope_kind="general")) is False


def test_companion_homes_never_queue_legacy_auto_memory_or_skill_extraction():
    """The general-chat preference must not become a Companion memory writer."""
    assert _legacy_background_extraction_allowed(SimpleNamespace(scope_kind="general"), True) is True
    assert _legacy_background_extraction_allowed(SimpleNamespace(scope_kind="personal"), True) is False
    assert _legacy_background_extraction_allowed(SimpleNamespace(scope_kind="project"), True) is False
    assert _legacy_background_extraction_allowed(SimpleNamespace(scope_kind="computer"), True) is False
    assert _legacy_background_extraction_allowed(SimpleNamespace(scope_kind="general"), False) is False


def test_personal_turn_does_not_schedule_global_auto_memory(monkeypatch):
    """The gate protects the actual post-response dispatch, not just a helper."""
    import routes.chat_helpers as helpers

    scheduled = []
    monkeypatch.delenv("ODYSSEUS_SEMANTIC_PROPOSALS_AUTO", raising=False)
    monkeypatch.setattr(helpers, "_spawn_bg", lambda task: scheduled.append(task))
    session = SimpleNamespace(
        scope_kind="personal", history=[{}, {}, {}, {}], endpoint_url="http://unused",
        model="unused", headers={}, name="Personal Advisor",
    )
    run_post_response_tasks(
        session, None, "personal", "hello", "reply", None,
        {"auto_memory": True, "auto_skills": True}, None, None, None,
        owner="alice", agent_rounds=3, agent_tool_calls=3,
    )

    assert scheduled == []


@pytest.mark.asyncio
async def test_companion_context_does_not_inject_legacy_memory_rag_or_skills(monkeypatch):
    """A global preference cannot bypass the Personal/project scope boundary."""
    import routes.chat_helpers as helpers

    captured = {}

    async def fake_preprocess(_handler, message, _attachments, _session, **_kwargs):
        return helpers.PreprocessedMessage(
            enhanced_message=message, user_content=message, text_for_context=message,
            youtube_transcripts=[], attachment_meta=[],
        )

    def fake_add_user_message(session, _handler, preprocessed, **_kwargs):
        session.messages.append({"role": "user", "content": preprocessed.user_content})

    async def fake_maybe_compact(_session, _url, _model, messages, _headers, **_kwargs):
        return messages, 8192, False

    def fake_preface(**kwargs):
        captured.update(kwargs)
        return [], [], []

    monkeypatch.setattr(helpers, "preprocess", fake_preprocess)
    monkeypatch.setattr(helpers, "extract_preset", lambda *_args, **_kwargs: helpers.PresetInfo(0.7, 1024, None, None))
    monkeypatch.setattr(helpers, "add_user_message", fake_add_user_message)
    monkeypatch.setattr(helpers, "effective_user", lambda _request: "alice")
    monkeypatch.setattr(helpers, "load_prefs_for_user", lambda _owner: {"memory_enabled": True, "skills_enabled": True})
    monkeypatch.setattr(helpers, "_normalize_model_id_from_cache", lambda _session: None)
    monkeypatch.setattr(helpers, "normalize_model_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(helpers, "maybe_compact", fake_maybe_compact)
    monkeypatch.setattr(helpers, "trim_for_context", lambda messages, _limit: messages)
    monkeypatch.setattr(helpers, "_continuity_enabled_for_session", lambda _session: False)

    session = SimpleNamespace(
        scope_kind="personal", endpoint_url="http://unused", model="unused", headers={},
        owner="alice", history=[], messages=[],
    )
    session.get_context_messages = lambda: list(session.messages)
    await helpers.build_chat_context(
        session, SimpleNamespace(), SimpleNamespace(),
        SimpleNamespace(build_context_preface=fake_preface), "keep this private", "personal",
    )

    assert captured["use_memory"] is False
    assert captured["use_skills"] is False
    assert captured["use_rag"] is False


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


def test_private_companion_scope_gets_reviewed_artifact_creation_protocol():
    bundle = ContextBundle("", ResolvedScope("alice", "computer", "computer"), "create an incident")
    message = _continuity_prompt_message(bundle)
    payload = json.loads(message["content"].split("\n", 1)[1])
    protocol = next(item["artifact_creation_protocol"] for item in payload if "artifact_creation_protocol" in item)
    assert "odysseus-artifact-create" in protocol["instruction"]
    assert "owner must" in protocol["instruction"]


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
