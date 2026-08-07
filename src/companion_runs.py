"""Shared admission state for read-only Companion worker turns."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class CompanionRunRegistry:
    runs: dict[str, asyncio.Task] = field(default_factory=dict)
    admitted: set[str] = field(default_factory=set)
    cancel_requested: set[str] = field(default_factory=set)
