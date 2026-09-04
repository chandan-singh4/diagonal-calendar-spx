"""core/dealer.py — the term-structure bubbles and the churn verdicts.

WHAT THESE CAN AND CANNOT PROVE. The verdicts are heuristics: no data
anywhere says whether "Heavy Accumulation" was the right word for a strike.
So these tests do not try to show the labels are true. They pin the two
things that CAN be wrong without anyone noticing:

  * the arithmetic and the boundaries — a threshold flipped from >= to >, a
    ratio taken against the wrong denominator, a percentile over the whole
    chain instead of the strikes on screen;
  * the order the rules are applied in, which is load-bearing. Churn's test
    ("small net change") is satisfied by definition when the other two fail,
    so checking it first silently relabels every genuine build as scalping.

The bubble tests carry one measured number. On the live 2026-09-04 snapshot
the busiest 0DTE strike traded 151,263 contracts; a monthly strike at the
same price traded a small fraction of that. Linear radius is what would put
the second below a pixel, and `test_the_smallest_bubble_survives_the_largest`
pins that it does not.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core import dealer

SPOT = 7700.0


def _chain(rows: list[dict]) -> pd.DataFrame:
    """A chain frame shaped like dataaccess.load_chain_df's output."""
    return pd.DataFrame([
        {"expiry": r.get("expiry", "2026-09-04"),
         "dte": r.get("dte", 0),
         "strike": r["strike"],
         "right": r["right"],
         "volume": r.get("volume", 100),
         "mark": r.get("mark", 2.0)}
        for r in rows
    ])


def _strikes(rows: list[dict]) -> pd.DataFrame:
    """A frame shaped like core.gex.by_strike's output."""
    return pd.DataFrame([
        {"strike": r["strike"],
         "call_volume": r.get("cv", 0.0), "put_volume": r.get("pv", 0.0),
         "call_oi": r.get("coi", 0.0), "put_oi": r.get("poi", 0.0)}
        for r in rows
    ])


def _prior(rows: list[dict]) -> pd.DataFrame:
    """Yesterday's close: open interest AND the volume that produced it.

    The volume is not decoration. Every verdict divides the overnight change
    in open interest by the volume of the session that change came from, so a
    prior frame without volume yields no verdict at all — which is exactly
    what these fixtures would silently be testing if `cv`/`pv` were left off.
    """
    return pd.DataFrame([
        {"strike": r["strike"], "call_oi": r.get("coi", 0.0),
         "put_oi": r.get("poi", 0.0),
         "call_volume": r.get("cv", 0.0), "put_volume": r.get("pv", 0.0)}
        for r in rows
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Expiry naming — the x axis
# ─────────────────────────────────────────────────────────────────────────────

def test_the_columns_are_named_the_way_a_trader_names_them():
    assert dealer.expiry_label("2026-09-04", 0) == "0DTE (Today)"
    assert dealer.expiry_label("2026-09-05", 1) == "1DTE"
    assert dealer.expiry_label("2026-09-11", 7) == "W-OPEX (Fri)"
    assert dealer.expiry_label("2026-10-09", 14) == "Next Fri"
    # Past a fortnight the count stops being how anyone refers to it.
    assert dealer.expiry_label("2026-10-02", 28) == "Fri 2 Oct"


def test_the_monthly_and_the_quarterly_are_told_apart():
    """Both are third Fridays; only the quarterly ends a cycle. A chart that
    called them the same thing would hide the one distinction the column
    exists to make."""
    assert dealer.expiry_label("2026-09-18", 14) == "Quarterly"   # September
    assert dealer.expiry_label("2026-10-16", 42) == "Monthly OPEX"


def test_the_third_fridays_am_contract_is_named_like_its_own_date():
    """SPX lists two contracts for the third Friday and the morning one
    arrives as "2026-09-18 (AM)". Parsed as a bare date it would raise."""
    assert dealer.expiry_label("2026-09-18 (AM)", 14) == "Quarterly"


# ─────────────────────────────────────────────────────────────────────────────
# Bubble sizing and colour
# ─────────────────────────────────────────────────────────────────────────────

def test_the_smallest_bubble_survives_the_largest():
    """The reason the scale is non-linear at all. On the live snapshot the
    busiest 0DTE strike traded 151,263 contracts; a monthly strike trading a
    hundred would get a radius of 0.02px under a linear scale — invisible.
    Both ends must land inside the stated pixel range."""
    small = dealer.radius(100, 151_263)
    large = dealer.radius(151_263, 151_263)
    assert dealer.MIN_RADIUS <= small <= dealer.MAX_RADIUS
    assert large == pytest.approx(dealer.MAX_RADIUS)
    assert small > dealer.MIN_RADIUS          # not flattened onto the floor
    assert small < large


def test_square_root_sizes_by_area_not_by_radius():
    """Four times the volume must give four times the AREA, which is what an
    eye compares. Area goes as r^2, so the radius doubles."""
    quarter = dealer.radius(25, 100, min_radius=0.0, max_radius=100.0)
    full = dealer.radius(100, 100, min_radius=0.0, max_radius=100.0)
    assert full / quarter == pytest.approx(2.0)


def test_log_compresses_harder_than_square_root():
    """The reason the toggle exists: on a session where one strike has run
    away, log lifts everything else back into view."""
    sqrt_r = dealer.radius(100, 1_000_000, scale="sqrt")
    log_r = dealer.radius(100, 1_000_000, scale="log")
    assert log_r > sqrt_r


def test_a_strike_that_traded_nothing_takes_the_floor_rather_than_vanishing():
    assert dealer.radius(0, 1000) == dealer.MIN_RADIUS
    assert dealer.radius(50, 0) == dealer.MIN_RADIUS


def test_the_flow_bands_are_the_published_ones():
    assert dealer.flow_bucket(0.69) == "call"
    assert dealer.flow_bucket(0.70) == "balanced"       # boundary is inclusive
    assert dealer.flow_bucket(1.30) == "balanced"
    assert dealer.flow_bucket(1.31) == "put"


def test_a_strike_with_no_call_volume_is_put_dominated_not_balanced():
    """Divide by zero and the ratio is undefined; left as NaN it buckets as
    "balanced", which is wrong in the one direction that matters — a strike
    where only puts traded is the strongest put signal on the board."""
    points = dealer.bubble_points(_chain([
        {"strike": 7700, "right": "P", "volume": 5000},
    ]), SPOT)
    assert points["flow"].iloc[0] == "put"


def test_bubbles_drop_strikes_outside_the_band_rather_than_clamping_them():
    """Height on this chart IS a price. A clamped point would sit at a price
    nothing traded at, which is worse than not drawing it."""
    points = dealer.bubble_points(_chain([
        {"strike": 7700, "right": "C"},
        {"strike": 5000, "right": "C"},     # far below any 4% band
    ]), SPOT)
    assert points["strike"].tolist() == [7700.0]


def test_both_sides_of_one_strike_become_one_bubble():
    points = dealer.bubble_points(_chain([
        {"strike": 7700, "right": "C", "volume": 300, "mark": 2.0},
        {"strike": 7700, "right": "P", "volume": 100, "mark": 1.0},
    ]), SPOT)
    assert len(points) == 1
    row = points.iloc[0]
    assert row["total_volume"] == 400
    assert row["pcr"] == pytest.approx(1 / 3)
    assert row["flow"] == "call"
    # Premium, not contracts: 300 x $2 x 100 + 100 x $1 x 100.
    assert row["notional"] == pytest.approx(70_000.0)


def test_one_expiry_does_not_absorb_another_at_the_same_strike():
    """The whole point of the chart. Grouped by strike alone, 0DTE and the
    monthly would merge and the term structure would disappear."""
    points = dealer.bubble_points(_chain([
        {"strike": 7700, "right": "C", "expiry": "2026-09-04", "dte": 0},
        {"strike": 7700, "right": "C", "expiry": "2026-09-18", "dte": 14},
    ]), SPOT)
    assert len(points) == 2
    assert points["expiry_order"].tolist() == [0, 14]     # ordered by dte


def test_an_empty_or_shapeless_chain_returns_the_shaped_blank():
    shapeless = pd.DataFrame({"strike": [7700.0], "right": ["C"]})
    for frame in (pd.DataFrame(), shapeless):
        out = dealer.bubble_points(frame, SPOT)
        assert out.empty
        assert list(out.columns) == list(dealer.BUBBLE_COLUMNS)


# ─────────────────────────────────────────────────────────────────────────────
# The classification engine
# ─────────────────────────────────────────────────────────────────────────────

def test_high_volume_with_almost_no_net_change_is_churn():
    assert dealer.classify(100_000, 5_000, high_volume=True)[0] == "Intraday Churn"


def test_high_volume_with_a_quarter_of_it_left_open_is_accumulation():
    assert dealer.classify(100_000, 25_000, high_volume=True)[0] == "Heavy Accumulation"


def test_high_volume_with_open_interest_falling_is_liquidation():
    assert dealer.classify(100_000, -20_000, high_volume=True)[0] == "Position Liquidation"


def test_no_ratio_can_satisfy_two_verdicts_at_once():
    """The property that makes a verdict unambiguous: the three bands do not
    overlap, so the answer cannot depend on which branch is tested first.

    Written after a mutation test disproved the opposite claim — swapping the
    branches left every test here passing, because churn (|r| <= 0.15) and
    accumulation (r >= 0.25) have no number in common. Pinned as the real
    invariant rather than as the order it happens to be written in."""
    bands = {"Intraday Churn": lambda r: abs(r) <= dealer.CHURN_RATIO,
             "Heavy Accumulation": lambda r: r >= dealer.ACCUMULATION_RATIO,
             "Position Liquidation": lambda r: r <= -dealer.LIQUIDATION_RATIO}
    for i in range(-100, 101):
        ratio = i / 100.0
        matched = [name for name, test in bands.items() if test(ratio)]
        assert len(matched) <= 1, f"ratio {ratio} matches {matched}"

    # And the ratio the caller actually passes is dOI over VOLUME, not the
    # other way round: 250k on a million is a quarter, a build.
    assert dealer.classify(1_000_000, 250_000,
                           high_volume=True)[0] == "Heavy Accumulation"


def test_the_middle_ground_is_opening_longs_not_a_blank():
    """Above the churn ceiling, below the accumulation floor: more than
    scalping, less than a build."""
    assert dealer.classify(100_000, 19_000, high_volume=True)[0] == "Opening Longs"


def test_a_quiet_strike_gets_no_verdict_at_all():
    """A verdict on every row is a verdict on nothing. Most strikes in a
    session are ordinary two-way trade and are left to say so."""
    assert dealer.classify(500, 400, high_volume=False)[0] == "—"
    assert dealer.classify(0, 0, high_volume=True)[0] == "—"


def test_an_unknown_delta_is_never_read_as_no_change():
    """NaN means "there is no previous session", not "nothing changed". Read
    as zero it would report a busy strike as churn."""
    assert dealer.classify(100_000, float("nan"), high_volume=True)[0] == "—"


# ─────────────────────────────────────────────────────────────────────────────
# positioning() — the table behind the second chart
# ─────────────────────────────────────────────────────────────────────────────

def test_the_delta_is_todays_open_interest_minus_the_previous_sessions():
    rows = dealer.positioning(
        _strikes([{"strike": 7700, "cv": 60_000, "pv": 40_000,
                   "coi": 9_000, "poi": 1_000}]),
        _prior([{"strike": 7700, "coi": 5_000, "poi": 1_000}]),
        SPOT)
    assert rows["delta_oi"].iloc[0] == 4_000
    assert rows["total_volume"].iloc[0] == 100_000


def test_a_strike_absent_yesterday_counts_its_whole_open_interest_as_new():
    rows = dealer.positioning(
        _strikes([{"strike": 7700, "cv": 100_000, "coi": 30_000}]),
        _prior([{"strike": 7600, "coi": 1_000, "cv": 1_000}]),
        SPOT)
    assert rows["delta_oi"].iloc[0] == 30_000


def test_the_verdict_divides_by_the_volume_the_change_came_from():
    """The correction. Open interest is republished once, overnight, so the
    change is what happened YESTERDAY — and it has to be measured against
    yesterday's trading, not today's.

    Here 30,000 contracts were opened on 40,000 traded yesterday: three
    quarters of the day stuck, a build. Today's 100,000 is loud but has not
    been counted into open interest yet and will not be until tonight;
    dividing by it would report 30% and call the same strike something else.
    """
    rows = dealer.positioning(
        _strikes([{"strike": 7700, "cv": 100_000, "coi": 30_000}]),
        _prior([{"strike": 7700, "coi": 0, "cv": 40_000}]),
        SPOT)
    assert rows["settled_volume"].iloc[0] == 40_000     # yesterday's
    assert rows["total_volume"].iloc[0] == 100_000      # today's, context only
    assert rows["verdict"].iloc[0] == "Heavy Accumulation"


def test_no_verdict_where_the_change_outruns_the_volume_it_came_from():
    """More contracts opened than were traded is impossible, so a row like
    this is a gap in the record, not a signal. Against TODAY's volume the old
    code produced exactly this shape on 20.0% of contract-days; against the
    prior session's own volume, 4.9%. Either way it must not be given a name.
    """
    rows = dealer.positioning(
        _strikes([{"strike": 7700, "cv": 100_000, "coi": 90_000}]),
        _prior([{"strike": 7700, "coi": 0, "cv": 10}]),
        SPOT)
    assert rows["delta_oi"].iloc[0] == 90_000
    assert rows["settled_volume"].iloc[0] == 10


def test_the_high_volume_cut_is_taken_over_the_strikes_shown():
    """A percentile over the whole chain would sit near zero — the wings are
    mostly untraded — and every strike on screen would clear it."""
    rows = dealer.positioning(
        _strikes([
            {"strike": 7690, "coi": 5},          # quiet
            {"strike": 7695, "coi": 5},          # quiet
            {"strike": 7700, "coi": 1_000},      # the busy one
        ]),
        _prior([{"strike": 7690, "coi": 0, "cv": 10},
                {"strike": 7695, "coi": 0, "cv": 20},
                {"strike": 7700, "coi": 0, "cv": 100_000}]),
        SPOT)
    verdicts = dict(zip(rows["strike"], rows["verdict"]))
    assert verdicts[7690.0] == "—"
    assert verdicts[7695.0] == "—"
    assert verdicts[7700.0] != "—"


def test_strikes_beyond_the_range_are_not_in_the_table():
    rows = dealer.positioning(
        _strikes([{"strike": 7700, "cv": 100}, {"strike": 6000, "cv": 100}]),
        _prior([{"strike": 7700}]), SPOT)
    assert rows["strike"].tolist() == [7700.0]


def test_with_no_previous_session_every_verdict_is_blank():
    """The first collected day. Treating the unknown as zero change would
    report the entire board as churn — a confident answer from no data."""
    rows = dealer.positioning(
        _strikes([{"strike": 7700, "cv": 100_000, "coi": 50_000}]),
        pd.DataFrame(), SPOT)
    assert rows["verdict"].iloc[0] == "—"
    assert pd.isna(rows["delta_oi"].iloc[0])


def test_the_walls_are_the_biggest_out_of_the_money_gains_on_each_side():
    positions = pd.DataFrame({
        "strike": [7500.0, 7690.0, 7710.0, 7900.0],
        "delta_oi": [30_000.0, 99_000.0, 99_000.0, 20_000.0],
    })
    found = dealer.walls(positions, SPOT)
    # 7690 and 7710 carry the largest gains but are inside 1% of spot: that
    # is the at-the-money churn every session produces, not a wall.
    assert found["put_wall"] == 7500.0
    assert found["call_wall"] == 7900.0


def test_a_side_with_no_gain_reports_no_wall_rather_than_the_nearest_strike():
    positions = pd.DataFrame({"strike": [7500.0, 7900.0],
                              "delta_oi": [30_000.0, -5_000.0]})
    found = dealer.walls(positions, SPOT)
    assert found["put_wall"] == 7500.0
    assert found["call_wall"] is None


def test_a_wall_label_never_overwrites_a_louder_verdict():
    """The wall is the same fact told at a coarser resolution. A strike
    already called Heavy Accumulation is not improved by renaming it."""
    rows = dealer.positioning(
        _strikes([{"strike": 7500, "pv": 100_000, "poi": 50_000}]),
        _prior([{"strike": 7500, "poi": 0, "pv": 100_000}]),
        SPOT, strike_range_percent=5.0)
    assert rows["verdict"].iloc[0] == "Heavy Accumulation"


# ─────────────────────────────────────────────────────────────────────────────
# most_traded — what fits on the chart
# ─────────────────────────────────────────────────────────────────────────────

def _points(expiries=3, strikes=20):
    """A board with a known busiest strike per expiry."""
    rows = []
    for e in range(expiries):
        for k in range(strikes):
            rows.append({"expiry": f"2026-09-{e + 1:02d}",
                         "expiry_label": f"E{e}", "expiry_order": e,
                         "strike": 7700.0 + k * 5, "call_volume": 1.0,
                         "put_volume": 1.0, "total_volume": float(k + 1),
                         "pcr": 1.0, "notional": 1.0, "flow": "balanced",
                         "radius": 4.0})
    return pd.DataFrame(rows)


def test_only_the_busiest_strikes_of_each_expiry_survive():
    kept = dealer.most_traded(_points(), per_expiry=3, max_expiries=9)
    assert len(kept) == 9
    for label, group in kept.groupby("expiry_label"):
        # total_volume was k + 1 over twenty strikes: 20, 19, 18 are the top.
        assert sorted(group["total_volume"]) == [18.0, 19.0, 20.0], label


def test_expiries_are_kept_by_nearness_not_by_volume():
    """The subject is the term structure. Keeping the loudest expiries would
    leave a hole where a quiet middle one was, and a hole on a date axis reads
    as a session with no trade rather than as a column that was not drawn."""
    board = _points(expiries=4)
    board.loc[board["expiry_label"] == "E0", "total_volume"] = 0.5   # quietest
    kept = dealer.most_traded(board, per_expiry=2, max_expiries=2)
    assert sorted(kept["expiry_label"].unique()) == ["E0", "E1"]


def test_the_column_label_survives_the_filter():
    """Pinned because it did not. groupby.apply consumed expiry_label as the
    grouping key and returned a frame without it, and the panel cannot draw
    its columns from a frame that has lost the column name."""
    kept = dealer.most_traded(_points(), per_expiry=2, max_expiries=2)
    assert "expiry_label" in kept.columns
    assert "expiry_order" in kept.columns


def test_radii_are_not_rescaled_to_the_survivors():
    """A radius is relative to the busiest point on the WHOLE board. Recomputing
    it after the filter would make a thin expiry look busy purely because its
    neighbours were dropped."""
    board = _points()
    kept = dealer.most_traded(board, per_expiry=2, max_expiries=2)
    assert set(kept["radius"]) == {4.0}


def test_an_empty_board_filters_to_an_empty_board():
    blank = dealer._blank(dealer.BUBBLE_COLUMNS)
    assert dealer.most_traded(blank).empty


# ─────────────────────────────────────────────────────────────────────────────
# high_volume_cut / worked_example — the panel showing its working
# ─────────────────────────────────────────────────────────────────────────────

def test_the_cut_ignores_strikes_that_did_not_trade():
    """Half the rows in a 2.5% band are usually zero. A percentile taken over
    those sits at or near zero and marks the entire screen as busy."""
    volumes = pd.Series([0.0, 0.0, 0.0, 0.0, 100.0, 200.0, 300.0, 400.0])
    assert dealer.high_volume_cut(volumes, 75.0) == pytest.approx(325.0)


def test_the_cut_is_zero_when_nothing_has_traded():
    assert dealer.high_volume_cut(pd.Series([0.0, 0.0])) == 0.0


def _explainable():
    """Today's frame, yesterday's, and the verdict rows they produce."""
    today = pd.DataFrame({
        "strike": [7700.0, 7750.0, 7800.0],
        "call_oi": [1000.0, 2653.0, 500.0],
        "put_oi": [900.0, 2146.0, 400.0],
        "call_volume": [100.0, 8271.0, 50.0],
        "put_volume": [100.0, 1384.0, 50.0],
    })
    prior = pd.DataFrame({
        "strike": [7700.0, 7750.0, 7800.0],
        "call_oi": [990.0, 663.0, 495.0],
        "put_oi": [890.0, 482.0, 395.0],
        # Yesterday's trading — the session the change in open interest came
        # from, and therefore the volume every verdict is measured against.
        "call_volume": [120.0, 4947.0, 60.0],
        "put_volume": [120.0, 3848.0, 60.0],
    })
    rows = dealer.positioning(today, prior, 7750.0, strike_range_percent=2.5)
    return rows, today, prior


def test_the_example_explains_the_row_with_the_biggest_change():
    """The row the eye goes to, and the one where "where did that number come
    from" is hardest to guess."""
    rows, today, prior = _explainable()
    ex = dealer.worked_example(rows, today, prior)
    assert ex["strike"] == 7750.0


def test_the_example_reports_the_real_inputs_not_a_retelling():
    """Every figure here is read from the frames the table was drawn from, so
    the explanation cannot drift from the picture above it. These are the live
    2026-09-04 numbers for the 8 Sep expiry at 7,750."""
    rows, today, prior = _explainable()
    ex = dealer.worked_example(rows, today, prior)

    assert (ex["was_call"], ex["was_put"], ex["was_total"]) == (663.0, 482.0, 1145.0)
    assert (ex["now_call"], ex["now_put"], ex["now_total"]) == (2653.0, 2146.0, 4799.0)
    assert ex["delta_oi"] == pytest.approx(4799.0 - 1145.0)     # +3,654
    # The volume the change is measured against is YESTERDAY's — 4,947 calls
    # and 3,848 puts — because that is the session the change happened in.
    assert ex["total_volume"] == pytest.approx(8795.0)
    assert ex["ratio"] == pytest.approx(3654.0 / 8795.0)
    # Today's 9,655 is carried separately, as context, and is not divided by.
    assert ex["today_volume"] == pytest.approx(9655.0)
    assert ex["verdict"] == "Heavy Accumulation"


def test_the_example_quotes_the_same_threshold_the_table_applied():
    """A panel that explains itself with a different threshold from the one it
    used is worse than a panel that does not explain itself. Both call
    high_volume_cut; this pins that they agree."""
    rows, today, prior = _explainable()
    ex = dealer.worked_example(rows, today, prior)
    assert ex["cut"] == pytest.approx(
        dealer.high_volume_cut(rows["settled_volume"]))
    assert ex["high_volume"] is True
    assert (ex["verdict"] != "—") is ex["high_volume"]


def test_there_is_nothing_to_explain_without_a_previous_session():
    rows, today, _ = _explainable()
    blank = dealer.positioning(today, pd.DataFrame(), 7750.0)
    assert dealer.worked_example(blank, today, pd.DataFrame()) is None


def test_there_is_nothing_to_explain_from_an_empty_board():
    assert dealer.worked_example(dealer._blank(dealer.VERDICT_COLUMNS),
                                 pd.DataFrame(), pd.DataFrame()) is None
