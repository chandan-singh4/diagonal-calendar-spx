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
from datetime import UTC, datetime

import config

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

-- ── atm_iv_by_expiry ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS atm_iv_by_expiry (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id         INTEGER NOT NULL
                            REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    expiry_date         TEXT    NOT NULL,
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
        _has_uq = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'index' AND name = 'uq_option_rows_contract'"
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

      So: compare cursor.rowcount against what was offered and log any
      shortfall as a WARNING. This does not change what gets stored. It only
      makes a loss that was previously silent and permanent visible in
      collector.log. Deciding per-constraint behaviour (keep OR IGNORE for
      genuine duplicates, raise on everything else) remains M3.6 work.

      UPDATED 2026-07-26 (BUG-017): the collector used to discard this return
      value and record `strikes_fetched = len(option_rows)` — the offered
      count — so a snapshot that had lost rows still reported full coverage.
      It now stores this value. Callers must keep doing so: the log warning
      below is a signal for a human, while this return value is what the
      recorded history is judged by.
    """
    if not rows:
        return 0

    sql = """
        INSERT OR IGNORE INTO option_rows (
            snapshot_id, expiry_date, dte, strike, right,
            bid, ask, mark, last,
            iv, delta, gamma, theta, vega,
            volume, open_interest, intrinsic_value, time_value
        ) VALUES (
            :snapshot_id, :expiry_date, :dte, :strike, :right,
            :bid, :ask, :mark, :last,
            :iv, :delta, :gamma, :theta, :vega,
            :volume, :open_interest, :intrinsic_value, :time_value
        )
    """
    with managed_conn(db_path) as conn:
        inserted = conn.executemany(sql, rows).rowcount

    if inserted < len(rows):
        logger.warning(
            "insert_option_rows: %d of %d rows were DISCARDED by the database "
            "(snapshot_id=%s). A duplicate contract is benign; anything else is "
            "silent data loss — see DEBT-008 / ADR-022.",
            len(rows) - inserted, len(rows), rows[0].get("snapshot_id"),
        )

    return inserted


def insert_atm_iv_records(db_path: str, records: list[dict]) -> None:
    """
    Bulk-insert pre-aggregated ATM IV records.
    One record per expiry per snapshot — call after insert_option_rows() commits.
    """
    if not records:
        return

    sql = """
        INSERT INTO atm_iv_by_expiry (
            snapshot_id, expiry_date, dte, atm_strike,
            atm_call_iv, atm_put_iv, atm_avg_iv,
            iv_spread_to_front, iv_ratio_to_front
        ) VALUES (
            :snapshot_id, :expiry_date, :dte, :atm_strike,
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
                                  expiry_date: str,
                                  n: int = 2) -> list:
    """
    Last N ATM IV records for a specific expiry, most recent first.
    Used for the day-change metric in the dashboard left panel.

    IVs are returned in decimal form (0.18 = 18%) — multiply by 100 for display.
    """
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT a.atm_avg_iv, s.snapshot_timestamp
            FROM atm_iv_by_expiry a
            JOIN snapshots s ON s.snapshot_id = a.snapshot_id
            WHERE a.expiry_date = ?
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
    """
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM option_rows
            WHERE snapshot_id = ?
            ORDER BY expiry_date, strike, right
            """,
            (snapshot_id,)
        ).fetchall()


def get_contract_iv_history(db_path: str, expiry_date: str, strike: float,
                              right: str, days: int = 30) -> list:
    """
    IV time-series for a specific option contract over the last N days.
    Drives the 'Selected-Strike IV' chart in the dashboard.

    right: 'C' or 'P' (not 'CALL'/'PUT').
    IVs are in decimal form — app.py multiplies by 100 at the load boundary.

    Performance: uses idx_option_rows_contract_snap (covering index).
    """
    with get_conn(db_path) as conn:
        return conn.execute(
            """
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
              AND s.status      = 'COMPLETE'
              AND s.snapshot_timestamp >= datetime('now', ?, 'utc')
            ORDER BY s.snapshot_timestamp
            """,
            (expiry_date, strike, right, f"-{days} days")
        ).fetchall()


def get_atm_iv_history(db_path: str, expiry_date: str,
                        days: int = 30) -> list:
    """
    ATM IV history for a specific expiry over the last N days.
    Primary query for term structure charts and range stats.

    IVs are in decimal form — app.py multiplies by 100 at the load boundary.

    Performance: uses idx_atm_iv_expiry_snap. Scans ~3,150 rows per 30 days
    rather than scanning option_rows directly (~4.8M rows).
    """
    with get_conn(db_path) as conn:
        return conn.execute(
            """
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

        leg_rows = conn.execute(
            """
            SELECT expiry_date, strike, right, iv
            FROM option_rows
            WHERE snapshot_id = ?
              AND ( (expiry_date = ? AND strike = ? AND right = 'C')
                 OR (expiry_date = ? AND strike = ? AND right = 'P')
                 OR (expiry_date = ? AND strike = ? AND right = 'C')
                 OR (expiry_date = ? AND strike = ? AND right = 'P') )
            """,
            (sid, front_expiry, cs, front_expiry, ps,
             back_expiry, cs, back_expiry, ps),
        ).fetchall()

        legs = {(r["expiry_date"], float(r["strike"]), r["right"]): r["iv"]
                for r in leg_rows}
        front_iv = _mean([legs.get((front_expiry, cs, "C")),
                          legs.get((front_expiry, ps, "P"))])
        back_iv = _mean([legs.get((back_expiry, cs, "C")),
                         legs.get((back_expiry, ps, "P"))])

        atm_rows = conn.execute(
            """
            SELECT expiry_date, atm_avg_iv
            FROM atm_iv_by_expiry
            WHERE snapshot_id = ? AND expiry_date IN (?, ?)
            """,
            (sid, front_expiry, back_expiry),
        ).fetchall()
        atm = {r["expiry_date"]: r["atm_avg_iv"] for r in atm_rows}
        atm_front, atm_back = atm.get(front_expiry), atm.get(back_expiry)

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
    with get_conn(db_path) as conn:
        return conn.execute(
            """
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
            JOIN atm_iv_by_expiry b
                ON b.snapshot_id = s.snapshot_id AND b.expiry_date = ?
            LEFT JOIN option_rows ofc
                ON ofc.snapshot_id = s.snapshot_id
               AND ofc.expiry_date = ? AND ofc.strike = ? AND ofc.right = 'C'
            LEFT JOIN option_rows obc
                ON obc.snapshot_id = s.snapshot_id
               AND obc.expiry_date = ? AND obc.strike = ? AND obc.right = 'C'
            LEFT JOIN option_rows ofp
                ON ofp.snapshot_id = s.snapshot_id
               AND ofp.expiry_date = ? AND ofp.strike = ? AND ofp.right = 'P'
            LEFT JOIN option_rows obp
                ON obp.snapshot_id = s.snapshot_id
               AND obp.expiry_date = ? AND obp.strike = ? AND obp.right = 'P'
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
                front_expiry, back_expiry,
                front_expiry, float(call_strike),
                back_expiry,  float(call_strike),
                front_expiry, float(put_strike),
                back_expiry,  float(put_strike),
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
    with get_conn(db_path) as conn:
        return conn.execute(
            """
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
            LEFT JOIN option_rows obc
                ON obc.snapshot_id = s.snapshot_id
               AND obc.expiry_date = ? AND obc.strike = ? AND obc.right = 'C'
            LEFT JOIN option_rows ofp
                ON ofp.snapshot_id = s.snapshot_id
               AND ofp.expiry_date = ? AND ofp.strike = ? AND ofp.right = 'P'
            LEFT JOIN option_rows obp
                ON obp.snapshot_id = s.snapshot_id
               AND obp.expiry_date = ? AND obp.strike = ? AND obp.right = 'P'
            LEFT JOIN option_rows owc
                ON owc.snapshot_id = s.snapshot_id
               AND owc.expiry_date = ? AND owc.strike = ? AND owc.right = 'C'
            LEFT JOIN option_rows owp
                ON owp.snapshot_id = s.snapshot_id
               AND owp.expiry_date = ? AND owp.strike = ? AND owp.right = 'P'
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
                front_expiry, float(call_strike),
                back_expiry,  float(call_strike),
                front_expiry, float(put_strike),
                back_expiry,  float(put_strike),
                front_expiry, float(call_strike) + 5,
                front_expiry, float(put_strike)  - 5,
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
    ]
    col_str = ", ".join(columns + ['created_at', 'updated_at'])
    val_str = ", ".join(f":{c}" for c in columns) + ", :created_at, :updated_at"
    with managed_conn(db_path) as conn:
        conn.execute(
            f"INSERT INTO trades ({col_str}) VALUES ({val_str})",
            {**{c: trade.get(c) for c in columns}, 'created_at': now, 'updated_at': now}
        )


def update_trade(db_path: str, trade_id: str, **fields) -> None:
    """
    Update specific columns on a trade. Pass column=value keyword args.
    'updated_at' is always set to UTC now automatically.
    """
    if not fields:
        return
    fields['updated_at'] = _utcnow()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with managed_conn(db_path) as conn:
        conn.execute(
            f"UPDATE trades SET {set_clause} WHERE trade_id = :trade_id",
            {**fields, 'trade_id': trade_id}
        )


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
