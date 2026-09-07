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


# When the tab stops asking about today and starts asking about tomorrow.
#
# 8:00 PM Eastern, which is Chandan's number ("after eight PM, the default
# should be for the next DTE"), not a derived one. It is deliberately LATER
# than the 4:15 PM close: the hours after the bell are when the session just
# finished is still the thing being read, and rolling the default forward at
# 4:16 would take today's chart away while he was still looking at it.
ROLL_FORWARD = time(20, 0)


def default_scope(options, now: datetime, session_date: date) -> str | None:
    """Which expiry the Gamma tab should open on, given the clock.

    WHY THIS IS NOT THREE LINES IN THE BROWSER. It is a comparison of a
    wall-clock time against a market timezone, and a browser doing it would do
    it in the VIEWER'S timezone -- the same silent class of bug DEBT-030
    records for timestamps. A laptop in London would roll the default forward
    five hours early and nothing on screen would say so. The rule is here, the
    answer travels on the response, and the tab reads it.

    THE ANSWER IS A DISPLAY KEY, not a date, so the third Friday's a.m. and
    p.m. contracts stay apart (ADR-046/047).

    TWO REASONS TO ROLL FORWARD, and the second is the one that catches the
    case the stated rule misses. The stated one is the clock: past 8 PM the
    day is over and the next expiry is the live question. The other is that
    the DATA may already be older than today -- over a weekend, or on a
    holiday, or with the collector stopped, the newest session on disk is not
    today's. Asking for "0 DTE" then means an expiry that has already settled:
    a chart of a contract that no longer exists. Judging by the clock alone
    would show that all Saturday and Sunday.

    `options` is `expiry_board`'s list, or anything carrying `key` and `dte`.
    Returns None for an empty board -- the tab then opens on the whole board,
    which is the question it asked before this rule existed.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("default_scope needs a timezone-aware `now`")
    rows = [o for o in options if o.get("dte") is not None]
    if not rows:
        return None

    market_now = now.astimezone(ZoneInfo(MARKET_TIMEZONE))
    roll = (session_date < market_now.date()
            or market_now.time() >= ROLL_FORWARD)

    # THE NEAREST, NOT THE FIRST IN THE LIST. The board arrives in date order
    # today, but that is the board's business; a default that silently depends
    # on someone else's sort order breaks the day the sort changes.
    if roll:
        ahead = [o for o in rows if o["dte"] > 0]
        return min(ahead, key=lambda o: o["dte"])["key"] if ahead else None

    today = [o for o in rows if o["dte"] == 0]
    # NO 0 DTE IS A REAL STATE -- SPX lists one most days but not every day,
    # and inventing the next one instead would answer a different question
    # from the one asked. The whole board is the honest fallback.
    return today[0]["key"] if today else None


def countdown_anchor(session_date: date, now: datetime, *,
                     is_latest: bool) -> date:
    """The date "N DTE" should be counted FROM.

    THE PROBLEM IT SOLVES (Chandan, 2026-09-06). On Sunday the 6th the picker
    showed the 8th as "4 DTE", because the newest snapshot was Friday's and
    every countdown on the board was measured from Friday. The number was
    right about the data and wrong about the reader: "given that we have the
    live time on the dashboard, it should use that and automatically adjust
    all this date. So after twelve midnight, the zero DTE will be minus one
    DTE, and 0DTE will correctly categorize onto the next."

    WHY IT IS NOT SIMPLY `today`. api/computed.snapshot_date exists because
    measuring a REPLAYED snapshot against today's calendar is wrong in a way
    that empties the screen: every expiry on a chain from last Tuesday has
    since passed, so every filter window comes back empty and every countdown
    is negative. That behaviour is worth keeping, and `is_latest` is what
    separates the two cases -- the board on screen is either the current one,
    where the reader's own calendar is the right frame, or a historical one,
    where the day it was taken is.

    NEVER EARLIER THAN THE SESSION ITSELF. A machine whose clock is behind, or
    a snapshot stamped slightly ahead, would otherwise produce countdowns that
    grow as the day passes. `max` is a floor, not a correction: it says this
    rule may move a countdown FORWARD as time passes and never backward.

    `now` must be timezone-aware and is read in market time, for the reason
    every other clock rule in this module gives: the alternative resolves in
    whoever is looking's timezone (DEBT-030).
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("countdown_anchor needs a timezone-aware `now`")
    if not is_latest:
        return session_date
    return max(session_date, now.astimezone(ZoneInfo(MARKET_TIMEZONE)).date())
