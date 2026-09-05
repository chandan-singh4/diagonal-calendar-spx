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

# How much of each end of the collected strike range is treated as too close
# to the edge for a gamma flip to be believed. See flip_strike: the running
# total's baseline is set by where collection STOPPED, so a crossing near the
# boundary says more about the record than about the market.
EDGE_GUARD = 0.10

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

    **A TRUNCATED CHAIN MOVES THIS NUMBER, AND ONLY IN ONE DIRECTION.** The
    running total starts at zero at the lowest strike COLLECTED, not at the
    lowest strike that exists. Every put below that point is negative gamma
    left out, so the curve starts too high and the crossing is pushed UP. On
    the ±300 chain the effect is small; on an 0DTE selection, which the
    collector only carries out to about ±100, it is large enough to shove the
    crossing to the top edge of the range — where it was measured at 7821.5
    on a 7620-7830 chain with spot at 7723.66, which is not a market fact
    about 7821.5, it is an artefact of where collection stopped.
    So a crossing landing in the outermost tenth at either end is reported as
    NO FLIP. That is the honest answer: the flip, if there is one, is off the
    edge of what was collected and this data cannot locate it. Better a blank
    than a confident line drawn at the boundary of the record.

    None also when the cumulative total never changes sign — a chain that is
    long or short gamma throughout has no flip, which is a real market state
    and not a failure.
    """
    if gex_df.empty or "net_gex" not in gex_df.columns:
        return None

    cum = cumulative_net(gex_df).to_numpy()
    strikes = gex_df["strike"].to_numpy()

    crossing = None
    for i in range(1, len(cum)):
        lo, hi = cum[i - 1], cum[i]
        if lo == 0.0:
            crossing = float(strikes[i - 1])
            break
        if (lo < 0) != (hi < 0):
            if hi == lo:                      # cannot happen with a sign change
                crossing = float(strikes[i])  # pragma: no cover
            else:
                frac = -lo / (hi - lo)
                crossing = float(strikes[i - 1]
                                 + frac * (strikes[i] - strikes[i - 1]))
            break

    if crossing is None:
        return None

    low, high = float(strikes[0]), float(strikes[-1])
    guard = EDGE_GUARD * (high - low)
    if guard > 0 and not (low + guard <= crossing <= high - guard):
        return None
    return crossing


def summary(gex_df: pd.DataFrame) -> dict:
    """The headline figures above the chart.

    **PASS THE DISPLAYED WINDOW, NOT THE WHOLE CHAIN.** Option Alpha computes
    the ratio and the sentiment over the bars actually on screen, so both move
    when the strike window is narrowed -- "it only includes displayed bars so
    it can be adjusted for only closest to the current price". That is the
    point of them: a ratio over the whole chain is dominated by far strikes
    nobody is hedging. An earlier version of this function deliberately used
    the full selection so the number would not shift under the reader; that
    was the wrong call, and it made our figures disagree with the vendor's for
    a reason no caption could explain away.

    THE DEFINITIONS ARE OPTION ALPHA'S, taken from their published
    documentation rather than reverse-engineered, so ours should agree with
    their screen given the same chain:

      net_gex      Sum of signed exposure -- positive call gex plus negative
                   put gex. 9.6b call and -1b put nets to 8.6b.
      abs_gex      The same two summed as magnitudes: 10.6b. "Total gamma at
                   this strike", regardless of direction.
      ratio        Larger side divided by smaller, SIGNED by which side wins:
                   3b positive against 2b negative is +1.5x (green); 3b
                   negative against 2b positive is -1.5x (red). Not call/put.
      sentiment    The PERCENTAGE OF DISPLAYED STRIKES whose net exposure is
                   positive. A count of bars, not a share of dollars -- "55%
                   of 40 bars nearest the money are positive".
      peak_strike  Where absolute exposure is greatest.
      flip_strike  See flip_strike().

    Every value is None when it cannot be computed, never 0 -- a chain with no
    gamma and a perfectly balanced one are different states, and a zero shown
    for both breaks the blank-not-zero rule the project runs on.
    """
    empty = dict(net_gex=None, call_gex=None, put_gex=None, abs_gex=None,
                 ratio=None, sentiment=None, peak_strike=None,
                 peak_side=None, flip_strike=None, positive_bars=None,
                 total_bars=None)
    if gex_df.empty:
        return empty

    call_gex = float(gex_df["call_gex"].sum())
    put_gex = float(gex_df["put_gex"].sum())
    total = call_gex + put_gex
    if total <= 0:
        return empty

    net = gex_df["net_gex"]
    positive = float(net[net > 0].sum())
    negative = float(-net[net < 0].sum())      # as a positive magnitude

    if positive > 0 and negative > 0:
        ratio = (positive / negative) if positive >= negative else -(negative / positive)
    else:
        # One side is entirely absent. An infinite ratio is not a number to
        # put on screen, so the honest answer is that there isn't one.
        ratio = None

    total_bars = int(len(gex_df))
    positive_bars = int((net > 0).sum())

    peak_idx = gex_df["abs_gex"].idxmax()
    peak_strike = float(gex_df.loc[peak_idx, "strike"])
    peak_net = float(gex_df.loc[peak_idx, "net_gex"])

    return dict(
        net_gex=float(net.sum()),
        call_gex=call_gex,
        put_gex=put_gex,
        abs_gex=total,
        ratio=ratio,
        sentiment=100.0 * positive_bars / total_bars,
        positive_bars=positive_bars,
        total_bars=total_bars,
        peak_strike=peak_strike,
        peak_side="Call" if peak_net > 0 else "Put",
        flip_strike=flip_strike(gex_df),
    )


def cumulative_net(gex_df: pd.DataFrame) -> pd.Series:
    """Running total of net exposure from the lowest strike upward.

    The curve whose zero crossing IS `flip_strike` — literally: that function
    calls this one, so there is a single definition of the running total
    rather than a cumsum here and an identical cumsum there that could drift.
    The chart that drew this curve was removed; the definition stays because
    the flip is computed from it.

    Returned as a Series aligned to the frame so a caller can plot it against
    `strike` without another groupby.
    """
    if gex_df.empty:
        return pd.Series(dtype="float64")
    return gex_df["net_gex"].cumsum()


def dollar_scale(spot: float) -> float:
    """The multiplier turning a summed `gamma x open_interest` into dollars.

    Split out because the intraday reads aggregate `gamma x open_interest` in
    SQL — where each snapshot's spot price is a column, not a constant — and
    the scaling must still be the ONE definition in this module rather than a
    second copy embedded in a query.
    """
    return SHARES_PER_CONTRACT * (spot ** 2) * ONE_PERCENT


def dex_by_strike(chain_df: pd.DataFrame, spot: float,
                  *, expiry: str | None = None) -> pd.DataFrame:
    """Delta exposure per strike — dollars of stock behind the open interest.

    Where gamma exposure says how much dealers will be FORCED to trade as SPX
    moves, delta exposure says how much they must hold RIGHT NOW. The two
    disagree usefully: a strike can carry enormous gamma and almost no delta
    (at the money, near expiry) or the reverse (deep in the money).

    **THE DEALER SIGN IS DELIBERATELY NOT APPLIED HERE, unlike GEX.** A put's
    delta is already negative — that is what delta means — so multiplying by
    another -1 would double-count the direction and turn every put into a
    positive contribution. Gamma is positive for both calls and puts, which is
    why it needs the convention imposed and delta does not. Getting this wrong
    is silent: the numbers stay plausible and the sign of the answer inverts.

    So `net_dex` here is the CHAIN's net delta, not an inferred dealer
    inventory. It is a description of what is listed, which is the more honest
    thing to draw and the one that needs no assumption to be true.

    Unit: delta x open_interest x 100 x spot, i.e. dollars of underlying.
    """
    blank = pd.DataFrame({c: pd.Series(dtype="float64") for c in
                          ("strike", "call_dex", "put_dex", "net_dex", "abs_dex")})
    if chain_df is None or chain_df.empty:
        return blank
    if not {"strike", "right", "delta"}.issubset(chain_df.columns):
        return blank

    work = chain_df
    if expiry is not None:
        if "expiry" not in work.columns:
            return blank
        work = work[work["expiry"] == expiry]

    work = work[work["delta"].notna()].copy()
    if work.empty:
        return blank

    oi = (pd.to_numeric(work.get("open_interest"), errors="coerce").fillna(0.0)
          if "open_interest" in work.columns else 0.0)
    work["dex"] = work["delta"] * oi * SHARES_PER_CONTRACT * spot

    is_call = work["right"] == "C"
    out = pd.DataFrame({
        "call_dex": work["dex"].where(is_call, 0.0),
        "put_dex": work["dex"].where(work["right"] == "P", 0.0),
        "net_dex": work["dex"],
        "strike": work["strike"],
    }).groupby("strike", as_index=False).sum()
    out["abs_dex"] = out["call_dex"].abs() + out["put_dex"].abs()
    return out.sort_values("strike", ignore_index=True)


def oi_change(today: pd.DataFrame, prior: pd.DataFrame) -> pd.DataFrame:
    """Today's open interest per strike minus the previous session's.

    Open interest is republished once a day, overnight, so this difference is
    the number of contracts actually OPENED (positive) or CLOSED (negative) —
    about as close to a direct reading of new positioning as this data gets.
    Within a session it does not move, which is why both frames must come from
    DIFFERENT sessions for the answer to mean anything.

    A strike present today and absent yesterday is genuinely new and its whole
    open interest is the change; a strike that has gone is dropped rather than
    reported as a collapse to zero, because "not listed" and "listed at zero"
    are different and only one of them is a closing.
    """
    cols = ("strike", "call_oi_change", "put_oi_change", "net_oi_change")
    if today is None or today.empty:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})

    left = today[["strike", "call_oi", "put_oi"]].copy()
    if prior is None or prior.empty:
        merged = left.assign(call_oi_prior=0.0, put_oi_prior=0.0)
    else:
        merged = left.merge(
            prior[["strike", "call_oi", "put_oi"]].rename(
                columns={"call_oi": "call_oi_prior", "put_oi": "put_oi_prior"}),
            on="strike", how="left",
        ).fillna({"call_oi_prior": 0.0, "put_oi_prior": 0.0})

    merged["call_oi_change"] = merged["call_oi"] - merged["call_oi_prior"]
    merged["put_oi_change"] = merged["put_oi"] - merged["put_oi_prior"]
    merged["net_oi_change"] = merged["call_oi_change"] - merged["put_oi_change"]
    return merged.sort_values("strike", ignore_index=True)[list(cols)]


def key_strikes(gex_df: pd.DataFrame, count: int = 6) -> list[float]:
    """The strikes carrying the most absolute exposure, largest first.

    What the 0DTE flow chart draws a line for. Chosen by absolute rather than
    net exposure on purpose: a strike where calls and puts nearly cancel has a
    net near zero and is often exactly the level being fought over.
    """
    if gex_df.empty or count <= 0:
        return []
    top = gex_df.nlargest(min(count, len(gex_df)), "abs_gex")
    return [float(s) for s in top["strike"]]


def net_flow_by_strike(intraday: pd.DataFrame,
                       *, top: int | None = None) -> pd.DataFrame:
    """How much net gamma exposure each strike GAINED since the open.

    Per-strike net GEX now, minus the same figure at the session's first
    snapshot. Positive means dealers are longer gamma at that strike than they
    were at the open — supply that damps movement through it; negative means
    the opposite, and a strike that has flipped negative during the day is one
    the market can now accelerate through.

    THE SUBTRACTION IS THE POINT. Net GEX alone says what the board looks
    like, and the board is mostly yesterday's positions; the DIFFERENCE is
    what today did. A strike can carry enormous exposure and have seen no
    trade at all, and only this column tells the two apart.

    Each snapshot is scaled by ITS OWN spot price, not the latest, because
    dollar_scale is quadratic in spot: scaling the open by the current price
    would fold the index's move into a figure that is supposed to isolate flow.

    `top` keeps only the largest absolute movers. Returned sorted by strike so
    a horizontal bar chart reads as a price ladder.
    """
    needed = {"strike", "timestamp", "call_gamma_oi", "put_gamma_oi",
              "underlying_price"}
    if intraday is None or intraday.empty or not needed.issubset(intraday.columns):
        return pd.DataFrame(columns=["strike", "open_gex", "now_gex", "flow"])

    work = intraday.copy()
    scale = work["underlying_price"].map(dollar_scale)
    work["net_gex"] = (work["call_gamma_oi"] - work["put_gamma_oi"]) * scale

    first, last = work["timestamp"].min(), work["timestamp"].max()
    if first == last:
        # One snapshot is not a change. Reporting the level as the flow would
        # claim the whole board traded in the first five minutes.
        return pd.DataFrame(columns=["strike", "open_gex", "now_gex", "flow"])

    at_open = work[work["timestamp"] == first].groupby("strike")["net_gex"].sum()
    at_now = work[work["timestamp"] == last].groupby("strike")["net_gex"].sum()

    # Outer, then fill: a strike listed only now was zero at the open, and one
    # that has since gone unquoted has not therefore returned to zero.
    frame = pd.DataFrame({"open_gex": at_open, "now_gex": at_now})
    frame["open_gex"] = frame["open_gex"].fillna(0.0)
    frame["now_gex"] = frame["now_gex"].fillna(0.0)
    frame["flow"] = frame["now_gex"] - frame["open_gex"]
    frame = frame.reset_index()

    if top is not None:
        frame = frame.assign(mag=frame["flow"].abs()).nlargest(top, "mag")
        frame = frame.drop(columns=["mag"])
    return frame.sort_values("strike").reset_index(drop=True)
