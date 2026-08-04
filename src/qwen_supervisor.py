"""Rootless, disposable lifecycle for the read-only Qwen Serve worker."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from .qwen_harness import QwenHarnessError, QwenServeClient, create_disposable_config


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
    port: int, environment: dict[str, str], bubblewrap: str = "bwrap",
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
    for key, value in environment.items():
        command.extend(["--setenv", key, value])
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
    log_lines: list[str] = field(default_factory=list)
    log_task: Optional[asyncio.Task] = None


class QwenSupervisor:
    def __init__(self, *, binary: Path, bubblewrap: str = "bwrap"):
        self.binary = binary.resolve()
        self.qwen_root = self.binary.parent.parent.parent
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
            "OPENAI_API_KEY": bridge_token, "OPENAI_BASE_URL": bridge_url,
            "OPENAI_MODEL": bridge_model, "QWEN_MODEL": bridge_model,
            "QWEN_SERVER_TOKEN": config.server_token,
            "QWEN_CODE_SAFE_MODE": "true", "QWEN_SERVE_NO_MCP_POOL": "1", "NO_COLOR": "1",
        }
        command = bubblewrap_command(
            qwen_root=self.qwen_root, private_home=config.home,
            workspace_root=workspace_root, port=port,
            environment=environment, bubblewrap=self.bubblewrap,
        )
        process = await asyncio.create_subprocess_exec(
            *command, env={}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        client = QwenServeClient(
            f"http://127.0.0.1:{port}", config.server_token, timeout=None,
        )
        runtime = QwenRuntime(process, client, root)
        runtime.log_task = asyncio.create_task(self._drain_logs(runtime))
        self.runtime = runtime
        try:
            async with asyncio.timeout(startup_timeout):
                while True:
                    if process.returncode is not None:
                        raise QwenHarnessError("Qwen Serve exited during startup")
                    try:
                        await client.verify_capabilities()
                        break
                    except (httpx.HTTPError, QwenHarnessError):
                        await asyncio.sleep(0.2)
        except Exception:
            await self.stop()
            raise
        return runtime

    async def run_prompt(self, prompt: str, *, timeout: float = 240) -> tuple[str, dict]:
        if not self.runtime:
            raise QwenHarnessError("Qwen runtime is not active")
        session_id = await self.runtime.client.create_session(cwd="/workspace")
        prompt_id, cursor = await self.runtime.client.prompt(session_id, prompt)
        answer_parts: list[str] = []
        terminal: Optional[dict] = None
        seen_event_ids: set[str] = set()
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
                            if update.get("sessionUpdate") == "agent_message_chunk":
                                content = update.get("content") or {}
                                if isinstance(content, dict) and content.get("text"):
                                    answer_parts.append(str(content["text"]))
                        if event_type in {"turn_complete", "turn_error", "session_died", "client_evicted"}:
                            terminal = event
                            break
                    if terminal:
                        break
        except (asyncio.CancelledError, TimeoutError):
            await self.runtime.client.cancel(session_id)
            raise
        if not terminal or terminal.get("type") != "turn_complete":
            raise QwenHarnessError(f"Qwen turn failed: {(terminal or {}).get('type', 'missing terminal event')}")
        if ((terminal.get("data") or {}).get("stopReason")) != "end_turn":
            raise QwenHarnessError("Qwen turn did not finish normally")
        return "".join(answer_parts), {"prompt_id": prompt_id, "terminal": terminal}

    async def stop(self) -> None:
        runtime, self.runtime = self.runtime, None
        if not runtime:
            return
        runtime.process.terminate()
        try:
            await asyncio.wait_for(runtime.process.wait(), timeout=10)
        except asyncio.TimeoutError:
            runtime.process.kill()
            await runtime.process.wait()
        if runtime.log_task:
            await runtime.log_task
        await runtime.client.close()
        shutil.rmtree(runtime.root, ignore_errors=True)

    @staticmethod
    async def _drain_logs(runtime: QwenRuntime) -> None:
        if not runtime.process.stdout:
            return
        while line := await runtime.process.stdout.readline():
            text = line.decode("utf-8", "replace").strip()
            if text:
                runtime.log_lines.append(text[:500])
                del runtime.log_lines[:-40]
