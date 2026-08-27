import asyncio
import re
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
        assert (input_dir / "README.md").stat().st_mode & 0o777 == 0o644
        assert input_dir.stat().st_mode & 0o777 == 0o755
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


def test_sandbox_read_display_keeps_argv_boundaries_and_marks_a_clamp():
    import src.agent_tools.sandbox_tools as module
    from src.computer_sandbox import ReadOnlyCommand

    assert module._display((ReadOnlyCommand(("grep", "two words", "README.md")),)) == "grep 'two words' README.md"
    preview = module._display((ReadOnlyCommand(("grep", "x" * 600)),))
    assert preview.endswith("✂")
    assert len(preview) == 512


def test_snapshot_descriptor_copy_rejects_a_symlink_source(tmp_path):
    import src.agent_tools.sandbox_tools as module

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be copied", encoding="utf-8")
    source = workspace / "changed-to-link"
    source.symlink_to(outside)
    target = tmp_path / "snapshot.txt"

    try:
        module._copy_regular_file(
            source, target, workspace_root=workspace, remaining_bytes=1024,
        )
    except module._SnapshotUnsafe as exc:
        assert str(exc) == "snapshot_symlink"
    else:
        raise AssertionError("a symlink source must never be copied into the sandbox snapshot")
    assert not target.exists()


def test_snapshot_rejects_a_symlinked_workspace_root(tmp_path):
    import src.agent_tools.sandbox_tools as module

    source = tmp_path / "workspace"
    source.mkdir()
    (source / "README.md").write_text("must not be copied", encoding="utf-8")
    swapped = tmp_path / "workspace-link"
    swapped.symlink_to(source, target_is_directory=True)

    try:
        module._copy_workspace_input(swapped, tmp_path / "snapshot")
    except ValueError as exc:
        assert str(exc) == "workspace_unavailable"
    else:
        raise AssertionError("a symlinked workspace root must never be snapshotted")


def test_snapshot_rejects_a_workspace_swapped_to_symlink_after_open(monkeypatch, tmp_path):
    """The snapshot root must be descriptor-bound, not path-resolved late."""
    import os
    import src.agent_tools.sandbox_tools as module

    workspace = tmp_path / "workspace"; workspace.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    original_open = os.open
    swapped = False

    def swap_after_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        if not swapped and os.fspath(path) == os.fspath(workspace):
            swapped = True
            workspace.rename(tmp_path / "workspace-original")
            workspace.symlink_to(outside, target_is_directory=True)
        return fd

    monkeypatch.setattr(module.os, "open", swap_after_open)
    try:
        module._copy_workspace_input(workspace, tmp_path / "snapshot")
    except ValueError as exc:
        assert str(exc) == "workspace_unavailable"
    else:
        raise AssertionError("a post-open workspace symlink swap must be rejected")
    assert not (tmp_path / "snapshot").exists()


def test_sandbox_read_is_registered_and_requires_a_capability_gate():
    from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    assert "sandbox_read" in TOOL_HANDLERS
    assert "sandbox_read" in TOOL_TAGS
    assert any(item["function"]["name"] == "sandbox_read" for item in FUNCTION_TOOL_SCHEMAS)
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    assert 'disabled_tools.add("sandbox_read")' in source


def test_sandbox_read_is_part_of_file_intent_tool_selection_and_prompt_help():
    source = Path("src/agent_loop.py").read_text(encoding="utf-8")
    file_tools = re.search(r'"files": \{(?P<tools>[^}]*)\}', source)
    assert file_tools is not None
    assert '"sandbox_read"' in file_tools.group("tools")
    assert '"sandbox_read": "- ```sandbox_read```' in source

    index = Path("src/tool_index.py").read_text(encoding="utf-8")
    assert '"sandbox_read": "Run a short read-only command pipeline' in index
