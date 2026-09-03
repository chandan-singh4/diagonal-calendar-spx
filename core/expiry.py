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

from core import contract

# SPX options settle at the close, but the trader's interest in the position
# ends at 4:15 PM Eastern — the cash-index close, not the 4:00 PM equity bell.
MARKET_CLOSE = time(16, 15)

# The a.m. third-Friday contract is the exception: it settles against the
# OPENING print, so its value is decided at 9:30 AM Eastern on the expiry date
# and nothing that happens afterwards can change it (BUG-027, ADR-048).
MARKET_OPEN = time(9, 30)

MARKET_TIMEZONE = "America/New_York"


def is_expired(front_expiry: str, now: datetime) -> bool:
    """Is a diagonal whose FRONT leg expires on `front_expiry` finished at `now`?

    front_expiry is a display key: either a bare ISO date, "YYYY-MM-DD", or the
    a.m. contract's labelled form, "YYYY-MM-DD (AM)" (core.contract). It is NOT
    always a date, so the date is taken with `contract.date_of` rather than by
    parsing the whole string — `date.fromisoformat` RAISES on the labelled form.
    That failure would be silent: the caller catches ValueError and keeps the
    lock, so a labelled position would simply never expire and the locks file
    would grow forever, which is the thing ADR-039 exists to prevent.

    The two contracts finish at DIFFERENT moments (BUG-027, closed by ADR-048).
    The p.m. contract trades all day and is over at 4:15 PM on the expiry date.
    The a.m. contract settles against the OPENING print, so it is over at 9:30
    AM that same morning — its value is fixed by then and no later trading can
    change it.

    Chandan chose the opening print over the contract's true last trade, which
    is the evening BEFORE. Both are defensible; the open is the later of the
    two, and later is the safe direction here. This rule is the only one in the
    program whose answer DELETES a record (ADR-039), so an hour held too long
    costs a stale row in a popover, while an hour cut too early destroys the
    entry price a live position is measured against.

    `now` must be timezone-aware. It is converted to New York before comparing,
    so a machine set to any other timezone gets the same answer; a naive
    datetime is a caller bug and raises rather than silently comparing wall
    clocks from two different places.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("is_expired needs a timezone-aware `now`")

    expiry_date = date.fromisoformat(contract.date_of(front_expiry))
    market_now = now.astimezone(ZoneInfo(MARKET_TIMEZONE))

    if market_now.date() > expiry_date:
        return True
    if market_now.date() < expiry_date:
        return False

    cutoff = MARKET_OPEN if contract.is_am(front_expiry) else MARKET_CLOSE
    return market_now.time() >= cutoff
