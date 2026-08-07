import sqlite3

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
