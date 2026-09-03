"""Which market session it is, and how often prices are expected during it.

WHY THIS IS ITS OWN MODULE. This logic began in `collector.py`, where it was
the collector's private business: how long to sleep between polls. Three
callers now need the same answer, and they need it to be the SAME answer:

  - `collector.py` — how long to sleep.
  - `ui/header.py` — how old the newest price has to be before the header
    calls it late. Chandan's threshold ("over 5 minutes midday, over a minute
    in the first and last half hour") is not a second policy that happens to
    agree with the collector's; it IS the collector's polling interval. Two
    copies of a number that must agree is a number that will eventually
    disagree, and the disagreement shows up as a dashboard that either cries
    wolf or stays quiet through a real outage.
  - `scripts/watchdog.py` — the same question again, from outside the
    dashboard, which is the whole point of the watchdog (M3.4).

PURE, AND HANDED EVERYTHING. `core/` may not import `config`, so the holiday
set and the two intervals arrive as arguments rather than as hidden globals.
That is not ceremony: it is what lets the tests below drive a Thanksgiving or
a 15:29:59 boundary without touching a settings file.

TIMEZONE. Every function here takes EASTERN time and trusts the caller to have
converted. There is no conversion inside, because a function that silently
reinterprets a naive datetime is exactly how a market-hours check ends up
being wrong for one hour twice a year.
"""
from __future__ import annotations

from datetime import date, datetime, time

# Session boundaries, Eastern. Moved from collector.py unchanged.
OPEN_START = time(9, 30)    # OPEN session begins
OPEN_END   = time(10, 0)    # OPEN ends / MIDDAY begins
MIDDAY_END = time(15, 30)   # MIDDAY ends / CLOSE begins
CLOSE_END  = time(16, 2)    # CLOSE ends — two minutes PAST the equity close,
                            # on purpose, to capture the settled closing print
                            # (ADR-049). See session_of for why not 16:00.

# The sessions that poll at the fast interval: the first and last half hour,
# when the price moves most and a five-minute gap loses the most.
EVENT_SESSIONS = ("OPEN", "CLOSE")


def is_trading_day(d: date, holidays: set[str]) -> bool:
    """True if d is a weekday and not one of the given market holidays.

    `holidays` is a set of ISO date strings — `config.MARKET_HOLIDAYS`.
    """
    return d.weekday() < 5 and d.isoformat() not in holidays


def session_of(now_et: datetime, holidays: set[str]) -> str | None:
    """The market session containing `now_et`, or None if the market is shut.

      'OPEN'   → 09:30–10:00 ET
      'MIDDAY' → 10:00–15:30 ET
      'CLOSE'  → 15:30–16:02 ET
      None     → overnight, weekend, or holiday

    Collection stops at 16:02 ET — not 16:15, and no longer 16:00 (ADR-049).

    It used to stop at 16:00, on the reasoning that SPX is a cash-settled index
    and freezes at the equity close, so IVs computed later use a frozen
    underlying and are analytically unreliable. That reasoning still holds, and
    it is still why collection does not run to 16:15 with the options. **What it
    got wrong was the boundary.** Stopping AT 16:00 meant the last price of the
    day was the 15:59 poll: the close itself was never recorded, on any day,
    since collection began. Chandan spotted it.

    Why 16:02 and not 16:01. The SPX close is not struck at 16:00:00 — the index
    is computed from its component stocks' closing auction prices, and those
    print over the following seconds. A poll at 16:00 would very likely still
    carry the 15:59:59 level and record a "close" that is not the close. Two
    minutes buys the settled print for the cost of one extra poll.

    **The two extra polls are the only ones taken against a frozen underlying,
    and they are identifiable by their timestamp alone** — at or after 16:00 ET.
    Nothing needed a new session name or a schema change to tell them apart.
    Anything wanting a live-underlying-only series filters on that; anything
    wanting the closing price now has one.

    **None is not a fault.** Everything reading this must treat a closed market
    as the expected state, or the dashboard glows red every evening and the
    watchdog emails Chandan every night at midnight — and an alarm that cries
    wolf nightly is an alarm nobody reads on the morning it is right.
    """
    if not is_trading_day(now_et.date(), holidays):
        return None

    # Seconds stripped for clean boundary comparison, as in the original.
    t = now_et.time().replace(second=0, microsecond=0)

    if OPEN_START <= t < OPEN_END:
        return "OPEN"
    if OPEN_END <= t < MIDDAY_END:
        return "MIDDAY"
    if MIDDAY_END <= t < CLOSE_END:
        return "CLOSE"
    return None


def expected_interval(session: str | None, event_secs: int, normal_secs: int) -> int | None:
    """How many seconds should pass between prices during `session`.

    None when the market is shut — meaning "no expectation", not "zero
    seconds". Callers must branch on it rather than compare against it.
    """
    if session is None:
        return None
    return event_secs if session in EVENT_SESSIONS else normal_secs
