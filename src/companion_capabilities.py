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
PROJECT_SHELL = "project_shell"
PROJECT_WRITE = "project_write"
MEMORY_WRITE = "memory_write"

ALL_CAPABILITIES = frozenset({
    WEB_SEARCH, WORKSPACE_READ, SYSTEM_OBSERVE, SANDBOX_READ,
    PROJECT_SHELL, PROJECT_WRITE, MEMORY_WRITE,
})

# The drawer groups grants by what they let the agent do. "See" grants only
# read. "Do" grants run or change something, and each one names exactly what.
SEE_CAPABILITIES = (WORKSPACE_READ, WEB_SEARCH, SYSTEM_OBSERVE, SANDBOX_READ)
DO_CAPABILITIES = (PROJECT_SHELL, PROJECT_WRITE, MEMORY_WRITE)

_COMPANION_SCOPES = frozenset({"personal", "project", "computer"})

# Which homes offer which grant. A grant outside its homes is neither shown
# nor honoured, so a stale stored value cannot switch a tool on elsewhere.
_SCOPES = {
    WEB_SEARCH: frozenset({"general", "personal", "project", "computer"}),
    WORKSPACE_READ: frozenset({"project"}),
    SYSTEM_OBSERVE: frozenset({"general", "personal", "project", "computer"}),
    # Project chats get the offline ``project_shell`` instead, which needs no image.
    SANDBOX_READ: frozenset({"general", "personal", "computer"}),
    PROJECT_SHELL: frozenset({"project"}),
    PROJECT_WRITE: frozenset({"project"}),
    MEMORY_WRITE: frozenset({"personal", "project"}),
}

# The native tools each grant switches on inside a Companion home.
GRANT_TOOLS = {
    WORKSPACE_READ: frozenset({"read_file", "grep", "glob", "ls", "get_workspace"}),
    SYSTEM_OBSERVE: frozenset({"system_observe"}),
    SANDBOX_READ: frozenset({"sandbox_read"}),
    PROJECT_SHELL: frozenset({"project_shell"}),
    PROJECT_WRITE: frozenset({"write_file", "edit_file", "apply_patch"}),
    MEMORY_WRITE: frozenset({"update_memory"}),
}
_TOOL_GRANT = {tool: grant for grant, tools in GRANT_TOOLS.items() for tool in tools}
PROJECT_EDIT_TOOLS = GRANT_TOOLS[PROJECT_WRITE]

# Host-authority tools no Companion home gets, whatever its grants say. The
# contained ``project_shell`` replaces them: it runs offline and cannot keep writes.
_COMPANION_HOST_TOOLS = frozenset({"bash", "python", "manage_bg_jobs"})
# Companion-only tools have no meaning, and no containment, in ordinary chats.
_COMPANION_ONLY_TOOLS = frozenset({"project_shell", "update_memory"})

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
    WORKSPACE_READ: "Read project files",
    SYSTEM_OBSERVE: "System inspection",
    SANDBOX_READ: "Sandboxed read-only commands",
    PROJECT_SHELL: "Run commands",
    PROJECT_WRITE: "Edit project files",
    MEMORY_WRITE: "Update memory",
}

# Readiness reasons are deliberately stable and contain no host paths,
# container arguments, credentials, or image references.  Keeping their
# owner-facing wording here lets the API and the capability drawer explain an
# unavailable sandbox without exposing implementation details.
_SANDBOX_UNAVAILABLE_REASONS = {
    "podman_unavailable": "Rootless Podman is unavailable on this computer.",
    "podman_not_rootless": "Podman must run rootlessly and pass containment qualification.",
    "pinned_sandbox_image_required": "A reviewed digest-pinned sandbox image must be configured and qualified.",
    "sandbox_image_not_local": "The reviewed sandbox image must be present locally and qualified.",
    "containment_probe_incomplete": "The sandbox containment check has not passed yet.",
    "sandbox_report_outdated": "The containment check must run again under the current, stricter gates.",
}


def capability_label(name: str) -> str:
    return _LABELS[name]


def sandbox_unavailable_reason(reason: str | None) -> str:
    """Return a safe, actionable reason why contained reads stay disabled."""
    return _SANDBOX_UNAVAILABLE_REASONS.get(
        str(reason or ""), "The qualified Podman sandbox is unavailable.",
    )


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
        # Commands run offline and every write they make is discarded, so a
        # project can offer them from the start like reading files.
        PROJECT_SHELL: scope == "project",
        # Changing the checkout or durable memory is the owner's call.
        PROJECT_WRITE: False,
        MEMORY_WRITE: False,
    }


def offered_in_scope(name: str, scope_kind: str) -> bool:
    """Return whether this kind of chat offers the grant at all."""
    return str(scope_kind or "general") in _SCOPES.get(name, frozenset())


def grant_for_tool(tool: str) -> str | None:
    """Return the owner grant that controls a native tool in Companion homes."""
    return _TOOL_GRANT.get(str(tool or ""))


def scope_dependent_tools() -> frozenset[str]:
    """Tools whose authority differs between a Companion home and ordinary chat."""
    return frozenset(_TOOL_GRANT) | _COMPANION_HOST_TOOLS | _COMPANION_LEGACY_DENIED_TOOLS


def companion_tool_denial(tool: str, scope_kind: str, granted) -> str:
    """Return why the executor must refuse ``tool``, or ``""`` to allow it.

    ``granted`` is a callable that reads one durable grant, so the executor
    only queries what a given tool needs. Readiness (sandbox, workspace) is
    checked by the tool itself at the point of use.
    """
    scope = str(scope_kind or "general")
    name = str(tool or "")
    if scope not in _COMPANION_SCOPES:
        if name in _COMPANION_ONLY_TOOLS:
            return f"Tool '{name}' is only available in Companion homes."
        return ""
    if name in _COMPANION_HOST_TOOLS:
        return (
            f"Tool '{name}' has host authority and is unavailable in Companion homes. "
            "Use the contained `project_shell` tool in a project chat."
        )
    grant = grant_for_tool(name)
    if grant is None:
        return ""
    if not offered_in_scope(grant, scope):
        return f"Tool '{name}' is not available in this kind of chat."
    if not granted(grant):
        return f"Enable {_LABELS[grant]} in Chat capabilities first."
    return ""


def companion_disabled_tools(
    grants: dict[str, bool],
    scope_kind: str,
    *,
    workspace_attached: bool,
    shell_ready: bool,
) -> set[str]:
    """Tool schemas to hide for one Companion turn.

    The model never sees a tool it cannot use, and the executor repeats the
    same decision through ``companion_tool_denial``.
    """
    scope = str(scope_kind or "general")
    if scope not in _COMPANION_SCOPES:
        return set(_COMPANION_ONLY_TOOLS)
    disabled = set(_COMPANION_HOST_TOOLS)
    for grant, tools in GRANT_TOOLS.items():
        usable = bool(grants.get(grant)) and offered_in_scope(grant, scope)
        if grant in (WORKSPACE_READ, PROJECT_SHELL, PROJECT_WRITE) and not workspace_attached:
            usable = False
        if grant == PROJECT_SHELL and not shell_ready:
            usable = False
        if not usable:
            disabled |= tools
    return disabled


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
    # Do not rely on MCP namespacing. A future integration may register one
    # of the same global-memory actions directly; it has no Companion scope
    # contract until a purpose-built adapter exists.
    if normalized in _COMPANION_LEGACY_MEMORY_MCP_ACTIONS or normalized in _COMPANION_LEGACY_MEMORY_MCP_VERBS:
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
    sandbox_reason: str | None = None,
    shell_ready: bool = False,
    shell_reason: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Safe UI/API representation of requested versus effective grants."""
    grants = normalize(raw, scope_kind=scope_kind)
    details: dict[str, dict[str, Any]] = {}
    for name in sorted(ALL_CAPABILITIES):
        available, reason = _availability(
            name,
            scope_kind=scope_kind,
            workspace_attached=workspace_attached,
            sandbox_ready=sandbox_ready,
            sandbox_reason=sandbox_reason,
            shell_ready=shell_ready,
            shell_reason=shell_reason,
        )
        details[name] = {
            "name": name,
            "label": _LABELS[name],
            "group": "do" if name in DO_CAPABILITIES else "see",
            "offered": offered_in_scope(name, scope_kind),
            "requested": grants[name],
            "effective": bool(grants[name] and available),
            "available": available,
            "reason": reason,
        }
    return details


def _availability(
    name: str,
    *,
    scope_kind: str,
    workspace_attached: bool,
    sandbox_ready: bool,
    sandbox_reason: str | None,
    shell_ready: bool,
    shell_reason: str | None,
) -> tuple[bool, str]:
    if not offered_in_scope(name, scope_kind):
        return False, "Not offered in this kind of chat"
    if name in (WORKSPACE_READ, PROJECT_SHELL, PROJECT_WRITE) and not workspace_attached:
        return False, "Attach a registered project or working directory first"
    if name == SANDBOX_READ and not sandbox_ready:
        return False, sandbox_unavailable_reason(sandbox_reason)
    if name == PROJECT_SHELL and not shell_ready:
        from src.project_shell import unavailable_reason

        return False, unavailable_reason(shell_reason)
    return True, ""


def can_change(
    name: str,
    *,
    scope_kind: str,
    workspace_attached: bool,
    sandbox_ready: bool,
    sandbox_reason: str | None = None,
    shell_ready: bool = False,
    shell_reason: str | None = None,
) -> tuple[bool, str]:
    """Validate an owner-requested capability change without trusting the UI."""
    if name not in ALL_CAPABILITIES:
        return False, "Unknown chat capability"
    return _availability(
        name,
        scope_kind=scope_kind,
        workspace_attached=workspace_attached,
        sandbox_ready=sandbox_ready,
        sandbox_reason=sandbox_reason,
        shell_ready=shell_ready,
        shell_reason=shell_reason,
    )
