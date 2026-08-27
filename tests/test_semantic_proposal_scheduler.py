import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Session as DbSession
import routes.chat_helpers as helpers
import src.continuity.semantic_deriver as deriver
from src.continuity.semantic_deriver import SemanticDerivationError
from src.continuity.store import ContinuityStore
import src.continuity.store as store_module


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


@pytest.mark.asyncio
async def test_semantic_proposal_scheduler_persists_safe_failed_outcome(monkeypatch):
    helpers._SEMANTIC_PROPOSAL_TASKS.clear()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine)
    monkeypatch.setattr(store_module, "SessionLocal", local)
    db = local()
    db.add(DbSession(
        id="session-3", owner="alice", name="Personal", endpoint_url="http://x", model="m",
        scope_kind="personal",
    ))
    db.commit(); db.close()
    monkeypatch.setattr(helpers, "_SEMANTIC_PROPOSAL_SETTLE_SECONDS", 0)
    monkeypatch.setattr(helpers, "_is_session_stream_active", lambda _session_id: False)

    async def fake_derive(**_kwargs):
        raise SemanticDerivationError("provider_failed", "safe provider failure")

    monkeypatch.setattr(deriver, "derive_semantic_proposal", fake_derive)
    helpers._schedule_semantic_proposal("alice", "session-3", 4)
    await asyncio.sleep(0.02)

    outcome = ContinuityStore().latest_semantic_proposal_attempt(owner="alice", session_id="session-3")
    assert (outcome.outcome, outcome.code) == ("failed", "provider_failed")
