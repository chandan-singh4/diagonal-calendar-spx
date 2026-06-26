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
                 get_iv_spread_history, get_spx_intraday_today,
                 get_all_expiry_atm_iv_today, update_snapshot_notes

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
from __future__ import annotations   # allows X | Y type hints on Python 3.7+

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

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

CREATE INDEX IF NOT EXISTS idx_option_rows_snapshot_id
    ON option_rows(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_option_rows_contract
    ON option_rows(expiry_date, strike, right);

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

def _make_conn(db_path: str) -> sqlite3.Connection:
    """
    Open a SQLite connection with:
      - row_factory = sqlite3.Row  (columns accessible by name)
      - WAL mode                   (readers don't block the collector writer)
      - foreign_keys = ON          (enforces ON DELETE CASCADE)
      - 15-second timeout          (handles transient locks gracefully)
    """
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_conn(db_path: str | None = None):
    """
    Context manager for app.py read operations.
    Accepts optional db_path; defaults to config.DB_PATH.
    """
    conn = _make_conn(db_path or config.DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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
    Either all rows commit or none do. Returns the number of rows inserted.
    """
    if not rows:
        return 0

    sql = """
        INSERT INTO option_rows (
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
        conn.executemany(sql, rows)
    return len(rows)


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
    with managed_conn(db_path) as conn:
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
    with managed_conn(db_path) as conn:
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
    with managed_conn(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(snapshot_timestamp) AS ts FROM snapshots"
        ).fetchone()
        return row["ts"] if row and row["ts"] else None


def get_snapshots(db_path: str, start: str, end: str,
                   status: str = "COMPLETE") -> list:
    """Snapshots between start and end (UTC ISO8601) with given status."""
    with managed_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM snapshots
            WHERE snapshot_timestamp BETWEEN ? AND ?
              AND status = ?
            ORDER BY snapshot_timestamp
            """,
            (start, end, status)
        ).fetchall()


def get_option_chain(db_path: str, snapshot_id: int) -> list:
    """
    Full option chain for a specific snapshot.
    Used by app.py to reconstruct chain_df on every dashboard refresh.

    Results ordered by expiry_date, strike, right for consistent display.
    IVs are in decimal form — app.py multiplies by 100 at the load boundary.
    """
    with managed_conn(db_path) as conn:
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
    with managed_conn(db_path) as conn:
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
    with managed_conn(db_path) as conn:
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


def get_term_structure(db_path: str, snapshot_id: int) -> list:
    """All expiries for a given snapshot, ordered by DTE ascending."""
    with managed_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM atm_iv_by_expiry
            WHERE snapshot_id = ?
            ORDER BY dte
            """,
            (snapshot_id,)
        ).fetchall()


def get_iv_spread_history(db_path: str, front_expiry: str, back_expiry: str,
                           days: int = 180) -> list:
    """
    Front-vs-back IV spread history for the IV percentile engine.
    Returns one row per COMPLETE snapshot where both expiries are present.
    Used for 'current IV spread is at the Nth percentile of last 180 days'.
    """
    with managed_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT
                s.snapshot_timestamp,
                s.underlying_price,
                s.vix_value,
                f.atm_avg_iv                                   AS front_iv,
                b.atm_avg_iv                                   AS back_iv,
                b.atm_avg_iv - f.atm_avg_iv                   AS iv_spread,
                CASE WHEN f.atm_avg_iv > 0
                     THEN b.atm_avg_iv / f.atm_avg_iv
                     ELSE NULL END                             AS iv_ratio
            FROM snapshots s
            JOIN atm_iv_by_expiry f
                ON f.snapshot_id = s.snapshot_id
               AND f.expiry_date = ?
            JOIN atm_iv_by_expiry b
                ON b.snapshot_id = s.snapshot_id
               AND b.expiry_date = ?
            WHERE s.status = 'COMPLETE'
              AND s.snapshot_timestamp >= datetime('now', ?, 'utc')
            ORDER BY s.snapshot_timestamp
            """,
            (front_expiry, back_expiry, f"-{days} days")
        ).fetchall()


def get_gaps(db_path: str, start: str, end: str,
              exclude_reasons: list[str] | None = None) -> list:
    """Collection gaps within a date range."""
    with managed_conn(db_path) as conn:
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


def get_spx_intraday_today(db_path: str) -> list:
    """
    SPX price at every COMPLETE snapshot in today's UTC calendar date.

    Used by app.py for:
      - The SPX intraday price chart (Section 3)
      - Daily change calculation: current price vs first snapshot of the day

    'Today' is bounded by SQLite's date('now') in UTC, which is reliable
    here because the collector only runs during ET market hours (13:30–20:00 UTC)
    — entirely within a single UTC calendar day. No timezone library required.

    Returns rows with: snapshot_timestamp (TEXT), underlying_price (REAL).
    """
    with managed_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT snapshot_timestamp, underlying_price
            FROM snapshots
            WHERE status              = 'COMPLETE'
              AND snapshot_timestamp >= date('now')
            ORDER BY snapshot_timestamp
            """
        ).fetchall()


def get_all_expiry_atm_iv_today(db_path: str) -> list:
    """
    ATM IV for every expiry at every COMPLETE snapshot in today's UTC date.

    Used by app.py's Pair Scanner to compute intraday IV ratios for all
    possible (front, back) expiry combinations.  Queried once per dashboard
    refresh; the pivot and ratio computation happen in Python.

    'Today' is bounded by date('now') in UTC — same rationale as
    get_spx_intraday_today above.

    Returns rows with:
      snapshot_timestamp  TEXT  — UTC 'YYYY-MM-DD HH:MM:SS'
      expiry_date         TEXT  — 'YYYY-MM-DD'
      dte                 INT   — days to expiration at snapshot time
      atm_avg_iv          REAL  — decimal form (0.18 = 18%); caller × 100

    Performance: uses idx_atm_iv_expiry_snap; at 5-min polling across
    20 expirations for 6.5 market hours ≈ 1,560 rows/day — trivially fast.
    """
    with managed_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT
                s.snapshot_timestamp,
                a.expiry_date,
                a.dte,
                a.atm_avg_iv
            FROM atm_iv_by_expiry a
            JOIN snapshots s ON s.snapshot_id = a.snapshot_id
            WHERE s.status              = 'COMPLETE'
              AND s.snapshot_timestamp >= date('now')
            ORDER BY s.snapshot_timestamp, a.expiry_date
            """
        ).fetchall()


def update_snapshot_notes(db_path: str, snapshot_id: int, notes: str) -> None:
    """
    Update the notes field on a snapshot record.
    The only write operation permitted from app.py on this schema.
    """
    with managed_conn(db_path) as conn:
        conn.execute(
            "UPDATE snapshots SET notes = ? WHERE snapshot_id = ?",
            (notes, snapshot_id)
        )
