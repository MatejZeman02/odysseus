"""Loopback-only, provider-neutral OpenAI-compatible model bridge."""

from __future__ import annotations

import secrets
import time
import json
import asyncio
import socket
from dataclasses import dataclass
from typing import Callable, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.endpoint_resolver import resolve_endpoint_by_id


@dataclass(frozen=True)
class BridgeRoute:
    run_id: str
    token: str
    owner: str
    endpoint_id: str
    model: str
    expires_at: float
    requests_remaining: int


class ModelBridge:
    """Creates single-purpose routes; workers receive only the route token."""

    def __init__(self, *, resolver: Callable = resolve_endpoint_by_id, client_factory: Callable = httpx.AsyncClient, max_body_bytes: int = 1_000_000):
        self._resolver = resolver
        self._client_factory = client_factory
        self._routes: dict[str, BridgeRoute] = {}
        self._max_body_bytes = max_body_bytes
        self.app = FastAPI()
        self.app.post("/v1/chat/completions")(self.chat_completions)

    def issue_route(self, *, owner: str, endpoint_id: str, model: str, run_id: str | None = None, ttl_seconds: int = 600, request_budget: int = 1) -> BridgeRoute:
        if not owner or not endpoint_id or not model or ttl_seconds < 1 or request_budget < 1:
            raise ValueError("owner, endpoint_id, model, and positive ttl are required")
        route = BridgeRoute(
            run_id or secrets.token_urlsafe(12),
            secrets.token_urlsafe(32),
            owner,
            endpoint_id,
            model,
            time.monotonic() + ttl_seconds,
            request_budget,
        )
        self._routes[route.token] = route
        return route

    def _authorize(self, authorization: Optional[str]) -> BridgeRoute:
        token = (authorization or "").removeprefix("Bearer ").strip()
        # Don't index untrusted bearer text directly; compare every issued
        # token in constant time and consume the bounded route on success.
        route = next((candidate for candidate_token, candidate in self._routes.items() if secrets.compare_digest(token, candidate_token)), None)
        if not route or route.expires_at < time.monotonic():
            raise HTTPException(401, "invalid or expired bridge route")
        if route.requests_remaining == 1:
            self._routes.pop(route.token, None)
        else:
            self._routes[route.token] = BridgeRoute(route.token, route.run_id, route.owner, route.endpoint_id, route.model, route.expires_at, route.requests_remaining - 1)
        return route

    async def chat_completions(self, request: Request):
        peer = getattr(getattr(request, "client", None), "host", None)
        if peer is not None and peer not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(403, "bridge accepts loopback clients only")
        route = self._authorize(request.headers.get("authorization"))
        raw_body = await request.body()
        if len(raw_body) > self._max_body_bytes:
            raise HTTPException(413, "bridge request is too large")
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "invalid JSON request") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "request body must be an object")
        target = self._resolver(route.endpoint_id, model=route.model, owner=route.owner)
        if not target:
            raise HTTPException(503, "configured endpoint is unavailable")
        url, resolved_model, headers = target
        if resolved_model != route.model:
            raise HTTPException(409, "configured model changed")
        body["model"] = route.model
        # The worker may not choose provider routing or cache identity.  Add
        # llama.cpp/LM Studio slot-affinity hints only after the server has
        # resolved the owner-owned upstream endpoint.  ``run_id`` is the
        # stable Odysseus session id for Companion turns, so separate Qwen
        # workers still reach the same upstream KV-cache slot.
        from src.llm_core import _apply_local_cache_affinity
        body.pop("session_id", None)
        body.pop("cache_prompt", None)
        _apply_local_cache_affinity(body, url, route.run_id)
        # Never forward arbitrary worker headers, credentials, or an alternate URL.
        client = self._client_factory(timeout=90.0)
        try:
            upstream = await client.send(
                client.build_request("POST", url, headers=headers, json=body), stream=True,
            )
        except httpx.HTTPError:
            # Qwen needs a normal OpenAI-compatible error response in order to
            # finish its ACP turn. Letting a transport exception escape this
            # short-lived loopback server closes the connection mid-request and
            # leaves the worker waiting for its full prompt deadline. Never
            # include an upstream URL, credentials, or transport detail here.
            await client.aclose()
            return JSONResponse(
                status_code=502,
                content={"error": {
                    "message": "Model provider request failed",
                    "type": "provider_error",
                    "code": "provider_failed",
                }},
            )
        if body.get("stream"):
            async def stream():
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()
            return StreamingResponse(stream(), status_code=upstream.status_code, media_type=upstream.headers.get("content-type"))
        try:
            payload = await upstream.aread()
            return JSONResponse(content=json.loads(payload), status_code=upstream.status_code)
        finally:
            await upstream.aclose()
            await client.aclose()


class ModelBridgeRuntime:
    """Owns the dedicated loopback listener; never joins the public app router."""

    def __init__(self, bridge: ModelBridge):
        self.bridge = bridge
        self._server = None
        self._task = None
        self._socket = None
        self.base_url: Optional[str] = None

    async def start(self) -> str:
        if self._task:
            raise RuntimeError("ModelBridge runtime is already active")
        import uvicorn

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            self.bridge.app, log_level="warning", lifespan="off",
        ))
        self._socket = sock
        self._server = server
        self._task = asyncio.create_task(server.serve(sockets=[sock]))
        self.base_url = f"http://127.0.0.1:{port}/v1"
        await asyncio.sleep(0)
        return self.base_url

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task
        self._server = self._task = self._socket = None
        self.base_url = None
