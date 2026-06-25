"""
iv_engine.py — Analytics core.

Everything here is pure functions: given data, return numbers.
No API calls, no UI, no database writes.

Dashboard v1 additions (2026-06-25):
  - iv_regime()        → regime label + CSS color for any IV ratio
  - CalendarEdge       → call edge / put edge per-strike dataclass
  - calendar_edge()    → computes CalendarEdge from chain_df
  - TransformCredit    → full transformation viability dataclass
  - transform_credit() → theoretical lock-in credit calculation
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
    back_iv:  float
    spread:   float   # front - back
    ratio:    float   # front / back


def term_structure(front_iv: float, back_iv: float) -> TermStructure:
    return TermStructure(
        front_iv=front_iv,
        back_iv=back_iv,
        spread=front_iv - back_iv,
        ratio=(front_iv / back_iv) if back_iv else float("nan"),
    )


def interpret_curve(ts: TermStructure) -> str:
    """
    Plain-language, NON-DIRECTIONAL read on the curve shape.

    TERMINOLOGY (standard volatility conventions):
      ratio > 1.0  → front IV > back IV → BACKWARDATION (inverted term structure)
      ratio < 1.0  → front IV < back IV → CONTANGO (normal term structure)
      ratio ≈ 1.0  → flat

    FAVORABILITY IS NOT ASSERTED. Whether a given structure is good or bad for
    this strategy is an OPEN, UNVALIDATED question (see DOCUMENTATION.md §3.1 and
    the 2026-06-25 audit). Earlier versions claimed ratio < 1.0 was "favorable";
    that claim rested on a single paper trade and has been retracted. This text
    describes the structure factually and offers no entry recommendation.
    """
    if ts.ratio < 0.95:
        shape = "Contango (normal) — back IV above front."
    elif ts.ratio <= 1.05:
        shape = "Flat — front and back IV roughly equal."
    else:
        shape = "Backwardation (inverted) — front IV above back."
    return (
        f"ℹ️ {shape}  Favorability for this strategy is unvalidated — treat this "
        f"as neutral context, not an entry signal. Validate against logged trades "
        f"before acting on any regime."
    )


# ---------------------------------------------------------------------------
# IV Regime classification  (NEW — Dashboard v1)
# ---------------------------------------------------------------------------

def iv_regime(ratio: float) -> tuple[str, str]:
    """
    Returns (regime_label, hex_color) for a given IV ratio (front_iv / back_iv).

    NEUTRAL, NON-VALENCED classification. Labels describe WHICH SIDE is elevated;
    they do NOT assert good/bad. Colors are a non-valenced blue↔purple palette
    (neither conventionally means good or bad in finance) so the UI never implies
    that one regime is favorable. Favorability is unvalidated — see
    DOCUMENTATION.md §3.1 and the 2026-06-25 audit.

    TERMINOLOGY (standard volatility conventions):
      ratio > 1.0 → front IV > back IV → BACKWARDATION (inverted term structure)
      ratio < 1.0 → front IV < back IV → CONTANGO (normal term structure)

    Bands:
      < 0.92        BACK-ELEVATED   (strong contango)
      0.92 – 0.97   BACK-LEANING
      0.97 – 1.03   FLAT
      1.03 – 1.08   FRONT-LEANING
      > 1.08        FRONT-ELEVATED  (strong backwardation)
    """
    if ratio < 0.92:
        return "BACK-ELEVATED", "#5b8fb9"     # muted blue
    elif ratio < 0.97:
        return "BACK-LEANING",  "#7b9cc4"
    elif ratio <= 1.03:
        return "FLAT",          "#8b949e"      # neutral gray
    elif ratio <= 1.08:
        return "FRONT-LEANING", "#b59cc4"
    else:
        return "FRONT-ELEVATED","#9b7cc4"      # muted purple


# ---------------------------------------------------------------------------
# Percentile context
# ---------------------------------------------------------------------------

def percentile_rank(history: pd.Series, current_value: float) -> float:
    """
    Returns what percentile `current_value` falls at relative to `history`.
    E.g. 92.0 means current value is higher than 92% of historical observations.
    """
    clean = history.dropna()
    if len(clean) == 0:
        return float("nan")
    return float((clean < current_value).mean() * 100)


def sample_size_warning(history: pd.Series, min_recommended: int = 200) -> str | None:
    """Returns a warning string if there isn't enough history yet to trust the
    percentile figure (roughly: 200 observations ≈ a few weeks of 5-min polling
    during market hours)."""
    n = len(history.dropna())
    if n < min_recommended:
        return (
            f"Only {n} historical observations — percentile estimates are not "
            f"statistically reliable yet (recommend {min_recommended}+)."
        )
    return None


# ---------------------------------------------------------------------------
# Mean reversion estimate
# ---------------------------------------------------------------------------

@dataclass
class ReversionEstimate:
    front_vs_mean:      float   # current front IV minus historical mean front IV
    back_vs_mean:       float
    estimated_crush_pct: float  # rough estimate of how much front IV could contract


def mean_reversion_estimate(
    current_front_iv: float,
    current_back_iv: float,
    historical_front: pd.Series,
    historical_back: pd.Series,
    dte: int,
) -> ReversionEstimate:
    """
    A deliberately simple model: assumes IV reverts toward its historical mean,
    and that more of that reversion happens the more DTE remain.

    This is a heuristic, not a forecast — treat estimated_crush_pct as a rough
    sizing signal, not a prediction to bet position size on.
    """
    front_mean = historical_front.dropna().mean()
    back_mean  = historical_back.dropna().mean()

    front_vs_mean = (current_front_iv - front_mean) if not np.isnan(front_mean) else float("nan")
    back_vs_mean  = (current_back_iv  - back_mean)  if not np.isnan(back_mean)  else float("nan")

    # Reversion speed assumption: capped at 70% reversion by 30 DTE.
    time_factor         = min(dte / 30, 1.0) * 0.7
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
    """0-100 score from volume + OI. SPX is generally liquid, so thresholds
    are set higher than you'd use for single-name equity options."""
    vol_score = min(volume / 500,  1.0) * 50 if volume else 0
    oi_score  = min(open_interest / 2000, 1.0) * 50 if open_interest else 0
    return vol_score + oi_score


@dataclass
class RangeStats:
    low:          float
    high:         float
    current:      float
    position_pct: float   # 0-100, where current sits between low and high


def range_stats(series: pd.Series, current_value: float) -> RangeStats:
    """Returns the low/high of `series` plus where `current_value` sits within
    that range as a 0-100 position — drives the slider-style bar in the
    Historical Statistics panel."""
    clean = series.dropna()
    if clean.empty:
        return RangeStats(
            low=float("nan"), high=float("nan"),
            current=current_value, position_pct=50.0,
        )
    low, high = float(clean.min()), float(clean.max())
    if high == low:
        pct = 50.0
    else:
        pct = max(0.0, min(100.0, (current_value - low) / (high - low) * 100))
    return RangeStats(low=low, high=high, current=current_value, position_pct=pct)


def trade_quality_score(
    iv_spread_percentile: float,
    liquidity: float,
    theta_advantage: float,
) -> float:
    """
    Weighted composite, 0-100.
    Weights are a starting point — once you've logged enough actual trade
    outcomes (Phase 4), replace these with weights fit to your historical win
    rate rather than these initial guesses.
    """
    weights = {"iv_edge": 0.45, "liquidity": 0.30, "theta": 0.25}
    iv_edge          = min(max(iv_spread_percentile, 0), 100)
    liquidity_clamped = min(max(liquidity, 0), 100)
    theta_clamped    = min(max(theta_advantage, 0), 100)
    return (
        weights["iv_edge"]  * iv_edge
        + weights["liquidity"] * liquidity_clamped
        + weights["theta"]     * theta_clamped
    )


# ---------------------------------------------------------------------------
# Strike-specific IV lookup
# ---------------------------------------------------------------------------

@dataclass
class StrikeContract:
    expiry:       str
    strike:       float
    side:         str
    iv:           float | None
    bid:          float | None
    ask:          float | None
    mark:         float | None
    volume:       float | None
    open_interest: float | None
    found_exact:  bool   # False means we fell back to nearest available strike


def strike_contract(
    chain_df: pd.DataFrame,
    expiry: str,
    strike: float,
    side: str,
) -> StrikeContract:
    """
    Returns IV and market data for a specific strike/side/expiry.

    If the exact strike exists in the chain, returns it directly (found_exact=True).
    If not, falls back to the nearest available strike and flags found_exact=False
    so the UI can warn the user.
    """
    def _row_to_contract(row, exact: bool) -> StrikeContract:
        def _f(col):
            v = row.get(col)
            return float(v) if v is not None and not pd.isna(v) else None

        # Compute mark from bid/ask if the pre-computed column is missing/null
        bid_v  = _f("bid")
        ask_v  = _f("ask")
        mark_v = _f("mark")
        if mark_v is None and bid_v is not None and ask_v is not None:
            mark_v = (bid_v + ask_v) / 2.0

        return StrikeContract(
            expiry=expiry,
            strike=float(row["strike"]),
            side=side.upper(),
            iv=_f("iv"),
            bid=bid_v,
            ask=ask_v,
            mark=mark_v,
            volume=_f("volume"),
            open_interest=_f("open_interest"),
            found_exact=exact,
        )

    subset = chain_df[
        (chain_df["expiry"] == expiry)
        & (chain_df["strike"] == float(strike))
        & (chain_df["side"]   == side.upper())
    ]

    if not subset.empty:
        return _row_to_contract(subset.iloc[0], exact=True)

    # Nearest-strike fallback
    candidates = chain_df[
        (chain_df["expiry"] == expiry)
        & (chain_df["side"]  == side.upper())
    ].copy()

    if candidates.empty:
        return StrikeContract(
            expiry=expiry, strike=strike, side=side,
            iv=None, bid=None, ask=None, mark=None,
            volume=None, open_interest=None, found_exact=False,
        )

    candidates["_dist"] = (candidates["strike"] - strike).abs()
    return _row_to_contract(candidates.nsmallest(1, "_dist").iloc[0], exact=False)


# ---------------------------------------------------------------------------
# Calendar Edge  (NEW — Dashboard v1)
# ---------------------------------------------------------------------------

@dataclass
class CalendarEdge:
    """
    Per-strike IV differential between front and back expiry.

    call_edge = front_call_iv - back_call_iv
    put_edge  = front_put_iv  - back_put_iv

    Sign reading (standard terminology, NO favorability implied — see audit
    2026-06-25 and DOCUMENTATION.md §3.1):
      positive edge → front IV above back → backwardation on that side
      negative edge → front IV below back → contango on that side
    Which (if either) is advantageous is an open, unvalidated question.
    """
    call_edge:   float | None
    put_edge:    float | None
    call_ratio:  float | None   # front_call_iv / back_call_iv
    put_ratio:   float | None   # front_put_iv  / back_put_iv
    front_call:  StrikeContract | None
    back_call:   StrikeContract | None
    front_put:   StrikeContract | None
    back_put:    StrikeContract | None


def calendar_edge(
    chain_df: pd.DataFrame,
    front_expiry: str,
    back_expiry:  str,
    call_strike:  float,
    put_strike:   float,
) -> CalendarEdge:
    """
    Computes call-side and put-side IV edge at the selected strikes.

    call_edge = front_call_iv - back_call_iv
    put_edge  = front_put_iv  - back_put_iv

    Negative values mean front IV is below back IV (contango on that side);
    positive values mean front above back (backwardation). No favorability is
    implied — see DOCUMENTATION.md §3.1.
    """
    fc = strike_contract(chain_df, front_expiry, call_strike, "CALL")
    bc = strike_contract(chain_df, back_expiry,  call_strike, "CALL")
    fp = strike_contract(chain_df, front_expiry, put_strike,  "PUT")
    bp = strike_contract(chain_df, back_expiry,  put_strike,  "PUT")

    c_edge   = (fc.iv - bc.iv)     if (fc.iv and bc.iv) else None
    p_edge   = (fp.iv - bp.iv)     if (fp.iv and bp.iv) else None
    c_ratio  = (fc.iv / bc.iv)     if (fc.iv and bc.iv) else None
    p_ratio  = (fp.iv / bp.iv)     if (fp.iv and bp.iv) else None

    return CalendarEdge(
        call_edge=c_edge,
        put_edge=p_edge,
        call_ratio=c_ratio,
        put_ratio=p_ratio,
        front_call=fc,
        back_call=bc,
        front_put=fp,
        back_put=bp,
    )


# ---------------------------------------------------------------------------
# Transform Credit  (NEW — Dashboard v1)
# ---------------------------------------------------------------------------

@dataclass
class TransformCredit:
    """
    Theoretical transformation credit — how much you lock in if you close
    the diagonal into an Iron Condor right now.

    Formula:
        theoretical_credit = back_legs_value - close_cost - entry_debit

    Where:
        back_legs_value = back_call_mark + back_put_mark  (your long legs)
        close_cost      = front_call_ask + front_put_ask  (cost to close shorts)
        entry_debit     = what you originally paid to enter
        diagonal_mark   = back_legs_value - close_cost    (position value if closed now)

    If theoretical_credit >= threshold → transformation is viable.

    Note on the metric (from 2026-06-23 review, corroborated by 2026-06-25 audit):
      The correct viability metric is theoretical_credit, NOT the diagonal mark alone.
      The diagonal mark ignores the entry debit, so it overstates the locked profit.
      (This is a definitional point and is independent of the unvalidated IV-regime
      favorability question.)
    """
    back_call_mark:    float | None
    back_put_mark:     float | None
    front_call_ask:    float | None
    front_put_ask:     float | None
    back_legs_value:   float | None   # back_call + back_put
    close_cost:        float | None   # front_call_ask + front_put_ask
    diagonal_mark:     float | None   # back_legs_value - close_cost
    theoretical_credit: float | None  # diagonal_mark - entry_debit
    gap_to_threshold:  float | None   # threshold - theoretical_credit (negative = above)
    is_viable:         bool           # theoretical_credit >= threshold
    threshold:         float
    entry_debit:       float
    # NOTE: Theta ETA fields were REMOVED 2026-06-25 (audit). The estimate
    # ignored back-leg theta, vega, delta, and gamma and presented a single-leg
    # linear-decay guess as an actionable time-to-threshold. A proper estimate
    # belongs in Phase 3, built from stored per-leg Greeks — not from close_cost/dte.


def transform_credit(
    chain_df: pd.DataFrame,
    front_expiry:  str,
    back_expiry:   str,
    call_strike:   float,
    put_strike:    float,
    entry_debit:   float,
    threshold:     float = 5.0,
) -> TransformCredit:
    """
    Computes the theoretical transformation credit.

    back_legs_value = back call mark + back put mark
    close_cost      = front call ask + front put ask
    diagonal_mark   = back_legs_value - close_cost
    theoretical_credit = diagonal_mark - entry_debit

    (Theta ETA was removed 2026-06-25 — see dataclass note.)
    """
    def _get(expiry, strike, side, col):
        rows = chain_df[
            (chain_df["expiry"] == expiry)
            & (chain_df["strike"] == float(strike))
            & (chain_df["side"]   == side)
        ]
        if rows.empty:
            return None
        v = rows.iloc[0].get(col)
        return float(v) if v is not None and not pd.isna(v) else None

    def _mark(expiry, strike, side):
        m = _get(expiry, strike, side, "mark")
        if m is None:
            b = _get(expiry, strike, side, "bid")
            a = _get(expiry, strike, side, "ask")
            if b is not None and a is not None:
                m = (b + a) / 2.0
        return m

    bc_mark = _mark(back_expiry,  call_strike, "CALL")
    bp_mark = _mark(back_expiry,  put_strike,  "PUT")
    fc_ask  = _get(front_expiry, call_strike, "CALL", "ask")
    fp_ask  = _get(front_expiry, put_strike,  "PUT",  "ask")

    back_legs  = (bc_mark + bp_mark) if (bc_mark is not None and bp_mark is not None) else None
    close_cost = (fc_ask  + fp_ask)  if (fc_ask  is not None and fp_ask  is not None) else None

    diag_mark = (back_legs - close_cost) if (back_legs is not None and close_cost is not None) else None
    credit    = (diag_mark - entry_debit) if diag_mark is not None else None
    gap       = (threshold - credit) if credit is not None else None

    return TransformCredit(
        back_call_mark=bc_mark,
        back_put_mark=bp_mark,
        front_call_ask=fc_ask,
        front_put_ask=fp_ask,
        back_legs_value=back_legs,
        close_cost=close_cost,
        diagonal_mark=diag_mark,
        theoretical_credit=credit,
        gap_to_threshold=gap,
        is_viable=(credit >= threshold) if credit is not None else False,
        threshold=threshold,
        entry_debit=entry_debit,
    )


# ---------------------------------------------------------------------------
# Expected Move Log Check
# ---------------------------------------------------------------------------

@dataclass
class ExpectedMoveCheck:
    spot:               float
    atm_iv_pct:         float
    max_dte:            int
    em_1sd:             float
    em_2sd:             float
    configured_window:  int
    window_adequate:    bool


def expected_move_log_check(
    spot: float,
    atm_iv_pct: float,
    max_dte: int,
) -> ExpectedMoveCheck:
    """
    Computes 1 SD and 2 SD expected move for the longest expiry in scope
    and checks whether the configured strike window covers the full 2 SD range.

    Informational only — result is never used to gate or modify what gets fetched.

    Formula: Expected Move = Spot × (IV / 100) × √(DTE / 365)
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
