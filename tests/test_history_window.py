"""History windows are measured from the record, not from the wall clock.

WHAT WENT WRONG (Chandan, 2026-09-05). The Calendar Edge chart on "Today"
drew only the last few minutes of the session and left the whole morning
blank, while the same morning appeared correctly on the 5D view.

The four history reads filtered on `datetime('now', '-N days', 'utc')` — a
window ending NOW rather than at the newest thing in the database. The record
ended at 2026-09-04 20:01 UTC and the question was asked at 2026-09-05 19:45
UTC, so the one-day window began at 2026-09-04 19:45 and cut off everything
before 15:45 ET. Measured on the real record at the time: "Today" returned 0
snapshots, and "5D" returned four sessions instead of five.

The failure is silent and it MOVES: the same chart shows less of the morning
every hour you leave it open, and shows everything correctly the moment a new
snapshot lands. That is what makes it worth pinning here rather than trusting
a one-off look.

Every test below therefore builds a record whose newest snapshot is days in
the PAST, which is the state that exposes it — a weekend, a holiday, or a
collector that has been stopped.
"""
from __future__ import annotations

import sqlite3

import pytest

import db

EXPIRY = "2026-09-18"

# Deliberately stale. Against a wall-clock window nothing here is reachable.
SESSIONS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
OPEN, CLOSE = "13:30:00", "20:01:00"


def _record(path: str) -> None:
    """One session per day, each with an open, a midday and a close row."""
    conn = sqlite3.connect(path)
    try:
        snap = 0
        for day in SESSIONS:
            for clock in (OPEN, "17:00:00", CLOSE):
                snap += 1
                conn.execute(
                    "INSERT INTO snapshots (snapshot_id, snapshot_timestamp, "
                    "status, underlying_price) VALUES (?, ?, 'COMPLETE', 7700.0)",
                    (snap, f"{day} {clock}"),
                )
                conn.execute(
                    # settlement='PM' because contract.match_clause treats a
                    # NULL settlement as "same-day expiry only" (BUG-028);
                    # without it these rows are filtered out for reasons that
                    # have nothing to do with the window under test.
                    "INSERT INTO atm_iv_by_expiry (snapshot_id, expiry_date, "
                    "settlement, dte, atm_strike, atm_call_iv, atm_put_iv, "
                    "atm_avg_iv) "
                    "VALUES (?, ?, 'PM', 7, 7700.0, 0.18, 0.18, 0.18)",
                    (snap, EXPIRY),
                )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def stale_db(temp_db) -> str:
    _record(temp_db)
    return temp_db


def test_one_day_returns_the_whole_final_session(stale_db):
    """The reported bug. The morning is in the database; it must be in the
    answer, however long ago the collector last wrote."""
    rows = db.get_atm_iv_history(stale_db, EXPIRY, days=1)
    stamps = [r["snapshot_timestamp"] for r in rows]
    final = [s for s in stamps if s.startswith(SESSIONS[-1])]

    assert final, "the most recent session is missing entirely"
    assert final[0] == f"{SESSIONS[-1]} {OPEN}", (
        "the session must start at the open, not partway through the afternoon"
    )
    assert final[-1] == f"{SESSIONS[-1]} {CLOSE}"


def test_a_window_is_not_emptied_by_the_passage_of_time(stale_db):
    """The sharpest statement of it. Under the old wall-clock window this
    record — every row of it days old — returned nothing at all."""
    assert db.get_atm_iv_history(stale_db, EXPIRY, days=1) != []


def test_five_days_covers_five_sessions(stale_db):
    """The quieter half of the same bug: a window ending now begins partway
    through the oldest session and silently drops it. Five days drew four."""
    rows = db.get_atm_iv_history(stale_db, EXPIRY, days=5)
    sessions = sorted({r["snapshot_timestamp"][:10] for r in rows})

    assert sessions == SESSIONS


def test_the_window_is_measured_from_the_newest_snapshot(stale_db):
    """What "anchored to the record" means, stated directly: adding a newer
    snapshot moves the window forward and drops the oldest session."""
    before = {r["snapshot_timestamp"][:10]
              for r in db.get_atm_iv_history(stale_db, EXPIRY, days=5)}

    conn = sqlite3.connect(stale_db)
    try:
        conn.execute(
            "INSERT INTO snapshots (snapshot_id, snapshot_timestamp, status, "
            "underlying_price) VALUES (999, '2026-01-12 13:30:00', "
            "'COMPLETE', 7700.0)")
        conn.execute(
            "INSERT INTO atm_iv_by_expiry (snapshot_id, expiry_date, "
            "settlement, dte, atm_strike, atm_call_iv, atm_put_iv, atm_avg_iv) "
            "VALUES (999, ?, 'PM', 7, 7700.0, 0.18, 0.18, 0.18)", (EXPIRY,))
        conn.commit()
    finally:
        conn.close()

    after = {r["snapshot_timestamp"][:10]
             for r in db.get_atm_iv_history(stale_db, EXPIRY, days=5)}

    assert "2026-01-12" in after, "the new session must be inside the window"
    assert SESSIONS[0] in before and SESSIONS[0] not in after, (
        "the window must move with the record, not merely grow"
    )


def test_an_incomplete_newest_snapshot_does_not_move_the_anchor(stale_db):
    """A snapshot still being written is not the end of the record. Anchoring
    to it would push the window past the last COMPLETE data and could empty a
    chart mid-collection — the same silent blanking, on a one-minute cycle."""
    conn = sqlite3.connect(stale_db)
    try:
        conn.execute(
            "INSERT INTO snapshots (snapshot_id, snapshot_timestamp, status, "
            "underlying_price) VALUES (998, '2026-02-20 13:30:00', "
            "'PARTIAL', 7700.0)")
        conn.commit()
    finally:
        conn.close()

    rows = db.get_atm_iv_history(stale_db, EXPIRY, days=1)

    assert [r["snapshot_timestamp"] for r in rows if
            r["snapshot_timestamp"].startswith(SESSIONS[-1])], (
        "an in-progress snapshot must not drag the window away from the data"
    )


def test_every_history_read_shares_the_window():
    """Four reads had this bug and all four were fixed. A fifth added later
    that writes its own `datetime('now', ...)` would reintroduce it silently."""
    from pathlib import Path
    source = Path(db.__file__).read_text(encoding="utf-8")

    code = [ln for ln in source.splitlines()
            if not ln.strip().startswith("#")]
    offenders = [ln.strip() for ln in code if "datetime('now'" in ln]

    assert offenders == [], (
        "a history window measured from the wall clock will blank a chart as "
        f"the day goes on: {offenders}"
    )
    assert sum("{_WINDOW_START}" in ln for ln in code) == 4
