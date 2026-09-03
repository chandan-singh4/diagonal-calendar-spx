"""An entry lock stops existing once the trade it tracks is over.

WHY THIS IS A DELETE AND NOT A HIDE
  Chandan's call, 2026-07-30: "I don't think I'd look once it expires so no
  point in archiving it." So the record leaves the file. That makes the clock
  rule below load-bearing in a way a display filter would not be — a rule that
  fires one day early destroys a lock on a position still open. Hence the
  boundary cases here are tested to the minute, from both sides.

WHAT IS PINNED
  1. The rule itself, against a fixed clock, with no files involved
  2. The moment, to the minute, from both sides — and it differs by contract:
     the p.m. contract at 4:15 PM New York on expiry day (4:14 keeps, 4:15
     deletes), the a.m. third-Friday contract at 9:30 AM the same morning,
     because it settles on the opening print (ADR-048, closing BUG-027)
  3. The purge writes the survivors back and leaves them otherwise untouched
  4. Every read of the locks file goes through the purge, not just the popover

Number 4 is the one that stops this regressing quietly. Filtering the popover
list alone would look correct on screen while every other reader — the chart,
the lock lookup for the current combo — still saw the dead record.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core import expiry as expiry_rule
from state import entry_locks

NY = ZoneInfo("America/New_York")


def _at(date_str: str, hour: int, minute: int) -> datetime:
    y, m, d = (int(p) for p in date_str.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=NY)


# ─────────────────────────────────────────────────────────────────────────────
# 1. The rule, with no file anywhere near it
# ─────────────────────────────────────────────────────────────────────────────

def test_a_front_expiry_in_the_past_is_expired():
    assert expiry_rule.is_expired("2026-07-17", _at("2026-07-30", 9, 30)) is True


def test_a_front_expiry_in_the_future_is_not_expired():
    assert expiry_rule.is_expired("2026-08-12", _at("2026-07-30", 9, 30)) is False


def test_the_morning_of_expiry_day_is_not_expired():
    """The position is still live and still worth watching all session."""
    assert expiry_rule.is_expired("2026-07-30", _at("2026-07-30", 9, 30)) is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. The minute it turns over
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,minute,expected", [
    (16, 14, False),   # one minute before — still live
    (16, 15, True),    # the moment itself
    (16, 16, True),    # after
    (23, 59, True),    # end of expiry day
])
def test_four_fifteen_new_york_is_the_boundary(hour, minute, expected):
    assert expiry_rule.is_expired("2026-07-30", _at("2026-07-30", hour, minute)) is expected


def test_the_boundary_is_new_york_time_not_the_wall_clock_it_arrives_on():
    """Both cases below are chosen so that CONVERTING the incoming time and
    merely RELABELLING it as New York give opposite answers — an earlier
    version of this test used times where both approaches agreed, so it passed
    against code that ignored the timezone entirely.

    1:30 PM in Los Angeles is 4:30 PM in New York: past the close, expired.
    Relabel it instead and you get 1:30 PM New York — still trading.
    """
    la = ZoneInfo("America/Los_Angeles")
    half_one_in_la = datetime(2026, 7, 30, 13, 30, tzinfo=la)
    assert expiry_rule.is_expired("2026-07-30", half_one_in_la) is True

    # 7:00 PM UTC is 3:00 PM in New York — an hour and a quarter of trading
    # left. Relabel it and you get 7:00 PM New York, long closed.
    seven_pm_utc = datetime(2026, 7, 30, 19, 0, tzinfo=ZoneInfo("UTC"))
    assert expiry_rule.is_expired("2026-07-30", seven_pm_utc) is False


def test_a_clock_with_no_timezone_is_refused():
    """Silently assuming the machine is in New York is how a lock on a live
    position gets deleted from a laptop in another timezone."""
    with pytest.raises(ValueError):
        expiry_rule.is_expired("2026-07-30", datetime(2026, 7, 30, 16, 30))


# ─────────────────────────────────────────────────────────────────────────────
# 3. The purge — what leaves the file, and what must not
# ─────────────────────────────────────────────────────────────────────────────

def _seed(state_dir, fronts: dict[str, str]) -> None:
    """fronts maps lock key -> front expiry. Everything else is filler."""
    locks = {
        k: {
            "lock_id": f"id-{k}",
            "front_expiry": front,
            "back_expiry": "2026-12-31",
            "put_strike": 7500.0,
            "call_strike": 7550.0,
            "entry_diagonal_mark": 11.25,
            "locked_at": "2026-07-01T13:50:31.345649-04:00",
            "mode": "monitor_only",
            "journal_trade_id": None,
        }
        for k, front in fronts.items()
    }
    (state_dir / entry_locks.FILENAME).write_text(json.dumps(locks), encoding="utf-8")


def test_the_purge_removes_only_the_expired_records(tmp_path):
    _seed(tmp_path, {
        "dead":  "2026-07-17",
        "alive": "2026-08-12",
        "today_before_close": "2026-07-30",
    })

    removed = entry_locks.purge_expired(tmp_path, now=_at("2026-07-30", 9, 30))

    assert removed == ["dead"]
    assert set(entry_locks.load(tmp_path)) == {"alive", "today_before_close"}


def test_the_purge_leaves_a_surviving_record_byte_for_byte_intact(tmp_path):
    """Deleting a neighbour must not quietly rewrite the entry price or the
    lock_id of the one that stays — that is the fill price the chart is
    measured against."""
    _seed(tmp_path, {"dead": "2026-07-17", "alive": "2026-08-12"})
    before = entry_locks.load(tmp_path)["alive"]

    entry_locks.purge_expired(tmp_path, now=_at("2026-07-30", 9, 30))

    assert entry_locks.load(tmp_path)["alive"] == before


def test_the_purge_does_not_rewrite_the_file_when_nothing_expired(tmp_path):
    """No expired locks is the common case — roughly every run. Rewriting a
    file 126 times a day for no reason is 126 chances to lose it."""
    _seed(tmp_path, {"alive": "2026-08-12"})
    path = tmp_path / entry_locks.FILENAME
    stamp = path.stat().st_mtime_ns
    raw = path.read_bytes()

    removed = entry_locks.purge_expired(tmp_path, now=_at("2026-07-30", 9, 30))

    assert removed == []
    assert path.read_bytes() == raw
    assert path.stat().st_mtime_ns == stamp


def test_the_purge_survives_a_lock_with_an_unreadable_expiry(tmp_path):
    """A malformed date must not take the whole popover down with it, and must
    not be deleted either — deleting what you cannot read is how data goes
    missing. It stays, visibly, for a human to deal with."""
    _seed(tmp_path, {"alive": "2026-08-12"})
    locks = entry_locks.load(tmp_path)
    locks["broken"] = dict(locks["alive"], front_expiry="not-a-date")
    entry_locks.save(tmp_path, locks)

    removed = entry_locks.purge_expired(tmp_path, now=_at("2026-07-30", 9, 30))

    assert removed == []
    assert set(entry_locks.load(tmp_path)) == {"alive", "broken"}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Nobody reads the locks file around the purge
# ─────────────────────────────────────────────────────────────────────────────

def test_the_app_purges_before_it_reads_locks():
    """The popover, the current-combo lookup and the chart all go through
    _load_entry_locks. The purge belongs inside it, so there is exactly one
    place to get this right rather than one per caller.

    Re-pointed at services/sidecars.py in M2 step 2.5, when the sidecar
    wrappers left app.py. The function did not change — only its address —
    and this test failed loudly on its own anchor rather than passing
    vacuously, which is the whole reason the anchor assertion is here.
    """
    src = (Path(__file__).resolve().parents[1]
           / "services" / "sidecars.py").read_text(encoding="utf-8")
    body = re.search(
        r"\ndef _load_entry_locks\(\)[^\n]*\n(.*?)(?=\ndef |\n@)", src, re.S,
    )
    assert body, "_load_entry_locks moved or changed shape — re-point this test"
    assert "purge_expired" in body.group(1), (
        "_load_entry_locks no longer purges; expired locks will reappear "
        "everywhere the file is read"
    )


# ---------------------------------------------------------------------------
# The a.m. contract's labelled key (BUG-023 display half)
#
# `is_expired` is the only rule whose answer causes records to be DELETED
# (ADR-039). A display key for the a.m. contract is "2026-08-21 (AM)", which is
# not a date — and the failure would be silent rather than loud, because
# entry_locks catches ValueError and KEEPS the lock. So a labelled position
# would never expire and the locks file would grow forever: exactly the leak
# that rule was written to stop.
# ---------------------------------------------------------------------------

AM_KEY = "2026-08-21 (AM)"
BARE_KEY = "2026-08-21"


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NY)


def test_a_labelled_position_expires_rather_than_lingering_forever():
    assert expiry_rule.is_expired(AM_KEY, _et(2026, 8, 22, 10, 0)) is True


def test_a_labelled_position_is_not_expired_the_day_before():
    assert expiry_rule.is_expired(AM_KEY, _et(2026, 8, 20, 10, 0)) is False


def test_the_two_contracts_no_longer_expire_at_the_same_moment():
    """REPLACES the pin that said they did. That pin recorded BUG-027 as a
    deliberate inaccuracy awaiting a decision; Chandan made the decision on
    2026-09-03 (ADR-048), so the rule and its pin change together — the old
    test is not failing, it is describing behaviour that is no longer wanted.

    Mid-morning on expiry day is the whole difference: the a.m. contract has
    already settled on the opening print, the p.m. one has a full session left.
    """
    mid_morning = _et(2026, 8, 21, 11, 0)
    assert expiry_rule.is_expired(AM_KEY, mid_morning) is True
    assert expiry_rule.is_expired(BARE_KEY, mid_morning) is False


def test_the_am_contract_expires_at_the_opening_print_to_the_minute():
    """9:29 keeps the marker, 9:30 deletes it. Both sides of the minute,
    because this answer deletes a record (ADR-039)."""
    assert expiry_rule.is_expired(AM_KEY, _et(2026, 8, 21, 9, 29)) is False
    assert expiry_rule.is_expired(AM_KEY, _et(2026, 8, 21, 9, 30)) is True


def test_the_am_contract_survives_the_evening_before_its_expiry_date():
    """The contract's true last trade is the previous evening, and Chandan
    chose NOT to model that — the open is the later, safer cutoff. A marker
    deleted here would be deleted while the position is still live."""
    assert expiry_rule.is_expired(AM_KEY, _et(2026, 8, 20, 16, 15)) is False
    assert expiry_rule.is_expired(AM_KEY, _et(2026, 8, 20, 23, 59)) is False


def test_the_pm_contract_is_untouched_by_the_am_change():
    """The regression that would matter most: p.m. is ~94% of all expiries, so
    an a.m. cutoff leaking into the normal case would delete nearly every
    marker at 9:30 in the morning."""
    assert expiry_rule.is_expired(BARE_KEY, _et(2026, 8, 21, 9, 30)) is False
    assert expiry_rule.is_expired(BARE_KEY, _et(2026, 8, 21, 16, 14)) is False
    assert expiry_rule.is_expired(BARE_KEY, _et(2026, 8, 21, 16, 15)) is True


def test_a_labelled_key_still_refuses_a_naive_clock():
    """The label must not become a way round the timezone guard."""
    with pytest.raises(ValueError):
        expiry_rule.is_expired(AM_KEY, datetime(2026, 8, 22, 10, 0))


def test_a_key_that_is_neither_a_date_nor_a_label_still_raises():
    """Anything unrecognised must stay loud, so the caller keeps the lock for a
    human rather than quietly treating it as expired."""
    with pytest.raises(ValueError):
        expiry_rule.is_expired("not a date at all", _et(2026, 8, 22, 10, 0))
