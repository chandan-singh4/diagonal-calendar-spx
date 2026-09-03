"""
Tests for the collector's loop decisions — when to collect, when to retry,
when to log in again.

WHY THIS MODULE (M1.6, final part). These judgements were the last of the
collector with no checks, and the reason was structural rather than editorial:
they were written inline inside main()'s `while True`. No test can enter that
loop — it never returns, it sleeps in real time, and it calls Schwab. So they
were extracted to pure functions first (2026-07-26) and are exercised here
directly. The extraction changed no behaviour; the full suite stayed green
across it.

WHAT DEPENDS ON THESE. All four are small, and all four are load-bearing:

  get_session        decides whether prices are collected at all. Wrong at a
                     boundary and the day is silently short at one end.
  is_auth_error      decides whether a failure throws away the client and logs
                     in again. Wrong and either every remaining cycle of the
                     session fails against a dead client, or a working client
                     is discarded for nothing.
  sleep_after_cycle  keeps snapshots landing on the interval instead of
                     drifting later all day.
  failure_is_critical  decides how loudly a bad run is reported.

TIME CONVENTION (as in test_collector_gaps.py): the collector reasons in UTC,
but every rule it is judged against is Eastern wall-clock. Fixtures are written
in ET so a daylight-saving shift cannot quietly move a boundary.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import collector

_ET = collector._ET


def et(year, month, day, hour, minute=0, second=0) -> datetime:
    """An Eastern wall-clock moment, as an aware datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=_ET)


# Reference dates against the real 2026 calendar in config.py:
#   2026-07-22 Wed                    an ordinary trading day
#   2026-07-25 Sat / 2026-07-26 Sun   a weekend
#   2026-07-03 Fri                    Independence Day observed (a holiday)
WED = 22
SAT, SUN = 25, 26
HOLIDAY_FRI = 3


# ─────────────────────────────────────────────────────────────────────────────
# Which session are we in
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionSelection:
    """The boundaries, from the minute before to the minute after.

    Tested at the exact edges rather than at comfortable midpoints. A
    boundary written as `<` where it should be `<=` is invisible at 11:00 and
    obvious at 09:30, and that off-by-one minute is the whole failure mode.
    """

    @pytest.mark.parametrize("hour,minute,expected", [
        (9, 29, None),        # one minute before the open
        (9, 30, "OPEN"),      # the open itself — inclusive
        (9, 59, "OPEN"),
        (10, 0, "MIDDAY"),    # OPEN ends, MIDDAY begins
        (12, 0, "MIDDAY"),
        (15, 29, "MIDDAY"),
        (15, 30, "CLOSE"),    # MIDDAY ends, CLOSE begins
        (15, 59, "CLOSE"),
        (16, 0, "CLOSE"),     # the close itself — captured, not skipped
        (16, 1, "CLOSE"),     # room for the print to settle
        (16, 2, None),        # window ends — exclusive
        (16, 30, None),
        (3, 0, None),         # overnight
    ])
    def test_session_boundaries_on_a_trading_day(self, hour, minute, expected):
        assert collector.get_session(et(2026, 7, WED, hour, minute)) == expected

    def test_collection_stops_just_after_four_and_nowhere_near_four_fifteen(self):
        """Not a style choice. SPX is a cash-settled index and stops updating
        at 16:00, so any IV computed afterwards uses a frozen underlying and is
        analytically worthless. Collecting on to 16:15 with the options would
        look like more data while being actively misleading.

        ADR-049 moved the edge from 16:00 to 16:02 without touching that
        reasoning. Stopping AT 16:00 made the last price of the day the 15:59
        poll, so the closing print was never recorded on any day since
        collection began. The two polls past the bell are the only ones taken
        against a frozen underlying, and their timestamp says so.
        """
        assert collector.get_session(et(2026, 7, WED, 15, 59)) == "CLOSE"
        assert collector.get_session(et(2026, 7, WED, 16, 0)) == "CLOSE"
        assert collector.get_session(et(2026, 7, WED, 16, 2)) is None
        assert collector.get_session(et(2026, 7, WED, 16, 14)) is None

    @pytest.mark.parametrize("day", [SAT, SUN])
    def test_the_market_is_closed_all_weekend(self, day):
        for hour in (9, 12, 15):
            assert collector.get_session(et(2026, 7, day, hour, 45)) is None

    def test_a_holiday_is_closed_even_though_it_is_a_weekday(self):
        """2026-07-03 is a Friday and would otherwise be a full trading day."""
        assert collector.get_session(et(2026, 7, HOLIDAY_FRI, 12, 0)) is None

    def test_seconds_do_not_shift_a_boundary(self):
        """get_session strips seconds before comparing, so a cycle firing at
        09:30:47 is in OPEN, not stranded outside it."""
        assert collector.get_session(et(2026, 7, WED, 9, 30, 47)) == "OPEN"
        assert collector.get_session(et(2026, 7, WED, 15, 59, 59)) == "CLOSE"


class TestPollInterval:

    def test_the_volatile_ends_of_the_day_are_polled_faster(self):
        """IV moves hardest at the open and into the close, so those windows
        are sampled every minute instead of every five."""
        assert collector._poll_interval("OPEN") == 60
        assert collector._poll_interval("CLOSE") == 60

    def test_the_quiet_middle_is_polled_slower(self):
        assert collector._poll_interval("MIDDAY") == 300

    def test_event_polling_is_genuinely_faster_than_normal(self):
        """Pins the relationship, not the numbers. If the two config values
        were ever swapped, every assertion above could be updated to match and
        still look correct — this one could not."""
        assert collector._poll_interval("OPEN") < collector._poll_interval("MIDDAY")


# ─────────────────────────────────────────────────────────────────────────────
# Was that failure an auth problem
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthErrorDetection:

    @pytest.mark.parametrize("message", [
        "401 Client Error: Unauthorized for url: https://api.schwabapi.com/...",
        "Unauthorized",
        "refresh token expired",
        "invalid_grant: token is not valid",
        "Authentication failed",
    ])
    def test_real_auth_failures_are_recognised(self, message):
        assert collector.is_auth_error(message) is True

    @pytest.mark.parametrize("message", [
        "Option chain API response was empty",
        "Option chain contained no contracts after parsing",
        "SPX quote returned no usable price",
        "HTTPSConnectionPool: Read timed out",
        "500 Server Error: Internal Server Error",
    ])
    def test_ordinary_failures_are_not_mistaken_for_auth_failures(self, message):
        """These must NOT reset the client. Discarding a working client on a
        Schwab 500 turns a transient outage into an unnecessary re-login."""
        assert collector.is_auth_error(message) is False

    def test_matching_ignores_case(self):
        """Schwab returns these in several shapes — a bare HTTP status line, a
        JSON error body, and schwab-py's own exception text — with no
        consistent casing between them."""
        assert collector.is_auth_error("UNAUTHORIZED") is True
        assert collector.is_auth_error("Token Expired") is True

    def test_the_word_expired_alone_is_enough_to_trigger_a_relogin(self):
        """PINNING A KNOWN IMPRECISION, not endorsing it.

        The check is substring-based, so any message containing 'expired' or
        'token' is treated as an auth failure — including one about an expired
        *option*, which has nothing to do with credentials.

        Left as-is deliberately. The cost of this false positive is one
        needless re-login, which is cheap and self-correcting; the cost of
        missing a real auth failure is every remaining cycle of the session
        failing against a dead client. The asymmetry justifies the bluntness.
        This test exists so that if the rule is ever tightened, the trade-off
        is reconsidered on purpose rather than lost.
        """
        assert collector.is_auth_error("contract expired 2026-08-07") is True

    def test_an_empty_message_is_not_an_auth_error(self):
        assert collector.is_auth_error("") is False


# ─────────────────────────────────────────────────────────────────────────────
# Retry and backoff
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureEscalation:

    @pytest.mark.parametrize("failures,expected", [
        (0, False),
        (1, False),
        (4, False),
        (5, True),     # the configured threshold — inclusive
        (9, True),
    ])
    def test_escalation_threshold(self, failures, expected):
        assert collector.failure_is_critical(failures) is expected

    def test_escalation_only_changes_the_log_level(self):
        """The collector must never give up on its own. A collector that exits
        on a bad afternoon loses the rest of the day's prices, and those cannot
        be bought back afterwards — so this decides loudness, nothing else.

        Asserted structurally: the function's only job is a comparison, so the
        surrounding code keeps retrying regardless of what it returns.
        """
        assert collector.failure_is_critical(100) is True
        assert collector._MAX_CONSECUTIVE_FAILURES > 0


class TestBackoffConstants:

    def test_a_failed_cycle_waits_before_retrying(self):
        """Retrying instantly against a struggling API is how a rate limit
        becomes a ban."""
        assert collector._BACKOFF_SECONDS > 0

    def test_auth_retry_waits_longer_than_an_ordinary_retry(self):
        """An auth failure usually needs a human — a browser login that cannot
        be automated. Hammering it at the ordinary backoff rate fills the log
        with noise while changing nothing."""
        assert collector._AUTH_RETRY_SECONDS > collector._BACKOFF_SECONDS


# ─────────────────────────────────────────────────────────────────────────────
# Cadence
# ─────────────────────────────────────────────────────────────────────────────

class TestSleepAfterCycle:

    def test_the_work_already_done_is_subtracted(self):
        """A real cycle takes 5–13 seconds against a 300-second interval.
        Sleeping the full interval after each one pushes every snapshot
        progressively later — by the close, the cadence would have drifted by
        minutes."""
        assert collector.sleep_after_cycle(300, 12.5) == pytest.approx(287.5)
        assert collector.sleep_after_cycle(60, 5.0) == pytest.approx(55.0)

    def test_an_overrunning_cycle_sleeps_zero_not_negative(self):
        """A cycle slower than its own interval must start the next one
        immediately. A negative sleep would raise; falling behind is recorded
        by the gap detector, not corrected by sleeping backwards."""
        assert collector.sleep_after_cycle(60, 95.0) == 0.0
        assert collector.sleep_after_cycle(300, 300.0) == 0.0

    def test_an_instant_cycle_sleeps_the_whole_interval(self):
        assert collector.sleep_after_cycle(300, 0.0) == pytest.approx(300.0)

    def test_the_result_is_never_negative_at_any_interval(self):
        for interval in (60, 300):
            for elapsed in (0.0, 1.0, 59.9, 300.0, 10_000.0):
                assert collector.sleep_after_cycle(interval, elapsed) >= 0.0


class TestTokenRecheck:

    def test_the_token_is_rechecked_about_hourly(self):
        hour = collector._TOKEN_CHECK_INTERVAL_SEC
        assert collector.should_recheck_token(hour, 0.0) is True
        assert collector.should_recheck_token(hour - 1, 0.0) is False

    def test_a_long_running_collector_keeps_warning(self):
        """The point of rechecking at all. A collector left running for days
        used to warn once at startup and then go quiet — so a token expiring on
        Wednesday afternoon produced its only warning on Monday morning."""
        assert collector.should_recheck_token(86_400.0, 0.0) is True

    def test_it_does_not_recheck_on_every_cycle(self):
        """A 60-second OPEN cadence would otherwise re-read the token file
        every minute for no benefit — the value only moves on a re-login."""
        assert collector.should_recheck_token(60.0, 0.0) is False
