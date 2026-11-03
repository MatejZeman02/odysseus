"""Startup migrations for the tables the Companion layer adds.

Separate from ``src/companion_tables.py`` on purpose. This module imports
nothing from ``core.database`` at module scope, so ``init_db`` can import it
whatever order the process happens to load modules in.

A new Companion migration goes in the tuples at the bottom, not into the call
list in ``core/database.py``. That list is upstream's, and threading fork
entries through it is what made every merge conflict in that file.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


def _sqlite_path() -> str | None:
    """The SQLite file these migrations may touch, or None for other backends.

    Resolved at call time. Tests point ``core.database.DATABASE_URL`` at a
    temporary file after import, so a value captured at import time would
    migrate the wrong database, or none at all.
    """
    import core.database as database

    url = str(getattr(database, "DATABASE_URL", "") or "")
    if not url.startswith("sqlite:///"):
        return None
    path = url.replace("sqlite:///", "", 1)
    return path if os.path.exists(path) else None


# Preserve the initial G2C pre-release schema if a user exercised it before
# the public terminology was corrected from British ``artefact`` to American
# ``artifact``. These are data-preserving SQLite renames, not a reset.
def _migrate_artifact_table_spelling():
    db_path = _sqlite_path()
    if db_path is None:
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "working_artefacts" in tables and "working_artifacts" not in tables:
            conn.execute("ALTER TABLE working_artefacts RENAME TO working_artifacts")
        if "working_artefact_revisions" in tables and "working_artifact_revisions" not in tables:
            conn.execute("ALTER TABLE working_artefact_revisions RENAME TO working_artifact_revisions")
        if "working_artifact_revisions" in tables:
            revision_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(working_artifact_revisions)")
            }
            if "artefact_id" in revision_columns and "artifact_id" not in revision_columns:
                conn.execute(
                    "ALTER TABLE working_artifact_revisions "
                    "RENAME COLUMN artefact_id TO artifact_id"
                )
        # The first private beta also used the old spelling for the optional
        # artifact-path allowlist on grants. ``create_all`` never alters an
        # existing table, so leave its data intact and rename the column here.
        if "context_grants" in tables:
            grant_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(context_grants)")
            }
            if (
                "artefact_paths_json" in grant_columns
                and "artifact_paths_json" not in grant_columns
            ):
                conn.execute(
                    "ALTER TABLE context_grants "
                    "RENAME COLUMN artefact_paths_json TO artifact_paths_json"
                )
        conn.commit()
    except Exception as e:
        logger.warning("artifact table spelling migration failed: %s", e)
    finally:
        if conn is not None:
            conn.close()


def _migrate_add_working_artifact_document_id():
    """Link existing Personal artifacts to Documents lazily and safely."""
    db_path = _sqlite_path()
    if db_path is None:
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "working_artifacts" not in tables:
            return
        columns = {row[1] for row in conn.execute("PRAGMA table_info(working_artifacts)")}
        if "document_id" not in columns:
            conn.execute("ALTER TABLE working_artifacts ADD COLUMN document_id VARCHAR")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_working_artifacts_document_id "
            "ON working_artifacts (document_id)"
        )
        conn.commit()
    except Exception as exc:
        logger.warning("working artifact document bridge migration failed: %s", exc)
    finally:
        if conn is not None:
            conn.close()


def _migrate_continuity_derivation_metadata():
    """Classify pre-C0 continuity payloads without changing their content.

    Older records do not reliably say whether they came from the automatic
    heuristic or an owner edit. Marking them ``legacy_unclassified`` keeps
    them inspectable while preventing a later reader from treating them as
    owner-approved facts. Raw messages and artifact revisions are untouched.
    """
    db_path = _sqlite_path()
    if db_path is None:
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "continuity_artifacts" not in tables:
            return
        rows = conn.execute(
            "SELECT id, payload_json FROM continuity_artifacts "
            "WHERE kind IN ('thread_checkpoint_v1', 'project_brief_v1', 'personal_brief_v1')"
        ).fetchall()
        for artifact_id, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or "derivation_status" in payload:
                continue
            payload["derivation_status"] = "legacy_unclassified"
            payload["derivation_version"] = 0
            payload["derivation_method"] = "legacy_unclassified"
            conn.execute(
                "UPDATE continuity_artifacts SET payload_json = ? WHERE id = ?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False), artifact_id),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("continuity derivation metadata migration failed: %s", exc)
    finally:
        if conn is not None:
            conn.close()

def _migrate_add_continuity_session_columns():
    """Add non-destructive continuity bindings to existing session tables."""
    db_path = _sqlite_path()
    if db_path is None:
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "scope_kind" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN scope_kind TEXT NOT NULL DEFAULT 'general'")
        if "project_id" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_sessions_scope_project ON sessions(scope_kind, project_id)"
        )
        conn.commit()
    except Exception as e:
        logger.warning("continuity session migration failed: %s", e)
    finally:
        if conn is not None:
            conn.close()


def _migrate_add_g15_session_columns():
    """Add companion routing/home markers without changing legacy sessions."""
    db_path = _sqlite_path()
    if db_path is None:
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "endpoint_id" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN endpoint_id TEXT")
        if "harness_kind" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN harness_kind TEXT NOT NULL DEFAULT 'native'")
        if "capability_profile" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN capability_profile TEXT NOT NULL DEFAULT 'project_read'")
        if "capability_grants" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN capability_grants TEXT NOT NULL DEFAULT '{}'")
        if "is_scope_primary" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN is_scope_primary BOOLEAN NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_owner_scope_primary ON sessions(owner, scope_kind, is_scope_primary)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_owner_project_primary ON sessions(owner, project_id, is_scope_primary)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_sessions_personal_computer_primary ON sessions(owner, scope_kind) WHERE is_scope_primary = 1 AND scope_kind IN ('personal', 'computer')")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_sessions_project_primary ON sessions(owner, project_id) WHERE is_scope_primary = 1 AND scope_kind = 'project'")
        conn.commit()
    except Exception as e:
        logger.warning("G1.5 session migration failed: %s", e)
    finally:
        if conn is not None:
            conn.close()


# Two phases, because the rename has to run before ``create_all``: it cannot
# fix a table that ``create_all`` has already recreated empty beside it.
_BEFORE_CREATE = (_migrate_artifact_table_spelling,)
_AFTER_CREATE = (
    _migrate_add_working_artifact_document_id,
    _migrate_continuity_derivation_metadata,
    _migrate_add_continuity_session_columns,
    _migrate_add_g15_session_columns,
)


def run_companion_migrations(phase: str) -> None:
    """Run the fork's startup migrations for one phase of ``init_db``."""
    steps = {"before_create": _BEFORE_CREATE, "after_create": _AFTER_CREATE}.get(phase)
    if steps is None:
        raise ValueError(f"unknown migration phase: {phase!r}")
    for step in steps:
        step()
