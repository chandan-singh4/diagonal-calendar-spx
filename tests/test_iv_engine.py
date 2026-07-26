"""
Unit tests for iv_engine.py — the analytics core.

WHY THIS MODULE FIRST (M1.2): iv_engine is pure. Given data, it returns
numbers — no API, no database, no Streamlit. That makes it the only part of the
codebase testable today without scaffolding, and it is the seed of the
framework-agnostic core/ package that M2 builds out. Locking its behaviour down
now is what makes M2's refactor safe.

These tests assert CURRENT behaviour, including two places where current
behaviour looks wrong (marked FINDING below). They are written to fail loudly
if that behaviour changes, so a future fix is a deliberate decision with a
visible diff — not an accident.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pandas as pd
import pytest
from conftest import BACK, FRONT

import iv_engine as ive

# ─────────────────────────────────────────────────────────────────────────────
# atm_iv
# ─────────────────────────────────────────────────────────────────────────────

def test_atm_iv_averages_call_and_put_at_nearest_strike(chain_df, spot):
    # Spot 6000 sits exactly on the 6000 strike; call and put are both 18.0.
    assert ive.atm_iv(chain_df, FRONT, spot) == pytest.approx(18.0)
    assert ive.atm_iv(chain_df, BACK, spot) == pytest.approx(20.0)


def test_atm_iv_averages_across_the_two_sides_not_just_one(chain_df):
    # Make the call and put differ so a one-sided implementation would show up.
    df = chain_df.copy()
    mask = (df["expiry"] == FRONT) & (df["strike"] == 6000.0) & (df["side"] == "CALL")
    df.loc[mask, "iv"] = 22.0  # put stays 18.0 → mean 20.0
    assert ive.atm_iv(df, FRONT, 6000.0) == pytest.approx(20.0)


def test_atm_iv_picks_nearest_strike_when_spot_is_between_strikes(chain_df):
    # 6040 is closer to 6050 (10 away) than to 6000 (40 away).
    assert ive.atm_iv(chain_df, FRONT, 6040.0) == pytest.approx(19.0)


def test_atm_iv_raises_for_unknown_expiry(chain_df, spot):
    with pytest.raises(ValueError, match="No contracts found for expiry"):
        ive.atm_iv(chain_df, "2099-01-01", spot)


def test_atm_iv_raises_when_the_nearest_strike_has_no_iv(chain_df, spot):
    df = chain_df.copy()
    df.loc[(df["expiry"] == FRONT) & (df["strike"] == 6000.0), "iv"] = None
    with pytest.raises(ValueError, match="No IV data at strike"):
        ive.atm_iv(df, FRONT, spot)


def test_atm_iv_does_not_mutate_the_caller_s_dataframe(chain_df, spot):
    # atm_iv adds a dist_from_spot column internally; it must copy first.
    before = list(chain_df.columns)
    ive.atm_iv(chain_df, FRONT, spot)
    assert list(chain_df.columns) == before


# ─────────────────────────────────────────────────────────────────────────────
# term_structure / interpret_curve
# ─────────────────────────────────────────────────────────────────────────────

def test_term_structure_computes_spread_and_ratio():
    ts = ive.term_structure(front_iv=18.0, back_iv=20.0)
    assert ts.spread == pytest.approx(-2.0)
    assert ts.ratio == pytest.approx(0.9)


def test_term_structure_ratio_is_nan_when_back_iv_is_zero():
    ts = ive.term_structure(front_iv=18.0, back_iv=0.0)
    assert math.isnan(ts.ratio)


@pytest.mark.parametrize(
    "front, back, expected_shape",
    [
        (18.0, 20.0, "Contango"),        # ratio 0.90 — clearly below 0.95
        (18.9, 20.0, "Contango"),        # ratio 0.945 — just below the boundary
        (19.0, 20.0, "Flat"),            # ratio 0.95 — boundary is inclusive of flat
        (20.0, 20.0, "Flat"),            # ratio 1.00
        (21.0, 20.0, "Flat"),            # ratio 1.05 — upper boundary, still flat
        (21.1, 20.0, "Backwardation"),   # ratio 1.055 — just past the boundary
        (24.0, 20.0, "Backwardation"),
    ],
)
def test_interpret_curve_classifies_shape(front, back, expected_shape):
    text = ive.interpret_curve(ive.term_structure(front, back))
    assert expected_shape in text


def test_interpret_curve_never_asserts_favorability():
    """Favorability is an open, unvalidated question (DOCUMENTATION.md §3.1).

    An earlier version claimed ratio < 1.0 was 'favorable' on the strength of a
    single paper trade; that claim was retracted. This test exists to stop it
    creeping back in through wording.
    """
    for front, back in ((18.0, 20.0), (20.0, 20.0), (24.0, 20.0)):
        text = ive.interpret_curve(ive.term_structure(front, back)).lower()
        assert "unvalidated" in text
        for banned in ("favorable", "favourable", "good entry", "recommend", "signal to"):
            assert banned not in text


def test_interpret_curve_reports_backwardation_for_a_nan_ratio():
    """FINDING (M1.2, 2026-07-26): a NaN ratio is reported as backwardation.

    When back_iv is 0, term_structure() sets ratio to NaN. Every NaN comparison
    is False, so interpret_curve() falls through both branches to the final
    `else` and states 'Backwardation (inverted) — front IV above back.' as
    fact, with no indication the input was unusable.

    This test pins the CURRENT behaviour so the bug cannot be forgotten, and so
    that fixing it produces a visible, deliberate test change. It is not an
    endorsement — see backlog.
    """
    text = ive.interpret_curve(ive.term_structure(front_iv=18.0, back_iv=0.0))
    assert "Backwardation" in text


# ─────────────────────────────────────────────────────────────────────────────
# percentile_rank / sample_size_warning
# ─────────────────────────────────────────────────────────────────────────────

def test_percentile_rank_basic():
    history = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert ive.percentile_rank(history, 3.5) == pytest.approx(75.0)


def test_percentile_rank_is_strictly_less_than_so_ties_do_not_count():
    history = pd.Series([1.0, 2.0, 2.0, 2.0])
    # Only the 1.0 is strictly below 2.0 → 25%, not 100%.
    assert ive.percentile_rank(history, 2.0) == pytest.approx(25.0)


def test_percentile_rank_at_the_extremes():
    history = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert ive.percentile_rank(history, 0.0) == pytest.approx(0.0)
    assert ive.percentile_rank(history, 99.0) == pytest.approx(100.0)


def test_percentile_rank_ignores_nans():
    history = pd.Series([1.0, None, 3.0, None])
    assert ive.percentile_rank(history, 2.0) == pytest.approx(50.0)


def test_percentile_rank_is_nan_for_empty_history():
    assert math.isnan(ive.percentile_rank(pd.Series([], dtype=float), 5.0))
    assert math.isnan(ive.percentile_rank(pd.Series([None, None], dtype=float), 5.0))


def test_sample_size_warning_fires_below_the_threshold():
    msg = ive.sample_size_warning(pd.Series(range(199)), min_recommended=200)
    assert msg is not None
    assert "199" in msg


def test_sample_size_warning_is_silent_at_and_above_the_threshold():
    assert ive.sample_size_warning(pd.Series(range(200)), min_recommended=200) is None
    assert ive.sample_size_warning(pd.Series(range(500)), min_recommended=200) is None


def test_sample_size_warning_counts_only_non_nan_observations():
    # 150 real values padded with NaN must still warn — padding is not evidence.
    series = pd.Series(list(range(150)) + [None] * 100)
    assert ive.sample_size_warning(series, min_recommended=200) is not None


# ─────────────────────────────────────────────────────────────────────────────
# liquidity_score
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "volume, oi, expected",
    [
        (0, 0, 0.0),
        (500, 2000, 100.0),      # both exactly at their caps
        (5000, 20000, 100.0),    # far past the caps — must clamp, not exceed
        (250, 1000, 50.0),       # half of each
        (500, 0, 50.0),          # volume only
        (0, 2000, 50.0),         # OI only
    ],
)
def test_liquidity_score(volume, oi, expected):
    assert ive.liquidity_score(volume, oi) == pytest.approx(expected)


def test_liquidity_score_never_leaves_the_0_to_100_range():
    for v, oi in ((0, 0), (1, 1), (10**9, 10**9), (500, 2000)):
        assert 0.0 <= ive.liquidity_score(v, oi) <= 100.0


# ─────────────────────────────────────────────────────────────────────────────
# range_stats
# ─────────────────────────────────────────────────────────────────────────────

def test_range_stats_positions_current_within_the_range():
    rs = ive.range_stats(pd.Series([10.0, 20.0, 30.0]), current_value=25.0)
    assert (rs.low, rs.high) == (10.0, 30.0)
    assert rs.position_pct == pytest.approx(75.0)


def test_range_stats_clamps_values_outside_the_historical_range():
    series = pd.Series([10.0, 20.0, 30.0])
    assert ive.range_stats(series, 5.0).position_pct == pytest.approx(0.0)
    assert ive.range_stats(series, 99.0).position_pct == pytest.approx(100.0)


def test_range_stats_centres_when_the_range_is_degenerate():
    rs = ive.range_stats(pd.Series([7.0, 7.0, 7.0]), current_value=7.0)
    assert rs.position_pct == pytest.approx(50.0)


def test_range_stats_on_empty_history_is_nan_bounds_and_centred():
    rs = ive.range_stats(pd.Series([], dtype=float), current_value=42.0)
    assert math.isnan(rs.low) and math.isnan(rs.high)
    assert rs.current == 42.0
    assert rs.position_pct == pytest.approx(50.0)


# ─────────────────────────────────────────────────────────────────────────────
# strike_contract
# ─────────────────────────────────────────────────────────────────────────────

def test_strike_contract_exact_match(chain_df):
    sc = ive.strike_contract(chain_df, BACK, 6000, "CALL")
    assert sc.found_exact is True
    assert sc.strike == 6000.0
    assert sc.side == "CALL"
    assert sc.iv == pytest.approx(20.0)
    assert sc.mark == pytest.approx(25.0)  # stored mark, NOT the 20.0 midpoint


def test_strike_contract_computes_mark_from_bid_ask_when_absent(chain_df):
    # Front legs carry mark=None in the fixture → midpoint of 9.0/11.0.
    sc = ive.strike_contract(chain_df, FRONT, 6000, "CALL")
    assert sc.mark == pytest.approx(10.0)


def test_strike_contract_falls_back_to_nearest_strike(chain_df):
    sc = ive.strike_contract(chain_df, FRONT, 6037, "CALL")
    assert sc.found_exact is False
    assert sc.strike == 6050.0  # 13 away, vs 37 to 6000


def test_strike_contract_side_is_matched_case_insensitively(chain_df):
    sc = ive.strike_contract(chain_df, FRONT, 6000, "call")
    assert sc.found_exact is True
    assert sc.side == "CALL"


def test_strike_contract_returns_empty_contract_when_expiry_has_no_rows(chain_df):
    sc = ive.strike_contract(chain_df, "2099-01-01", 6000, "CALL")
    assert sc.found_exact is False
    assert sc.iv is None and sc.bid is None and sc.ask is None and sc.mark is None


def test_strike_contract_does_not_mutate_the_caller_s_dataframe(chain_df):
    before = list(chain_df.columns)
    ive.strike_contract(chain_df, FRONT, 6037, "CALL")  # takes the fallback path
    assert list(chain_df.columns) == before


# ─────────────────────────────────────────────────────────────────────────────
# calendar_edge
# ─────────────────────────────────────────────────────────────────────────────

def test_calendar_edge_signs_and_ratios(chain_df):
    ce = ive.calendar_edge(chain_df, FRONT, BACK, call_strike=6000, put_strike=6000)
    # Front 18.0 vs back 20.0 → negative edge (contango) on both sides.
    assert ce.call_edge == pytest.approx(-2.0)
    assert ce.put_edge == pytest.approx(-2.0)
    assert ce.call_ratio == pytest.approx(0.9)
    assert ce.put_ratio == pytest.approx(0.9)


def test_calendar_edge_supports_different_call_and_put_strikes(chain_df):
    ce = ive.calendar_edge(chain_df, FRONT, BACK, call_strike=6050, put_strike=5950)
    assert ce.front_call.strike == 6050.0
    assert ce.front_put.strike == 5950.0
    assert ce.call_edge == pytest.approx(19.0 - 21.0)
    assert ce.put_edge == pytest.approx(17.0 - 19.0)


def test_calendar_edge_is_none_when_an_iv_is_missing(chain_df):
    df = chain_df.copy()
    df.loc[(df["expiry"] == BACK) & (df["side"] == "CALL"), "iv"] = None
    ce = ive.calendar_edge(df, FRONT, BACK, 6000, 6000)
    assert ce.call_edge is None and ce.call_ratio is None
    assert ce.put_edge is not None  # put side unaffected


def test_calendar_edge_treats_zero_iv_as_missing(chain_df):
    """FINDING (M1.2, 2026-07-26): a real 0.0 IV is indistinguishable from absent.

    calendar_edge() guards with `if (fc.iv and bc.iv)`, which is a truthiness
    test, so an IV of exactly 0.0 takes the same path as None and yields
    edge=None. A 0.0 IV is not physically meaningful for a live SPX option, so
    the practical impact is nil today — but the same `and`-on-floats pattern is
    a latent trap if these guards are copied to a quantity where 0.0 IS valid
    (theta and edge both legitimately reach 0.0).

    Pinning current behaviour; see backlog.
    """
    df = chain_df.copy()
    df.loc[(df["expiry"] == BACK) & (df["side"] == "CALL"), "iv"] = 0.0
    ce = ive.calendar_edge(df, FRONT, BACK, 6000, 6000)
    assert ce.call_edge is None
    assert ce.back_call.iv == pytest.approx(0.0)  # the value did arrive intact


# ─────────────────────────────────────────────────────────────────────────────
# transform_credit
# ─────────────────────────────────────────────────────────────────────────────

def test_transform_credit_full_arithmetic(chain_df):
    tc = ive.transform_credit(
        chain_df, FRONT, BACK, call_strike=6000, put_strike=6000,
        entry_debit=10.0, threshold=5.0,
    )
    # back legs: mark 25.0 each → 50.0
    # close cost: front asks 11.0 each → 22.0
    # diagonal mark: 50 - 22 = 28.0 ; credit: 28 - 10 = 18.0
    assert tc.back_legs_value == pytest.approx(50.0)
    assert tc.close_cost == pytest.approx(22.0)
    assert tc.diagonal_mark == pytest.approx(28.0)
    assert tc.theoretical_credit == pytest.approx(18.0)
    assert tc.gap_to_threshold == pytest.approx(5.0 - 18.0)
    assert tc.is_viable is True


def test_transform_credit_subtracts_the_entry_debit(chain_df):
    """The viability metric is theoretical_credit, not the diagonal mark.

    The diagonal mark ignores what was paid to enter, so it overstates the
    locked profit (2026-06-23 review, corroborated by the 2026-06-25 audit).
    Raising only the entry debit must move the credit and leave the mark alone.
    """
    kwargs = dict(front_expiry=FRONT, back_expiry=BACK, call_strike=6000, put_strike=6000)
    cheap = ive.transform_credit(chain_df, entry_debit=10.0, **kwargs)
    dear = ive.transform_credit(chain_df, entry_debit=25.0, **kwargs)

    assert cheap.diagonal_mark == pytest.approx(dear.diagonal_mark)
    assert dear.theoretical_credit == pytest.approx(cheap.theoretical_credit - 15.0)


def test_transform_credit_viability_boundary_is_inclusive(chain_df):
    kwargs = dict(front_expiry=FRONT, back_expiry=BACK, call_strike=6000, put_strike=6000)
    # diagonal mark is 28.0, so entry_debit 23.0 gives a credit of exactly 5.0.
    exact = ive.transform_credit(chain_df, entry_debit=23.0, threshold=5.0, **kwargs)
    assert exact.theoretical_credit == pytest.approx(5.0)
    assert exact.is_viable is True

    just_under = ive.transform_credit(chain_df, entry_debit=23.01, threshold=5.0, **kwargs)
    assert just_under.is_viable is False


def test_transform_credit_default_threshold_is_five(chain_df):
    tc = ive.transform_credit(chain_df, FRONT, BACK, 6000, 6000, entry_debit=10.0)
    assert tc.threshold == pytest.approx(5.0)


def test_transform_credit_falls_back_to_midpoint_for_a_missing_back_mark(chain_df):
    df = chain_df.copy()
    df.loc[(df["expiry"] == BACK) & (df["side"] == "CALL"), "mark"] = None
    tc = ive.transform_credit(df, FRONT, BACK, 6000, 6000, entry_debit=10.0)
    # Back call falls to midpoint (19+21)/2 = 20.0; back put keeps its 25.0.
    assert tc.back_call_mark == pytest.approx(20.0)
    assert tc.back_legs_value == pytest.approx(45.0)


def test_transform_credit_is_not_viable_when_data_is_missing(chain_df):
    df = chain_df.copy()
    df.loc[(df["expiry"] == FRONT) & (df["side"] == "PUT"), "ask"] = None
    tc = ive.transform_credit(df, FRONT, BACK, 6000, 6000, entry_debit=10.0)
    assert tc.close_cost is None
    assert tc.theoretical_credit is None
    assert tc.gap_to_threshold is None
    assert tc.is_viable is False  # never True on absent data


def test_transform_credit_handles_an_unknown_strike(chain_df):
    tc = ive.transform_credit(chain_df, FRONT, BACK, 9999, 9999, entry_debit=10.0)
    # transform_credit does exact lookups only — no nearest-strike fallback.
    assert tc.back_call_mark is None
    assert tc.is_viable is False


# ─────────────────────────────────────────────────────────────────────────────
# atm_straddle_price / normalized_debit
# ─────────────────────────────────────────────────────────────────────────────

def test_atm_straddle_price_matches_the_closed_form():
    got = ive.atm_straddle_price(spx_price=6000.0, atm_iv_pct=20.0, dte=30)
    expected = 6000.0 * 0.20 * math.sqrt(2.0 * (30 / 365.0) / math.pi)
    assert got == pytest.approx(expected)


def test_atm_straddle_price_treats_iv_as_a_percentage_not_a_decimal():
    """Guards the ×100 load-boundary convention.

    If a caller ever passes 0.20 instead of 20.0, the result must be ~100×
    smaller — this test is what makes that mistake visible rather than silent.
    """
    as_pct = ive.atm_straddle_price(6000.0, 20.0, 30)
    as_decimal = ive.atm_straddle_price(6000.0, 0.20, 30)
    assert as_pct / as_decimal == pytest.approx(100.0)


def test_atm_straddle_price_grows_with_iv_and_with_time():
    base = ive.atm_straddle_price(6000.0, 20.0, 30)
    assert ive.atm_straddle_price(6000.0, 30.0, 30) > base
    assert ive.atm_straddle_price(6000.0, 20.0, 60) > base


@pytest.mark.parametrize(
    "spx, iv, dte",
    [(6000.0, 0.0, 30), (6000.0, -1.0, 30), (6000.0, 20.0, 0),
     (6000.0, 20.0, -5), (0.0, 20.0, 30), (-6000.0, 20.0, 30)],
)
def test_atm_straddle_price_is_none_for_non_positive_inputs(spx, iv, dte):
    assert ive.atm_straddle_price(spx, iv, dte) is None


def test_normalized_debit_is_a_ratio():
    assert ive.normalized_debit(net_debit=10.0, straddle_price=200.0) == pytest.approx(0.05)


def test_normalized_debit_is_none_without_a_usable_straddle():
    assert ive.normalized_debit(10.0, None) is None
    assert ive.normalized_debit(10.0, 0.0) is None
    assert ive.normalized_debit(10.0, -5.0) is None


def test_normalized_debit_composes_with_atm_straddle_price():
    straddle = ive.atm_straddle_price(6000.0, 20.0, 30)
    assert ive.normalized_debit(50.0, straddle) == pytest.approx(50.0 / straddle)


# ─────────────────────────────────────────────────────────────────────────────
# theta_differential
# ─────────────────────────────────────────────────────────────────────────────

def test_theta_differential_sign_convention(chain_df):
    """net = -front_sum + back_sum, and it is positive for a normal diagonal.

    Fixture thetas: front -0.80 each, back -0.30 each.
      front_sum = -1.60, back_sum = -0.60
      net = 1.60 - 0.60 = +1.00 per share  → +100.00 per contract
    Positive means the position earns time decay each day, which is the usual
    case when front DTE < back DTE.
    """
    td = ive.theta_differential(chain_df, FRONT, BACK, 6000, 6000)
    assert td.front_sum == pytest.approx(-1.60)
    assert td.back_sum == pytest.approx(-0.60)
    assert td.net_daily_theta == pytest.approx(1.00)
    assert td.net_daily_theta_ct == pytest.approx(100.00)
    assert td.available is True


def test_theta_differential_goes_negative_when_back_decays_faster(chain_df):
    df = chain_df.copy()
    df.loc[df["expiry"] == BACK, "theta"] = -2.0  # back now decays faster than front
    td = ive.theta_differential(df, FRONT, BACK, 6000, 6000)
    assert td.net_daily_theta == pytest.approx(1.60 - 4.0)
    assert td.net_daily_theta < 0


def test_theta_differential_contract_multiplier_is_exactly_100(chain_df):
    td = ive.theta_differential(chain_df, FRONT, BACK, 6000, 6000)
    assert td.net_daily_theta_ct == pytest.approx(td.net_daily_theta * 100)


def test_theta_differential_unavailable_when_greeks_are_absent(chain_df):
    df = chain_df.copy()
    df["theta"] = None
    td = ive.theta_differential(df, FRONT, BACK, 6000, 6000)
    assert td.available is False
    assert td.net_daily_theta is None
    assert td.net_daily_theta_ct is None


def test_theta_differential_unavailable_when_one_leg_is_missing(chain_df):
    df = chain_df.copy()
    df.loc[(df["expiry"] == FRONT) & (df["side"] == "PUT"), "theta"] = None
    td = ive.theta_differential(df, FRONT, BACK, 6000, 6000)
    assert td.front_sum is None
    assert td.available is False


def test_theta_differential_unavailable_for_an_unknown_strike(chain_df):
    td = ive.theta_differential(chain_df, FRONT, BACK, 9999, 9999)
    assert td.available is False


# ─────────────────────────────────────────────────────────────────────────────
# Module hygiene
# ─────────────────────────────────────────────────────────────────────────────

def test_iv_engine_imports_no_framework_or_io_modules():
    """iv_engine's purity is load-bearing for M2's core/ extraction.

    Its docstring says: do not import streamlit, sqlite3, or schwab here. This
    test makes that a rule the suite enforces rather than a comment people read
    once.
    """
    # Parse the AST rather than grepping the text: the module docstring itself
    # contains the phrase "Do not import streamlit, sqlite3, or schwab here",
    # which a substring search would flag as a violation of the rule it states.
    tree = ast.parse(Path(ive.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    banned = {"streamlit", "sqlite3", "schwab", "schwab_client", "schwabdev",
              "requests", "db", "config", "app"}
    assert not (imported & banned), (
        f"iv_engine must stay pure; found {sorted(imported & banned)}"
    )


def test_removed_functions_stay_removed():
    """M0.11 removed five unreachable helpers (decisions.md ADR-001).

    trade_quality_score in particular was a composite 'magic score' rejected on
    the grounds that it obscures which dimension drives the value and that two
    of its three inputs had no validated basis. Reintroducing any of these
    should be a deliberate act that breaks this test first.
    """
    for name in ("iv_regime", "mean_reversion_estimate", "trade_quality_score",
                 "expected_move_log_check", "ReversionEstimate", "ExpectedMoveCheck"):
        assert not hasattr(ive, name), f"{name} was removed in M0.11 — see decisions.md"
