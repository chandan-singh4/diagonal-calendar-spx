"""core/dealer.py — the churn-versus-commitment verdicts.

WHAT THESE CAN AND CANNOT PROVE. The verdicts are heuristics: no data
anywhere says whether "Heavy Accumulation" was the right word for a strike.
So these tests do not try to show the labels are true. They pin what CAN be
wrong without anyone noticing — the arithmetic and the boundaries: a
threshold flipped from >= to >, a ratio taken against the wrong denominator,
a percentile computed over the whole chain instead of the strikes on screen.

They also pin that no ratio can satisfy two verdicts at once, which is what
makes a verdict unambiguous rather than an artefact of branch order. An
earlier version of this file claimed the order itself was load-bearing; a
mutation test that swapped the branches left every test passing, which
disproved it.
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
    return pd.DataFrame([
        {"strike": r["strike"], "call_oi": r.get("coi", 0.0),
         "put_oi": r.get("poi", 0.0)}
        for r in rows
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Expiry naming — the x axis
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Bubble sizing and colour
# ─────────────────────────────────────────────────────────────────────────────

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
        _prior([{"strike": 7600, "coi": 1_000}]),
        SPOT)
    assert rows["delta_oi"].iloc[0] == 30_000
    assert rows["verdict"].iloc[0] == "Heavy Accumulation"


def test_the_high_volume_cut_is_taken_over_the_strikes_shown():
    """A percentile over the whole chain would sit near zero — the wings are
    mostly untraded — and every strike on screen would clear it."""
    rows = dealer.positioning(
        _strikes([
            {"strike": 7690, "cv": 10, "coi": 5},          # quiet
            {"strike": 7695, "cv": 20, "coi": 5},          # quiet
            {"strike": 7700, "cv": 100_000, "coi": 1_000},  # the busy one
        ]),
        _prior([{"strike": s, "coi": 0} for s in (7690, 7695, 7700)]),
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
        _prior([{"strike": 7500, "poi": 0}]),
        SPOT, strike_range_percent=5.0)
    assert rows["verdict"].iloc[0] == "Heavy Accumulation"


# ─────────────────────────────────────────────────────────────────────────────
