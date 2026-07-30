"""When is an option position over?

One rule, one place. The clock arrives as an argument rather than being read
here, so this can be tested against a fixed instant — which matters more than
usual, because the only caller DELETES records on the strength of the answer
(ADR-039). A rule that fires one day early destroys a lock on a live position.

Pinned by tests/test_entry_lock_expiry.py, including both sides of the minute.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

# SPX options settle at the close, but the trader's interest in the position
# ends at 4:15 PM Eastern — the cash-index close, not the 4:00 PM equity bell.
MARKET_CLOSE = time(16, 15)
MARKET_TIMEZONE = "America/New_York"


def is_expired(front_expiry: str, now: datetime) -> bool:
    """Is a diagonal whose FRONT leg expires on `front_expiry` finished at `now`?

    front_expiry is an ISO date string, "YYYY-MM-DD" — the form the chain and
    the locks file both already use.

    `now` must be timezone-aware. It is converted to New York before comparing,
    so a machine set to any other timezone gets the same answer; a naive
    datetime is a caller bug and raises rather than silently comparing wall
    clocks from two different places.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("is_expired needs a timezone-aware `now`")

    expiry_date = date.fromisoformat(front_expiry)
    market_now = now.astimezone(ZoneInfo(MARKET_TIMEZONE))

    if market_now.date() > expiry_date:
        return True
    if market_now.date() < expiry_date:
        return False
    return market_now.time() >= MARKET_CLOSE
