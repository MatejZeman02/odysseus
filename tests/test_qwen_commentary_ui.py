from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_live_qwen_stream_renders_commentary_events():
    source = (ROOT / "static/js/chat.js").read_text()
    timeline = (ROOT / "static/js/processTimeline.js").read_text()
    assert "if (event === 'commentary') _qwenProcess.commentary(data);" in source
    assert "appendProcessCommentary(tools, text);" in source
    assert "row.className = 'qwen-process-commentary';" in timeline


def test_reloaded_process_renders_persisted_commentary():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    stylesheet = (ROOT / "static/style.css").read_text()
    assert "rawEvent.kind === 'commentary'" in renderer
    assert "createProcessToolNode" in renderer
    assert ".qwen-process-commentary" in stylesheet
    assert "text-align: left" in stylesheet


def test_process_header_style_does_not_leak_into_tool_rows():
    stylesheet = (ROOT / "static/style.css").read_text()
    assert ".qwen-process-card > summary {" in stylesheet
    assert ".qwen-process-card summary {" not in stylesheet
    assert ".qwen-process-timeline.agent-thread" in stylesheet


def test_qwen_reuses_native_agent_timeline_instead_of_duplicate_tool_cards():
    source = (ROOT / "static/js/chat.js").read_text()
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    stylesheet = (ROOT / "static/style.css").read_text()
    assert "createProcessThread({ streaming: true })" in source
    assert "createProcessThread()" in renderer
    assert "const node = createProcessToolNode({" in renderer
    assert "qwen-process-tool-label" not in source
    assert "qwen-process-tool-label" not in renderer
    assert ".qwen-process-tool" not in stylesheet


def test_user_messages_bypass_model_thinking_heuristics():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "role === 'assistant'\n\t        ? markdownModule.processWithThinking(text)\n\t        : markdownModule.mdToHtml(text)" in renderer
    assert "markdownModule.mdToHtml(instrText)" in renderer


def test_failed_qwen_turn_is_not_labeled_stopped():
    source = (ROOT / "static/js/chat.js").read_text()
    assert "finish(stopped ? 'stopped' : 'failed')" in source
    assert "status: outcome === 'worked' ? 'done' : outcome" in source
    assert "ok: outcome === 'worked'" in source
