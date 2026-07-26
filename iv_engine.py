"""
iv_engine.py — Analytics core.

Everything here is pure functions: given data, return numbers.
No API calls, no UI, no database writes, no framework imports.

This purity is deliberate and load-bearing: it makes this module the one part
of the codebase that is fully unit-testable today, and it is the seed of the
framework-agnostic `core/` package that M2 builds out. Do not import streamlit,
sqlite3, or schwab here.

CONVENTIONS
  IV is passed in PERCENTAGE form (18.5 means 18.5%). The database stores
  decimals; callers multiply by 100 at the load boundary — nowhere else.
  Premium is in points, where 1.00 point = $100 per SPX contract.

Contents:
  atm_iv()             → ATM IV for an expiry, averaged across call and put
  TermStructure        → front/back IV, spread, ratio
  term_structure()     → builds TermStructure
  interpret_curve()    → plain-language, NON-DIRECTIONAL read on curve shape
  percentile_rank()    → where a value sits within a history
  sample_size_warning()→ guards against trusting thin percentiles
  liquidity_score()    → 0-100 from volume + open interest (thresholds UNVALIDATED)
  RangeStats /
  range_stats()        → low/high/current position for the range bars
  StrikeContract /
  strike_contract()    → per-strike market data with nearest-strike fallback
  CalendarEdge /
  calendar_edge()      → per-side front-vs-back IV differential
  TransformCredit /
  transform_credit()   → theoretical lock-in credit for the transformation
  atm_straddle_price() → S x sigma x sqrt(2T/pi) normalisation denominator
  normalized_debit()   → diagonal mark as a fraction of the expected move
  ThetaDifferential /
  theta_differential() → position-level daily theta across all four legs

Removed 2026-07-25 (M0.11) — all were unreachable; see decisions.md:
  iv_regime, mean_reversion_estimate/ReversionEstimate, trade_quality_score,
  expected_move_log_check/ExpectedMoveCheck.

NOTE: calendar_edge() and transform_credit() are currently NOT called by the
dashboard, which duplicates their logic inline. That is a defect in app.py,
not a reason to delete these — M2.2 wires app.py to call them, which also
removes the four duplicated copies of the $5.00 threshold and the two
hardcoded copies of the +/-5 wing offset (backlog DEBT-004, DEBT-006).
"""

import math
from dataclasses import dataclass

import pandas as pd

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
# Liquidity / range helpers
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


# NOTE: trade_quality_score() was removed 2026-07-25 (M0.11). It was never
# called. DOCUMENTATION.md §8.3 had already rejected composite "magic scores"
# on the grounds that they obscure which dimension drives the value, and §6.9
# flagged that two of its three inputs (IV_Edge_Pct direction, Theta_Advantage
# placeholder) had no validated basis. Do not reintroduce a composite score
# without first validating the components. See decisions.md ADR-001.


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


# NOTE: expected_move_log_check() / ExpectedMoveCheck were removed 2026-07-25
# (M0.11). Never called — the live 2 SD window check is implemented inline in
# schwab_client.filter_chain_by_strike_window(), which is where it actually
# runs. Keeping a second, unreachable copy of the same formula was a
# duplication hazard (backlog DEBT-012).


# ---------------------------------------------------------------------------
# Normalized Debit  (v3.2)
# ---------------------------------------------------------------------------

def atm_straddle_price(spx_price: float, atm_iv_pct: float, dte: int) -> float | None:
    """
    ATM straddle price approximation.

    Formula:  S × σ × √(2T / π)
    where σ = annualised IV (decimal) and T = DTE / 365.

    atm_iv_pct: IV in PERCENTAGE form (e.g. 18.5 means 18.5%).
    This matches the convention in chain_df after the ×100 load boundary.

    Returns None if any input is non-positive (no valid straddle can be computed).
    """
    if atm_iv_pct <= 0 or dte <= 0 or spx_price <= 0:
        return None
    sigma = atm_iv_pct / 100.0
    T     = dte / 365.0
    return spx_price * sigma * math.sqrt(2.0 * T / math.pi)


def normalized_debit(net_debit: float, straddle_price: float | None) -> float | None:
    """
    Normalize the diagonal entry cost by the ATM straddle price.

    net_debit / atm_straddle_price

    Removes the confounding effect of SPX price-level drift and vol-regime
    shifts, making the trade cost comparable across different dates and market
    environments.

    Returns None if straddle_price is None or zero.
    """
    if straddle_price is None or straddle_price <= 0:
        return None
    return net_debit / straddle_price


# ---------------------------------------------------------------------------
# Theta Differential  (v3.2)
# ---------------------------------------------------------------------------

@dataclass
class ThetaDifferential:
    """
    Position-level daily theta for the diagonal calendar spread.

    Structure:
        Short front call  +  Short front put   (we GAIN their daily decay)
        Long  back  call  +  Long  back  put   (we LOSE their daily decay)

    Theta convention: raw chain values are negative (the option loses value
    per day against its holder). For short legs, the sign flips — we receive
    that decay as profit.

    net_daily_theta = −front_sum + back_sum
        (−front_sum: front legs' decay magnitude, positive for us)
        (+back_sum:  back legs' decay burden, negative for us)

    Positive net_daily_theta means the position earns net time decay each day.
    This is the usual case for a diagonal when front DTE < back DTE.

    NOTE: theta_differential is a HYPOTHESIS metric (v3.2). Whether its
    magnitude at entry predicts transform profit has not been validated.
    Display raw values only — do not fold into any composite score.
    """
    front_call_theta:   float | None   # raw (negative)
    front_put_theta:    float | None   # raw (negative)
    back_call_theta:    float | None   # raw (negative)
    back_put_theta:     float | None   # raw (negative)
    front_sum:          float | None   # front_call + front_put (negative)
    back_sum:           float | None   # back_call + back_put (negative)
    net_daily_theta:    float | None   # per share / per day (+ve = position earns)
    net_daily_theta_ct: float | None   # × 100 (one contract = 100 shares)
    available:          bool           # False when Greeks absent from snapshot


def theta_differential(
    chain_df:      pd.DataFrame,
    front_expiry:  str,
    back_expiry:   str,
    call_strike:   float,
    put_strike:    float,
) -> ThetaDifferential:
    """
    Compute position-level daily theta for the four-leg diagonal.

    Positive net_daily_theta = the position earns time decay each day.
    This is expected to be positive for a diagonal where the front
    decays faster than the back, but magnitude depends on IV, DTE, and
    how far strikes are from spot.
    """
    def _theta(expiry: str, strike: float, side: str) -> float | None:
        rows = chain_df[
            (chain_df["expiry"] == expiry)
            & (chain_df["strike"] == float(strike))
            & (chain_df["side"]   == side.upper())
        ]
        if rows.empty:
            return None
        v = rows.iloc[0].get("theta")
        return float(v) if (v is not None and not pd.isna(v)) else None

    fc_t = _theta(front_expiry, call_strike, "CALL")
    fp_t = _theta(front_expiry, put_strike,  "PUT")
    bc_t = _theta(back_expiry,  call_strike, "CALL")
    bp_t = _theta(back_expiry,  put_strike,  "PUT")

    f_sum = (fc_t + fp_t) if (fc_t is not None and fp_t is not None) else None
    b_sum = (bc_t + bp_t) if (bc_t is not None and bp_t is not None) else None

    # Short front → position gains -f_sum (positive, since f_sum is negative)
    # Long back   → position pays   b_sum (negative, adding a loss)
    # net = -f_sum + b_sum
    net    = ((-f_sum) + b_sum) if (f_sum is not None and b_sum is not None) else None
    net_ct = (net * 100)        if net is not None else None

    return ThetaDifferential(
        front_call_theta=fc_t,
        front_put_theta=fp_t,
        back_call_theta=bc_t,
        back_put_theta=bp_t,
        front_sum=f_sum,
        back_sum=b_sum,
        net_daily_theta=net,
        net_daily_theta_ct=net_ct,
        available=net is not None,
    )
