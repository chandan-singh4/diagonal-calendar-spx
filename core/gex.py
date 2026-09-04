"""Gamma exposure — how much dealer hedging pressure sits at each strike.

WHAT THIS COMPUTES, AND WHY IT IS A CONVENTION RATHER THAN A MEASUREMENT.
Gamma exposure ("GEX") estimates how much stock a market maker must buy or
sell to stay delta-neutral as SPX moves. Nobody publishes dealer inventory, so
every GEX figure in existence rests on an ASSUMPTION about who holds what. The
standard one, adopted here and stated rather than buried:

    dealers are LONG calls and SHORT puts.

That is why calls carry +1 and puts -1 below. It is a convention, not a fact,
and it is the single largest source of error in anything built on this module.
It is also testable against the record we keep — a positive-GEX regime should
show lower realised intraday volatility than a negative one — so it can be
CHECKED later rather than believed forever. Nothing here assumes it is right.

THE UNIT. Dollar-delta change per 1% move in SPX:

    gamma x open_interest x 100 x spot^2 x 0.01

`gamma` is per share per point, `x 100` makes it per contract, and `spot^2 x
0.01` converts a one-point move into a one-percent move in dollar terms. This
is the industry-standard scaling and is what makes a headline figure like
"Net GEX: 110.2M" comparable to anything published elsewhere.

**The scale factor cannot change which strike is the peak**, because it is one
positive constant applied to every row alike. That matters because
`core.market.max_gex_label` — the header's "max GEX strike" — used a different
scaling (per one-POINT move) and now delegates here. The strike it names is
unchanged, and a test pins exactly that.

WHAT THE RECORD CAN AND CANNOT SUPPORT. Measured on the live database
2026-09-04, gamma x OI at the edge of the collector's +/-300 point window is
**0.2%** of its at-the-money value, so truncating there costs GEX almost
nothing: gamma concentrates near the money and in near-dated contracts, which
is precisely the slice the collector keeps.

**The same is emphatically NOT true of vega.** Vega lives in long-dated
options and the record stops at ~28 days, so a vanna or "VEX" measure built
from this data would describe the front month and not the market. That is a
real limitation of the data, not of the arithmetic, and this module therefore
offers gamma-flavoured measures only. Do not quietly extend it to vega
without widening collection first.

PURE. No database, no config, no clock, no Streamlit — a DataFrame and a spot
price in, a DataFrame out, so the whole thing is testable without a broker.
"""
from __future__ import annotations

import pandas as pd

# One option contract covers 100 shares of the underlying.
SHARES_PER_CONTRACT = 100

# A one-percent move, expressed as the fraction the spot^2 term is scaled by.
ONE_PERCENT = 0.01

# The dealer-positioning assumption, in one place. See the module docstring:
# this is the convention the whole measure rests on.
DEALER_SIGN = {"C": 1, "P": -1}

# The columns `by_strike` always returns, even when it returns no rows. A
# caller that has to branch on "did I get columns or not" ends up writing the
# empty case twice; every consumer here can rely on the shape.
COLUMNS = [
    "strike",
    "call_gex", "put_gex", "net_gex", "abs_gex",
    "call_oi", "put_oi",
    "call_volume", "put_volume",
]


def _blank() -> pd.DataFrame:
    """An empty result with the full column set. See COLUMNS."""
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS})


def by_strike(chain_df: pd.DataFrame, spot: float,
              *, expiry: str | None = None) -> pd.DataFrame:
    """Gamma exposure, open interest and volume per strike.

    `expiry` selects one expiry by its DISPLAY KEY (so the third Friday's a.m.
    and p.m. contracts stay apart — core/contract.py); None aggregates every
    expiry in the frame, which is the whole-chain figure most GEX commentary
    refers to.

    ROWS MISSING GAMMA ARE DROPPED, ROWS MISSING OPEN INTEREST ARE NOT.
    A contract with no gamma contributes nothing computable and is excluded.
    A contract with no open interest genuinely has none — that is a zero, and
    it must still appear so the strike shows up on the axis with an honest
    empty bar rather than vanishing. This is the "missing price -> blank, not
    0" rule pointed the correct way round: absent gamma is unknown, absent
    open interest is known to be nothing.

    Returns strikes in ascending order. Empty in, empty out — with columns.
    """
    if chain_df is None or chain_df.empty:
        return _blank()

    required = {"strike", "right", "gamma"}
    if not required.issubset(chain_df.columns):
        return _blank()

    work = chain_df
    if expiry is not None:
        if "expiry" not in work.columns:
            return _blank()
        work = work[work["expiry"] == expiry]

    work = work[work["gamma"].notna()].copy()
    if work.empty:
        return _blank()

    for col in ("open_interest", "volume"):
        work[col] = (pd.to_numeric(work.get(col), errors="coerce").fillna(0.0)
                     if col in work.columns else 0.0)

    scale = SHARES_PER_CONTRACT * (spot ** 2) * ONE_PERCENT
    work["gex"] = work["gamma"] * work["open_interest"] * scale
    work["sign"] = work["right"].map(DEALER_SIGN).fillna(0)

    is_call = work["right"] == "C"
    is_put = work["right"] == "P"

    out = pd.DataFrame({
        "call_gex": work["gex"].where(is_call, 0.0),
        "put_gex": work["gex"].where(is_put, 0.0),
        "net_gex": work["gex"] * work["sign"],
        "call_oi": work["open_interest"].where(is_call, 0.0),
        "put_oi": work["open_interest"].where(is_put, 0.0),
        "call_volume": work["volume"].where(is_call, 0.0),
        "put_volume": work["volume"].where(is_put, 0.0),
        "strike": work["strike"],
    }).groupby("strike", as_index=False).sum()

    # Absolute exposure — how much gamma sits here regardless of side. This is
    # the "Abs Gamma" view: it answers "where is the hedging concentrated",
    # which is a different question from "which way does it push".
    out["abs_gex"] = out["call_gex"] + out["put_gex"]

    return out.sort_values("strike", ignore_index=True)[COLUMNS]


def window(gex_df: pd.DataFrame, spot: float, count: int) -> pd.DataFrame:
    """The `count` strikes nearest spot, still in ascending strike order.

    A count of 0 or less, or a frame shorter than the count, returns
    everything — narrowing to a window is a display convenience and must never
    be able to hide strikes the caller asked to see.
    """
    if gex_df.empty or count <= 0 or len(gex_df) <= count:
        return gex_df
    nearest = (gex_df["strike"] - spot).abs().nsmallest(count).index
    return gex_df.loc[nearest].sort_values("strike", ignore_index=True)


def flip_strike(gex_df: pd.DataFrame) -> float | None:
    """The strike where CUMULATIVE net gamma exposure crosses zero.

    Widely called the "gamma flip" or zero-gamma level: below it dealers are
    said to be short gamma and to amplify moves, above it long gamma and to
    damp them. It is the one number from this module with a claimed
    directional meaning, so treat it as the hypothesis it is.

    Interpolated linearly between the two strikes that straddle the crossing,
    because the true level almost never falls exactly on a listed strike and
    reporting the nearer strike would quantise it to the strike spacing.

    None when the cumulative total never changes sign — a chain that is long
    or short gamma throughout has no flip, which is a real market state and
    not a failure.
    """
    if gex_df.empty or "net_gex" not in gex_df.columns:
        return None

    cum = gex_df["net_gex"].cumsum().to_numpy()
    strikes = gex_df["strike"].to_numpy()

    for i in range(1, len(cum)):
        lo, hi = cum[i - 1], cum[i]
        if lo == 0.0:
            return float(strikes[i - 1])
        if (lo < 0) != (hi < 0):
            if hi == lo:                      # cannot happen with a sign change
                return float(strikes[i])      # pragma: no cover
            frac = -lo / (hi - lo)
            return float(strikes[i - 1] + frac * (strikes[i] - strikes[i - 1]))

    return None


def summary(gex_df: pd.DataFrame) -> dict:
    """The headline figures above the chart.

    Every value is None when it cannot be computed, never 0 — a chain with no
    gamma and a chain that is perfectly balanced are different states, and a
    zero shown for both is the "missing price -> blank, not 0" rule broken.

    THE DEFINITIONS ARE OURS AND ARE WRITTEN DOWN HERE. Vendors publish
    figures called "GEX Ratio" and "GEX Sentiment" without publishing the
    arithmetic behind them, so ours will not match theirs and pretending
    otherwise would be the more dishonest choice:

      net_gex      Sum of signed exposure. Positive = call-side dominant
                   under the dealer assumption.
      call_gex     Total call-side exposure (always >= 0).
      put_gex      Total put-side exposure (always >= 0).
      ratio        call_gex / put_gex. None when there is no put gamma at
                   all, because an infinite ratio is not a number to show.
      sentiment    Call share of total exposure, 0-100. 50 is balanced.
      peak_strike  Where absolute exposure is greatest — the strike most
                   likely to act as a magnet or a wall.
      flip_strike  See flip_strike().
    """
    empty = dict(net_gex=None, call_gex=None, put_gex=None, abs_gex=None,
                 ratio=None, sentiment=None, peak_strike=None,
                 peak_side=None, flip_strike=None)
    if gex_df.empty:
        return empty

    call_gex = float(gex_df["call_gex"].sum())
    put_gex = float(gex_df["put_gex"].sum())
    total = call_gex + put_gex
    if total <= 0:
        return empty

    peak_idx = gex_df["abs_gex"].idxmax()
    peak_strike = float(gex_df.loc[peak_idx, "strike"])
    peak_net = float(gex_df.loc[peak_idx, "net_gex"])

    return dict(
        net_gex=float(gex_df["net_gex"].sum()),
        call_gex=call_gex,
        put_gex=put_gex,
        abs_gex=total,
        ratio=(call_gex / put_gex) if put_gex > 0 else None,
        sentiment=100.0 * call_gex / total,
        peak_strike=peak_strike,
        peak_side="Call" if peak_net > 0 else "Put",
        flip_strike=flip_strike(gex_df),
    )
