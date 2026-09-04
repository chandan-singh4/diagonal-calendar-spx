"""The audit asks whether the RECORD is complete, not whether the code works.

WHY IT IS TESTED AT ALL, given it only reads. Because a check that quietly
finds nothing is indistinguishable from a record with nothing wrong, and this
project has now been bitten three times by exactly that shape of silence
(ADR-046, ADR-048, ADR-049). An audit nobody has seen fail is an assumption.

So every check below is proved in BOTH directions: it fires on a record with
the fault, and it stays quiet on one without. The negative half is the half
that matters — a check that always fires gets ignored within a week.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import audit

import config
import db
from core import session as core_session

# 15 July 2026 is a Wednesday; 17 July 2026 is the third Friday of that month.
WED = "2026-07-15"
THIRD_FRIDAY = "2026-07-17"


def _snap(path: str, et_date: str, et_time: str, session: str = "MIDDAY") -> int:
    """One COMPLETE snapshot at the given EASTERN wall-clock time.

    Stored in UTC, as the collector does. July is UTC-4, and the audit converts
    back with the same offset, so the two agree by construction.
    """
    hh, mm, ss = (int(p) for p in et_time.split(":"))
    y, mo, d = (int(p) for p in et_date.split("-"))
    utc = datetime(y, mo, d, hh, mm, ss, tzinfo=UTC) + timedelta(hours=4)
    sid = db.create_snapshot(path, utc.strftime("%Y-%m-%d %H:%M:%S"), session, 300,
                             underlying_price=6000.0, underlying_bid=5999.0,
                             underlying_ask=6001.0, vix_value=15.0)
    db.finalize_snapshot(path, sid, "COMPLETE", 10, 2, 500)
    return sid


def _iv_row(path: str, sid: int, expiry: str, settlement, iv: float) -> None:
    with sqlite3.connect(path) as c:
        c.execute(
            """insert into atm_iv_by_expiry
               (snapshot_id, expiry_date, dte, atm_strike, atm_call_iv,
                atm_put_iv, atm_avg_iv, settlement)
               values (?,?,?,?,?,?,?,?)""",
            (sid, expiry, 20, 6000.0, iv, iv, iv, settlement))


def _full_day(path: str, et_date: str, count: int, last: str = "16:01:00") -> None:
    """`count` snapshots on a day, the last of them at `last` ET."""
    for i in range(count - 1):
        _snap(path, et_date, f"10:{i // 60:02d}:{i % 60:02d}")
    _snap(path, et_date, last)


def _findings(check, path, since=None):
    conn = audit.connect(path)
    try:
        return check(conn, since)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Read-only by construction — the guarantee the docstring makes
# ─────────────────────────────────────────────────────────────────────────────

def test_the_audit_physically_cannot_write_to_the_record(temp_db):
    """Not a convention to remember. This runs against the one irreplaceable
    file in the project, so the connection itself must refuse."""
    conn = audit.connect(temp_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("delete from snapshots")
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# What a day should contain, derived rather than restated
# ─────────────────────────────────────────────────────────────────────────────

def test_the_daily_expectation_is_derived_from_the_session_rules():
    """128 = 30 one-minute polls at the open + 66 five-minute + 32 at the close.
    Written as a derivation, not a number, so ADR-049's window change carried
    through with no edit here."""
    assert audit.expected_snapshots_per_day() == 128


def test_the_expectation_follows_the_window_rather_than_a_constant(monkeypatch):
    """The point of deriving it. Shrink the window and the expectation shrinks;
    a hardcoded 128 would have silently gone stale on 2026-09-03."""
    monkeypatch.setattr(core_session, "CLOSE_END", clock_time(16, 0))
    assert audit.expected_snapshots_per_day() == 126


# ─────────────────────────────────────────────────────────────────────────────
# The closing price (ADR-049) — the check that would have caught it on day two
# ─────────────────────────────────────────────────────────────────────────────

def test_a_day_that_stops_at_1559_is_reported(temp_db):
    _snap(temp_db, WED, "15:59:53")
    found = _findings(audit.check_closing_price, temp_db)
    assert len(found) == 1
    assert "no price at or after 16:00" in found[0].summary


def test_a_day_that_reaches_the_bell_is_not_reported(temp_db):
    """The half that matters. 16:00:05 is enough — the check asks for a price
    at or after the bell, not for a perfect one."""
    _snap(temp_db, WED, "16:00:05")
    assert _findings(audit.check_closing_price, temp_db) == []


def test_missing_closes_before_the_fix_are_history_and_after_it_are_faults(
        temp_db, monkeypatch):
    """Severity carries the judgement so Chandan does not have to remember the
    date. Everything before 2026-09-03 is the known ten-week hole.

    The clock is frozen well past both dates because this test is about
    SEVERITY, not about the day in progress. Unfrozen it passed on every date
    except 2026-09-04, when its own fixture date was today and the new
    in-progress exclusion correctly withheld it -- a test that fails on one
    calendar day a year is a trap for whoever is on shift that day."""
    _freeze(monkeypatch, "2026-09-10", "12:00")
    _snap(temp_db, "2026-08-10", "15:59:00")
    assert _findings(audit.check_closing_price, temp_db)[0].severity == "note"

    _snap(temp_db, "2026-09-04", "15:59:00")
    found = _findings(audit.check_closing_price, temp_db)[0]
    assert found.severity == "alarm"
    assert "new faults" in found.detail


# ─────────────────────────────────────────────────────────────────────────────
def _freeze(monkeypatch, et_date: str, et_time: str) -> None:
    """Pin `datetime.now(ET)` inside audit.py to a wall-clock Eastern moment.

    The close check has to know whether today's bell has rung yet, so the
    tests below are about the clock and nothing else. Patching the module's
    `datetime` name is the smallest seam that does it without the audit
    growing a parameter only tests would ever pass.
    """
    hh, mm = (int(p) for p in et_time.split(":"))
    y, mo, d = (int(p) for p in et_date.split("-"))
    frozen = datetime(y, mo, d, hh, mm, tzinfo=audit.ET)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(audit, "datetime", _Frozen)


def test_the_day_still_in_progress_is_not_yet_a_missing_close(temp_db, monkeypatch):
    """The false alarm this fixes. At 09:33 today has no 16:00 price because
    16:00 has not happened -- that is the day running, not the day failing.
    Reported every morning, it teaches its reader to skim the audit."""
    _snap(temp_db, WED, "09:33:00")
    _freeze(monkeypatch, WED, "09:33")
    assert _findings(audit.check_closing_price, temp_db) == []


def test_after_the_close_today_is_judged_like_any_other_day(temp_db, monkeypatch):
    """The other half, and why this is not simply "skip today". Once 16:02 has
    passed the question is answerable, so a today that genuinely missed its
    close is reported the same evening rather than a day late."""
    _snap(temp_db, WED, "15:59:53")
    _freeze(monkeypatch, WED, "16:30")
    found = _findings(audit.check_closing_price, temp_db)
    assert len(found) == 1
    assert "no price at or after 16:00" in found[0].summary


def test_the_in_progress_day_leaves_the_denominator(temp_db, monkeypatch):
    """Excluding today from the numerator but not the total would report
    "1 of 2 trading days" on a record whose only judgable day is faulty."""
    _snap(temp_db, "2026-07-14", "15:59:00")
    _snap(temp_db, WED, "09:33:00")
    _freeze(monkeypatch, WED, "09:33")
    found = _findings(audit.check_closing_price, temp_db)
    assert len(found) == 1
    assert "1 of 1 trading days" in found[0].summary


# ─────────────────────────────────────────────────────────────────────────────
# Both third-Friday contracts (ADR-046)
# ─────────────────────────────────────────────────────────────────────────────

def test_a_third_friday_with_only_one_contract_is_reported(temp_db):
    sid = _snap(temp_db, WED, "12:00:00")
    _iv_row(temp_db, sid, THIRD_FRIDAY, "PM", 0.15)
    found = _findings(audit.check_both_third_friday_contracts, temp_db)
    assert len(found) == 1
    assert THIRD_FRIDAY in found[0].summary


def test_a_third_friday_with_both_contracts_is_silent(temp_db):
    sid = _snap(temp_db, WED, "12:00:00")
    _iv_row(temp_db, sid, THIRD_FRIDAY, "AM", 0.15)
    _iv_row(temp_db, sid, THIRD_FRIDAY, "PM", 0.16)
    assert _findings(audit.check_both_third_friday_contracts, temp_db) == []


def test_an_ordinary_expiry_with_one_contract_is_not_a_finding(temp_db):
    """Only the third Friday lists two. Flagging every other date would bury
    the real one under ~250 false positives a year."""
    sid = _snap(temp_db, WED, "12:00:00")
    _iv_row(temp_db, sid, "2026-07-16", "PM", 0.15)
    assert _findings(audit.check_both_third_friday_contracts, temp_db) == []


# ─────────────────────────────────────────────────────────────────────────────
# Day completeness — and reconciling it against what was already known
# ─────────────────────────────────────────────────────────────────────────────

def test_a_short_day_with_no_recorded_gap_is_an_alarm(temp_db):
    _full_day(temp_db, "2026-07-13", 128)      # first day, always excused
    _full_day(temp_db, "2026-07-14", 128)
    _full_day(temp_db, "2026-07-15", 40)       # the short one
    found = _findings(audit.check_day_completeness, temp_db)
    assert [f.severity for f in found] == ["alarm"]
    assert "NO gap recorded" in found[0].summary


def test_a_short_day_the_collector_owned_up_to_is_only_a_note(temp_db):
    """The ADR-045 lesson. The gap detector working is not a finding, and
    reporting it as one teaches the reader to skim the whole report."""
    _full_day(temp_db, "2026-07-13", 128)
    _full_day(temp_db, "2026-07-14", 128)
    _full_day(temp_db, "2026-07-15", 40)
    with sqlite3.connect(temp_db) as c:
        c.execute(
            """insert into collection_gaps
               (gap_start, gap_end, gap_minutes, expected_snapshots_lost,
                reason, detected_at)
               values ('2026-07-15 14:00:00', '2026-07-15 18:00:00', 240, 48,
                       'COLLECTOR_OFFLINE', '2026-07-15 18:00:00')""")
    found = _findings(audit.check_day_completeness, temp_db)
    assert [f.severity for f in found] == ["note"]
    assert "explained by a recorded outage" in found[0].summary


def test_the_very_first_day_is_never_short(temp_db):
    """Collection began at 12:22 on 2026-06-23. A first day is a partial day by
    definition and flagging it is noise for the life of the project."""
    _full_day(temp_db, "2026-06-23", 57)
    _full_day(temp_db, "2026-06-24", 128)
    assert _findings(audit.check_day_completeness, temp_db) == []


def test_full_days_produce_nothing(temp_db):
    for d in ("2026-07-13", "2026-07-14", "2026-07-15"):
        _full_day(temp_db, d, 128)
    assert _findings(audit.check_day_completeness, temp_db) == []


# ─────────────────────────────────────────────────────────────────────────────
# IV sanity — the check that found the -9.99 sentinel (BUG-030)
# ─────────────────────────────────────────────────────────────────────────────

def test_the_brokers_no_value_sentinel_is_caught(temp_db):
    """Schwab sends -999.0 when it has no IV to give; the collector's /100
    turns it into -9.99, and it was stored as though it were a volatility.
    This is the check that found it (BUG-030)."""
    sid = _snap(temp_db, WED, "09:30:30")
    _iv_row(temp_db, sid, "2026-08-05", "PM", -9.99)
    found = _findings(audit.check_iv_sanity, temp_db)
    assert len(found) == 1
    assert found[0].severity == "alarm"
    assert "at or below zero" in found[0].summary


def test_ordinary_volatility_is_not_flagged(temp_db):
    """12% and 45% are both entirely normal and must stay silent."""
    sid = _snap(temp_db, WED, "12:00:00")
    _iv_row(temp_db, sid, "2026-08-05", "PM", 0.12)
    _iv_row(temp_db, sid, "2026-08-06", "PM", 0.45)
    assert _findings(audit.check_iv_sanity, temp_db) == []


def test_an_absurdly_high_iv_is_a_prompt_not_a_verdict(temp_db):
    """SPX has printed above 100% in a real panic, so this warns rather than
    alarms — the ceiling catches decimal slips, not market drama."""
    sid = _snap(temp_db, WED, "12:00:00")
    _iv_row(temp_db, sid, "2026-08-05", "PM", 12.0)
    found = _findings(audit.check_iv_sanity, temp_db)
    assert [f.severity for f in found] == ["warn"]


def test_a_missing_iv_is_not_a_bad_one(temp_db):
    """Missing price -> blank, not 0, is a standing rule. A NULL means the
    question was never answered and must not be read as a value of zero."""
    sid = _snap(temp_db, WED, "12:00:00")
    _iv_row(temp_db, sid, "2026-08-05", "PM", None)
    assert _findings(audit.check_iv_sanity, temp_db) == []


# ─────────────────────────────────────────────────────────────────────────────
# The report itself
# ─────────────────────────────────────────────────────────────────────────────

def test_a_clean_record_exits_zero(temp_db, monkeypatch, capsys):
    monkeypatch.setattr(config, "DB_PATH", temp_db)
    monkeypatch.setattr(audit.config, "DB_PATH", temp_db)
    _full_day(temp_db, "2026-07-14", 128)
    _full_day(temp_db, "2026-07-15", 128)
    assert audit.main([]) == 0
    assert "Nothing found" in capsys.readouterr().out


def test_findings_exit_one_and_are_printed(temp_db, monkeypatch, capsys):
    monkeypatch.setattr(config, "DB_PATH", temp_db)
    monkeypatch.setattr(audit.config, "DB_PATH", temp_db)
    _freeze(monkeypatch, "2026-09-10", "12:00")
    _snap(temp_db, "2026-09-04", "15:59:00")
    assert audit.main([]) == 1
    assert "no price at or after 16:00" in capsys.readouterr().out
