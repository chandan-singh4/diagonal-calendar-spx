"""
iv_engine.py — The analytics core. Everything here is pure functions: given data,
return numbers. No API calls, no UI, no database writes. This separation matters
because it means you can unit-test the math (and you should — options math is
exactly the kind of thing where a sign error silently costs you real money) without
needing a live Schwab connection or a running dashboard.
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

import config


# ---------------------------------------------------------------------------
# ATM IV extraction
# ---------------------------------------------------------------------------

def atm_iv(chain_df: pd.DataFrame, expiry: str, underlying_price: float) -> float:
    """
    Returns the ATM implied volatility for a given expiry, averaged across the
    nearest call and put strike to spot.

    Why average call & put: at-the-money, put-call parity means their IVs should
    be very close; averaging reduces noise from one side having a stale quote.
    Why nearest-strike-to-spot rather than interpolating: SPX strikes are tight
    enough near spot (often 5-25 points) that the nearest strike is a fine
    approximation for term-structure purposes, and it keeps the code simple.
    """
    subset = chain_df[chain_df["expiry"] == expiry].copy()
    if subset.empty:
        raise ValueError(f"No contracts found for expiry {expiry}")

    subset["dist_from_spot"] = (subset["strike"] - underlying_price).abs()
    nearest_strike = subset.loc[subset["dist_from_spot"].idxmin(), "strike"]

    at_strike = subset[subset["strike"] == nearest_strike]
    ivs = at_strike["iv"].dropna()
    if ivs.empty:
        raise ValueError(f"No IV data at strike {nearest_strike} for expiry {expiry}")
    return float(ivs.mean())


# ---------------------------------------------------------------------------
# Term structure
# ---------------------------------------------------------------------------

@dataclass
class TermStructure:
    front_iv: float
    back_iv: float
    spread: float       # front - back
    ratio: float         # front / back


def term_structure(front_iv: float, back_iv: float) -> TermStructure:
    return TermStructure(
        front_iv=front_iv,
        back_iv=back_iv,
        spread=front_iv - back_iv,
        ratio=(front_iv / back_iv) if back_iv else float("nan"),
    )


def interpret_curve(ts: TermStructure) -> str:
    """Plain-language read on the curve shape. Thresholds here are starting points —
    tune them against your own historical data once you have it; there's nothing
    universally "correct" about 1.15 vs 1.10 as a cutoff."""
    if ts.ratio >= 1.15:
        return "Steep front-loaded curve — potential mean-reversion / elevated front-IV opportunity"
    elif ts.ratio <= 0.95:
        return "Inverted curve (back IV > front IV) — unusual, often signals an anticipated event in the back month"
    elif 0.98 <= ts.ratio <= 1.05:
        return "Flat curve — low term-structure edge"
    else:
        return "Normal mild contango — no extreme signal either way"


# ---------------------------------------------------------------------------
# Percentile context
# ---------------------------------------------------------------------------

def percentile_rank(history: pd.Series, current_value: float) -> float:
    """
    Returns what percentile `current_value` falls at relative to `history`.
    E.g. 92.0 means current value is higher than 92% of historical observations.

    This is the piece that turns a raw "IV spread = 3.3" into something decision-
    relevant: "3.3 is higher than 92% of the last 6 months" tells you whether
    today is statistically unusual or just an average Tuesday.
    """
    clean = history.dropna()
    if len(clean) == 0:
        return float("nan")
    return float((clean < current_value).mean() * 100)


def sample_size_warning(history: pd.Series, min_recommended: int = 200) -> str | None:
    """Returns a warning string if there isn't enough history yet to trust the
    percentile figure (roughly: 200 observations ≈ a few weeks of 10s polling
    during market hours, or several months of sparser sampling)."""
    n = len(history.dropna())
    if n < min_recommended:
        return (f"Only {n} historical observations — percentile estimates are not "
                 f"statistically reliable yet (recommend {min_recommended}+).")
    return None


# ---------------------------------------------------------------------------
# Mean reversion estimate
# ---------------------------------------------------------------------------

@dataclass
class ReversionEstimate:
    front_vs_mean: float          # current front IV minus historical mean front IV
    back_vs_mean: float
    estimated_crush_pct: float     # rough estimate of how much front IV could contract


def mean_reversion_estimate(current_front_iv: float, current_back_iv: float,
                              historical_front: pd.Series, historical_back: pd.Series,
                              dte: int) -> ReversionEstimate:
    """
    A deliberately simple model: assumes IV reverts toward its historical mean,
    and that more of that reversion happens the more DTE remain (more time for
    mean reversion to play out) — capped so the estimate doesn't imply more
    reversion than is plausible in the time available.

    This is a heuristic, not a forecast — treat "estimated_crush_pct" as a rough
    sizing signal for how much edge might be on the table, not a prediction you
    should bet the position size on. If you want something more rigorous later,
    look into a proper mean-reverting stochastic vol model (e.g. an OU process
    fit to your accumulated history) — this scaffold deliberately keeps Phase 2
    simple so you have something working before investing in that.
    """
    front_mean = historical_front.dropna().mean()
    back_mean = historical_back.dropna().mean()

    front_vs_mean = current_front_iv - front_mean if not np.isnan(front_mean) else float("nan")
    back_vs_mean = current_back_iv - back_mean if not np.isnan(back_mean) else float("nan")

    # Reversion speed assumption: capped at 70% reversion by 30 DTE, scaling down
    # for fewer days remaining. Tune this once you have real data on how fast
    # your front-month IV actually mean-reverts.
    time_factor = min(dte / 30, 1.0) * 0.7
    estimated_crush_pct = (front_vs_mean * time_factor) if not np.isnan(front_vs_mean) else float("nan")

    return ReversionEstimate(
        front_vs_mean=front_vs_mean,
        back_vs_mean=back_vs_mean,
        estimated_crush_pct=estimated_crush_pct,
    )


# ---------------------------------------------------------------------------
# Trade Quality Score
# ---------------------------------------------------------------------------

def liquidity_score(volume: float, open_interest: float) -> float:
    """0-100 score from volume + OI. SPX is generally liquid, so thresholds are
    set higher than you'd use for a single-name equity option."""
    vol_score = min(volume / 500, 1.0) * 50 if volume else 0
    oi_score = min(open_interest / 2000, 1.0) * 50 if open_interest else 0
    return vol_score + oi_score


@dataclass
class RangeStats:
    low: float
    high: float
    current: float
    position_pct: float  # 0-100, where current sits between low and high


def range_stats(series: pd.Series, current_value: float) -> RangeStats:
    """Returns the low/high of `series` plus where `current_value` sits within
    that range as a 0-100 position — this is what drives the slider-style bar
    in the 'Historical Statistics' panel (mirrors the Today/5D/20D bars in
    Flux: low value on the left, high on the right, current marked between)."""
    clean = series.dropna()
    if clean.empty:
        return RangeStats(low=float("nan"), high=float("nan"), current=current_value, position_pct=50.0)
    low, high = float(clean.min()), float(clean.max())
    if high == low:
        pct = 50.0
    else:
        pct = max(0.0, min(100.0, (current_value - low) / (high - low) * 100))
    return RangeStats(low=low, high=high, current=current_value, position_pct=pct)


def trade_quality_score(iv_spread_percentile: float, liquidity: float,
                          theta_advantage: float) -> float:
    """
    Weighted composite, 0-100. Weights are a starting point — once you've logged
    enough actual trade outcomes (Phase 4), replace these with weights fit to
    what's actually predicted your historical win rate rather than guessed weights.
    """
    weights = {"iv_edge": 0.45, "liquidity": 0.30, "theta": 0.25}
    iv_edge = min(max(iv_spread_percentile, 0), 100)
    liquidity_clamped = min(max(liquidity, 0), 100)
    theta_clamped = min(max(theta_advantage, 0), 100)
    return (weights["iv_edge"] * iv_edge
            + weights["liquidity"] * liquidity_clamped
            + weights["theta"] * theta_clamped)


# ---------------------------------------------------------------------------
# Strike-specific IV lookup
# ---------------------------------------------------------------------------

@dataclass
class StrikeContract:
    expiry: str
    strike: float
    side: str
    iv: float | None
    bid: float | None
    ask: float | None
    volume: float | None
    open_interest: float | None
    found_exact: bool   # False means we fell back to nearest available strike


def strike_contract(chain_df: pd.DataFrame, expiry: str, strike: float, side: str) -> StrikeContract:
    """
    Returns IV and market data for a specific strike/side/expiry.

    If the exact strike exists in the chain, returns it directly (found_exact=True).
    If not (e.g. the typed strike is between two available strikes, or this expiry
    has coarser strike spacing), falls back to the nearest available strike and
    flags found_exact=False so the UI can warn the user. This matters for your
    entry accuracy — SPX has 5-point strike spacing near spot but 25-point spacing
    far out, so a typo or slightly-off strike is a realistic scenario.
    """
    subset = chain_df[
        (chain_df["expiry"] == expiry) &
        (chain_df["strike"] == float(strike)) &
        (chain_df["side"] == side.upper())
    ]

    exact = not subset.empty
    if not exact:
        # Nearest-strike fallback
        candidates = chain_df[
            (chain_df["expiry"] == expiry) &
            (chain_df["side"] == side.upper())
        ].copy()
        if candidates.empty:
            return StrikeContract(expiry=expiry, strike=strike, side=side,
                                   iv=None, bid=None, ask=None, volume=None,
                                   open_interest=None, found_exact=False)
        candidates["dist"] = (candidates["strike"] - strike).abs()
        subset = candidates.nsmallest(1, "dist")

    row = subset.iloc[0]
    return StrikeContract(
        expiry=expiry,
        strike=float(row["strike"]),
        side=side.upper(),
        iv=float(row["iv"]) if row["iv"] is not None and not pd.isna(row["iv"]) else None,
        bid=float(row["bid"]) if row["bid"] is not None and not pd.isna(row["bid"]) else None,
        ask=float(row["ask"]) if row["ask"] is not None and not pd.isna(row["ask"]) else None,
        volume=float(row["volume"]) if row["volume"] is not None and not pd.isna(row["volume"]) else None,
        open_interest=float(row["open_interest"]) if row["open_interest"] is not None and not pd.isna(row["open_interest"]) else None,
        found_exact=exact,
    )


# ---------------------------------------------------------------------------
# Expected Move Log Check
# ---------------------------------------------------------------------------

@dataclass
class ExpectedMoveCheck:
    spot: float
    atm_iv_pct: float        # ATM IV as a percentage, e.g. 18.4
    max_dte: int             # Longest DTE in the current fetch window
    em_1sd: float            # 1 standard deviation expected move (points)
    em_2sd: float            # 2 standard deviation expected move (points)
    configured_window: int   # config.STRIKE_FETCH_WIDTH_POINTS
    window_adequate: bool    # True if configured window >= 2 SD move


def expected_move_log_check(
    spot: float,
    atm_iv_pct: float,
    max_dte: int,
) -> ExpectedMoveCheck:
    """
    Computes the 1 SD and 2 SD expected move for the longest expiry in scope
    and checks whether the configured strike window (STRIKE_FETCH_WIDTH_POINTS)
    covers the full 2 SD range.

    This function is informational only — its result is never used to gate or
    modify what gets fetched or stored. Call it once per snapshot cycle and log
    the result. If window_adequate is ever False, it is a signal to increase
    STRIKE_FETCH_WIDTH_POINTS in config.py (and correspondingly STRIKE_COUNT).

    Formula: Expected Move = Spot × (IV / 100) × √(DTE / 365)
    This is the standard market-implied 1 SD move, derived from ATM straddle
    pricing. It matches the convention used by most options platforms.

    Args:
        spot:        Current SPX underlying price.
        atm_iv_pct: ATM implied volatility as a percentage (e.g. 18.4 for 18.4%).
                    Use the longest-DTE expiry in the current window as the anchor,
                    since this produces the widest expected move and is therefore
                    the most conservative adequacy check.
        max_dte:    DTE of the longest expiry in the current fetch window.

    Returns:
        ExpectedMoveCheck dataclass with all computed values and adequacy flag.
    """
    iv_decimal = atm_iv_pct / 100.0
    em_1sd = spot * iv_decimal * math.sqrt(max_dte / 365)
    em_2sd = 2 * em_1sd
    window = config.STRIKE_FETCH_WIDTH_POINTS

    return ExpectedMoveCheck(
        spot=spot,
        atm_iv_pct=atm_iv_pct,
        max_dte=max_dte,
        em_1sd=round(em_1sd, 1),
        em_2sd=round(em_2sd, 1),
        configured_window=window,
        window_adequate=em_2sd <= window,
    )