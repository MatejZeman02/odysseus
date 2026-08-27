"""Disposable, read-only Qwen Serve configuration and protocol adapter.

No installer or process launcher lives here: a caller must separately authorize
use of a verified user-provided binary.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import httpx


REQUIRED_CAPABILITIES = frozenset({"health", "capabilities", "session_create", "session_events", "require_auth"})


class QwenHarnessError(RuntimeError):
    pass


_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,80}$")
_RETRYABLE_REJECTION_CODES = frozenset({
    # Qwen's bootstrap server may expose capabilities before the full runtime
    # has replaced it. These responses reject the request before admission.
    "bootstrap",
    "daemon_runtime_starting",
    "workspace_runtime_unavailable",
})


class QwenServeHTTPError(QwenHarnessError):
    """Sanitized Qwen Serve rejection with enough structure for safe retries."""

    def __init__(
        self, operation: str, *, status_code: int,
        error_code: str | None = None,
        retry_after_seconds: float | None = None,
    ):
        self.operation = operation
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after_seconds = retry_after_seconds
        self.retryable = status_code == 503 and error_code in _RETRYABLE_REJECTION_CODES
        suffix = f" ({error_code})" if error_code else ""
        # Deliberately exclude the response body: Qwen errors can contain
        # workspace paths and other daemon details that must not reach SSE/UI.
        super().__init__(f"Qwen Serve {operation} failed with HTTP {status_code}{suffix}")


def _http_error(response: httpx.Response, operation: str) -> QwenServeHTTPError:
    payload: dict[str, Any] = {}
    try:
        candidate = response.json()
        if isinstance(candidate, dict):
            payload = candidate
    except (json.JSONDecodeError, ValueError):
        pass
    raw_code = payload.get("code") or payload.get("errorKind") or payload.get("reason")
    error_code = raw_code if isinstance(raw_code, str) and _SAFE_ERROR_CODE.fullmatch(raw_code) else None
    retry_after_seconds = None
    try:
        raw_retry_after = response.headers.get("retry-after")
        if raw_retry_after is not None:
            parsed = float(raw_retry_after)
            if 0 <= parsed <= 60:
                retry_after_seconds = parsed
    except (TypeError, ValueError):
        pass
    return QwenServeHTTPError(
        operation, status_code=response.status_code,
        error_code=error_code, retry_after_seconds=retry_after_seconds,
    )


def _require_success(response: httpx.Response, operation: str) -> None:
    if not response.is_success:
        raise _http_error(response, operation)


@dataclass(frozen=True)
class DisposableQwenConfig:
    path: Path
    home: Path
    token_env_key: str
    server_token: str
    bridge_url: str
    bridge_model: str


@dataclass(frozen=True)
class QwenLaunchSpec:
    """A reviewable launch contract; executing it remains caller-authorized."""
    command: tuple[str, ...]
    environment: dict[str, str]


def create_disposable_config(*, root: Path, bridge_url: str, bridge_model: str, ephemeral_bridge_token: str) -> DisposableQwenConfig:
    """Write a 0600 settings file containing only bridge-facing credentials."""
    if not bridge_url.startswith("http://127.0.0.1") and not bridge_url.startswith("http://localhost"):
        raise ValueError("Qwen bridge must be loopback-only")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    home = root / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    token_env_key = "ODYSSEUS_QWEN_BRIDGE_TOKEN"
    config = {
        "modelProviders": {"openai": [{"id": bridge_model, "baseUrl": bridge_url, "envKey": token_env_key}]},
        "model": {"id": bridge_model},
        "tools": {
            "approvalMode": "plan",
            "core": ["read_file", "grep_search", "glob", "list_directory"],
            "computerUse": {"enabled": False},
            "toolSearch": {"enabled": False},
        },
        "permissions": {
            "allow": ["Read"],
            "deny": ["Shell", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Agent", "Skill", "SaveMemory"],
        },
        "mcpServers": {},
        "memory": {
            "enableManagedAutoMemory": False,
            "enableManagedAutoDream": False,
            "enableAutoSkill": False,
            "enableTeamMemory": False,
            "enableTeamMemorySync": False,
        },
    }
    qwen_home = home / ".qwen"
    qwen_home.mkdir(mode=0o700, exist_ok=True)
    path = qwen_home / "settings.json"
    path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    os.chmod(path, 0o600)
    # The server token is distinct from the single-use bridge token.
    return DisposableQwenConfig(path, home, token_env_key, secrets.token_urlsafe(32), bridge_url, bridge_model)


def build_read_only_launch(*, binary: str, config: DisposableQwenConfig, workspace_root: Path, bridge_token: str, port: int) -> QwenLaunchSpec:
    """Build, but do not run, the isolated Qwen Serve invocation."""
    candidate = Path(workspace_root)
    try:
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("Qwen workspace must be a regular directory")
        workspace = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Qwen workspace is unavailable") from exc
    if port < 1024 or port > 65535:
        raise ValueError("Qwen Serve port must be unprivileged")
    environment = {
        "HOME": str(config.home),
        "PATH": os.environ.get("PATH", ""),
        config.token_env_key: bridge_token,
        "OPENAI_API_KEY": bridge_token,
        "OPENAI_BASE_URL": config.bridge_url,
        "OPENAI_MODEL": config.bridge_model,
        "QWEN_SERVER_TOKEN": config.server_token,
        # Safe mode ignores ambient/project settings, MCP, skills, extensions,
        # hooks, context files, permission rules, and all memory features.
        "QWEN_CODE_SAFE_MODE": "true",
        "QWEN_SERVE_NO_MCP_POOL": "1",
    }
    command = (
        binary, "serve", "--hostname", "127.0.0.1", "--port", str(port),
        "--workspace", str(workspace), "--no-web", "--require-auth",
        "--safe-mode", "--max-sessions", "1", "--max-pending-prompts-per-session", "1",
        "--prompt-deadline-ms", "120000", "--rate-limit",
    )
    return QwenLaunchSpec(command, environment)


class QwenServeClient:
    def __init__(
        self, base_url: str, token: str, *,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = 30.0,
    ):
        if not base_url.startswith("http://127.0.0.1") and not base_url.startswith("http://localhost"):
            raise ValueError("Qwen Serve must be loopback-only")
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owned_client = client is None

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def verify_capabilities(self) -> dict[str, Any]:
        response = await self._client.get(f"{self.base_url}/capabilities", headers=self._headers)
        _require_success(response, "GET /capabilities")
        payload = response.json()
        features = set(payload.get("features") or [])
        missing = REQUIRED_CAPABILITIES - features
        if missing:
            raise QwenHarnessError(f"Qwen Serve lacks required read-only capabilities: {sorted(missing)}")
        return payload

    async def verify_runtime_ready(self) -> dict[str, Any]:
        """Use Qwen's deep probe; bootstrap capabilities alone are premature."""
        response = await self._client.get(f"{self.base_url}/health?deep=1", headers=self._headers)
        _require_success(response, "GET /health?deep=1")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise QwenHarnessError("Qwen Serve deep health probe did not report ready")
        return payload

    async def create_session(self, *, cwd: str, model_service_id: str | None = None) -> str:
        body: dict[str, str] = {"cwd": cwd, "sessionScope": "thread"}
        if model_service_id:
            body["modelServiceId"] = model_service_id
        response = await self._client.post(f"{self.base_url}/session", headers=self._headers, json=body)
        _require_success(response, "POST /session")
        payload = response.json()
        session_id = payload.get("sessionId") or payload.get("id")
        if not session_id:
            raise QwenHarnessError("Qwen Serve returned no session id")
        return str(session_id)

    async def prompt(self, session_id: str, text: str) -> tuple[str, str]:
        """Admit a text-only turn; completion is observed through replayable SSE."""
        response = await self._client.post(
            f"{self.base_url}/session/{session_id}/prompt", headers=self._headers,
            json={"prompt": [{"type": "text", "text": text}]},
        )
        _require_success(response, "POST /session/:id/prompt")
        payload = response.json()
        prompt_id = payload.get("promptId")
        if not prompt_id:
            raise QwenHarnessError("Qwen Serve admitted no prompt id")
        return str(prompt_id), str(payload.get("lastEventId") or "0")

    async def cancel(self, session_id: str) -> None:
        response = await self._client.post(f"{self.base_url}/session/{session_id}/cancel", headers=self._headers)
        if response.status_code not in {204, 404}:
            _require_success(response, "POST /session/:id/cancel")

    async def events(self, session_id: str, *, last_event_id: str = "0") -> AsyncIterator[dict[str, Any]]:
        async with self._client.stream("GET", f"{self.base_url}/session/{session_id}/events", headers={**self._headers, "Last-Event-ID": last_event_id}) as response:
            _require_success(response, "GET /session/:id/events")
            sse_id: str | None = None
            async for line in response.aiter_lines():
                if line.startswith("id:"):
                    sse_id = line[3:].strip()
                if line.startswith("data:"):
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        if sse_id is not None and "_sse_id" not in event:
                            event["_sse_id"] = sse_id
                        yield event


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Keep UI-facing state stable while safely retaining unknown daemon frames."""
    event_type = str(event.get("type") or event.get("event") or "unknown")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    kind = {
        "session_update": "assistant_update", "turn_complete": "completion",
        "turn_error": "failure", "session_died": "worker_death",
        "client_evicted": "reconnect_required", "permission_request": "permission",
    }.get(event_type, "unknown")
    return {"kind": kind, "event_type": event_type, "data": data}
