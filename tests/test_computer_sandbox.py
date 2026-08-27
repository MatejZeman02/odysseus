import json
import signal
from unittest.mock import Mock
from pathlib import Path

from src import computer_sandbox


def test_terminate_group_signals_descendants_even_if_podman_client_exited(monkeypatch):
    process = Mock()
    process.pid = 4242
    process.poll.return_value = 0
    killpg = Mock()
    monkeypatch.setattr(computer_sandbox.os, "killpg", killpg)

    computer_sandbox._terminate_group(process)

    killpg.assert_called_once_with(4242, signal.SIGTERM)
    process.wait.assert_not_called()


def test_task_command_has_mandatory_containment_flags(tmp_path):
    command = computer_sandbox._podman_task_command(
        "example.invalid/sandbox@sha256:" + "a" * 64,
        input_dir=tmp_path / "input", script="printf ok",
    )
    assert "--network=none" in command
    assert "--read-only" in command
    assert any("relabel=private" in value for value in command)
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--pull=never" in command
    assert "/bin/sh" in command


def test_readiness_requires_matching_passing_report(monkeypatch, tmp_path):
    image = "example.invalid/sandbox@sha256:" + "a" * 64
    report = tmp_path / "qualification.json"
    monkeypatch.setenv("ODYSSEUS_COMPUTER_SANDBOX_IMAGE", image)
    monkeypatch.setattr(computer_sandbox.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(computer_sandbox, "_is_local", lambda value: True)
    monkeypatch.setattr(computer_sandbox, "_is_rootless_runtime", lambda: True)
    monkeypatch.setattr(computer_sandbox, "_REPORT_PATH", report)
    assert computer_sandbox.readiness().qualified is False
    report.write_text(json.dumps({
        "version": computer_sandbox._REPORT_VERSION, "image": image, "passed": True, "complete": True,
        "checks": ["rootless_podman", "read_only_input", "private_writable_task", "network_none", "socket_absence", "host_path_absence", "resource_limits", "command_inventory", "descendant_cleanup"],
    }), encoding="utf-8")
    assert computer_sandbox.readiness().qualified is True


def test_readiness_requires_a_qualified_command_inventory(monkeypatch, tmp_path):
    image = "example.invalid/sandbox@sha256:" + "a" * 64
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps({
        "version": computer_sandbox._REPORT_VERSION, "image": image,
        "passed": True, "complete": True,
        "checks": ["rootless_podman", "read_only_input", "private_writable_task",
                   "network_none", "socket_absence", "host_path_absence",
                   "resource_limits", "descendant_cleanup"],
    }), encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_COMPUTER_SANDBOX_IMAGE", image)
    monkeypatch.setattr(computer_sandbox.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(computer_sandbox, "_is_local", lambda value: True)
    monkeypatch.setattr(computer_sandbox, "_is_rootless_runtime", lambda: True)
    monkeypatch.setattr(computer_sandbox, "_REPORT_PATH", report)

    state = computer_sandbox.readiness()

    assert state.qualified is False
    assert state.reason == "containment_probe_incomplete"


def test_readiness_fails_closed_for_a_corrupt_qualification_report(monkeypatch, tmp_path):
    image = "example.invalid/sandbox@sha256:" + "a" * 64
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps({
        "version": computer_sandbox._REPORT_VERSION, "image": image,
        "passed": True, "complete": True, "checks": None,
    }), encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_COMPUTER_SANDBOX_IMAGE", image)
    monkeypatch.setattr(computer_sandbox.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(computer_sandbox, "_is_local", lambda value: True)
    monkeypatch.setattr(computer_sandbox, "_is_rootless_runtime", lambda: True)
    monkeypatch.setattr(computer_sandbox, "_REPORT_PATH", report)

    state = computer_sandbox.readiness()

    assert state.qualified is False
    assert state.reason == "containment_probe_incomplete"


def test_qualification_fixture_requires_every_advertised_command(monkeypatch, tmp_path):
    image = "example.invalid/sandbox@sha256:" + "a" * 64
    captured_scripts = []
    reports = []
    monkeypatch.setattr(
        computer_sandbox, "readiness",
        lambda: computer_sandbox.ComputerSandboxReadiness(True, True, True, False, "probe_required"),
    )
    monkeypatch.setattr(computer_sandbox, "_image", lambda: image)
    monkeypatch.setattr(computer_sandbox, "_is_rootless_runtime", lambda: True)
    monkeypatch.setattr(computer_sandbox, "_container_exists", lambda _name: False)
    monkeypatch.setattr(computer_sandbox, "_store_report", reports.append)

    def fake_run_bounded(command, **_kwargs):
        captured_scripts.append(command[-1])
        return computer_sandbox._BoundedRun(0, "qualified" if len(captured_scripts) == 1 else "cleanup", "")

    monkeypatch.setattr(computer_sandbox, "_run_bounded", fake_run_bounded)

    result = computer_sandbox.qualify_containment()

    assert result["passed"] is True
    assert "command_inventory" in result["checks"]
    assert len(captured_scripts) == 2
    for command in computer_sandbox._READ_ONLY_COMMANDS:
        assert command in captured_scripts[0]
    assert reports[-1]["complete"] is True


def test_readiness_revokes_a_historic_report_when_podman_is_no_longer_rootless(monkeypatch, tmp_path):
    image = "example.invalid/sandbox@sha256:" + "a" * 64
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps({
        "version": computer_sandbox._REPORT_VERSION, "image": image, "passed": True, "complete": True,
        "checks": ["rootless_podman", "read_only_input", "private_writable_task", "network_none", "socket_absence", "host_path_absence", "resource_limits", "command_inventory", "descendant_cleanup"],
    }), encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_COMPUTER_SANDBOX_IMAGE", image)
    monkeypatch.setattr(computer_sandbox.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(computer_sandbox, "_is_local", lambda value: True)
    monkeypatch.setattr(computer_sandbox, "_is_rootless_runtime", lambda: False)
    monkeypatch.setattr(computer_sandbox, "_REPORT_PATH", report)

    state = computer_sandbox.readiness()

    assert state.qualified is False
    assert state.reason == "podman_not_rootless"


def test_server_owned_scratch_rejects_unqualified_or_symlink_input(monkeypatch, tmp_path):
    monkeypatch.setattr(
        computer_sandbox, "readiness",
        lambda: computer_sandbox.ComputerSandboxReadiness(True, True, True, False, "probe_required"),
    )
    try:
        computer_sandbox.run_server_owned_scratch("printf ok", input_dir=tmp_path)
    except computer_sandbox.SandboxRunError as exc:
        assert exc.code == "sandbox_unavailable"
    else:
        raise AssertionError("unqualified sandbox must never run a task")

    monkeypatch.setattr(
        computer_sandbox, "readiness",
        lambda: computer_sandbox.ComputerSandboxReadiness(True, True, True, True, "qualified"),
    )
    (tmp_path / "target").write_text("fixture", encoding="utf-8")
    (tmp_path / "link").symlink_to(tmp_path / "target")
    try:
        computer_sandbox.run_server_owned_scratch("printf ok", input_dir=tmp_path)
    except computer_sandbox.SandboxRunError as exc:
        assert exc.code == "input_denied"
    else:
        raise AssertionError("symlinked input must never be mounted")


def test_server_owned_scratch_never_converts_bounded_failures_to_success(monkeypatch, tmp_path):
    image = "example.invalid/sandbox@sha256:" + "a" * 64
    monkeypatch.setattr(
        computer_sandbox, "readiness",
        lambda: computer_sandbox.ComputerSandboxReadiness(True, True, True, True, "qualified"),
    )
    monkeypatch.setattr(computer_sandbox, "_image", lambda: image)
    monkeypatch.setattr(computer_sandbox, "_run_bounded", lambda *args, **kwargs: computer_sandbox._BoundedRun(-1, "", "", "command_output_limited"))
    try:
        computer_sandbox.run_server_owned_scratch("printf ok", input_dir=tmp_path)
    except computer_sandbox.SandboxRunError as exc:
        assert exc.code == "command_output_limited"
    else:
        raise AssertionError("output exhaustion must fail the task")


def test_readonly_pipeline_is_structured_allowlisted_and_never_uses_caller_shell_text(tmp_path):
    command = computer_sandbox.ReadOnlyCommand(("find", ".", "-name", "*.md"))
    rendered = computer_sandbox._display_pipeline((command,))
    assert rendered == "find . -name '*.md'"
    built = computer_sandbox._podman_readonly_pipeline_command(
        "example.invalid/sandbox@sha256:" + "a" * 64,
        input_dir=tmp_path, commands=(command,),
    )
    assert "--network=none" in built
    assert "--read-only" in built
    assert any("relabel=private" in value for value in built)
    assert built.count("--workdir") == 1
    assert built[built.index("--workdir") + 1] == "/inputs"
    assert built[-1] == "set -f; find . -name '*.md'"


def test_readonly_pipeline_rejects_writes_shell_control_and_untrusted_paths():
    denied = [
        ("rm", "-rf", "/inputs"),
        ("find", ".", "-delete"),
        ("sed", "-i", "s/a/b/", "file.md"),
        ("git", "commit", "-m", "no"),
        ("rg", "token", "/etc"),
        ("grep", "x", "file; touch pwned"),
    ]
    for argv in denied:
        try:
            computer_sandbox._validate_readonly_argv(argv)
        except computer_sandbox.SandboxRunError as exc:
            assert exc.code == "command_denied"
        else:
            raise AssertionError(f"command must be denied: {argv!r}")


def test_readonly_pipeline_rejects_command_specific_execution_escapes():
    """Read-tool names must not conceal a second arbitrary executor."""
    denied = [
        ("xargs", "rm", "-rf", "/inputs"),
        ("xargs", "-P", "8", "wc", "-l"),
        ("awk", "BEGIN { system(\"id\") }"),
        ("awk", "{ getline line; print line }"),
        ("sed", "e id"),
        ("sed", "s/x/y/e"),
        ("find", ".", "-fprint", "/tmp/out"),
        ("fd", "--exec", "id"),
        ("rg", "--pre", "id", "needle"),
        ("sort", "--compress-program", "id"),
        ("git", "diff", "--ext-diff"),
    ]
    for argv in denied:
        try:
            computer_sandbox._validate_readonly_argv(argv)
        except computer_sandbox.SandboxRunError as exc:
            assert exc.code == "command_denied"
        else:
            raise AssertionError(f"command-specific escape must be denied: {argv!r}")


def test_readonly_pipeline_allows_bounded_xargs_readonly_runner():
    argv = ("xargs", "-0", "-n", "16", "wc", "-l")
    assert computer_sandbox._validate_readonly_argv(argv) == argv


def test_readonly_pipeline_refuses_execution_rendering_that_would_need_clamping(tmp_path):
    command = computer_sandbox.ReadOnlyCommand(("grep", "x" * 4096))
    try:
        computer_sandbox._podman_readonly_pipeline_command(
            "example.invalid/sandbox@sha256:" + "a" * 64,
            input_dir=tmp_path,
            commands=(command,),
        )
    except computer_sandbox.SandboxRunError as exc:
        assert exc.code == "command_denied"
    else:
        raise AssertionError("a clamped execution command must not be launched")


def test_readonly_pipeline_never_starts_when_sandbox_is_unqualified(monkeypatch, tmp_path):
    monkeypatch.setattr(
        computer_sandbox, "readiness",
        lambda: computer_sandbox.ComputerSandboxReadiness(True, True, True, False, "probe_required"),
    )
    try:
        computer_sandbox.run_admitted_readonly_pipeline(
            (computer_sandbox.ReadOnlyCommand(("ls", ".")),), input_dir=tmp_path,
        )
    except computer_sandbox.SandboxRunError as exc:
        assert exc.code == "sandbox_unavailable"
    else:
        raise AssertionError("unqualified sandbox must never run a command")
