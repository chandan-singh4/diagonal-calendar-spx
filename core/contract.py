"""Which of the two options is this?

On the third Friday of each month SPX lists TWO contracts for the same date and
strike: the traditional monthly, which settles at the OPENING price and stops
trading the evening before, and the SPXW weekly, which trades all day and
settles at the CLOSE. Every other expiry lists the weekly alone. Both are
recorded (ADR-046); this module is how the rest of the program refers to one
rather than the other.

THE NAMING RULE, and why it is this way round (Chandan, 2026-08-19):

    "2026-08-21"        the p.m. contract  — the normal case, UNLABELLED
    "2026-08-21 (AM)"   the a.m. contract  — the exception, LABELLED

P.m. is what almost every expiry is, so the label marks the odd one out rather
than the rule. An unlabelled key therefore means "the ordinary contract", which
is what every date in the list except one already was — so old saved positions,
old locks and old journal rows keep meaning exactly what they meant before.
That is not a happy accident; it is the reason for choosing this direction.

WHAT A KEY IS. A display key is a date plus, optionally, which contract. It is
NOT a date, and `date.fromisoformat` will raise on the labelled form. Anything
needing a real calendar date must call `date_of()`. The one caller that deletes
records on the strength of a date — the saved-position sweep in
state/entry_locks.py — goes through `core.expiry`, which does exactly that.

READING THE OLD ROWS. Everything recorded before 2026-08-19 carries no
settlement, and the stored blank stays honestly "not recorded" — it is never
rewritten. But which contract those rows describe IS recoverable, and by rule
rather than by guessing:

  * A weekly only ever listed one contract, so an old row on one is p.m.
  * On a monthly, both were listed and the a.m. contract always won the slot,
    so an old row recorded BEFORE expiry day is a.m.
  * On the monthly's expiry day itself the a.m. option had already settled out
    of the broker's chain, so an old row recorded ON expiry day is p.m.

Verified rather than assumed: the unlabelled 21 Aug rows were matched against
both labelled contracts on open interest — a figure that does not move during
the day, so it identifies a contract independently of price — and 170 of 170
matched a.m., 0 matched p.m. See ADR-046's amendment.
"""
from __future__ import annotations

from datetime import date, timedelta

AM = "AM"
PM = "PM"

#: What a labelled key ends with. One definition — parsing and building both
#: read it from here, so the two can never drift apart.
AM_SUFFIX = " (AM)"


def key(expiry_date: str, settlement: str | None) -> str:
    """Build the display key for one contract.

    `expiry_date` is an ISO date, "YYYY-MM-DD". `settlement` is 'AM', 'PM', or
    None for a row whose settlement was never recorded — and None is treated as
    the ordinary contract, which is what the unlabelled key means.
    """
    return f"{expiry_date}{AM_SUFFIX}" if settlement == AM else expiry_date


def parse(display_key: str) -> tuple[str, str | None]:
    """Split a display key into (expiry_date, settlement).

    Returns settlement as 'AM' for a labelled key and None for a bare one.
    None means "the ordinary contract" — deliberately not 'PM', because it must
    also match the old rows that carry no settlement at all.
    """
    if display_key.endswith(AM_SUFFIX):
        return display_key[: -len(AM_SUFFIX)], AM
    return display_key, None


def date_of(display_key: str) -> str:
    """The calendar date alone, for anything that must do date arithmetic."""
    return parse(display_key)[0]


def is_am(display_key: str) -> bool:
    """Does this key name the a.m. contract?"""
    return display_key.endswith(AM_SUFFIX)


def sort_key(display_key: str) -> tuple[str, int]:
    """Order contracts by when they actually STOP EXISTING.

    On the third Friday the a.m. contract settles against the OPENING price and
    the p.m. one against the close, so the a.m. contract is the earlier of the
    two — several hours earlier, on the same date. Listing it first is therefore
    the same rule the rest of the list already follows, not a cosmetic
    preference: a plain text sort puts "2026-08-21 (AM)" after "2026-08-21",
    which reads as the later contract and is the wrong way round.

    Use this anywhere display keys are sorted for a human to read or to pick a
    back leg from.
    """
    expiry_date, settlement = parse(display_key)
    return (expiry_date, 0 if settlement == AM else 1)


def is_third_friday(expiry_date: str) -> bool:
    """Is this date a monthly expiry — the only kind with two contracts?

    The third Friday is the one that falls on the 15th-21st: there are exactly
    seven days in that window, so precisely one of them is a Friday. No holiday
    table is involved, because this asks which date the contract is listed for,
    not whether the market opens that day.
    """
    d = date.fromisoformat(expiry_date)
    return d.weekday() == 4 and 15 <= d.day <= 21


def opex_of(day: date) -> date:
    """The third Friday of `day`'s own month.

    THE MONTHLY EXPIRY IS THE CALENDAR THE OPTIONS MARKET ACTUALLY KEEPS.
    Weeklies expire every Friday, but the big open interest, the index
    rebalances and the quarterly roll all land on the third Friday, so
    "this cycle" means "up to and including the next monthly", not "the next
    thirty days". Grouping expiries any other way would cut a cycle in half
    and put the contract that matters most in the wrong bucket.

    Derived rather than tabulated: `is_third_friday` above says the third
    Friday is the unique Friday in the 15th-21st window, so walking that
    window finds it with no holiday table and no year-by-year list to
    maintain.
    """
    for dom in range(15, 22):
        candidate = date(day.year, day.month, dom)
        if candidate.weekday() == 4:
            return candidate
    raise AssertionError("a seven-day window always contains one Friday")


def next_opex(day: date) -> date:
    """The next monthly expiry on or after `day`.

    Once `day` is past this month's third Friday the current cycle is next
    month's, which is why this rolls forward rather than clamping. December
    rolls the year as well as the month; `date` will not do that arithmetic
    for us, so it is done explicitly here rather than by adding 30 days and
    hoping.
    """
    this = opex_of(day)
    if this >= day:
        return this
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return opex_of(date(year, month, 1))


def week_end(day: date) -> date:
    """The Friday of `day`'s own trading week.

    Saturday and Sunday belong to the week that has just ENDED by the
    calendar, but to a trader on a Sunday evening "this week" is the one
    about to start. So the weekend rolls forward to the coming Friday rather
    than backwards to the one just gone -- otherwise a Sunday would show an
    empty list, every expiry in it having already passed.
    """
    if day.weekday() >= 5:                      # Sat, Sun
        return day + timedelta(days=(4 - day.weekday()) + 7)
    return day + timedelta(days=4 - day.weekday())


#: The expiry filters the Gamma tab offers, in the order it lists them, each
#: paired with the function giving its LAST included date. Every one is a
#: CUMULATIVE upper bound -- "This OpEx Cycle" holds the weeklies before the
#: monthly as well as the monthly -- because that is how a trader reads the
#: phrase, and because a filter that excluded the near dates would hide the
#: 0DTE contract this dashboard looks at most.
EXPIRY_FILTERS: list[tuple[str, str]] = [
    ("this_week", "This Week"),
    ("next_2_weeks", "Next 2 Weeks"),
    ("this_opex_cycle", "This OpEx Cycle"),
    ("next_2_opex_cycles", "Next 2 OpEx Cycles"),
]


def filter_cutoffs(today: date) -> dict[str, date]:
    """The last date each filter in `EXPIRY_FILTERS` includes.

    ONE DEFINITION, TWO SCREENS. The React tab does not decide what "this
    week" means; it receives, per expiry, the list of filters that expiry
    belongs to. A browser computing this itself would do it in the VIEWER'S
    timezone off the viewer's clock, so a trader in London opening the tab at
    01:00 would see the next day's cycle -- plausible, wrong, and silent.
    """
    first = next_opex(today)
    after = next_opex(first + timedelta(days=1))
    return {
        "this_week": week_end(today),
        "next_2_weeks": week_end(today) + timedelta(days=7),
        "this_opex_cycle": first,
        "next_2_opex_cycles": after,
    }


def filters_for(expiry_date: str, today: date) -> list[str]:
    """Which of `EXPIRY_FILTERS` this expiry falls inside.

    Membership rather than a bound, so the browser filters with a set lookup
    and never compares two dates itself. An expiry already past -- the record
    holds them, and a snapshot replayed from last week is full of them -- is
    in no bucket at all: it is not in "this week" by any reading a trader
    would accept.
    """
    if expiry_date is None:
        return []
    try:
        d = date.fromisoformat(date_of(expiry_date))
    except ValueError:
        return []
    if d < today:
        return []
    cutoffs = filter_cutoffs(today)
    return [k for k, _label in EXPIRY_FILTERS if d <= cutoffs[k]]


def legacy_clause(expiry_date: str, settlement: str | None,
                  *, rows: str, snaps: str) -> str:
    """SQL matching the OLD unlabelled rows that belong to this contract.

    Returns a predicate over rows whose `settlement` is NULL, deciding by the
    rule in this module's docstring. `rows` and `snaps` are the table aliases to
    read `settlement`/`expiry_date` and the snapshot timestamp from.

    Returns the literal `0` — never true — when no old row can belong to this
    contract, which is the case for the a.m. side of an ordinary weekly, since
    no a.m. contract was ever listed there to record.
    """
    old = f"{rows}.settlement IS NULL"
    if not is_third_friday(expiry_date):
        # One contract ever existed here, so every old row is the ordinary one.
        return "0" if settlement == AM else old
    if settlement == AM:
        return f"({old} AND date({snaps}.snapshot_timestamp) < {rows}.expiry_date)"
    return f"({old} AND date({snaps}.snapshot_timestamp) = {rows}.expiry_date)"


def match_clause(expiry_date: str, settlement: str | None,
                 *, rows: str, snaps: str) -> str:
    """SQL selecting every row belonging to one contract, old rows included.

    This is the predicate to AND into any history query that has been handed a
    display key. It deliberately does not filter on the expiry date itself —
    the caller already does that, and doing it twice invites the two from
    drifting apart.
    """
    labelled = (f"{rows}.settlement = 'AM'" if settlement == AM
                else f"{rows}.settlement = 'PM'")
    return f"({labelled} OR {legacy_clause(expiry_date, settlement, rows=rows, snaps=snaps)})"
