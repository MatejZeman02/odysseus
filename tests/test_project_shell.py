"""The offline project shell: containment of the sandbox and its readiness."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import src.project_shell as project_shell


def _live_ready() -> bool:
    try:
        return project_shell.readiness().ready
    except Exception:
        return False


LIVE = pytest.mark.skipif(not _live_ready(), reason="requires a working unprivileged Bubblewrap")


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "https://user:secret-token@example.invalid/repo.git"],
        check=True,
    )
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=do-not-leak\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "first"], check=True)
    return root


def test_argv_unshares_everything_and_clears_the_environment():
    argv = project_shell._argv("/usr/bin/bwrap", 7, overlay=True, extra=[], command=["/usr/bin/bash", "-c", "true"])
    assert "--unshare-all" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--clearenv" in argv
    assert "--die-with-parent" in argv and "--new-session" in argv
    assert argv[argv.index("--overlay-src") + 1] == "/proc/self/fd/7"
    assert argv[argv.index("--tmp-overlay") + 1] == "/workspace"
    # Nothing from the host home or the Odysseus data directory is mounted.
    mounted = {argv[i + 2] for i, value in enumerate(argv) if value in {"--bind", "--ro-bind"}}
    assert not any(target.startswith(("/home", "/root", "/var")) for target in mounted)
    assert "--bind" not in argv

    readonly = project_shell._argv("/usr/bin/bwrap", 7, overlay=False, extra=[], command=["true"])
    assert "--overlay-src" not in readonly
    index = readonly.index("/proc/self/fd/7")
    assert readonly[index - 1] == "--ro-bind" and readonly[index + 1] == "/workspace"


def test_git_config_copy_drops_credentials_from_remote_urls(checkout, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    args = project_shell._sanitized_git_config(checkout, scratch)
    assert args[-1] == "/workspace/.git/config"
    cleaned = Path(args[1]).read_text(encoding="utf-8")
    assert "secret-token" not in cleaned
    assert "https://example.invalid/repo.git" in cleaned


def test_secret_files_and_folders_are_masked(checkout):
    (checkout / "sub").mkdir()
    (checkout / "sub" / ".ssh").mkdir()
    (checkout / "sub" / "id_rsa").write_text("key", encoding="utf-8")
    args = project_shell._masks(checkout)
    pairs = list(zip(args, args[1:]))
    assert ("/dev/null", "/workspace/.env") in pairs
    assert ("/dev/null", "/workspace/sub/id_rsa") in pairs
    assert ("--tmpfs", "/workspace/sub/.ssh") in pairs


def test_missing_bubblewrap_reports_a_readable_reason(monkeypatch):
    monkeypatch.setattr(project_shell.shutil, "which", lambda _name: None)
    monkeypatch.setattr(project_shell, "_readiness_cache", None)
    state = project_shell.readiness(refresh=True)
    assert state.ready is False
    assert state.reason == "bubblewrap_unavailable"
    assert "bwrap" in project_shell.unavailable_reason(state.reason)
    monkeypatch.setattr(project_shell, "_readiness_cache", None)
    with pytest.raises(project_shell.ShellUnavailable):
        project_shell.run("/tmp", "true")
    monkeypatch.setattr(project_shell, "_readiness_cache", None)


@LIVE
def test_commands_see_the_project_and_git_but_cannot_change_them(checkout):
    result = project_shell.run(checkout, "cat README.md && git log --oneline | wc -l && git remote -v")
    assert result.exit_code == 0
    assert "# Project" in result.output
    assert "secret-token" not in result.output

    result = project_shell.run(checkout, "echo changed > README.md; touch new-file; cat README.md")
    assert (checkout / "README.md").read_text(encoding="utf-8") == "# Project\n"
    assert not (checkout / "new-file").exists()


@LIVE
def test_sandbox_has_no_network_no_secrets_and_no_host_home(checkout):
    result = project_shell.run(
        checkout,
        "cat .env; echo ---; ls /home 2>&1; echo ---; grep -v -e '^Inter' -e '^ face' -e '^ *lo:' /proc/net/dev | wc -l; "
        "echo ---; env | sort",
    )
    sections = result.output.split("---")
    assert "do-not-leak" not in result.output
    assert sections[2].strip() == "0"
    assert "ODYSSEUS" not in sections[3]
    assert "HOME=/tmp" in sections[3]


@LIVE
def test_timeout_stops_the_command_and_keeps_its_output(checkout):
    result = project_shell.run(checkout, "echo started; sleep 30", timeout=1)
    assert result.timed_out is True
    assert result.exit_code == 124
    assert "started" in result.output
