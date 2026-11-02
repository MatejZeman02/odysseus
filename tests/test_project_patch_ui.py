from pathlib import Path
import re

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


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


def test_patch_card_is_reconstructed_from_persisted_assistant_metadata():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "metadata?.project_patch" in renderer
    assert "buildProjectPatchCard(metadata.project_patch)" in renderer
    assert ".project-patch-card" in app_css()


def test_failed_process_replays_its_actionable_safe_detail():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    assert "process.failure_detail" in renderer
    assert "failure.textContent = failureDetail" in renderer


def test_patch_build_id_invalidates_existing_service_worker_cache():
    index = (ROOT / "static/index.html").read_text()
    worker = (ROOT / "static/sw.js").read_text()
    index_build = re.search(r"__ODYSSEUS_BUILD_ID='([^']+)'", index)
    worker_build = re.search(r"const BUILD_ID = '([^']+)'", worker)
    assert index_build and worker_build
    assert index_build.group(1) == worker_build.group(1)
