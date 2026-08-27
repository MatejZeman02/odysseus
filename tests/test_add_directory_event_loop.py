"""Regression guard for #5558 — POST /api/personal/add_directory must not run
the indexing job on the event loop.

The handler is ``async def`` but called ``rag.index_personal_documents``
(os.walk + file reads + per-chunk embedding + Chroma inserts) inline, so
FastAPI ran the whole job on the event loop and every other request queued
behind it: indexing a real directory froze the UI and API for 25+ minutes.
``personal_docs_manager.add_directory`` sits in the same blocking section — it
triggers ``refresh_index()``, which re-extracts text across tracked dirs.

These tests build the real router with fake managers and compare the thread
the indexing work runs on against the event loop's thread.
"""
import asyncio
import functools
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.datastructures import UploadFile as StarletteUploadFile
from src.request_models import DirectoryRequest


def _serialization_probe():
    """Shared counter proving two critical sections never overlap."""
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def enter():
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])

    def leave():
        with lock:
            state["active"] -= 1

    return state, enter, leave


# Concurrency tests are `async def` (pyproject asyncio_mode="auto") and drive the
# ASGI app through httpx.ASGITransport + AsyncClient + asyncio.gather, NOT starlette
# TestClient + ThreadPoolExecutor: the job lock is an asyncio.Lock acquired in the
# async handler, and TestClient's portal-thread dispatch deadlocks against it (same
# reason test_notes_fail_closed_auth.py uses ASGITransport). asyncio.gather runs both
# requests on the test's own loop.
def _async_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _handler(app, path):
    """Find a router endpoint without involving the ASGI transport scheduler."""
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        nested = getattr(route, "original_router", None)
        if nested is not None:
            pending.extend(nested.routes)
            continue
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"Route not found: {path}")


def _upload(filename, content):
    # Keep the fixture in Starlette's in-memory path. Plain BytesIO is treated
    # as disk-backed and delegates ``read`` to AnyIO's global worker pool.
    # The test needs predictable local upload semantics, not that pool's
    # lifecycle under Python 3.13.
    file = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    file.write(content)
    file.seek(0)
    return StarletteUploadFile(file=file, filename=filename)

import routes.personal_routes as personal_routes
from core.middleware import require_admin
from src.auth_helpers import require_user


class _FakeRag:
    def __init__(self, record):
        self._record = record

    def index_personal_documents(self, directory, owner=None):
        self._record["index_thread"] = threading.get_ident()
        return {"success": True, "indexed_count": 3, "failed_count": 0}

    def _split_into_chunks(self, text, chunk_size=500):
        return [text]

    def add_document(self, chunk, metadata):
        self._record["add_document_thread"] = threading.get_ident()
        return True

    def delete_by_source(self, filepath):
        self._record["delete_thread"] = threading.get_ident()
        return 1


class _FakeDocsManager:
    def __init__(self, record):
        self._record = record
        self.index = []

    def add_directory(self, directory, *, index=True, owner=None):
        self._record["bookkeeping_thread"] = threading.get_ident()
        self._record["bookkeeping_index_flag"] = index

    def exclude_file(self, filepath):
        self._record["exclude_thread"] = threading.get_ident()


def _build_app(tmp_path, monkeypatch, record):
    monkeypatch.setattr(personal_routes, "PERSONAL_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: _FakeRag(record))

    async def _isolated_threadpool(call, *args, **kwargs):
        """Exercise the route's off-loop contract without AnyIO's global pool.

        The in-memory SQLite bootstrap performed by the shared test conftest
        triggers a Python 3.13 executor-shutdown defect: AnyIO's otherwise
        healthy global worker pool never closes, so this regression module
        stalls after the route has finished. A fresh, short-lived executor
        proves the route's actual invariant (blocking work stays off the
        event loop) while keeping the test runner deterministic.
        """
        callback = functools.partial(call, *args, **kwargs)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="personal-index-test") as pool:
            result = asyncio.get_running_loop().run_in_executor(pool, callback)
            # Python 3.13's selector loop can retain a cross-thread Future
            # waiter until another timer fires after the in-memory SQLite
            # bootstrap. A tiny local heartbeat observes completion without
            # changing the test's off-loop execution contract.
            while not result.done():
                await asyncio.sleep(0.01)
            return result.result()

    monkeypatch.setattr(personal_routes, "run_in_threadpool", _isolated_threadpool)

    app = FastAPI()
    app.include_router(
        personal_routes.setup_personal_routes(_FakeDocsManager(record), None, True)
    )

    async def _owner_override():
        return "tester"

    async def _admin_override():
        return None

    app.dependency_overrides[require_user] = _owner_override
    app.dependency_overrides[require_admin] = _admin_override

    @app.get("/loop-thread")
    async def loop_thread_probe():
        return {"thread": threading.get_ident()}

    return app


async def test_indexing_runs_off_the_event_loop(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    # One ASGI event loop serves both requests, so the probe and POST are
    # guaranteed to see the same loop thread without a TestClient portal.
    async with _async_client(app) as client:
        loop_thread = (await client.get("/loop-thread")).json()["thread"]
        resp = await client.post(
            "/api/personal/add_directory", json={"directory": str(target)}
        )

    assert resp.status_code == 200
    assert record["index_thread"] != loop_thread, (
        "index_personal_documents ran on the event loop thread — every other "
        "request queues behind the indexing job (#5558)"
    )
    assert record["bookkeeping_thread"] != loop_thread, (
        "personal_docs_manager.add_directory (refresh_index) ran on the event "
        "loop thread"
    )


async def test_response_and_bookkeeping_unchanged(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    async with _async_client(app) as client:
        resp = await client.post("/api/personal/add_directory", json={"directory": str(target)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["indexed_count"] == 3
    assert body["failed_count"] == 0
    assert body["directory"] == os.path.realpath(str(target))
    assert record["bookkeeping_index_flag"] is False


async def test_concurrent_add_directory_requests_serialize_indexing(tmp_path, monkeypatch):
    """Off-loop execution must not mean parallel index jobs: concurrent
    requests would race PersonalDocsManager's unsynchronized list mutations
    and file writes (save_directories/_save_excluded are plain open('w'))."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.2); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    for name in ("docs_a", "docs_b"):
        (tmp_path / name).mkdir()
    add = _handler(app, "/api/personal/add_directory")
    results = await asyncio.gather(
        add(None, DirectoryRequest(directory=str(tmp_path / "docs_a")), "tester", None),
        add(None, DirectoryRequest(directory=str(tmp_path / "docs_b")), "tester", None),
    )

    assert all(result["success"] for result in results)
    assert state["max_active"] == 1, (
        f"{state['max_active']} index jobs ran in parallel — concurrent "
        "add_directory requests must serialize"
    )


async def test_failed_indexing_still_returns_500(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    def _fail(directory, owner=None):
        return {"success": False, "message": "boom"}

    monkeypatch.setattr(_FakeRag, "index_personal_documents", staticmethod(_fail))

    add = _handler(app, "/api/personal/add_directory")
    with pytest.raises(HTTPException, match="boom"):
        await add(None, DirectoryRequest(directory=str(target)), "tester", None)


async def test_add_and_remove_serialize(tmp_path, monkeypatch):
    """#5634: remove must hold the SAME job lock as add. Otherwise a remove
    running while an add job is in flight races PersonalDocsManager's
    unsynchronized list/index mutations — the inconsistent state the PR's
    'add/remove are serialized' guarantee claims to prevent."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.25); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    def _slow_remove(self, directory):
        enter(); time.sleep(0.25); leave()

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)
    monkeypatch.setattr(_FakeDocsManager, "remove_directory", _slow_remove, raising=False)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    (tmp_path / "docs_a").mkdir()
    (tmp_path / "docs_b").mkdir()

    add = _handler(app, "/api/personal/add_directory")
    remove = _handler(app, "/api/personal/remove_directory")
    results = await asyncio.gather(
        add(None, DirectoryRequest(directory=str(tmp_path / "docs_a")), "tester", None),
        remove(str(tmp_path / "docs_b"), "tester", None),
    )

    assert all(result["success"] for result in results)
    assert state["max_active"] == 1, (
        f"{state['max_active']} add/remove critical sections overlapped — "
        "remove must hold the same index job lock as add"
    )


async def test_add_and_upload_serialize(tmp_path, monkeypatch):
    """#5634 follow-up: POST /upload writes chunks into the vector store and then
    calls personal_docs_manager.add_directory — the same vector/tracking state
    add_directory mutates. It must hold the SAME job lock, or an upload landing
    mid-add interleaves two writers over unsynchronized state."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.25); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    def _slow_add_document(self, chunk, metadata):
        self._record["add_document_thread"] = threading.get_ident()
        enter(); time.sleep(0.25); leave()
        return True

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)
    monkeypatch.setattr(_FakeRag, "add_document", _slow_add_document)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(personal_routes, "require_privilege", lambda request, key: "tester")
    (tmp_path / "docs_a").mkdir()

    add = _handler(app, "/api/personal/add_directory")
    upload = _handler(app, "/api/personal/upload")
    results = await asyncio.gather(
        add(None, DirectoryRequest(directory=str(tmp_path / "docs_a")), "tester", None),
        upload(None, [_upload("a.txt", b"hello world")]),
    )

    assert all(result["success"] for result in results)
    # The test coroutine runs on the event loop, so this IS the loop thread.
    assert record["add_document_thread"] != threading.get_ident(), (
        "rag.add_document ran on the event loop thread — chunk writes block "
        "every other request for the duration of the upload"
    )
    assert state["max_active"] == 1, (
        f"{state['max_active']} add/upload critical sections overlapped — "
        "upload must hold the same index job lock as add"
    )


async def test_upload_processes_each_payload_before_reading_the_next(tmp_path, monkeypatch):
    """A multi-file upload must retain at most one capped payload at a time."""
    reads = []
    original_read = StarletteUploadFile.read

    async def _recording_read(upload, size=-1):
        reads.append(upload.filename)
        return await original_read(upload, size)

    def _record_first_index(self, chunk, metadata):
        self._record.setdefault("reads_at_first_index", len(reads))
        return True

    monkeypatch.setattr(StarletteUploadFile, "read", _recording_read)
    monkeypatch.setattr(_FakeRag, "add_document", _record_first_index)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(personal_routes, "require_privilege", lambda request, key: "tester")

    upload = _handler(app, "/api/personal/upload")
    response = await upload(None, [
        _upload("a.txt", b"alpha"), _upload("b.txt", b"bravo"), _upload("c.txt", b"charlie"),
    ])

    assert response["uploaded"] == ["a.txt", "b.txt", "c.txt"]
    assert reads == ["a.txt", "b.txt", "c.txt"]
    assert record["reads_at_first_index"] == 1, (
        "all upload bodies were retained before worker processing began"
    )


async def test_add_and_delete_file_serialize(tmp_path, monkeypatch):
    """#5634 follow-up: DELETE /file removes chunks from the vector store and
    calls personal_docs_manager.exclude_file. Both mutate state add_directory
    also touches, so the delete must hold the SAME job lock as add."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.25); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    def _slow_delete(self, filepath):
        self._record["delete_thread"] = threading.get_ident()
        enter(); time.sleep(0.25); leave()
        return 1

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)
    monkeypatch.setattr(_FakeRag, "delete_by_source", _slow_delete)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))
    (tmp_path / "docs_a").mkdir()
    doomed = tmp_path / "doomed.txt"
    doomed.write_text("bye")

    add = _handler(app, "/api/personal/add_directory")
    delete_file = _handler(app, "/api/personal/file")
    results = await asyncio.gather(
        add(None, DirectoryRequest(directory=str(tmp_path / "docs_a")), "tester", None),
        delete_file(str(doomed), "tester", None),
    )

    assert all(result["success"] for result in results)
    assert record["delete_thread"] != threading.get_ident(), (
        "rag.delete_by_source ran on the event loop thread"
    )
    assert state["max_active"] == 1, (
        f"{state['max_active']} add/delete critical sections overlapped — "
        "delete must hold the same index job lock as add"
    )


async def test_reload_serializes_with_add(tmp_path, monkeypatch):
    """#5634: POST /reload rebuilds the index via refresh_index(); it must hold
    the same job lock so it cannot race an in-flight add job."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.25); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    def _slow_refresh(self):
        enter(); time.sleep(0.25); leave()

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)
    monkeypatch.setattr(_FakeDocsManager, "refresh_index", _slow_refresh, raising=False)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    (tmp_path / "docs_a").mkdir()

    add = _handler(app, "/api/personal/add_directory")
    reload_index = _handler(app, "/api/personal/reload")
    results = await asyncio.gather(
        add(None, DirectoryRequest(directory=str(tmp_path / "docs_a")), "tester", None),
        reload_index("tester", None),
    )

    assert results[0]["success"] is True
    assert results[1]["ok"] is True
    assert state["max_active"] == 1, (
        f"{state['max_active']} add/reload critical sections overlapped — "
        "reload must hold the same index job lock as add"
    )
