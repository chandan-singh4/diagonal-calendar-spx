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
    with managed_conn(db_path) as conn:
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
    with managed_conn(db_path) as conn:
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
        return float(row["underlying_price"]) if row and row["underlying_price"] else None



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
    """
    bound = session_date if session_date else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with managed_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT snapshot_timestamp, underlying_price
            FROM snapshots
            WHERE status              = 'COMPLETE'
              AND snapshot_timestamp >= ?
            ORDER BY snapshot_timestamp
            """,
            (bound,)
        ).fetchall()


def get_all_expiry_atm_iv_today(db_path: str, session_date: str | None = None) -> list:
    """
    ATM IV for every expiry at every COMPLETE snapshot on the given session date.

    Used by app.py's Pair Scanner to compute intraday IV ratios for all
    possible (front, back) expiry combinations.

    session_date: 'YYYY-MM-DD' string in UTC. App derives this from the latest
    snapshot's own timestamp so the scanner always shows the most recent session's
    data rather than 0 rows when called after-hours or pre-open (when no snapshots
    exist for the current UTC calendar day yet).

    Returns rows with:
      snapshot_timestamp  TEXT  — UTC 'YYYY-MM-DD HH:MM:SS'
      expiry_date         TEXT  — 'YYYY-MM-DD'
      dte                 INT   — days to expiration at snapshot time
      atm_avg_iv          REAL  — decimal form (0.18 = 18%); caller × 100

    Performance: uses idx_atm_iv_expiry_snap; at 5-min polling across
    20 expirations for 6.5 market hours ≈ 1,560 rows/day — trivially fast.
    """
    bound = session_date if session_date else datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
              AND s.snapshot_timestamp >= ?
            ORDER BY s.snapshot_timestamp, a.expiry_date
            """,
            (bound,)
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

"""
APPEND THIS ENTIRE FILE TO THE BOTTOM OF db.py
───────────────────────────────────────────────
Adds the trades table, all CRUD operations, live IC mark-price queries,
and the T-001 seed record.  All new functions follow the same conventions
as the rest of db.py (managed_conn, _utcnow, logger).
"""

import json as _json  # local alias — only used in seed_t001


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
    logger.info("Trades table verified at %s", db_path)


# ─────────────────────────────────────────────────────────────────────────────
# Write Operations (journal.py only)
# ─────────────────────────────────────────────────────────────────────────────

def get_next_trade_id(db_path: str) -> str:
    """Return the next sequential trade ID string, e.g. 'T-004'."""
    with managed_conn(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()
        return f"T-{row['n'] + 1:03d}"


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


def delete_trade(db_path: str, trade_id: str) -> None:
    """Permanently removes a trade record by trade_id.
    Called only from pages/journal.py after explicit user confirmation.
    No cascade needed — trades have no child rows in other tables.
    """
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM trades WHERE trade_id = ?", (trade_id,))


# ─────────────────────────────────────────────────────────────────────────────
# Read Operations
# ─────────────────────────────────────────────────────────────────────────────

def get_all_trades(db_path: str) -> list:
    """All trades, newest entry date first."""
    with managed_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM trades ORDER BY entry_date DESC, entry_time DESC"
        ).fetchall()


def get_trade(db_path: str, trade_id: str) -> "sqlite3.Row | None":
    """Single trade by ID. Returns None if not found."""
    with managed_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()


def get_eod_spx(db_path: str, date_str: str) -> "float | None":
    """
    Last COMPLETE snapshot underlying_price on or before date_str (YYYY-MM-DD).
    Used by journal.py to auto-suggest SPX close when recording expiration.
    """
    with managed_conn(db_path) as conn:
        row = conn.execute("""
            SELECT underlying_price FROM snapshots
            WHERE status             = 'COMPLETE'
              AND snapshot_timestamp <= ?
            ORDER BY snapshot_timestamp DESC
            LIMIT 1
        """, (date_str + " 23:59:59",)).fetchone()
        return float(row["underlying_price"]) if row and row["underlying_price"] else None


def get_ic_marks(
    db_path: str,
    ic_expiry_date: str,
    short_call: float,
    long_call: float,
    short_put: float,
    long_put: float,
    eod_date: "str | None" = None,
) -> "dict | None":
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
    with managed_conn(db_path) as conn:
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
            SELECT strike, right, bid, ask, mark
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

        leg_map = {}
        for r in rows:
            leg_map[(float(r["strike"]), r["right"])] = {
                "bid":  r["bid"]  or 0.0,
                "ask":  r["ask"]  or 0.0,
                "mark": r["mark"] or 0.0,
            }

        sc = leg_map.get((float(short_call), "C"))
        lc = leg_map.get((float(long_call),  "C"))
        sp = leg_map.get((float(short_put),  "P"))
        lp = leg_map.get((float(long_put),   "P"))

        if not all([sc, lc, sp, lp]):
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
        initial = _json.dumps([
            {"expiry": "2026-06-30", "type": "Call", "action": "Sell to Open", "strike": 7380, "fill": 24.10},
            {"expiry": "2026-06-30", "type": "Put",  "action": "Sell to Open", "strike": 7320, "fill": 66.65},
            {"expiry": "2026-07-02", "type": "Call", "action": "Buy to Open",  "strike": 7400, "fill": 32.95},
            {"expiry": "2026-07-02", "type": "Put",  "action": "Buy to Open",  "strike": 7300, "fill": 70.70},
        ])
        transform = _json.dumps([
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
