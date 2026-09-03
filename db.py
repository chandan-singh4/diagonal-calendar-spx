"""
db.py — Database layer for the SPX Diagonal Calendar Dashboard.

This module is the single source of truth for:
  - Schema creation and versioning
  - All write operations  (collector.py ONLY)
  - All read operations   (app.py)

ARCHITECTURE RULE
  collector.py and app.py never issue SQL directly.
  All database interaction goes through functions defined here.

WRITER / READER SPLIT
  collector.py — sole writer; calls create_snapshot, finalize_snapshot,
                 insert_option_rows, insert_atm_iv_records, record_gap
  app.py       — pure reader; calls get_latest_complete_snapshot,
                 get_latest_atm_iv_snapshots, get_option_chain,
                 get_atm_iv_history, get_contract_iv_history,
                 get_spx_intraday_today, get_diagonal_history,
                 get_transform_mark_history, get_prior_session_close
  journal.py   — trades reader/writer; plus get_entry_iv_context, get_ic_marks

  The read path is enforced, not merely conventional: get_conn() opens with
  PRAGMA query_only = ON, so a reader physically cannot take a write lock.

REMOVED 2026-07-25 (M0.11) — all were unreachable; see decisions.md:
  get_term_structure, get_iv_spread_history, get_snapshots,
  get_all_expiry_atm_iv_today (orphaned when the Pair Scanner was removed in
  v3.3), update_snapshot_notes. get_gaps() is deliberately RETAINED despite
  being uncalled — backlog OPS-005 surfaces collection_gaps in the UI.

SCHEMA
  schema_version    — version tracking; enables future migrations
  snapshots         — one row per collection cycle; anchor for all child data
  option_rows       — one row per contract per snapshot; irreplaceable record
  atm_iv_by_expiry  — pre-aggregated ATM IV per expiry; powers analytics queries
  collection_gaps   — audit log of missed collection windows

IV SCALE
  All IV values are stored as decimals (0.18 = 18%).
  Callers are responsible for multiplying by 100 before display or passing
  to iv_engine functions (which expect percentage form).
"""
from __future__ import annotations  # allows X | Y type hints on Python 3.7+

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
from core import contract

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schema Version
# Increment this constant when the schema changes and add a migration
# function. The init_db() version check will detect the mismatch on startup.
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

# ─────────────────────────────────────────────────────────────────────────────
# DDL — Snapshot-Anchored Schema
# ─────────────────────────────────────────────────────────────────────────────

_DDL = """
-- ── Schema version tracker ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL,
    description TEXT
);

-- ── snapshots ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_timestamp    TEXT    NOT NULL,
    status                TEXT    NOT NULL
                              CHECK(status IN ('COMPLETE', 'PARTIAL', 'FAILED')),
    underlying_price      REAL,
    underlying_bid        REAL,
    underlying_ask        REAL,
    vix_value             REAL,
    market_session        TEXT
                              CHECK(market_session IN ('OPEN', 'MIDDAY', 'CLOSE')),
    poll_interval_used    INTEGER,
    strikes_fetched       INTEGER,
    expiries_fetched      INTEGER,
    collection_latency_ms INTEGER,
    error_message         TEXT,
    notes                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
    ON snapshots(snapshot_timestamp);

CREATE INDEX IF NOT EXISTS idx_snapshots_status
    ON snapshots(status);

CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp_status
    ON snapshots(snapshot_timestamp, status);

-- ── option_rows ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS option_rows (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id      INTEGER NOT NULL
                         REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    expiry_date      TEXT    NOT NULL,
    dte              INTEGER NOT NULL,
    strike           REAL    NOT NULL,
    right            TEXT    NOT NULL
                         CHECK(right IN ('C', 'P')),
    -- 'AM' | 'PM' | NULL. NULL means NOT RECORDED, not 'AM' (BUG-023).
    -- Every row written before 2026-08-19 is NULL: the collector could not tell
    -- the two apart, so it kept whichever the broker happened to list first.
    -- That was the a.m. monthly on ordinary days and the p.m. weekly on the
    -- expiry day itself, when the a.m. contract had already settled and dropped
    -- out of the chain. Stamping those rows 'AM' would therefore be wrong on
    -- precisely the day that matters most. They stay blank and honest.
    settlement       TEXT
                         CHECK(settlement IS NULL OR settlement IN ('AM', 'PM')),
    bid              REAL,
    ask              REAL,
    mark             REAL,
    last             REAL,
    iv               REAL,
    delta            REAL,
    gamma            REAL,
    theta            REAL,
    vega             REAL,
    volume           INTEGER,
    open_interest    INTEGER,
    intrinsic_value  REAL,
    time_value       REAL
);

-- Index policy for option_rows (revised 2026-07-25, M0.10).
--
-- Two indexes were dropped because each was a strict LEFT PREFIX of another
-- and therefore served no query the surviving index could not:
--
--   idx_option_rows_snapshot_id (snapshot_id)
--       prefix of uq_option_rows_contract(snapshot_id, expiry_date, strike, right)
--       -- 100 MB
--   idx_option_rows_contract    (expiry_date, strike, right)
--       prefix of idx_option_rows_contract_snap(expiry_date, strike, right, snapshot_id)
--       -- 218 MB
--
-- Verified before dropping by rehearsing on a backup clone: EXPLAIN QUERY PLAN
-- confirmed every hot query still resolves via an index (snapshot lookups fall
-- through to uq_option_rows_contract, whose leading column is snapshot_id), and
-- measured timings were unchanged. Net effect: 1.810 GB -> 1.423 GB, and ~3,000
-- fewer index writes per collection cycle.
--
-- DO NOT re-add either index without measuring first. They are not free: at
-- current volume each costs 100-220 MB and is maintained on every insert.
CREATE INDEX IF NOT EXISTS idx_option_rows_contract_snap
    ON option_rows(expiry_date, strike, right, snapshot_id);

-- uq_option_rows_contract_settle is deliberately NOT created here. A UNIQUE
-- index cannot be built over a table that still holds duplicates, and _DDL runs
-- BEFORE the deduplication migration below. Creating it here crashes init_db on
-- exactly the legacy databases the migration exists to repair. It is created in
-- init_db() instead, after the duplicates are gone — see the BUG-023 block.

-- ── atm_iv_by_expiry ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS atm_iv_by_expiry (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id         INTEGER NOT NULL
                            REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    expiry_date         TEXT    NOT NULL,
    -- 'AM' | 'PM' | NULL. NULL is every row written before BUG-028 was fixed;
    -- core.contract.match_clause attributes those by when they were taken.
    settlement          TEXT,
    dte                 INTEGER NOT NULL,
    atm_strike          REAL    NOT NULL,
    atm_call_iv         REAL,
    atm_put_iv          REAL,
    atm_avg_iv          REAL,
    iv_spread_to_front  REAL,
    iv_ratio_to_front   REAL
);

CREATE INDEX IF NOT EXISTS idx_atm_iv_snapshot_id
    ON atm_iv_by_expiry(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_atm_iv_expiry_snap
    ON atm_iv_by_expiry(expiry_date, snapshot_id);

-- ── collection_gaps ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS collection_gaps (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_start                TEXT    NOT NULL,
    gap_end                  TEXT    NOT NULL,
    gap_minutes              REAL    NOT NULL,
    expected_snapshots_lost  INTEGER,
    reason                   TEXT,
    detected_at              TEXT    NOT NULL,
    notes                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_gaps_start
    ON collection_gaps(gap_start);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Connection Management
# ─────────────────────────────────────────────────────────────────────────────

def _make_conn(db_path: str, *, read_only: bool = False) -> sqlite3.Connection:
    """
    Open a SQLite connection with row_factory = sqlite3.Row and a 15-second
    timeout to ride out transient locks.

    read_only=True  → dashboard reader path. Sets PRAGMA query_only=ON so this
                      connection can NEVER take a write lock or contend with the
                      collector. WAL already lets readers run concurrently with
                      the writer, so no journal-mode PRAGMA is needed (and none
                      that could stall on a lock is issued).
    read_only=False → writer path (collector, journal). Ensures WAL journal mode
                      and enforces foreign keys for ON DELETE CASCADE.
    """
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    if read_only:
        conn.execute("PRAGMA query_only = ON;")
    else:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_conn(db_path: str | None = None):
    """
    Context manager for app.py READ operations. Opens a read-only connection
    (PRAGMA query_only=ON) so the dashboard can never take a write lock, and
    does NOT commit — a SELECT has nothing to commit, and the previous commit
    on every read added pointless overhead on every rerun.
    Accepts optional db_path; defaults to config.DB_PATH.
    """
    conn = _make_conn(db_path or config.DB_PATH, read_only=True)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def managed_conn(db_path: str):
    """
    Context manager for collector.py write operations.
    Requires explicit db_path — no silent default writes.
    """
    conn = _make_conn(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    """Current UTC time as a sortable ISO8601 string: 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────────────────────────────────────
# Schema Initialization and Versioning
# ─────────────────────────────────────────────────────────────────────────────

def init_db(db_path: str | None = None) -> None:
    """
    Initialize database schema. Safe to call on an existing database —
    every DDL statement uses IF NOT EXISTS.

    Raises RuntimeError if the database contains a schema_version newer than
    SCHEMA_VERSION — this means old code is opening a newer database.
    """
    path = db_path or config.DB_PATH
    conn = _make_conn(path)
    try:
        conn.executescript(_DDL)
        conn.commit()

        # ── Foundational integrity migration ─────────────────────────────────
        # option_rows historically had no uniqueness guarantee on
        # (snapshot_id, expiry_date, strike, right), so a re-fetch or overlapping
        # poll could store the same contract twice in one snapshot. Those
        # duplicates fan out across the six-leg mark-history joins and render as
        # a sawtooth. Deduplicate ONCE (keeping the earliest row per contract),
        # then create a UNIQUE index so it can never recur. Guarded on the index
        # so the (potentially expensive) DELETE runs only the first time.
        # The guard must name BOTH indexes (BUG-025). It originally asked only
        # whether uq_option_rows_contract existed — but the BUG-023 migration
        # below DROPS that index once it has been superseded. On the next call
        # the legacy block therefore concluded "never migrated", re-ran this
        # DELETE, and recreated the superseded index, which then rejected every
        # p.m. row on arrival. Worse: this DELETE groups WITHOUT settlement, so
        # a second restart would have deleted the p.m. contract outright,
        # keeping MIN(id) — the a.m. row — and reported it as deduplication.
        # A migration guard must survive its own migration.
        _has_uq = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name IN "
            "('uq_option_rows_contract', 'uq_option_rows_contract_settle')"
        ).fetchone()
        if not _has_uq:
            _dupes = conn.execute(
                "DELETE FROM option_rows WHERE id NOT IN ("
                "  SELECT MIN(id) FROM option_rows "
                "  GROUP BY snapshot_id, expiry_date, strike, right)"
            ).rowcount
            conn.execute(
                "CREATE UNIQUE INDEX uq_option_rows_contract "
                "ON option_rows(snapshot_id, expiry_date, strike, right)"
            )
            conn.commit()
            logger.info(
                "option_rows integrity migration: removed %d duplicate row(s), "
                "UNIQUE(snapshot_id, expiry_date, strike, right) enforced",
                _dupes,
            )

        # ── AM/PM settlement migration (BUG-023) ─────────────────────────────
        # Adding the column is O(1) in SQLite: it rewrites the table's header,
        # not its 14.3M rows. The uniqueness rule is the part that matters —
        # without swapping it the p.m. contract is still rejected on arrival and
        # the column would sit empty forever.
        #
        # COALESCE(settlement, '?') rather than the bare column: SQLite treats
        # every NULL in a UNIQUE index as distinct from every other NULL, so
        # indexing the raw column would stop deduplicating the legacy rows and
        # reopen the six-leg fan-out this index was created to close.
        _cols = {r["name"] for r in conn.execute("PRAGMA table_info(option_rows)")}
        if "settlement" not in _cols:
            conn.execute("ALTER TABLE option_rows ADD COLUMN settlement TEXT")
            conn.commit()
            logger.info("option_rows: added settlement column (BUG-023)")

        _has_settle_uq = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'index' AND name = 'uq_option_rows_contract_settle'"
        ).fetchone()
        if not _has_settle_uq:
            conn.execute(
                "CREATE UNIQUE INDEX uq_option_rows_contract_settle "
                "ON option_rows(snapshot_id, expiry_date, strike, right, "
                "COALESCE(settlement, '?'))"
            )
            conn.execute("DROP INDEX IF EXISTS uq_option_rows_contract")
            conn.commit()
            logger.info(
                "option_rows: uniqueness now spans settlement; the p.m. "
                "contract is no longer discarded (BUG-023)"
            )

        # ── atm_iv_by_expiry settlement migration (BUG-028) ──────────────────
        # The daily summary had one row per DATE, so on the third Friday the two
        # contracts shared a slot and whichever was written won. The column
        # splits them; the unique index stops them ever sharing a slot again.
        #
        # The index is NOT created when duplicates are already present. Building
        # it would fail and take init_db down with it, and the alternative —
        # deleting rows to make it fit — is a data loss nobody asked for. It is
        # logged instead, loudly, for a human to decide.
        _atm_cols = {r["name"]
                     for r in conn.execute("PRAGMA table_info(atm_iv_by_expiry)")}
        if "settlement" not in _atm_cols:
            conn.execute("ALTER TABLE atm_iv_by_expiry ADD COLUMN settlement TEXT")
            conn.commit()
            logger.info("atm_iv_by_expiry: added settlement column (BUG-028)")

        _has_atm_uq = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'index' AND name = 'uq_atm_iv_contract'"
        ).fetchone()
        if not _has_atm_uq:
            _atm_dupes = conn.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM atm_iv_by_expiry "
                "GROUP BY snapshot_id, expiry_date, COALESCE(settlement, '?') "
                "HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            if _atm_dupes:
                logger.warning(
                    "atm_iv_by_expiry holds %d duplicated contract slot(s); "
                    "uq_atm_iv_contract NOT created (BUG-028)", _atm_dupes,
                )
            else:
                conn.execute(
                    "CREATE UNIQUE INDEX uq_atm_iv_contract ON atm_iv_by_expiry("
                    "snapshot_id, expiry_date, COALESCE(settlement, '?'))"
                )
                conn.commit()
                logger.info(
                    "atm_iv_by_expiry: one summary row per contract enforced "
                    "(BUG-028)"
                )

        row = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        current = row["v"] if row and row["v"] is not None else 0

        if current == 0:
            conn.execute(
                "INSERT INTO schema_version (version, applied_at, description) "
                "VALUES (?, ?, ?)",
                (SCHEMA_VERSION, _utcnow(),
                 "Snapshot-anchored schema: snapshots, option_rows, "
                 "atm_iv_by_expiry, collection_gaps")
            )
            conn.commit()
            logger.info("Schema v%d created at %s", SCHEMA_VERSION, path)

        elif current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current} is newer than "
                f"code version {SCHEMA_VERSION}. Update the codebase."
            )

        else:
            logger.info("Schema v%d verified at %s", current, path)

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Write Operations  (collector.py ONLY)
# app.py never calls these. collector.py is the sole writer.
# ─────────────────────────────────────────────────────────────────────────────

def create_snapshot(db_path: str,
                     snapshot_timestamp: str,
                     market_session: str,
                     poll_interval_used: int,
                     underlying_price: float | None = None,
                     underlying_bid: float | None = None,
                     underlying_ask: float | None = None,
                     vix_value: float | None = None) -> int:
    """
    Open a new snapshot record with status='PARTIAL'. Returns snapshot_id.

    Created at cycle START with status='PARTIAL' so a record always exists even
    if the process crashes during option_row insertion. Updated to 'COMPLETE'
    or 'FAILED' only after all child rows are committed.
    """
    with managed_conn(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO snapshots (
                snapshot_timestamp, status,
                underlying_price, underlying_bid, underlying_ask,
                vix_value, market_session, poll_interval_used
            ) VALUES (?, 'PARTIAL', ?, ?, ?, ?, ?, ?)
            """,
            (snapshot_timestamp, underlying_price, underlying_bid,
             underlying_ask, vix_value, market_session, poll_interval_used)
        )
        return cursor.lastrowid


def finalize_snapshot(db_path: str,
                       snapshot_id: int,
                       status: str,
                       strikes_fetched: int,
                       expiries_fetched: int,
                       collection_latency_ms: int,
                       error_message: str | None = None) -> None:
    """Seal a snapshot after all child rows are committed."""
    with managed_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE snapshots SET
                status                = ?,
                strikes_fetched       = ?,
                expiries_fetched      = ?,
                collection_latency_ms = ?,
                error_message         = ?
            WHERE snapshot_id = ?
            """,
            (status, strikes_fetched, expiries_fetched,
             collection_latency_ms, error_message, snapshot_id)
        )


# ─────────────────────────────────────────────────────────────────────────────
# Which contract does a history read mean?  (BUG-023)
# ─────────────────────────────────────────────────────────────────────────────
#
# It used to collapse the third Friday's two contracts to one here, so that a
# caller handing in a date got a single series back. That is what hid the p.m.
# prices from every screen at once, and it is gone. Every history read below
# now takes a DISPLAY KEY — a date plus, for the a.m. contract only, a label —
# and builds its predicate with core.contract.match_clause, which also decides
# which of the two an old unlabelled row belongs to. See core/contract.py.

# The columns of an option row, written once. The OR IGNORE form is what the
# collector uses; the plain form exists only to ask the database why it refused
# a row (_why_the_database_refused). Building both from one list means the
# diagnosis can never be run against a different statement from the write.
_OPTION_COLUMNS = (
    "snapshot_id", "expiry_date", "dte", "strike", "right", "settlement",
    "bid", "ask", "mark", "last",
    "iv", "delta", "gamma", "theta", "vega",
    "volume", "open_interest", "intrinsic_value", "time_value",
)
_OPTION_INSERT_TEMPLATE = (
    "INSERT {conflict}INTO option_rows ({cols}) VALUES ({binds})"
).format(
    conflict="{conflict}",
    cols=", ".join(_OPTION_COLUMNS),
    binds=", ".join(f":{c}" for c in _OPTION_COLUMNS),
)
_OPTION_INSERT = _OPTION_INSERT_TEMPLATE.format(conflict="OR IGNORE ")
_PLAIN_OPTION_INSERT = _OPTION_INSERT_TEMPLATE.format(conflict="")


def insert_option_rows(db_path: str, rows: list[dict]) -> int:
    """
    Bulk-insert option rows for a snapshot in a single transaction.
    Returns the number of rows ACTUALLY STORED, which may be fewer than were
    offered — see the warning below.

    ON THE `OR IGNORE` (ADR-004, revised by ADR-022 — DEBT-008)
      `INSERT OR IGNORE` was chosen to absorb duplicate contracts. It is NOT
      scoped to uniqueness: SQLite applies the conflict clause to every
      constraint on the statement, so a CHECK or NOT NULL violation ALSO skips
      the row instead of raising.

      This function used to return len(rows), computed before the statement
      ran. A row silently discarded by the database was therefore reported to
      the caller as stored. If Schwab ever changed its `right` convention from
      'C' to 'CALL', every row would be dropped, this would return 3,096, and
      the log would record a healthy cycle indefinitely — while the prices, of
      course, would be gone for good.

      So: compare cursor.rowcount against what was offered, and — M3.6, ADR-050
      — say WHICH KIND of loss it was, because the two are not remotely alike.
      A duplicate contract is benign: the row that was kept holds the same
      prices as the row that was dropped, and nothing is missing. A CHECK or
      NOT NULL violation is data that is gone for good. Both used to produce
      the same WARNING, so the one that mattered was indistinguishable from
      the one that did not — and for eight weeks it was, in exactly that way
      (2,181 identical warnings, ADR-046).

      The classification is EXACT rather than inferred: after the statement,
      the unique key of every offered row is looked up in the table. A key that
      is present was stored (by this row or by the duplicate it collided with);
      a key that is ABSENT is a row the database threw away. One of the absent
      rows is then replayed as a plain INSERT inside a SAVEPOINT, so the reason
      in the log is SQLite's own message rather than this module's guess, and
      the savepoint is rolled back so the replay stores nothing.

      It still does not RAISE, and that is deliberate. Aborting the cycle over
      a handful of bad rows would discard the several thousand good ones in the
      same batch — a much larger loss than the one being reported.

      UPDATED 2026-07-26 (BUG-017): the collector used to discard this return
      value and record `strikes_fetched = len(option_rows)` — the offered
      count — so a snapshot that had lost rows still reported full coverage.
      It now stores this value. Callers must keep doing so: the log warning
      below is a signal for a human, while this return value is what the
      recorded history is judged by.
    """
    if not rows:
        return 0

    # A caller that predates the settlement column gets an honest NULL rather
    # than a crash. NULL is the correct value for "this code did not know" —
    # the same thing every pre-2026-08-19 row says (BUG-023).
    rows = [{"settlement": None, **r} for r in rows]

    with managed_conn(db_path) as conn:
        inserted = conn.executemany(_OPTION_INSERT, rows).rowcount
        if inserted < len(rows):
            _report_discards(conn, rows, inserted)

    return inserted


def _unique_key(row: dict) -> tuple:
    """The row's identity under uq_option_rows_contract_settle.

    Must track that index exactly. COALESCE(settlement, '?') is part of it,
    which is what lets the two third-Friday contracts coexist (ADR-046); an
    unlabelled row is its own third possibility, not a match for either.
    """
    settlement = row.get("settlement")
    return (row["snapshot_id"], row["expiry_date"], row["strike"], row["right"],
            "?" if settlement is None else settlement)


def _rows_the_database_kept(conn, snapshot_ids) -> set[tuple]:
    """The unique keys actually in the table, as plain tuples.

    `tuple(...)` is load-bearing, not tidiness. This connection sets
    row_factory = sqlite3.Row, and a Row NEVER compares equal to a tuple, so a
    set of Rows matches nothing: every discard would look like unrecoverable
    loss and the ERROR below would fire on ordinary duplicates. That is the
    crying-wolf failure this whole task exists to remove, so it is pinned by
    test_a_duplicate_is_reported_as_benign_not_as_loss.
    """
    keys = set()
    for sid in snapshot_ids:
        keys.update(tuple(r) for r in conn.execute(
            """select snapshot_id, expiry_date, strike, right,
                      coalesce(settlement, '?')
               from option_rows where snapshot_id = ?""", (sid,)))
    return keys


def _why_the_database_refused(conn, row: dict) -> str:
    """SQLite's own words, not ours.

    Replays one lost row as a plain INSERT inside a savepoint that is always
    rolled back, so asking the question stores nothing and cannot itself lose
    or duplicate data. A guessed reason would be worse than none: the whole
    point of this path is that nobody knew what was being discarded.
    """
    conn.execute("savepoint diagnose_discard")
    try:
        conn.execute(_PLAIN_OPTION_INSERT, row)
    except sqlite3.Error as exc:
        return str(exc)
    else:
        # It inserts cleanly on its own, so the collision was with another row
        # in the same batch — a duplicate the key check could not see because
        # the row that won is indistinguishable from the row that lost.
        return "no error on replay; it collided with another row in the batch"
    finally:
        conn.execute("rollback to diagnose_discard")
        conn.execute("release diagnose_discard")


def _report_discards(conn, rows: list[dict], inserted: int) -> None:
    """Split a shortfall into 'benign' and 'gone', and log them differently.

    Called only when the counts disagree, which after ADR-046 should be never.
    """
    discarded = len(rows) - inserted
    kept = _rows_the_database_kept(conn, {r["snapshot_id"] for r in rows})
    lost = [r for r in rows if _unique_key(r) not in kept]

    if not lost:
        logger.warning(
            "insert_option_rows: %d of %d rows were duplicates and were "
            "dropped (snapshot_id=%s). Nothing is missing — every contract "
            "offered is in the table. Benign (ADR-022, ADR-050).",
            discarded, len(rows), rows[0].get("snapshot_id"),
        )
        return

    logger.error(
        "insert_option_rows: %d of %d rows were REFUSED BY THE DATABASE and "
        "those prices are GONE (snapshot_id=%s). This is not a duplicate — "
        "their contracts are absent from the table. SQLite says: %s. First "
        "one: expiry=%s strike=%s right=%s settlement=%s. See ADR-050.",
        len(lost), len(rows), rows[0].get("snapshot_id"),
        _why_the_database_refused(conn, lost[0]),
        lost[0].get("expiry_date"), lost[0].get("strike"),
        lost[0].get("right"), lost[0].get("settlement"),
    )
    if discarded > len(lost):
        logger.warning(
            "insert_option_rows: the other %d discarded rows were duplicates "
            "and are benign.", discarded - len(lost),
        )


def insert_atm_iv_records(db_path: str, records: list[dict]) -> None:
    """
    Bulk-insert pre-aggregated ATM IV records.
    One record per CONTRACT per snapshot — call after insert_option_rows()
    commits. On the third Friday that is two rows for the one date, the a.m.
    contract and the p.m. one (BUG-028).

    A plain INSERT, not INSERT OR IGNORE: uq_atm_iv_contract exists to catch a
    second row for the same contract, and swallowing that would leave the term
    structure quietly wrong, which is the failure this table already had once.
    """
    if not records:
        return

    sql = """
        INSERT INTO atm_iv_by_expiry (
            snapshot_id, expiry_date, settlement, dte, atm_strike,
            atm_call_iv, atm_put_iv, atm_avg_iv,
            iv_spread_to_front, iv_ratio_to_front
        ) VALUES (
            :snapshot_id, :expiry_date, :settlement, :dte, :atm_strike,
            :atm_call_iv, :atm_put_iv, :atm_avg_iv,
            :iv_spread_to_front, :iv_ratio_to_front
        )
    """
    with managed_conn(db_path) as conn:
        conn.executemany(sql, records)


def record_gap(db_path: str,
                gap_start: str,
                gap_end: str,
                gap_minutes: float,
                expected_snapshots_lost: int,
                reason: str,
                notes: str | None = None) -> None:
    """Write a collection gap record."""
    with managed_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collection_gaps (
                gap_start, gap_end, gap_minutes,
                expected_snapshots_lost, reason, detected_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (gap_start, gap_end, gap_minutes,
             expected_snapshots_lost, reason, _utcnow(), notes)
        )


# ─────────────────────────────────────────────────────────────────────────────
# Read Operations  (app.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_latest_complete_snapshot(db_path: str) -> sqlite3.Row | None:
    """
    Most recent COMPLETE snapshot row.
    Returns None if no complete snapshots exist (collector not yet running).

    Called once per dashboard refresh to get the current SPX price,
    VIX value, snapshot timestamp, and snapshot_id for chain reconstruction.
    """
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM snapshots
            WHERE status = 'COMPLETE'
            ORDER BY snapshot_timestamp DESC
            LIMIT 1
            """
        ).fetchone()


def get_latest_atm_iv_snapshots(db_path: str,
                                  expiry: str,
                                  n: int = 2) -> list:
    """
    Last N ATM IV records for one CONTRACT, most recent first.
    Used for the day-change metric in the dashboard left panel.

    `expiry` is a display key, so on the third Friday the a.m. and p.m.
    contracts give two different answers rather than one shared one (BUG-028).

    IVs are returned in decimal form (0.18 = 18%) — multiply by 100 for display.
    """
    expiry_date, settlement = contract.parse(expiry)
    match = contract.match_clause(expiry_date, settlement, rows="a", snaps="s")
    with get_conn(db_path) as conn:
        return conn.execute(
            f"""
            SELECT a.atm_avg_iv, s.snapshot_timestamp
            FROM atm_iv_by_expiry a
            JOIN snapshots s ON s.snapshot_id = a.snapshot_id
            WHERE a.expiry_date = ?
              AND {match}
              AND s.status      = 'COMPLETE'
            ORDER BY s.snapshot_timestamp DESC
            LIMIT ?
            """,
            (expiry_date, n)
        ).fetchall()


def get_last_snapshot_timestamp(db_path: str) -> str | None:
    """
    UTC timestamp of the most recent snapshot (any status).
    Used by collector.py gap detection on startup.
    """
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(snapshot_timestamp) AS ts FROM snapshots"
        ).fetchone()
        return row["ts"] if row and row["ts"] else None


def get_option_chain(db_path: str, snapshot_id: int) -> list:
    """
    Full option chain for a specific snapshot.
    Used by app.py to reconstruct chain_df on every dashboard refresh.

    Results ordered by expiry_date, strike, right for consistent display.
    IVs are in decimal form — app.py multiplies by 100 at the load boundary.

    RETURNS BOTH THIRD-FRIDAY CONTRACTS, one row each. Telling them apart is the
    `settlement` column, and the load boundary turns the pair into one display
    key per contract (dataaccess/queries.load_chain_df, core/contract.py). This
    read deliberately does NOT collapse them: doing so here is what hid the p.m.
    prices from every screen at once, and a reader that silently drops half the
    contracts is indistinguishable from one that has no data.
    """
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM option_rows
            WHERE snapshot_id = ?
            ORDER BY expiry_date, strike, right, settlement
            """,
            (snapshot_id,)
        ).fetchall()


def get_contract_iv_history(db_path: str, expiry_date: str, strike: float,
                              right: str, days: int = 30,
                              settlement: str | None = None) -> list:
    """
    IV time-series for a specific option contract over the last N days.
    Drives the 'Selected-Strike IV' chart in the dashboard.

    right: 'C' or 'P' (not 'CALL'/'PUT').
    settlement: 'AM' for the third-Friday morning contract, None for the
    ordinary one. None also matches rows recorded before 2026-08-19, which
    carry no settlement at all — see core/contract.py for how those are
    attributed, and why the rule is read off the calendar date rather than
    guessed. Callers hand this in already parsed from the display key.
    IVs are in decimal form — app.py multiplies by 100 at the load boundary.

    Performance: uses idx_option_rows_contract_snap (covering index).
    """
    with get_conn(db_path) as conn:
        return conn.execute(
            f"""
            SELECT
                s.snapshot_timestamp,
                s.underlying_price,
                s.market_session,
                o.iv, o.delta, o.gamma, o.theta, o.vega,
                o.bid, o.ask, o.mark,
                o.volume, o.open_interest
            FROM option_rows o
            JOIN snapshots s ON s.snapshot_id = o.snapshot_id
            WHERE o.expiry_date = ?
              AND o.strike      = ?
              AND o.right       = ?
              AND {contract.match_clause(expiry_date, settlement, rows="o", snaps="s")}
              AND s.status      = 'COMPLETE'
              AND s.snapshot_timestamp >= datetime('now', ?, 'utc')
            ORDER BY s.snapshot_timestamp
            """,
            (expiry_date, strike, right, f"-{days} days")
        ).fetchall()


def get_atm_iv_history(db_path: str, expiry: str,
                        days: int = 30) -> list:
    """
    ATM IV history for one CONTRACT over the last N days.
    Primary query for term structure charts and range stats.

    `expiry` is a display key — the third Friday's two contracts return two
    different series (BUG-028).

    IVs are in decimal form — app.py multiplies by 100 at the load boundary.

    Performance: uses idx_atm_iv_expiry_snap. Scans ~3,150 rows per 30 days
    rather than scanning option_rows directly (~4.8M rows).
    """
    expiry_date, settlement = contract.parse(expiry)
    match = contract.match_clause(expiry_date, settlement, rows="a", snaps="s")
    with get_conn(db_path) as conn:
        return conn.execute(
            f"""
            SELECT
                s.snapshot_timestamp,
                s.underlying_price,
                s.vix_value,
                a.dte, a.atm_strike,
                a.atm_call_iv, a.atm_put_iv, a.atm_avg_iv,
                a.iv_spread_to_front, a.iv_ratio_to_front
            FROM atm_iv_by_expiry a
            JOIN snapshots s ON s.snapshot_id = a.snapshot_id
            WHERE a.expiry_date = ?
              AND {match}
              AND s.status      = 'COMPLETE'
              AND s.snapshot_timestamp >= datetime('now', ?, 'utc')
            ORDER BY s.snapshot_timestamp
            """,
            (expiry_date, f"-{days} days")
        ).fetchall()


def get_entry_iv_context(db_path: str, entry_ts_utc: str,
                         front_expiry: str, back_expiry: str,
                         call_strike: float, put_strike: float) -> dict | None:
    """
    Reconstruct the IV term-structure context at a trade's entry moment from
    stored snapshots — used by the Trade Journal "Regime Analysis" sub-tab so
    the analysis works retroactively without any schema change.

    Steps: (1) find the COMPLETE snapshot nearest in time to ``entry_ts_utc``
    (a 'YYYY-MM-DD HH:MM:SS' UTC string); (2) pull the at-strike IV of the four
    diagonal legs (front/back x call/put) from option_rows, averaging the two
    legs per side; (3) also pull ATM avg IV for both expiries for macro context.

    IVs are returned in DECIMAL form (DB convention); the caller multiplies by
    100 at the load boundary, like the rest of app.py. Returns None if no
    snapshot exists; individual IV fields may be None if a leg wasn't captured.
    """
    def _mean(vals):
        present = [v for v in vals if v is not None]
        return sum(present) / len(present) if present else None

    def _ratio(f, b):
        return (f / b) if (f and b) else None

    def _level(f, b):
        return ((f * b) ** 0.5) if (f and b and f > 0 and b > 0) else None

    cs, ps = float(call_strike), float(put_strike)
    front_date, front_settle = contract.parse(front_expiry)
    back_date, back_settle = contract.parse(back_expiry)
    with get_conn(db_path) as conn:
        snap = conn.execute(
            """
            SELECT snapshot_id, snapshot_timestamp,
                   ABS(strftime('%s', snapshot_timestamp)
                       - strftime('%s', ?)) AS dist
            FROM snapshots
            WHERE status = 'COMPLETE'
            ORDER BY dist ASC
            LIMIT 1
            """,
            (entry_ts_utc,),
        ).fetchone()
        if snap is None:
            return None
        sid = snap["snapshot_id"]

        # Each leg carries its own contract, so the match clause goes INSIDE
        # each branch rather than once around the lot — the front leg may be
        # the a.m. contract while the back leg is an ordinary p.m. one.
        fm = contract.match_clause(front_date, front_settle, rows="o", snaps="s")
        bm = contract.match_clause(back_date, back_settle, rows="o", snaps="s")
        leg_rows = conn.execute(
            f"""
            SELECT o.expiry_date, o.strike, o.right, o.iv
            FROM option_rows o JOIN snapshots s USING (snapshot_id)
            WHERE o.snapshot_id = ?
              AND ( (o.expiry_date = ? AND o.strike = ? AND o.right = 'C' AND {fm})
                 OR (o.expiry_date = ? AND o.strike = ? AND o.right = 'P' AND {fm})
                 OR (o.expiry_date = ? AND o.strike = ? AND o.right = 'C' AND {bm})
                 OR (o.expiry_date = ? AND o.strike = ? AND o.right = 'P' AND {bm}) )
            """,
            (sid, front_date, cs, front_date, ps,
             back_date, cs, back_date, ps),
        ).fetchall()

        # Keyed on the plain date, not the display key: the clause above has
        # already picked the right contract, and a legacy row attributed to the
        # a.m. side still carries no settlement of its own to rebuild a key from.
        legs = {(r["expiry_date"], float(r["strike"]), r["right"]): r["iv"]
                for r in leg_rows}
        front_iv = _mean([legs.get((front_date, cs, "C")),
                          legs.get((front_date, ps, "P"))])
        back_iv = _mean([legs.get((back_date, cs, "C")),
                         legs.get((back_date, ps, "P"))])

        # One read per contract rather than one IN (...) read for both. The two
        # sides need different settlement clauses, and when front and back land
        # on the same date keying the result by date alone would collapse them
        # back into one number — the whole of BUG-028 in miniature.
        afm = contract.match_clause(front_date, front_settle, rows="a", snaps="s")
        abm = contract.match_clause(back_date, back_settle, rows="a", snaps="s")

        def _atm(expiry_date: str, match: str):
            row = conn.execute(
                f"""
                SELECT a.atm_avg_iv
                FROM atm_iv_by_expiry a
                JOIN snapshots s USING (snapshot_id)
                WHERE a.snapshot_id = ? AND a.expiry_date = ? AND {match}
                """,
                (sid, expiry_date),
            ).fetchone()
            return row["atm_avg_iv"] if row else None

        atm_front = _atm(front_date, afm)
        atm_back = _atm(back_date, abm)

    return {
        "snapshot_id": sid,
        "snapshot_timestamp": snap["snapshot_timestamp"],
        "front_iv": front_iv,          # at-strike, decimal form
        "back_iv": back_iv,
        "ratio": _ratio(front_iv, back_iv),
        "level": _level(front_iv, back_iv),
        "atm_front_iv": atm_front,     # ATM macro context, decimal form
        "atm_back_iv": atm_back,
        "atm_ratio": _ratio(atm_front, atm_back),
        "atm_level": _level(atm_front, atm_back),
    }


# Column name → SQLite type, and the get_entry_iv_context() key each one stores.
# Declared once: init_trades_table() migrates from it, insert_trade/update_trade
# write it, and tests assert against it, so the three cannot drift apart.
ENTRY_IV_COLUMNS: dict[str, str] = {
    "entry_iv_snapshot_id": "INTEGER",
    "entry_iv_snapshot_ts": "TEXT",
    "entry_front_iv":       "REAL",
    "entry_back_iv":        "REAL",
    "entry_iv_ratio":       "REAL",
    "entry_iv_level":       "REAL",
    "entry_atm_front_iv":   "REAL",
    "entry_atm_back_iv":    "REAL",
}

_ENTRY_IV_SOURCE_KEY = {
    "entry_iv_snapshot_id": "snapshot_id",
    "entry_iv_snapshot_ts": "snapshot_timestamp",
    "entry_front_iv":       "front_iv",
    "entry_back_iv":        "back_iv",
    "entry_iv_ratio":       "ratio",
    "entry_iv_level":       "level",
    "entry_atm_front_iv":   "atm_front_iv",
    "entry_atm_back_iv":    "atm_back_iv",
}


def snapshot_entry_iv_context(db_path: str, entry_date: str, entry_time: str,
                              initial_legs: str | list) -> dict:
    """
    Compute the entry-IV context for a trade and return it as trades columns.

    THIS IS THE GATE ON RETENTION (ADR-044, ADR-016). get_entry_iv_context()
    answers "what did the term structure look like when I opened this?" by
    reading historical option_rows. Retention deletes those rows 90 days past
    expiry, at which point the question becomes permanently unanswerable — and
    unanswerable *silently*: Regime Analysis would just plot fewer trades each
    month with no error anywhere. So the answer is written onto the trade while
    the rows still exist, and reconstruction becomes the fallback for rows
    logged before these columns existed.

    Derives the four legs the way render_regime_analysis() does — earliest and
    latest expiry, one call strike and one put strike — so the stored value
    matches what reconstruction would have returned at the same moment.

    Always returns every key in ENTRY_IV_COLUMNS. Missing inputs, an unparseable
    entry time, or no nearby snapshot all yield all-None rather than raising:
    a trade must always be recordable, even with the collector down. All-None is
    also what a pre-existing row looks like, so the read path needs one case,
    not two.
    """
    blank = dict.fromkeys(ENTRY_IV_COLUMNS)
    try:
        legs = json.loads(initial_legs) if isinstance(initial_legs, str) else initial_legs
        expiries = sorted({leg["expiry"] for leg in legs})
        front_expiry, back_expiry = expiries[0], expiries[-1]
        call_strike = next(leg["strike"] for leg in legs if leg["type"] == "Call")
        put_strike  = next(leg["strike"] for leg in legs if leg["type"] == "Put")

        # entry_date/entry_time are local (ET); snapshots are UTC.
        entered = datetime.strptime(f"{entry_date} {entry_time}", "%Y-%m-%d %H:%M")
        ts_utc = (entered.replace(tzinfo=ZoneInfo(config.DISPLAY_TIMEZONE))
                         .astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"))

        ctx = get_entry_iv_context(db_path, ts_utc, front_expiry, back_expiry,
                                   call_strike, put_strike)
    except Exception:
        logger.warning("snapshot_entry_iv_context: could not derive context for "
                       "%s %s — storing NULLs", entry_date, entry_time, exc_info=True)
        return blank

    if ctx is None:
        logger.info("snapshot_entry_iv_context: no snapshot near %s %s",
                    entry_date, entry_time)
        return blank
    return {col: ctx.get(key) for col, key in _ENTRY_IV_SOURCE_KEY.items()}


def get_diagonal_history(
    db_path:      str,
    front_expiry: str,
    back_expiry:  str,
    call_strike:  float,
    put_strike:   float,
    days:         int = 90,
) -> list:
    """
    Historical (iv_ratio, mark prices) for the IV Ratio vs. Normalized Debit scatter.

    Returns one row per COMPLETE snapshot where all four diagonal legs have a
    computable mark price (COALESCE(mark, (bid+ask)/2.0)).  Rows where any leg
    mark is NULL are excluded so the caller always gets a clean four-leg set.

    Columns returned:
        snapshot_timestamp, spx, front_iv (decimal), back_iv (decimal),
        iv_ratio, front_dte,
        front_call_mark, back_call_mark, front_put_mark, back_put_mark.

    IVs are in decimal form (as stored in atm_iv_by_expiry) — multiply ×100
    at the caller if percentage display is needed.
    """
    front_date, front_settle = contract.parse(front_expiry)
    back_date, back_settle = contract.parse(back_expiry)
    def _m(alias, front):
        return contract.match_clause(front_date if front else back_date,
                                     front_settle if front else back_settle,
                                     rows=alias, snaps="s")
    with get_conn(db_path) as conn:
        ofc_match = _m("ofc", True)
        ofp_match = _m("ofp", True)
        obc_match = _m("obc", False)
        obp_match = _m("obp", False)
        f_match   = _m("f", True)    # the daily summary names its contract
        b_match   = _m("b", False)   # too now — BUG-028
        return conn.execute(
            f"""
            SELECT
                s.snapshot_timestamp,
                s.underlying_price                              AS spx,
                f.atm_avg_iv                                    AS front_iv,
                b.atm_avg_iv                                    AS back_iv,
                CASE WHEN f.atm_avg_iv > 0
                     THEN b.atm_avg_iv / f.atm_avg_iv
                     ELSE NULL END                              AS iv_ratio,
                f.dte                                           AS front_dte,
                COALESCE(ofc.mark, (ofc.bid + ofc.ask) / 2.0) AS front_call_mark,
                COALESCE(obc.mark, (obc.bid + obc.ask) / 2.0) AS back_call_mark,
                COALESCE(ofp.mark, (ofp.bid + ofp.ask) / 2.0) AS front_put_mark,
                COALESCE(obp.mark, (obp.bid + obp.ask) / 2.0) AS back_put_mark
            FROM snapshots s
            JOIN atm_iv_by_expiry f
                ON f.snapshot_id = s.snapshot_id AND f.expiry_date = ?
               AND {f_match}
            JOIN atm_iv_by_expiry b
                ON b.snapshot_id = s.snapshot_id AND b.expiry_date = ?
               AND {b_match}
            LEFT JOIN option_rows ofc
                ON ofc.snapshot_id = s.snapshot_id
               AND ofc.expiry_date = ? AND ofc.strike = ? AND ofc.right = 'C'
               AND {ofc_match}
            LEFT JOIN option_rows obc
                ON obc.snapshot_id = s.snapshot_id
               AND obc.expiry_date = ? AND obc.strike = ? AND obc.right = 'C'
               AND {obc_match}
            LEFT JOIN option_rows ofp
                ON ofp.snapshot_id = s.snapshot_id
               AND ofp.expiry_date = ? AND ofp.strike = ? AND ofp.right = 'P'
               AND {ofp_match}
            LEFT JOIN option_rows obp
                ON obp.snapshot_id = s.snapshot_id
               AND obp.expiry_date = ? AND obp.strike = ? AND obp.right = 'P'
               AND {obp_match}
            WHERE s.status = 'COMPLETE'
              AND s.snapshot_timestamp >= datetime('now', ?, 'utc')
              AND COALESCE(ofc.mark, (ofc.bid + ofc.ask) / 2.0) IS NOT NULL
              AND COALESCE(obc.mark, (obc.bid + obc.ask) / 2.0) IS NOT NULL
              AND COALESCE(ofp.mark, (ofp.bid + ofp.ask) / 2.0) IS NOT NULL
              AND COALESCE(obp.mark, (obp.bid + obp.ask) / 2.0) IS NOT NULL
            -- Collapse duplicate-contract fan-out to one row per snapshot
            -- (see get_transform_mark_history for the full rationale).
            GROUP BY s.snapshot_id
            ORDER BY s.snapshot_timestamp
            """,
            (
                front_date, back_date,   # f / b, each narrowed by its own clause
                front_date, float(call_strike),
                back_date,  float(call_strike),
                front_date, float(put_strike),
                back_date,  float(put_strike),
                f"-{days} days",
            ),
        ).fetchall()


def get_transform_mark_history(
    db_path:      str,
    front_expiry: str,
    back_expiry:  str,
    call_strike:  float,
    put_strike:   float,
    days:         int = 90,
) -> list:
    """
    Historical mark prices for both the Diagonal Mark and the Transform Order
    Mark at a given strike/expiry pair, one row per COMPLETE snapshot.

    Extends get_diagonal_history() with the two additional front-expiry wing
    legs (call_strike + 5, put_strike - 5) needed to compute the Transform
    Order Mark: (back_call + back_put) - (front_wing_call + front_wing_put).

    Rows where ANY of the six required legs is missing a computable mark
    price (COALESCE(mark, (bid+ask)/2.0)) are excluded.

    Columns returned:
        snapshot_timestamp, spx,
        front_call_mark, back_call_mark, front_put_mark, back_put_mark,
        front_wing_call_mark, front_wing_put_mark.

    Caller computes:
        diagonal_mark  = (back_call_mark + back_put_mark)
                        - (front_call_mark + front_put_mark)
        transform_mark = (back_call_mark + back_put_mark)
                        - (front_wing_call_mark + front_wing_put_mark)
    """
    front_date, front_settle = contract.parse(front_expiry)
    back_date, back_settle = contract.parse(back_expiry)
    def _m(alias, front):
        return contract.match_clause(front_date if front else back_date,
                                     front_settle if front else back_settle,
                                     rows=alias, snaps="s")
    with get_conn(db_path) as conn:
        ofc_match = _m("ofc", True)
        ofp_match = _m("ofp", True)
        owc_match = _m("owc", True)
        owp_match = _m("owp", True)
        obc_match = _m("obc", False)
        obp_match = _m("obp", False)
        return conn.execute(
            f"""
            SELECT
                s.snapshot_timestamp,
                s.underlying_price                            AS spx,
                COALESCE(ofc.mark, (ofc.bid + ofc.ask) / 2.0) AS front_call_mark,
                COALESCE(obc.mark, (obc.bid + obc.ask) / 2.0) AS back_call_mark,
                COALESCE(ofp.mark, (ofp.bid + ofp.ask) / 2.0) AS front_put_mark,
                COALESCE(obp.mark, (obp.bid + obp.ask) / 2.0) AS back_put_mark,
                COALESCE(owc.mark, (owc.bid + owc.ask) / 2.0) AS front_wing_call_mark,
                COALESCE(owp.mark, (owp.bid + owp.ask) / 2.0) AS front_wing_put_mark
            FROM snapshots s
            LEFT JOIN option_rows ofc
                ON ofc.snapshot_id = s.snapshot_id
               AND ofc.expiry_date = ? AND ofc.strike = ? AND ofc.right = 'C'
               AND {ofc_match}
            LEFT JOIN option_rows obc
                ON obc.snapshot_id = s.snapshot_id
               AND obc.expiry_date = ? AND obc.strike = ? AND obc.right = 'C'
               AND {obc_match}
            LEFT JOIN option_rows ofp
                ON ofp.snapshot_id = s.snapshot_id
               AND ofp.expiry_date = ? AND ofp.strike = ? AND ofp.right = 'P'
               AND {ofp_match}
            LEFT JOIN option_rows obp
                ON obp.snapshot_id = s.snapshot_id
               AND obp.expiry_date = ? AND obp.strike = ? AND obp.right = 'P'
               AND {obp_match}
            LEFT JOIN option_rows owc
                ON owc.snapshot_id = s.snapshot_id
               AND owc.expiry_date = ? AND owc.strike = ? AND owc.right = 'C'
               AND {owc_match}
            LEFT JOIN option_rows owp
                ON owp.snapshot_id = s.snapshot_id
               AND owp.expiry_date = ? AND owp.strike = ? AND owp.right = 'P'
               AND {owp_match}
            WHERE s.status = 'COMPLETE'
              AND s.snapshot_timestamp >= datetime('now', ?, 'utc')
              AND COALESCE(ofc.mark, (ofc.bid + ofc.ask) / 2.0) IS NOT NULL
              AND COALESCE(obc.mark, (obc.bid + obc.ask) / 2.0) IS NOT NULL
              AND COALESCE(ofp.mark, (ofp.bid + ofp.ask) / 2.0) IS NOT NULL
              AND COALESCE(obp.mark, (obp.bid + obp.ask) / 2.0) IS NOT NULL
              AND COALESCE(owc.mark, (owc.bid + owc.ask) / 2.0) IS NOT NULL
              AND COALESCE(owp.mark, (owp.bid + owp.ask) / 2.0) IS NOT NULL
            -- One row per snapshot. option_rows has no uniqueness guarantee on
            -- (snapshot_id, expiry, strike, right), so duplicate contract rows
            -- fan out across the six LEFT JOINs and get plotted as a sawtooth.
            -- Duplicates within a snapshot are identical (same poll), so grouping
            -- by snapshot_id collapses the fan-out to the correct single value.
            GROUP BY s.snapshot_id
            ORDER BY s.snapshot_timestamp
            """,
            (
                front_date, float(call_strike),
                back_date,  float(call_strike),
                front_date, float(put_strike),
                back_date,  float(put_strike),
                front_date, float(call_strike) + 5,
                front_date, float(put_strike)  - 5,
                f"-{days} days",
            ),
        ).fetchall()


def get_gaps(db_path: str, start: str, end: str,
              exclude_reasons: list[str] | None = None) -> list:
    """Collection gaps within a date range."""
    with get_conn(db_path) as conn:
        if exclude_reasons:
            placeholders = ",".join("?" * len(exclude_reasons))
            return conn.execute(
                f"""
                SELECT * FROM collection_gaps
                WHERE gap_start BETWEEN ? AND ?
                  AND (reason IS NULL OR reason NOT IN ({placeholders}))
                ORDER BY gap_start
                """,
                (start, end, *exclude_reasons)
            ).fetchall()

        return conn.execute(
            """
            SELECT * FROM collection_gaps
            WHERE gap_start BETWEEN ? AND ?
            ORDER BY gap_start
            """,
            (start, end)
        ).fetchall()


def get_prior_session_close(db_path: str, session_date: str) -> float | None:
    """
    Underlying price of the last COMPLETE snapshot BEFORE session_date.

    Used by app.py to compute the daily SPX change:
        change = current_spx_price - get_prior_session_close(...)

    session_date: 'YYYY-MM-DD' UTC date string of the current session.
    The query returns the last snapshot from the PREVIOUS trading session
    (typically 3:58–3:59 PM ET the day before), which is the closest
    available approximation of the prior day's official close.

    Returns None if no prior-session data exists (first ever collection day).
    """
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT underlying_price FROM snapshots
            WHERE status              = 'COMPLETE'
              AND snapshot_timestamp  < ?
            ORDER BY snapshot_timestamp DESC
            LIMIT 1
            """,
            (session_date,)
        ).fetchone()
        # `is not None`, not truthiness (BUG-014, fixed 2026-07-26): a stored
        # 0.0 is a real price, not a missing one. Immaterial for SPX, but the
        # same pattern is a live trap wherever 0.0 is a legitimate value.
        if row is None or row["underlying_price"] is None:
            return None
        return float(row["underlying_price"])



def get_spx_intraday_today(db_path: str, session_date: str | None = None) -> list:
    """
    SPX price at every COMPLETE snapshot on the given session date.

    Used by app.py for:
      - The SPX intraday price chart (Section 3)
      - Daily change calculation: current price vs first snapshot of the session

    session_date: 'YYYY-MM-DD' string in UTC (e.g. '2026-06-25').
    App derives this from the latest snapshot's own timestamp so the scanner
    always shows data from the most recent session, not just the current UTC
    calendar day (which would return 0 rows when called after-hours or pre-open).

    Returns rows with: snapshot_timestamp (TEXT), underlying_price (REAL).

    BOUNDED AT BOTH ENDS (BUG-015, fixed 2026-07-26). This query used to have
    only a `>=` lower bound, so asking for an older session returned that
    session AND every session after it, concatenated into what the caller plots
    as a single intraday line. app.py was safe only by accident — it always
    passes the date derived from the LATEST snapshot, which made the open range
    coincidentally correct. Any caller asking for a historical session (a
    backtest replay, a per-day chart) got silently wrong data with no error.
    """
    bound = session_date if session_date else datetime.now(UTC).strftime("%Y-%m-%d")
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT snapshot_timestamp, underlying_price
            FROM snapshots
            WHERE status              = 'COMPLETE'
              AND snapshot_timestamp >= ?
              AND snapshot_timestamp <  date(?, '+1 day')
            ORDER BY snapshot_timestamp
            """,
            (bound, bound)
        ).fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# Trades Table DDL
# ─────────────────────────────────────────────────────────────────────────────

_TRADES_DDL = """
-- ── trades ────────────────────────────────────────────────────────────────────
-- One row per Diagonal Calendar → Iron Condor trade.
-- All monetary values stored per-share unless suffixed _contract.
-- Leg data stored as JSON arrays so the schema stays flat.
CREATE TABLE IF NOT EXISTS trades (
    trade_id               TEXT    PRIMARY KEY,
    entry_date             TEXT    NOT NULL,      -- YYYY-MM-DD
    entry_time             TEXT    NOT NULL,      -- HH:MM ET
    day_of_week            TEXT,
    spx_at_entry           REAL,
    status                 TEXT    NOT NULL DEFAULT 'Open'
                               CHECK(status IN ('Open','Transformed','Expired','Closed')),
    contracts              INTEGER NOT NULL DEFAULT 1,
    commissions            REAL,                  -- total $ across all legs
    initial_legs           TEXT    NOT NULL,      -- JSON: [{expiry,type,action,strike,fill}]
    total_debit            REAL    NOT NULL,      -- per share
    -- Transformation (null until transformed)
    transform_date         TEXT,
    transform_time         TEXT,
    transform_minutes      INTEGER,
    spx_at_transform       REAL,
    transform_legs         TEXT,                  -- JSON
    credit_received        REAL,                  -- per share
    profit_locked_in       REAL,                  -- per share = credit - debit
    -- Iron Condor structure (null until transformed)
    ic_expiry_date         TEXT,                  -- YYYY-MM-DD (front expiry)
    ic_short_call          REAL,
    ic_long_call           REAL,
    ic_short_put           REAL,
    ic_long_put            REAL,
    ic_call_wing           REAL,                  -- points
    ic_put_wing            REAL,                  -- points
    ic_max_profit          REAL,                  -- per contract $
    ic_worst_case          REAL,                  -- per contract $; positive = guaranteed profit
    ic_risk_free           INTEGER DEFAULT 0,     -- 1 if ic_max_profit > max_ic_loss
    -- Expiration results (null until expired)
    result_date            TEXT,
    spx_at_expiry          REAL,
    final_pl               REAL,                  -- per contract $
    expired_inside_wings   INTEGER,               -- 1 if ic_long_put < SPX < ic_long_call
    expired_between_shorts INTEGER,               -- 1 if ic_short_put <= SPX <= ic_short_call
    outcome                TEXT,
    -- Entry IV context, snapshotted at logging time (ADR-044 / ADR-016).
    -- These duplicate what get_entry_iv_context() can reconstruct from
    -- option_rows TODAY, and are the only copy once retention prunes those
    -- rows. IVs in DECIMAL form, matching the rest of the schema.
    entry_iv_snapshot_id   INTEGER,               -- snapshot the context came from
    entry_iv_snapshot_ts   TEXT,                  -- its timestamp, UTC
    entry_front_iv         REAL,                  -- at-strike, front expiry
    entry_back_iv          REAL,                  -- at-strike, back expiry
    entry_iv_ratio         REAL,                  -- front / back
    entry_iv_level         REAL,                  -- sqrt(front * back)
    entry_atm_front_iv     REAL,                  -- ATM macro context
    entry_atm_back_iv      REAL,
    -- Metadata
    notes                  TEXT,
    created_at             TEXT    NOT NULL,
    updated_at             TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_status     ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_entry_date ON trades(entry_date);
"""


# ─────────────────────────────────────────────────────────────────────────────
# Schema Init (called from journal.py, NOT from init_db)
# ─────────────────────────────────────────────────────────────────────────────

def init_trades_table(db_path: str) -> None:
    """
    Create the trades table and indexes if they don't exist.
    Safe to call on every journal.py startup — all DDL uses IF NOT EXISTS.
    Intentionally separate from init_db() so the main dashboard schema path
    and version number are unaffected.
    """
    with managed_conn(db_path) as conn:
        conn.executescript(_TRADES_DDL)

        # v3.1 column migrations — safe to run on existing databases
        try:
            conn.execute("ALTER TABLE trades ADD COLUMN transform_commissions REAL")
        except Exception:
            pass  # column already exists

        try:
            conn.execute("ALTER TABLE trades ADD COLUMN close_type TEXT")
        except Exception:
            pass  # column already exists

        # M3 entry-IV snapshot columns (ADR-044). Same add-if-missing pattern as
        # above; existing rows keep NULL and fall back to reconstruction.
        for _col, _type in ENTRY_IV_COLUMNS.items():
            try:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {_col} {_type}")
            except Exception:
                pass  # column already exists

    logger.info("Trades table verified at %s", db_path)


# ─────────────────────────────────────────────────────────────────────────────
# Write Operations (journal.py only)
# ─────────────────────────────────────────────────────────────────────────────

def get_next_trade_id(db_path: str) -> str:
    """
    Return the next sequential trade ID string, e.g. 'T-004'.

    Derived from the HIGHEST existing ID, not from COUNT(*) (BUG-016, fixed
    2026-07-26). COUNT(*) + 1 is not a sequence: delete any trade that is not
    the newest and the next value collides with a surviving `trade_id`, which
    is the PRIMARY KEY — so insert_trade() raises IntegrityError and the trade
    being recorded is lost, showing a raw sqlite error where a saved trade
    should be. Deleting T-002 of six and then adding a trade was enough.

    IDs are never reused. A deleted T-003 leaves a permanent gap, which is the
    correct behaviour for a trading record: the journal is evidence, and an ID
    that once meant one trade must never later mean a different one.

    Only 'T-'-prefixed IDs are considered, and the numeric part is compared as
    an INTEGER rather than as text — 'T-010' must outrank 'T-009', which a
    string MAX() would get right only by luck of zero-padding, and not at all
    past 'T-999'.
    """
    with managed_conn(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(CAST(SUBSTR(trade_id, 3) AS INTEGER)) AS hi "
            "FROM trades WHERE trade_id LIKE 'T-_%'"
        ).fetchone()
        highest = row["hi"] if row and row["hi"] is not None else 0
        return f"T-{highest + 1:03d}"


def insert_trade(db_path: str, trade: dict) -> None:
    """
    Insert a new trade. Pass a dict whose keys match trades table columns.
    'created_at' and 'updated_at' are always overwritten to UTC now.
    """
    now = _utcnow()
    columns = [
        'trade_id','entry_date','entry_time','day_of_week','spx_at_entry',
        'status','contracts','commissions','initial_legs','total_debit',
        'transform_date','transform_time','transform_minutes','spx_at_transform',
        'transform_legs','credit_received','profit_locked_in',
        'ic_expiry_date','ic_short_call','ic_long_call','ic_short_put','ic_long_put',
        'ic_call_wing','ic_put_wing','ic_max_profit','ic_worst_case','ic_risk_free',
        'result_date','spx_at_expiry','final_pl',
        'expired_inside_wings','expired_between_shorts','outcome','notes',
        *ENTRY_IV_COLUMNS,
    ]
    # Snapshotted here, not by the caller, so it cannot be forgotten at a call
    # site (ADR-044). An explicit value in `trade` still wins — that is how a
    # backfill or a test supplies its own.
    entry_iv = _entry_iv_for(db_path, trade)

    col_str = ", ".join(columns + ['created_at', 'updated_at'])
    val_str = ", ".join(f":{c}" for c in columns) + ", :created_at, :updated_at"
    with managed_conn(db_path) as conn:
        conn.execute(
            f"INSERT INTO trades ({col_str}) VALUES ({val_str})",
            {**{c: trade.get(c) for c in columns}, **entry_iv,
             'created_at': now, 'updated_at': now}
        )


def _entry_iv_for(db_path: str, fields: dict) -> dict:
    """Entry-IV columns for a trade, honouring any the caller supplied itself.

    Shared by insert_trade and update_trade so an edit cannot leave a stored
    context describing the trade as it used to be — moving the entry time or a
    strike changes which snapshot and which legs the context should come from.
    """
    supplied = {c: fields[c] for c in ENTRY_IV_COLUMNS if c in fields}
    if len(supplied) == len(ENTRY_IV_COLUMNS):
        return supplied
    computed = snapshot_entry_iv_context(
        db_path, fields.get('entry_date'), fields.get('entry_time'),
        fields.get('initial_legs'),
    )
    return {**computed, **supplied}


# An edit that touches any of these invalidates the stored entry context.
_ENTRY_IV_INPUTS = ('entry_date', 'entry_time', 'initial_legs')


def update_trade(db_path: str, trade_id: str, **fields) -> None:
    """
    Update specific columns on a trade. Pass column=value keyword args.
    'updated_at' is always set to UTC now automatically.

    If the edit changes an input to the entry-IV context, the context is
    recomputed from the trade's *post-edit* values (ADR-044) — the row after an
    edit must describe the trade it now is. Recomputation reads the same
    historical option_rows, so it is only as available as retention has left it;
    once those rows are pruned an edit will store NULLs rather than a wrong
    answer, and the row falls back to reconstruction like any pre-M3 trade.
    """
    if not fields:
        return
    if any(k in fields for k in _ENTRY_IV_INPUTS):
        current = get_trade(db_path, trade_id)
        # `k in current.keys()`, NOT `k in current`. Ruff's SIM118 wants the
        # shorter form and it is wrong here: `in` on a sqlite3.Row tests its
        # VALUES, not its column names, so `'entry_date' in row` is False on a
        # row that has an entry_date. Taking the suggestion blanks the stored
        # context on every edit, silently. Verified in a REPL, not assumed.
        merged = {k: (current[k] if current is not None and k in current.keys() else None)  # noqa: SIM118
                  for k in _ENTRY_IV_INPUTS}
        merged.update(fields)  # the edit wins, including any explicit entry_* values
        fields.update(_entry_iv_for(db_path, merged))
    fields['updated_at'] = _utcnow()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with managed_conn(db_path) as conn:
        conn.execute(
            f"UPDATE trades SET {set_clause} WHERE trade_id = :trade_id",
            {**fields, 'trade_id': trade_id}
        )


# ─────────────────────────────────────────────────────────────────────────────
# Retention (M3.2 — ADR-044)
#
# The only code in this project that deletes irreplaceable data. It is split in
# two on purpose: plan_prune() answers "what would go" and touches nothing,
# execute_prune() acts on a plan it is handed. A caller cannot delete without
# first holding the description of what it is deleting, and scripts/prune.py
# prints that description before it will act on it.
# ─────────────────────────────────────────────────────────────────────────────

def get_protected_expiries(db_path: str) -> set[str]:
    """Every expiry_date any trade used. Never prunable, at any age (ADR-044).

    Read from the trades rows themselves rather than from a list maintained by
    hand, so logging a trade protects its data with no further action. Covers
    both the diagonal's legs and the iron condor's expiry — a transformed trade
    has two structures and the record of it is worth nothing without both.

    Fails CLOSED. A trades table that is absent, or a legs blob that will not
    parse, yields nothing to protect — so any exception here must never be
    swallowed into an empty set, or the answer "protect nothing" arrives looking
    exactly like the truth. Only genuinely-absent trades gives an empty set.
    """
    expiries: set[str] = set()
    with get_conn(db_path) as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "trades" not in tables:
            return expiries          # journal never opened; nothing to protect
        rows = conn.execute(
            "SELECT initial_legs, transform_legs, ic_expiry_date FROM trades"
        ).fetchall()

    for row in rows:
        if row["ic_expiry_date"]:
            expiries.add(row["ic_expiry_date"])
        for blob in (row["initial_legs"], row["transform_legs"]):
            if not blob:
                continue
            # Deliberately unguarded: a leg blob that will not parse means a
            # trade whose expiries cannot be determined, and pruning around an
            # unknown is exactly the mistake this function exists to prevent.
            for leg in json.loads(blob):
                if leg.get("expiry"):
                    expiries.add(leg["expiry"])
    return expiries


def plan_prune(db_path: str, retention_days: int | None = None,
               today: str | None = None) -> dict:
    """Describe what a prune WOULD delete. Read-only — opens query_only.

    Returns the cutoff, the expiries that would go with their row counts, the
    expiries held back and why, and the totals. `today` is injectable so the
    boundary can be tested without waiting for the calendar.
    """
    days = config.RETENTION_DAYS if retention_days is None else retention_days
    if days < 0:
        raise ValueError(f"retention_days must not be negative, got {days}")
    ref = date.fromisoformat(today) if today else date.today()
    cutoff = (ref - timedelta(days=days)).isoformat()

    protected = get_protected_expiries(db_path)
    with get_conn(db_path) as conn:
        aged = conn.execute(
            "SELECT expiry_date, COUNT(*) AS rows FROM option_rows "
            "WHERE expiry_date < ? GROUP BY expiry_date ORDER BY expiry_date",
            (cutoff,),
        ).fetchall()
        total_rows = conn.execute(
            "SELECT COUNT(*) AS c FROM option_rows").fetchone()["c"]

    prunable = [{"expiry_date": r["expiry_date"], "rows": r["rows"]}
                for r in aged if r["expiry_date"] not in protected]
    held = [{"expiry_date": r["expiry_date"], "rows": r["rows"]}
            for r in aged if r["expiry_date"] in protected]

    return {
        "cutoff": cutoff,
        "retention_days": days,
        "as_of": ref.isoformat(),
        "prunable": prunable,
        "held_for_trades": held,
        "rows_to_delete": sum(e["rows"] for e in prunable),
        "rows_held": sum(e["rows"] for e in held),
        "option_rows_total": total_rows,
    }


def execute_prune(db_path: str, plan: dict) -> int:
    """Delete the option_rows a plan named. Returns rows actually deleted.

    Takes the plan rather than re-deriving the cutoff so that what was shown to
    the user and what is deleted cannot be two different answers — re-running
    the query here would silently re-widen the set if a trade were deleted, or
    if midnight passed, between the report and the confirmation.

    Deletes by the exact expiry list, one statement per expiry, inside one
    transaction: all of it lands or none of it does. Only option_rows is
    touched. atm_iv_by_expiry, snapshots and collection_gaps are never deleted.
    """
    expiries = [e["expiry_date"] for e in plan["prunable"]]
    if not expiries:
        return 0

    deleted = 0
    with managed_conn(db_path) as conn:
        for expiry in expiries:
            deleted += conn.execute(
                "DELETE FROM option_rows WHERE expiry_date = ?", (expiry,)
            ).rowcount
    logger.warning("retention: deleted %d option_rows across %d expiries "
                   "older than %s", deleted, len(expiries), plan["cutoff"])
    return deleted


def delete_trade(db_path: str, trade_id: str) -> None:  # write path
    """Permanently removes a trade record by trade_id.
    Called only from pages/journal.py after explicit user confirmation.
    No cascade needed — trades have no child rows in other tables.
    """
    with managed_conn(db_path) as conn:
        conn.execute("DELETE FROM trades WHERE trade_id = ?", (trade_id,))


# ─────────────────────────────────────────────────────────────────────────────
# Read Operations
# ─────────────────────────────────────────────────────────────────────────────

def get_all_trades(db_path: str) -> list:
    """All trades, newest entry date first."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM trades ORDER BY entry_date DESC, entry_time DESC"
        ).fetchall()


def get_trade(db_path: str, trade_id: str) -> sqlite3.Row | None:
    """Single trade by ID. Returns None if not found."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()


def get_eod_spx(db_path: str, date_str: str) -> float | None:
    """
    Last COMPLETE snapshot underlying_price on or before date_str (YYYY-MM-DD).
    Used by journal.py to auto-suggest SPX close when recording expiration.
    """
    with get_conn(db_path) as conn:
        row = conn.execute("""
            SELECT underlying_price FROM snapshots
            WHERE status             = 'COMPLETE'
              AND snapshot_timestamp <= ?
            ORDER BY snapshot_timestamp DESC
            LIMIT 1
        """, (date_str + " 23:59:59",)).fetchone()
        # See get_prior_session_close — same BUG-014 fix, same reasoning.
        if row is None or row["underlying_price"] is None:
            return None
        return float(row["underlying_price"])


def get_ic_marks(
    db_path: str,
    ic_expiry_date: str,
    short_call: float,
    long_call: float,
    short_put: float,
    long_put: float,
    eod_date: str | None = None,
) -> dict | None:
    """
    Retrieve bid/ask/mark prices for the four Iron Condor legs from option_rows.

    Default (eod_date=None): uses the most recent COMPLETE snapshot in the DB.
    eod_date='YYYY-MM-DD':   uses the LAST COMPLETE snapshot on that date,
                             enabling 'end-of-day unrealized P&L' for a past session.

    Returns a dict:
        snapshot_ts        — ISO8601 UTC timestamp of the snapshot used
        spx                — SPX underlying price at that snapshot
        short_call_mark/bid/ask
        long_call_mark/bid/ask
        short_put_mark/bid/ask
        long_put_mark/bid/ask
        cost_to_close      — (short_call + short_put - long_call - long_put) per share
                             Positive = IC has remaining value; subtract from
                             profit_locked_in to get unrealized P&L per share.

    Returns None if the option data is not available for these strikes/expiry.

    IC cost-to-close math:
        To close the IC we BUY BACK short legs and SELL TO CLOSE long legs.
        cost = mark(short_call) + mark(short_put) - mark(long_call) - mark(long_put)
        unrealized_per_sh = profit_locked_in - cost_to_close
        unrealized_per_contract = unrealized_per_sh * 100 * contracts
    """
    with get_conn(db_path) as conn:
        if eod_date:
            snap = conn.execute("""
                SELECT snapshot_id, snapshot_timestamp, underlying_price
                FROM snapshots
                WHERE status             = 'COMPLETE'
                  AND snapshot_timestamp <= ?
                ORDER BY snapshot_timestamp DESC
                LIMIT 1
            """, (eod_date + " 23:59:59",)).fetchone()
        else:
            snap = conn.execute("""
                SELECT snapshot_id, snapshot_timestamp, underlying_price
                FROM snapshots
                WHERE status = 'COMPLETE'
                ORDER BY snapshot_timestamp DESC
                LIMIT 1
            """).fetchone()

        if not snap:
            return None

        rows = conn.execute("""
            SELECT strike, right, bid, ask,
                   COALESCE(mark, (bid + ask) / 2.0) AS mark_value
            FROM option_rows
            WHERE snapshot_id = ?
              AND expiry_date  = ?
              AND (
                  (strike = ? AND right = 'C') OR
                  (strike = ? AND right = 'C') OR
                  (strike = ? AND right = 'P') OR
                  (strike = ? AND right = 'P')
              )
        """, (snap["snapshot_id"], ic_expiry_date,
              short_call, long_call, short_put, long_put)).fetchall()

        if not rows:
            return None

        # BUG-014 (fixed 2026-07-26): this used to do `r["mark"] or 0.0`, which
        # turned a NULL mark into a real-looking 0.0. That number then flowed
        # straight into cost_to_close and out to the unrealized-P&L figure, so
        # a missing quote understated the cost of buying back a short — a wrong
        # money number presented as a right one.
        #
        # Two changes. First, fall back to the bid/ask midpoint in SQL, as every
        # history query in this module already does (DEBT-012 records that this
        # was the one place missing it). Second, if a leg has no computable mark
        # even then, treat the leg as absent rather than as zero: this function
        # is already all-four-or-nothing, and a partial IC valuation would be
        # quietly wrong instead of obviously unavailable.
        leg_map = {}
        for r in rows:
            leg_map[(float(r["strike"]), r["right"])] = {
                "bid":  r["bid"] if r["bid"] is not None else 0.0,
                "ask":  r["ask"] if r["ask"] is not None else 0.0,
                "mark": r["mark_value"],
            }

        sc = leg_map.get((float(short_call), "C"))
        lc = leg_map.get((float(long_call),  "C"))
        sp = leg_map.get((float(short_put),  "P"))
        lp = leg_map.get((float(long_put),   "P"))

        legs = [sc, lc, sp, lp]
        if not all(legs) or any(leg["mark"] is None for leg in legs):
            return None

        cost = sc["mark"] + sp["mark"] - lc["mark"] - lp["mark"]

        return {
            "snapshot_ts":       snap["snapshot_timestamp"],
            "spx":               snap["underlying_price"],
            "short_call_mark":   sc["mark"], "short_call_bid": sc["bid"], "short_call_ask": sc["ask"],
            "long_call_mark":    lc["mark"], "long_call_bid":  lc["bid"], "long_call_ask":  lc["ask"],
            "short_put_mark":    sp["mark"], "short_put_bid":  sp["bid"], "short_put_ask":  sp["ask"],
            "long_put_mark":     lp["mark"], "long_put_bid":   lp["bid"], "long_put_ask":   lp["ask"],
            "cost_to_close":     cost,
        }


# ─────────────────────────────────────────────────────────────────────────────
# T-001 Seed (first live trade, entered before journal was built)
# ─────────────────────────────────────────────────────────────────────────────

def seed_t001(db_path: str) -> None:
    """
    Insert T-001 if the trades table is empty or T-001 does not exist.
    No-op if T-001 already exists. Call from journal.py on every startup.
    """
    with managed_conn(db_path) as conn:
        if conn.execute(
            "SELECT trade_id FROM trades WHERE trade_id = 'T-001'"
        ).fetchone():
            return

        now = _utcnow()
        initial = json.dumps([
            {"expiry": "2026-06-30", "type": "Call", "action": "Sell to Open", "strike": 7380, "fill": 24.10},
            {"expiry": "2026-06-30", "type": "Put",  "action": "Sell to Open", "strike": 7320, "fill": 66.65},
            {"expiry": "2026-07-02", "type": "Call", "action": "Buy to Open",  "strike": 7400, "fill": 32.95},
            {"expiry": "2026-07-02", "type": "Put",  "action": "Buy to Open",  "strike": 7300, "fill": 70.70},
        ])
        transform = json.dumps([
            {"expiry": "2026-07-02", "type": "Call", "action": "Sell to Close", "strike": 7400, "fill": 37.01},
            {"expiry": "2026-07-02", "type": "Put",  "action": "Sell to Close", "strike": 7300, "fill": 58.34},
            {"expiry": "2026-06-30", "type": "Call", "action": "Buy to Open",   "strike": 7385, "fill": 25.92},
            {"expiry": "2026-06-30", "type": "Put",  "action": "Buy to Open",   "strike": 7315, "fill": 50.53},
        ])
        notes = (
            "First live trade. Entry-to-transformation in 13 minutes — unusually fast "
            "due to an immediate favorable SPX move. Back-month call gained +$4.06 "
            "during hold. 5-point wings (7380/7385C, 7315/7320P) created a fully "
            "risk-free structure. Locked net credit $5.90/sh > $5.00/sh max IC loss "
            "→ guaranteed $90 floor, $590 ceiling if SPX closes inside 7320–7380. "
            "First real-fill calibration point: $5–6 net credit achievable intraday. "
            "Paper benchmark of $5.00 confirmed directionally correct."
        )
        conn.execute("""
            INSERT INTO trades (
                trade_id, entry_date, entry_time, day_of_week, spx_at_entry,
                status, contracts, commissions, initial_legs, total_debit,
                transform_date, transform_time, transform_minutes, spx_at_transform,
                transform_legs, credit_received, profit_locked_in,
                ic_expiry_date, ic_short_call, ic_long_call, ic_short_put, ic_long_put,
                ic_call_wing, ic_put_wing, ic_max_profit, ic_worst_case, ic_risk_free,
                result_date, spx_at_expiry, final_pl,
                expired_inside_wings, expired_between_shorts, outcome,
                notes, created_at, updated_at
            ) VALUES (
                'T-001','2026-06-26','09:34','Friday',NULL,
                'Transformed',1,NULL,?,13.00,
                '2026-06-26','09:47',13,NULL,
                ?,18.90,5.90,
                '2026-06-30',7380.0,7385.0,7320.0,7315.0,
                5.0,5.0,590.0,90.0,1,
                NULL,NULL,NULL,NULL,NULL,NULL,
                ?,?,?
            )
        """, (initial, transform, notes, now, now))
        logger.info("T-001 seeded into trades table.")
