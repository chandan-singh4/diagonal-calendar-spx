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

from datetime import UTC, datetime, timedelta

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
        start_et.astimezone(UTC),
        end_et.astimezone(UTC),
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
    assert (collector._CLOSE_END.hour, collector._CLOSE_END.minute) == (16, 2)


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

def test_ordinary_overnight_is_routine():
    """FIXED — BUG-005, the headline case. Was pinned as COLLECTOR_OFFLINE.

    A normal Tuesday night: last snapshot at 15:59 (the collector's cadence
    puts the final write a minute before the 16:00 cutoff, not on it), restart
    at 09:30. One minute of session time falls inside the window, which is
    inside the tolerance, so nothing collectable was missed.
    """
    assert classify(et(2026, 7, TUE, 15, 59), et(2026, 7, WED, 9, 30)) == "MARKET_CLOSED"


def test_a_full_weekend_is_routine_for_the_right_reason_now():
    """Friday 15:59 to Monday 09:30 — 3,931 minutes.

    The old code got this right by accident, via `> 3,600 minutes must be a
    weekend`. It is now right because the weekend contains no trading day and
    therefore no collectable minutes. Same verdict, sound reasoning — and the
    rule that produced it no longer hides three-day outages.
    """
    assert classify(et(2026, 7, FRI, 15, 59), et(2026, 7, MON + 7, 9, 30)) == "MARKET_CLOSED"


def test_a_weekend_restart_is_routine():
    """FIXED — BUG-005. THE REAL OBSERVED CASE, and the one that started this.

    The gap `scripts/check_db.py` printed on 2026-07-26: last snapshot Friday
    15:59 ET, collector restarted on the Sunday. 2,432 minutes, none of it
    open market. Reported as a fault before; routine now.
    """
    assert classify(et(2026, 7, FRI, 15, 59), et(2026, 7, 26, 8, 31)) == "MARKET_CLOSED"


def test_restart_a_minute_after_the_open_is_still_routine():
    """FIXED — BUG-005. The collector restarts at 09:30–09:31, so up to a
    minute of session time is unavoidably inside the gap. That is cadence, not
    data loss, and the tolerance absorbs it.

    The last write moved from 15:59 to 16:01 with ADR-049; the end-of-day cost
    is 1.0 minute either way, so the budget is unchanged."""
    assert classify(et(2026, 7, TUE, 16, 1), et(2026, 7, WED, 9, 31)) == "MARKET_CLOSED"


def test_every_overnight_gap_the_collector_produces_is_now_routine():
    """FIXED — BUG-005, the property that actually matters. Was: all four
    COLLECTOR_OFFLINE, "not one is recognised as routine"."""
    overnight_gaps = [
        (et(2026, 7, MON, 16, 1), et(2026, 7, TUE, 9, 30)),
        (et(2026, 7, TUE, 16, 1), et(2026, 7, WED, 9, 30)),
        (et(2026, 7, WED, 16, 1), et(2026, 7, THU, 9, 31)),
        (et(2026, 7, THU, 16, 1), et(2026, 7, FRI, 9, 30)),
    ]
    verdicts = {classify(s, e) for s, e in overnight_gaps}
    assert verdicts == {"MARKET_CLOSED"}, "every routine night is recognised"


def test_the_tolerance_does_not_swallow_a_real_outage():
    """The tolerance absorbs cadence slop, not data loss. Four minutes late on
    the open is past it — during the OPEN session that is four missed
    snapshots, at the most volatile time of day."""
    assert classify(et(2026, 7, TUE, 15, 59), et(2026, 7, WED, 9, 35)) == "COLLECTOR_OFFLINE"


@pytest.mark.parametrize("minutes_late, missed, expected", [
    (1, 2.0, "MARKET_CLOSED"),        # 09:31 — cadence at both ends
    (2, 3.0, "MARKET_CLOSED"),        # 09:32 — exactly at the boundary, inclusive
    (3, 4.0, "COLLECTOR_OFFLINE"),    # 09:33 — past it
    (4, 5.0, "COLLECTOR_OFFLINE"),
])
def test_the_tolerance_boundary_is_where_it_is_documented_to_be(
        minutes_late, missed, expected):
    """3.0 market minutes, inclusive — pinned so the constant cannot drift.

    Note the budget is spent at BOTH ends: a 16:01 last write already consumes
    1.0 minute before the morning is considered, so the collector can be at
    most 2 minutes late on the open, not 3. My first version of this test got
    that wrong and the code caught it, which is the right way round.

    ADR-049 widened the window to 16:02 and moved the last write from 15:59 to
    16:01. Both numbers moved by two minutes, so the 1.0-minute end-of-day cost
    and this whole budget are exactly as they were.
    """
    end = et(2026, 7, WED, 9, 30) + timedelta(minutes=minutes_late)
    assert mm(et(2026, 7, TUE, 16, 1), end) == missed
    assert classify(et(2026, 7, TUE, 16, 1), end) == expected


# ─────────────────────────────────────────────────────────────────────────────
# PINNED — the far more dangerous direction, NOT recorded in the backlog
#
# BUG-005 was written up as "cries wolf". It also does the opposite: two rules
# can label a genuine multi-day outage as routine and suppress it entirely.
# _check_startup_gap() does not merely mislabel these — it returns early and
# never writes the row at all.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_three_day_outage_during_trading_days_is_a_fault():
    """FIXED — BUG-005, false-negative direction. Was pinned as MARKET_CLOSED.

    The collector dies Monday lunchtime, unnoticed until Thursday lunchtime.
    Three trading days gone — the worst data-loss event this system can suffer
    short of losing the file, and it was reported as routine and then silently
    discarded by _check_startup_gap(). This is the exact scenario the M3.4
    liveness alert exists to catch; the alarm was blind to its own primary case.
    """
    assert classify(et(2026, 7, MON, 12), et(2026, 7, THU, 12)) == "COLLECTOR_OFFLINE"


def test_a_long_outage_containing_a_holiday_is_still_a_fault():
    """FIXED — BUG-005, false-negative direction. Was pinned as HOLIDAY.

    The old holiday scan returned HOLIDAY if ANY day inside the gap was a
    holiday, however much open market the gap also covered. Dead from 30 June
    to 8 July is five lost trading days; 3 July being a holiday no longer
    excuses the other five.
    """
    assert classify(et(2026, 6, 30, 12), et(2026, 7, 8, 12)) == "COLLECTOR_OFFLINE"


def test_a_genuine_holiday_closure_is_still_reported_as_holiday():
    """The specific label survives where it is actually true: Thursday's close
    through Monday's open, spanning the Friday 3 July holiday. Nothing was
    collectable, and the holiday is the reason worth recording."""
    assert classify(et(2026, 7, PRE_HOLIDAY_THU, 15, 59),
                    et(2026, 7, 6, 9, 30)) == "HOLIDAY"


def test_holiday_outranks_market_closed_only_when_a_weekday_is_lost():
    """A plain weekend is MARKET_CLOSED, not HOLIDAY — there is no holiday in
    it. The distinction matters because it is the difference between 'the
    market was shut as usual' and 'the market was shut unusually'."""
    assert classify(et(2026, 7, FRI, 15, 59), et(2026, 7, MON + 7, 9, 30)) == "MARKET_CLOSED"


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

def midsession(start_et, end_et, poll_interval=300):
    return collector._midsession_gap_reason(
        start_et.astimezone(UTC),
        end_et.astimezone(UTC),
        poll_interval,
    )


def test_the_first_cycle_of_the_morning_no_longer_records_a_false_gap():
    """FIXED — BUG-005, and this is the one that produced most of the 46 rows.

    Previously: `prev_snapshot_ts` is not cleared when the market closes, so at
    09:30 the first cycle compares against 15:59 the previous day — ~1,051
    minutes against a 2.5-minute threshold — and wrote a hardcoded
    COLLECTOR_OFFLINE row. Every trading morning.

    Now the decision routes through the classifier, which sees no missed market
    time and returns None: do not record.

    Was pinned by asserting against main()'s source, because the logic was
    inline and untestable. Extracting it into _midsession_gap_reason() was part
    of the fix, so this now tests behaviour instead of reading code.
    """
    assert midsession(et(2026, 7, TUE, 15, 59), et(2026, 7, WED, 9, 30)) is None


def test_a_genuine_midsession_stall_is_still_recorded():
    """The detector must keep doing its actual job: a 40-minute hole in the
    middle of a trading session is a real stall and must be reported."""
    assert midsession(et(2026, 7, TUE, 11, 0), et(2026, 7, TUE, 11, 40)) == "COLLECTOR_OFFLINE"


def test_a_normal_gap_between_snapshots_is_not_recorded():
    """One poll interval apart is not a gap at all."""
    assert midsession(et(2026, 7, TUE, 11, 0), et(2026, 7, TUE, 11, 5)) is None


def test_the_midsession_threshold_still_scales_with_the_poll_interval():
    """2.5x the interval, and the cadence is a property of the TIME, not of the
    argument. A 4-minute hole inside OPEN (09:30-10:00, 60s cadence) is a
    stall; the same 4 minutes inside MIDDAY (300s cadence) is one ordinary
    interval.

    Originally written as 11:00-11:04 with poll_interval=60, which asserted the
    wrong thing: 11:00 is MIDDAY, so the previous snapshot was taken at the
    300s cadence whatever the caller passes. The slower-of-the-two rule exposed
    that, so the test now picks times that genuinely sit in each session.
    """
    assert midsession(et(2026, 7, TUE, 9, 40), et(2026, 7, TUE, 9, 44),
                      poll_interval=60) == "COLLECTOR_OFFLINE"
    assert midsession(et(2026, 7, TUE, 11, 0), et(2026, 7, TUE, 11, 4),
                      poll_interval=300) is None


def test_the_1530_session_change_no_longer_looks_like_a_stall():
    """FIXED — BUG-005, third defect. Found 2026-07-26 by running the fixed
    classifier over the real collection_gaps rows.

    At 15:30 MIDDAY becomes CLOSE and the cadence changes from 300s to 60s.
    A perfectly ordinary 5-minute MIDDAY interval was then judged against the
    new 60s threshold — 5.0 > 2.5 — so a gap was recorded at the session change
    EVERY trading day. That is 22 of the 47 rows in the table, more than the
    overnight misclassification produced.

    These are the real timestamps of row 43: 15:26:37 to 15:31:37 ET.
    """
    assert midsession(et(2026, 7, THU, 15, 26, 37),
                      et(2026, 7, THU, 15, 31, 37),
                      poll_interval=60) is None


def test_the_1000_session_change_is_also_safe():
    """The reverse transition — OPEN (60s) to MIDDAY (300s). Harmless before,
    but covered by the same rule now."""
    assert midsession(et(2026, 7, THU, 9, 59), et(2026, 7, THU, 10, 1),
                      poll_interval=300) is None


def test_a_real_stall_across_the_session_change_is_still_caught():
    """The slower-cadence rule must not become a blanket excuse: 20 minutes of
    silence across 15:30 is well past even the MIDDAY threshold."""
    assert midsession(et(2026, 7, THU, 15, 20), et(2026, 7, THU, 15, 40),
                      poll_interval=60) == "COLLECTOR_OFFLINE"


def test_a_stall_straddling_the_close_counts_only_the_market_side():
    """A stall from 15:00 to 09:45 next morning is real — an hour of the CLOSE
    session was missed, plus a quarter-hour of the open — but it must be
    reported as a fault on those minutes, not on the 18 hours of wall clock."""
    assert midsession(et(2026, 7, TUE, 15, 0),
                      et(2026, 7, WED, 9, 45)) == "COLLECTOR_OFFLINE"


# ─────────────────────────────────────────────────────────────────────────────
# market_minutes_between — the single measurement the whole fix rests on
#
# Every classification is now a consequence of this number, so it is tested
# directly rather than only through its verdicts.
# ─────────────────────────────────────────────────────────────────────────────

def mm(start_et, end_et) -> float:
    return collector.market_minutes_between(
        start_et.astimezone(UTC),
        end_et.astimezone(UTC),
    )


def test_a_full_trading_day_is_392_minutes():
    """09:30 to 16:00 — six and a half hours."""
    assert mm(et(2026, 7, TUE, 9, 30), et(2026, 7, TUE, 16, 2)) == 392.0


def test_a_window_wider_than_the_session_still_counts_only_the_session():
    """Midnight to midnight on a trading day is still 392 collectable minutes."""
    assert mm(et(2026, 7, TUE, 0, 0), et(2026, 7, WED, 0, 0)) == 392.0


def test_an_overnight_window_costs_nothing():
    assert mm(et(2026, 7, TUE, 16, 2), et(2026, 7, WED, 9, 30)) == 0.0


def test_a_weekend_costs_nothing_however_long():
    assert mm(et(2026, 7, FRI, 16, 2), et(2026, 7, MON + 7, 9, 30)) == 0.0


def test_a_holiday_costs_nothing():
    """3 July 2026 is a weekday, but the market is shut."""
    assert mm(et(2026, 7, HOLIDAY_FRI, 9, 30), et(2026, 7, HOLIDAY_FRI, 16, 2)) == 0.0


def test_a_multi_day_outage_sums_each_trading_day():
    """Monday noon to Thursday noon: half of Monday, all of Tue and Wed, half
    of Thursday. 242 + 392 + 392 + 150 = 1,176."""
    assert mm(et(2026, 7, MON, 12, 0), et(2026, 7, THU, 12, 0)) == 1176.0


def test_a_week_long_outage_skips_the_holiday_and_the_weekend():
    """30 June (Tue) noon to 8 July (Wed) noon. Trading days inside: Tue 30
    (242), Wed 1 (392), Thu 2 (392), Mon 6 (392), Tue 7 (392), Wed 8 (150).
    Friday 3 is the holiday, 4-5 the weekend. Total 1,960."""
    assert mm(et(2026, 6, 30, 12, 0), et(2026, 7, 8, 12, 0)) == 1960.0


def test_the_boundary_minute_at_the_close_is_counted():
    """16:01 to 16:02 is the one minute the old off-by-one tripped over — the
    last collectable minute of the day, wherever the window happens to end."""
    assert mm(et(2026, 7, TUE, 16, 1), et(2026, 7, TUE, 16, 2)) == 1.0


def test_the_two_minutes_after_the_bell_are_collectable_now():
    """ADR-049: the whole point. 16:00-16:02 used to cost zero because it was
    outside the window; it is now the stretch that holds the closing print."""
    assert mm(et(2026, 7, TUE, 16, 0), et(2026, 7, TUE, 16, 2)) == 2.0


def test_a_night_that_starts_at_the_new_last_write_is_still_routine():
    """The crying-wolf guard. Widening the window without moving the expected
    last write with it would make every ordinary night look like a fault —
    exactly the BUG-005 failure, reintroduced from the other direction."""
    assert classify(et(2026, 7, TUE, 16, 1), et(2026, 7, WED, 9, 30)) == "MARKET_CLOSED"
    assert classify(et(2026, 7, FRI, 16, 1), et(2026, 7, MON + 7, 9, 30)) == "MARKET_CLOSED"


def test_a_backwards_or_empty_window_is_zero_not_negative():
    """MUTATION NOTE — deleting the `if end_utc <= start_utc` guard leaves the
    suite green, and no test can distinguish it.

    The guard is genuinely redundant: for a backwards window the day loop
    either never runs (the start date is after the end date) or the
    `overlap_end > overlap_start` check rejects the only day it visits.
    Verified by running an unguarded copy side by side across same-day,
    multi-day and zero-length windows — identical results.

    Kept anyway. It states the function's contract at the top rather than
    leaving a reader to derive it from two subtler conditions, and a clock
    adjustment producing end < start is exactly the sort of input that should
    fail obviously rather than subtly. Recorded here as a known equivalent
    mutant so a later audit does not read it as an untested branch.
    """
    assert mm(et(2026, 7, TUE, 12, 0), et(2026, 7, TUE, 12, 0)) == 0.0
    assert mm(et(2026, 7, TUE, 14, 0), et(2026, 7, TUE, 11, 0)) == 0.0
    assert mm(et(2026, 7, THU, 12, 0), et(2026, 7, MON, 12, 0)) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Cases the classifier already got right — these must NOT regress
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
