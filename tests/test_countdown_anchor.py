"""Which day "N DTE" is counted from, on the board and on the wire.

THE BUG THIS GUARDS. On Sunday 2026-09-06 the expiry picker showed Tuesday
the 8th as "4 DTE", because the newest snapshot was Friday's and every
countdown on the board was measured from Friday. Chandan: "given that we have
the live time on the dashboard, it should use that and automatically adjust
all this date."

AND THE BEHAVIOUR IT MUST NOT BREAK. api/computed.snapshot_date deliberately
does NOT use today's date, because a board replayed from an older session
measured against today's calendar has every expiry in the past: every filter
window empties and every countdown goes negative. So the rule is not "use
today" but "use today ON THE CURRENT BOARD ONLY", and both halves are tested.

Every frame is built by hand. Nothing here touches the real database.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from api import computed
from core import expiry as expiry_rules

# A Sunday. The newest session is Friday the 4th; the next expiry is Tuesday
# the 8th, which is 4 days from Friday and 2 days from the reader's Sunday.
SUNDAY = dt.datetime(2026, 9, 6, 15, 0, tzinfo=dt.UTC)
FRIDAY = dt.date(2026, 9, 4)


def chain(rows):
    """rows: (expiry_key, dte). One strike is enough — this is about dates."""
    return pd.DataFrame([
        {"expiry": key, "dte": d, "strike": 7700.0, "right": r,
         "side": "CALL" if r == "C" else "PUT",
         "gamma": 0.001, "open_interest": 100, "iv": 15.0, "delta": 0.5}
        for key, d in rows for r in ("C", "P")])


BOARD = [("2026-09-04", 0), ("2026-09-08", 4), ("2026-09-18", 14)]


# ------------------------------------------------------------------ the rule

def test_the_current_board_counts_from_the_READERS_day():
    assert expiry_rules.countdown_anchor(
        FRIDAY, SUNDAY, is_latest=True) == dt.date(2026, 9, 6)


def test_a_REPLAYED_board_keeps_the_day_it_was_taken():
    """The behaviour snapshot_date exists for. Measured against today, a chain
    from an older session has every expiry in the past, every filter window
    empty and every countdown negative — a screen that looks broken."""
    assert expiry_rules.countdown_anchor(
        FRIDAY, SUNDAY, is_latest=False) == FRIDAY


def test_the_anchor_never_moves_BACKWARDS_from_the_session():
    """A clock behind the data would otherwise make countdowns GROW as the day
    passed. The floor says this rule may only ever move a countdown forward."""
    ahead = dt.date(2026, 9, 9)
    assert expiry_rules.countdown_anchor(
        ahead, SUNDAY, is_latest=True) == ahead


def test_during_the_session_itself_NOTHING_CHANGES():
    """The anchor and the session are the same day while the market is open,
    so this rule is invisible intraday — which is what makes it safe."""
    live = dt.datetime(2026, 9, 4, 18, 0, tzinfo=dt.UTC)   # 14:00 in New York
    assert expiry_rules.countdown_anchor(FRIDAY, live, is_latest=True) == FRIDAY


def test_a_NAIVE_clock_is_refused_rather_than_guessed_at():
    """Every clock rule in this module reads market time. A naive datetime
    would compare one machine's wall clock against another's and be right only
    where the server happens to sit."""
    with pytest.raises(ValueError):
        expiry_rules.countdown_anchor(
            FRIDAY, dt.datetime(2026, 9, 6, 15, 0), is_latest=True)


# ----------------------------------------------------------------- the board

def test_the_board_restates_the_countdown_against_the_anchor():
    """4 DTE from Friday is 2 DTE from Sunday, and it is the SAME contract."""
    board = computed.expiry_board(chain(BOARD), 7700.0,
                                  today=dt.date(2026, 9, 6))
    row = next(o for o in board if o["key"] == "2026-09-08")
    assert row["dte"] == 2
    assert row["dte_label"] == "2 DTE"


def test_the_LABEL_is_restated_too_and_does_not_keep_the_stale_number():
    """`label` embeds the countdown in prose. Re-basing the number and leaving
    the sentence would put "2 DTE" beside "(4 DTE)" on one row."""
    board = computed.expiry_board(chain(BOARD), 7700.0,
                                  today=dt.date(2026, 9, 6))
    row = next(o for o in board if o["key"] == "2026-09-08")
    assert "2 DTE" in row["label"] and "4 DTE" not in row["label"]


def test_an_expiry_that_has_already_settled_reads_NEGATIVE():
    """Friday's 0DTE contract is not 0DTE on Sunday. Clamping it to 0 would
    leave it in the one bucket a trader acts on."""
    board = computed.expiry_board(chain(BOARD), 7700.0,
                                  today=dt.date(2026, 9, 6))
    row = next(o for o in board if o["key"] == "2026-09-04")
    assert row["dte"] == -2


def test_the_FILTER_WINDOWS_move_with_the_countdown():
    """A row reading "-2 DTE" while still inside "This Week" is a
    contradiction on one line. Both are measured from the same anchor."""
    board = computed.expiry_board(chain(BOARD), 7700.0,
                                  today=dt.date(2026, 9, 6))
    settled = next(o for o in board if o["key"] == "2026-09-04")
    assert settled["filters"] == []


def test_WITHOUT_an_anchor_the_board_is_unchanged():
    """The default is the old behaviour exactly, so a replayed board and every
    existing caller keep the countdowns they had."""
    board = computed.expiry_board(chain(BOARD), 7700.0)
    assert [o["dte"] for o in board] == [0, 4, 14]


# ------------------------------------------------------------------- the API

def _serve(temp_db):
    """One completed snapshot, dated well in the past, with two expiries.

    THE SESSION IS DELIBERATELY OLD. The whole rule only does anything when
    the newest snapshot is from an earlier day than the reader's, so a fixture
    stamped "today" would exercise nothing — and would exercise nothing
    differently depending on the day the suite happened to run.
    """
    import db as database
    from fastapi.testclient import TestClient
    from test_db import add_snapshot, opt

    from api.app import create_app

    sid = add_snapshot(temp_db, "2026-08-07 14:00:00", spx=6000.0)
    rows = []
    for key, days in (("2026-08-07", 0), ("2026-08-28", 21)):
        for right in ("C", "P"):
            leg = opt(sid, key, 6000.0, right, dte=days)
            leg["open_interest"] = 500
            rows.append(leg)
    database.insert_option_rows(temp_db, rows)
    return TestClient(create_app(db_path=temp_db)), sid


def _board(client) -> list[dict]:
    reply = client.get("/mission/gamma")
    assert reply.status_code == 200, reply.text
    return reply.json()["expiries"]


def test_the_served_board_counts_from_TODAY_not_from_the_snapshot(temp_db):
    """THE LAYER THE PICKER READS. The rule and the board are both proved
    above; what only this layer can get wrong is failing to pass the anchor at
    all, which leaves every caption exactly as stale as before.

    ASSERTED AS A DIRECTION, not as a number. The seed's session is fixed but
    "today" is whatever day the suite runs, so the honest claim is that the
    countdown has SHRUNK by the days since — never that it equals 21. A test
    hard-coding a figure here would start failing tomorrow for no reason, and
    one recomputing the subtraction would be the formula written twice.
    """
    client, _ = _serve(temp_db)

    back = next(o for o in _board(client) if o["key"] == "2026-08-28")

    assert back["dte"] < 21, (
        "the board is still counting from the snapshot's own day — the "
        "endpoint is not passing an anchor"
    )
    assert back["dte_label"] == f"{back['dte']} DTE"


def test_the_countdown_and_the_label_agree_on_the_wire(temp_db):
    """Two captions built from one number, and the picker shows both."""
    client, _ = _serve(temp_db)

    for row in _board(client):
        assert f"{row['dte']} DTE" in row["label"], (
            f"{row['key']}: the prose label kept a different countdown from "
            f"the number beside it"
        )


def test_the_ANCHOR_IS_IN_THE_CACHE_KEY(temp_db, monkeypatch):
    """ONE CLIENT, ASKED ON TWO DIFFERENT DAYS. The board is cached, and the
    snapshot underneath it does not change when the day does — so a key made
    of the snapshot alone freezes the first day's countdowns into the cache
    and serves them to every later reader. That is the original bug put back
    one layer up, where it is harder to see: the endpoint would compute the
    right anchor and then decline to use it.

    A FRESH CLIENT WOULD PASS AGAINST THE BROKEN CODE, because a fresh client
    has an empty cache. The two requests below deliberately share one.
    """
    from core import expiry as rules

    client, _ = _serve(temp_db)
    days = iter([dt.date(2026, 9, 6), dt.date(2026, 9, 20)])
    monkeypatch.setattr(rules, "countdown_anchor",
                        lambda *a, **k: next(days))

    first = next(o for o in _board(client) if o["key"] == "2026-08-28")
    second = next(o for o in _board(client) if o["key"] == "2026-08-28")

    assert first["dte"] != second["dte"], (
        "the second day was served the first day's countdown — the anchor is "
        "missing from the cache key"
    )


# --------------------------------------------------- the Calendar Edge tab

def _controls(client, **params) -> dict:
    reply = client.get("/mission/controls", params=params)
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_the_PAIR_DROPDOWNS_count_from_the_anchor_too():
    """"DTE following the live clock should be applicable for Front Expiry
    and Back Expiry dropdown as well under Calendar Edge" (Chandan,
    2026-09-07).

    The same contract, the same day, the same number as the Gamma tab's
    picker -- because both go through `computed.restated`. Two tabs showing
    one expiry at two different countdowns is worse than both being stale.
    """
    controls = computed.pair_controls(chain(BOARD), 7700.0,
                                      today=dt.date(2026, 9, 6))
    row = next(o for o in controls["expiries"] if o["key"] == "2026-09-08")
    assert row["dte"] == 2
    assert "2 DTE" in row["label"] and "4 DTE" not in row["label"]


def test_the_BACK_dropdown_is_restated_as_well_as_the_front():
    """It is a narrowed copy of the same list, and narrowing it from the
    unrestated options would leave one dropdown live and one stale."""
    controls = computed.pair_controls(chain(BOARD), 7700.0,
                                      front="2026-09-04",
                                      today=dt.date(2026, 9, 6))
    row = next(o for o in controls["back_expiries"] if o["key"] == "2026-09-08")
    assert row["dte"] == 2 and "2 DTE" in row["label"]


def test_the_NARROWING_still_happens_by_DATE_and_not_by_countdown():
    """THE ONE THING THIS CHANGE MUST NOT TOUCH. The back list excludes
    anything not strictly later than the front, and that comparison is on
    dates. A settled front now reads a negative countdown, and a narrowing
    that had been written against `dte` would start dropping or admitting
    contracts as the clock moved -- a display change silently altering which
    trades are offered."""
    controls = computed.pair_controls(chain(BOARD), 7700.0,
                                      front="2026-09-04",
                                      today=dt.date(2026, 9, 6))
    front = next(o for o in controls["expiries"] if o["key"] == "2026-09-04")
    assert front["dte"] == -2, "the front is not settled in this fixture"
    assert [o["key"] for o in controls["back_expiries"]] == [
        "2026-09-08", "2026-09-18"]


def test_WITHOUT_an_anchor_the_pair_controls_are_unchanged():
    """A replayed session, and every existing caller, keep what they had."""
    controls = computed.pair_controls(chain(BOARD), 7700.0)
    assert [o["dte"] for o in controls["expiries"]] == [0, 4, 14]


def test_the_SERVED_pair_dropdowns_count_from_today(temp_db):
    """THE LAYER THE CALENDAR EDGE TAB READS, and the only one that can fail
    by never passing an anchor at all. Asserted as a direction rather than a
    number, for the reason the Gamma test gives: the seed is fixed and the
    day the suite runs is not."""
    client, _ = _serve(temp_db)

    row = next(o for o in _controls(client)["expiries"]
               if o["key"] == "2026-08-28")

    assert row["dte"] < 21, (
        "the pair dropdowns are still counting from the snapshot's own day — "
        "the endpoint is not passing an anchor"
    )
    assert f"{row['dte']} DTE" in row["label"]


def test_the_TWO_TABS_agree_on_the_wire(temp_db):
    """One client, one snapshot, both endpoints. The picker and the pair
    dropdowns name the same contracts, and a reader switching tabs must not
    see one of them move."""
    client, _ = _serve(temp_db)

    picker = {o["key"]: o["dte"] for o in _board(client)}
    pair = {o["key"]: o["dte"] for o in _controls(client)["expiries"]}

    assert pair == {k: picker[k] for k in pair}
