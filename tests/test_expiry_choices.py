"""Which expiries the two dropdowns offer, and in what order (BUG-023).

Chandan, 2026-08-19, on seeing both third-Friday contracts appear for the first
time: the a.m. one belongs ABOVE the p.m. one, and the Back Expiry list should
start at the expiry after whatever the front leg is, with the earlier ones not
merely discouraged but absent.

Both rules are about what is REACHABLE, not about what a particular click
returns, so these check the option lists the widgets were handed. A test that
only inspected the returned selection would pass just as happily against code
that offered twenty wrong choices and happened not to take one.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core import contract
from ui import controls
from test_lock_click_notice import _FakeStreamlit

# Aug 21 is a third Friday, so it lists two contracts; the rest are weeklies.
AM = "2026-08-21 (AM)"
PM = "2026-08-21"
EXPIRIES = ["2026-08-19", "2026-08-20", AM, PM, "2026-08-24", "2026-08-25"]
DTE = {"2026-08-19": 0, "2026-08-20": 1, AM: 2, PM: 2,
       "2026-08-24": 5, "2026-08-25": 6}
STRIKES = [5900.0, 6000.0, 6100.0]


@pytest.fixture
def st(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(controls, "st", fake)
    return fake


def _chain(expiries=EXPIRIES):
    return pd.DataFrame([
        {"expiry": e, "strike": s, "side": side, "dte": DTE[e]}
        for e in expiries for s in STRIKES for side in ("PUT", "CALL")
    ])


def _render(st, expiries=EXPIRIES):
    return controls.render(
        chain_df=_chain(expiries),
        available_expiries=sorted(expiries, key=contract.sort_key),
        dte_by_expiry={e: DTE[e] for e in expiries},
        spx_price=6000.0,
    )


def _pick(st, front=None, back=None):
    """Choose expiries the way the trader does, before rendering."""
    if front is not None:
        st.session_state["pending_front_expiry"] = front
    if back is not None:
        st.session_state["pending_back_expiry"] = back


# ---------------------------------------------------------------------------
# Order: the a.m. contract sits above the p.m. one
# ---------------------------------------------------------------------------

def test_the_am_contract_is_listed_above_the_pm_one(st):
    """Not cosmetic. The a.m. contract settles at that morning's OPEN and the
    p.m. one at the close, so it really is the earlier of the two, and the list
    is otherwise in order of when each contract ends."""
    _render(st)
    offered = st.options["front_expiry_select"]
    assert offered.index(AM) < offered.index(PM)


def test_the_front_list_is_in_order_of_when_each_contract_ends(st):
    _render(st)
    assert st.options["front_expiry_select"] == [
        "2026-08-19", "2026-08-20", AM, PM, "2026-08-24", "2026-08-25"]


def test_a_plain_text_sort_would_get_it_backwards():
    """The trap this ordering exists to avoid, stated once so the reason for
    the sort key survives someone 'simplifying' it back to sorted()."""
    assert sorted([PM, AM]) == [PM, AM]
    assert sorted([PM, AM], key=contract.sort_key) == [AM, PM]


# ---------------------------------------------------------------------------
# The back leg can only be something LATER
# ---------------------------------------------------------------------------

def test_the_back_list_starts_at_the_very_next_expiry(st):
    _pick(st, front="2026-08-20")
    _render(st)
    assert st.options["back_expiry_select"][0] == AM


def test_nothing_at_or_before_the_front_is_offered_as_a_back_leg(st):
    _pick(st, front=PM)
    _render(st)
    assert st.options["back_expiry_select"] == ["2026-08-24", "2026-08-25"]


def test_the_other_contract_on_the_same_day_is_not_offered(st):
    """Chandan's call. The p.m. contract does end a few hours after the a.m.
    one, so it is arguably a valid back leg — but the rule he wants is that the
    back list starts at the next DATE, so neither same-day contract appears."""
    _pick(st, front=AM)
    _render(st)
    assert PM not in st.options["back_expiry_select"]
    assert st.options["back_expiry_select"] == ["2026-08-24", "2026-08-25"]


def test_the_default_back_leg_is_the_next_expiry_not_the_second_in_the_list(st):
    """With the list narrowed, index 0 is already the right default — the old
    'skip one' rule would now skip a valid choice."""
    _pick(st, front="2026-08-19")
    selection = _render(st)
    assert selection.back_expiry == "2026-08-20"


def test_moving_the_front_past_the_chosen_back_leg_replaces_it(st):
    """The back leg left over from the previous choice is no longer valid.
    Streamlit raises 'not in options' and kills the page if it is left in
    place, so this is a crash guard as much as a tidiness one."""
    _pick(st, front="2026-08-19", back="2026-08-20")
    _render(st)
    st.session_state["pending_front_expiry"] = "2026-08-24"
    selection = _render(st)
    assert selection.front_expiry == "2026-08-24"
    assert selection.back_expiry == "2026-08-25"


# ---------------------------------------------------------------------------
# The edge: a front leg with nothing after it
# ---------------------------------------------------------------------------

def test_the_furthest_expiry_as_a_front_leg_says_so_rather_than_breaking(st):
    """Reachable in one click, and an empty option list takes the whole page
    down. The page has to survive it and explain itself."""
    _pick(st, front="2026-08-25")
    selection = _render(st)

    assert selection.front_expiry == "2026-08-25"
    assert st.options["back_expiry_select"] == ["2026-08-25"]
    assert any("no back leg" in w for w in st.warnings), st.warnings


def test_an_ordinary_front_leg_raises_no_such_warning(st):
    _pick(st, front="2026-08-19")
    _render(st)
    assert not any("no back leg" in w for w in st.warnings), st.warnings
