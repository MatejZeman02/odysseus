"""Loopback-only, provider-neutral OpenAI-compatible model bridge."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.endpoint_resolver import resolve_endpoint_by_id


@dataclass(frozen=True)
class BridgeRoute:
    token: str
    owner: str
    endpoint_id: str
    model: str
    expires_at: float


class ModelBridge:
    """Creates single-purpose routes; workers receive only the route token."""

    def __init__(self, *, resolver: Callable = resolve_endpoint_by_id, client_factory: Callable = httpx.AsyncClient):
        self._resolver = resolver
        self._client_factory = client_factory
        self._routes: dict[str, BridgeRoute] = {}
        self.app = FastAPI()
        self.app.post("/v1/chat/completions")(self.chat_completions)

    def issue_route(self, *, owner: str, endpoint_id: str, model: str, ttl_seconds: int = 600) -> BridgeRoute:
        if not owner or not endpoint_id or not model or ttl_seconds < 1:
            raise ValueError("owner, endpoint_id, model, and positive ttl are required")
        route = BridgeRoute(secrets.token_urlsafe(32), owner, endpoint_id, model, time.monotonic() + ttl_seconds)
        self._routes[route.token] = route
        return route

    def _authorize(self, authorization: Optional[str]) -> BridgeRoute:
        token = (authorization or "").removeprefix("Bearer ").strip()
        route = self._routes.pop(token, None)  # single use prevents worker reuse after a run
        if not route or route.expires_at < time.monotonic():
            raise HTTPException(401, "invalid or expired bridge route")
        return route

    async def chat_completions(self, request: Request):
        route = self._authorize(request.headers.get("authorization"))
        target = self._resolver(route.endpoint_id, model=route.model, owner=route.owner)
        if not target:
            raise HTTPException(503, "configured endpoint is unavailable")
        url, resolved_model, headers = target
        if resolved_model != route.model:
            raise HTTPException(409, "configured model changed")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "request body must be an object")
        body["model"] = route.model
        # Never forward arbitrary worker headers, credentials, or an alternate URL.
        client = self._client_factory(timeout=90.0)
        upstream = await client.send(client.build_request("POST", url, headers=headers, json=body), stream=True)
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
            return JSONResponse(content=__import__("json").loads(payload), status_code=upstream.status_code)
        finally:
            await upstream.aclose()
            await client.aclose()
