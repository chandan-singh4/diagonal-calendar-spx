"""Checks for core/ranges.py -- the session high/low behind every wick.

WHAT THESE ARE GUARDING. A wick is two numbers per strike, drawn behind a bar,
and wrong ones do not look wrong: a range computed from the last snapshot
alone, or from one measure while the bar draws another, still produces a
plausible line of the right general size at the right strike. So most of what
follows is about WHICH readings the range is taken over and WHICH measure
produced them, rather than about the arithmetic.

THE SIGN CONVENTIONS ARE THE SUBTLE PART. Vanna and charm impose the dealer
long-calls/short-puts sign; delta deliberately does not, because a put's delta
is already negative. That difference lives in core/gex.py and this module
exists to call it rather than restate it -- so the tests below check that the
right function was reached, not that the sign rule was reimplemented here.

Every frame is built by hand. Nothing here touches the real database.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core import gex, ranges

R, Q, TZ = 0.04, 0.012, "America/New_York"
STAMP = "2026-09-04 14:00:00"


def chain(rows):
    """rows: (snapshot_id, spot, strike, right, gamma, delta, oi, volume).

    One expiry, fixed iv and dte, because these tests are about which readings
    the range spans -- not about the Greeks, which core/gex.py's own tests
    cover.
    """
    return pd.DataFrame([
        {"snapshot_id": sid, "underlying_price": float(spot),
         "snapshot_timestamp": STAMP, "expiry": "2026-09-18", "dte": 14,
         "strike": float(k), "right": right, "gamma": g, "delta": d,
         "iv": 15.0, "open_interest": oi, "volume": vol}
        for sid, spot, k, right, g, d, oi, vol in rows])


def spans(out, strike=7700.0):
    row = out[out["strike"] == strike].iloc[0]
    return row["call_low"], row["call_high"]


# ------------------------------------------------------------- the range rule

def test_the_range_spans_EVERY_snapshot_not_just_the_last():
    """The whole point of a wick: a strike worked hard at the open and quiet
    since belongs on the chart at its full height. Taking the latest reading
    would draw a flat line at today's bar and say nothing."""
    out = ranges.session_ranges(chain([
        (1, 7700, 7700, "C", 0.010, 0.5, 1000, 10),
        (2, 7700, 7700, "C", 0.002, 0.5, 1000, 10),
    ]), "gamma", r=R, q=Q, display_tz=TZ)
    low, high = spans(out)
    assert low < high, "the range collapsed onto one snapshot"


def test_a_strike_that_never_moved_has_a_wick_of_ZERO_LENGTH():
    """Not an error and not a gap. A strike that has held the same exposure
    all day is exactly what a flat wick should say."""
    out = ranges.session_ranges(chain([
        (1, 7700, 7700, "C", 0.010, 0.5, 1000, 10),
        (2, 7700, 7700, "C", 0.010, 0.5, 1000, 10),
    ]), "gamma", r=R, q=Q, display_tz=TZ)
    low, high = spans(out)
    assert low == pytest.approx(high)


def test_each_snapshot_is_scaled_by_ITS_OWN_spot():
    """`dollar_scale` is quadratic in spot, so scaling the morning by the
    afternoon's price folds the index's own move into the range and widens
    every wick on the board on a trending day."""
    out = ranges.session_ranges(chain([
        (1, 7000, 7700, "C", 0.010, 0.5, 1000, 10),
        (2, 8000, 7700, "C", 0.010, 0.5, 1000, 10),
    ]), "gamma", r=R, q=Q, display_tz=TZ)
    low, high = spans(out)
    # The only difference between the two readings is the spot they were
    # scaled by, so their ratio is the ratio of the two scales.
    assert high / low == pytest.approx(
        gex.dollar_scale(8000) / gex.dollar_scale(7000))


def test_a_snapshot_with_NO_PRICE_is_skipped_rather_than_counted_as_zero():
    """Missing price -> blank, not 0. Every measure here is scaled by spot, so
    a zero would drop that snapshot's whole board to nothing and drag the low
    of every wick on the chart down to meet it."""
    out = ranges.session_ranges(chain([
        (1, 7700, 7700, "C", 0.010, 0.5, 1000, 10),
        (2, float("nan"), 7700, "C", 0.010, 0.5, 1000, 10),
    ]), "gamma", r=R, q=Q, display_tz=TZ)
    low, high = spans(out)
    assert low == pytest.approx(high) and low > 0


def test_the_rows_come_back_in_price_order():
    """A ladder reads top to bottom; out of order it reads as noise.

    THIS CANNOT FAIL AGAINST THE EXPLICIT SORT BEING DELETED, and that is
    worth writing down rather than leaving for someone to discover. Pandas'
    `groupby(...)` sorts by the group key by default, so the rows arrive in
    strike order whether or not `sort_values` runs; a mutation removing it
    survives this test. The call stays as a statement of the contract — the
    day someone passes `sort=False` for speed, the ordering is still
    guaranteed by something. What this test does pin is the CONTRACT: callers
    may rely on price order, whichever line provides it.
    """
    out = ranges.session_ranges(chain([
        (1, 7700, 7720, "C", 0.01, 0.5, 100, 1),
        (1, 7700, 7700, "C", 0.01, 0.5, 100, 1),
        (1, 7700, 7710, "C", 0.01, 0.5, 100, 1),
    ]), "gamma", r=R, q=Q, display_tz=TZ)
    assert out["strike"].tolist() == [7700.0, 7710.0, 7720.0]


# ----------------------------------------------------------------- the measures

def test_every_measure_the_tab_draws_has_a_range():
    """The request this module was written for: "I want that same wick to be
    present in all the six chart" (Chandan, 2026-09-06)."""
    board = chain([(1, 7700, 7700, "C", 0.01, 0.5, 1000, 20),
                   (1, 7700, 7700, "P", 0.01, -0.5, 800, 15),
                   (2, 7705, 7700, "C", 0.02, 0.6, 1000, 40),
                   (2, 7705, 7700, "P", 0.02, -0.4, 800, 30)])
    for measure in ranges.MEASURES:
        out = ranges.session_ranges(board, measure, r=R, q=Q, display_tz=TZ)
        assert list(out.columns) == ranges.COLUMNS, measure
        assert len(out) == 1, measure


def test_the_measures_do_not_all_return_the_SAME_numbers():
    """A dispatch that quietly answered gamma for everything would pass every
    other test in this file -- same columns, same strikes, same shape."""
    board = chain([(1, 7700, 7700, "C", 0.01, 0.5, 1000, 20),
                   (1, 7700, 7700, "P", 0.01, -0.5, 800, 15),
                   (2, 7705, 7700, "C", 0.02, 0.6, 1000, 40),
                   (2, 7705, 7700, "P", 0.02, -0.4, 800, 30)])
    highs = {m: ranges.session_ranges(board, m, r=R, q=Q,
                                      display_tz=TZ)["call_high"].iloc[0]
             for m in ranges.MEASURES}
    assert len(set(highs.values())) == len(highs), (
        f"two measures returned an identical figure: {highs}")


def test_vgex_weights_by_VOLUME_and_gamma_weights_by_OPEN_INTEREST():
    """The two share every column name on purpose, which is exactly why they
    must not share an answer: served under each other's label the tab would
    draw today's flow as the installed structure."""
    board = chain([(1, 7700, 7700, "C", 0.01, 0.5, 1000, 1),
                   (2, 7700, 7700, "C", 0.01, 0.5, 1000, 1)])
    gamma = ranges.session_ranges(board, "gamma", r=R, q=Q, display_tz=TZ)
    vgex = ranges.session_ranges(board, "vgex", r=R, q=Q, display_tz=TZ)
    # Open interest is 1000 against a volume of 1, so the two cannot coincide.
    assert gamma["call_high"].iloc[0] == pytest.approx(
        1000 * vgex["call_high"].iloc[0])


def test_each_measure_agrees_WITH_THE_FUNCTION_THAT_DRAWS_ITS_BARS():
    """THE POINT OF THE WHOLE MODULE, and the only test here that can prove
    it. Over a single snapshot the range collapses to that snapshot's bars, so
    both ends of every wick must equal what core/gex.py returns for the panel
    the wick sits behind. Anything else means the tab is drawing one measure's
    bars over another measure's range.

    IT COMPARES AGAINST THE REAL FUNCTIONS, not against numbers written here.
    Restating vanna's dealer sign or delta's lack of one in this file is
    exactly the second copy core/ranges.py exists to avoid — and a test
    carrying that copy would go on passing while the two drifted apart.

    An earlier version of this test asserted only that the put side of the
    delta range was negative. That is true of vanna too on this fixture, so
    routing delta through vanna — dealer sign and all — passed it.
    """
    board = chain([(1, 7700, 7700, "C", 0.01, 0.5, 1000, 20),
                   (1, 7700, 7700, "P", 0.01, -0.4, 800, 15)])
    spot = 7700.0
    expected = {
        "gamma": gex.by_strike(board, spot, weight="open_interest"),
        "vgex": gex.by_strike(board, spot, weight="volume"),
        "delta": gex.dex_by_strike(board, spot),
        "vanna": gex.vanna_by_strike(board, spot, r=R, q=Q,
                                     day_remainder=gex.day_remainder(STAMP, TZ)),
        "charm": gex.charm_by_strike(board, spot, r=R, q=Q,
                                     day_remainder=gex.day_remainder(STAMP, TZ)),
    }
    columns = {"gamma": "gex", "vgex": "gex", "delta": "dex",
               "vanna": "vex", "charm": "cex"}

    for measure, bars in expected.items():
        out = ranges.session_ranges(board, measure, r=R, q=Q, display_tz=TZ)
        for side in ("call", "put"):
            bar = bars[f"{side}_{columns[measure]}"].iloc[0]
            for end in ("low", "high"):
                assert out[f"{side}_{end}"].iloc[0] == pytest.approx(bar), (
                    f"{measure}: the {side} wick does not sit on the "
                    f"{side} bar it is drawn behind"
                )


def test_an_unknown_measure_RAISES_rather_than_falling_back_to_gamma():
    """A caller handed gamma's range under charm's name would draw a wick from
    a different Greek at a scale that looks entirely plausible."""
    with pytest.raises(ValueError):
        ranges.session_ranges(chain([(1, 7700, 7700, "C", 0.01, 0.5, 1, 1)]),
                              "theta", r=R, q=Q, display_tz=TZ)


# -------------------------------------------------------------------- scoping

def test_the_expiry_scope_reaches_the_computation():
    """A whole-board range behind a single-expiry bar would put the bar inside
    a wick belonging to twenty other contracts."""
    # BOTH EXPIRIES IN ONE SNAPSHOT, at one strike. Split across snapshots
    # instead, the scoped and unscoped answers can come out the same size and
    # a dropped scope passes: the range still spans one contract's readings.
    # Summed within a snapshot they cannot, because the whole board is the
    # two added together.
    board = chain([(1, 7700, 7700, "C", 0.01, 0.5, 1000, 10),
                   (1, 7700, 7700, "C", 0.01, 0.5, 1000, 10)])
    board.loc[board.index[1], "expiry"] = "2026-10-16"

    whole = ranges.session_ranges(board, "gamma", r=R, q=Q, display_tz=TZ)
    scoped = ranges.session_ranges(board, "gamma", r=R, q=Q, display_tz=TZ,
                                   expiry="2026-09-18")

    assert whole["call_high"].iloc[0] == pytest.approx(
        2 * scoped["call_high"].iloc[0]), (
        "the scope never reached the computation — the wick spans both "
        "contracts while the bar draws one")


# --------------------------------------------------------------- empty states

def test_an_empty_session_returns_empty_WITH_COLUMNS():
    """Before the first snapshot of the day there is no data and no error. A
    bare DataFrame() here becomes a KeyError in the serializer."""
    out = ranges.session_ranges(pd.DataFrame(), "gamma", r=R, q=Q,
                                display_tz=TZ)
    assert out.empty and list(out.columns) == ranges.COLUMNS


def test_a_frame_without_the_snapshot_columns_is_empty_not_an_error():
    out = ranges.session_ranges(pd.DataFrame({"strike": [7700.0]}), "gamma",
                                r=R, q=Q, display_tz=TZ)
    assert out.empty and list(out.columns) == ranges.COLUMNS
