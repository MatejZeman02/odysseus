import json

from routes.chat_helpers import _continuity_context_enabled, _continuity_prompt_message
from src.continuity.contracts import ContextBundle, ResolvedScope, ThreadCheckpointV1


def test_continuity_gate_is_default_off_and_opt_in(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_CONTINUITY_CONTEXT", raising=False)
    assert _continuity_context_enabled() is False
    monkeypatch.setenv("ODYSSEUS_CONTINUITY_CONTEXT", "true")
    assert _continuity_context_enabled() is True


def test_continuity_prompt_marks_derived_context_without_raw_transcript():
    checkpoint = ThreadCheckpointV1(session_id="s", source_hash="hash", source_message_ids=["m1"], source_through_message_id="m1")
    bundle = ContextBundle("", ResolvedScope("alice", "s", "general"), "continue", checkpoint, transcript_tail=({"role": "user", "content": "raw secret"},))
    message = _continuity_prompt_message(bundle)
    payload = json.loads(message["content"].split("\n", 1)[1])
    assert message["role"] == "user"
    assert message["metadata"]["continuity_artifact"] is True
    assert "raw secret" not in message["content"]
    assert payload[0]["thread_checkpoint"]["source_hash"] == "hash"
