"""
db.py — Database layer for the SPX Diagonal Calendar Dashboard.

This module is the single source of truth for:
  - Schema creation and versioning
  - All write operations  (collector.py for the new schema;
                           legacy writes kept for the existing app.py)
  - All read operations   (app.py — both legacy and new)

ARCHITECTURE RULE
  collector.py and app.py never issue SQL directly.
  All database interaction goes through functions defined here.

SCHEMA EVOLUTION
  Two schemas coexist in the same .db file until app.py is refactored:

  LEGACY (supports existing app.py — do not remove until app.py is updated)
    expiry_snapshots  — per-expiry ATM IV rows used by current charts
    strike_snapshots  — per-strike IV rows used by current charts
    positions         — basic position log

  NEW (snapshot-anchored — used by collector.py and future refactored app.py)
    schema_version    — version tracking; enables future migrations
    snapshots         — one row per collection cycle; anchor for all child data
    option_rows       — one row per contract per snapshot; irreplaceable record
    atm_iv_by_expiry  — pre-aggregated ATM IV per expiry; powers analytics queries
    collection_gaps   — audit log of missed collection windows

  Migration plan: once collector.py is running and app.py is refactored to
  read from the new tables, the LEGACY tables and their functions are removed
  in a single commit.
"""
from __future__ import annotations  # allows X | Y type hints on Python 3.7+

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schema Version
# Increment this constant when the new schema changes and add a migration
# function. The init_db() version check will detect the mismatch on startup.
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

# ─────────────────────────────────────────────────────────────────────────────
# DDL — Legacy Schema
# Kept verbatim from the original db.py so existing app.py calls are unaffected.
# Remove this block (and the legacy functions below) when app.py is refactored.
# ─────────────────────────────────────────────────────────────────────────────

_LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS expiry_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    underlying_price REAL    NOT NULL,
    expiry           TEXT    NOT NULL,
    dte              INTEGER,
    atm_iv           REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expiry_snapshots_expiry_ts
    ON expiry_snapshots(expiry, timestamp);

CREATE TABLE IF NOT EXISTS strike_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    underlying_price REAL    NOT NULL,
    expiry           TEXT    NOT NULL,
    strike           REAL    NOT NULL,
    side             TEXT    NOT NULL,
    iv               REAL,
    bid              REAL,
    ask              REAL,
    volume           INTEGER,
    open_interest    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_strike_snapshots
    ON strike_snapshots(expiry, strike, side, timestamp);

CREATE TABLE IF NOT EXISTS positions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at        TEXT    NOT NULL,
    front_expiry     TEXT    NOT NULL,
    back_expiry      TEXT    NOT NULL,
    call_strike      REAL    NOT NULL,
    put_strike       REAL    NOT NULL,
    entry_debit      REAL    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'open',
    transformed_at   TEXT,
    transform_notes  TEXT
);
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — New Snapshot-Anchored Schema
# ─────────────────────────────────────────────────────────────────────────────

_NEW_DDL = """
-- ── Schema version tracker ───────────────────────────────────────────────────
-- Enables future migrations. Written once on fresh database creation.
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL,   -- UTC ISO8601
    description TEXT
);

-- ── snapshots ─────────────────────────────────────────────────────────────────
-- One row per collection cycle, attempted or successful.
-- Created at cycle START with status='PARTIAL' so a record always exists even
-- if the process crashes during option_row insertion. Updated to 'COMPLETE' or
-- 'FAILED' only after all child rows are committed.
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_timestamp    TEXT    NOT NULL,          -- UTC ISO8601; set when cycle begins, not when it ends
    status                TEXT    NOT NULL
                              CHECK(status IN ('COMPLETE', 'PARTIAL', 'FAILED')),
    underlying_price      REAL,                      -- SPX mid-price at collection time
    underlying_bid        REAL,                      -- SPX bid (widens during stress)
    underlying_ask        REAL,                      -- SPX ask
    vix_value             REAL,                      -- VIX spot; NULL if VIX call failed (non-fatal)
    market_session        TEXT                       -- 'OPEN' | 'MIDDAY' | 'CLOSE'
                              CHECK(market_session IN ('OPEN', 'MIDDAY', 'CLOSE')),
    poll_interval_used    INTEGER,                   -- seconds: 60 (event) or 300 (normal)
    strikes_fetched       INTEGER,                   -- actual option_rows written this cycle
    expiries_fetched      INTEGER,                   -- distinct expiry_dates written this cycle
    collection_latency_ms INTEGER,                   -- wall-clock ms: first API call → DB commit
    error_message         TEXT,                      -- populated for PARTIAL or FAILED
    notes                 TEXT                       -- human annotations; writable from app.py
);

-- Time-range queries are the dominant access pattern.
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
    ON snapshots(snapshot_timestamp);

-- Status filter: analytics always filters to COMPLETE snapshots.
CREATE INDEX IF NOT EXISTS idx_snapshots_status
    ON snapshots(status);

-- Combined: nearly every dashboard query filters by both time range and status.
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp_status
    ON snapshots(snapshot_timestamp, status);

-- ── option_rows ───────────────────────────────────────────────────────────────
-- One row per option contract per snapshot.
-- IRREPLACEABLE — Schwab has no historical intraday option chain endpoint.
-- Every row here is data that cannot be reconstructed from any external source.
CREATE TABLE IF NOT EXISTS option_rows (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id      INTEGER NOT NULL
                         REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    expiry_date      TEXT    NOT NULL,               -- 'YYYY-MM-DD'
    dte              INTEGER NOT NULL,               -- DTE as of snapshot_timestamp
                                                     -- (stored, not derived — DTE at collection is a historical fact)
    strike           REAL    NOT NULL,
    right            TEXT    NOT NULL
                         CHECK(right IN ('C', 'P')),
    bid              REAL,
    ask              REAL,
    mark             REAL,                           -- (bid + ask) / 2; stored explicitly to avoid recomputation
    last             REAL,                           -- last traded price
    iv               REAL,                           -- decimal: 0.18 = 18%
    delta            REAL,                           -- directional exposure
    gamma            REAL,                           -- rate of delta change; elevated near expiry
    theta            REAL,                           -- daily time decay; core diagonal strategy metric
    vega             REAL,                           -- IV sensitivity; drives transformation timing
    volume           INTEGER,                        -- today's contract volume; first liquidity signal
    open_interest    INTEGER,                        -- total open contracts; second liquidity signal
    intrinsic_value  REAL,                           -- stored at collection to avoid joins in historical queries
    time_value       REAL                            -- mark - intrinsic_value; the pure optionality premium
);

-- Chain reconstruction: "give me all rows for snapshot X"
CREATE INDEX IF NOT EXISTS idx_option_rows_snapshot_id
    ON option_rows(snapshot_id);

-- Contract lookup: "give me all rows for the 5700C expiring 2026-06-26"
CREATE INDEX IF NOT EXISTS idx_option_rows_contract
    ON option_rows(expiry_date, strike, right);

-- MOST CRITICAL INDEX: makes "IV history for a specific contract over N days"
-- a covering index scan rather than a full-table scan.
-- Without this, every per-contract time-series query scans the entire table.
-- At ~40M rows/year this difference is seconds vs milliseconds.
CREATE INDEX IF NOT EXISTS idx_option_rows_contract_snap
    ON option_rows(expiry_date, strike, right, snapshot_id);

-- ── atm_iv_by_expiry ──────────────────────────────────────────────────────────
-- Pre-aggregated ATM IV per expiry per snapshot. Computed and stored at
-- collection time. This is what the IV percentile engine, term structure charts,
-- and diagonal opportunity scanner primarily query — not option_rows directly.
--
-- Performance rationale: "give me the front-vs-back IV spread for every COMPLETE
-- snapshot in the last 180 days" scans ~1.8M rows here vs ~360M rows if done
-- against option_rows. The difference compounds as history grows.
CREATE TABLE IF NOT EXISTS atm_iv_by_expiry (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id         INTEGER NOT NULL
                            REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    expiry_date         TEXT    NOT NULL,            -- 'YYYY-MM-DD'
    dte                 INTEGER NOT NULL,
    atm_strike          REAL    NOT NULL,            -- strike closest to underlying at collection time
                                                     -- (stored because ATM shifts as SPX moves)
    atm_call_iv         REAL,
    atm_put_iv          REAL,
    atm_avg_iv          REAL,                        -- (atm_call_iv + atm_put_iv) / 2
    iv_spread_to_front  REAL,                        -- this_iv - front_iv; NULL for front expiry itself
    iv_ratio_to_front   REAL                         -- this_iv / front_iv; NULL for front; handles inversions
);

-- Term structure per snapshot
CREATE INDEX IF NOT EXISTS idx_atm_iv_snapshot_id
    ON atm_iv_by_expiry(snapshot_id);

-- IV history for a specific expiry: primary index for percentile calculations.
CREATE INDEX IF NOT EXISTS idx_atm_iv_expiry_snap
    ON atm_iv_by_expiry(expiry_date, snapshot_id);

-- ── collection_gaps ───────────────────────────────────────────────────────────
-- Audit log of intervals where no collection occurred during market hours.
-- No FK to snapshots — gap records describe the ABSENCE of snapshots.
-- Used by the analytics layer to qualify IV percentile claims:
--   "88th percentile based on 163 of 180 trading days" is honest;
--   "88th percentile" without coverage context may be misleading.
CREATE TABLE IF NOT EXISTS collection_gaps (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_start                TEXT    NOT NULL,       -- UTC: last successful snapshot before gap
    gap_end                  TEXT    NOT NULL,       -- UTC: first successful snapshot after gap
    gap_minutes              REAL    NOT NULL,
    expected_snapshots_lost  INTEGER,               -- gap_minutes / poll_interval estimate
    reason                   TEXT,                  -- 'COLLECTOR_OFFLINE' | 'API_ERROR' |
                                                    -- 'MARKET_CLOSED' | 'HOLIDAY' | 'UNKNOWN'
    detected_at              TEXT    NOT NULL,       -- UTC: when collector noticed the gap
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
    Legacy context manager — keeps the original function signature so all
    existing app.py calls work unchanged.

    Upgraded from original: now rolls back on exception (was missing before).
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
    New context manager for collector.py functions. Requires explicit db_path
    (no default) to prevent accidental writes to the wrong database.
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

    Creates both the legacy tables (backward-compat for app.py) and the new
    snapshot-anchored tables (for collector.py). Both coexist until app.py
    is refactored.

    Raises RuntimeError if the database contains a schema_version newer than
    SCHEMA_VERSION — this means old code is opening a newer database.
    """
    path = db_path or config.DB_PATH
    conn = _make_conn(path)
    try:
        # executescript issues implicit commits between statements — correct
        # behavior for DDL (schema changes don't need to be transactional).
        conn.executescript(_LEGACY_DDL)
        conn.executescript(_NEW_DDL)
        conn.commit()

        # Version tracking
        row = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        current = row["v"] if row and row["v"] is not None else 0

        if current == 0:
            # Fresh database — write the version record
            conn.execute(
                "INSERT INTO schema_version (version, applied_at, description) "
                "VALUES (?, ?, ?)",
                (SCHEMA_VERSION, _utcnow(),
                 "Initial schema: legacy compat tables + new snapshot-anchored tables")
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
# LEGACY — Write Operations  (app.py — keep until app.py is refactored)
# Function signatures are UNCHANGED from original db.py.
# ─────────────────────────────────────────────────────────────────────────────

def save_expiry_snapshot(underlying_price, expiry, dte, atm_iv,
                          db_path: str | None = None,
                          timestamp: str | None = None) -> str:
    """
    Write one ATM IV row for a single expiry.
    Pass the same timestamp value for all expiries in the same poll cycle
    so they share a consistent timestamp for front/back joining.
    """
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO expiry_snapshots "
            "(timestamp, underlying_price, expiry, dte, atm_iv) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, underlying_price, expiry, dte, atm_iv),
        )
    return ts


def save_strike_snapshot(underlying_price, expiry, strike, side, iv, bid, ask,
                          volume, open_interest,
                          db_path: str | None = None,
                          timestamp: str | None = None) -> str:
    """
    Write IV for a specific strike. Call once per leg of the diagonal
    (front_call, front_put, back_call, back_put) with the same timestamp.
    """
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO strike_snapshots "
            "(timestamp, underlying_price, expiry, strike, side, "
            "iv, bid, ask, volume, open_interest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, underlying_price, expiry, strike, side,
             iv, bid, ask, volume, open_interest),
        )
    return ts


def save_position(front_expiry, back_expiry, call_strike, put_strike,
                   entry_debit, db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO positions "
            "(opened_at, front_expiry, back_expiry, call_strike, "
            "put_strike, entry_debit) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), front_expiry, back_expiry,
             call_strike, put_strike, entry_debit),
        )


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY — Read Operations  (app.py — keep until app.py is refactored)
# Function signatures are UNCHANGED from original db.py.
# ─────────────────────────────────────────────────────────────────────────────

def get_expiry_history(expiry: str, since_iso: str | None = None,
                        db_path: str | None = None, limit: int = 20000) -> list:
    query = "SELECT * FROM expiry_snapshots WHERE expiry = ?"
    params: list = [expiry]
    if since_iso:
        query += " AND timestamp >= ?"
        params.append(since_iso)
    query += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        return conn.execute(query, params).fetchall()


def get_latest_two_snapshots(expiry: str, db_path: str | None = None) -> list:
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM expiry_snapshots WHERE expiry = ? "
            "ORDER BY timestamp DESC LIMIT 2",
            (expiry,),
        ).fetchall()


def has_any_data(db_path: str | None = None) -> bool:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM expiry_snapshots"
        ).fetchone()
        return row["n"] > 0


def get_strike_history(expiry: str, strike: float, side: str,
                        since_iso: str | None = None,
                        db_path: str | None = None,
                        limit: int = 20000) -> list:
    query = ("SELECT * FROM strike_snapshots "
             "WHERE expiry = ? AND strike = ? AND side = ?")
    params: list = [expiry, strike, side]
    if since_iso:
        query += " AND timestamp >= ?"
        params.append(since_iso)
    query += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        return conn.execute(query, params).fetchall()


def get_open_positions(db_path: str | None = None) -> list:
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM positions WHERE status = 'open'"
        ).fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# NEW — Write Operations  (collector.py ONLY)
# app.py never calls these. collector.py is the sole writer on the new schema.
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

    Why PARTIAL at creation: if the process crashes during option_row insertion,
    the snapshot record still exists with PARTIAL status rather than the database
    containing orphaned option rows with no parent anchor. Every failure mode is
    auditable; no silent data corruption is possible.

    Call finalize_snapshot() after all child rows are committed.
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
    """
    Seal a snapshot after all child rows are committed.
    status: 'COMPLETE' if all rows written cleanly; 'PARTIAL' or 'FAILED' otherwise.
    collection_latency_ms: wall-clock time from first API call to DB commit.
    """
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
    Either all rows commit or none do — no half-written snapshots.
    Returns the number of rows inserted.

    Each dict must contain these keys:
        snapshot_id, expiry_date, dte, strike, right,
        bid, ask, mark, last,
        iv, delta, gamma, theta, vega,
        volume, open_interest, intrinsic_value, time_value
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

    Each dict must contain:
        snapshot_id, expiry_date, dte, atm_strike,
        atm_call_iv, atm_put_iv, atm_avg_iv,
        iv_spread_to_front, iv_ratio_to_front
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
    """
    Write a collection gap record.
    Called by collector.py on startup (if a gap is detected since the last
    snapshot) and during cycle monitoring (if consecutive snapshots are further
    apart than expected).

    reason values: 'COLLECTOR_OFFLINE', 'API_ERROR', 'MARKET_CLOSED',
                   'HOLIDAY', 'UNKNOWN'
    """
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
# NEW — Read Operations  (app.py — future refactored version)
# These replace the LEGACY read functions once app.py is updated.
# ─────────────────────────────────────────────────────────────────────────────

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
    """
    Snapshots between start and end (UTC ISO8601 strings) with given status.
    Default status='COMPLETE' — all analytics must filter to verified data only.
    Partial and failed snapshots exist for auditability, not analysis.
    """
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
    Enables complete chain reconstruction at any historical timestamp.
    Results ordered by expiry_date, strike, right for consistent display.
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

    This is the query that drives the "selected strike IV" chart — showing
    the IV of the actual contracts you'd trade, not the floating ATM proxy.

    Performance: uses idx_option_rows_contract_snap (covering index).
    At 40M+ rows/year, this is a fast indexed scan, not a table scan.
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
    Primary query for term structure charts and IV percentile input.

    Performance: uses idx_atm_iv_expiry_snap. Scans ~3,150 rows per 30 days
    rather than scanning option_rows directly (which would be ~4.8M rows).
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
    """
    All expiries for a given snapshot, ordered by DTE ascending.
    Used to render the IV term structure curve at a single point in time.
    """
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

    Primary input to the 180-day IV spread percentile calculation:
      front_iv      → ATM IV of the front expiry
      back_iv       → ATM IV of the back expiry
      iv_spread     → back_iv - front_iv  (absolute term structure spread)
      iv_ratio      → back_iv / front_iv  (ratio; handles inversions better
                      than absolute spread alone)

    Example use: "current iv_spread is at the 88th percentile of the
    last 180 days of iv_spread values" = strong entry signal.
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
    """
    Collection gaps within a date range.

    Pass exclude_reasons=['MARKET_CLOSED', 'HOLIDAY'] to suppress expected
    gaps so the dashboard only surfaces unintentional data losses.
    """
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


def update_snapshot_notes(db_path: str, snapshot_id: int, notes: str) -> None:
    """
    Update the notes field on a snapshot record.

    This is the ONLY write operation permitted from app.py on the new schema.
    All other new-schema writes are collector.py's exclusive domain.
    The narrow scope (one field, keyed by snapshot_id) makes this safe
    to allow from app.py without risking write conflicts with the collector.
    """
    with managed_conn(db_path) as conn:
        conn.execute(
            "UPDATE snapshots SET notes = ? WHERE snapshot_id = ?",
            (notes, snapshot_id)
        )
