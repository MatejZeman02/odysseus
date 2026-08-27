import asyncio
from pathlib import Path

from src.computer_sandbox import SandboxRunResult


def test_sandbox_read_snapshots_only_the_active_workspace(monkeypatch, tmp_path):
    import src.agent_tools.sandbox_tools as module

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("private project text", encoding="utf-8")
    (workspace / "skip-link").symlink_to(workspace / "README.md")
    captured = {}

    def fake_run(commands, *, input_dir, timeout=30.0):
        captured["commands"] = commands
        captured["input_dir"] = input_dir
        assert input_dir != workspace
        assert (input_dir / "README.md").read_text(encoding="utf-8") == "private project text"
        assert not (input_dir / "skip-link").exists()
        return SandboxRunResult(output="1 README.md\n", duration_ms=4)

    monkeypatch.setattr(module, "get_active_workspace", lambda: str(workspace))
    monkeypatch.setattr(module, "vet_workspace", lambda value: value)
    monkeypatch.setattr(module, "run_admitted_readonly_pipeline", fake_run)
    # The behavior under test is the broker contract, not asyncio's executor.
    # Running these tiny fixture operations inline avoids a pytest-asyncio
    # executor teardown hang on Python 3.13.
    async def direct_thread(function, /, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(module.asyncio, "to_thread", direct_thread)

    result = asyncio.run(module.SandboxedReadTool().execute(
        '{"commands":[["find", ".", "-name", "*.md"]]}', {},
    ))
    assert result["exit_code"] == 0
    assert result["sandboxed"] is True
    assert result["snapshot_files"] == 1
    assert captured["commands"][0].argv == ("find", ".", "-name", "*.md")
    assert not captured["input_dir"].exists()


def test_sandbox_read_rejects_shell_text_before_any_workspace_access(monkeypatch):
    import src.agent_tools.sandbox_tools as module

    monkeypatch.setattr(module, "get_active_workspace", lambda: "/not-used")
    result = asyncio.run(module.SandboxedReadTool().execute(
        '{"commands":["find .; rm -rf /"]}', {},
    ))
    assert result == {"error": "command_denied", "exit_code": 1}


def test_sandbox_read_is_registered_and_requires_a_capability_gate():
    from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    assert "sandbox_read" in TOOL_HANDLERS
    assert "sandbox_read" in TOOL_TAGS
    assert any(item["function"]["name"] == "sandbox_read" for item in FUNCTION_TOOL_SCHEMAS)
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    assert 'disabled_tools.add("sandbox_read")' in source
