import asyncio

import pytest

import routes.chat_helpers as helpers
import src.continuity.semantic_deriver as deriver


@pytest.mark.asyncio
async def test_semantic_proposal_scheduler_derives_after_quiet_period(monkeypatch):
    helpers._SEMANTIC_PROPOSAL_TASKS.clear()
    monkeypatch.setattr(helpers, "_SEMANTIC_PROPOSAL_SETTLE_SECONDS", 0)
    monkeypatch.setattr(helpers, "_is_session_stream_active", lambda _session_id: False)
    seen = []

    async def fake_derive(**kwargs):
        seen.append(kwargs)
        return type("Result", (), {"created": True})()

    monkeypatch.setattr(deriver, "derive_semantic_proposal", fake_derive)
    helpers._schedule_semantic_proposal("alice", "session-1", 4)
    await asyncio.sleep(0.02)

    assert seen == [{"owner": "alice", "session_id": "session-1", "only_if_absent": True}]
    assert helpers._SEMANTIC_PROPOSAL_TASKS == {}


@pytest.mark.asyncio
async def test_semantic_proposal_scheduler_cancellation_prevents_derivation(monkeypatch):
    helpers._SEMANTIC_PROPOSAL_TASKS.clear()
    monkeypatch.setattr(helpers, "_SEMANTIC_PROPOSAL_SETTLE_SECONDS", 0.05)
    seen = []

    async def fake_derive(**kwargs):
        seen.append(kwargs)
        return type("Result", (), {"created": True})()

    monkeypatch.setattr(deriver, "derive_semantic_proposal", fake_derive)
    helpers._schedule_semantic_proposal("alice", "session-2", 4)
    helpers.cancel_scheduled_semantic_proposal("alice", "session-2")
    await asyncio.sleep(0.08)

    assert seen == []
    assert helpers._SEMANTIC_PROPOSAL_TASKS == {}
