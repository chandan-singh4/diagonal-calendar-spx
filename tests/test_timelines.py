"""Checks for core/timelines.py -- the two "through the session" panels.

WHAT THESE ARE GUARDING. Both functions are a ranking followed by a filter, and
a ranking is the kind of code that keeps working while being wrong: pick the
seven busiest strikes with the wrong rule and you still get seven lines through
the day, correctly drawn, of the wrong strikes. So most of what follows is
about WHICH strikes survive rather than about the arithmetic on them.

Every frame is built by hand. Nothing here touches the real database.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core import gex, timelines

T0 = pd.Timestamp("2026-09-04 13:30", tz="UTC")   # 09:30 in New York
ET = "America/New_York"


def volume(df, **kw):
    """net_volume_by_strike with the market's zone already supplied.

    A HELPER RATHER THAN display_tz ON TWENTY CALLS. The zone is not what any
    test below is about, and repeating it would bury the one argument that
    differs in each. The two timezone tests at the bottom pass it explicitly,
    because there it IS the subject.
    """
    return timelines.net_volume_by_strike(df, display_tz=ET, **kw)


def gex_lines(df, **kw):
    return timelines.gex_by_strike_over_time(df, display_tz=ET, **kw)


def frame(rows):
    """rows: (minutes_in, strike, call_vol, put_vol, call_gam, put_gam, spot)."""
    return pd.DataFrame([
        {"timestamp": T0 + pd.Timedelta(minutes=m), "strike": float(k),
         "call_volume": cv, "put_volume": pv,
         "call_gamma_oi": cg, "put_gamma_oi": pg,
         "underlying_price": float(spot)}
        for m, k, cv, pv, cg, pg, spot in rows])


# --------------------------------------------------------------- net volume

def test_the_bar_is_calls_minus_puts():
    out = volume(frame([(0, 7700, 500, 200, 0, 0, 7700)]))
    assert out["net_volume"].tolist() == [300]


def test_more_puts_than_calls_goes_NEGATIVE_and_is_not_clamped():
    """The sign IS the signal -- it is which side of the strike the day was
    being fought on. A max(0, ...) anywhere would erase half the chart."""
    out = volume(frame([(0, 7700, 200, 900, 0, 0, 7700)]))
    assert out["net_volume"].tolist() == [-700]


def test_the_busiest_strikes_are_ranked_by_the_ABSOLUTE_reading():
    """A put-heavy strike is exactly as interesting as a call-heavy one. Rank
    on the raw net and every negative strike falls off the bottom of the
    chart, leaving a panel that only ever shows call demand."""
    out = volume(frame([
        (0, 7700, 0, 5000, 0, 0, 7700),   # net -5000, the loudest strike here
        (0, 7710, 100, 0, 0, 0, 7700),
        (0, 7720, 50, 0, 0, 0, 7700),
    ]), count=2)
    assert set(out["strike"]) == {7700.0, 7710.0}


def test_a_strike_is_ranked_by_its_LOUDEST_MOMENT_not_its_last_reading():
    """A strike worked hard at the open and quiet after belongs on this chart.
    Ranking on the final snapshot would drop it for one that was never busy.

    THE TWO STRIKES HAVE TO CROSS OVER for this to prove anything. The first
    version of this test had 7700 ending on a net of 100 and 7710 on 80 -- so
    the last-reading rule and the loudest-moment rule both picked 7700, and a
    deliberately broken ranking passed. Here 7710 ENDS the larger of the two
    and 7700 only ever LED, so the two rules disagree and the test can fail.
    """
    out = volume(frame([
        (0, 7700, 9000, 0, 0, 0, 7700), (5, 7700, 9000, 8900, 0, 0, 7700),
        (0, 7710, 40, 0, 0, 0, 7700),   (5, 7710, 5000, 0, 0, 0, 7700),
    ]), count=1)
    assert set(out["strike"]) == {7700.0}


def test_count_limits_the_lines_drawn():
    out = volume(frame([
        (0, k, k, 0, 0, 0, 7700) for k in (7700, 7710, 7720, 7730)]), count=2)
    assert len(set(out["strike"])) == 2


def test_the_strikes_argument_narrows_to_the_board_on_screen():
    """The panel sits under bars drawn at a set of strikes; it must not answer
    about a strike the reader cannot see."""
    out = volume(frame([(0, k, 500, 0, 0, 0, 7700) for k in (7700, 7710, 7720)]),
        strikes=[7700.0, 7720.0])
    assert set(out["strike"]) == {7700.0, 7720.0}


def test_expiries_at_one_strike_are_summed_before_ranking():
    """Two rows, same timestamp, same strike -- two expiries. The strike's net
    volume is 300, not two separate readings of 150."""
    df = frame([(0, 7700, 200, 50, 0, 0, 7700),
                (0, 7700, 200, 50, 0, 0, 7700)])
    out = volume(df)
    assert len(out) == 1
    assert out["net_volume"].tolist() == [300]


def test_every_snapshot_of_a_kept_strike_survives():
    """The filter drops STRIKES, not moments. A line with holes in it is worse
    than no line."""
    out = volume(frame([
        (m, 7700, 100 * m, 0, 0, 0, 7700) for m in range(4)]), count=1)
    assert len(out) == 4


def test_the_rows_come_back_grouped_by_strike_and_in_time_order():
    """A caller draws one trace per strike by walking the frame; out of order,
    the line doubles back on itself."""
    out = volume(frame([
        (5, 7710, 10, 0, 0, 0, 7700), (0, 7700, 10, 0, 0, 0, 7700),
        (5, 7700, 20, 0, 0, 0, 7700), (0, 7710, 20, 0, 0, 0, 7700)]))
    ordered = out[["strike", "timestamp"]]
    assert ordered.equals(ordered.sort_values(["strike", "timestamp"],
                                              ignore_index=True))


def test_an_empty_session_returns_empty_WITH_COLUMNS():
    """Before the first snapshot of the day there is no data and no error. A
    bare DataFrame() here becomes a KeyError in the serializer."""
    out = volume(pd.DataFrame())
    assert out.empty and list(out.columns) == timelines.VOLUME_COLUMNS


def test_narrowing_to_strikes_that_are_not_there_is_empty_not_an_error():
    out = volume(frame([(0, 7700, 10, 0, 0, 0, 7700)]), strikes=[9999.0])
    assert out.empty and list(out.columns) == timelines.VOLUME_COLUMNS


# ------------------------------------------------------------------ net gex

def test_net_gex_is_calls_minus_puts_scaled_by_that_snapshots_own_spot():
    out = gex_lines(        frame([(0, 7700, 0, 0, 10.0, 4.0, 7700)]))
    assert out["net_gex"].tolist() == [pytest.approx(6.0 * gex.dollar_scale(7700))]


def test_each_snapshot_is_scaled_by_ITS_OWN_spot_not_the_last_one():
    """dollar_scale is quadratic in spot, so using one price for the whole day
    folds the index's own move into every line and tilts the chart."""
    out = gex_lines(frame([
        (0, 7700, 0, 0, 10.0, 0.0, 7000),
        (5, 7700, 0, 0, 10.0, 0.0, 8000)]))
    assert out["net_gex"].tolist() == [
        pytest.approx(10.0 * gex.dollar_scale(7000)),
        pytest.approx(10.0 * gex.dollar_scale(8000))]


def test_the_gamma_lines_are_ranked_at_the_LATEST_snapshot():
    """This panel is about where gamma is sitting NOW. A strike that carried
    the board this morning and has since been closed out is not the answer."""
    out = gex_lines(frame([
        (0, 7700, 0, 0, 900.0, 0.0, 7700), (5, 7700, 0, 0, 1.0, 0.0, 7700),
        (0, 7710, 0, 0, 1.0, 0.0, 7700),   (5, 7710, 0, 0, 500.0, 0.0, 7700),
    ]), count=1)
    assert set(out["strike"]) == {7710.0}


def test_a_put_heavy_strike_ranks_on_MAGNITUDE():
    """Negative net gamma is the interesting half of this chart -- it is where
    dealers are short and moves accelerate."""
    out = gex_lines(frame([
        (0, 7700, 0, 0, 0.0, 900.0, 7700),
        (0, 7710, 0, 0, 10.0, 0.0, 7700)]), count=1)
    assert set(out["strike"]) == {7700.0}


def test_gamma_expiries_at_one_strike_are_summed():
    out = gex_lines(frame([
        (0, 7700, 0, 0, 6.0, 1.0, 7700),
        (0, 7700, 0, 0, 4.0, 1.0, 7700)]))
    assert len(out) == 1
    assert out["net_gex"].tolist() == [pytest.approx(8.0 * gex.dollar_scale(7700))]


def test_an_empty_gamma_session_returns_empty_WITH_COLUMNS():
    out = gex_lines(pd.DataFrame())
    assert out.empty and list(out.columns) == timelines.GEX_COLUMNS


# ------------------------------------------------------------------- totals

def test_the_headline_totals_are_the_sum_of_the_LINES_DRAWN():
    """Computed from the chart rather than from the board, so the strip and
    the picture cannot disagree by a number nobody can account for."""
    lines = gex_lines(frame([
        (0, 7700, 0, 0, 1.0, 0.0, 7700), (5, 7700, 0, 0, 3.0, 0.0, 7700),
        (0, 7710, 0, 0, 1.0, 0.0, 7700), (5, 7710, 0, 0, 1.0, 0.0, 7700)]))
    out = timelines.gex_totals(lines)
    unit = gex.dollar_scale(7700)
    assert out["at_open"] == pytest.approx(2.0 * unit)
    assert out["now"] == pytest.approx(4.0 * unit)
    assert out["change"] == pytest.approx(2.0 * unit)


def test_the_change_can_be_negative():
    lines = gex_lines(frame([
        (0, 7700, 0, 0, 9.0, 0.0, 7700), (5, 7700, 0, 0, 2.0, 0.0, 7700)]))
    assert timelines.gex_totals(lines)["change"] < 0


def test_the_levels_come_back_in_price_order():
    """BUILT BY HAND, NOT VIA gex_by_strike_over_time, and that is the point.
    Fed the real function's output this test cannot fail: that function already
    sorts by strike, so `unique()` is in price order whether gex_totals sorts
    or not -- and a version with the sort deleted passed. `gex_totals` is
    public and takes any frame of lines, so its own contract is tested against
    a frame that is genuinely out of order.
    """
    lines = pd.DataFrame({
        "timestamp": [T0, T0],
        "strike": [7710.0, 7700.0],
        "net_gex": [9.0, 1.0]})
    assert timelines.gex_totals(lines)["levels"] == [7700.0, 7710.0]


def test_no_lines_means_NO_READING_and_not_zero():
    """Missing price -> blank, not 0. A headline reading "$0" is a claim that
    the board is flat; "--" is the truth, which is that nobody has looked."""
    out = timelines.gex_totals(pd.DataFrame())
    assert out["now"] is None and out["at_open"] is None
    assert out["change"] is None and out["levels"] == []


# --------------------------------------------------------------- market time

def test_the_timestamps_come_back_ON_THE_MARKET_CLOCK():
    """THE BUG THIS EXISTS FOR. The read layer hands out UTC, so 09:30 in New
    York arrives as 13:30+00:00 -- and plotly draws the WALL-CLOCK part of an
    ISO string and ignores the offset, so both panels drew a session starting
    in the afternoon and ending at eight at night. Asserting the offset alone
    is not enough: a frame that was relabelled rather than converted carries
    the right offset on the wrong hour. So this pins the hour too.
    """
    out = timelines.net_volume_by_strike(
        frame([(0, 7700, 500, 0, 0, 0, 7700)]), display_tz=ET)
    stamp = out["timestamp"].iloc[0]
    assert (stamp.hour, stamp.minute) == (9, 30)
    assert stamp.utcoffset() == pd.Timedelta(hours=-4)


def test_the_gamma_panel_is_on_the_market_clock_too():
    """The two panels sit one above the other, so one converted and one not is
    a pair of charts whose x axes disagree by four hours."""
    out = timelines.gex_by_strike_over_time(
        frame([(0, 7700, 0, 0, 10.0, 0.0, 7700)]), display_tz=ET)
    stamp = out["timestamp"].iloc[0]
    assert (stamp.hour, stamp.minute) == (9, 30)


def test_the_zone_is_the_CALLERS_and_the_module_has_no_opinion():
    """Passed in, never read from config -- so a second deployment reading
    another market's clock is a different argument, not a fork of this file."""
    out = timelines.net_volume_by_strike(
        frame([(0, 7700, 500, 0, 0, 0, 7700)]), display_tz="Europe/London")
    assert out["timestamp"].iloc[0].hour == 14   # 13:30 UTC -> 14:30 BST


def test_the_moment_itself_is_UNCHANGED_by_the_conversion():
    """CONVERTED, NOT STRIPPED. Shifting the underlying instant to make the
    wall clock read right would move every point on the chart to a time the
    trade did not happen at."""
    out = timelines.net_volume_by_strike(
        frame([(0, 7700, 500, 0, 0, 0, 7700)]), display_tz=ET)
    assert out["timestamp"].iloc[0] == T0
