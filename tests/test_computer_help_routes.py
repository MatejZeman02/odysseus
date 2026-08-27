from routes.computer_help_routes import _device_profile_markdown, _observation_message


def test_observation_message_is_a_persistent_process_trace_without_raw_output():
    content, process = _observation_message([{
        "summary": "Fedora · kernel",
        "facts": ["OS: Fedora", "Kernel: 6.x"],
        "process": {"operation": "Inspect: operating system and kernel"},
    }])
    assert "Computer diagnostic snapshot" in content
    assert "OS: Fedora" in content
    assert process["events"] == [{
        "kind": "tool", "tool": "inspect",
        "command": "Inspect: operating system and kernel", "status": "completed",
    }]


def test_device_profile_is_markdown_from_verified_sanitized_facts_only():
    profile = _device_profile_markdown([{
        "category": "graphics",
        "summary": "ignored summary",
        "facts": ["GPU: Example", "Driver: example"],
    }])
    assert profile.startswith("# Device profile")
    assert "## Graphics" in profile
    assert "GPU: Example" in profile
    assert "ignored summary" not in profile
