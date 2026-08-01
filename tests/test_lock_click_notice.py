"""When the dashboard cannot open a lock as saved, it SAYS SO.

WHY THIS IS A SEPARATE FILE FROM test_lock_pinning.py. Pinning and the notice
fix BUG-022 from opposite ends and must be able to fail independently. Pinning
removes the two known causes; the notice covers the CLASS — any future reason a
staged value goes missing (a collector outage, a gap day, a lock edited by
hand, a cause nobody has thought of) lands back on the guard. If these shared a
file it would be easy to believe the notice was still tested when only the
pinning half was.

WHAT IS PINNED
  1. A dropped value that came from a lock click is NAMED on screen
  2. Every one of the four controls can be named, and several at once
  3. A value the trader set BY HAND going stale stays silent — Chandan's
     scoping call: the reported defect is the lock click, and a notice that
     fired during ordinary browsing would train him to ignore it
  4. An honoured click says nothing at all
  5. The guard still DROPS the value — the notice is an addition, never a
     replacement. Leaving the value in place would restore the "not in options"
     crash the guard exists to prevent.

WHY A STUB STREAMLIT AND NOT AppTest. The behaviour under test is a decision
about four session-state keys, and AppTest cannot see inside a rendered chart
anyway (test_layering.py says so at length). The stub keeps the test about the
decision, and records what would have been drawn so the assertion is on the
message the trader actually reads — not on an internal flag.
"""
from __future__ import annotations

import contextlib

import pandas as pd
import pytest

from ui import controls

FRONT = "2026-08-07"
BACK = "2026-08-21"
STRIKES = [5900.0, 6000.0, 6100.0]


class _FakeStreamlit:
    """Only the surface ui.controls.render actually touches."""

    def __init__(self):
        self.session_state: dict = {}
        self.warnings: list[str] = []

    def markdown(self, *_a, **_k):
        pass

    def warning(self, message):
        self.warnings.append(message)

    def columns(self, n):
        @contextlib.contextmanager
        def _col():
            yield
        return [_col() for _ in range(n)]

    def selectbox(self, label, options=None, *, format_func=None, key=None,
                  index=None, help=None, **_k):
        """Mirrors the one Streamlit behaviour this code is built around: a
        value already in session_state under `key` wins over `index`."""
        if key in self.session_state:
            return self.session_state[key]
        chosen = options[index if index is not None else 0]
        self.session_state[key] = chosen
        return chosen


@pytest.fixture
def st(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(controls, "st", fake)
    return fake


def _chain():
    return pd.DataFrame([
        {"expiry": e, "strike": s, "side": side, "dte": dte}
        for e, dte in ((FRONT, 7), (BACK, 21))
        for s in STRIKES for side in ("PUT", "CALL")
    ])


def _render(st):
    return controls.render(
        chain_df=_chain(),
        available_expiries=[FRONT, BACK],
        dte_by_expiry={FRONT: 7, BACK: 21},
        spx_price=6000.0,
    )


def _stage(st, **kwargs):
    for name, value in kwargs.items():
        st.session_state[f"pending_{name}"] = value


# ─────────────────────────────────────────────────────────────────────────────
# 1. The admission
# ─────────────────────────────────────────────────────────────────────────────

def test_a_lock_click_that_cannot_be_honoured_is_reported(st):
    _stage(st, front_expiry=FRONT, back_expiry=BACK,
           put_strike=5000.0, call_strike=6000.0)
    _render(st)

    assert len(st.warnings) == 1
    assert "Put Strike" in st.warnings[0]


def test_the_missing_value_itself_is_named(st):
    """"Put Strike is missing" sends him looking; "Put Strike (5,000)" tells him
    which position it was."""
    _stage(st, put_strike=5000.0)
    _render(st)

    assert "5,000" in st.warnings[0]


def test_the_notice_says_the_chart_is_a_different_diagonal(st):
    """The whole defect was a plausible chart of the wrong trade. A notice that
    only said "showing defaults" would leave him to infer the consequence."""
    _stage(st, put_strike=5000.0)
    _render(st)

    assert "different diagonal" in st.warnings[0].lower()


def test_a_dropped_expiry_is_reported_too(st):
    _stage(st, back_expiry="2027-06-18")
    _render(st)

    assert "Back Expiry" in st.warnings[0]
    assert "2027-06-18" in st.warnings[0]


def test_several_dropped_values_are_reported_in_one_notice(st):
    _stage(st, front_expiry="2027-01-15", back_expiry="2027-06-18",
           put_strike=5000.0, call_strike=9000.0)
    _render(st)

    assert len(st.warnings) == 1
    for expected in ("Front Expiry", "Back Expiry", "Put Strike", "Call Strike"):
        assert expected in st.warnings[0]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Silence where silence is right
# ─────────────────────────────────────────────────────────────────────────────

def test_an_honoured_click_says_nothing(st):
    _stage(st, front_expiry=FRONT, back_expiry=BACK,
           put_strike=6000.0, call_strike=6100.0)
    _render(st)

    assert st.warnings == []


def test_a_stale_value_the_trader_set_by_hand_stays_silent(st):
    """Chandan's scoping call. The same drop, but nobody clicked a lock — this
    is the tab left open overnight while the chain rolled. Warning here would
    fire during ordinary browsing and teach him to ignore the banner that
    matters."""
    st.session_state["put_strike_select"] = 5000.0
    _render(st)

    assert st.warnings == []


def test_no_interaction_at_all_says_nothing(st):
    _render(st)
    assert st.warnings == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. The notice is an ADDITION — the drop still happens
# ─────────────────────────────────────────────────────────────────────────────

def test_the_unusable_value_is_still_discarded(st):
    """If the notice ever replaced the drop, Streamlit would raise "not in
    options" and the page would die — the exact failure the guard prevents."""
    _stage(st, put_strike=5000.0)
    selection = _render(st)

    assert selection.put_strike in STRIKES
    assert selection.put_strike != 5000.0


def test_the_honoured_parts_of_a_partly_dropped_click_still_apply(st):
    """A lock whose strike drifted but whose expiries are both fine should
    still open on those expiries — the notice names what was lost, and the rest
    of the click is honoured rather than thrown away wholesale."""
    _stage(st, front_expiry=BACK, back_expiry=BACK, put_strike=5000.0)
    selection = _render(st)

    assert selection.front_expiry == BACK
    assert "Put Strike" in st.warnings[0]
    assert "Front Expiry" not in st.warnings[0]
