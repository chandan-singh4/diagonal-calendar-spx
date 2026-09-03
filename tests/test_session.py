"""core/session.py — which session it is, and how fresh prices ought to be.

WHY THIS FILE EXISTS SEPARATELY from tests/test_collector_main.py, which
already covers `collector.get_session`. That file tests the collector's
question ("how long do I sleep?"). This one tests the same function in its new
role as the SHARED answer relied on by the dashboard header and the watchdog.
The collector tests stay where they are and still pass — they now exercise
this module through the collector's thin wrapper, which is the point of
delegating rather than duplicating.

The thing worth guarding here is the boundary behaviour and the None case.
None means "the market is shut, there is no expectation" — and every caller
that mistakes it for zero produces the same failure: a dashboard glowing red
all evening, or a watchdog emailing at midnight. An alarm that cries wolf
nightly is not read on the morning it is right.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import collector
import config
from core import session

ET = ZoneInfo("America/New_York")

# A known-good week in July 2026: Wed 15th is an ordinary trading day.
WED = 15
SAT, SUN = 18, 19

# Christmas Day 2026 falls on a Friday — a weekday that is not a trading day,
# which is the only kind of holiday that can catch a weekday-only check out.
XMAS = date(2026, 12, 25)
HOLIDAYS = {"2026-12-25"}


def et(day: int, hour: int, minute: int, second: int = 0, month: int = 7) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=ET)


# ─────────────────────────────────────────────────────────────────────────────
# is_trading_day
# ─────────────────────────────────────────────────────────────────────────────

def test_an_ordinary_weekday_is_a_trading_day():
    assert session.is_trading_day(date(2026, 7, WED), set())


@pytest.mark.parametrize("day", [SAT, SUN])
def test_the_weekend_is_not(day):
    assert not session.is_trading_day(date(2026, 7, day), set())


def test_a_weekday_holiday_is_not():
    """The case a `weekday() < 5` check alone gets wrong."""
    assert session.is_trading_day(XMAS, set())          # without the list
    assert not session.is_trading_day(XMAS, HOLIDAYS)   # with it


# ─────────────────────────────────────────────────────────────────────────────
# session_of — the boundaries
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,minute,expected", [
    (9, 29, None),        # one minute before the bell
    (9, 30, "OPEN"),      # the bell
    (9, 59, "OPEN"),
    (10, 0, "MIDDAY"),    # OPEN ends, MIDDAY begins — no gap, no overlap
    (15, 29, "MIDDAY"),
    (15, 30, "CLOSE"),    # MIDDAY ends, CLOSE begins
    (15, 59, "CLOSE"),
    (16, 0, "CLOSE"),     # the equity close itself — captured, not skipped
    (16, 1, "CLOSE"),     # the settled closing print lands in here
    (16, 2, None),        # window ends; SPX has frozen and options run to 16:15
    (20, 0, None),
    (3, 0, None),
])
def test_each_boundary_falls_on_the_expected_side(hour, minute, expected):
    assert session.session_of(et(WED, hour, minute), set()) == expected


def test_seconds_do_not_shift_a_boundary():
    """09:30:47 is OPEN, not 'not yet'. The original stripped seconds before
    comparing and this keeps that, because a 47-second-late first poll must not
    be classified as out of hours."""
    assert session.session_of(et(WED, 9, 30, 47), set()) == "OPEN"
    assert session.session_of(et(WED, 15, 59, 59), set()) == "CLOSE"
    assert session.session_of(et(WED, 16, 1, 59), set()) == "CLOSE"


# ─────────────────────────────────────────────────────────────────────────────
# The closing print (ADR-049)
#
# Until 2026-09-03 the window ended AT 16:00, so the last price of every day
# was the 15:59 poll and the close itself was never recorded — not once, since
# collection began. These pin the fix, and the reason it is 16:02 rather than
# 16:01: the index is struck from component closing auction prints that arrive
# over the seconds after the bell.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_closing_bell_is_inside_the_window_not_outside_it():
    """The regression that motivated ADR-049. If this fails, the close is being
    dropped again and nobody will notice for weeks — the data looks complete."""
    assert session.session_of(et(WED, 16, 0), set()) == "CLOSE"


def test_there_is_room_after_the_bell_for_the_print_to_settle():
    """One minute is not enough; the whole point is polling AFTER 16:00."""
    assert session.session_of(et(WED, 16, 1), set()) == "CLOSE"
    assert session.CLOSE_END > __import__("datetime").time(16, 0)


def test_the_window_still_closes_well_before_the_options_do():
    """16:02, not 16:15. Options trade on to 16:15 but SPX is frozen, so IVs
    computed there are unreliable — that original reasoning is unchanged."""
    assert session.session_of(et(WED, 16, 2), set()) is None
    assert session.session_of(et(WED, 16, 14), set()) is None


def test_the_closing_polls_run_at_the_fast_interval():
    """They fall in CLOSE, so they inherit the 60-second cadence rather than
    the 5-minute one — which is what makes 16:00 and 16:01 both get sampled."""
    assert session.expected_interval("CLOSE", 60, 300) == 60


def test_the_market_is_shut_all_weekend_whatever_the_hour():
    for day in (SAT, SUN):
        for hour in (9, 12, 15):
            assert session.session_of(et(day, hour, 45), set()) is None


def test_a_holiday_is_shut_at_midday():
    assert session.session_of(et(25, 12, 0, month=12), HOLIDAYS) is None


# ─────────────────────────────────────────────────────────────────────────────
# expected_interval — the number the header's threshold IS
# ─────────────────────────────────────────────────────────────────────────────

def test_the_first_and_last_half_hour_expect_a_price_every_minute():
    assert session.expected_interval("OPEN", 60, 300) == 60
    assert session.expected_interval("CLOSE", 60, 300) == 60


def test_midday_expects_one_every_five_minutes():
    assert session.expected_interval("MIDDAY", 60, 300) == 300


def test_a_shut_market_has_no_expectation_at_all():
    """None, NOT zero. Zero would mean 'a price is due every zero seconds',
    so every caller would report the data as permanently late from 16:00
    until 09:30 the next morning."""
    assert session.expected_interval(None, 60, 300) is None


def test_the_busy_sessions_expect_data_more_often_than_the_quiet_one():
    """States the relationship rather than the two numbers, so it still means
    something if the intervals are ever retuned."""
    assert (session.expected_interval("OPEN", 60, 300)
            < session.expected_interval("MIDDAY", 60, 300))


# ─────────────────────────────────────────────────────────────────────────────
# The collector still gets the same answers through its wrapper
# ─────────────────────────────────────────────────────────────────────────────

def test_the_collector_and_the_header_cannot_disagree():
    """The reason this module was extracted at all. If someone re-inlines the
    boundaries into collector.py, the two stop agreeing and this fails."""
    for hour, minute in [(9, 30), (9, 59), (10, 0), (15, 29), (15, 30), (16, 0)]:
        now = et(WED, hour, minute)
        assert collector.get_session(now) == session.session_of(
            now, config.MARKET_HOLIDAYS
        )
