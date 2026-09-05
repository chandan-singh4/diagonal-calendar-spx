"""The display key: a date plus which of the two third-Friday contracts.

The rule under test is Chandan's, 2026-08-19: the p.m. contract is UNLABELLED
because it is the normal case, and the a.m. contract carries "(AM)" because it
is the exception. See core/contract.py and ADR-046.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from core import contract

THIRD_FRIDAY = "2026-08-21"
WEEKLY = "2026-08-24"


# ---------------------------------------------------------------------------
# Building and parsing
# ---------------------------------------------------------------------------

def test_the_pm_contract_is_unlabelled():
    """The whole point of this direction: the ordinary contract keeps the bare
    date, so every saved position and journal row written before today still
    means what it meant."""
    assert contract.key(THIRD_FRIDAY, "PM") == "2026-08-21"


def test_the_am_contract_is_labelled():
    assert contract.key(THIRD_FRIDAY, "AM") == "2026-08-21 (AM)"


def test_an_unrecorded_settlement_is_treated_as_the_ordinary_contract():
    """Old rows carry no settlement. They must land on the unlabelled key, not
    invent a third one."""
    assert contract.key(THIRD_FRIDAY, None) == "2026-08-21"


@pytest.mark.parametrize("settlement", ["AM", "PM", None])
def test_parsing_undoes_building(settlement):
    built = contract.key(THIRD_FRIDAY, settlement)
    parsed_date, parsed_settlement = contract.parse(built)
    assert parsed_date == THIRD_FRIDAY
    assert parsed_settlement == ("AM" if settlement == "AM" else None)


def test_parse_reports_none_rather_than_pm_for_a_bare_key():
    """None means 'the ordinary contract', which must also match the old rows
    that carry no settlement at all. Reporting 'PM' would exclude them."""
    assert contract.parse("2026-08-21") == ("2026-08-21", None)


def test_date_of_recovers_a_real_date_from_a_labelled_key():
    """The guard on the one path that deletes records: a labelled key is NOT a
    date and date.fromisoformat raises on it."""
    with pytest.raises(ValueError):
        date.fromisoformat("2026-08-21 (AM)")
    assert date.fromisoformat(contract.date_of("2026-08-21 (AM)")) == date(2026, 8, 21)


def test_date_of_leaves_a_bare_key_alone():
    assert contract.date_of("2026-08-24") == "2026-08-24"


def test_is_am():
    assert contract.is_am("2026-08-21 (AM)")
    assert not contract.is_am("2026-08-21")


# ---------------------------------------------------------------------------
# Which dates have two contracts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("d", [
    "2026-08-21",  # third Friday, the one this session was about
    "2026-09-18",
    "2026-10-16",
    "2026-01-16",  # 16th — the earliest a third Friday can fall
    "2026-05-15",  # 15th
])
def test_third_fridays_are_recognised(d):
    assert contract.is_third_friday(d)


@pytest.mark.parametrize("d", [
    "2026-08-24",  # a Monday
    "2026-08-28",  # the FOURTH Friday — the trap this rule exists to avoid
    "2026-08-14",  # the second Friday
    "2026-08-07",
    "2026-08-22",  # the Saturday after
])
def test_other_dates_are_not(d):
    assert not contract.is_third_friday(d)


def test_the_third_friday_rule_agrees_with_counting_fridays():
    """Cross-check the 15th-21st shortcut against the definition it stands in
    for, over four years. A shortcut nobody has checked is just an assertion."""
    for year in (2025, 2026, 2027, 2028):
        for month in range(1, 13):
            fridays = [
                date(year, month, day)
                for day in range(1, 32)
                if _valid(year, month, day)
                and date(year, month, day).weekday() == 4
            ]
            third = fridays[2]
            for d in fridays:
                assert contract.is_third_friday(d.isoformat()) == (d == third), d


def _valid(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Reading the old unlabelled rows
#
# These run the generated SQL against a real (temporary) database rather than
# comparing strings, because a predicate that reads correctly and selects the
# wrong rows is exactly the failure this session already shipped once.
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE snapshots (
            snapshot_id INTEGER PRIMARY KEY, snapshot_timestamp TEXT);
        CREATE TABLE option_rows (
            snapshot_id INTEGER, expiry_date TEXT, strike REAL,
            right TEXT, settlement TEXT, tag TEXT);
    """)
    return c


def _add(c, snap_id, taken_on, expiry, settlement, tag):
    c.execute("INSERT OR IGNORE INTO snapshots VALUES (?, ?)",
              (snap_id, f"{taken_on} 15:30:00"))
    c.execute("INSERT INTO option_rows VALUES (?, ?, 7000, 'C', ?, ?)",
              (snap_id, expiry, settlement, tag))


def _selected(c, expiry, settlement):
    clause = contract.match_clause(expiry, settlement, rows="o", snaps="s")
    sql = (f"SELECT o.tag FROM option_rows o JOIN snapshots s USING(snapshot_id) "
           f"WHERE o.expiry_date = ? AND {clause} ORDER BY o.tag")
    return [r[0] for r in c.execute(sql, (expiry,))]


def test_a_monthly_splits_its_old_rows_by_when_they_were_taken(conn):
    """The rule proved on real data: before expiry day the stored row is the
    a.m. contract; on expiry day itself the a.m. option has already settled out
    of the chain, so the row is the p.m. one."""
    _add(conn, 1, "2026-08-19", THIRD_FRIDAY, None, "old-before")
    _add(conn, 2, "2026-08-21", THIRD_FRIDAY, None, "old-on-the-day")
    _add(conn, 3, "2026-08-20", THIRD_FRIDAY, "AM", "new-am")
    _add(conn, 4, "2026-08-20", THIRD_FRIDAY, "PM", "new-pm")

    assert _selected(conn, THIRD_FRIDAY, "AM") == ["new-am", "old-before"]
    assert _selected(conn, THIRD_FRIDAY, None) == ["new-pm", "old-on-the-day"]


def test_a_weekly_gives_every_old_row_to_the_ordinary_contract(conn):
    """Only one contract ever existed on a weekly, so 'before expiry day' does
    NOT mean a.m. here. Getting this wrong would hand most of the history to a
    contract that never existed."""
    _add(conn, 1, "2026-08-19", WEEKLY, None, "old-before")
    _add(conn, 2, "2026-08-24", WEEKLY, None, "old-on-the-day")
    _add(conn, 3, "2026-08-20", WEEKLY, "PM", "new-pm")

    assert _selected(conn, WEEKLY, None) == ["new-pm", "old-before", "old-on-the-day"]


def test_a_weekly_has_no_am_contract_at_all(conn):
    """There is no a.m. option on a weekly, so asking for one returns nothing
    rather than quietly returning the p.m. rows."""
    _add(conn, 1, "2026-08-19", WEEKLY, None, "old-before")
    _add(conn, 2, "2026-08-20", WEEKLY, "PM", "new-pm")

    assert _selected(conn, WEEKLY, "AM") == []


def test_every_old_row_lands_on_exactly_one_of_the_two_contracts(conn):
    """No row may be dropped, and none may be counted twice — the two clauses
    have to partition the history, not merely overlap it plausibly."""
    for i, taken_on in enumerate(["2026-08-18", "2026-08-19", "2026-08-20",
                                  "2026-08-21"], start=1):
        _add(conn, i, taken_on, THIRD_FRIDAY, None, f"old-{taken_on}")

    am = set(_selected(conn, THIRD_FRIDAY, "AM"))
    pm = set(_selected(conn, THIRD_FRIDAY, None))
    assert am | pm == {f"old-{d}" for d in
                       ["2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]}
    assert am & pm == set()
