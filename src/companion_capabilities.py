"""Server-owned capability profiles for Companion and ordinary chats.

The browser may ask to toggle a named capability, but it never supplies a
command, mount, endpoint, or policy.  This keeps the product-level tool UI
consistent without turning localStorage into an authority boundary.
"""
from __future__ import annotations

from typing import Any


WEB_SEARCH = "web_search"
WORKSPACE_READ = "workspace_read"
SYSTEM_OBSERVE = "system_observe"
SANDBOX_READ = "sandbox_read"

ALL_CAPABILITIES = frozenset({WEB_SEARCH, WORKSPACE_READ, SYSTEM_OBSERVE, SANDBOX_READ})

# These legacy tools have no home/project/grant-aware contract.  Companion
# homes must use the continuity, artifact, and explicit-transfer APIs instead
# of being able to read or mutate global native memory/skills or raw chats.
_COMPANION_LEGACY_DENIED_TOOLS = frozenset({
    "manage_memory",
    "manage_skills",
    "search_chats",
})

# Dynamic MCP servers namespace their tools as ``mcp__<server>__<tool>``.  An
# installed memory server can therefore evade the three stable tool names
# above (for example, ``mcp__server_uuid__memory_save``).  These names all
# operate on legacy global memory and have no home/project/grant-aware
# contract, so Companion homes must never expose or dispatch them either.
_COMPANION_LEGACY_MEMORY_MCP_ACTIONS = frozenset({
    "manage_memory",
    "memory_add",
    "memory_delete",
    "memory_edit",
    "memory_get",
    "memory_list",
    "memory_save",
    "memory_search",
    "memory_update",
})

# AgentMemory and other MCP implementations do not have a stable server id or
# a complete, version-independent action inventory.  They do consistently
# namespace their legacy-memory operations with ``memory`` (or use the direct
# verbs below).  Companion homes have no contract for that global store, so a
# newly added MCP action must fail closed until it gets a scoped contract—not
# become callable merely because we did not know its exact leaf name in
# advance.
_COMPANION_LEGACY_MEMORY_MCP_VERBS = frozenset({"remember", "recall", "forget"})

_LABELS = {
    WEB_SEARCH: "Web search",
    WORKSPACE_READ: "Working directory",
    SYSTEM_OBSERVE: "System inspection",
    SANDBOX_READ: "Sandboxed read-only commands",
}


def defaults_for_scope(scope_kind: str) -> dict[str, bool]:
    """Return conservative initial grants for a newly-created session."""
    scope = str(scope_kind or "general")
    return {
        # Public search is a low-authority read and should not make Personal
        # Advisor or Computer Help stop for a permission card.  The owner can
        # still turn it off for an individual chat; the route then enforces
        # that stored choice rather than trusting a browser toggle.
        WEB_SEARCH: scope in {"personal", "project", "computer"},
        WORKSPACE_READ: scope == "project",
        # Computer Help keeps its existing safe diagnostics ready.  Other
        # homes must opt in before their chat can receive host observations.
        SYSTEM_OBSERVE: scope == "computer",
        SANDBOX_READ: False,
    }


def legacy_tools_denied_for_scope(scope_kind: str) -> frozenset[str]:
    """Return tools that would bypass Companion scope and provenance rules."""
    if str(scope_kind or "general") in {"personal", "project", "computer"}:
        return _COMPANION_LEGACY_DENIED_TOOLS
    return frozenset()


def legacy_tool_denied_for_scope(tool_name: str, scope_kind: str) -> bool:
    """Return whether one stable or qualified tool bypasses Companion scope.

    This intentionally applies only to the known legacy global-memory tool
    family. Other MCP integrations keep their ordinary capability policy;
    Companion scope does not imply a blanket MCP ban.
    """
    if str(scope_kind or "general") not in {"personal", "project", "computer"}:
        return False
    normalized = str(tool_name or "").strip().casefold()
    if normalized in _COMPANION_LEGACY_DENIED_TOOLS:
        return True
    if not normalized.startswith("mcp__"):
        return False
    # ``split`` rather than a positional server-id assumption: installed MCP
    # server IDs can themselves be generated identifiers.
    leaf = normalized.rsplit("__", 1)[-1]
    return (
        leaf in _COMPANION_LEGACY_MEMORY_MCP_ACTIONS
        or leaf in _COMPANION_LEGACY_MEMORY_MCP_VERBS
        or "memory" in leaf
    )


def normalize(raw: Any, *, scope_kind: str) -> dict[str, bool]:
    """Merge persisted grants with defaults and discard malformed keys."""
    grants = defaults_for_scope(scope_kind)
    if not isinstance(raw, dict):
        return grants
    for name in ALL_CAPABILITIES:
        if isinstance(raw.get(name), bool):
            grants[name] = raw[name]
    return grants


def capability_payload(
    raw: Any,
    *,
    scope_kind: str,
    workspace_attached: bool,
    sandbox_ready: bool,
) -> dict[str, dict[str, Any]]:
    """Safe UI/API representation of requested versus effective grants."""
    grants = normalize(raw, scope_kind=scope_kind)
    details: dict[str, dict[str, Any]] = {}
    for name in sorted(ALL_CAPABILITIES):
        available = True
        reason = ""
        if name == WORKSPACE_READ and not workspace_attached:
            available, reason = False, "Attach a registered project or working directory first"
        elif name == SANDBOX_READ and not sandbox_ready:
            available, reason = False, "The qualified Podman sandbox is unavailable"
        details[name] = {
            "name": name,
            "label": _LABELS[name],
            "requested": grants[name],
            "effective": bool(grants[name] and available),
            "available": available,
            "reason": reason,
        }
    return details


def can_change(name: str, *, scope_kind: str, workspace_attached: bool, sandbox_ready: bool) -> tuple[bool, str]:
    """Validate an owner-requested capability change without trusting the UI."""
    if name not in ALL_CAPABILITIES:
        return False, "Unknown chat capability"
    if name == WORKSPACE_READ and not workspace_attached:
        return False, "Attach a registered project or working directory first"
    if name == SANDBOX_READ and not sandbox_ready:
        return False, "The qualified Podman sandbox is unavailable"
    return True, ""
