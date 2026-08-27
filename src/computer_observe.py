"""Bounded, server-owned observations for the Computer Help home.

This module deliberately has no generic ``run command`` entry point.  The
browser selects a small diagnostic category and the server maps it to a fixed
collector.  That keeps the useful G2D-1 read path separate from the future
contained task executor.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


MAX_CATEGORIES = 5
MAX_FACTS_PER_CATEGORY = 24
_VALID_CATEGORIES = frozenset({"system", "hardware", "storage", "graphics", "network"})
_SENSITIVE_VALUE = re.compile(r"(?i)(token|password|secret|authorization|api[_-]?key)\s*[:=]\s*\S+")
_HOME_PATH = re.compile(r"/home/[^/\s]+")


class ObservationError(ValueError):
    """A rejected observation request, safe to show to the owner."""


@dataclass(frozen=True)
class Observation:
    category: str
    summary: str
    facts: list[str]
    source: str

    def public_payload(self) -> dict:
        return {
            "category": self.category,
            "summary": self.summary,
            "facts": self.facts[:MAX_FACTS_PER_CATEGORY],
            "process": {"operation": f"Inspect: {self.source}", "status": "completed"},
        }


def _sanitize(value: str) -> str:
    value = _HOME_PATH.sub("/home/[owner]", str(value))
    value = _SENSITIVE_VALUE.sub(lambda match: f"{match.group(1)}=[redacted]", value)
    return " ".join(value.split())[:360]


def _read_os_release() -> dict[str, str]:
    allowed = {"PRETTY_NAME", "ID", "VERSION_ID"}
    result: dict[str, str] = {}
    try:
        for raw in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            if key in allowed:
                result[key] = value.strip().strip('"')
    except OSError:
        pass
    return result


def _run_fixed(args: list[str], *, timeout: float = 3.0) -> list[str]:
    """Run a reviewed diagnostic with no shell and a small output ceiling."""
    if not args or shutil.which(args[0]) is None:
        return []
    try:
        completed = subprocess.run(
            args, check=False, shell=False, text=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [_sanitize(line) for line in completed.stdout.splitlines()[:MAX_FACTS_PER_CATEGORY] if line.strip()]


def _system() -> Observation:
    release = _read_os_release()
    label = release.get("PRETTY_NAME") or platform.system()
    facts = [
        f"OS: {label}",
        f"Kernel: {platform.release()}",
        f"Architecture: {platform.machine() or 'unknown'}",
        f"Logical CPUs: {os.cpu_count() or 'unknown'}",
    ]
    return Observation("system", f"{label} · {platform.release()}", facts, "operating system and kernel")


def _hardware() -> Observation:
    facts = _run_fixed(["lscpu", "--parse=CPU,CORE,SOCKET,NODE"])[:8]
    if not facts:
        facts = [f"Logical CPUs: {os.cpu_count() or 'unknown'}", f"Architecture: {platform.machine() or 'unknown'}"]
    return Observation("hardware", "CPU topology", facts, "CPU topology")


def _storage() -> Observation:
    facts = _run_fixed(["df", "-P", "-x", "tmpfs", "-x", "devtmpfs"])
    return Observation("storage", "Mounted filesystem capacity", facts or ["Storage information is unavailable."], "storage capacity")


def _graphics() -> Observation:
    rows = _run_fixed(["lspci", "-nn"])
    facts = [line for line in rows if any(tag in line.lower() for tag in ("vga", "3d controller", "display controller"))]
    return Observation("graphics", "Display adapters", facts or ["No display adapter was reported."], "graphics adapters")


def _network() -> Observation:
    rows = _run_fixed(["ip", "-o", "link", "show"])
    facts = [line.split(": ", 1)[-1].split("@", 1)[0] for line in rows]
    return Observation("network", "Network interface names only", facts or ["Network interface metadata is unavailable."], "network interface metadata")


_COLLECTORS: dict[str, Callable[[], Observation]] = {
    "system": _system,
    "hardware": _hardware,
    "storage": _storage,
    "graphics": _graphics,
    "network": _network,
}


def collect_observations(categories: list[str] | None = None) -> list[dict]:
    requested = categories or ["system", "hardware", "storage", "graphics"]
    if len(requested) > MAX_CATEGORIES:
        raise ObservationError("At most five diagnostic categories can be requested at once.")
    if len(set(requested)) != len(requested):
        raise ObservationError("Each diagnostic category may be requested only once.")
    unknown = [category for category in requested if category not in _VALID_CATEGORIES]
    if unknown:
        raise ObservationError("Unknown diagnostic category.")
    payloads = []
    for category in requested:
        observation = _COLLECTORS[category]()
        # Collectors should already sanitize command output, but preserve this
        # final boundary when a collector changes or a platform helper returns
        # an unexpected value.
        payload = observation.public_payload()
        payload["summary"] = _sanitize(payload["summary"])
        payload["facts"] = [_sanitize(fact) for fact in payload["facts"]]
        payloads.append(payload)
    return payloads
