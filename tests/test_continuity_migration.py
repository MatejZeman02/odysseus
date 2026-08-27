import sqlite3
import json

import core.database as database


def test_legacy_sessions_gain_additive_continuity_columns(monkeypatch, tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("INSERT INTO sessions (id, name) VALUES ('legacy', 'Old chat')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{path}")
    database._migrate_add_continuity_session_columns()

    conn = sqlite3.connect(path)
    row = conn.execute("SELECT id, name, scope_kind, project_id FROM sessions").fetchone()
    indexes = {item[1] for item in conn.execute("PRAGMA index_list(sessions)")}
    conn.close()
    assert row == ("legacy", "Old chat", "general", None)
    assert "ix_sessions_scope_project" in indexes


def test_legacy_continuity_payloads_are_marked_unclassified_without_content_rewrite(monkeypatch, tmp_path):
    path = tmp_path / "legacy-continuity.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE continuity_artifacts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL)")
    original = {"session_id": "legacy", "source_hash": "hash", "accepted_decisions": ["old text"]}
    conn.execute(
        "INSERT INTO continuity_artifacts (id, kind, payload_json) VALUES ('checkpoint', 'thread_checkpoint_v1', ?)",
        (json.dumps(original),),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{path}")
    database._migrate_continuity_derivation_metadata()

    conn = sqlite3.connect(path)
    payload = json.loads(conn.execute("SELECT payload_json FROM continuity_artifacts WHERE id = 'checkpoint'").fetchone()[0])
    conn.close()
    assert payload["accepted_decisions"] == ["old text"]
    assert payload["derivation_status"] == "legacy_unclassified"
    assert payload["derivation_version"] == 0
    assert payload["derivation_method"] == "legacy_unclassified"


def test_legacy_sessions_gain_safe_project_capability_default(monkeypatch, tmp_path):
    path = tmp_path / "legacy-g2a.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT, scope_kind TEXT, project_id TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions (id, name, owner, scope_kind, project_id) "
        "VALUES ('legacy', 'Old project', 'alice', 'project', 'dust')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{path}")
    database._migrate_add_g15_session_columns()

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT capability_profile, harness_kind, is_scope_primary FROM sessions WHERE id = 'legacy'"
    ).fetchone()
    conn.close()
    assert row == ("project_read", "native", 0)


def test_g2c_pre_release_artifact_column_is_renamed(monkeypatch, tmp_path):
    path = tmp_path / "g2c-pre-release.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE context_grants ("
        "id TEXT PRIMARY KEY, artefact_paths_json TEXT NOT NULL DEFAULT '[]')"
    )
    conn.execute(
        "INSERT INTO context_grants (id, artefact_paths_json) VALUES ('grant', '[\"notes.md\"]')"
    )
    conn.execute(
        "CREATE TABLE working_artifact_revisions ("
        "id TEXT PRIMARY KEY, artefact_id TEXT NOT NULL, revision INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO working_artifact_revisions (id, artefact_id, revision) VALUES ('revision', 'artifact', 1)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{path}")
    database._migrate_artifact_table_spelling()

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(context_grants)")}
    value = conn.execute("SELECT artifact_paths_json FROM context_grants WHERE id = 'grant'").fetchone()[0]
    revision_columns = {row[1] for row in conn.execute("PRAGMA table_info(working_artifact_revisions)")}
    revision_value = conn.execute("SELECT artifact_id FROM working_artifact_revisions WHERE id = 'revision'").fetchone()[0]
    conn.close()
    assert "artifact_paths_json" in columns
    assert "artefact_paths_json" not in columns
    assert value == '["notes.md"]'
    assert "artifact_id" in revision_columns
    assert "artefact_id" not in revision_columns
    assert revision_value == "artifact"
