"""Rootless-Podman containment contract for Computer Help.

This is a broker, not a generic subprocess wrapper. It owns the exact
container arguments and accepts neither browser supplied commands nor mounts.
G2D-0 uses this launch profile for its hostile qualification fixture.
"""
from __future__ import annotations

import json
import os
import re
import selectors
import secrets
import shlex
import signal
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from core.constants import DATA_DIR


_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
# Bump whenever the qualification contract gets a materially stronger gate so
# an old report can never authorize a newer execution profile.
_REPORT_VERSION = 4
_REPORT_PATH = Path(DATA_DIR) / "computer_sandbox_qualification.json"
_REQUIRED_FINAL_CHECKS = frozenset({
    "rootless_podman", "read_only_input", "private_writable_task", "network_none",
    "socket_absence", "host_path_absence", "resource_limits", "command_inventory",
    "descendant_cleanup",
})
_MAX_PROBE_OUTPUT = 16 * 1024


class SandboxQualificationError(RuntimeError):
    """Safe reason why the computer executor remains disabled."""


class SandboxRunError(RuntimeError):
    """Stable failure code for a server-owned contained operation."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ComputerSandboxReadiness:
    podman_available: bool
    image_configured: bool
    image_local: bool
    qualified: bool
    reason: str

    def public_payload(self) -> dict:
        return {
            "podman_available": self.podman_available,
            "sandbox_image_configured": self.image_configured,
            "sandbox_image_local": self.image_local,
            "computer_assist_ready": self.qualified,
            "computer_assist_reason": self.reason,
        }


def _image() -> str:
    return os.getenv("ODYSSEUS_COMPUTER_SANDBOX_IMAGE", "").strip()


def _run(args: list[str], *, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, check=False, text=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )


@dataclass(frozen=True)
class _BoundedRun:
    returncode: int
    stdout: str
    stderr: str
    failure: str = ""


@dataclass(frozen=True)
class SandboxRunResult:
    """Internal-only result. Raw output must never be streamed or persisted."""

    output: str
    duration_ms: int


@dataclass(frozen=True)
class ReadOnlyCommand:
    """One broker-admitted command; arguments are never parsed as shell text."""

    argv: tuple[str, ...]


_READ_ONLY_COMMANDS = frozenset({
    "find", "fd", "rg", "grep", "wc", "head", "tail", "sed", "awk", "cut", "tr",
    "sort", "uniq", "stat", "file", "ls", "git",
})
_FORBIDDEN_ARGUMENTS = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-i", "--in-place", "-o", "--output",
})
_READ_ONLY_GIT_SUBCOMMANDS = frozenset({"status", "diff", "log", "show", "ls-files", "grep"})


def _validate_readonly_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Reject a command before it reaches a shell or container process.

    This is defense in depth. The Podman profile is authoritative; validation
    prevents accidental expansion of the command language and keeps the
    process display safe to persist. Paths are confined to the supplied
    read-only /inputs mount rather than the container root.
    """
    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)):
        raise SandboxRunError("command_denied")
    values = tuple(argv)
    if not values or len(values) > 64 or any(not isinstance(item, str) or not item or "\x00" in item or len(item) > 4096 for item in values):
        raise SandboxRunError("command_denied")
    command = values[0]
    if command not in _READ_ONLY_COMMANDS:
        raise SandboxRunError("command_denied")
    if command == "git" and (len(values) < 2 or values[1] not in _READ_ONLY_GIT_SUBCOMMANDS):
        raise SandboxRunError("command_denied")
    for argument in values[1:]:
        if argument in _FORBIDDEN_ARGUMENTS or argument.startswith("--output=") or argument.startswith("--in-place="):
            raise SandboxRunError("command_denied")
        # Shell control characters are ordinary data to execve, but rejecting
        # them makes the plan representation unambiguous and prevents later
        # code from accidentally converting this structured format to text.
        if any(char in argument for char in ("\n", "\r", "\x00", "`", "$", ";", "&", "|", ">", "<")):
            raise SandboxRunError("command_denied")
        if argument.startswith("/") and not argument.startswith("/inputs/") and argument != "/inputs":
            raise SandboxRunError("command_denied")
        if argument == ".." or argument.startswith("../") or "/../" in argument:
            raise SandboxRunError("command_denied")
    return values


def _display_pipeline(commands: Sequence[ReadOnlyCommand]) -> str:
    """Render a bounded shell-like preview from already-validated argv."""
    return " | ".join(shlex.join(command.argv) for command in commands)[:4096]


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the Podman client and all descendants it owns, best-effort."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass


def _run_bounded(args: list[str], *, timeout: float, output_limit: int) -> _BoundedRun:
    """Run a broker-owned command without buffering unbounded container output."""
    process = subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    failure = ""
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "command_timeout"; _terminate_group(process); break
            for key, _ in selector.select(min(remaining, 0.2)):
                data = os.read(key.fd, 4096)
                if not data:
                    selector.unregister(key.fileobj); continue
                chunks[key.data].extend(data)
                if sum(len(value) for value in chunks.values()) > output_limit:
                    failure = "command_output_limited"; _terminate_group(process); break
            if failure:
                break
            if process.poll() is not None:
                # Let the pipe EOF arrive without allowing a busy spin.
                continue
        if process.poll() is None:
            _terminate_group(process)
        return _BoundedRun(
            process.returncode if process.returncode is not None else -1,
            bytes(chunks["stdout"][:output_limit]).decode("utf-8", "replace"),
            bytes(chunks["stderr"][:output_limit]).decode("utf-8", "replace"), failure,
        )
    finally:
        selector.close()


def _is_local(image: str) -> bool:
    if not image or not shutil.which("podman"):
        return False
    try:
        return _run(["podman", "image", "exists", image], timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _is_rootless_runtime() -> bool:
    """Check the currently active Podman mode, not just a historic probe.

    A qualification report is evidence for the pinned image/profile, but it
    cannot authorize a later rootful Podman configuration. Every admission
    therefore rechecks this property before a container is started.
    """
    if not shutil.which("podman"):
        return False
    try:
        result = _run(["podman", "info", "--format", "{{.Host.Security.Rootless}}"], timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _load_report() -> dict | None:
    try:
        data = json.loads(_REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == _REPORT_VERSION else None


def readiness() -> ComputerSandboxReadiness:
    image = _image()
    configured = bool(_DIGEST_IMAGE.fullmatch(image))
    podman = bool(shutil.which("podman"))
    image_local = configured and _is_local(image)
    report = _load_report()
    rootless = podman and _is_rootless_runtime()
    qualified = bool(
        rootless and configured and image_local and report and report.get("image") == image
        and report.get("passed") is True and report.get("complete") is True
        and _REQUIRED_FINAL_CHECKS.issubset(set(report.get("checks", [])))
    )
    if qualified:
        reason = "qualified"
    elif not podman:
        reason = "podman_unavailable"
    elif not rootless:
        reason = "podman_not_rootless"
    elif not configured:
        reason = "pinned_sandbox_image_required"
    elif not image_local:
        reason = "sandbox_image_not_local"
    else:
        reason = "containment_probe_incomplete"
    return ComputerSandboxReadiness(podman, configured, image_local, qualified, reason)


def _podman_task_command(image: str, *, input_dir: Path, script: str, name: str | None = None) -> list[str]:
    """The non-negotiable command-only container boundary."""
    command = [
        "podman", "run", "--rm", "--pull=never", "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--userns=keep-id",
        "--pids-limit=64", "--memory=512m", "--cpus=2", "--ulimit", "nofile=256:256",
        "--http-proxy=false", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--tmpfs", "/home/sandbox:rw,noexec,nosuid,nodev,size=16m",
        "--tmpfs", "/task:rw,noexec,nosuid,nodev,size=32m",
        # The broker supplies fresh directories and will reject nested mount
        # points before general task admission. Podman does not support Docker's
        # non-standard `rbind=false` mount option.
        # Fedora SELinux denies an unlabeled bind mount to the image's
        # unprivileged command user even when its POSIX mode is readable.
        # This is a fresh server-created temporary input tree, never a user
        # workspace, so private relabeling cannot relabel user data.
        "--mount", f"type=bind,src={input_dir},dst=/inputs,ro=true,relabel=private",
        "--workdir", "/task", "--env", "HOME=/home/sandbox", "--env", "TMPDIR=/tmp",
        "--env", "PATH=/usr/bin:/bin", "--env", "LC_ALL=C",
    ]
    if name:
        command.extend(["--name", name])
    command.extend([
        "--entrypoint", "/bin/sh",
        image, "-ec", script,
    ])
    return command


def _podman_readonly_pipeline_command(image: str, *, input_dir: Path, commands: Sequence[ReadOnlyCommand]) -> list[str]:
    """Build a shell command from validated argv only; no caller text is used.

    A pipeline is useful for routine inspection (for example ``find | wc``),
    but the shell is only a transport for the broker-rendered argv vectors.
    The user/model cannot inject redirects, substitutions, loops, or a second
    command into this string.
    """
    rendered = _display_pipeline(commands)
    base = _podman_task_command(image, input_dir=input_dir, script="true")
    # Strip ``--entrypoint /bin/sh IMAGE -ec SCRIPT`` and replace it with the
    # controlled pipeline.  Keep every isolation flag from the base command.
    return base[:-5] + ["--workdir", "/inputs", "--entrypoint", "/bin/sh", image, "-ec", f"set -f; {rendered}"]


def _container_exists(name: str) -> bool:
    try:
        return _run(["podman", "container", "exists", name], timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def _validate_input_directory(input_dir: Path) -> Path:
    """Allow only a server-created, ordinary input tree as a read-only mount."""
    resolved = input_dir.resolve(strict=True)
    if not resolved.is_dir() or resolved.is_symlink():
        raise SandboxRunError("input_denied")
    for entry in resolved.rglob("*"):
        if entry.is_symlink() or entry.is_mount():
            raise SandboxRunError("input_denied")
    return resolved


def run_server_owned_scratch(script: str, *, input_dir: Path, timeout: float = 30.0) -> SandboxRunResult:
    """Run a server-authored task in the qualified disposable container.

    It is deliberately not an HTTP route and does not accept browser data. A
    later command-admission layer may call it only after parsing a model plan
    into server-owned policy objects. Scratch is the only writable location.
    """
    state = readiness()
    if not state.qualified:
        raise SandboxRunError("sandbox_unavailable")
    if not isinstance(script, str) or not script or "\x00" in script or len(script) > 16 * 1024:
        raise SandboxRunError("command_denied")
    try:
        mounted_input = _validate_input_directory(input_dir)
    except (OSError, RuntimeError):
        raise SandboxRunError("input_denied") from None
    started = time.monotonic()
    try:
        result = _run_bounded(
            _podman_task_command(_image(), input_dir=mounted_input, script=script),
            timeout=min(max(timeout, 1.0), 30.0), output_limit=256 * 1024,
        )
    except (OSError, subprocess.SubprocessError):
        raise SandboxRunError("worker_died") from None
    if result.failure:
        raise SandboxRunError(result.failure)
    if result.returncode != 0:
        raise SandboxRunError("command_failed")
    return SandboxRunResult(output=result.stdout, duration_ms=int((time.monotonic() - started) * 1000))


def run_admitted_readonly_pipeline(
    commands: Sequence[ReadOnlyCommand], *, input_dir: Path, timeout: float = 30.0,
) -> SandboxRunResult:
    """Run an allowlisted inspection pipeline inside the qualified sandbox.

    This is deliberately an internal broker primitive. A future agent tool
    must construct ``ReadOnlyCommand`` values from a strict schema; browser
    callers never submit a raw command, shell text, mount, or image option.
    The input tree is supplied by the server and is mounted read-only.
    """
    if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)) or not commands or len(commands) > 4:
        raise SandboxRunError("command_denied")
    admitted = tuple(ReadOnlyCommand(_validate_readonly_argv(command.argv)) if isinstance(command, ReadOnlyCommand) else None for command in commands)
    if any(command is None for command in admitted):
        raise SandboxRunError("command_denied")
    state = readiness()
    if not state.qualified:
        raise SandboxRunError("sandbox_unavailable")
    try:
        mounted_input = _validate_input_directory(input_dir)
    except (OSError, RuntimeError):
        raise SandboxRunError("input_denied") from None
    started = time.monotonic()
    try:
        result = _run_bounded(
            _podman_readonly_pipeline_command(_image(), input_dir=mounted_input, commands=admitted),
            timeout=min(max(timeout, 1.0), 30.0), output_limit=256 * 1024,
        )
    except (OSError, subprocess.SubprocessError):
        raise SandboxRunError("worker_died") from None
    if result.failure:
        raise SandboxRunError(result.failure)
    if result.returncode != 0:
        raise SandboxRunError("command_failed")
    return SandboxRunResult(output=result.stdout, duration_ms=int((time.monotonic() - started) * 1000))


def _store_report(report: dict) -> None:
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _REPORT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    temporary.replace(_REPORT_PATH)


def qualify_containment() -> dict:
    """Run a fixed hostile fixture and persist only a safe pass/fail report."""
    state = readiness()
    if not state.podman_available:
        raise SandboxQualificationError("Rootless Podman is not available.")
    image = _image()
    if not state.image_configured:
        raise SandboxQualificationError("Set a digest-pinned computer sandbox image before qualification.")
    if not state.image_local:
        raise SandboxQualificationError("The reviewed computer sandbox image is not present locally.")
    started = time.monotonic()
    checks: list[str] = []
    passed = False
    try:
        if not _is_rootless_runtime():
            raise SandboxQualificationError("Podman is not operating rootlessly.")
        checks.append("rootless_podman")
        with tempfile.TemporaryDirectory(prefix="odysseus-computer-probe-") as root:
            root_path = Path(root); inputs = root_path / "inputs"; marker = root_path / "host-only"
            # The temporary parent remains private. The direct container mount
            # needs a traversable input directory because the reviewed image
            # intentionally runs commands as an unprivileged user.
            inputs.mkdir(mode=0o755)
            (inputs / "sentinel.txt").write_text("read-only fixture", encoding="utf-8")
            marker.write_text("must not appear in container", encoding="utf-8")
            home = str(Path.home()).replace("'", "'\\''")
            marker_path = str(marker).replace("'", "'\\''")
            script = f"""
              test -r /inputs/sentinel.txt; test ! -w /inputs/sentinel.txt
              find /inputs -maxdepth 1 -name sentinel.txt -print | grep -qx /inputs/sentinel.txt
              for tool in {' '.join(sorted(_READ_ONLY_COMMANDS))}; do command -v "$tool" >/dev/null; done
              test -w /task; test -w /tmp
              test ! -e /run/user/$(id -u)/podman/podman.sock
              test ! -e /var/run/docker.sock; test ! -e /host-home
              test ! -e /workspace; test ! -e '{home}'; test ! -e '{marker_path}'
              test "$(ulimit -n)" -eq 256
              test "$(tail -n +2 /proc/net/route | wc -l)" -eq 0
              printf qualified
            """
            name = f"odysseus-probe-{secrets.token_hex(8)}"
            result = _run_bounded(
                _podman_task_command(image, input_dir=inputs, script=script, name=name),
                timeout=30, output_limit=_MAX_PROBE_OUTPUT,
            )
            if result.failure or result.returncode != 0 or result.stdout.strip() != "qualified":
                raise SandboxQualificationError("The command container did not satisfy the containment fixture.")
            checks.extend([
                "read_only_input", "private_writable_task", "network_none", "socket_absence",
                "host_path_absence", "resource_limits", "command_inventory",
            ])
            # `--rm` must also remove a background descendant container. The
            # shell exits while a child is sleeping; after the client returns
            # the named container must no longer exist.
            cleanup_name = f"odysseus-cleanup-{secrets.token_hex(8)}"
            cleanup = _run_bounded(
                _podman_task_command(image, input_dir=inputs, name=cleanup_name, script="sleep 20 & printf cleanup"),
                timeout=10, output_limit=_MAX_PROBE_OUTPUT,
            )
            if cleanup.failure or cleanup.returncode != 0 or cleanup.stdout.strip() != "cleanup" or _container_exists(cleanup_name):
                raise SandboxQualificationError("The container cleanup fixture did not complete.")
            checks.append("descendant_cleanup")
        passed = True
    except (OSError, subprocess.SubprocessError, SandboxQualificationError):
        passed = False
    report = {
        "version": _REPORT_VERSION, "image": image, "passed": passed,
        "complete": passed and _REQUIRED_FINAL_CHECKS.issubset(checks), "checks": checks,
        "failure": "" if passed else "containment_probe_failed",
        "duration_ms": int((time.monotonic() - started) * 1000), "completed_at": int(time.time()),
    }
    _store_report(report)
    if not passed:
        raise SandboxQualificationError("The containment probe failed; Computer Help execution remains disabled.")
    return {"passed": True, "complete": report["complete"], "checks": checks, "duration_ms": report["duration_ms"]}
