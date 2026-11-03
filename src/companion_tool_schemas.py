"""OpenAI-compatible function schemas for the Companion-only tools.

A fork addition, kept out of ``src/tool_schemas.py`` so an upstream merge has
one ``extend`` line to reconcile there instead of ninety lines inside a
ninety-seven element literal. ``src/agent_tools/companion_tools.py`` holds the
handlers these describe, and registers them the same way.
"""
from __future__ import annotations

from typing import Any

from src.companion_capabilities import (
    MEMORY_WRITE, PROJECT_SHELL, PROJECT_WRITE, SANDBOX_READ, SYSTEM_OBSERVE,
    WEB_SEARCH, WORKSPACE_READ,
)

# The order the prompt has always shown. The names come from the module that
# owns the grants, so adding a capability there cannot leave this list behind:
# tests/test_companion_agent_tools.py checks the two agree.
REQUESTABLE_CAPABILITIES: tuple[str, ...] = (
    WEB_SEARCH, WORKSPACE_READ, SYSTEM_OBSERVE, SANDBOX_READ,
    PROJECT_SHELL, PROJECT_WRITE, MEMORY_WRITE,
)

COMPANION_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "system_observe",
            "description": "Collect fixed, read-only diagnostic facts about this computer. It is not shell access and cannot change settings, install software, read files, or reach the network. Use only after System inspection is enabled for this chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "categories": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {"type": "string", "enum": ["system", "hardware", "storage", "graphics", "network"]},
                        "description": "Optional diagnostic categories. Omit for the standard system, hardware, storage, and graphics snapshot.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sandbox_read",
            "description": "Run a short read-only command pipeline inside the qualified Podman sandbox against a disposable snapshot of the attached working directory. This is not host shell access. Provide argv arrays only; no shell syntax, paths outside /inputs, redirects, loops, or write commands. Prefer ordinary structured read/list/search tools for simple requests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "description": "One to four argv vectors, connected by a broker-owned pipe.",
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string"},
                        },
                    },
                },
                "required": ["commands"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_shell",
            "description": "Run one bash command in an offline sandbox whose working directory is the project at /workspace. Use it for anything a terminal does: git log, git diff, grep, find, running tests, builds, scripts. The project and .git are visible, there is no network, and any file the command writes is thrown away when it ends. To change project files use write_file, edit_file or apply_patch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command, run with bash -c in /workspace."},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "description": "Seconds before the command is stopped. Default 120."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "Change one item of this home's durable memory brief, the memory the owner sees in Context. Save only what stays true beyond this conversation: a confirmed fact, a decision, a preference, an open question, a plan, or an approach that failed. Quote an existing item exactly to remove or replace it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "remove", "replace"]},
                    "section": {
                        "type": "string",
                        "enum": ["summary", "confirmed_facts", "accepted_decisions", "open_questions",
                                 "current_plans", "failed_approaches", "preferences", "ongoing_goals", "commitments"],
                        "description": "Project homes use summary, confirmed_facts, accepted_decisions, open_questions, current_plans, failed_approaches. Personal homes use summary, confirmed_facts, preferences, ongoing_goals, commitments, open_questions, failed_approaches.",
                    },
                    "text": {"type": "string", "description": "The new item, or the new summary. One short sentence per item."},
                    "old_text": {"type": "string", "description": "For remove and replace: the existing item, quoted exactly."},
                },
                "required": ["action", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_capability",
            "description": "Request one disabled chat capability instead of claiming a tool is unavailable. This shows the user an Enable/Not now card and ends the turn. Use only when that capability is necessary to complete the current request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": list(REQUESTABLE_CAPABILITIES)},
                    "reason": {"type": "string", "description": "One short, user-facing reason it is needed."},
                },
                "required": ["name", "reason"],
            },
        }
    },
]
