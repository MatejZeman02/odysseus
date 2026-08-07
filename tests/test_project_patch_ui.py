from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_patch_action_is_project_qwen_only_and_uses_version_neutral_api():
    index = (ROOT / "static/index.html").read_text()
    sessions = (ROOT / "static/js/sessions.js").read_text()
    chat = (ROOT / "static/js/chat.js").read_text()

    assert 'id="project-patch-btn"' in index
    assert "patchBtn.hidden = !projectScope" in sessions
    assert "patchBtn.disabled = projectScope && !qwenActive" in sessions
    assert "Enable Qwen Companion to propose project changes" in sessions
    assert "window.__odysseusPatchProposalSessionId === streamSessionId" in chat
    assert "/api/companion/projects/${encodeURIComponent(_g15Session.project_id)}/patch-turn/stream" in chat
    assert "{ session_id: streamSessionId, message: _finalMsgWithInject }" in chat


def test_patch_card_fetches_trusted_diff_and_sends_only_revision_on_mutation():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    card = renderer[renderer.index("export function buildProjectPatchCard"):renderer.index("export function stripToolBlocks")]

    assert "file.diff" in card
    assert "diff.textContent" in card
    assert "body: JSON.stringify({expected_revision: patch.revision})" in card
    assert "Apply patch" in card
    assert "Reject" in card
    assert "Roll back" in card
    assert "Review applied change" in card
    assert "replacement" not in card


def test_automatic_mode_applies_only_the_server_proposal_revision():
    chat = (ROOT / "static/js/chat.js").read_text()
    assert "/api/companion/patches/${encodeURIComponent(proposalData.id)}/apply" in chat
    assert "body: JSON.stringify({expected_revision: proposalData.revision})" in chat
    assert "Project change applied and verified · diff and Undo available" in chat
    assert "Concurrent changes preserved" in chat


def test_patch_card_is_reconstructed_from_persisted_assistant_metadata():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "metadata?.project_patch" in renderer
    assert "buildProjectPatchCard(metadata.project_patch)" in renderer
    assert ".project-patch-card" in (ROOT / "static/style.css").read_text()


def test_failed_process_replays_its_actionable_safe_detail():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "process.failure_detail" in renderer
    assert "failure.textContent = failureDetail" in renderer


def test_patch_build_id_invalidates_existing_service_worker_cache():
    index = (ROOT / "static/index.html").read_text()
    worker = (ROOT / "static/sw.js").read_text()
    assert "20260807g2bpatch4" in index
    assert "20260807g2bpatch4" in worker
