"""Disposable, read-only Qwen Serve configuration and protocol adapter.

No installer or process launcher lives here: a caller must separately authorize
use of a verified user-provided binary.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import httpx


REQUIRED_CAPABILITIES = frozenset({"health", "capabilities", "session_create", "session_events", "require_auth"})


class QwenHarnessError(RuntimeError):
    pass


@dataclass(frozen=True)
class DisposableQwenConfig:
    path: Path
    home: Path
    token_env_key: str
    server_token: str


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
        "tools": {"allowed": []},
        "mcpServers": {},
        "memory": {"enabled": False},
        "ui": {"web": False},
    }
    path = root / "settings.json"
    path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    os.chmod(path, 0o600)
    # The server token is distinct from the single-use bridge token.
    return DisposableQwenConfig(path, home, token_env_key, secrets.token_urlsafe(32))


class QwenServeClient:
    def __init__(self, base_url: str, token: str, *, client: httpx.AsyncClient | None = None):
        if not base_url.startswith("http://127.0.0.1") and not base_url.startswith("http://localhost"):
            raise ValueError("Qwen Serve must be loopback-only")
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owned_client = client is None

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def verify_capabilities(self) -> dict[str, Any]:
        response = await self._client.get(f"{self.base_url}/capabilities", headers=self._headers)
        response.raise_for_status()
        payload = response.json()
        features = set(payload.get("features") or [])
        missing = REQUIRED_CAPABILITIES - features
        if missing:
            raise QwenHarnessError(f"Qwen Serve lacks required read-only capabilities: {sorted(missing)}")
        return payload

    async def create_session(self, *, cwd: str, model_service_id: str) -> str:
        response = await self._client.post(f"{self.base_url}/session", headers=self._headers, json={"cwd": cwd, "modelServiceId": model_service_id})
        response.raise_for_status()
        session_id = response.json().get("id")
        if not session_id:
            raise QwenHarnessError("Qwen Serve returned no session id")
        return str(session_id)

    async def prompt(self, session_id: str, text: str) -> tuple[str, str]:
        """Admit a text-only turn; completion is observed through replayable SSE."""
        response = await self._client.post(
            f"{self.base_url}/session/{session_id}/prompt", headers=self._headers,
            json={"prompt": [{"type": "text", "text": text}]},
        )
        response.raise_for_status()
        payload = response.json()
        prompt_id = payload.get("promptId")
        if not prompt_id:
            raise QwenHarnessError("Qwen Serve admitted no prompt id")
        return str(prompt_id), str(payload.get("lastEventId") or "0")

    async def cancel(self, session_id: str) -> None:
        response = await self._client.post(f"{self.base_url}/session/{session_id}/cancel", headers=self._headers)
        if response.status_code not in {204, 404}:
            response.raise_for_status()

    async def events(self, session_id: str, *, last_event_id: str = "0") -> AsyncIterator[dict[str, Any]]:
        async with self._client.stream("GET", f"{self.base_url}/session/{session_id}/events", headers={**self._headers, "Last-Event-ID": last_event_id}) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
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
