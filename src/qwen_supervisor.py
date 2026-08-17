"""Rootless, disposable lifecycle for the read-only Qwen Serve worker."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import signal
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx

from .qwen_harness import (
    QwenHarnessError,
    QwenServeClient,
    QwenServeHTTPError,
    create_disposable_config,
)


PINNED_QWEN_VERSION = "0.21.3"


def verify_qwen_binary(binary: Path, *, expected_version: str = PINNED_QWEN_VERSION) -> None:
    if not binary.is_file():
        raise QwenHarnessError("pinned Qwen binary is unavailable")
    result = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0 or result.stdout.strip() != expected_version:
        raise QwenHarnessError(f"Qwen version mismatch; expected {expected_version}")


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def bubblewrap_command(
    *, qwen_root: Path, private_home: Path, workspace_root: Path,
    port: int, bubblewrap: str = "bwrap",
) -> tuple[str, ...]:
    workspace = workspace_root.resolve(strict=True)
    qwen_root = qwen_root.resolve(strict=True)
    private_home = private_home.resolve(strict=True)
    command = [
        bubblewrap, "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
        "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
        "--dir", "/opt", "--ro-bind", str(qwen_root), "/opt/qwen",
        "--dir", "/home", "--dir", "/home/qwen", "--bind", str(private_home), "/home/qwen",
        "--dir", "/workspace", "--ro-bind", str(workspace), "/workspace",
        "--chdir", "/workspace",
    ]
    command.extend([
        "/opt/qwen/node_modules/.bin/qwen", "serve", "--hostname", "127.0.0.1",
        "--port", str(port), "--workspace", "/workspace", "--no-web", "--require-auth",
        "--safe-mode", "--max-sessions", "1", "--max-pending-prompts-per-session", "1",
        "--prompt-deadline-ms", "180000", "--rate-limit", "--chat-recording=false",
    ])
    return tuple(command)


@dataclass
class QwenRuntime:
    process: asyncio.subprocess.Process
    client: QwenServeClient
    root: Path
    # Bubblewrap creates a new session for the contained worker.  Qwen Serve
    # can spawn an ACP child which outlives the Serve launcher, so retaining
    # this group id is essential for disposable-worker teardown.
    process_group_id: Optional[int] = None
    log_lines: list[str] = field(default_factory=list)
    log_task: Optional[asyncio.Task] = None


class QwenSupervisor:
    def __init__(self, *, binary: Path, bubblewrap: str = "bwrap"):
        # Keep the user-facing node_modules/.bin symlink shape when deriving
        # the install root. Resolving first points at @qwen-code/.../cli.js and
        # makes qwen_root one level too deep for /opt/qwen/node_modules/.bin/qwen.
        self.binary = binary.expanduser().absolute()
        self.qwen_root = self.binary.parent.parent.parent.resolve()
        self.bubblewrap = bubblewrap
        self.runtime: Optional[QwenRuntime] = None

    async def start(
        self, *, workspace_root: Path, bridge_url: str, bridge_model: str,
        bridge_token: str, startup_timeout: float = 30,
    ) -> QwenRuntime:
        if self.runtime:
            raise QwenHarnessError("Qwen runtime is already active")
        verify_qwen_binary(self.binary)
        if not shutil.which(self.bubblewrap):
            raise QwenHarnessError("bubblewrap is required for the read-only worker")
        root = Path(tempfile.mkdtemp(prefix="odysseus-qwen-run-"))
        config = create_disposable_config(
            root=root / "config", bridge_url=bridge_url,
            bridge_model=bridge_model, ephemeral_bridge_token=bridge_token,
        )
        port = _free_port()
        environment = {
            "HOME": "/home/qwen", "PATH": "/usr/local/bin:/usr/bin:/bin",
            # settings.json deliberately names this ephemeral key. OPENAI_API_KEY
            # alone is insufficient because Qwen resolves the provider envKey.
            config.token_env_key: bridge_token,
            "OPENAI_API_KEY": bridge_token, "OPENAI_BASE_URL": bridge_url,
            "OPENAI_MODEL": bridge_model, "QWEN_MODEL": bridge_model,
            "QWEN_SERVER_TOKEN": config.server_token,
            "QWEN_CODE_SAFE_MODE": "true", "QWEN_SERVE_NO_MCP_POOL": "1", "NO_COLOR": "1",
        }
        command = bubblewrap_command(
            qwen_root=self.qwen_root, private_home=config.home,
            workspace_root=workspace_root, port=port, bubblewrap=self.bubblewrap,
        )
        # Bubblewrap passes its environment to the contained process. Supplying
        # credentials through execve's environment keeps them out of argv and
        # therefore out of process listings.
        process = await asyncio.create_subprocess_exec(
            *command, env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        client = QwenServeClient(
            f"http://127.0.0.1:{port}", config.server_token, timeout=None,
        )
        runtime = QwenRuntime(process, client, root)
        runtime.log_task = asyncio.create_task(self._drain_logs(runtime))
        self.runtime = runtime
        try:
            await self._wait_until_ready(runtime, startup_timeout=startup_timeout)
            # bwrap remains the host-side launcher.  Its --new-session child
            # (Qwen Serve) is the group leader which can itself spawn ACP.
            # Record that inner group while the process tree still exists.
            runtime.process_group_id = self._sandbox_process_group_id(process.pid)
        except Exception:
            await self.stop()
            raise
        return runtime

    async def _wait_until_ready(self, runtime: QwenRuntime, *, startup_timeout: float) -> None:
        """Wait for the full runtime, not Qwen's early bootstrap capability page."""
        try:
            async with asyncio.timeout(startup_timeout):
                while True:
                    if runtime.process.returncode is not None:
                        raise QwenHarnessError("Qwen Serve exited during startup")
                    try:
                        await runtime.client.verify_runtime_ready()
                        await runtime.client.verify_capabilities()
                        return
                    except QwenServeHTTPError as exc:
                        if not exc.retryable:
                            raise
                        delay = exc.retry_after_seconds if exc.retry_after_seconds is not None else 0.2
                    except httpx.TransportError:
                        # The listener is not up yet. Once it is reachable,
                        # only explicit, pre-admission bootstrap codes retry.
                        delay = 0.2
                    await asyncio.sleep(min(max(delay, 0.05), 1.0))
        except TimeoutError as exc:
            raise QwenHarnessError("Qwen Serve did not become ready before its startup deadline") from exc

    async def _create_session(self) -> str:
        """Retry only explicit pre-admission rejections, never prompt admission."""
        if not self.runtime:
            raise QwenHarnessError("Qwen runtime is not active")
        for attempt in range(3):
            try:
                return await self.runtime.client.create_session(cwd="/workspace")
            except QwenServeHTTPError as exc:
                if not exc.retryable or attempt == 2:
                    raise
                delay = exc.retry_after_seconds if exc.retry_after_seconds is not None else 0.25
                await asyncio.sleep(min(max(delay, 0.05), 1.0))
        raise AssertionError("unreachable")

    async def run_prompt(
        self, prompt: str, *, timeout: float = 240,
        progress_callback: Optional[Callable[[dict], object]] = None,
    ) -> tuple[str, dict]:
        if not self.runtime:
            raise QwenHarnessError("Qwen runtime is not active")
        session_id = await self._create_session()
        prompt_id, cursor = await self.runtime.client.prompt(session_id, prompt)
        # Qwen ACP uses the same agent_message_chunk event for user-facing
        # narration before a tool batch and for the final answer.  Keep the
        # current message round provisional: a following tool call classifies
        # it as commentary, while turn_complete classifies it as the answer.
        pending_message_parts: list[str] = []
        terminal: Optional[dict] = None
        seen_event_ids: set[str] = set()
        # ACP sends the arguments on the initial tool_call, then commonly
        # sends a terminal tool_call_update containing only toolCallId and
        # status.  Preserve the safe call envelope here so completion updates
        # describe the same visible operation instead of creating an empty,
        # unmatched row that is later rendered as a failure.
        tool_calls_by_id: dict[str, dict] = {}

        async def flush_commentary() -> None:
            if not pending_message_parts:
                return
            raw_text = "".join(pending_message_parts)
            pending_message_parts.clear()
            if not self._looks_like_commentary(raw_text):
                return
            text = self._sanitize_commentary_text(raw_text)
            if text:
                await self._emit_progress(progress_callback, {
                    "kind": "commentary",
                    "text": text,
                    "status": "completed",
                })
        try:
            async with asyncio.timeout(timeout):
                # A cleanly dropped SSE connection is retried from its last
                # replay cursor. Duplicate replay frames never duplicate text.
                for _attempt in range(3):
                    async for event in self.runtime.client.events(session_id, last_event_id=cursor):
                        event_id = str(event.get("_sse_id") or event.get("id") or "")
                        if event_id:
                            cursor = event_id
                            if event_id in seen_event_ids:
                                continue
                            seen_event_ids.add(event_id)
                        event_type = str(event.get("type") or event.get("event") or "unknown")
                        if event_type == "session_update":
                            update = ((event.get("data") or {}).get("update") or {})
                            update_kind = update.get("sessionUpdate")
                            if update_kind in {"tool_call", "tool_call_update"}:
                                await flush_commentary()
                                update = self._coalesce_tool_update(update, tool_calls_by_id)
                            progress = self._sanitize_progress_update(update)
                            if progress:
                                await self._emit_progress(progress_callback, progress)
                            if update_kind == "agent_message_chunk":
                                content = update.get("content") or {}
                                if isinstance(content, dict) and content.get("text"):
                                    pending_message_parts.append(str(content["text"]))
                            # agent_thought_chunk is deliberately ignored. It
                            # is private reasoning, not a user-facing update.
                        if event_type in {"turn_complete", "turn_error", "session_died", "client_evicted"}:
                            terminal = event
                            break
                    if terminal:
                        break
        except (asyncio.CancelledError, TimeoutError):
            await self.runtime.client.cancel(session_id)
            raise
        if not terminal or terminal.get("type") != "turn_complete":
            terminal_type = str((terminal or {}).get("type") or "missing_terminal_event")
            terminal_data = (terminal or {}).get("data")
            if not isinstance(terminal_data, dict):
                terminal_data = {}
            # Qwen Serve exposes a closed error-kind vocabulary. Retain only a
            # conservative identifier so callers can present a useful stable
            # message without leaking provider responses or worker logs.
            raw_kind = terminal_data.get("errorKind") or terminal_data.get("code") or terminal_data.get("reason")
            error_kind = str(raw_kind or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9_]{1,80}", error_kind):
                error_kind = ""
            suffix = f":{error_kind}" if error_kind else ""
            raise QwenHarnessError(f"Qwen turn failed: {terminal_type}{suffix}")
        if ((terminal.get("data") or {}).get("stopReason")) != "end_turn":
            raise QwenHarnessError("Qwen turn did not finish normally")
        return "".join(pending_message_parts), {"prompt_id": prompt_id, "terminal": terminal}

    @staticmethod
    async def _emit_progress(callback: Optional[Callable[[dict], object]], payload: dict) -> None:
        if not callback:
            return
        result = callback(payload)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _looks_like_commentary(value: object) -> bool:
        """Reject answer-shaped prose accidentally followed by another tool."""
        if not isinstance(value, str):
            return False
        text = value.strip()
        if not text or len(text) > 600:
            return False
        # Progress is deliberately one or two short prose sentences. A model
        # sometimes drafts its answer, then changes its mind and invokes one
        # more tool; headings and multi-item lists must not be displayed as a
        # giant faux progress update in that case.
        if re.search(r"(?m)^\s*(?:#{1,6}\s|[-*]\s+|\d+[.)]\s+)", text):
            return False
        return len([line for line in text.splitlines() if line.strip()]) <= 3

    @staticmethod
    def _sanitize_commentary_text(value: object) -> str:
        """Return a compact, display-safe user-facing progress sentence."""
        if not isinstance(value, str):
            return ""
        text = " ".join(value.replace("/workspace/", "").replace("/workspace", "workspace").split())
        if not text:
            return ""
        # Commentary crosses the SSE and persistence boundary before the final
        # answer. Redact implementation paths, local bridge URLs, and common
        # credential shapes even though the contained worker should not see
        # reusable secrets in the first place.
        text = re.sub(
            r"(?<![\w:])/(?:home|root|run/user|tmp/odysseus-qwen-run-)(?:/[^\s`\"']+)+",
            "[private path]",
            text,
        )
        text = re.sub(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?(?:/\S*)?", "[local service]", text, flags=re.I)
        text = re.sub(r"\bBearer\s+[A-Za-z0-9._~-]{8,}", "Bearer [redacted]", text, flags=re.I)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[redacted key]", text)
        return text if len(text) <= 800 else f"{text[:799]}✂"

    @staticmethod
    def _coalesce_tool_update(update: dict, calls_by_id: dict[str, dict]) -> dict:
        """Join sparse ACP call updates without exposing the opaque call ID."""
        if not isinstance(update, dict):
            return update
        call_id = str(update.get("toolCallId") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", call_id):
            return update

        previous = calls_by_id.get(call_id, {})
        merged = dict(previous)
        merged.update(update)
        previous_meta = previous.get("_meta") if isinstance(previous.get("_meta"), dict) else {}
        current_meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
        if previous_meta or current_meta:
            merged["_meta"] = {**previous_meta, **current_meta}
        # An absent or skeletal rawInput in an update must not erase the
        # arguments that gave the operation its path/query identity.
        current_input = update.get("rawInput")
        if not isinstance(current_input, dict) or not current_input:
            previous_input = previous.get("rawInput")
            if isinstance(previous_input, dict):
                merged["rawInput"] = previous_input
        calls_by_id[call_id] = merged
        return merged

    @staticmethod
    def _sanitize_progress_update(update: dict) -> Optional[dict]:
        """Convert Qwen's rich tool envelope into a safe companion event."""
        if not isinstance(update, dict) or update.get("sessionUpdate") not in {"tool_call", "tool_call_update"}:
            return None
        meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
        raw_input = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
        tool_name = str(update.get("name") or meta.get("toolName") or "").lower()
        descriptor = " ".join((tool_name, str(update.get("title") or "").lower()))
        if any(token in descriptor for token in ("shell", "bash", "command", "exec")):
            operation = "Shell"
            safe_tool = "shell"
        elif any(token in descriptor for token in ("grep", "search", "find", "glob")):
            operation = "Search project files"
            safe_tool = "search"
        elif any(token in descriptor for token in ("list", "directory", "ls")):
            operation = "List project files"
            safe_tool = "list_files"
        elif any(token in descriptor for token in ("read", "view", "open", "cat")):
            operation = "Read project file"
            safe_tool = "read_file"
        else:
            operation = "Inspect project"
            safe_tool = "inspect_workspace"
        path = None
        for key in ("path", "file", "filename", "file_path", "filePath", "target", "cwd", "directory"):
            value = raw_input.get(key)
            if isinstance(value, str):
                candidate = value.replace("\\\\", "/")
                if candidate.startswith("/workspace/"):
                    candidate = candidate[len("/workspace/"):]
                elif candidate == "/workspace":
                    candidate = "."
                if candidate and not candidate.startswith("/") and ".." not in candidate.split("/"):
                    path = candidate.lstrip("./") or "."
                    break
        if not path and isinstance(update.get("title"), str):
            # Titles are untrusted model data. Only retain an explicitly
            # sandbox-relative fragment, never the rest of the title.
            marker = "/workspace/"
            title_path = update["title"].replace("\\", "/")
            if marker in title_path:
                candidate = title_path.split(marker, 1)[1].split()[0].rstrip(".,:;)")
                if candidate and ".." not in candidate.split("/"):
                    path = candidate
        query = next((raw_input[key] for key in ("query", "pattern", "search", "text")
                      if isinstance(raw_input.get(key), str) and raw_input[key].strip()), "")
        query = " ".join(str(query).split())[:400]
        # Qwen first emits a skeletal search call and later updates it with the
        # actual pattern.  That skeleton can be empty or just a quote character;
        # it performs no useful project operation and must not become the
        # confusing persisted `Search: ""` row in the Companion Process trace.
        if safe_tool == "search" and not query.strip("'\"`"):
            return None
        if safe_tool == "read_file":
            label = command = f"Read: {path or 'workspace'}"
        elif safe_tool == "search":
            target = path or query or "workspace"
            label = f"Search: {target}"
            command = f"Search: {query!r}" + (f" {path}" if path else "")
        elif safe_tool == "list_files":
            label = command = f"List: {path or 'workspace'}"
        elif safe_tool == "shell":
            shell_command = QwenSupervisor._sanitize_shell_command(raw_input.get("command"))
            label = command = f"Shell: {shell_command}" if shell_command else "Shell: read-only workspace operation"
        else:
            label = command = f"Inspect: {path or 'workspace'}"
        status = str(update.get("status") or "working").lower()
        return {
            "operation": operation, "tool": safe_tool, "path": path,
            "label": label, "command": command,
            "status": status if status in {"running", "in_progress", "completed", "failed"} else "working",
        }

    @staticmethod
    def _sanitize_shell_command(value: object) -> str:
        """Permit a display-only command only when it is local and non-secret."""
        if not isinstance(value, str):
            return ""
        command = " ".join(value.replace("/workspace/", "").replace("/workspace", ".").split())
        lowered = command.lower()
        if (not command or len(command) > 2000 or "http://" in lowered or "https://" in lowered
                or any(secret in lowered for secret in ("api_key", "apikey", "authorization", "bearer ", "token="))):
            return ""
        # Absolute host paths must not cross the UI boundary.
        if any(part.startswith("/") for part in command.split()):
            return ""
        return command

    async def stop(self) -> None:
        runtime, self.runtime = self.runtime, None
        if not runtime:
            return
        # Qwen Serve internally starts an ACP child.  In a normal turn the
        # Serve launcher can exit first, leaving that child re-parented; a
        # signal to only runtime.process would then leak a hot Node worker.
        # Bubblewrap's --new-session gives each invocation its own process
        # group, so terminate that complete group instead.
        process_group_id = runtime.process_group_id
        if process_group_id:
            self._signal_process_group(process_group_id, signal.SIGTERM)
        if runtime.process.returncode is None:
            try:
                runtime.process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(runtime.process.wait(), timeout=10)
        except asyncio.TimeoutError:
            if process_group_id:
                self._signal_process_group(process_group_id, signal.SIGKILL)
            try:
                runtime.process.kill()
            except ProcessLookupError:
                pass
            await runtime.process.wait()
        # The launcher may already have exited before the ACP child does.
        # Give its SIGTERM a brief chance, then make teardown deterministic.
        if process_group_id and await self._process_group_is_alive(process_group_id, timeout=1.0):
            self._signal_process_group(process_group_id, signal.SIGKILL)
        if runtime.log_task:
            await runtime.log_task
        await runtime.client.close()
        shutil.rmtree(runtime.root, ignore_errors=True)

    @staticmethod
    def _signal_process_group(process_group_id: int, signal_number: int) -> None:
        try:
            os.killpg(process_group_id, signal_number)
        except ProcessLookupError:
            # The worker already stopped; teardown remains idempotent.
            pass

    @staticmethod
    def _sandbox_process_group_id(launcher_pid: int) -> Optional[int]:
        """Return the nested Qwen Serve process group created by bwrap.

        ``bwrap --new-session`` leaves the host launcher in the application's
        process group and starts the contained program as a nested session
        leader.  Qwen Serve's ACP worker inherits that inner group, so killing
        the launcher group would either miss it or risk the web server.
        """
        pending = [launcher_pid]
        seen = {launcher_pid}
        while pending:
            parent = pending.pop()
            try:
                children = [int(value) for value in Path(
                    f"/proc/{parent}/task/{parent}/children"
                ).read_text().split()]
            except (FileNotFoundError, ProcessLookupError, ValueError):
                continue
            for child in children:
                if child in seen:
                    continue
                seen.add(child)
                try:
                    process_group_id = os.getpgid(child)
                except ProcessLookupError:
                    continue
                if process_group_id == child:
                    return process_group_id
                pending.append(child)
        return None

    @staticmethod
    async def _process_group_is_alive(process_group_id: int, *, timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                os.killpg(process_group_id, 0)
            except ProcessLookupError:
                return False
            if asyncio.get_running_loop().time() >= deadline:
                return True
            await asyncio.sleep(0.05)

    @staticmethod
    async def _drain_logs(runtime: QwenRuntime) -> None:
        if not runtime.process.stdout:
            return
        while line := await runtime.process.stdout.readline():
            text = line.decode("utf-8", "replace").strip()
            if text:
                runtime.log_lines.append(text[:500])
                del runtime.log_lines[:-40]
