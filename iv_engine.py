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


def iv_index(chain_df: pd.DataFrame) -> float:
    """One number for "how expensive is this whole chain right now".

    THE MEAN OF MEANS IS NOT THE MEAN, and that is the point rather than a
    slip. Averaging every contract in one pass would weight an expiry by how
    many strikes happen to be listed for it, so a heavily-quoted weekly would
    drown out a thin monthly and the number would move when the chain's shape
    moved rather than when volatility did. Grouping by expiry first gives each
    expiry one vote.

    ONE DEFINITION, TWO CALLERS -- the "IV Index (avg)" figure in
    `views/edge.py` and the served headline in `api.computed.edge_headline`.
    It lived only in the view until 2026-09-06, when the React tab needed the
    same figure and the choice was to copy the expression or to name it.

    Returns a percent, because `chain_df["iv"]` is stored as one.
    """
    return float(chain_df.groupby("expiry")["iv"].mean().mean())


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


# Where a percentile stops being unremarkable. Below the 25th the current
# ratio is near the bottom of everything on record for this window; above the
# 75th, near the top. The middle is called MID rather than left blank, because
# "we looked and it is ordinary" and "we did not look" are different answers.
#
# THE COLOURS ARE NOT A RECOMMENDATION. Green on HIGH means the front leg is
# expensive relative to its own history -- the condition this strategy looks
# for -- and NOT that the trade is good. Favorability is unvalidated; see
# `interpret_curve` above and DOCUMENTATION.md 3.1.
PERCENTILE_LOW = 25.0
PERCENTILE_HIGH = 75.0


def percentile_band(pct: float) -> tuple[str, str]:
    """("HIGH" | "MID" | "LOW", colour) for a percentile rank.

    ONE DEFINITION, TWO CALLERS -- the Historical Statistics panel in
    `views/historical.py` and the served windows in
    `api.computed.historical_stats`. The boundaries are a claim about when a
    reading is worth noticing, and two screens holding separate copies is two
    screens that will eventually disagree about whether today is unusual.

    NaN -- no history at all -- is MID and grey. An unknown percentile must
    not paint green.
    """
    if pct != pct:  # NaN
        return "MID", "#6d8fa8"
    if pct > PERCENTILE_HIGH:
        return "HIGH", "#10d4a3"
    if pct < PERCENTILE_LOW:
        return "LOW", "#f05252"
    return "MID", "#6d8fa8"


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


# ---------------------------------------------------------------------------
# Second-order Greeks — Vanna and Charm
# ---------------------------------------------------------------------------
#
# WHY THESE ARE COMPUTED AND NOT FETCHED. Schwab returns delta, gamma, theta
# and vega; almost no broker publishes the second-order pair. They are not
# extra market data, though — they are closed-form functions of the SAME five
# Black-Scholes inputs the first-order Greeks come from, four of which
# (strike, spot, time, IV) are already in `option_rows` at full precision.
#
# THE RECORD SUPPORTS THIS. Measured on the live database 2026-09-04, the
# stored Greeks are one internally consistent Black-Scholes set: across 2,760
# near-the-money contracts the identity vega = S^2 * sigma * T * gamma held to
# a median ratio of 1.0011 once vega was read as per-vol-POINT. So anything
# derived here lands on the same surface as what the dashboard already draws,
# rather than on a second, subtly different one.
#
# WHAT IS ASSUMED, STATED RATHER THAN BURIED. `r` and `q` are not in the
# record and are not recoverable from it:
#
#   * Put-call parity would give the forward exactly (SPX is European and both
#     sides are collected at all 80 strikes) but not from these prices. Fitted
#     across the full strike range on 2026-09-04's 4-DTE expiry it returned a
#     discount factor of 0.9936 over four days — a ~65% rate. The mid-prices
#     are too wide relative to the signal.
#   * Inverting the stored delta and gamma for d1 and the carry rate is
#     mathematically clean and dead on arrival in practice: `gamma` is stored
#     to three decimals and SPX gamma is ~0.003, so it carries ONE significant
#     figure — 4,000 sampled rows held 13 distinct gamma values. The implied
#     carry came back at ±200%, which is rounding noise and nothing else.
#
# So both are caller-supplied constants (config.RISK_FREE_RATE,
# config.DIVIDEND_YIELD). For a diagonal calendar the error is second-order
# and largely cancels between the two legs, because both are priced off the
# same r and q; it would matter much more for an outright long-dated position.
#
# UNITS, chosen to match what is already stored rather than what the textbook
# prints. Analytic vanna is per 1.00 of vol and analytic charm is per YEAR;
# the dashboard's vega is per vol POINT and its theta is per DAY. Both are
# converted here, so "vanna" reads against "vega" and "charm" against "theta"
# without a mental scale factor. See VANNA_PER_VOL_POINT / CHARM_PER_DAY.

# One vol point is 1/100th of 1.00 of volatility. Dividing by this turns the
# textbook per-unit-vol figure into the per-vol-point figure `vega` uses.
VANNA_PER_VOL_POINT = 100.0

# Calendar days in the year fraction `T` is measured in. Dividing by this
# turns the textbook per-year figure into the per-day figure `theta` uses.
CHARM_PER_DAY = 365.0


def _norm_pdf(x: float) -> float:
    """Standard normal density. Written out rather than pulled from SciPy:
    this module has no dependency beyond pandas and is not going to grow one
    for two lines of arithmetic."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF, via the error function in the standard library."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(spot: float, strike: float, t_years: float, sigma: float,
           r: float, q: float) -> tuple[float, float] | None:
    """The two Black-Scholes moneyness terms, or None if they are undefined.

    None rather than a number whenever an input makes the formula meaningless:
    a non-positive spot, strike, time or volatility. That is the project's
    blank-not-zero rule — an expired contract does not have a vanna of zero,
    it does not have one at all — and it is what keeps `nan` out of the frames
    these feed.
    """
    if (spot is None or strike is None or t_years is None or sigma is None
            or spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0):
        return None
    vol_t = sigma * math.sqrt(t_years)
    d1 = (math.log(spot / strike)
          + (r - q + 0.5 * sigma * sigma) * t_years) / vol_t
    return d1, d1 - vol_t


def vanna(spot: float, strike: float, t_years: float, iv_pct: float,
          r: float, q: float) -> float | None:
    """dDelta/dSigma — how much an option's delta moves per vol point.

    THE SAME FOR CALLS AND PUTS, and that is a fact rather than a
    simplification. Put-call parity fixes Delta_call - Delta_put at e^(-qT),
    which contains no sigma, so differentiating by sigma annihilates it and
    both sides have identical vanna. This is why `right` is not a parameter,
    and why core.gex.vanna_by_strike has to impose the dealer sign convention
    the way gamma does — the number itself carries no side.

        vanna = -e^(-qT) * phi(d1) * d2 / sigma

    Sign reads as: POSITIVE means the option's delta RISES when implied
    volatility rises. That happens for strikes ABOVE the forward, where d2 is
    negative; for strikes below it d2 is positive and vanna is negative, so
    rising vol pulls delta down. Note the direction of that sentence — d2 is
    negative for HIGH strikes, which is the opposite of the way "above the
    money" reads, and is the easiest thing on this page to state backwards.

    `iv_pct` is a PERCENTAGE, per this module's convention (18.5 means 18.5%).
    The result is per ONE VOL POINT, matching how `vega` is stored, so a vanna
    of 0.004 means "delta moves by 0.004 if IV goes from 18.5 to 19.5".

    Returns None when the inputs do not define an option — see _d1_d2.
    """
    if iv_pct is None:
        return None
    sigma = float(iv_pct) / 100.0
    terms = _d1_d2(spot, strike, t_years, sigma, r, q)
    if terms is None:
        return None
    d1, d2 = terms
    raw = -math.exp(-q * t_years) * _norm_pdf(d1) * d2 / sigma
    return raw / VANNA_PER_VOL_POINT


def charm(spot: float, strike: float, t_years: float, iv_pct: float,
          r: float, q: float, right: str) -> float | None:
    """dDelta/dTime — how much an option's delta drifts per day that PASSES.

    THE SIGN CONVENTION, spelled out because published ones contradict each
    other and a reader cannot tell which is meant from the number alone. This
    is the derivative with respect to CALENDAR TIME MOVING FORWARD, not with
    respect to time-to-expiry, so it answers the question actually being asked:

        POSITIVE  ->  this option's delta will be HIGHER tomorrow
        NEGATIVE  ->  this option's delta will be LOWER tomorrow

    which is the same direction-of-reading as theta (negative = you lose value
    as the day passes). The textbook form below is ALREADY this derivative —
    it is defined as -dDelta/dTau — so nothing is negated on the way out.

        dDelta/dT = ±q e^(-qT) N(±d1)
                    - e^(-qT) phi(d1) [2(r-q)T - d2 sigma sqrt(T)]
                      / (2 T sigma sqrt(T))

    with the upper sign for calls and the lower for puts. Unlike vanna, charm
    DOES differ between the two sides — parity's e^(-qT) term does depend on
    time — though only by q e^(-qT), which is small. `right` is therefore a
    real parameter here, and its absence from vanna() is not an oversight.

    THIS BLOWS UP AS EXPIRY APPROACHES. The 1/(T sqrt(T)) factor is unbounded,
    which is not a defect in the formula — an at-the-money option's delta
    really does lurch on its last day — but it does mean the figure is only as
    good as the precision of `t_years`. The database stores `dte` as whole
    days, so a 0DTE charm computed from that alone describes a contract the
    arithmetic believes has a full day left. See core.gex.year_fraction, which
    is where the fractional day is put back.

    Per DAY, matching `theta`. `iv_pct` is a percentage, per module convention.
    Returns None when the inputs do not define an option — see _d1_d2.
    """
    if iv_pct is None or right not in ("C", "P"):
        return None
    sigma = float(iv_pct) / 100.0
    terms = _d1_d2(spot, strike, t_years, sigma, r, q)
    if terms is None:
        return None
    d1, d2 = terms
    vol_t = sigma * math.sqrt(t_years)
    discount = math.exp(-q * t_years)

    if right == "C":
        carry = q * discount * _norm_cdf(d1)
    else:
        carry = -q * discount * _norm_cdf(-d1)

    decay = (discount * _norm_pdf(d1)
             * (2.0 * (r - q) * t_years - d2 * vol_t)
             / (2.0 * t_years * vol_t))

    # (carry - decay) IS -dDelta/dTau, i.e. already the derivative with
    # respect to calendar time moving forward — verified against a central
    # finite difference of the Black-Scholes delta in tests/test_second_order
    # _greeks.py. No further negation: adding one here inverts the answer
    # while leaving the magnitude right, which is the failure this project
    # keeps meeting and the reason that test exists.
    return (carry - decay) / CHARM_PER_DAY
