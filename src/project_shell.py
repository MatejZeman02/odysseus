"""Offline, discardable shell for Companion project chats.

The owner grants "Run commands" for one project chat. The agent then gets a
plain ``bash`` inside Bubblewrap with the project checkout mounted at
``/workspace``. Three properties make that safe to hand to a model without
per-command approval:

* **No network.** Every namespace is unshared, including the network one, so
  nothing the command reads can leave the machine except through the tool
  result the model already sees.
* **Nothing persists.** The checkout is the read-only lower layer of an
  overlay whose writes land in a private tmpfs that disappears with the
  sandbox. Builds and tests can write freely, the real files never change.
  Real edits go through ``src/project_patches.py`` like every other project
  write. On kernels without unprivileged overlayfs the checkout is mounted
  read-only instead, and writes fail rather than persist.
* **No host authority.** The sandbox sees ``/usr``, a handful of loader files
  from ``/etc``, a synthetic user and hosts file, and the project. No home
  directory, no Odysseus data, no sockets, no credentials, no capabilities.

The browser never supplies a mount, a path, or a policy. The workspace comes
from the server-side project binding, pinned by inode before use.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.path_identity import PathIdentityError, pin_directory

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
MAX_COMMAND_CHARS = 16_000
# The model sees the start and the end of long output, which is where build
# and test runners put the useful part.
_HEAD_BYTES = 24 * 1024
_TAIL_BYTES = 16 * 1024
# ``ulimit -f`` counts 1 KiB blocks in bash: 256 MiB per written file.
_FILE_SIZE_LIMIT_KIB = 256 * 1024
_SENSITIVE_WALK_LIMIT = 20_000
_SKIP_WALK_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "target", "dist", "build"})

# Files the native read tools refuse inside a workspace. The shell masks the
# same names so granting it does not quietly widen what the model can read.
_MASKED_NAMES = frozenset({
    ".env", ".netrc", ".ssh", ".gnupg", ".gitconfig", ".git-credentials",
    "authorized_keys", "id_rsa", "id_ed25519", "id_ecdsa", "known_hosts",
})

# Loader and locale files a dynamically linked toolchain needs. Nothing here
# names a user, a secret, or a service endpoint.
_ETC_FILES = (
    "alternatives", "ld.so.cache", "ld.so.conf", "ld.so.conf.d", "localtime",
    "mime.types", "protocols", "services",
)

_CREDENTIAL_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@")


class ShellUnavailable(RuntimeError):
    """The sandbox cannot run on this host. ``code`` is safe to show."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ShellReadiness:
    ready: bool
    reason: str = ""
    discardable_writes: bool = False


@dataclass(frozen=True)
class ShellResult:
    output: str
    exit_code: int
    timed_out: bool
    truncated: bool
    duration_ms: int


_UNAVAILABLE_REASONS = {
    "bubblewrap_unavailable": "Bubblewrap (bwrap) is not installed on this computer.",
    "sandbox_probe_failed": "Bubblewrap could not start an offline sandbox here. Unprivileged user namespaces may be disabled.",
    "shell_unavailable": "No bash or sh was found under /usr.",
    "workspace_unavailable": "The project folder could not be opened safely. Check that it still exists and is not a symlink.",
}


def unavailable_reason(code: str | None) -> str:
    return _UNAVAILABLE_REASONS.get(str(code or ""), "The offline command sandbox is unavailable.")


def _layout_args() -> list[str]:
    """Mount ``/usr`` and recreate the host's top-level links into it.

    Fedora and Debian point ``/bin`` and ``/lib64`` at ``/usr``, older layouts
    keep real directories. Either way the sandbox mirrors what the host has,
    read-only.
    """
    args = ["--ro-bind", "/usr", "/usr"]
    for name in ("bin", "sbin", "lib", "lib64", "lib32", "libx32"):
        host = Path("/") / name
        try:
            info = host.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode):
            args += ["--symlink", os.readlink(host), str(host)]
        elif stat.S_ISDIR(info.st_mode):
            args += ["--ro-bind", str(host), str(host)]
    for name in _ETC_FILES:
        host = Path("/etc") / name
        if host.exists():
            args += ["--ro-bind", str(host), str(host)]
    return args


def _shell_binary() -> str:
    for candidate in ("/usr/bin/bash", "/usr/bin/sh"):
        if os.access(candidate, os.X_OK):
            return candidate
    raise ShellUnavailable("shell_unavailable")


def _identity_files(directory: Path) -> list[str]:
    """Write a one-user passwd/group and a loopback-only hosts file."""
    uid, gid = os.getuid(), os.getgid()
    files = {
        "passwd": f"odysseus:x:{uid}:{gid}:Odysseus sandbox:/tmp:/usr/bin/bash\n",
        "group": f"odysseus:x:{gid}:\n",
        "hosts": "127.0.0.1 localhost\n::1 localhost\n",
        "hostname": "sandbox\n",
    }
    args: list[str] = []
    for name, content in files.items():
        path = directory / name
        path.write_text(content, encoding="utf-8")
        args += ["--ro-bind", str(path), f"/etc/{name}"]
    return args


def _sanitized_git_config(root: Path, directory: Path) -> list[str]:
    """Hide credentials embedded in remote URLs of ``.git/config``.

    ``git remote -v`` would otherwise print a token straight into the model's
    context. The sandbox gets a copy with the userinfo removed.
    """
    config = root / ".git" / "config"
    try:
        info = config.lstat()
    except OSError:
        return []
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
        return []
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    cleaned = directory / "git-config"
    cleaned.write_text(_CREDENTIAL_URL.sub(r"\1", text), encoding="utf-8")
    return ["--ro-bind", str(cleaned), "/workspace/.git/config"]


def _masks(root: Path) -> list[str]:
    """Cover secret-looking files and folders with empty stand-ins.

    The walk is bounded and skips dependency and VCS folders, so a huge
    checkout costs at most a few milliseconds. What it finds is masked, what
    lies past the bound stays visible, which is no worse than ``read_file``.
    """
    args: list[str] = []
    seen = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        relative = Path(current).relative_to(root)
        for name in list(dirs):
            if name.casefold() in _MASKED_NAMES:
                args += ["--tmpfs", str(Path("/workspace") / relative / name)]
                dirs.remove(name)
            elif name in _SKIP_WALK_DIRS:
                dirs.remove(name)
        for name in files:
            if name.casefold() in _MASKED_NAMES:
                path = Path(current) / name
                try:
                    if stat.S_ISREG(path.lstat().st_mode):
                        args += ["--ro-bind", "/dev/null", str(Path("/workspace") / relative / name)]
                except OSError:
                    continue
        seen += len(dirs) + len(files)
        if seen > _SENSITIVE_WALK_LIMIT:
            break
    return args


def _argv(
    bwrap: str,
    workspace_fd: int,
    *,
    overlay: bool,
    extra: list[str],
    command: list[str],
) -> list[str]:
    source = f"/proc/self/fd/{workspace_fd}"
    workspace = (
        ["--overlay-src", source, "--tmp-overlay", "/workspace"]
        if overlay
        else ["--ro-bind", source, "/workspace"]
    )
    return [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--cap-drop", "ALL",
        *_layout_args(),
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        *workspace,
        *extra,
        "--chdir", "/workspace",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp",
        "--setenv", "USER", "odysseus",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "TERM", "dumb",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "PAGER", "cat",
        "--setenv", "GIT_PAGER", "cat",
        "--setenv", "GIT_CONFIG_NOSYSTEM", "1",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--",
        *command,
    ]


_readiness_lock = threading.Lock()
_readiness_cache: ShellReadiness | None = None


def _probe(bwrap: str, overlay: bool) -> bool:
    with tempfile.TemporaryDirectory(prefix="odysseus-shell-probe-") as temporary:
        root = Path(temporary) / "project"
        root.mkdir()
        (root / "marker").write_text("ok\n", encoding="utf-8")
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            # The probe proves the three properties the tool relies on: the
            # project is readable, writes do not reach the host copy, and no
            # network interface other than loopback exists.
            script = (
                "cat marker >/dev/null || exit 3; "
                "echo changed > marker 2>/dev/null; "
                "grep -v -e '^Inter' -e '^ face' -e '^ *lo:' /proc/net/dev | grep -q . && exit 4; "
                "exit 0"
            )
            argv = _argv(
                bwrap, fd, overlay=overlay, extra=[],
                command=[_shell_binary(), "--noprofile", "--norc", "-c", script],
            )
            result = subprocess.run(
                argv, pass_fds=(fd,), capture_output=True, timeout=20,
                start_new_session=True, check=False,
            )
        except (OSError, subprocess.SubprocessError, ShellUnavailable):
            return False
        finally:
            os.close(fd)
        return result.returncode == 0 and (root / "marker").read_text(encoding="utf-8") == "ok\n"


def readiness(*, refresh: bool = False) -> ShellReadiness:
    """Probe once per process whether the offline sandbox works here."""
    global _readiness_cache
    with _readiness_lock:
        if _readiness_cache is not None and not refresh:
            return _readiness_cache
        bwrap = shutil.which("bwrap")
        if not bwrap:
            state = ShellReadiness(False, "bubblewrap_unavailable")
        else:
            try:
                _shell_binary()
            except ShellUnavailable as error:
                state = ShellReadiness(False, error.code)
            else:
                if _probe(bwrap, overlay=True):
                    state = ShellReadiness(True, "", discardable_writes=True)
                elif _probe(bwrap, overlay=False):
                    state = ShellReadiness(True, "", discardable_writes=False)
                else:
                    state = ShellReadiness(False, "sandbox_probe_failed")
        _readiness_cache = state
        return state


def _collect(process: subprocess.Popen, deadline: float) -> tuple[bytes, bool, bool]:
    """Read merged output until EOF or the deadline, keeping head and tail."""
    head = bytearray()
    tail = bytearray()
    dropped = False
    stream = process.stdout
    assert stream is not None
    fd = stream.fileno()
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        room = _HEAD_BYTES - len(head)
        if room > 0:
            head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            tail += chunk
            if len(tail) > _TAIL_BYTES:
                del tail[: len(tail) - _TAIL_BYTES]
                dropped = True
    if tail:
        if dropped:
            head += b"\n... [output truncated] ...\n"
        head += tail
    return bytes(head), dropped, timed_out


def run(workspace: str | Path, command: str, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> ShellResult:
    """Run one bash command against the pinned project in a fresh sandbox."""
    state = readiness()
    if not state.ready:
        raise ShellUnavailable(state.reason or "sandbox_probe_failed")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise ShellUnavailable("bubblewrap_unavailable")
    try:
        pinned = pin_directory(Path(workspace))
    except PathIdentityError as exc:
        raise ShellUnavailable("workspace_unavailable") from exc
    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
    fd = os.open(pinned.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise ShellUnavailable("workspace_unavailable")
        with tempfile.TemporaryDirectory(prefix="odysseus-shell-") as temporary:
            directory = Path(temporary)
            extra = [
                *_identity_files(directory),
                *_sanitized_git_config(pinned.path, directory),
                *_masks(pinned.path),
            ]
            # ``eval "$1"`` runs the model's text in the shell that already
            # applied the limits, so a command cannot raise them again.
            # Bubblewrap needs the checkout handle only to mount it and then
            # leaves it open in this shell. Through /proc/self/fd it reaches
            # the real checkout past the overlay and the masks, so it is
            # closed before the model's text runs.
            wrapper = (
                f"exec {fd}<&-; "
                f"ulimit -c 0 2>/dev/null; ulimit -f {_FILE_SIZE_LIMIT_KIB} 2>/dev/null; "
                'eval "$1"'
            )
            argv = _argv(
                bwrap, fd, overlay=state.discardable_writes, extra=extra,
                command=[_shell_binary(), "--noprofile", "--norc", "-c", wrapper, "odysseus-shell", command],
            )
            started = time.monotonic()
            process = subprocess.Popen(
                argv, pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                raw, truncated, timed_out = _collect(process, started + timeout)
                if timed_out:
                    os.killpg(process.pid, signal.SIGKILL)
                code = process.wait(timeout=10)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            finally:
                if process.stdout:
                    process.stdout.close()
            duration = int((time.monotonic() - started) * 1000)
    finally:
        os.close(fd)
    return ShellResult(
        output=raw.decode("utf-8", errors="replace"),
        exit_code=124 if timed_out else code,
        timed_out=timed_out,
        truncated=truncated,
        duration_ms=duration,
    )
