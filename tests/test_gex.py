"""core/gex.py — gamma exposure per strike, and the figures above the chart.

WHAT IS WORTH PINNING HERE, and what is not.

GEX rests on an ASSUMPTION — that dealers are long calls and short puts —
which no data anywhere can confirm. So these tests do not attempt to prove the
measure is *true*. They pin that the arithmetic is the arithmetic we said it
was, that the sign convention is applied the way the docstring claims, and
that the empty and partial cases behave — because those are the parts that can
be wrong without anybody noticing.

The one test doing real work beyond that is
`test_the_header_strike_is_unchanged_by_the_rescaling`. `core.market.
max_gex_label` carried its own copy of this formula at a different scale and
now delegates here. The claim justifying that move is that the constant cannot
reorder magnitudes; a claim load-bearing enough to justify touching a shipped
header is a claim worth a test rather than an argument.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core import gex, market

SPOT = 7700.0


def _chain(rows: list[dict]) -> pd.DataFrame:
    """A chain frame shaped like dataaccess.load_chain_df's output."""
    return pd.DataFrame([
        {"expiry": r.get("expiry", "2026-09-18"),
         "strike": r["strike"],
         "right": r["right"],
         "gamma": r.get("gamma", 0.001),
         "open_interest": r.get("oi", 1000),
         "volume": r.get("vol", 0)}
        for r in rows
    ])


# ─────────────────────────────────────────────────────────────────────────────
# The formula, stated once here so a silent change to core/gex.py fails loudly
# ─────────────────────────────────────────────────────────────────────────────

def test_one_call_carries_the_documented_dollar_gamma():
    """gamma x OI x 100 x spot^2 x 0.01 — the industry-standard 1% unit."""
    df = gex.by_strike(_chain([{"strike": 7700, "right": "C",
                                "gamma": 0.002, "oi": 500}]), SPOT)
    expected = 0.002 * 500 * 100 * (SPOT ** 2) * 0.01
    assert df["call_gex"].iloc[0] == pytest.approx(expected)
    assert df["net_gex"].iloc[0] == pytest.approx(expected)


def test_a_put_carries_the_same_magnitude_with_the_opposite_sign():
    """The dealer convention — long calls, short puts — and the whole reason
    net GEX can be negative. If this ever flips, every regime reading built on
    it inverts silently."""
    df = gex.by_strike(_chain([{"strike": 7700, "right": "P",
                                "gamma": 0.002, "oi": 500}]), SPOT)
    expected = 0.002 * 500 * 100 * (SPOT ** 2) * 0.01
    assert df["put_gex"].iloc[0] == pytest.approx(expected)     # magnitude
    assert df["net_gex"].iloc[0] == pytest.approx(-expected)    # signed


def test_absolute_exposure_adds_the_sides_and_net_cancels_them():
    """The distinction between the two chart views. A strike with equal call
    and put gamma has NO net push and a LOT of hedging sitting on it; showing
    only one of those is how a wall gets mistaken for empty space."""
    df = gex.by_strike(_chain([
        {"strike": 7700, "right": "C", "gamma": 0.002, "oi": 500},
        {"strike": 7700, "right": "P", "gamma": 0.002, "oi": 500},
    ]), SPOT)
    assert len(df) == 1
    assert df["net_gex"].iloc[0] == pytest.approx(0.0)
    assert df["abs_gex"].iloc[0] > 0


def test_strikes_come_back_in_ascending_order():
    df = gex.by_strike(_chain([
        {"strike": 7800, "right": "C"},
        {"strike": 7600, "right": "C"},
        {"strike": 7700, "right": "C"},
    ]), SPOT)
    assert list(df["strike"]) == [7600.0, 7700.0, 7800.0]


def test_every_expiry_is_summed_when_none_is_named():
    df = gex.by_strike(_chain([
        {"strike": 7700, "right": "C", "oi": 100, "expiry": "2026-09-18"},
        {"strike": 7700, "right": "C", "oi": 300, "expiry": "2026-10-16"},
    ]), SPOT)
    assert len(df) == 1
    assert df["call_oi"].iloc[0] == 400


def test_naming_an_expiry_selects_only_that_contract():
    """The third Friday lists two different options and the display key is
    what tells them apart (ADR-046). Filtering on a bare date here would
    silently merge them back together."""
    chain = _chain([
        {"strike": 7700, "right": "C", "oi": 100, "expiry": "2026-09-18"},
        {"strike": 7700, "right": "C", "oi": 300, "expiry": "2026-09-18 (AM)"},
    ])
    assert gex.by_strike(chain, SPOT, expiry="2026-09-18")["call_oi"].iloc[0] == 100
    assert gex.by_strike(chain, SPOT, expiry="2026-09-18 (AM)")["call_oi"].iloc[0] == 300


# ─────────────────────────────────────────────────────────────────────────────
# Absent data. Two different absences that must NOT be treated alike.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_contract_without_gamma_is_dropped_rather_than_counted_as_zero():
    """Unknown is not nothing. A missing gamma contributes no computable
    exposure; counting it as 0 would report a confident figure built partly on
    data we do not have."""
    df = gex.by_strike(_chain([
        {"strike": 7700, "right": "C", "gamma": None},
        {"strike": 7800, "right": "C", "gamma": 0.001},
    ]), SPOT)
    assert list(df["strike"]) == [7800.0]


def test_a_contract_without_open_interest_is_kept_as_a_real_zero():
    """The other direction, and deliberately so. No open interest is a fact
    about the strike, not a gap in the record — the strike must still appear
    on the axis, with an honest empty bar."""
    df = gex.by_strike(_chain([{"strike": 7700, "right": "C", "oi": None}]), SPOT)
    assert list(df["strike"]) == [7700.0]
    assert df["call_gex"].iloc[0] == 0.0


def test_an_empty_chain_returns_the_full_column_set():
    """Empty in, empty out — WITH columns, so no caller has to write the
    empty case twice."""
    df = gex.by_strike(pd.DataFrame(), SPOT)
    assert df.empty
    assert list(df.columns) == gex.COLUMNS


def test_a_chain_missing_the_gamma_column_entirely_is_not_a_crash():
    df = gex.by_strike(pd.DataFrame({"strike": [7700], "right": ["C"]}), SPOT)
    assert df.empty
    assert list(df.columns) == gex.COLUMNS


# ─────────────────────────────────────────────────────────────────────────────
# The strike window
# ─────────────────────────────────────────────────────────────────────────────

def test_the_window_keeps_the_strikes_nearest_spot():
    df = gex.by_strike(_chain([
        {"strike": s, "right": "C"} for s in (7500, 7600, 7700, 7800, 7900)
    ]), SPOT)
    assert list(gex.window(df, SPOT, 3)["strike"]) == [7600.0, 7700.0, 7800.0]


def test_the_window_returns_ascending_order_not_nearest_first():
    df = gex.by_strike(_chain([
        {"strike": s, "right": "C"} for s in (7500, 7600, 7700, 7800, 7900)
    ]), SPOT)
    got = list(gex.window(df, SPOT, 5)["strike"])
    assert got == sorted(got)


def test_a_window_wider_than_the_chain_hides_nothing():
    """Narrowing is a display convenience and must never be able to drop a
    strike the caller asked to see."""
    df = gex.by_strike(_chain([{"strike": s, "right": "C"}
                               for s in (7600, 7700, 7800)]), SPOT)
    assert len(gex.window(df, SPOT, 99)) == 3
    assert len(gex.window(df, SPOT, 0)) == 3


# ─────────────────────────────────────────────────────────────────────────────
# The gamma flip — the one figure here with a claimed directional meaning
# ─────────────────────────────────────────────────────────────────────────────

def test_the_flip_is_interpolated_between_the_straddling_strikes():
    """Reporting the nearer listed strike would quantise the level to the
    strike spacing — 5 points on SPX, which is most of a day's range."""
    df = pd.DataFrame({"strike": [7600.0, 7700.0], "net_gex": [-100.0, 300.0]})
    # Cumulative runs -100 then +200. Zero sits 100/300 of the way across the
    # 100-point gap, so the flip is 7633.33 and NOT either listed strike.
    assert gex.flip_strike(df) == pytest.approx(7600.0 + 100.0 / 3.0)


def test_a_chain_that_never_changes_sign_has_no_flip():
    """A real market state, not a failure — and None is the honest answer, not
    the lowest strike."""
    df = pd.DataFrame({"strike": [7600.0, 7700.0], "net_gex": [100.0, 300.0]})
    assert gex.flip_strike(df) is None


def test_an_empty_frame_has_no_flip():
    assert gex.flip_strike(gex.by_strike(pd.DataFrame(), SPOT)) is None


# ─────────────────────────────────────────────────────────────────────────────
# The headline figures
# ─────────────────────────────────────────────────────────────────────────────

def test_sentiment_counts_positive_strikes_rather_than_weighing_dollars():
    """Option Alpha's published definition: "the % of bars nearest to the
    current price that are positive". A COUNT of strikes, not a share of
    exposure. An earlier version here weighed dollars, which gives a very
    different answer on a chain where one huge strike outweighs ten small
    ones — exactly the case the figure exists to describe."""
    df = gex.by_strike(_chain([
        {"strike": 7600, "right": "C", "gamma": 0.0001, "oi": 1},    # +, tiny
        {"strike": 7700, "right": "C", "gamma": 0.0001, "oi": 1},    # +, tiny
        {"strike": 7800, "right": "P", "gamma": 0.9000, "oi": 9999},  # -, huge
    ]), SPOT)
    s = gex.summary(df)
    assert s["sentiment"] == pytest.approx(200.0 / 3.0)   # 2 of 3 bars positive
    assert s["positive_bars"] == 2
    assert s["total_bars"] == 3


def test_the_ratio_is_larger_over_smaller_signed_by_the_winning_side():
    """Their two worked examples, verbatim: "2b neg and 3b pos = 3b / 2b =
    1.5x (green)" and "3b neg and 2b pos = -3b / 2b = -1.5x (red)". Note it is
    NOT call divided by put — an earlier version here computed that, which
    agrees only by coincidence and never reports a negative."""
    positive_wins = pd.DataFrame({"strike": [1.0, 2.0], "net_gex": [3.0, -2.0],
                                  "call_gex": [3.0, 0.0], "put_gex": [0.0, 2.0],
                                  "abs_gex": [3.0, 2.0]})
    assert gex.summary(positive_wins)["ratio"] == pytest.approx(1.5)

    negative_wins = pd.DataFrame({"strike": [1.0, 2.0], "net_gex": [2.0, -3.0],
                                  "call_gex": [2.0, 0.0], "put_gex": [0.0, 3.0],
                                  "abs_gex": [2.0, 3.0]})
    assert gex.summary(negative_wins)["ratio"] == pytest.approx(-1.5)


def test_the_figures_follow_the_window_they_are_given():
    """The whole reason summary() takes the DISPLAYED frame. Their docs:
    "It only includes displayed bars so it can be adjusted for only closest to
    the current price." A figure computed over the full chain would not move
    when the reader narrows the window, and would disagree with the vendor."""
    full = gex.by_strike(_chain([
        {"strike": 7300, "right": "P", "gamma": 0.9, "oi": 9999},   # far, huge
        {"strike": 7700, "right": "C", "gamma": 0.001, "oi": 100},
    ]), SPOT)
    assert gex.summary(full)["sentiment"] == pytest.approx(50.0)
    narrowed = gex.window(full, SPOT, 1)
    assert gex.summary(narrowed)["sentiment"] == pytest.approx(100.0)


def test_a_chain_with_no_put_gamma_reports_no_ratio_rather_than_infinity():
    """An infinite ratio is not a number to put on screen."""
    df = gex.by_strike(_chain([{"strike": 7700, "right": "C"}]), SPOT)
    s = gex.summary(df)
    assert s["ratio"] is None
    assert s["sentiment"] == pytest.approx(100.0)


def test_the_peak_strike_names_the_side_that_dominates_there():
    df = gex.by_strike(_chain([
        {"strike": 7700, "right": "C", "gamma": 0.001, "oi": 10},
        {"strike": 7800, "right": "P", "gamma": 0.005, "oi": 900},
    ]), SPOT)
    s = gex.summary(df)
    assert s["peak_strike"] == 7800.0
    assert s["peak_side"] == "Put"


def test_every_summary_figure_is_none_when_there_is_nothing_to_measure():
    """Not zero. A chain with no gamma and a perfectly balanced chain are
    different states, and showing 0 for both breaks the blank-not-zero rule
    the whole project runs on."""
    s = gex.summary(gex.by_strike(pd.DataFrame(), SPOT))
    assert set(s) == {"net_gex", "call_gex", "put_gex", "abs_gex", "ratio",
                      "sentiment", "peak_strike", "peak_side", "flip_strike",
                      "positive_bars", "total_bars"}
    assert all(v is None for v in s.values())


# ─────────────────────────────────────────────────────────────────────────────
# The delegation. This is the test that justified touching a shipped header.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_header_strike_is_unchanged_by_the_rescaling():
    """`max_gex_label` used to scale per one POINT and now delegates to a
    module scaling per one PERCENT. The claim that let that happen is that one
    positive constant applied to every row cannot reorder magnitudes. Pinned
    here against the OLD arithmetic computed independently, rather than left
    as an argument in a docstring."""
    chain = _chain([
        {"strike": 7600, "right": "C", "gamma": 0.0011, "oi": 4000},
        {"strike": 7700, "right": "P", "gamma": 0.0090, "oi": 9000},
        {"strike": 7800, "right": "C", "gamma": 0.0030, "oi": 2000},
    ])

    old = (chain["gamma"] * chain["open_interest"] * 100 * SPOT
           * chain["right"].map({"C": 1, "P": -1}))
    by_old = old.groupby(chain["strike"]).sum()
    old_peak = by_old.abs().idxmax()
    old_side = "Call" if by_old[old_peak] > 0 else "Put"

    assert market.max_gex_label(chain, SPOT) == f"{old_peak:,.0f} ({old_side})"


def test_the_header_still_says_na_when_the_chain_carries_no_gamma():
    """The pre-existing behaviour the header depends on, kept through the
    move. Absent gamma is normal for some snapshots."""
    chain = _chain([{"strike": 7700, "right": "C", "gamma": None}])
    assert market.max_gex_label(chain, SPOT) == "N/A"
    assert market.max_gex_label(pd.DataFrame(), SPOT) == "N/A"


# ─────────────────────────────────────────────────────────────────────────────
# The panels added for the intraday and positioning charts.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_cumulative_curve_crosses_zero_exactly_where_the_flip_is():
    """These two are one claim stated twice — the curve is drawn, the flip is
    labelled, and a reader takes the label to be where the line crosses. If
    they ever disagree the chart lies without erroring, so pin them together
    rather than separately."""
    df = gex.by_strike(_chain([
        {"strike": 7600, "right": "P", "gamma": 0.002},
        {"strike": 7700, "right": "C", "gamma": 0.001},
        {"strike": 7800, "right": "C", "gamma": 0.003},
    ]), SPOT)
    curve = gex.cumulative_net(df)
    flip = gex.flip_strike(df)
    below = df.loc[curve.lt(0.0), "strike"].max()
    above = df.loc[curve.gt(0.0), "strike"].min()
    assert below < flip < above


def test_the_cumulative_curve_is_empty_rather_than_absent_for_an_empty_chain():
    assert gex.cumulative_net(gex.by_strike(_chain([]), SPOT)).empty


def test_the_dollar_scale_is_the_same_constant_by_strike_already_applies():
    """dollar_scale exists so the SQL aggregation can be scaled outside this
    module. The moment it drifts from what by_strike does, the intraday panels
    and the per-strike panels quote different dollars for the same market."""
    df = gex.by_strike(_chain([
        {"strike": 7700, "right": "C", "gamma": 0.001, "oi": 1000},
    ]), SPOT)
    assert df["call_gex"].iloc[0] == pytest.approx(0.001 * 1000 * gex.dollar_scale(SPOT))


def _delta_chain(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {"expiry": r.get("expiry", "2026-09-18"), "strike": r["strike"],
         "right": r["right"], "delta": r["delta"],
         "open_interest": r.get("oi", 1000)}
        for r in rows
    ])


def test_delta_exposure_leaves_a_puts_negative_delta_negative():
    """The one mistake in this module that stays plausible while being
    backwards: applying GEX's dealer sign to delta as well. A put's delta is
    already negative, so a second -1 flips every put to a positive
    contribution and the net comes out with the wrong sign."""
    df = gex.dex_by_strike(_delta_chain([
        {"strike": 7700, "right": "P", "delta": -0.4, "oi": 100},
    ]), SPOT)
    assert df["put_dex"].iloc[0] == pytest.approx(-0.4 * 100 * 100 * SPOT)
    assert df["net_dex"].iloc[0] < 0


def test_delta_exposure_sums_both_sides_at_one_strike_and_keeps_abs_positive():
    df = gex.dex_by_strike(_delta_chain([
        {"strike": 7700, "right": "C", "delta": 0.6, "oi": 100},
        {"strike": 7700, "right": "P", "delta": -0.4, "oi": 100},
    ]), SPOT)
    assert len(df) == 1
    unit = 100 * 100 * SPOT
    assert df["net_dex"].iloc[0] == pytest.approx(0.2 * unit)
    assert df["abs_dex"].iloc[0] == pytest.approx(1.0 * unit)


def test_delta_exposure_returns_the_shaped_blank_when_delta_was_never_stored():
    df = gex.dex_by_strike(_chain([{"strike": 7700, "right": "C"}]), SPOT)
    assert df.empty
    assert list(df.columns) == ["strike", "call_dex", "put_dex", "net_dex", "abs_dex"]


def test_open_interest_change_treats_a_strike_absent_yesterday_as_all_new():
    today = pd.DataFrame({"strike": [7700.0, 7800.0],
                          "call_oi": [500.0, 200.0], "put_oi": [100.0, 0.0]})
    prior = pd.DataFrame({"strike": [7700.0],
                          "call_oi": [300.0], "put_oi": [150.0]})
    out = gex.oi_change(today, prior).set_index("strike")
    assert out.loc[7700.0, "call_oi_change"] == 200.0
    assert out.loc[7700.0, "put_oi_change"] == -50.0
    assert out.loc[7800.0, "call_oi_change"] == 200.0   # not listed yesterday


def test_a_strike_that_has_gone_is_dropped_rather_than_reported_as_a_closing():
    """"Not listed" and "listed at zero" are different facts. Reporting the
    first as a collapse to zero would invent the largest closing on the chart
    out of an expiry rolling off."""
    today = pd.DataFrame({"strike": [7700.0], "call_oi": [500.0], "put_oi": [0.0]})
    prior = pd.DataFrame({"strike": [7700.0, 6000.0],
                          "call_oi": [500.0, 9999.0], "put_oi": [0.0, 0.0]})
    assert gex.oi_change(today, prior)["strike"].tolist() == [7700.0]


def test_key_strikes_rank_by_absolute_exposure_not_by_net():
    """A strike where calls and puts nearly cancel has a net near zero and is
    often the level being fought over — ranking by net would hide it."""
    df = gex.by_strike(_chain([
        {"strike": 7700, "right": "C", "gamma": 0.010},
        {"strike": 7700, "right": "P", "gamma": 0.009},   # nets to nearly nil
        {"strike": 7800, "right": "C", "gamma": 0.002},
    ]), SPOT)
    assert gex.key_strikes(df, 1) == [7700.0]


def test_key_strikes_asks_for_more_than_exist_and_gets_what_there_is():
    df = gex.by_strike(_chain([{"strike": 7700, "right": "C"}]), SPOT)
    assert gex.key_strikes(df, 6) == [7700.0]
    assert gex.key_strikes(df, 0) == []
    assert gex.key_strikes(gex.by_strike(_chain([]), SPOT)) == []
