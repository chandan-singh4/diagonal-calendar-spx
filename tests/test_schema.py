"""The migration framework (M3.3, ADR-051).

WHAT THESE ARE FOR. The thing being replaced was ten copies of

    try:  ALTER TABLE ...
    except Exception:  pass   # column already exists

whose comment was a guess. So the tests that matter here are not "does a
column get added" — the old code managed that — but the four things it could
not do: fail loudly, record what it did, refuse to half-apply, and refuse to
run at all against a database from newer code.

The live database is the case to keep in mind throughout: every column already
present, and `schema_version` still saying 1, because nothing recorded the ten
changes that had been made to it.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import schema


def _at_version_one(path: str) -> sqlite3.Connection:
    """A database in the state the live one was in: everything present, and a
    version that does not say so."""
    db.init_db(path)
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_version WHERE version > 1")
    conn.commit()
    return conn


def _columns(conn, table) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


# ─────────────────────────────────────────────────────────────────────────────
# The list itself
# ─────────────────────────────────────────────────────────────────────────────

def test_the_version_is_derived_from_the_migrations_not_written_down():
    """A hand-maintained constant beside a list of migrations is one edit away
    from disagreeing with it, and the disagreement is silent."""
    assert max(m.version for m in schema.MIGRATIONS) == schema.SCHEMA_VERSION
    assert db.SCHEMA_VERSION == schema.SCHEMA_VERSION


@pytest.mark.parametrize("versions, complaint", [
    ([1, 3], "no gaps"),
    ([1, 2, 2], "duplicate"),
    ([2, 1], "ascending"),
])
def test_a_broken_migration_list_fails_at_import_not_at_runtime(
        monkeypatch, versions, complaint):
    """A skipped or repeated number would otherwise surface as two machines
    quietly holding different shapes of the same database."""
    monkeypatch.setattr(schema, "MIGRATIONS", tuple(
        schema.Migration(v, "x", lambda conn: None) for v in versions))

    with pytest.raises(RuntimeError, match=complaint):
        schema._check_the_list_is_sane()


# ─────────────────────────────────────────────────────────────────────────────
# Applying
# ─────────────────────────────────────────────────────────────────────────────

def test_a_fresh_database_ends_at_the_current_version(temp_db):
    conn = sqlite3.connect(temp_db)
    assert schema.current_version(conn) == schema.SCHEMA_VERSION
    assert [r[0] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version")] == \
        list(range(1, schema.SCHEMA_VERSION + 1))


def test_the_live_databases_situation_changes_nothing_but_the_version(temp_db):
    """The one that had to be right before this could be run for real.

    Every column is already there; the only thing missing is the record of it.
    Migrating must stamp the version and touch nothing else — an ALTER that
    fired here would raise against the real file.

    "Touch nothing else" means the SHAPE OF THESE THREE TABLES. From v4 the
    list also creates a new table (mc_eligible_keys), which is additive and
    cannot disturb what is already stored; the assertion below is deliberately
    about existing columns rather than about the database being untouched.

    The expected list is derived from SCHEMA_VERSION rather than written out,
    so adding a migration does not require editing this test to keep passing —
    which would make it a record of what someone last typed instead of a check
    that every step from v1 actually runs."""
    conn = _at_version_one(temp_db)
    before = {t: _columns(conn, t)
              for t in ("option_rows", "atm_iv_by_expiry", "trades")}

    applied = schema.migrate(conn)

    assert applied == list(range(2, schema.SCHEMA_VERSION + 1))
    assert schema.current_version(conn) == schema.SCHEMA_VERSION
    assert {t: _columns(conn, t) for t in before} == before, \
        "nothing about the shape may change"


def test_running_it_again_does_nothing(temp_db):
    conn = sqlite3.connect(temp_db)
    assert schema.migrate(conn) == []


def test_each_migration_is_recorded_with_what_it_did(temp_db):
    """The record is the point. 'What shape is this file in?' was previously
    answerable only by inspecting it."""
    conn = sqlite3.connect(temp_db)
    rows = conn.execute(
        "SELECT version, applied_at, description FROM schema_version "
        "ORDER BY version").fetchall()

    for version, applied_at, description in rows:
        assert applied_at, f"v{version} has no timestamp"
        assert description, f"v{version} has no description"
    assert "settlement" in {r[0]: r[2] for r in rows}[2]


# ─────────────────────────────────────────────────────────────────────────────
# Failing — the half the old pattern could not do at all
# ─────────────────────────────────────────────────────────────────────────────

def test_a_failing_migration_raises_instead_of_passing_quietly(temp_db):
    """The whole indictment of `except Exception: pass`. A full disk, a locked
    database, a misspelled type and 'already there' were all success."""
    conn = _at_version_one(temp_db)

    def explode(_conn):
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        schema._run_one(conn, schema.Migration(2, "boom", explode))


def test_a_failed_migration_leaves_neither_the_change_nor_the_version(temp_db):
    """Half-applied is the worst outcome: the version says one thing and the
    shape says another, and the next run trusts the version."""
    conn = _at_version_one(temp_db)

    def half_then_fail(c):
        c.execute("ALTER TABLE trades ADD COLUMN nearly_there TEXT")
        raise sqlite3.OperationalError("interrupted")

    with pytest.raises(sqlite3.OperationalError):
        schema._run_one(conn, schema.Migration(2, "half", half_then_fail))

    assert "nearly_there" not in _columns(conn, "trades"), "rolled back"
    assert schema.current_version(conn) == 1, "and the version went nowhere"


def test_an_earlier_success_survives_a_later_failure(temp_db):
    """Each migration commits with its own version row, so the database stops
    at the last one that worked rather than losing the lot."""
    conn = _at_version_one(temp_db)

    def boom(_conn):
        raise sqlite3.OperationalError("nope")

    monkey = (schema.MIGRATIONS[0],
              schema.MIGRATIONS[1],
              schema.Migration(3, "explodes", boom))
    with pytest.raises(sqlite3.OperationalError):
        schema.migrate(conn, migrations=monkey)

    assert schema.current_version(conn) == 2, \
        "v2 applied and stayed applied; v3 did not"


def test_old_code_refuses_to_open_a_newer_database(temp_db):
    """Loudly, because the alternative is writing rows in a shape the running
    code does not understand."""
    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO schema_version (version, applied_at, description) "
        "VALUES (?, '2026-09-03 00:00:00', 'from the future')",
        (schema.SCHEMA_VERSION + 1,))
    conn.commit()

    with pytest.raises(RuntimeError, match="newer than"):
        schema.migrate(conn)


# ─────────────────────────────────────────────────────────────────────────────
# The helper that replaced the guess
# ─────────────────────────────────────────────────────────────────────────────

def test_add_column_reports_whether_it_did_anything(temp_db):
    conn = sqlite3.connect(temp_db)
    assert schema.add_column(conn, "trades", "brand_new", "TEXT") is True
    assert schema.add_column(conn, "trades", "brand_new", "TEXT") is False


def test_a_real_error_is_not_mistaken_for_already_there(temp_db):
    """The exact failure the old pattern could not distinguish. A typo in the
    type used to be indistinguishable from a column that already existed."""
    conn = sqlite3.connect(temp_db)

    with pytest.raises(sqlite3.OperationalError):
        schema.add_column(conn, "trades", "bad_one", "NOT A TYPE")

    with pytest.raises(sqlite3.OperationalError):
        schema.add_column(conn, "no_such_table", "x", "TEXT")
