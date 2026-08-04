import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.qwen_harness import QwenHarnessError, QwenServeHTTPError
from src.qwen_supervisor import QwenRuntime, QwenSupervisor, bubblewrap_command


def test_bubblewrap_command_exposes_only_read_only_workspace(tmp_path):
    qwen_root = tmp_path / "qwen"
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    for path in (qwen_root, home, workspace):
        path.mkdir()
    command = bubblewrap_command(
        qwen_root=qwen_root, private_home=home, workspace_root=workspace,
        port=4170,
    )
    workspace_index = command.index(str(workspace))
    assert command[workspace_index - 1] == "--ro-bind"
    assert command[workspace_index + 1] == "/workspace"
    assert "--safe-mode" in command
    assert "--no-web" in command
    assert "--enable-session-shell" not in command
    assert "--chat-recording=false" in command
    assert "ephemeral" not in command
    assert "--setenv" not in command


def test_supervisor_keeps_npm_install_root_when_bin_is_symlink(tmp_path):
    root = tmp_path / "qwen-install"
    package = root / "node_modules" / "@qwen-code" / "qwen-code"
    bin_dir = root / "node_modules" / ".bin"
    package.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    entry = package / "cli-entry.js"
    entry.write_text("#!/usr/bin/env node\n")
    launcher = bin_dir / "qwen"
    launcher.symlink_to(Path("..") / "@qwen-code" / "qwen-code" / "cli-entry.js")

    supervisor = QwenSupervisor(binary=launcher)

    assert supervisor.qwen_root == root.resolve()
    assert supervisor.binary == launcher.absolute()


def _serve_error(code, *, retry_after=0.25):
    return QwenServeHTTPError(
        "POST /session", status_code=503,
        error_code=code, retry_after_seconds=retry_after,
    )


@pytest.mark.asyncio
async def test_supervisor_waits_for_deep_runtime_health_before_capabilities(tmp_path, monkeypatch):
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("src.qwen_supervisor.asyncio.sleep", fake_sleep)

    class FakeClient:
        ready_calls = 0
        capability_calls = 0

        async def verify_runtime_ready(self):
            self.ready_calls += 1
            if self.ready_calls == 1:
                raise QwenServeHTTPError(
                    "GET /health?deep=1", status_code=503,
                    error_code="bootstrap", retry_after_seconds=1,
                )
            return {"status": "ok"}

        async def verify_capabilities(self):
            self.capability_calls += 1
            return {"features": []}

    client = FakeClient()
    runtime = QwenRuntime(SimpleNamespace(returncode=None), client, tmp_path)
    supervisor = QwenSupervisor(binary=tmp_path / "missing")

    await supervisor._wait_until_ready(runtime, startup_timeout=5)

    assert client.ready_calls == 2
    assert client.capability_calls == 1
    assert sleeps == [1]


@pytest.mark.asyncio
async def test_supervisor_does_not_retry_permanent_runtime_failure(tmp_path, monkeypatch):
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("src.qwen_supervisor.asyncio.sleep", fake_sleep)

    class FakeClient:
        async def verify_runtime_ready(self):
            raise QwenServeHTTPError(
                "GET /health?deep=1", status_code=503,
                error_code="daemon_runtime_failed",
            )

    runtime = QwenRuntime(SimpleNamespace(returncode=None), FakeClient(), tmp_path)
    supervisor = QwenSupervisor(binary=tmp_path / "missing")

    with pytest.raises(QwenServeHTTPError) as raised:
        await supervisor._wait_until_ready(runtime, startup_timeout=5)

    assert raised.value.error_code == "daemon_runtime_failed"
    assert sleeps == []


@pytest.mark.asyncio
async def test_supervisor_retries_only_transient_session_rejection(tmp_path, monkeypatch):
    sleeps = []
    create_calls = 0

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("src.qwen_supervisor.asyncio.sleep", fake_sleep)

    class FakeClient:
        async def create_session(self, **kwargs):
            nonlocal create_calls
            create_calls += 1
            if create_calls < 3:
                raise _serve_error("daemon_runtime_starting")
            return "session"

        async def prompt(self, session_id, prompt):
            return "prompt", "0"

        async def events(self, session_id, last_event_id):
            yield {"type": "turn_complete", "data": {"stopReason": "end_turn"}}

    supervisor = QwenSupervisor(binary=tmp_path / "missing")
    supervisor.runtime = QwenRuntime(SimpleNamespace(), FakeClient(), tmp_path)

    answer, metadata = await supervisor.run_prompt("question")

    assert answer == ""
    assert metadata["prompt_id"] == "prompt"
    assert create_calls == 3
    assert sleeps == [0.25, 0.25]


@pytest.mark.asyncio
async def test_supervisor_bounds_transient_session_retries(tmp_path, monkeypatch):
    sleeps = []
    create_calls = 0

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("src.qwen_supervisor.asyncio.sleep", fake_sleep)

    class FakeClient:
        async def create_session(self, **kwargs):
            nonlocal create_calls
            create_calls += 1
            raise _serve_error("daemon_runtime_starting")

    supervisor = QwenSupervisor(binary=tmp_path / "missing")
    supervisor.runtime = QwenRuntime(SimpleNamespace(), FakeClient(), tmp_path)

    with pytest.raises(QwenServeHTTPError):
        await supervisor.run_prompt("question")

    assert create_calls == 3
    assert sleeps == [0.25, 0.25]


@pytest.mark.asyncio
async def test_supervisor_stop_terminates_entire_bubblewrap_process_group(tmp_path, monkeypatch):
    signals = []

    class Process:
        returncode = 0

        async def wait(self):
            return 0

    class Client:
        async def close(self):
            return None

    def fake_killpg(process_group_id, signal_number):
        signals.append((process_group_id, signal_number))
        if signal_number == 0:
            raise ProcessLookupError

    monkeypatch.setattr("src.qwen_supervisor.os.killpg", fake_killpg)
    runtime = QwenRuntime(Process(), Client(), tmp_path, process_group_id=12345)
    supervisor = QwenSupervisor(binary=tmp_path / "missing")
    supervisor.runtime = runtime

    await supervisor.stop()

    assert signals == [(12345, 15), (12345, 0)]


def test_supervisor_records_the_nested_bubblewrap_session_group(tmp_path, monkeypatch):
    children = {
        100: "101",
        101: "102",
        102: "",
    }

    class ChildrenPath:
        def __init__(self, path):
            self.pid = int(str(path).split("/")[2])

        def read_text(self):
            return children[self.pid]

    monkeypatch.setattr("src.qwen_supervisor.Path", ChildrenPath)
    monkeypatch.setattr("src.qwen_supervisor.os.getpgid", lambda pid: 102 if pid == 102 else 100)

    assert QwenSupervisor._sandbox_process_group_id(100) == 102


@pytest.mark.asyncio
async def test_supervisor_collects_only_assistant_chunks_until_end_turn(tmp_path):
    class FakeClient:
        async def create_session(self, **kwargs):
            assert kwargs == {"cwd": "/workspace"}
            return "session"

        async def prompt(self, session_id, prompt):
            assert (session_id, prompt) == ("session", "question")
            return "prompt", "7"

        async def events(self, session_id, last_event_id):
            yield {"type": "session_update", "data": {"update": {
                "sessionUpdate": "agent_message_chunk", "content": {"text": "hello "},
            }}}
            yield {"type": "unknown_future_event", "data": {}}
            yield {"type": "session_update", "data": {"update": {
                "sessionUpdate": "agent_message_chunk", "content": {"text": "world"},
            }}}
            yield {"type": "turn_complete", "data": {"stopReason": "end_turn"}}

    supervisor = QwenSupervisor(binary=tmp_path / "missing")
    supervisor.runtime = QwenRuntime(SimpleNamespace(), FakeClient(), tmp_path)
    answer, metadata = await supervisor.run_prompt("question")
    assert answer == "hello world"
    assert metadata["prompt_id"] == "prompt"


def test_supervisor_sanitizes_qwen_tool_updates_for_the_browser():
    assert QwenSupervisor._sanitize_progress_update({
        "sessionUpdate": "tool_call", "name": "grep", "status": "running",
        "rawInput": {"path": "/workspace/docs/README.md", "pattern": "secret"},
    }) == {
        "operation": "Search project files", "tool": "search", "path": "docs/README.md",
        "label": "Search: docs/README.md", "command": "Search: 'secret' docs/README.md", "status": "running",
    }
    assert QwenSupervisor._sanitize_progress_update({
        "sessionUpdate": "tool_call", "title": "Read /etc/shadow", "rawInput": {"path": "/etc/shadow"},
    }) == {
        "operation": "Read project file", "tool": "read_file", "path": None,
        "label": "Read: workspace", "command": "Read: workspace", "status": "working",
    }

    assert QwenSupervisor._sanitize_progress_update({
        "sessionUpdate": "tool_call", "name": "read_file", "rawInput": {"file_path": "/workspace/images/meadows.jpg"},
    })["path"] == "images/meadows.jpg"
    assert QwenSupervisor._sanitize_shell_command('cat /workspace/docs/Story.md | grep -e "back"') == 'cat docs/Story.md | grep -e "back"'
    assert QwenSupervisor._sanitize_shell_command('cat /etc/shadow') == ""


@pytest.mark.asyncio
async def test_supervisor_reconnects_from_cursor_without_duplicate_chunks(tmp_path):
    cursors = []

    class FakeClient:
        async def create_session(self, **kwargs):
            return "session"

        async def prompt(self, session_id, prompt):
            return "prompt", "7"

        async def events(self, session_id, last_event_id):
            cursors.append(last_event_id)
            if len(cursors) == 1:
                yield {"_sse_id": "8", "type": "session_update", "data": {"update": {
                    "sessionUpdate": "agent_message_chunk", "content": {"text": "once"},
                }}}
                return
            yield {"_sse_id": "8", "type": "session_update", "data": {"update": {
                "sessionUpdate": "agent_message_chunk", "content": {"text": "once"},
            }}}
            yield {"_sse_id": "9", "type": "turn_complete", "data": {"stopReason": "end_turn"}}

    supervisor = QwenSupervisor(binary=tmp_path / "missing")
    supervisor.runtime = QwenRuntime(SimpleNamespace(), FakeClient(), tmp_path)
    answer, _ = await supervisor.run_prompt("question")
    assert answer == "once"
    assert cursors == ["7", "8"]


@pytest.mark.asyncio
async def test_supervisor_cancels_admitted_turn_on_caller_cancellation(tmp_path):
    admitted = asyncio.Event()
    cancelled = []

    class FakeClient:
        async def create_session(self, **kwargs):
            return "session"

        async def prompt(self, session_id, prompt):
            admitted.set()
            return "prompt", "0"

        async def events(self, session_id, last_event_id):
            await asyncio.sleep(3600)
            yield {}

        async def cancel(self, session_id):
            cancelled.append(session_id)

    supervisor = QwenSupervisor(binary=tmp_path / "missing")
    supervisor.runtime = QwenRuntime(SimpleNamespace(), FakeClient(), tmp_path)
    task = asyncio.create_task(supervisor.run_prompt("question"))
    await admitted.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == ["session"]


@pytest.mark.asyncio
async def test_supervisor_timeout_cancels_the_admitted_qwen_session(tmp_path):
    cancelled = []

    class FakeClient:
        async def create_session(self, **kwargs): return "session"
        async def prompt(self, session_id, prompt): return "prompt", "0"
        async def events(self, session_id, last_event_id):
            await asyncio.sleep(3600)
            yield {}
        async def cancel(self, session_id): cancelled.append(session_id)

    supervisor = QwenSupervisor(binary=tmp_path / "missing")
    supervisor.runtime = QwenRuntime(SimpleNamespace(), FakeClient(), tmp_path)

    with pytest.raises(TimeoutError):
        await supervisor.run_prompt("question", timeout=0.01)

    assert cancelled == ["session"]


@pytest.mark.asyncio
async def test_supervisor_reports_worker_death(tmp_path):
    class FakeClient:
        async def create_session(self, **kwargs):
            return "session"

        async def prompt(self, session_id, prompt):
            return "prompt", "0"

        async def events(self, session_id, last_event_id):
            yield {"type": "session_died", "data": {}}

    supervisor = QwenSupervisor(binary=tmp_path / "missing")
    supervisor.runtime = QwenRuntime(SimpleNamespace(), FakeClient(), tmp_path)
    with pytest.raises(QwenHarnessError, match="session_died"):
        await supervisor.run_prompt("question")
