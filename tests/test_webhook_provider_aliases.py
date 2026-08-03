"""Provider aliases exposed by the webhook sync-chat endpoint."""

from pathlib import Path


def test_kimicode_alias_remains_available_for_webhook_chat():
    source = (Path(__file__).resolve().parents[1] / "routes" / "webhook_routes.py").read_text(
        encoding="utf-8"
    )
    assert '"kimicode": "https://api.kimi.com/coding/v1"' in source
