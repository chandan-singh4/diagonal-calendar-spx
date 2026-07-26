"""
Tests for the collection-gap classifier in collector.py.

WHY THIS MODULE (M1.6, BUG-005): every time the collector starts, it looks at
how long it has been since the last snapshot and decides *why*. Three answers
are possible — HOLIDAY, MARKET_CLOSED (routine: nights, weekends) and
COLLECTOR_OFFLINE (a real fault). That verdict decides whether a gap is
recorded as a data-quality problem at all, and M3.4 plans to build liveness
alerting on top of it.

It has never once produced MARKET_CLOSED or HOLIDAY. All 46 rows in
collection_gaps are COLLECTOR_OFFLINE, at least 19 of them plainly routine.
The health check confirmed it live on 2026-07-26: an ordinary weekend was
reported as COLLECTOR_OFFLINE.

WRITTEN BEFORE THE FIX, ON PURPOSE (ADR-019). Everything below pins the
CURRENT, WRONG behaviour so that the fix arrives as a visible, deliberate
change to these tests rather than as tests written to fit whatever the new
code happens to do. Each such test is marked PINNED and names the defect.

TIME CONVENTION: the collector stores and reasons in UTC, but every rule it is
being judged against ("the market opens at 09:30") is Eastern wall-clock time.
Fixtures are therefore written in ET and converted, so daylight-saving shifts
cannot silently move a boundary. In July, ET is UTC-4.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import collector

_ET = collector._ET


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def et(year, month, day, hour, minute=0, second=0) -> datetime:
    """An Eastern wall-clock moment, as an aware datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=_ET)


def classify(start_et: datetime, end_et: datetime) -> str:
    """Classify a gap given its ET endpoints (the function takes UTC)."""
    return collector._classify_gap(
        start_et.astimezone(collector.timezone.utc),
        end_et.astimezone(collector.timezone.utc),
    )


# Reference dates, chosen against the real 2026 calendar in config.py:
#   2026-07-20 Mon … 2026-07-24 Fri   ordinary trading week
#   2026-07-03 Fri                    Independence Day observed (a HOLIDAY)
#   2026-07-02 Thu                    the trading day before that holiday
MON, TUE, WED, THU, FRI = 20, 21, 22, 23, 24
HOLIDAY_FRI = 3          # 2026-07-03
PRE_HOLIDAY_THU = 2      # 2026-07-02


# ─────────────────────────────────────────────────────────────────────────────
# The session window itself — the ground truth the classifier is judged against
# ─────────────────────────────────────────────────────────────────────────────

def test_market_window_constants_are_what_the_rest_of_this_file_assumes():
    assert (collector._OPEN_START.hour, collector._OPEN_START.minute) == (9, 30)
    assert (collector._CLOSE_END.hour, collector._CLOSE_END.minute) == (16, 0)


def test_the_2026_holiday_table_contains_the_dates_used_below():
    assert collector._is_holiday(et(2026, 7, HOLIDAY_FRI, 12).date())
    assert not collector._is_holiday(et(2026, 7, FRI, 12).date())


def test_weekends_are_not_trading_days():
    assert collector._is_trading_day(et(2026, 7, FRI, 12).date())
    assert not collector._is_trading_day(et(2026, 7, 25, 12).date())   # Saturday
    assert not collector._is_trading_day(et(2026, 7, 26, 12).date())   # Sunday


def test_a_weekday_holiday_is_not_a_trading_day():
    assert not collector._is_trading_day(et(2026, 7, HOLIDAY_FRI, 12).date())


# ─────────────────────────────────────────────────────────────────────────────
# PINNED — the routine gaps that are wrongly reported as faults
#
# These are the ~19 rows in collection_gaps that should never have been there.
# ─────────────────────────────────────────────────────────────────────────────

def test_pinned_ordinary_overnight_is_called_collector_offline(collector_offline_note=None):
    """PINNED — BUG-005, the headline case. This is a normal Tuesday night.

    The collector wrote its last snapshot at 15:59 (its cadence puts the final
    write one minute before the 16:00 cutoff, not on it) and restarted at 09:30
    the next morning. Nothing was collectable in between and nothing was lost.

    Two off-by-ones against the collector's own window make it a fault:
      after_close = start.time() >= 16:00  →  15:59 is not, so False
      before_open = end.time()   <  09:30  →  09:30 is not, so False
    Both False, so it falls through to COLLECTOR_OFFLINE.
    """
    assert classify(et(2026, 7, TUE, 15, 59), et(2026, 7, WED, 9, 30)) == "COLLECTOR_OFFLINE"


def test_pinned_a_full_weekend_is_right_but_for_the_wrong_reason():
    """PINNED — Friday 15:59 to Monday 09:30 is 3,931 minutes, which trips the
    crude `> 3,600 minutes must be a weekend` heuristic and lands on the right
    answer by accident.

    The backlog claimed this case was misfiled; it is not. Writing the test
    first is what established that — the number in the write-up (2,611) was
    wrong. The verdict is right, the reasoning is not: the same rule hides a
    genuine three-day outage (see below), which is the same defect wearing the
    opposite sign.
    """
    assert classify(et(2026, 7, FRI, 15, 59), et(2026, 7, MON + 7, 9, 30)) == "MARKET_CLOSED"


def test_pinned_a_weekend_restart_is_called_collector_offline():
    """PINNED — BUG-005. THE REAL OBSERVED CASE.

    This is the gap `scripts/check_db.py` printed on 2026-07-26: last snapshot
    Friday 15:59 ET, collector restarted on the Sunday. 2,432 minutes — under
    the 3,600 heuristic, so it falls through to the two off-by-ones and is
    reported as a fault. Nothing was collectable the entire time.
    """
    assert classify(et(2026, 7, FRI, 15, 59), et(2026, 7, 26, 8, 31)) == "COLLECTOR_OFFLINE"


def test_pinned_restart_a_minute_after_the_open_is_called_collector_offline():
    """PINNED — BUG-005. The collector restarts at 09:30–09:31, so the `<09:30`
    test fails even when the overnight period itself was entirely routine."""
    assert classify(et(2026, 7, TUE, 15, 59), et(2026, 7, WED, 9, 31)) == "COLLECTOR_OFFLINE"


def test_pinned_every_overnight_gap_the_collector_produces_is_misfiled():
    """PINNED — BUG-005, stated as the property that actually matters.

    Every ordinary overnight gap is reported as a fault. The endpoints the rule
    needs for MARKET_CLOSED (start at or after 16:00, end strictly before
    09:30) are not the endpoints the collector generates — it writes its last
    snapshot at 15:59 and restarts at 09:30–09:31.

    Weekends are excluded here: they are long enough to trip the 3,600-minute
    heuristic and come out right by accident (tested above).
    """
    overnight_gaps = [
        (et(2026, 7, MON, 15, 59), et(2026, 7, TUE, 9, 30)),
        (et(2026, 7, TUE, 15, 59), et(2026, 7, WED, 9, 30)),
        (et(2026, 7, WED, 15, 59), et(2026, 7, THU, 9, 31)),
        (et(2026, 7, THU, 15, 59), et(2026, 7, FRI, 9, 30)),
    ]
    verdicts = {classify(s, e) for s, e in overnight_gaps}
    assert verdicts == {"COLLECTOR_OFFLINE"}, "not one is recognised as routine"


# ─────────────────────────────────────────────────────────────────────────────
# PINNED — the far more dangerous direction, NOT recorded in the backlog
#
# BUG-005 was written up as "cries wolf". It also does the opposite: two rules
# can label a genuine multi-day outage as routine and suppress it entirely.
# _check_startup_gap() does not merely mislabel these — it returns early and
# never writes the row at all.
# ─────────────────────────────────────────────────────────────────────────────

def test_pinned_a_three_day_outage_during_trading_days_is_hidden_as_market_closed():
    """PINNED — BUG-005, false-negative direction. Found 2026-07-26 (M1.6).

    The collector dies Monday lunchtime and is not noticed until Thursday
    lunchtime. Three full trading days are gone — the single worst data-loss
    event this system can suffer short of losing the file.

    `if gap_minutes > 3600: return "MARKET_CLOSED"` assumes any gap longer than
    60 hours must be a weekend. This one is 72 hours of mostly *open* market, so
    it is reported as routine, and _check_startup_gap() then discards it without
    recording anything. The outage leaves no trace whatsoever.

    This is the exact scenario the M3.4 liveness alert exists to catch, so the
    bug is worse than "the alarm is noisy" — the alarm is blind to its own
    primary case.
    """
    assert classify(et(2026, 7, MON, 12), et(2026, 7, THU, 12)) == "MARKET_CLOSED"


def test_pinned_any_holiday_anywhere_in_a_long_outage_hides_it_as_holiday():
    """PINNED — BUG-005, false-negative direction. Found 2026-07-26 (M1.6).

    The holiday scan returns HOLIDAY if *any* calendar day inside the gap is a
    holiday, regardless of how much open market the gap also covers. A collector
    dead from 30 June to 8 July loses five trading days, but 3 July is in the
    range, so the whole outage is filed as "holiday" and suppressed.
    """
    assert classify(et(2026, 6, 30, 12), et(2026, 7, 8, 12)) == "HOLIDAY"


def test_pinned_a_single_holiday_day_is_reported_the_same_way_as_a_week_long_outage():
    """PINNED — the consequence: the label carries no information about size."""
    genuine = classify(et(2026, 7, PRE_HOLIDAY_THU, 15, 59), et(2026, 7, 6, 9, 30))
    masked = classify(et(2026, 6, 30, 12), et(2026, 7, 8, 12))
    assert genuine == masked == "HOLIDAY"


# ─────────────────────────────────────────────────────────────────────────────
# PINNED — the path that produced most of the 46 rows, and never asked the
# classifier at all. NOT in the backlog write-up; found 2026-07-26 (M1.6).
#
# BUG-005 blamed _classify_gap(). But _classify_gap() is only reached from
# _check_startup_gap(), which runs once per collector START. The collector has
# run continuously since 2026-07-16, so that path has fired a handful of times.
#
# The mid-session detector in main() is the one that fires constantly — and it
# hardcodes reason="COLLECTOR_OFFLINE" without consulting the classifier.
# Critically, `prev_snapshot_ts` is NOT reset when the market closes: the loop
# simply sleeps and continues. So at 09:30 every trading morning the first
# cycle compares against 15:59 the previous day, sees ~1,051 minutes against a
# 2.5-minute threshold, and writes a COLLECTOR_OFFLINE row for a night during
# which nothing could have been collected.
#
# That is once per trading day, and it explains the volume of false rows far
# better than the startup path does. Fixing only _classify_gap() would have
# left the actual generator untouched.
# ─────────────────────────────────────────────────────────────────────────────

def test_pinned_midsession_detector_exists_and_bypasses_the_classifier():
    """PINNED — the decision is inline in main() and hardcoded.

    Asserted against the source because the logic is not extracted into
    anything callable yet. Part of the fix is to extract it so it can be
    tested properly rather than read.
    """
    import inspect

    src = inspect.getsource(collector.main)
    assert 'reason                  = "COLLECTOR_OFFLINE"' in src, (
        "the mid-session gap reason is hardcoded"
    )
    assert "_classify_gap" not in src, (
        "and main() never consults the classifier"
    )


def test_pinned_prev_snapshot_timestamp_is_not_reset_when_the_market_closes():
    """PINNED — the reason the mid-session detector fires every morning.

    The market-closed branch sleeps and continues without clearing
    `prev_snapshot_ts`, so the comparison straddles the overnight break.
    """
    import inspect

    src = inspect.getsource(collector.main)
    closed_branch = src.split("if session is None:")[1].split("continue")[0]
    assert "prev_snapshot_ts" not in closed_branch, (
        "the market-closed branch does not clear prev_snapshot_ts"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cases the current classifier already gets right — these must NOT regress
# ─────────────────────────────────────────────────────────────────────────────

def test_a_gap_inside_one_trading_session_is_a_fault():
    """11:00 to 14:00 on a Tuesday: three hours of open market missed."""
    assert classify(et(2026, 7, TUE, 11), et(2026, 7, TUE, 14)) == "COLLECTOR_OFFLINE"


def test_a_gap_spanning_the_lunch_hours_of_two_days_is_a_fault():
    assert classify(et(2026, 7, TUE, 14), et(2026, 7, WED, 11)) == "COLLECTOR_OFFLINE"


def test_a_gap_strictly_between_close_and_open_is_market_closed():
    """The narrow case the current endpoint test does handle: a gap that starts
    exactly at 16:00 and ends before 09:30. The collector does not actually
    produce these, which is why the rule looked correct and never fired."""
    assert classify(et(2026, 7, TUE, 16, 0), et(2026, 7, WED, 9, 0)) == "MARKET_CLOSED"