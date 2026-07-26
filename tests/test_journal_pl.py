"""
Unit tests for the profit-and-loss maths in pages/journal.py.

WHY THIS MODULE (M1.3): these four functions decide what every trade "made".
resolved_pl() feeds the Master Log, the statistics panel, Net P&L, and the
win/loss split — and STATUS.md commits to judging the whole strategy against
those numbers at M6. An error here does not crash; it quietly reports the wrong
profit, and could make a losing strategy look like a winning one.

The functions are loaded out of journal.py by tests/journal_loader.py rather
than imported, because importing that file runs a Streamlit page against the
production database. See that module for the reasoning and its limits.

As in test_iv_engine.py, behaviour that looks wrong is PINNED rather than
silently fixed, and cross-referenced to a backlog ID.
"""
from __future__ import annotations

import json

import pytest
from conftest import FakeRow, make_legacy_trade, make_trade

# ─────────────────────────────────────────────────────────────────────────────
# ic_expiry_pnl_per_share — the iron condor payoff at expiry
#
# Structure:  lp (long put) < sp (short put) < sc (short call) < lc (long call)
# Fixture:    5900        <  5950         <  6050          <  6100
#
# The result is ALWAYS 0 or negative: the credit was already banked at
# transformation time, so this figure is only the assignment liability.
# ─────────────────────────────────────────────────────────────────────────────

LP, SP, SC, LC = 5900.0, 5950.0, 6050.0, 6100.0


@pytest.mark.parametrize(
    "spx, expected, region",
    [
        (5000.0, -50.0, "far below the long put — capped at the put wing width"),
        (5900.0, -50.0, "exactly at the long put — still the max put loss"),
        (5925.0, -25.0, "between the puts — partial loss, linear"),
        (5949.99, -0.01, "a hair below the short put — almost no loss"),
        (5950.0, 0.0, "exactly at the short put — the safe zone starts here"),
        (6000.0, 0.0, "mid-zone — both shorts expire worthless"),
        (6050.0, 0.0, "exactly at the short call — safe zone ends here, inclusive"),
        (6050.01, -0.01, "a hair above the short call"),
        (6075.0, -25.0, "between the calls — partial loss, linear"),
        (6100.0, -50.0, "exactly at the long call — max call loss"),
        (7000.0, -50.0, "far above — capped at the call wing width"),
    ],
)
def test_ic_expiry_payoff_by_region(journal, spx, expected, region):
    got = journal["ic_expiry_pnl_per_share"](spx, LP, SP, SC, LC)
    assert got == pytest.approx(expected), region


def test_ic_expiry_payoff_is_never_positive(journal):
    """The credit is banked at transformation; expiry can only take money back."""
    for spx in range(5000, 7001, 5):
        assert journal["ic_expiry_pnl_per_share"](float(spx), LP, SP, SC, LC) <= 0.0


def test_ic_expiry_payoff_is_continuous_at_every_boundary(journal):
    """No jumps at the four strikes.

    A discontinuity here would mean a one-cent move in SPX changed the payoff by
    a whole point — the classic symptom of a wrong comparison operator. Walking
    a cent either side of each strike catches that where single-point tests do
    not.
    """
    f = journal["ic_expiry_pnl_per_share"]
    for strike in (LP, SP, SC, LC):
        below = f(strike - 0.01, LP, SP, SC, LC)
        at = f(strike, LP, SP, SC, LC)
        above = f(strike + 0.01, LP, SP, SC, LC)
        assert abs(at - below) < 0.02, f"jump just below {strike}"
        assert abs(above - at) < 0.02, f"jump just above {strike}"


def test_ic_expiry_payoff_worst_case_equals_the_wider_wing(journal):
    """With asymmetric wings, each side is capped at its OWN width."""
    f = journal["ic_expiry_pnl_per_share"]
    # put wing 100 wide, call wing 25 wide
    assert f(5000.0, 5850.0, 5950.0, 6050.0, 6075.0) == pytest.approx(-100.0)
    assert f(7000.0, 5850.0, 5950.0, 6050.0, 6075.0) == pytest.approx(-25.0)


# ─────────────────────────────────────────────────────────────────────────────
# resolved_pl — the single source of truth
# ─────────────────────────────────────────────────────────────────────────────

def test_resolved_pl_expired_uses_the_formula(journal):
    # (6.00 locked + 0.00 assignment) x 100 x 2 contracts
    assert journal["resolved_pl"](make_trade()) == pytest.approx(1200.0)


def test_resolved_pl_subtracts_assignment_when_spx_lands_in_a_wing(journal):
    # spx 5925 → -25.00/share assignment. (6.00 - 25.00) x 100 x 2 = -3,800
    t = make_trade(spx_at_expiry=5925.0)
    assert journal["resolved_pl"](t) == pytest.approx(-3800.0)


def test_resolved_pl_formula_overrides_a_stored_final_pl(journal):
    """The docstring says the formula 'supersedes any stored final_pl'.

    This matters: a stale or hand-edited final_pl must never win over the
    derived figure for a trade with complete IC data.
    """
    t = make_trade(final_pl=999999.0)
    assert journal["resolved_pl"](t) == pytest.approx(1200.0)


def test_resolved_pl_scales_linearly_with_contracts(journal):
    one = journal["resolved_pl"](make_trade(contracts=1))
    ten = journal["resolved_pl"](make_trade(contracts=10))
    assert ten == pytest.approx(one * 10)


def test_resolved_pl_falls_back_to_final_pl_without_ic_data(journal):
    """A direct close (no IC) has no strikes to work from — use the stored value."""
    t = make_trade(profit_locked_in=None, final_pl=432.10, close_type="direct")
    assert journal["resolved_pl"](t) == pytest.approx(432.10)


def test_resolved_pl_falls_back_when_spx_at_expiry_is_missing(journal):
    t = make_trade(spx_at_expiry=None, final_pl=100.0)
    assert journal["resolved_pl"](t) == pytest.approx(100.0)


def test_resolved_pl_on_a_legacy_row_without_ic_columns(journal):
    """Legacy rows lack ic_* entirely; row_get() must absorb that, not raise."""
    assert journal["resolved_pl"](make_legacy_trade()) == pytest.approx(250.0)


def test_resolved_pl_transformed_but_not_yet_expired_returns_locked_only(journal):
    """The IC is still running, so only the banked component is realized."""
    t = make_trade(status="Transformed", spx_at_expiry=None)
    assert journal["resolved_pl"](t) == pytest.approx(6.00 * 100 * 2)


def test_resolved_pl_is_none_for_an_open_trade(journal):
    assert journal["resolved_pl"](make_trade(status="Open")) is None


def test_resolved_pl_is_none_when_nothing_is_known(journal):
    t = make_trade(status="Closed", profit_locked_in=None, final_pl=None)
    assert journal["resolved_pl"](t) is None


def test_resolved_pl_is_none_for_transformed_without_a_locked_figure(journal):
    assert journal["resolved_pl"](make_trade(status="Transformed",
                                             profit_locked_in=None)) is None


def test_resolved_pl_matches_auto_final_pl_for_a_transformed_trade(journal):
    """Two functions compute the same lifecycle figure by different routes.

    auto_final_pl() is the Expiration tab's path; resolved_pl() is everywhere
    else. They must agree, or the same trade shows two different profits on two
    different screens.
    """
    for spx in (5800.0, 5925.0, 6000.0, 6075.0, 6200.0):
        t = make_trade(status="Transformed", spx_at_expiry=spx)
        expected = journal["auto_final_pl"](t, spx)
        derived = journal["resolved_pl"](make_trade(status="Expired", spx_at_expiry=spx))
        assert derived == pytest.approx(expected), f"divergence at SPX {spx}"


def test_resolved_pl_treats_zero_contracts_as_zero_not_a_crash(journal):
    assert journal["resolved_pl"](make_trade(contracts=0)) == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# derive_ic — building the condor description from the leg lists
# ─────────────────────────────────────────────────────────────────────────────

def _legs(*specs):
    return json.dumps([
        {"action": a, "type": ty, "strike": k, "expiry": e, "fill": 1.0}
        for a, ty, k, e in specs
    ])


INITIAL = _legs(
    ("Sell to Open", "Call", 6050.0, "2026-08-07"),
    ("Sell to Open", "Put", 5950.0, "2026-08-07"),
    ("Buy to Open", "Call", 6050.0, "2026-08-21"),
    ("Buy to Open", "Put", 5950.0, "2026-08-21"),
)
TF_LEGS = json.loads(_legs(
    ("Buy to Open", "Call", 6100.0, "2026-08-21"),
    ("Buy to Open", "Put", 5900.0, "2026-08-21"),
))


def test_derive_ic_builds_the_condor(journal):
    ic = journal["derive_ic"](INITIAL, TF_LEGS, credit=10.0, total_debit=4.0, contracts=2)
    assert ic["ic_short_call"] == 6050.0
    assert ic["ic_long_call"] == 6100.0
    assert ic["ic_short_put"] == 5950.0
    assert ic["ic_long_put"] == 5900.0
    assert ic["ic_call_wing"] == pytest.approx(50.0)
    assert ic["ic_put_wing"] == pytest.approx(50.0)
    assert ic["ic_expiry_date"] == "2026-08-21"


def test_derive_ic_max_profit_is_the_locked_credit(journal):
    # locked = credit - debit = 10 - 4 = 6 points → 6 x 100 x 2 = $1,200
    ic = journal["derive_ic"](INITIAL, TF_LEGS, credit=10.0, total_debit=4.0, contracts=2)
    assert ic["ic_max_profit"] == pytest.approx(1200.0)


def test_derive_ic_flags_a_risk_free_condor(journal):
    """Risk-free = locked profit exceeds the worst the condor can cost.

    Wings are 50 wide → max IC loss 50 x 100 x 2 = $10,000. A locked credit
    above that cannot be given back, whatever SPX does.
    """
    rf = journal["derive_ic"](INITIAL, TF_LEGS, credit=104.0, total_debit=4.0, contracts=2)
    assert rf["ic_risk_free"] == 1
    assert rf["ic_worst_case"] == pytest.approx(20000.0 - 10000.0)

    not_rf = journal["derive_ic"](INITIAL, TF_LEGS, credit=10.0, total_debit=4.0, contracts=2)
    assert not_rf["ic_risk_free"] == 0
    assert not_rf["ic_worst_case"] == pytest.approx(10000.0 - 1200.0)


def test_derive_ic_handles_asymmetric_wings(journal):
    tf = json.loads(_legs(
        ("Buy to Open", "Call", 6150.0, "2026-08-21"),
        ("Buy to Open", "Put", 5900.0, "2026-08-21"),
    ))
    ic = journal["derive_ic"](INITIAL, tf, credit=10.0, total_debit=4.0, contracts=1)
    assert ic["ic_call_wing"] == pytest.approx(100.0)
    assert ic["ic_put_wing"] == pytest.approx(50.0)
    # Worst case uses the WIDER wing — the condor's true maximum exposure.
    assert ic["ic_worst_case"] == pytest.approx(100.0 * 100 * 1 - 600.0)


def test_derive_ic_returns_none_when_a_leg_is_missing(journal):
    no_call = json.loads(_legs(("Buy to Open", "Put", 5900.0, "2026-08-21")))
    assert journal["derive_ic"](INITIAL, no_call, 10.0, 4.0, 1) is None


def test_derive_ic_returns_none_on_malformed_json(journal):
    assert journal["derive_ic"]("not json at all", TF_LEGS, 10.0, 4.0, 1) is None


def test_derive_ic_max_profit_is_rounded_to_whole_dollars(journal):
    """FINDING (M1.3, 2026-07-26): ic_max_profit is round()ed, the rest is not.

    locked = 10.005 - 4.0, which in binary floating point is 6.005000000000001,
    not 6.005. x100 gives 600.5000000000001 — a hair ABOVE the midpoint — so
    round() goes up to 601, where exact arithmetic would have hit round-half-to
    -even and given 600. The direction of the rounding is decided by float
    representation error, not by the trade.

    Half a dollar per contract is immaterial on its own. It matters because
    ic_max_profit then feeds ic_worst_case and the ic_risk_free comparison, so a
    representation artefact propagates into a displayed dollar figure and a
    boolean flag.

    Pinned as current behaviour; see backlog BUG-009.
    """
    ic = journal["derive_ic"](INITIAL, TF_LEGS, credit=10.005, total_debit=4.0, contracts=1)
    assert ic["ic_max_profit"] == pytest.approx(601.0)  # rounded, and rounded UP
    # The unrounded figure would be 600.50; nothing else in the dict is rounded.
    assert ic["ic_call_wing"] == pytest.approx(50.0)


# ─────────────────────────────────────────────────────────────────────────────
# compute_stats — the statistics panel
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_stats_on_no_trades_is_empty(journal):
    assert journal["compute_stats"]([]) == {}


def test_compute_stats_basic_win_loss_split(journal):
    rows = [
        make_trade(trade_id=1, spx_at_expiry=6000.0),   # +1200 win
        make_trade(trade_id=2, spx_at_expiry=5925.0),   # -3800 loss
    ]
    s = journal["compute_stats"](rows)
    assert s["Total Trades"] == 2
    assert s["Win Rate"] == pytest.approx(50.0)
    assert s["Average Winner"] == pytest.approx(1200.0)
    assert s["Average Loser"] == pytest.approx(-3800.0)
    assert s["Largest Winner"] == pytest.approx(1200.0)
    assert s["Largest Loser"] == pytest.approx(-3800.0)


def test_compute_stats_profit_factor_and_expectancy(journal):
    rows = [
        make_trade(trade_id=1, spx_at_expiry=6000.0),   # +1200
        make_trade(trade_id=2, spx_at_expiry=5925.0),   # -3800
    ]
    s = journal["compute_stats"](rows)
    assert s["Profit Factor"] == pytest.approx(1200.0 / 3800.0)
    # 0.5 x 1200 + 0.5 x -3800
    assert s["Expectancy"] == pytest.approx(0.5 * 1200.0 + 0.5 * -3800.0)


def test_compute_stats_totals_fees_across_the_lifecycle(journal):
    rows = [make_trade(trade_id=1), make_trade(trade_id=2)]
    s = journal["compute_stats"](rows)
    # (2.60 entry + 2.60 transform) x 2 trades
    assert s["Total Fees"] == pytest.approx(10.40)
    assert s["Total Net P&L"] == pytest.approx(2400.0 - 10.40)


def test_compute_stats_excludes_open_trades_from_performance(journal):
    """An Open trade counts in Total Trades but must not skew the win rate."""
    rows = [
        make_trade(trade_id=1, spx_at_expiry=6000.0),          # +1200, completed
        make_trade(trade_id=2, status="Open", final_pl=None),  # no result yet
    ]
    s = journal["compute_stats"](rows)
    assert s["Total Trades"] == 2
    assert s["Win Rate"] == pytest.approx(100.0)  # 1 of 1 completed


def test_compute_stats_all_winners_gives_no_profit_factor(journal):
    """Pinned: with no losses the divisor is 0, so Profit Factor is None.

    Reporting None (rendered as blank) rather than infinity is defensible, but
    it means a flawless record and a missing calculation look identical on
    screen. Noted rather than changed — see backlog BUG-010.
    """
    rows = [make_trade(trade_id=1), make_trade(trade_id=2)]
    s = journal["compute_stats"](rows)
    assert s["Win Rate"] == pytest.approx(100.0)
    assert s["Profit Factor"] is None
    assert s["Average Loser"] is None


def test_compute_stats_excludes_a_breakeven_trade_from_the_loss_average(journal):
    """BUG-011 fixed 2026-07-26: a scratch trade is no longer counted as a loss.

    A P&L of exactly 0 is neither a win nor a loss. It used to enter the loss
    list as a zero and pull Average Loser toward zero, understating the typical
    loss. It still counts in the win-rate denominator — it is a completed trade
    that was not won.

    Reachable in practice: a Transformed trade whose locked credit exactly
    offsets its assignment cost.
    """
    scratch = make_trade(trade_id=1, status="Closed", profit_locked_in=None, final_pl=0.0)
    winner = make_trade(trade_id=2, spx_at_expiry=6000.0)      # +1200
    loser = make_trade(trade_id=3, spx_at_expiry=5925.0)       # -3800
    s = journal["compute_stats"]([scratch, winner, loser])

    # 1 win out of 3 completed trades — the scratch stays in the denominator.
    assert s["Win Rate"] == pytest.approx(100.0 / 3.0)
    # The real loss only. Previously (0 + -3800)/2 = -1900, which understated it.
    assert s["Average Loser"] == pytest.approx(-3800.0)
    assert s["Largest Loser"] == pytest.approx(-3800.0)


def test_compute_stats_expectancy_is_the_mean_outcome(journal):
    """Expectancy must stay the per-trade mean once scratches exist (BUG-011).

    The old weighted form swept scratches in at the average LOSS via the
    (1 - win_rate) term, overstating losses.
    """
    rows = [
        make_trade(trade_id=1, status="Closed", profit_locked_in=None, final_pl=0.0),
        make_trade(trade_id=2, spx_at_expiry=6000.0),   # +1200
        make_trade(trade_id=3, spx_at_expiry=5925.0),   # -3800
    ]
    s = journal["compute_stats"](rows)
    assert s["Expectancy"] == pytest.approx((0.0 + 1200.0 - 3800.0) / 3.0)


def test_compute_stats_expectancy_unchanged_when_there_are_no_scratches(journal):
    """The expectancy rewrite must be a no-op for data without scratches.

    win_rate x avg_win + (1 - win_rate) x avg_loss is algebraically identical to
    the plain mean when every trade is strictly a win or a loss. Asserting the
    old formula explicitly proves the fix moved no existing number.
    """
    rows = [
        make_trade(trade_id=1, spx_at_expiry=6000.0),   # +1200
        make_trade(trade_id=2, spx_at_expiry=5925.0),   # -3800
        make_trade(trade_id=3, spx_at_expiry=6000.0),   # +1200
    ]
    s = journal["compute_stats"](rows)
    wr, aw, al = s["Win Rate"] / 100, s["Average Winner"], s["Average Loser"]
    assert s["Expectancy"] == pytest.approx(wr * aw + (1 - wr) * al)


def test_compute_stats_averages_holding_days_and_transform_time(journal):
    rows = [
        make_trade(trade_id=1, entry_date="2026-07-01", result_date="2026-07-11",
                   transform_minutes=100),
        make_trade(trade_id=2, entry_date="2026-07-01", result_date="2026-07-21",
                   transform_minutes=200),
    ]
    s = journal["compute_stats"](rows)
    assert s["Avg Holding (days)"] == pytest.approx(15.0)
    assert s["Avg Time to Transform"] == pytest.approx(150.0)


def test_compute_stats_averages_entry_debit_and_close_credit(journal):
    rows = [
        make_trade(trade_id=1, total_debit=4.0, credit_received=10.0),
        make_trade(trade_id=2, total_debit=6.0, credit_received=None),
    ]
    s = journal["compute_stats"](rows)
    assert s["Avg Entry Debit"] == pytest.approx(5.0)
    # credit_received=None is skipped, not counted as zero.
    assert s["Avg Close Credit"] == pytest.approx(10.0)


def test_compute_stats_survives_a_null_total_debit(journal):
    """BUG-012 fixed 2026-07-26: a NULL total_debit no longer kills the panel.

    Previously one legacy or hand-edited row raised TypeError out of
    compute_stats, so the ENTIRE statistics panel failed to render rather than
    just that one average. The NULL row is now skipped for the debit average,
    exactly as credit_received already did, and every other statistic still
    computes.
    """
    rows = [make_trade(trade_id=1, total_debit=4.0),
            make_trade(trade_id=2, total_debit=None)]
    s = journal["compute_stats"](rows)

    assert s["Avg Entry Debit"] == pytest.approx(4.0)  # the NULL is skipped, not zero
    assert s["Total Trades"] == 2                      # the row still counts as a trade
    assert s["Win Rate"] == pytest.approx(100.0)       # unrelated stats unaffected


def test_compute_stats_avg_entry_debit_is_none_when_every_debit_is_null(journal):
    rows = [make_trade(trade_id=1, total_debit=None),
            make_trade(trade_id=2, total_debit=None)]
    s = journal["compute_stats"](rows)
    assert s["Avg Entry Debit"] is None
    assert s["Total Trades"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Supporting helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_row_get_absorbs_missing_columns(journal):
    row = FakeRow({"a": 1, "b": None})
    assert journal["row_get"](row, "a") == 1
    assert journal["row_get"](row, "b") is None
    assert journal["row_get"](row, "nonexistent") is None
    assert journal["row_get"](row, "nonexistent", "fallback") == "fallback"


def test_total_fees_sums_entry_and_transform_commissions(journal):
    assert journal["total_fees"](make_trade()) == pytest.approx(5.20)


def test_total_fees_on_a_legacy_row_without_transform_commissions(journal):
    assert journal["total_fees"](make_legacy_trade()) == pytest.approx(1.30)


def test_total_fees_treats_null_commissions_as_zero(journal):
    assert journal["total_fees"](make_trade(commissions=None,
                                            transform_commissions=None)) == pytest.approx(0.0)


def test_holding_days_counts_calendar_days(journal):
    assert journal["holding_days"](make_trade(entry_date="2026-07-01",
                                              result_date="2026-07-15")) == 14


def test_holding_days_is_none_on_a_bad_date(journal):
    assert journal["holding_days"](make_trade(entry_date="not-a-date")) is None
