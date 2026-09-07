"""Vanna and Charm — the arithmetic, the sign conventions, and the scaling.

WHY THE CENTRAL TEST IS A FINITE DIFFERENCE. Everything else on this tab draws
a column the broker sent, so a wrong number would have to be a wrong column.
These two are DERIVED, and a second-order Black-Scholes formula copied from a
reference is wrong in a way nothing else notices: the magnitude stays entirely
plausible and only the sign or a factor is off.

That is not hypothetical. The first implementation of `charm` negated the
textbook form on the reasoning that "time passing means T decreasing", which
sounds right and is wrong — the published form is ALREADY -dDelta/dTau. It
produced numbers of exactly the right size pointing exactly the wrong way, and
it was this test that said so. So the reference here is not another formula,
which could be miscopied the same way; it is a numerical derivative of the
Black-Scholes delta itself. If `vanna` really is dDelta/dSigma and `charm`
really is dDelta/dt, they agree with it, and no algebra slip survives.

The `_bs_delta` below is therefore deliberately NOT imported from iv_engine —
a test that shares its subject's code cannot contradict it.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

import iv_engine
from core import gex

SPOT = 7718.36
R = 0.04
Q = 0.012

# Spot, strike, days, IV%, right — spread across moneyness and tenor, because
# both formulas divide by sqrt(T) and by sigma and the near-expiry corner is
# where a wrong power of T stops being invisible.
CASES = [
    (SPOT, 7700.0, 28, 10.8, "C"),
    (SPOT, 7700.0, 28, 10.8, "P"),
    (SPOT, 7600.0, 5, 9.5, "C"),
    (SPOT, 7900.0, 60, 14.0, "P"),
    (SPOT, SPOT, 3, 12.0, "C"),
    (SPOT, 7500.0, 14, 11.0, "P"),
    (SPOT, 8000.0, 45, 16.5, "C"),
]


def _bs_delta(spot, strike, t_years, sigma, r, q, right):
    """Black-Scholes delta, written out here on purpose. See the docstring."""
    d1 = ((math.log(spot / strike) + (r - q + 0.5 * sigma ** 2) * t_years)
          / (sigma * math.sqrt(t_years)))
    disc = math.exp(-q * t_years)

    def ncdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    return disc * ncdf(d1) if right == "C" else -disc * ncdf(-d1)


# ─────────────────────────────────────────────────────────────────────────────
# The formulas, against a numerical derivative
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spot,strike,days,iv,right", CASES)
def test_gamma_is_the_derivative_of_delta_by_spot(spot, strike, days, iv,
                                                  right):
    """Checked against a numerical derivative of the delta written out above,
    for the same reason vanna and charm are: an analytic formula agreeing with
    itself proves nothing. Added 2026-09-07 with `zero_gamma_spot`, which is
    the only caller -- the per-strike panels use the broker's gamma, so an
    error here would surface in one number and nowhere else."""
    t = iv_engine.year_fraction(days) if hasattr(iv_engine, "year_fraction") \
        else gex.year_fraction(days)
    sigma = iv / 100.0
    h = 0.01
    numeric = ((_bs_delta(spot + h, strike, t, sigma, R, Q, right)
                - _bs_delta(spot - h, strike, t, sigma, R, Q, right))
               / (2 * h))
    assert iv_engine.gamma(spot, strike, t, iv, R, Q) == pytest.approx(
        numeric, rel=1e-4)


def test_the_one_gamma_is_the_put_gamma_as_well_as_the_call_gamma():
    """Put-call parity fixes their delta difference at e^(-qT), which does not
    depend on spot, so differentiating by spot annihilates it. This is why
    `gamma` takes no `right` -- and why core.gex has to impose the dealer sign
    itself rather than reading one off the number.

    Checked against the numerical PUT derivative specifically. Comparing the
    function to itself would pass on any formula at all."""
    t, sigma, h = 21 / 365.0, 0.12, 0.01
    numeric_put = ((_bs_delta(SPOT + h, 7800.0, t, sigma, R, Q, "P")
                    - _bs_delta(SPOT - h, 7800.0, t, sigma, R, Q, "P"))
                   / (2 * h))
    assert iv_engine.gamma(SPOT, 7800.0, t, 12.0, R, Q) == pytest.approx(
        numeric_put, rel=1e-4)


def test_the_fast_path_and_the_single_contract_agree_exactly():
    """`gamma_many` exists ONLY to be faster (core.gex prices the whole book
    fifty-odd times per request). The moment it is a second opinion rather
    than a second shape, the screen shows one number and the definition says
    another, and nothing errors. Both call `_gamma_raw`, and this is what
    holds them to it.

    Checked across every case in CASES plus rows that cannot be priced, since
    the two disagree in KIND there by design: None for a scalar, nan for an
    array entry that has to exist."""
    t = [days / 365.0 for _, _, days, _, _ in CASES]
    strikes = [strike for _, strike, _, _, _ in CASES]
    ivs = [iv for _, _, _, iv, _ in CASES]

    many = iv_engine.gamma_many(SPOT, strikes, t, ivs, R, Q)
    for i, (k, years, iv) in enumerate(zip(strikes, t, ivs, strict=True)):
        one = iv_engine.gamma(SPOT, k, years, iv, R, Q)
        assert one is not None
        assert many[i] == pytest.approx(one, rel=1e-12)

    # An expired contract: blank in both shapes, spelled differently.
    dead = iv_engine.gamma_many(SPOT, [7700.0], [0.0], [12.0], R, Q)
    assert math.isnan(dead[0])
    assert iv_engine.gamma(SPOT, 7700.0, 0.0, 12.0, R, Q) is None


def test_an_unpriceable_contract_is_left_out_of_the_book_not_counted_as_zero():
    """`core.gex._net_gamma` sums with nansum. A nan treated as 0 would say a
    contract has no gamma when what is true is that we cannot say -- the
    blank-not-zero rule in array form. The totals differ, so this is a real
    check rather than a restatement."""
    priced = iv_engine.gamma_many(SPOT, [7700.0, 7750.0], [0.02, 0.02],
                                  [12.0, 12.0], R, Q)
    partial = iv_engine.gamma_many(SPOT, [7700.0, 7750.0], [0.02, 0.0],
                                   [12.0, 12.0], R, Q)
    import numpy as np
    assert np.nansum(partial) < np.nansum(priced)
    assert np.nansum(partial) == pytest.approx(priced[0])


def test_gamma_is_positive_everywhere_and_concentrated_at_the_money():
    """Every long option gains delta as spot rises, and the gain is
    concentrated at the strike. This is what limits the damage a truncated
    chain does to the zero-gamma level -- but only limits it: measured here,
    the 300-point wing still carries about 5% of an at-the-money contract's
    gamma at a week to expiry, so the bound pinned is 10%, not "nothing".
    The claim that survives is that a missing wing enters the total ONCE, at
    its own small weight, instead of shifting a running total that every
    strike above it inherits."""
    t = 7 / 365.0
    at_money = iv_engine.gamma(SPOT, SPOT, t, 12.0, R, Q)
    wing = iv_engine.gamma(SPOT, SPOT - 300, t, 12.0, R, Q)
    assert at_money > 0 and wing > 0
    assert wing < 0.10 * at_money


@pytest.mark.parametrize("kwargs", [
    dict(spot=0.0, strike=7700.0, t_years=0.1, iv_pct=12.0),
    dict(spot=SPOT, strike=0.0, t_years=0.1, iv_pct=12.0),
    dict(spot=SPOT, strike=7700.0, t_years=0.0, iv_pct=12.0),
    dict(spot=SPOT, strike=7700.0, t_years=0.1, iv_pct=0.0),
    dict(spot=SPOT, strike=7700.0, t_years=0.1, iv_pct=None),
])
def test_gamma_is_blank_rather_than_zero_where_it_is_undefined(kwargs):
    """An expired contract does not have a gamma of zero; it does not have
    one. A zero here would be summed into the book's total as a real
    measurement of nothing."""
    assert iv_engine.gamma(r=R, q=Q, **kwargs) is None


@pytest.mark.parametrize("spot,strike,days,iv,right", CASES)
def test_vanna_is_the_derivative_of_delta_by_volatility(spot, strike, days,
                                                        iv, right):
    """dDelta/dSigma, per ONE VOL POINT — the unit `vega` is stored in."""
    t = days / 365.0
    sigma, h = iv / 100.0, 1e-7
    expected = (_bs_delta(spot, strike, t, sigma + h, R, Q, right)
                - _bs_delta(spot, strike, t, sigma - h, R, Q, right)) / (2 * h)
    # /100 is the per-vol-point conversion. If it were dropped the value would
    # still look like a greek, a hundred times too large.
    assert iv_engine.vanna(spot, strike, t, iv, R, Q) == pytest.approx(
        expected / 100.0, rel=1e-6)


@pytest.mark.parametrize("spot,strike,days,iv,right", CASES)
def test_charm_is_the_derivative_of_delta_by_calendar_time(spot, strike, days,
                                                           iv, right):
    """dDelta/dt with time moving FORWARD, per DAY — theta's unit and sense.

    The leading minus on the difference quotient is the whole convention: this
    asks how delta changes as the clock advances, which is the opposite sign
    from how it changes with time-to-expiry. A `charm` that returned the other
    one would fail here and nowhere else.
    """
    t = days / 365.0
    sigma, h = iv / 100.0, 1e-7
    d_by_tau = (_bs_delta(spot, strike, t + h, sigma, R, Q, right)
                - _bs_delta(spot, strike, t - h, sigma, R, Q, right)) / (2 * h)
    assert iv_engine.charm(spot, strike, t, iv, R, Q, right) == pytest.approx(
        -d_by_tau / 365.0, rel=1e-5)


def test_charm_says_an_itm_call_gains_delta_and_an_otm_call_loses_it():
    """The plain-language claim the caption makes, pinned as arithmetic.

    In the money, delta walks toward 1.00 as expiry nears; out of the money it
    walks toward zero. Reversing the sign convention would flip both, and both
    readings are stated on screen.
    """
    t = 10 / 365.0
    itm = iv_engine.charm(SPOT, SPOT - 200, t, 11.0, R, Q, "C")
    otm = iv_engine.charm(SPOT, SPOT + 200, t, 11.0, R, Q, "C")
    assert itm > 0
    assert otm < 0


def test_a_call_and_a_put_have_identical_vanna():
    """Not an approximation — the reason vanna takes no `right` argument.

    Put-call parity fixes Delta_call - Delta_put at e^(-qT), which has no sigma
    in it, so differentiating by sigma annihilates it. This is also the
    justification for vanna_by_strike imposing the dealer sign convention, so
    it is worth holding rather than assuming.
    """
    t = 21 / 365.0
    # vanna() is not given a right at all, so the claim is tested against the
    # numerical derivative on each side instead.
    sigma, h = 0.11, 1e-7
    per_side = [
        (_bs_delta(SPOT, 7750.0, t, sigma + h, R, Q, side)
         - _bs_delta(SPOT, 7750.0, t, sigma - h, R, Q, side)) / (2 * h)
        for side in ("C", "P")
    ]
    assert per_side[0] == pytest.approx(per_side[1], rel=1e-8)
    assert iv_engine.vanna(SPOT, 7750.0, t, 11.0, R, Q) == pytest.approx(
        per_side[0] / 100.0, rel=1e-6)


def test_a_call_and_a_put_differ_in_charm_by_the_dividend_term():
    """Charm DOES depend on the side, by exactly q*e^(-qT) per year.

    The counterpart to the test above: parity's e^(-qT) does depend on time,
    so the time derivative does not annihilate it. Small, but real — and it is
    why `charm` takes a `right` and `vanna` does not.
    """
    t = 21 / 365.0
    call = iv_engine.charm(SPOT, 7750.0, t, 11.0, R, Q, "C")
    put = iv_engine.charm(SPOT, 7750.0, t, 11.0, R, Q, "P")
    expected = Q * math.exp(-Q * t) / 365.0
    assert (call - put) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("kwargs", [
    dict(spot=0.0, strike=7700.0, t_years=0.1, iv_pct=11.0),
    dict(spot=SPOT, strike=0.0, t_years=0.1, iv_pct=11.0),
    dict(spot=SPOT, strike=7700.0, t_years=0.0, iv_pct=11.0),
    dict(spot=SPOT, strike=7700.0, t_years=-0.1, iv_pct=11.0),
    dict(spot=SPOT, strike=7700.0, t_years=0.1, iv_pct=0.0),
    dict(spot=SPOT, strike=7700.0, t_years=0.1, iv_pct=None),
])
def test_undefined_inputs_give_none_and_never_a_number(kwargs):
    """Blank, not zero — an expired contract has no vanna, not a vanna of 0.

    A zero here would flow straight into a bar chart and draw "no exposure at
    this strike", which is a different and false statement.
    """
    assert iv_engine.vanna(r=R, q=Q, **kwargs) is None
    assert iv_engine.charm(r=R, q=Q, right="C", **kwargs) is None


def test_charm_rejects_a_right_it_does_not_understand():
    assert iv_engine.charm(SPOT, 7700.0, 0.1, 11.0, R, Q, "CALL") is None


# ─────────────────────────────────────────────────────────────────────────────
# year_fraction
# ─────────────────────────────────────────────────────────────────────────────

def test_year_fraction_adds_the_rest_of_the_day_to_whole_days():
    assert gex.year_fraction(28, 0.25) == pytest.approx(28.25 / 365.0)
    assert gex.year_fraction(28) == pytest.approx(28.0 / 365.0)


def test_year_fraction_is_none_once_there_is_no_time_left():
    """The 0DTE-after-the-close case, which is most of what the tab holds.

    None rather than a tiny number: charm's 1/(T sqrt(T)) would turn any floor
    put here into the largest bar on the chart, invented entirely by the floor.
    """
    assert gex.year_fraction(0, 0.0) is None
    assert gex.year_fraction(-1, 0.0) is None
    assert gex.year_fraction(None) is None
    assert gex.year_fraction(0, 0.25) == pytest.approx(0.25 / 365.0)


# ─────────────────────────────────────────────────────────────────────────────
# The per-strike exposure frames
# ─────────────────────────────────────────────────────────────────────────────

def _chain(**over) -> pd.DataFrame:
    """One strike, both sides, with DIFFERENT open interest on each.

    The asymmetry is load-bearing: with equal OI the dealer-sign convention
    nets to zero and a frame that applied it and one that did not would be
    indistinguishable — which is precisely the confusion these tests exist to
    catch.
    """
    base = dict(strike=[7700.0, 7700.0], right=["C", "P"], iv=[11.0, 11.0],
                dte=[28, 28], open_interest=[10.0, 20.0], volume=[0.0, 0.0],
                expiry=["2026-10-02", "2026-10-02"])
    base.update(over)
    return pd.DataFrame(base)


def test_vanna_exposure_imposes_the_dealer_sign_convention():
    """net_vex is calls MINUS puts, the way net_gex is — see vanna_by_strike.

    A call and a put have the same vanna, so the number carries no side and
    the convention has to supply one.
    """
    out = gex.vanna_by_strike(_chain(), SPOT, r=R, q=Q)
    row = out.iloc[0]
    assert row["net_vex"] == pytest.approx(row["call_vex"] - row["put_vex"])
    # And not the other thing, which is what a copy-paste from charm would do.
    assert row["net_vex"] != pytest.approx(row["call_vex"] + row["put_vex"])


def test_charm_exposure_does_not_impose_the_dealer_sign_convention():
    """net_cex is calls PLUS puts — charm is a delta figure, already signed.

    The mirror of the test above, and the pair is the point: these two
    functions sit next to each other and differ in exactly this, so pinning
    only one would leave the other free to be "made consistent" later.
    """
    out = gex.charm_by_strike(_chain(), SPOT, r=R, q=Q)
    row = out.iloc[0]
    assert row["net_cex"] == pytest.approx(row["call_cex"] + row["put_cex"])


def test_exposure_scales_linearly_in_spot_not_quadratically():
    """vanna x OI x 100 x spot. GEX is quadratic in spot; these are not.

    Reaching for the existing `dollar_scale` would have been the natural move
    and is wrong by a factor of spot/100 — a plausible-looking number two
    orders of magnitude out. Pinned against the arithmetic spelled out.
    """
    out = gex.vanna_by_strike(_chain(), SPOT, r=R, q=Q)
    per_contract = iv_engine.vanna(SPOT, 7700.0, gex.year_fraction(28),
                                   11.0, R, Q)
    assert out.iloc[0]["call_vex"] == pytest.approx(
        per_contract * 10.0 * gex.SHARES_PER_CONTRACT * SPOT)


def test_the_two_frames_are_empty_with_columns_not_empty_without():
    """The shape contract `by_strike` already keeps, kept here too.

    A caller that has to branch on "did I get columns" writes the empty case
    twice.
    """
    for frame, columns in ((gex.vanna_by_strike(pd.DataFrame(), SPOT, r=R, q=Q),
                            gex.VANNA_COLUMNS),
                           (gex.charm_by_strike(pd.DataFrame(), SPOT, r=R, q=Q),
                            gex.CHARM_COLUMNS)):
        assert frame.empty
        assert list(frame.columns) == columns


def test_a_chain_without_iv_computes_nothing_rather_than_guessing():
    """IV is the one Black-Scholes input with no substitute in this record."""
    no_iv = _chain(iv=[None, None])
    assert gex.vanna_by_strike(no_iv, SPOT, r=R, q=Q).empty
    assert gex.charm_by_strike(no_iv, SPOT, r=R, q=Q).empty


def test_expiry_selection_filters_the_frame():
    chain = pd.concat([_chain(), _chain(strike=[7750.0, 7750.0],
                                        expiry=["2026-10-09"] * 2)])
    out = gex.vanna_by_strike(chain, SPOT, r=R, q=Q, expiry="2026-10-09")
    assert list(out["strike"]) == [7750.0]


def test_summary_reports_blank_rather_than_zero_when_there_is_nothing():
    """Both halves absent is a different state from both being balanced."""
    empty = gex.second_order_summary(None, None)
    assert set(empty.values()) == {None}


def test_summary_finds_the_strike_carrying_the_most_exposure():
    chain = pd.concat([
        _chain(),
        _chain(strike=[7750.0, 7750.0], open_interest=[9000.0, 9000.0]),
    ])
    vex = gex.vanna_by_strike(chain, SPOT, r=R, q=Q)
    totals = gex.second_order_summary(vex, None)
    assert totals["peak_vex_strike"] == 7750.0
    assert totals["net_cex"] is None      # not asked for, so not invented
