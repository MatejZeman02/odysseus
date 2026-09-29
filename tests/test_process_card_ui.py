from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_reloaded_process_renders_persisted_commentary():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    timeline = (ROOT / "static/js/processTimeline.js").read_text()
    stylesheet = (ROOT / "static/style.css").read_text()
    assert "rawEvent.kind === 'commentary'" in renderer
    assert "appendProcessCommentary(tools, text);" in renderer
    assert "createProcessToolNode" in renderer
    assert "row.className = 'process-card-commentary';" in timeline
    assert ".process-card-commentary" in stylesheet
    assert "text-align: left" in stylesheet


def test_process_header_style_does_not_leak_into_tool_rows():
    stylesheet = (ROOT / "static/style.css").read_text()
    assert ".process-card > summary {" in stylesheet
    assert ".process-card summary {" not in stylesheet
    assert ".process-card-timeline.agent-thread" in stylesheet


def test_process_card_reuses_native_agent_timeline_instead_of_duplicate_tool_cards():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    stylesheet = (ROOT / "static/style.css").read_text()
    assert "createProcessThread()" in renderer
    assert "const node = createProcessToolNode({" in renderer
    assert "process-card-tool-label" not in renderer
    assert ".process-card-tool" not in stylesheet


def test_older_qwen_transcripts_keep_their_process_card():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "buildProcessCard(metadata.qwen_process || metadata.computer_process)" in renderer


def test_user_messages_bypass_model_thinking_heuristics():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "role === 'assistant'\n\t        ? markdownModule.processWithThinking(text)\n\t        : markdownModule.mdToHtml(text)" in renderer
    assert "markdownModule.mdToHtml(instrText)" in renderer
