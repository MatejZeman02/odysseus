from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_patch_action_is_project_qwen_only_and_uses_version_neutral_api():
    index = (ROOT / "static/index.html").read_text()
    sessions = (ROOT / "static/js/sessions.js").read_text()
    chat = (ROOT / "static/js/chat.js").read_text()

    assert 'id="project-patch-btn"' in index
    assert "patchBtn.hidden = !qwenActive" in sessions
    assert "window.__odysseusPatchProposalActive" in chat
    assert "/api/companion/projects/${encodeURIComponent(_g15Session.project_id)}/patch-turn/stream" in chat
    assert "{ session_id: streamSessionId, message: _finalMsgWithInject }" in chat


def test_patch_card_fetches_trusted_diff_and_sends_only_revision_on_mutation():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    card = renderer[renderer.index("export function buildProjectPatchCard"):renderer.index("export function stripToolBlocks")]

    assert "file.diff" in card
    assert "diff.textContent" in card
    assert "body: JSON.stringify({expected_revision: patch.revision})" in card
    assert "styledConfirm" in card
    assert "Apply patch" in card
    assert "Reject" in card
    assert "Roll back" in card
    assert "Review applied change" in card
    assert "replacement" not in card


def test_patch_card_is_reconstructed_from_persisted_assistant_metadata():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "metadata?.project_patch" in renderer
    assert "buildProjectPatchCard(metadata.project_patch)" in renderer
    assert ".project-patch-card" in (ROOT / "static/style.css").read_text()


def test_patch_build_id_invalidates_existing_service_worker_cache():
    index = (ROOT / "static/index.html").read_text()
    worker = (ROOT / "static/sw.js").read_text()
    assert "20260807g2bpatch1" in index
    assert "20260807g2bpatch1" in worker
