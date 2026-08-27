import asyncio
from collections import namedtuple
import json
from pathlib import Path


ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])


def test_system_observe_is_fixed_and_sanitized(monkeypatch):
    import src.agent_tools.system_observe_tools as module

    def fake_collect(categories):
        assert categories == ["graphics"]
        return [{
            "summary": "Display adapters",
            "facts": ["00:02.0 VGA compatible controller"],
            "process": {"operation": "Inspect: graphics adapters"},
        }]

    monkeypatch.setattr(module, "collect_observations", fake_collect)

    async def direct_thread(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(module.asyncio, "to_thread", direct_thread)
    result = asyncio.run(module.SystemObserveTool().execute(json.dumps({"categories": ["graphics"]}), {}))
    assert result["exit_code"] == 0
    assert result["read_only"] is True
    assert "Display adapters" in result["output"]


def test_system_observe_rejects_unstructured_or_unknown_categories(monkeypatch):
    import src.agent_tools.system_observe_tools as module

    async def direct_thread(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(module.asyncio, "to_thread", direct_thread)

    malformed = asyncio.run(module.SystemObserveTool().execute("not json", {}))
    assert malformed["exit_code"] == 1
    rejected = asyncio.run(module.SystemObserveTool().execute('{"categories":["secrets"]}', {}))
    assert rejected["exit_code"] == 1
    assert "Unknown diagnostic category" in rejected["error"]


def test_system_observe_is_registered_and_gated_by_chat_capability(monkeypatch):
    from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
    from src.tool_execution import NO_TOOL_SECURITY_CONTEXT, execute_tool_block
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    import src.tool_execution as execution

    assert "system_observe" in TOOL_HANDLERS
    assert "system_observe" in TOOL_TAGS
    assert any(item["function"]["name"] == "system_observe" for item in FUNCTION_TOOL_SCHEMAS)
    assert 'disabled_tools.add("system_observe")' in Path("routes/chat_routes.py").read_text(encoding="utf-8")

    monkeypatch.setattr(execution, "_scope_kind_for_session", lambda _session_id: "personal")
    monkeypatch.setattr(execution, "_session_capability_enabled", lambda _session_id, _name: False)
    _description, blocked = asyncio.run(execute_tool_block(
        ToolBlock("system_observe", "{}"), session_id="personal", security_context=NO_TOOL_SECURITY_CONTEXT,
    ))
    assert blocked["policy"] == "chat_capability"


def test_system_observe_is_selectable_and_has_explicit_effect_classification():
    from src.tool_capabilities import ToolEffect, capabilities_for_tool

    source = Path("src/agent_loop.py").read_text(encoding="utf-8")
    assert '"system": {"system_observe"}' in source
    assert '"system_observe": "- ```system_observe```' in source
    index = Path("src/tool_index.py").read_text(encoding="utf-8")
    assert '"system_observe": "Collect a small fixed' in index
    assert ToolEffect.READ_PRIVATE in capabilities_for_tool("system_observe").effects
