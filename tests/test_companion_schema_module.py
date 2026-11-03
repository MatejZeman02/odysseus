"""The fork's tables live outside core/database.py, and must still work.

Nine models and five migrations used to be threaded through that upstream
file, which made every upstream merge conflict in it. They now live in
``src/companion_tables.py`` and ``src/companion_migrations.py``. Three things
have to keep holding for that to be a move rather than a break:

* the models are still importable where the rest of the code asks for them,
* ``init_db`` still creates every one of their tables on a fresh database,
* and neither module order deadlocks, because ``core.database`` runs
  ``init_db`` while it is still importing.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import src.companion_migrations as companion_migrations
import src.companion_tables as companion_tables

REPO_ROOT = Path(__file__).resolve().parents[1]

COMPANION_TABLES = {
    "projects",
    "continuity_artifacts",
    "working_artifacts",
    "working_artifact_revisions",
    "context_grants",
    "scoped_memory_records",
    "legacy_memory_migration_reviews",
    "computer_task_roots",
    "project_change_sets",
}


def _run_python(source: str, *, database_url: str | None = None) -> str:
    """Run a snippet in a fresh interpreter and return its stdout."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    if database_url:
        env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True, text=True, timeout=180, cwd=str(REPO_ROOT), env=env,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout


def test_every_model_is_importable_from_core_database():
    """``from core.database import Project`` is what 17 modules already write."""
    import core.database as database

    missing = [
        name for name in companion_tables.MODEL_NAMES
        if getattr(database, name, None) is not getattr(companion_tables, name)
    ]
    assert missing == []


def test_the_star_import_shim_does_not_carry_the_companion_models():
    """``src/database.py`` is upstream's star-import shim, and stays that way.

    A module ``__getattr__`` is invisible to ``from core.database import *``,
    so the nine models are reachable from ``core.database`` or from
    ``src.companion_tables`` and not from the shim. Nothing imports them from
    there, and teaching the shim about them would put a fork hunk back into an
    upstream file for an import no one writes. This test is the signpost.
    """
    import src.database as shim

    assert shim.Session.__tablename__ == "sessions"  # upstream names still arrive
    for name in companion_tables.MODEL_NAMES:
        assert not hasattr(shim, name), (
            f"{name} now reaches src.database too. Either the shim gained a fork "
            "hunk, or core.database started defining these eagerly again."
        )


def test_core_database_still_refuses_a_name_nobody_defines():
    """The re-export is a named list, not a catch-all that invents attributes."""
    import core.database as database

    with pytest.raises(AttributeError):
        database.CompanionTableThatDoesNotExist


@pytest.mark.parametrize("first", ["core.database", "src.companion_tables", "src.companion_migrations"])
def test_either_import_order_works(first):
    """core.database calls init_db() while importing, so order is a real risk.

    Importing the table module first used to raise ImportError from inside
    init_db, because that module's own import of Base re-entered a half-built
    core.database.
    """
    source = (
        f"import {first}\n"
        "from core.database import Project\n"
        "import src.companion_tables as t\n"
        "print(Project.__tablename__, len(t.MODEL_NAMES))\n"
    )
    assert _run_python(source).strip() == "projects 9"


def test_init_db_creates_every_companion_table_on_a_fresh_database():
    """The models have to reach Base.metadata before create_all runs."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "fresh.db")
        source = (
            "import core.database as database\n"
            "database.init_db()\n"
            "print('done')\n"
        )
        assert _run_python(source, database_url=f"sqlite:///{db_path}").strip() == "done"

        conn = sqlite3.connect(db_path)
        try:
            present = {
                row[0] for row in
                conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()

    assert sorted(COMPANION_TABLES - present) == []
    assert "sessions" in present  # the upstream schema is still there too


def test_no_companion_migration_is_left_out_of_a_phase():
    """A migration defined and never registered would never run.

    The old flat call list in init_db made that impossible to get wrong by
    construction. A tuple does not, so check it here instead.
    """
    registered = {
        step.__name__ for step in
        companion_migrations._BEFORE_CREATE + companion_migrations._AFTER_CREATE
    }
    defined = {
        name for name in vars(companion_migrations)
        if name.startswith("_migrate_") and callable(getattr(companion_migrations, name))
    }
    assert sorted(defined - registered) == []


def test_an_unknown_migration_phase_is_refused():
    with pytest.raises(ValueError):
        companion_migrations.run_companion_migrations("whenever")


def test_the_migrations_read_the_database_url_at_call_time(monkeypatch, tmp_path):
    """Tests repoint DATABASE_URL after import. A captured copy would miss it."""
    import core.database as database

    monkeypatch.setattr(database, "DATABASE_URL", "postgresql://example/db")
    assert companion_migrations._sqlite_path() is None

    target = tmp_path / "pointed.db"
    sqlite3.connect(target).close()
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{target}")
    assert companion_migrations._sqlite_path() == str(target)
