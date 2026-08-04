import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.qwen_harness import QwenHarnessError
from src.qwen_supervisor import QwenRuntime, QwenSupervisor, bubblewrap_command


def test_bubblewrap_command_exposes_only_read_only_workspace(tmp_path):
    qwen_root = tmp_path / "qwen"
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    for path in (qwen_root, home, workspace):
        path.mkdir()
    command = bubblewrap_command(
        qwen_root=qwen_root, private_home=home, workspace_root=workspace,
        port=4170, environment={"OPENAI_API_KEY": "ephemeral"},
    )
    workspace_index = command.index(str(workspace))
    assert command[workspace_index - 1] == "--ro-bind"
    assert command[workspace_index + 1] == "/workspace"
    assert "--safe-mode" in command
    assert "--no-web" in command
    assert "--enable-session-shell" not in command
    assert "--chat-recording=false" in command


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
