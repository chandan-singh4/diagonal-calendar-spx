"""The time machine's data — one session, replayed frame by frame.

WHAT THIS HAS TO GUARANTEE (Chandan, 2026-09-05). The replay shows two panels
side by side: where the gamma sits, and how much of it arrived or left since
the open. His requirement for it was that they agree — "when gex adds then
I'll see in the GEX chart as well". So the load-bearing property here is not
that either number is plausible, it is that the second IS the first minus the
first frame, at every strike and in every frame. A test that only checked the
totals would pass while the two panels disagreed strike by strike, which is
precisely the way this would look wrong on screen.

The other three properties are all about a bar chart that MOVES. A static
chart tolerates a ragged strike list; an animated one does not, because Plotly
matches bars across frames by position, so a frame that drops a strike
silently repaints every bar after it onto the wrong rung.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core import gex

OPEN, MID, CLOSE = "2026-09-04 13:30:00", "2026-09-04 16:00:00", "2026-09-04 20:00:00"


def _rows(*rows) -> pd.DataFrame:
    """(timestamp, strike, call_gamma_oi, put_gamma_oi, spot) per row."""
    frame = pd.DataFrame(
        list(rows),
        columns=["timestamp", "strike", "call_gamma_oi", "put_gamma_oi",
                 "underlying_price"],
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def _at(out: pd.DataFrame, stamp: str) -> pd.DataFrame:
    return out[out["timestamp"] == pd.Timestamp(stamp, tz="UTC")] \
        .set_index("strike")


# ─────────────────────────────────────────────────────────────────────────────
# The guarantee the feature was asked for
# ─────────────────────────────────────────────────────────────────────────────

def test_the_change_is_the_level_minus_the_level_at_the_open():
    """The two panels are one subtraction apart, at every strike and moment.

    Checked per strike rather than on the totals: two panels can agree on a
    sum while disagreeing on every rung in it, and the rungs are what is
    being compared on screen.
    """
    out = gex.replay_by_strike(_rows(
        (OPEN, 7700.0, 100.0, 40.0, 7700.0),
        (OPEN, 7750.0, 80.0, 20.0, 7700.0),
        (CLOSE, 7700.0, 130.0, 40.0, 7700.0),
        (CLOSE, 7750.0, 50.0, 20.0, 7700.0),
    ))

    at_open, at_close = _at(out, OPEN), _at(out, CLOSE)
    for strike in (7700.0, 7750.0):
        assert at_close.loc[strike, "flow"] == pytest.approx(
            at_close.loc[strike, "net_gex"] - at_open.loc[strike, "net_gex"]
        )


def test_the_first_frame_shows_no_change_anywhere():
    """The open is the baseline, so nothing has moved yet. A non-zero first
    frame would mean the replay opens by claiming the whole board traded
    before the market did."""
    out = gex.replay_by_strike(_rows(
        (OPEN, 7700.0, 100.0, 40.0, 7700.0),
        (CLOSE, 7700.0, 130.0, 40.0, 7700.0),
    ))

    assert (_at(out, OPEN)["flow"] == 0).all()


def test_gamma_arriving_shows_on_both_panels_in_the_same_direction():
    """The sentence the feature exists to make true. A sign convention wrong
    on one panel would have green bars growing beside red ones."""
    out = gex.replay_by_strike(_rows(
        (OPEN, 7700.0, 100.0, 0.0, 7700.0),
        (CLOSE, 7700.0, 160.0, 0.0, 7700.0),
    ))

    at_open, at_close = _at(out, OPEN), _at(out, CLOSE)
    assert at_close.loc[7700.0, "net_gex"] > at_open.loc[7700.0, "net_gex"]
    assert at_close.loc[7700.0, "flow"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Properties an ANIMATED chart needs and a static one does not
# ─────────────────────────────────────────────────────────────────────────────

def test_every_frame_carries_every_strike():
    """Plotly matches bars across frames BY POSITION. A frame listing three
    strikes where the next lists four does not draw one extra bar — it
    repaints the existing bars onto the wrong rungs, silently, and only while
    playing."""
    out = gex.replay_by_strike(_rows(
        (OPEN, 7700.0, 100.0, 0.0, 7700.0),
        (MID, 7700.0, 100.0, 0.0, 7700.0),
        (MID, 7750.0, 50.0, 0.0, 7700.0),      # quoted only at midday
        (CLOSE, 7700.0, 100.0, 0.0, 7700.0),
    ))

    counts = out.groupby("timestamp")["strike"].apply(set)
    assert all(s == {7700.0, 7750.0} for s in counts), (
        f"frames carry different strike lists: {list(counts)}"
    )


def test_a_strike_that_is_not_quoted_is_blank_and_not_zero():
    """The project's standing rule, and here the difference is visible: zero
    draws a bar saying "no gamma at this level", blank draws nothing and says
    "not quoted". They are opposite claims about the same rung."""
    out = gex.replay_by_strike(_rows(
        (OPEN, 7700.0, 100.0, 0.0, 7700.0),
        (OPEN, 7750.0, 50.0, 0.0, 7700.0),
        (CLOSE, 7700.0, 100.0, 0.0, 7700.0),   # 7750 unquoted at the close
    ))

    assert pd.isna(_at(out, CLOSE).loc[7750.0, "net_gex"]), (
        "an unquoted strike must be blank, not a bar of zero"
    )


def test_the_drawn_strikes_are_chosen_once_for_the_whole_session():
    """`strikes` keeps the biggest movers. Chosen per frame, the ladder would
    reshuffle its rungs as it played and nothing could be tracked through the
    day; chosen across the session, it holds still."""
    out = gex.replay_by_strike(_rows(
        (OPEN, 7700.0, 100.0, 0.0, 7700.0),
        (OPEN, 7750.0, 100.0, 0.0, 7700.0),
        (OPEN, 7800.0, 100.0, 0.0, 7700.0),
        # 7750 moves early and settles back; 7800 moves only at the close.
        (MID, 7700.0, 100.0, 0.0, 7700.0),
        (MID, 7750.0, 900.0, 0.0, 7700.0),
        (MID, 7800.0, 100.0, 0.0, 7700.0),
        (CLOSE, 7700.0, 100.0, 0.0, 7700.0),
        (CLOSE, 7750.0, 100.0, 0.0, 7700.0),
        (CLOSE, 7800.0, 500.0, 0.0, 7700.0),
    ), strikes=2)

    per_frame = out.groupby("timestamp")["strike"].apply(set)
    assert all(s == set(list(per_frame)[0]) for s in per_frame), (
        "the ladder changed rungs between frames"
    )
    assert set(list(per_frame)[0]) == {7750.0, 7800.0}, (
        "a strike that moved hugely at midday and came back must still be "
        "kept — it is one of the day's biggest movers"
    )


def test_each_snapshot_is_scaled_by_its_own_spot():
    """dollar_scale is quadratic in spot. Scaling every frame by the latest
    price would fold the index's own move into a figure that is supposed to
    isolate what traded."""
    out = gex.replay_by_strike(_rows(
        (OPEN, 7700.0, 100.0, 0.0, 7000.0),
        (CLOSE, 7700.0, 100.0, 0.0, 8000.0),
    ))

    at_open, at_close = _at(out, OPEN), _at(out, CLOSE)
    assert at_open.loc[7700.0, "net_gex"] == pytest.approx(
        100.0 * gex.dollar_scale(7000.0))
    assert at_close.loc[7700.0, "net_gex"] == pytest.approx(
        100.0 * gex.dollar_scale(8000.0))
    assert at_close.loc[7700.0, "net_gex"] > at_open.loc[7700.0, "net_gex"], (
        "identical gamma at a higher spot is a larger dollar exposure"
    )


def test_puts_count_against_calls():
    """Net gamma exposure, so the two sides subtract. Summing them would draw
    a board that is never short gamma anywhere."""
    out = gex.replay_by_strike(_rows(
        (OPEN, 7700.0, 40.0, 100.0, 7700.0),
        (CLOSE, 7700.0, 40.0, 100.0, 7700.0),
    ))

    assert _at(out, OPEN).loc[7700.0, "net_gex"] < 0


def test_empty_in_empty_out_with_columns():
    """A session before its first snapshot is a normal state, not an error.
    The columns have to survive so the caller can ask whether it is empty
    rather than whether it exploded."""
    out = gex.replay_by_strike(pd.DataFrame())

    assert out.empty
    assert {"timestamp", "strike", "net_gex", "flow"} <= set(out.columns)
