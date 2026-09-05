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

from datetime import date

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
