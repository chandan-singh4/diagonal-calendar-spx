"""The Gamma Exposure tab remembers which expiry is on screen.

WHAT WAS WRONG (Chandan, 2026-09-05): the expiry dropdown reset to "All
expiries" whenever the tab was left and returned to, and whenever the data
refreshed while another tab was open.

The cause is not a bug in the tab. Streamlit discards a widget's state when
the widget is not drawn, and the custom nav bar means the five other tabs
never draw this one — so the key was gone from session_state entirely while
away (verified against the running app before the fix). A second, non-widget
key now remembers the choice.

Two separate behaviours are pinned here and they fail in opposite directions:
restoring too eagerly overwrites a fresh pick and freezes the dropdown;
restoring too timidly loses the selection on the next tab change.

NO DATABASE, NO PAGE. Streamlit is replaced with a stand-in whose whole
surface is a session_state dict, which is all these two functions touch.
"""
from __future__ import annotations

import pytest

from views import gex

TODAY = "2026-09-04"
NEXT = "2026-09-08"
LATER = "2026-09-11"
ALL = "All expiries"


class FakeStreamlit:
    """session_state and nothing else — the only surface under test.

    If this ever needs another attribute, that is a finding: it means the
    remembering logic has started reaching for the page.
    """

    def __init__(self, state=None):
        self.session_state = dict(state or {})


@pytest.fixture
def st_state(monkeypatch):
    fake = FakeStreamlit()
    monkeypatch.setattr(gex, "st", fake)
    return fake.session_state


@pytest.fixture
def board():
    """A normal board: something expiring today, and two later contracts."""
    expiries = [TODAY, NEXT, LATER]
    dte = {TODAY: 0, NEXT: 4, LATER: 7}
    return expiries, dte, [ALL, *expiries]


# ─────────────────────────────────────────────────────────────────────────────
# The default
# ─────────────────────────────────────────────────────────────────────────────

def test_the_default_is_todays_expiry(board):
    """0DTE, which is what was asked for. It falls out of "nearest expiry"
    rather than being a special case — see _default_expiry for why the
    explicit branch was removed."""
    expiries, dte, _ = board

    assert gex._default_expiry(expiries, dte) == TODAY
    assert dte[gex._default_expiry(expiries, dte)] == 0


def test_without_a_0dte_the_nearest_expiry_is_used(board):
    """A weekend or a holiday. Deliberately NOT "All expiries" — that is the
    behaviour being fixed, and it would come back on exactly the days it is
    least expected."""
    expiries, dte, _ = board
    dte = {NEXT: 4, LATER: 7}

    assert gex._default_expiry([NEXT, LATER], dte) == NEXT


def test_the_nearest_expiry_is_not_merely_the_first_listed():
    """min() on the DTE, not [0] on the list. A board that arrived in another
    order would otherwise pick the wrong contract, and the sort order upstream
    is not this function's to assume."""
    assert gex._default_expiry([LATER, NEXT], {LATER: 7, NEXT: 4}) == NEXT


def test_a_board_with_no_expiries_falls_back_to_all(board):
    assert gex._default_expiry([], {}) == ALL


def test_a_duplicated_0dte_picks_the_first_listed():
    """The third Friday lists two contracts, AM and PM settled, and both can
    be 0 DTE. Either is defensible; picking consistently is what matters, so
    that the tab does not open on a different one each time."""
    expiries = ["2026-09-18", "2026-09-18-PM"]
    dte = {"2026-09-18": 0, "2026-09-18-PM": 0}

    assert gex._default_expiry(expiries, dte) == "2026-09-18"


# ─────────────────────────────────────────────────────────────────────────────
# Restoring — and knowing when not to
# ─────────────────────────────────────────────────────────────────────────────

def test_the_first_ever_visit_opens_on_0dte(st_state, board):
    expiries, dte, options = board

    gex._restore_expiry_choice(options, expiries, dte)

    assert st_state["gex_expiry"] == TODAY


def test_a_fresh_pick_is_never_overwritten(st_state, board):
    """The failure that would make the dropdown appear frozen.

    When the reader picks an expiry, Streamlit has already written it into
    session_state before the script runs. Assigning the remembered value
    unconditionally would put the previous choice back on every single change.
    """
    expiries, dte, options = board
    st_state["gex_expiry"] = LATER          # what the reader just chose
    st_state[gex._EXPIRY_MEMORY] = NEXT     # what was there before

    gex._restore_expiry_choice(options, expiries, dte)

    assert st_state["gex_expiry"] == LATER


def test_the_choice_comes_back_after_streamlit_discards_it(st_state, board):
    """The reported bug. While the tab is not drawn the widget key is removed
    entirely — this reproduces that state exactly, rather than simulating it
    with an empty string."""
    expiries, dte, options = board
    st_state[gex._EXPIRY_MEMORY] = LATER
    assert "gex_expiry" not in st_state

    gex._restore_expiry_choice(options, expiries, dte)

    assert st_state["gex_expiry"] == LATER


def test_all_expiries_is_restored_when_it_was_chosen_deliberately(st_state, board):
    """Not everything is a 0DTE reader. An explicit choice of the whole board
    is a choice, and must survive a tab change like any other."""
    expiries, dte, options = board
    st_state[gex._EXPIRY_MEMORY] = ALL

    gex._restore_expiry_choice(options, expiries, dte)

    assert st_state["gex_expiry"] == ALL


def test_an_expiry_that_has_rolled_off_is_not_restored(st_state, board):
    """What makes the first visit of a new session correct. Yesterday's 0DTE
    is no longer on the board; restoring it would select a contract that no
    longer exists, and the tab would show nothing."""
    expiries, dte, options = board
    st_state[gex._EXPIRY_MEMORY] = "2026-09-03"   # yesterday, now gone

    gex._restore_expiry_choice(options, expiries, dte)

    assert st_state["gex_expiry"] == TODAY


def test_a_widget_value_that_has_rolled_off_is_replaced(st_state, board):
    """Same thing one step earlier: the tab was open across the roll. A stale
    value left in place would be passed to the chart as a real selection."""
    expiries, dte, options = board
    st_state["gex_expiry"] = "2026-09-03"

    gex._restore_expiry_choice(options, expiries, dte)

    assert st_state["gex_expiry"] == TODAY
