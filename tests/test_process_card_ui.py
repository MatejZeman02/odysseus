import re
from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_reloaded_process_renders_persisted_commentary():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    timeline = (ROOT / "static/js/processTimeline.js").read_text()
    stylesheet = app_css()
    assert "rawEvent.kind === 'commentary'" in renderer
    assert "appendProcessCommentary(tools, text);" in renderer
    assert "createProcessToolNode" in renderer
    assert "row.className = 'process-card-commentary';" in timeline
    assert ".process-card-commentary" in stylesheet
    assert "text-align: left" in stylesheet


def test_process_header_style_does_not_leak_into_tool_rows():
    stylesheet = app_css()
    assert ".process-card > summary {" in stylesheet
    assert ".process-card summary {" not in stylesheet
    assert ".process-card-timeline.agent-thread" in stylesheet


def test_process_card_reuses_native_agent_timeline_instead_of_duplicate_tool_cards():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    stylesheet = app_css()
    assert "createProcessThread()" in renderer
    assert "createProcessToolNode({" in renderer
    assert "process-card-tool-label" not in renderer
    assert ".process-card-tool" not in stylesheet


def test_older_qwen_transcripts_keep_their_process_card():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "buildProcessCard(metadata.qwen_process || metadata.computer_process)" in renderer


def test_user_messages_bypass_model_thinking_heuristics():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    # Only assistant prose goes through the thinking heuristic. A user bubble
    # that opens with "We need ..." or "Let me ..." is prose, not reasoning, so
    # folding it into a thinking disclosure hides the prompt. Match the branch
    # loosely: its indentation and the assistant helper's name may change.
    branch = re.search(
        r"const renderedText = role === 'assistant'\s*\?\s*(?P<assistant>[^\n]+)\s*:\s*(?P<user>[^\n]+)",
        renderer,
    )
    assert branch, "the role-split render branch is gone"
    assert "mdToHtml(text)" in branch.group("user")
    assert "mdToHtml" not in branch.group("assistant")
    # A doc-edit bubble carries the owner's own instruction, so it takes the
    # same prose path.
    assert "markdownModule.mdToHtml(instrText)" in renderer
    assert "processWithThinking(instrText)" not in renderer
