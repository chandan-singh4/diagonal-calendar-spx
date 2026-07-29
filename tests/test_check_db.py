"""
check_db.py — the health-check script run at the start of every session.

BUG-018: the report is drawn with box characters (═ ─ →). When stdout is a
console Python encodes through the Windows console API and those are fine, but
when stdout is REDIRECTED OR PIPED Python falls back to the locale encoding —
cp1252 on this machine — which cannot represent them. The script then died with
UnicodeEncodeError before printing a single line.

That is why these tests run the script in a SUBPROCESS with PYTHONIOENCODING
forced to cp1252. Calling main() in-process would not reproduce it: pytest
replaces sys.stdout with its own UTF-8 capture object, so the encoding that
actually breaks is never in play. The bug lives in the interaction between the
process and its stdout, so the test has to own a real process.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# check_db.py lives in scripts/, which is not a package and is not on the path
# that conftest.py sets up (that one covers the flat modules at the repo root).
# This must run before the check_db import below.
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_db  # noqa: E402  — requires the sys.path line above

# ─────────────────────────────────────────────────────────────────────────────
# Driving the real entry point
# ─────────────────────────────────────────────────────────────────────────────

# Runs check_db.main() against a throwaway database. config.DB_PATH is
# reassigned BEFORE check_db is imported and main() reads it at call time, so
# the production 1.4 GB file is never opened (conftest.py scope rule).
DRIVER = """
import sys
sys.path.insert(0, {repo!r})
sys.path.insert(0, {scripts!r})

import config
config.DB_PATH = {db!r}

import db
db.init_db({db!r})

import check_db
{clock_patch}
check_db.main()
"""

# Pins the child's idea of "now" so the ET-day count is decidable rather than
# depending on the wall clock when the suite runs. Patched from the test side
# only — check_db carries no test-awareness of its own; the `now` argument is a
# seam that exists because the boundary is worth testing at all.
CLOCK_PATCH = """
import datetime as _dt
_fixed = _dt.datetime.fromisoformat({now_iso!r})
_orig = check_db._et_trading_day_bounds
check_db._et_trading_day_bounds = lambda now=None: _orig(_fixed)
"""


def _run_check_db(
    db_path: str, *, io_encoding: str, now_iso: str | None = None
) -> subprocess.CompletedProcess:
    """Run check_db.main() in a subprocess whose stdout uses `io_encoding`.

    Output is captured as raw bytes and decoded here, so a crash inside the
    child surfaces as a non-zero returncode plus its traceback rather than as a
    decoding error in the test itself.

    `now_iso` freezes the child's clock so tests about the day boundary do not
    depend on when the suite happens to run.
    """
    clock_patch = "" if now_iso is None else CLOCK_PATCH.format(now_iso=now_iso)
    env = {
        **_base_env(),
        "PYTHONIOENCODING": io_encoding,
        # UTF-8 mode would override PYTHONIOENCODING and mask the very fault
        # under test, so pin it off regardless of how the parent was launched.
        "PYTHONUTF8": "0",
    }
    return subprocess.run(
        [
            sys.executable,
            "-c",
            DRIVER.format(
                repo=str(REPO_ROOT),
                scripts=str(REPO_ROOT / "scripts"),
                db=db_path,
                clock_patch=clock_patch,
            ),
        ],
        capture_output=True,
        env=env,
        cwd=str(REPO_ROOT),
        # A non-zero exit IS the failure under test, so it must come back as a
        # result to assert on, not as a CalledProcessError raised here.
        check=False,
    )


def _base_env() -> dict:
    # Copy the real environment (PATH, SystemRoot — Windows needs both) but drop
    # any inherited encoding settings so each test states its own.
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    return env


@pytest.mark.integration
def test_report_survives_a_cp1252_stdout(temp_db):
    """The regression. Before the fix this died with UnicodeEncodeError.

    cp1252 is what Python picks when output is piped on this machine, which is
    every redirected run — Git Bash, `> out.txt`, or a wrapper capturing the
    output. The script must print its report, not a traceback.
    """
    result = _run_check_db(temp_db, io_encoding="cp1252")

    assert result.returncode == 0, (
        "check_db crashed under a cp1252 stdout:\n"
        + result.stderr.decode("utf-8", errors="replace")
    )
    assert b"UnicodeEncodeError" not in result.stderr

    text = result.stdout.decode("utf-8", errors="replace")
    assert "DATABASE HEALTH CHECK" in text
    # The box rule survived intact rather than being dropped or mangled — the
    # fix must widen what stdout can carry, not strip the characters.
    assert "═" in text


@pytest.mark.integration
def test_report_still_works_on_a_utf8_stdout(temp_db):
    """The fix must not break the case that already worked (PowerShell here)."""
    result = _run_check_db(temp_db, io_encoding="utf-8")

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    text = result.stdout.decode("utf-8", errors="replace")
    assert "DATABASE HEALTH CHECK" in text
    assert "═" in text


def _insert_snapshot(db_path: str, timestamp_utc: str, status: str = "COMPLETE"):
    """Add one snapshot row at an exact stored (UTC) timestamp."""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO snapshots (snapshot_timestamp, status, underlying_price, "
            "strikes_fetched, expiries_fetched, collection_latency_ms) "
            "VALUES (?, ?, 6000.0, 3200, 20, 1000)",
            (timestamp_utc, status),
        )
    conn.close()


@pytest.mark.integration
def test_evening_snapshots_still_count_as_today(temp_db):
    """BUG-019 end to end: the report counts the ET day, not the UTC day.

    The clock is frozen at 21:00 ET on 28 July — after UTC has already rolled
    into the 29th. All three snapshots belong to the ET day of the 28th, and
    two of them are stored with a UTC date of the 29th. The old UTC-day count
    reported 1 here; the ET-day count must report 3.
    """
    _insert_snapshot(temp_db, "2026-07-28 14:30:00")  # 10:30 ET, market open
    _insert_snapshot(temp_db, "2026-07-29 00:30:00")  # 20:30 ET, same ET day
    _insert_snapshot(temp_db, "2026-07-29 00:50:00")  # 20:50 ET, same ET day

    result = _run_check_db(
        temp_db, io_encoding="cp1252", now_iso="2026-07-29T01:00:00+00:00"
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    text = result.stdout.decode("utf-8", errors="replace")
    assert "Snapshots today    : 3" in text
    # The date is stated, so the boundary being used is never left to guess.
    assert "2026-07-28 ET" in text


@pytest.mark.integration
def test_the_previous_et_day_is_excluded(temp_db):
    """The window is half-open: yesterday evening must not leak into today.

    The mirror of the test above, and the half that a naive "just use local
    time" fix would still get wrong. 23:00 ET on the 27th is stored as the 28th
    in UTC, so a UTC-day count would have included it in the 28th's total.
    """
    _insert_snapshot(temp_db, "2026-07-28 03:00:00")  # 23:00 ET on the 27th
    _insert_snapshot(temp_db, "2026-07-28 14:30:00")  # 10:30 ET on the 28th

    result = _run_check_db(
        temp_db, io_encoding="cp1252", now_iso="2026-07-28T18:00:00+00:00"
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    text = result.stdout.decode("utf-8", errors="replace")
    assert "Snapshots today    : 1" in text
    assert "2026-07-28 ET" in text


@pytest.mark.integration
def test_only_complete_snapshots_count_as_today(temp_db):
    """The status filter must survive the rewritten query.

    A FAILED snapshot is a collection attempt that stored nothing. Counting it
    as one of today's would overstate the health of exactly the thing this line
    exists to report on.
    """
    _insert_snapshot(temp_db, "2026-07-28 14:30:00", status="COMPLETE")
    _insert_snapshot(temp_db, "2026-07-28 14:31:00", status="FAILED")
    _insert_snapshot(temp_db, "2026-07-28 14:32:00", status="PARTIAL")

    result = _run_check_db(
        temp_db, io_encoding="cp1252", now_iso="2026-07-28T18:00:00+00:00"
    )

    text = result.stdout.decode("utf-8", errors="replace")
    assert "Snapshots today    : 1" in text


@pytest.mark.integration
def test_empty_database_reports_no_snapshots(temp_db):
    """An empty database is a legitimate state, not an error.

    Worth pinning because it is what a genuinely broken collector looks like:
    the script must say so plainly instead of crashing on the missing rows.
    """
    result = _run_check_db(temp_db, io_encoding="cp1252")

    assert result.returncode == 0
    text = result.stdout.decode("utf-8", errors="replace")
    assert "No snapshots found" in text


# ─────────────────────────────────────────────────────────────────────────────
# The stream fix in isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_force_utf8_stdout_tolerates_a_stream_it_cannot_reconfigure():
    """A stream with no reconfigure() must not take the health check down.

    Test harnesses and log capturers substitute their own stdout objects, and
    plenty of them are not TextIOWrapper. Failing to widen the encoding is a
    cosmetic problem; raising here would stop the script printing anything.
    """

    class Unreconfigurable:
        """No reconfigure attribute — like pytest's own capture object."""

    original_out, original_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = Unreconfigurable()
    try:
        check_db._force_utf8_stdout()  # must not raise
    finally:
        sys.stdout, sys.stderr = original_out, original_err


def test_et_trading_day_puts_an_evening_snapshot_on_the_right_day():
    """BUG-019, the regression. 8:30pm ET belongs to that ET day, not the next.

    UTC rolls over at 8pm ET during daylight time. The old count compared
    against SQLite's DATE('now') — also UTC — so from 8pm onwards it reported 0
    snapshots today while the collector was running perfectly. This is the top
    line of the session-start health check, and a 0 there reads as a dead
    collector.

    2026-07-29 00:30 UTC is 2026-07-28 20:30 ET. The day must be the 28th.
    """
    now = dt.datetime(2026, 7, 29, 0, 30, tzinfo=dt.UTC)
    start_utc, end_utc, et_date = check_db._et_trading_day_bounds(now)

    assert et_date == "2026-07-28"
    # Midnight ET on the 28th and 29th, expressed in UTC (EDT = UTC-4).
    assert start_utc == "2026-07-28 04:00:00"
    assert end_utc == "2026-07-29 04:00:00"
    # The instant itself must fall inside its own day's window.
    assert start_utc <= "2026-07-29 00:30:00" < end_utc


def test_et_trading_day_window_is_23_hours_when_the_clocks_spring_forward():
    """DST, spring: 2026-03-08 is a 23-hour day in New York.

    Worth pinning because the obvious implementation — take midnight and add 24
    hours — is wrong exactly twice a year, and wrong quietly: it would drag an
    hour of the next day into the count.
    """
    now = dt.datetime(2026, 3, 8, 18, 0, tzinfo=dt.UTC)  # 13:00 EDT
    start_utc, end_utc, et_date = check_db._et_trading_day_bounds(now)

    assert et_date == "2026-03-08"
    # Starts on EST (UTC-5), ends on EDT (UTC-4) — so 23 hours, not 24.
    assert start_utc == "2026-03-08 05:00:00"
    assert end_utc == "2026-03-09 04:00:00"
    assert _hours_between(start_utc, end_utc) == 23


def test_et_trading_day_window_is_25_hours_when_the_clocks_fall_back():
    """DST, autumn: 2026-11-01 is a 25-hour day in New York."""
    now = dt.datetime(2026, 11, 1, 16, 0, tzinfo=dt.UTC)  # 11:00 EST
    start_utc, end_utc, et_date = check_db._et_trading_day_bounds(now)

    assert et_date == "2026-11-01"
    assert start_utc == "2026-11-01 04:00:00"  # still EDT
    assert end_utc == "2026-11-02 05:00:00"    # now EST
    assert _hours_between(start_utc, end_utc) == 25


def test_et_trading_day_defaults_to_now(monkeypatch):
    """Called with no argument it must use the real clock, not a fixed date."""
    start_utc, end_utc, et_date = check_db._et_trading_day_bounds()

    expected = dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    assert et_date == expected
    assert start_utc < end_utc


def _hours_between(start: str, end: str) -> float:
    fmt = "%Y-%m-%d %H:%M:%S"
    delta = dt.datetime.strptime(end, fmt) - dt.datetime.strptime(start, fmt)
    return delta.total_seconds() / 3600


def test_force_utf8_stdout_sets_utf8_on_a_real_stream(tmp_path):
    """The function does what it claims on a stream that can be reconfigured."""
    target = tmp_path / "out.txt"
    original_out, original_err = sys.stdout, sys.stderr
    with open(target, "w", encoding="cp1252") as handle:
        sys.stdout = sys.stderr = handle
        try:
            check_db._force_utf8_stdout()
            assert handle.encoding.lower().replace("-", "") == "utf8"
        finally:
            sys.stdout, sys.stderr = original_out, original_err
