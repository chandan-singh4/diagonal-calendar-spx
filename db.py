"""
db.py — SQLite storage for IV history.

Schema design note (v2): snapshots are stored PER EXPIRY, not per front/back pair.
Every poll captures the full chain, so every expiry visible that poll shares the
same timestamp. That means we can reconstruct the front/back ratio for ANY pair
of expiries after the fact by joining two expiries' history on matching timestamps,
rather than locking ourselves into whatever pair was selected at poll time.

Schema design note (v3): added strike_snapshots table for per-strike IV tracking.
When specific call/put strikes are entered in the dashboard, their exact IV (not
just ATM approximation) is recorded each poll. This is what drives the top
"selected strike" chart — showing the IV of the actual contracts you'd trade,
not the floating ATM proxy.

Two separate db files are supported: the real one (config.DB_PATH) and a demo one
(config.DEMO_DB_PATH) so synthetic preview data never mixes with real collected data.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS expiry_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    underlying_price REAL NOT NULL,
    expiry TEXT NOT NULL,
    dte INTEGER,
    atm_iv REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expiry_snapshots_expiry_ts ON expiry_snapshots(expiry, timestamp);

CREATE TABLE IF NOT EXISTS strike_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    underlying_price REAL NOT NULL,
    expiry TEXT NOT NULL,
    strike REAL NOT NULL,
    side TEXT NOT NULL,        -- 'CALL' or 'PUT'
    iv REAL,
    bid REAL,
    ask REAL,
    volume INTEGER,
    open_interest INTEGER
);

CREATE INDEX IF NOT EXISTS idx_strike_snapshots ON strike_snapshots(expiry, strike, side, timestamp);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    front_expiry TEXT NOT NULL,
    back_expiry TEXT NOT NULL,
    call_strike REAL NOT NULL,
    put_strike REAL NOT NULL,
    entry_debit REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    transformed_at TEXT,
    transform_notes TEXT
);
"""


@contextmanager
def get_conn(db_path: str = None):
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = None):
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)


def save_expiry_snapshot(underlying_price, expiry, dte, atm_iv, db_path: str = None,
                          timestamp: str = None):
    """timestamp param exists so a single poll can write matching timestamps for
    multiple expiries — pass the same value for all calls in the same poll cycle."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO expiry_snapshots (timestamp, underlying_price, expiry, dte, atm_iv) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, underlying_price, expiry, dte, atm_iv),
        )
    return ts


def get_expiry_history(expiry: str, since_iso: str = None, db_path: str = None, limit: int = 20000):
    query = "SELECT * FROM expiry_snapshots WHERE expiry = ?"
    params = [expiry]
    if since_iso:
        query += " AND timestamp >= ?"
        params.append(since_iso)
    query += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        return conn.execute(query, params).fetchall()


def get_latest_two_snapshots(expiry: str, db_path: str = None):
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM expiry_snapshots WHERE expiry = ? ORDER BY timestamp DESC LIMIT 2",
            (expiry,),
        ).fetchall()


def has_any_data(db_path: str = None) -> bool:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as n FROM expiry_snapshots").fetchone()
        return row["n"] > 0


def save_strike_snapshot(underlying_price, expiry, strike, side, iv, bid, ask,
                          volume, open_interest, db_path: str = None, timestamp: str = None):
    """Records IV for a specific strike each poll. Call once per leg
    (front_call, front_put, back_call, back_put) passing the same timestamp."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO strike_snapshots
               (timestamp, underlying_price, expiry, strike, side, iv, bid, ask, volume, open_interest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, underlying_price, expiry, strike, side, iv, bid, ask, volume, open_interest),
        )
    return ts


def get_strike_history(expiry: str, strike: float, side: str, since_iso: str = None,
                        db_path: str = None, limit: int = 20000):
    query = "SELECT * FROM strike_snapshots WHERE expiry = ? AND strike = ? AND side = ?"
    params = [expiry, strike, side]
    if since_iso:
        query += " AND timestamp >= ?"
        params.append(since_iso)
    query += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        return conn.execute(query, params).fetchall()


def save_position(front_expiry, back_expiry, call_strike, put_strike, entry_debit, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO positions (opened_at, front_expiry, back_expiry, call_strike,
               put_strike, entry_debit) VALUES (?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), front_expiry, back_expiry,
             call_strike, put_strike, entry_debit),
        )


def get_open_positions(db_path: str = None):
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM positions WHERE status = 'open'").fetchall()

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS expiry_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,           -- ISO 8601 UTC. Shared across all expiries from the same poll.
    underlying_price REAL NOT NULL,
    expiry TEXT NOT NULL,
    dte INTEGER,
    atm_iv REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expiry_snapshots_expiry_ts ON expiry_snapshots(expiry, timestamp);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    front_expiry TEXT NOT NULL,
    back_expiry TEXT NOT NULL,
    call_strike REAL NOT NULL,
    put_strike REAL NOT NULL,
    entry_debit REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'transformed' | 'closed'
    transformed_at TEXT,
    transform_notes TEXT
);
"""


@contextmanager
def get_conn(db_path: str = None):
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = None):
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)


def save_expiry_snapshot(underlying_price, expiry, dte, atm_iv, db_path: str = None,
                          timestamp: str = None):
    """timestamp param exists so a single poll can write matching timestamps for
    multiple expiries (front + back) — pass the same value for both calls."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO expiry_snapshots (timestamp, underlying_price, expiry, dte, atm_iv) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, underlying_price, expiry, dte, atm_iv),
        )
    return ts


def get_expiry_history(expiry: str, since_iso: str = None, db_path: str = None, limit: int = 20000):
    query = "SELECT * FROM expiry_snapshots WHERE expiry = ?"
    params = [expiry]
    if since_iso:
        query += " AND timestamp >= ?"
        params.append(since_iso)
    query += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        return conn.execute(query, params).fetchall()


def get_latest_two_snapshots(expiry: str, db_path: str = None):
    """Used for day-change % in the expirations list panel."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM expiry_snapshots WHERE expiry = ? ORDER BY timestamp DESC LIMIT 2",
            (expiry,),
        ).fetchall()


def has_any_data(db_path: str = None) -> bool:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as n FROM expiry_snapshots").fetchone()
        return row["n"] > 0


def save_position(front_expiry, back_expiry, call_strike, put_strike, entry_debit, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO positions (opened_at, front_expiry, back_expiry, call_strike,
               put_strike, entry_debit) VALUES (?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), front_expiry, back_expiry,
             call_strike, put_strike, entry_debit),
        )


def get_open_positions(db_path: str = None):
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM positions WHERE status = 'open'").fetchall()
