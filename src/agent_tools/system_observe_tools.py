"""Structured, read-only host observations for any owner-enabled chat.

This is deliberately a narrow wrapper around ``computer_observe`` rather than
a shell.  The capability grant is enforced by the shared tool dispatcher; the
model can select only fixed diagnostic categories.
"""
from __future__ import annotations

import asyncio
import json

from src.computer_observe import ObservationError, collect_observations


class SystemObserveTool:
    """Collect a small, server-owned diagnostic snapshot."""

    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            payload = json.loads((content or "").strip() or "{}")
        except (TypeError, ValueError):
            return {"error": "system_observe needs a JSON object.", "exit_code": 1}
        categories = payload.get("categories", []) if isinstance(payload, dict) else []
        if not isinstance(categories, list) or not all(isinstance(item, str) for item in categories):
            return {"error": "categories must be a list of diagnostic category names.", "exit_code": 1}
        try:
            observations = await asyncio.to_thread(collect_observations, categories or None)
        except ObservationError as error:
            return {"error": str(error), "exit_code": 1}

        lines: list[str] = []
        for item in observations:
            lines.append(str(item.get("summary") or "Diagnostic completed"))
            lines.extend(f"- {fact}" for fact in list(item.get("facts") or []))
        return {
            "output": "\n".join(lines),
            "observations": observations,
            "exit_code": 0,
            "read_only": True,
        }
