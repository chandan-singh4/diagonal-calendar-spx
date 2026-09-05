"""schema.py — versioned, forward-only schema migrations (M3.3, ADR-051).

WHY THIS EXISTS, precisely.

Columns were added by this pattern, ten times over:

    try:
        conn.execute("ALTER TABLE trades ADD COLUMN close_type TEXT")
    except Exception:
        pass  # column already exists

The comment is a guess. `except Exception: pass` cannot tell "the column is
already there" from "the disk is full", "the database is locked", "the type
name is misspelled" or "the table does not exist". All four are silently
successful. This project's recurring failure is data that was never captured
while everything reported healthy (ADR-046, ADR-048, ADR-049, BUG-030); a
schema change that fails silently is the same failure aimed at the container
instead of the contents.

It also left no record. Ten changes had been applied to the live database and
`schema_version` still said 1, so nothing could answer "what shape is this file
actually in?" except by inspecting it.

WHAT THIS IS

  * **Versioned** — every change has a number, a description, and a row in
    `schema_version` recording when it was applied.
  * **Forward-only** — there is no `down()`. A down-migration is a promise to
    undo a change to the one irreplaceable file in this project, and it would
    be written when nobody is looking at it and run when something is already
    going wrong. Reversing a mistake here means restoring the backup taken
    before the change (see docs/DATABASE.md), which is a real answer rather
    than an aspirational one.
  * **Loud** — a migration that fails rolls back and raises. Nothing is
    swallowed, and the database is never left half-changed.
  * **Ordered and gapless** — checked at import, so a duplicated or skipped
    number is a startup error rather than a database that quietly diverges
    between two machines.

ON THE DEFENSIVENESS OF MIGRATIONS 2 AND 3

They check whether their columns already exist, which a clean framework would
not need to do. That is a one-time debt, not the pattern: every database in
existence had those columns added by the old add-if-missing code while still
being stamped version 1, so the two cannot be told apart from the version
number alone. Rather than guess, they converge both. **Migrations from 4 onward
may assume the state their predecessors left** — say so in the description if
one ever cannot.

WHAT IT DELIBERATELY DOES NOT OWN

The conditional unique-index creation in `db.init_db` stays where it is. That
is a data-repair guard, not a schema step: it inspects the table for duplicate
contract slots and declines to create the index if it finds any (BUG-028).
A forward-only migration must either succeed or raise, and "found duplicates,
warned, carried on" is neither.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    """One forward step. `apply` gets an open connection inside a transaction."""
    version: int
    description: str
    apply: Callable[[sqlite3.Connection], None]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers available to migrations
# ─────────────────────────────────────────────────────────────────────────────

def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Ask the database, rather than infer it from an exception.

    This is the whole difference from the pattern being replaced: a missing
    table, a locked database or a misspelled type now raise, because the only
    thing being suppressed is the one condition actually checked for.
    """
    return any(r[1] == column
               for r in conn.execute(f"PRAGMA table_info({table})"))


def add_column(conn: sqlite3.Connection, table: str, column: str,
               decl: str) -> bool:
    """Add a column unless it is already there. Returns True if it was added."""
    if column_exists(conn, table, column):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    logger.info("migration: %s.%s added", table, column)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# The migrations themselves
# ─────────────────────────────────────────────────────────────────────────────

def _v2_settlement_columns(conn: sqlite3.Connection) -> None:
    """ADR-046: the two third-Friday contracts are different options.

    SPX lists a monthly settling at the OPEN and an SPXW weekly settling at the
    CLOSE for the same date, and Schwab returns both under one expiry key. With
    nowhere to record which was which, the second collided with the first and
    was silently dropped — 160 rows a cycle for eight weeks.
    """
    add_column(conn, "option_rows", "settlement", "TEXT")
    add_column(conn, "atm_iv_by_expiry", "settlement", "TEXT")


# The trades columns, in the order they were historically added. The first two
# are v3.1; the eight `entry_*` are the M3 entry-IV snapshot (ADR-044), and
# db.ENTRY_IV_COLUMNS is the single source of truth for their names and types —
# it is imported lazily inside the migration so this module stays importable
# without db.py and the two cannot silently disagree.
_TRADES_V31_COLUMNS = (
    ("transform_commissions", "REAL"),
    ("close_type", "TEXT"),
)


def _v3_trades_columns(conn: sqlite3.Connection) -> None:
    """The ten columns that used to be added by ten try/except/pass blocks.

    The trades table must already exist. `db.init_db` now creates it alongside
    the collector's tables, which it did not before: the journal's schema was
    deliberately kept out of the version number, and that separation is exactly
    what this task removes. One database, one version, one place to look.
    """
    # Imported here, not at module scope: db imports schema, so the reverse
    # at import time is a cycle. ENTRY_IV_COLUMNS stays the single source of
    # truth for these names rather than being copied into a second list that
    # could drift.
    from db import ENTRY_IV_COLUMNS  # noqa: PLC0415

    for column, decl in _TRADES_V31_COLUMNS:
        add_column(conn, "trades", column, decl)
    for column, decl in ENTRY_IV_COLUMNS.items():
        add_column(conn, "trades", column, decl)


def _v4_mc_eligible_keys(conn: sqlite3.Connection) -> None:
    """M4.3: the Mission Control "New" registry, moved out of a browser tab.

    WHAT "NEW" MEANS AND WHY IT NEEDED A TABLE. A pair is new when it is
    eligible now and was not eligible at the previous recorded snapshot. The
    dashboard has always worked that out by holding the previous set in
    `st.session_state` — which is per-browser-tab: closing the tab makes
    everything new again, and a second client has its own opinion. Neither is
    a property of the market, and M4 serves clients that have no session at
    all (Chandan's decision, 2026-09-05).

    Anchored on snapshot_id, not on a clock or a viewer, so the answer is the
    same for every client and survives a restart of anything.

    THE ONLY WRITE IN AN OTHERWISE READ-ONLY MILESTONE, and deliberately a
    small one: an append-only log of which pairs cleared the threshold at
    which snapshot. It derives from data already in the record and can be
    deleted and rebuilt without loss — which is the test a new table in this
    project has to pass.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mc_eligible_keys (
            snapshot_id  INTEGER NOT NULL,
            pair_key     TEXT    NOT NULL,
            gap          REAL,
            recorded_at  TEXT    NOT NULL,
            PRIMARY KEY (snapshot_id, pair_key)
        )
        """
    )
    # The lookup this table exists to serve is "the newest recorded snapshot
    # at or before N", which is a descending scan of distinct snapshot_ids.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mc_eligible_snapshot "
        "ON mc_eligible_keys (snapshot_id DESC)"
    )


MIGRATIONS: tuple[Migration, ...] = (
    # v1 is the initial snapshot-anchored schema. It has no step here because
    # db._DDL creates it in full on a fresh database and every existing
    # database already carries it; it is listed so the numbering starts where
    # the recorded history does.
    Migration(1, "Initial snapshot-anchored schema", lambda conn: None),
    Migration(2, "settlement column on option_rows and atm_iv_by_expiry "
                 "(ADR-046)", _v2_settlement_columns),
    Migration(3, "trades: transform_commissions, close_type, and the eight "
                 "entry-IV columns (ADR-044)", _v3_trades_columns),
    Migration(4, "mc_eligible_keys: the Mission Control \"New\" registry, "
                 "anchored on the snapshot rather than a browser tab (M4.3)",
              _v4_mc_eligible_keys),
)

SCHEMA_VERSION = max(m.version for m in MIGRATIONS)


def _check_the_list_is_sane() -> None:
    """Run at import. A duplicated or skipped number is a bug that would
    otherwise show up as two machines quietly holding different shapes."""
    versions = [m.version for m in MIGRATIONS]
    if versions != sorted(versions):
        raise RuntimeError(f"MIGRATIONS are not in ascending order: {versions}")
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"MIGRATIONS contain a duplicate version: {versions}")
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(
            f"MIGRATIONS must be numbered 1..N with no gaps, got {versions}")


_check_the_list_is_sane()


# ─────────────────────────────────────────────────────────────────────────────
# The runner
# ─────────────────────────────────────────────────────────────────────────────

def current_version(conn: sqlite3.Connection) -> int:
    """The highest version recorded as applied, or 0 on an unstamped database."""
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return (row[0] if row and row[0] is not None else 0)


def _run_one(conn: sqlite3.Connection, migration: Migration) -> None:
    """Apply one migration and record it, together, or neither.

    The version row is written inside the SAME transaction as the change it
    describes. Half-applied is the worst possible outcome here: the version
    would say one thing, the shape another, and the next startup would trust
    the version and carry on.
    """
    logger.info("migration %d: %s", migration.version, migration.description)
    # executescript() and some driver paths leave a transaction open; BEGIN
    # would then raise, and the failure would look like the migration's rather
    # than the caller's.
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN")
    try:
        migration.apply(conn)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (?, ?, ?)",
            (migration.version,
             datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
             migration.description),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception(
            "migration %d FAILED and was rolled back; the database is still "
            "at v%d", migration.version, current_version(conn))
        raise


def migrate(conn: sqlite3.Connection,
            migrations: tuple[Migration, ...] | None = None) -> list[int]:
    """Bring the database up to date. Returns the versions applied.

    Safe to call on every startup — an up-to-date database does no work and
    logs nothing. A failure stops at the migration that failed; everything
    before it stays applied, which is the point of numbering them separately.

    `migrations` exists so the failure paths can be exercised against a real
    database in the tests. Production passes nothing.
    """
    steps = MIGRATIONS if migrations is None else migrations
    target = max(m.version for m in steps)
    at = current_version(conn)

    if at > target:
        raise RuntimeError(
            f"Database schema version {at} is newer than this code's "
            f"{target}. Old code is opening a newer database — update the "
            f"codebase rather than the database."
        )

    applied: list[int] = []
    for migration in steps:
        if migration.version <= at:
            continue
        _run_one(conn, migration)
        applied.append(migration.version)

    if applied:
        logger.info("schema migrated to v%d (applied %s)", target, applied)
    return applied
