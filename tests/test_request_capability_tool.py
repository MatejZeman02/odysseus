import asyncio
import json

from src.agent_tools.interaction_tools import RequestCapabilityTool
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block


def test_request_capability_is_a_fixed_safe_choice_card():
    _description, result = asyncio.run(RequestCapabilityTool().execute(json.dumps({
        "name": "web_search", "reason": "Current sources are needed.",
    }), None))
    card = result["ask_user"]
    assert card["kind"] == "capability_request"
    assert card["capability"] == "web_search"
    assert card["options"][0]["label"] == "Enable Web search"
    assert result["exit_code"] == 0


def test_request_capability_rejects_model_invented_authority():
    _description, result = asyncio.run(RequestCapabilityTool().execute(json.dumps({
        "name": "host_shell", "reason": "trust me",
    }), None))
    assert result["exit_code"] == 1
    assert "Unknown" in result["error"]


def test_request_capability_maps_a_tool_name_to_its_grant():
    # A real model asked for "write_file" when editing was off. The card must
    # still offer the grant, never the tool itself.
    _description, result = asyncio.run(RequestCapabilityTool().execute(json.dumps({
        "name": "write_file", "reason": "Apply the fix.",
    }), None))
    assert result["exit_code"] == 0
    assert result["ask_user"]["capability"] == "project_write"
    assert result["ask_user"]["options"][0]["label"] == "Enable Edit project files"


def test_an_unknown_capability_lists_the_names_that_exist():
    _description, result = asyncio.run(RequestCapabilityTool().execute(json.dumps({
        "name": "files", "reason": "edit",
    }), None))
    assert result["exit_code"] == 1
    assert "project_write" in result["error"] and "memory_write" in result["error"]


def test_request_capability_has_schema_and_native_function_translation():
    schemas = {item["function"]["name"] for item in FUNCTION_TOOL_SCHEMAS}
    assert "request_capability" in schemas
    block = function_call_to_tool_block("request_capability", {"name": "system_observe", "reason": "diagnose"})
    assert block.tool_type == "request_capability"
    assert json.loads(block.content)["name"] == "system_observe"


def test_capability_card_ui_persists_before_continuing():
    renderer = open("static/js/chatRenderer.js", encoding="utf-8").read()
    assert "aq.kind === 'capability_request'" in renderer
    assert "/chat-capabilities" in renderer
    assert "odysseus:capability-updated" in renderer
