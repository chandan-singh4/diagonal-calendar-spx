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
from this data describes the front month and not the market.

That paragraph used to end "so this module offers gamma-flavoured measures
only". It no longer does: `vanna_by_strike` and `charm_by_strike` were added
deliberately, and the limitation above is unchanged and unfixed — what changed
is the judgement about whether a front-month figure is worth having. It is,
because the front month is the whole universe of a diagonal calendar: the
strategy this dashboard exists for never holds anything the truncation drops.
The figure is therefore CORRECT ABOUT THE COLLECTED CHAIN and must never be
captioned as a market-wide one, because it will not agree with a vendor's and
the difference is the data, not the arithmetic. Every caller says so on screen.
Widening collection is still the only thing that would make it market-wide.

PURE. No database, no config, no AMBIENT clock, no Streamlit — everything
arrives as an argument, so the whole thing is testable without a broker.

That word "ambient" is doing work. `day_remainder` below parses a timestamp
and a timezone name, both HANDED IN; what this module still refuses to do is
call `datetime.now()` or read `config.DISPLAY_TIMEZONE` for itself. The
difference is the whole point — a function told what time it is can be driven
to a 15:59 boundary by a test, and one that asks cannot.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from datetime import time as dtime
from typing import Union
from zoneinfo import ZoneInfo

import pandas as pd

# The pure analytics core, for the second-order Greeks it alone defines. Both
# modules are framework-free, so this is a sideways import between two leaves
# rather than a layering breach — core/position.py and core/scanner.py already
# do the same.
import iv_engine

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


ExpiryScope = Union[str, Sequence[str], None]


def scope_to(work: pd.DataFrame, expiry: ExpiryScope) -> pd.DataFrame:
    """Narrow a chain to one expiry, or to several.

    ONE STRING AND A LIST OF ONE MEAN THE SAME THING, and both are accepted
    because the two callers genuinely differ: every existing caller passes a
    single display key, while the Gamma tab's expiry board lets a trader tick
    several and asks for their combined exposure. Adding a second parameter
    for the plural case would have left two ways to say the same thing and a
    question over what happens when both are set.

    A str IS a Sequence, so the isinstance check has to come first; without
    it "2026-09-18" would be read as a list of ten characters and match
    nothing, silently, returning an empty chain that looks like a thin day.

    AN EMPTY LIST IS NOT "NO FILTER". Asking for the exposure of no expiries
    is answered with no rows -- the caller deselected everything and an empty
    board is the honest reply. `None` is the one value that means "all".
    """
    if isinstance(expiry, str):
        return work[work["expiry"] == expiry]
    return work[work["expiry"].isin(list(expiry))]


def by_expiry(chain_df: pd.DataFrame, spot: float) -> pd.DataFrame:
    """Call and put gamma exposure TOTALLED PER EXPIRY, one row each.

    WHY THIS IS NOT TWENTY CALLS TO `by_strike`. It could be, and the answer
    would match; but the expiry board asks the question for every contract in
    the chain at once, and twenty filtered passes over the same frame is
    twenty times the work for one screen that redraws on every snapshot. One
    groupby says the same thing once.

    THE SCALE IS by_strike's, DELIBERATELY. Both weight gamma by open interest
    and by the same notional (`SHARES_PER_CONTRACT * spot**2 * ONE_PERCENT`),
    so a board figure and the sum of that expiry's bars are the SAME number
    and can be checked against each other. Two scales would have made the
    board decorative.

    `put_gex` comes back POSITIVE, as it does everywhere else in this module;
    which side of the axis it is drawn on is the view's business, and the
    "Call vs Put" panel and the expiry board happen to make the same choice.

    Rows missing gamma are dropped and missing open interest is a real zero --
    the same asymmetry `by_strike` documents at length, for the same reason.
    Empty in, empty out, with columns.
    """
    cols = ["expiry", "call_gex", "put_gex", "net_gex"]
    if chain_df is None or chain_df.empty:
        return pd.DataFrame(columns=cols)
    if not {"expiry", "right", "gamma"}.issubset(chain_df.columns):
        return pd.DataFrame(columns=cols)

    work = chain_df[chain_df["gamma"].notna()].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)

    oi = (pd.to_numeric(work.get("open_interest"), errors="coerce").fillna(0.0)
          if "open_interest" in work.columns else 0.0)
    gex_val = work["gamma"] * oi * (SHARES_PER_CONTRACT * (spot ** 2) * ONE_PERCENT)
    sign = work["right"].map(DEALER_SIGN).fillna(0)

    is_call = work["right"] == "C"
    return pd.DataFrame({
        "expiry": work["expiry"],
        "call_gex": gex_val.where(is_call, 0.0),
        "put_gex": gex_val.where(work["right"] == "P", 0.0),
        "net_gex": gex_val * sign,
    }).groupby("expiry", as_index=False).sum()


#: Which column gamma is weighted by. Open interest is the INSTALLED
#: structure -- every contract still open, however long ago it was written.
#: Volume is TODAY'S FLOW -- what has actually traded this session, and
#: nothing else. Same arithmetic, two different questions, which is exactly
#: why vGEX is this module's `by_strike` with one word changed rather than a
#: second function that would drift from it.
WEIGHTS = {"gex": "open_interest", "vgex": "volume"}


def by_strike(chain_df: pd.DataFrame, spot: float,
              *, expiry: ExpiryScope = None,
              weight: str = "open_interest") -> pd.DataFrame:
    """Gamma exposure, open interest and volume per strike.

    `weight` is what gamma is multiplied by: "open_interest" gives GEX, the
    installed dealer structure, and "volume" gives vGEX, the same measure over
    TODAY'S traded flow alone (Chandan, 2026-09-06). The columns are named
    `call_gex`/`put_gex`/`net_gex` under both, deliberately: `summary`,
    `flip_strike`, `cumulative_net` and the tab's whole strike panel all read
    those names, and vGEX is the same quantity over a different weight rather
    than a different quantity. The CALLER says which it asked for; nothing
    downstream needs to.

    THE SCALE IS OURS, NOT THE VENDOR CARD'S. A published vGEX definition
    reads "gamma x volume x 100"; this multiplies by
    `SHARES_PER_CONTRACT * spot**2 * ONE_PERCENT` because that is what GEX is
    scaled by everywhere else here, and the entire use of vGEX is to be read
    BESIDE GEX -- divergence between them is the signal. Two scales would make
    the comparison meaningless while looking fine.

    VOLUME-WEIGHTED IS EMPTY BEFORE THE OPEN and small early in the session.
    That is the measure behaving correctly, not a fault: no trades yet means
    no flow yet. A near-flat vGEX panel at 09:31 is the honest picture.

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
        work = scope_to(work, expiry)

    work = work[work["gamma"].notna()].copy()
    if work.empty:
        return _blank()

    for col in ("open_interest", "volume"):
        work[col] = (pd.to_numeric(work.get(col), errors="coerce").fillna(0.0)
                     if col in work.columns else 0.0)

    scale = SHARES_PER_CONTRACT * (spot ** 2) * ONE_PERCENT
    work["gex"] = work["gamma"] * work[weight] * scale
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


def flow_ratio(gex_df: pd.DataFrame) -> float | None:
    """Share of net exposure sitting on the POSITIVE side: sum(V>0) / sum(|V|).

    The published vGEX definition Chandan brought on 2026-09-06 --
    "vGEX Ratio = sum positive V(K) / sum |V(K)|" -- and a DIFFERENT NUMBER
    from `summary()["ratio"]`, which is the larger side over the smaller,
    signed. Both are kept because they answer different questions: this one is
    bounded in [0, 1] and reads as "what fraction of today's gamma flow is
    positive", while the other is unbounded and reads as "how lopsided". A
    single field would have had to pick one and mislabel it.

    None rather than 0 when there is no exposure at all -- before the open,
    volume-weighted exposure is genuinely absent, and 0.0 would read as
    "entirely negative", which is a claim about a session that has not
    started.
    """
    if gex_df is None or gex_df.empty or "net_gex" not in gex_df.columns:
        return None
    net = gex_df["net_gex"]
    total = float(net.abs().sum())
    if total <= 0:
        return None
    return float(net[net > 0].sum()) / total


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
                  *, expiry: ExpiryScope = None) -> pd.DataFrame:
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
        work = scope_to(work, expiry)

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


def session_range_by_strike(intraday: pd.DataFrame) -> pd.DataFrame:
    """Each strike's HIGHEST and LOWEST call and put exposure so far today.

    THE "WICKS" Chandan asked for on 2026-09-06, after gexstream.com, whose
    own documentation defines them as the "per-strike session high / low for
    GEX, DEX and vGEX, tracked since the 4:00 PM ET reset". Worth stating
    plainly because the obvious reading is the wrong one: a wick is NOT the
    change since the open. `net_flow_by_strike` above is the change. This is
    the RANGE the strike has travelled through, and the two answer different
    questions -- a strike sitting at +500 having been between -800 and +900 is
    a different animal from one that has held +400 to +600 all day, and the
    flow figure is the same for both if they end where they started.

    WHY IT EARNS ITS PLACE ON THE BAR. The bar is one instant. A reader
    looking at a tall bar cannot tell whether it has stood there since the
    open or arrived in the last ten minutes, and the wick answers that without
    a second chart -- which is exactly why Chandan wanted it.

    EACH SNAPSHOT IS SCALED BY ITS OWN SPOT, for the reason `net_flow_by_strike`
    gives: `dollar_scale` is quadratic in spot, so scaling this morning's
    reading by this afternoon's price would fold the index's own move into the
    range and widen every wick on a trending day.

    THE SESSION IS WHATEVER `intraday` HOLDS. Scoping to a session is the
    caller's job (`load_intraday_strike_metrics` takes a session date), so
    "since the 4pm reset" is enforced by the query, not re-derived here.

    Returns one row per strike, sorted by strike so a horizontal ladder reads
    top to bottom. Empty in, empty out -- with columns.
    """
    cols = ["strike", "call_low", "call_high", "put_low", "put_high",
            "net_low", "net_high"]
    needed = {"strike", "timestamp", "call_gamma_oi", "put_gamma_oi",
              "underlying_price"}
    if intraday is None or intraday.empty or not needed.issubset(intraday.columns):
        return pd.DataFrame(columns=cols)

    work = intraday.copy()
    scale = work["underlying_price"].map(dollar_scale)
    work["call_gex"] = work["call_gamma_oi"] * scale
    work["put_gex"] = work["put_gamma_oi"] * scale
    work["net_gex"] = work["call_gex"] - work["put_gex"]

    # SUM WITHIN A SNAPSHOT BEFORE TAKING THE RANGE. A strike lists several
    # expiries, and the panel draws their total; taking min/max over the
    # unaggregated rows would give the range of a single expiry's exposure
    # and label it as the strike's.
    per_snapshot = (work.groupby(["timestamp", "strike"], as_index=False)
                        [["call_gex", "put_gex", "net_gex"]].sum())

    out = per_snapshot.groupby("strike").agg(
        call_low=("call_gex", "min"), call_high=("call_gex", "max"),
        put_low=("put_gex", "min"), put_high=("put_gex", "max"),
        net_low=("net_gex", "min"), net_high=("net_gex", "max"),
    ).reset_index()
    return out.sort_values("strike", ignore_index=True)[cols]


def replay_by_strike(intraday: pd.DataFrame,
                     *, strikes: int | None = None) -> pd.DataFrame:
    """Every snapshot of the session as a level AND a change since the open.

    THE TIME MACHINE'S DATA, and the reason it is one function rather than two.
    Chandan's requirement for the replay was that the level chart and the
    added/removed chart agree — "when gex adds then I'll see in the GEX chart
    as well". Two functions computing net GEX from the same rows would agree
    today and drift the first time one of them was touched; here `flow` is
    literally `net_gex` minus the same column at the open, so the two panels
    cannot disagree without the subtraction itself being wrong.

    Returned LONG — one row per (timestamp, strike) — because the caller turns
    it into animation frames and a wide pivot would have to be unpivoted again.

    EVERY FRAME CARRIES EVERY STRIKE. An animation whose bars appear and
    vanish between frames reads as the market moving when it is only the
    quote list changing, and Plotly matches bars across frames by position,
    so a shorter array in one frame silently repaints the wrong strikes. The
    grid is therefore the union of strikes seen all session.

    A strike not quoted in a given snapshot is left BLANK, not zero — the
    project's standing rule, and here the difference is visible: zero would
    draw a bar saying "no gamma at this level", blank draws nothing and says
    "not quoted". `flow` is the deliberate exception, matching
    net_flow_by_strike: a strike absent at the OPEN was genuinely carrying
    nothing then, so its change is measured from zero.

    Each snapshot is scaled by ITS OWN spot, for the reason given in
    net_flow_by_strike: dollar_scale is quadratic in spot, so using the latest
    price throughout would fold the index's own move into the flow.

    `strikes` keeps only the N with the largest absolute flow at any point in
    the day — chosen ACROSS the whole session, not per frame, so the ladder
    holds still while it plays.
    """
    needed = {"strike", "timestamp", "call_gamma_oi", "put_gamma_oi",
              "underlying_price"}
    columns = ["timestamp", "strike", "net_gex", "flow", "underlying_price"]
    if intraday is None or intraday.empty or not needed.issubset(intraday.columns):
        return pd.DataFrame(columns=columns)

    work = intraday.copy()
    work["net_gex"] = ((work["call_gamma_oi"] - work["put_gamma_oi"])
                       * work["underlying_price"].map(dollar_scale))

    grid = (work.groupby(["timestamp", "strike"])
                .agg(net_gex=("net_gex", "sum"),
                     underlying_price=("underlying_price", "first"))
                .unstack("strike"))
    levels = grid["net_gex"]
    # Spot is a property of the snapshot, not of a strike, so it survives the
    # reindex that leaves unquoted strikes blank.
    spot = grid["underlying_price"].bfill(axis=1).ffill(axis=1).iloc[:, 0]

    at_open = levels.iloc[0].fillna(0.0)
    flow = levels.fillna(0.0).sub(at_open, axis=1)

    if strikes is not None and not flow.empty:
        keep = flow.abs().max(axis=0).nlargest(strikes).index
        levels, flow = levels[sorted(keep)], flow[sorted(keep)]

    out = (levels.stack(future_stack=True).rename("net_gex").to_frame()
           .join(flow.stack(future_stack=True).rename("flow"))
           .reset_index())
    out["underlying_price"] = out["timestamp"].map(spot)
    return out.sort_values(["timestamp", "strike"], ignore_index=True)[columns]


# ─────────────────────────────────────────────────────────────────────────────
# Second-order exposure — Vanna and Charm
#
# READ THE LIMITATION IN THE MODULE DOCSTRING FIRST. Vega, and therefore
# vanna, lives largely in long-dated options and this record stops at ~28
# days. What follows is a FRONT-MONTH figure and must be captioned as one; it
# is not the market-wide "VEX" a vendor publishes and will not agree with one.
#
# It earns its place anyway because the front month is the entire universe of
# a diagonal calendar. The question this answers — how does dealer hedging
# pressure at these strikes change when IV moves, and how does it drift
# overnight — is a question about exactly the contracts collected.
# ─────────────────────────────────────────────────────────────────────────────

# Calendar days in the year fraction the Black-Scholes formulas want.
DAYS_PER_YEAR = 365.0

VANNA_COLUMNS = ["strike", "call_vex", "put_vex", "net_vex", "abs_vex"]
CHARM_COLUMNS = ["strike", "call_cex", "put_cex", "net_cex", "abs_cex"]


# SPX p.m. contracts settle on the 4 p.m. close. The a.m. monthly settles on
# the OPEN instead, which this constant does not know about — see
# day_remainder for why that is left alone rather than half-handled.
SETTLE_AT = dtime(16, 0)


def day_remainder(snapshot_ts: str, display_tz: str) -> float:
    """How much of a 24-hour day is left between this snapshot and the close.

    MOVED HERE FROM views/gex.py ON 2026-09-06, and the move is the point: it
    was a private function on the page, so `/mission/gamma` could not serve
    charm without either importing a Streamlit view into a server or keeping a
    second copy of this arithmetic. That is the same fault DEBT-041 was opened
    for, one layer down, and a second copy here would be worse than most —
    the two screens would disagree only on the last day of an expiry, only in
    the afternoon, and by an amount that looks like a market move.

    WHY CHARM NEEDS THIS AND NOTHING ELSE DOES. `dte` is a whole number of
    days. Vanna is well-behaved as expiry approaches, so rounding time to the
    nearest day barely moves it. Charm carries a 1/(T*sqrt(T)) factor: on the
    last day the difference between "0 days" and "three hours" is the
    difference between a blank column and the largest bar on the chart, and
    0DTE is the expiry this dashboard looks at most.

    Returned as a fraction of a DAY, which is what year_fraction adds to
    `dte`. Clamped to [0, 1]: a snapshot after the close has no day left, and
    one before the open has at most a whole one.

    THE A.M. MONTHLY IS KNOWINGLY WRONG HERE, by up to six and a half hours on
    one expiry a month. Doing better means reading `settlement` per contract
    and returning a different remainder for each — but `settlement` is NULL on
    every row written before 2026-08-19 (db.py), so for most of the record the
    correction would be a guess wearing the costume of a fix. One stated hour
    of error beats an unstated one. Vanna is unaffected either way.

    `display_tz` is passed in rather than read from config, exactly as
    core.charts.to_display_time takes it: this is core/, and core/ is handed
    what it needs (tests/test_layering.py).

    0.0 on an unparseable timestamp or an unknown zone. That is the same
    answer as "the close has passed", which is the conservative one: it makes
    a 0DTE contract's charm fall back on whole days rather than inventing a
    fraction from a value nobody can read.
    """
    try:
        ts = datetime.fromisoformat(snapshot_ts).replace(tzinfo=UTC)
        local = ts.astimezone(ZoneInfo(display_tz))
    except (TypeError, ValueError, KeyError):
        return 0.0
    close = local.replace(hour=SETTLE_AT.hour, minute=SETTLE_AT.minute,
                          second=0, microsecond=0)
    return max(0.0, min(1.0, (close - local).total_seconds() / 86400.0))


def year_fraction(dte: float, day_remainder: float = 0.0) -> float | None:
    """Time to expiry in years, from whole days plus the rest of today.

    WHY THE SECOND ARGUMENT EXISTS. `option_rows.dte` is a whole number of
    calendar days, and charm carries a 1/(T*sqrt(T)) factor that is unbounded
    as T goes to zero — so a 0DTE contract computed from `dte` alone would be
    handed T = 0 and vanish, and a 1DTE one would be told it has a full extra
    day. `day_remainder` is the fraction of a 24-hour day between the snapshot
    and the expiry instant. `day_remainder()` below works it out; this function
    only adds it, so a caller with a better figure can pass one instead.

    A CAVEAT THE CALLER CANNOT FIX FROM HERE. The expiry instant differs by
    contract: SPX's third-Friday monthly settles at the OPEN and the weekly at
    the CLOSE, and `settlement` is NULL for every row written before
    2026-08-19 (see db.py). On those older rows a third-Friday `day_remainder`
    is a guess, and charm — not vanna — is where that shows.

    None when the result is not positive: an expired contract has no time
    value left to differentiate, and a zero would be a lie dressed as data.
    """
    if dte is None:
        return None
    total = float(dte) + float(day_remainder)
    return total / DAYS_PER_YEAR if total > 0 else None


def _second_order_frame(chain_df: pd.DataFrame, spot: float,
                        expiry: ExpiryScope, columns: list[str]):
    """The setup every second-order measure repeats: filter, check, weight.

    Returns (work, blank) where `work` is None if there is nothing to compute.
    Split out because vanna and charm differ only in which per-contract number
    they multiply by, and a second copy of the filtering would be a second
    place for the `iv`-in-percent convention to be got wrong.
    """
    blank = pd.DataFrame({c: pd.Series(dtype="float64") for c in columns})
    if chain_df is None or chain_df.empty:
        return None, blank
    if not {"strike", "right", "iv", "dte"}.issubset(chain_df.columns):
        return None, blank

    work = chain_df
    if expiry is not None:
        if "expiry" not in work.columns:
            return None, blank
        work = scope_to(work, expiry)

    work = work[work["iv"].notna() & work["dte"].notna()].copy()
    if work.empty:
        return None, blank

    work["_oi"] = (pd.to_numeric(work.get("open_interest"), errors="coerce")
                   .fillna(0.0) if "open_interest" in work.columns else 0.0)
    # Dollars of underlying behind one contract. Both measures are DELTA
    # derivatives, so both scale linearly in spot — unlike GEX, which is
    # quadratic because gamma is itself a delta derivative in the same
    # variable. Using dollar_scale here would be wrong by a factor of spot/100.
    work["_notional"] = work["_oi"] * SHARES_PER_CONTRACT * spot
    return work, blank


def vanna_by_strike(chain_df: pd.DataFrame, spot: float, *,
                    r: float, q: float, expiry: ExpiryScope = None,
                    day_remainder: float = 0.0) -> pd.DataFrame:
    """Vanna exposure per strike — dollars of delta created per vol point.

    WHAT IT MEANS. Gamma exposure says how much dealers must trade as SPX
    MOVES. Vanna exposure says how much they must trade when IMPLIED
    VOLATILITY moves and SPX does not. The two are routinely confused and are
    different hedging pressures: a vol-crush morning with a flat index still
    forces flow, and this is the column that shows it.

    THE DEALER SIGN CONVENTION IS APPLIED HERE, exactly as it is for gamma and
    exactly as it is NOT for delta — and the reason is a fact rather than a
    preference. A call and a put at the same strike and expiry have the SAME
    vanna: put-call parity fixes their delta difference at e^(-qT), which
    contains no sigma, so it differentiates away (iv_engine.vanna). The number
    therefore carries no side of its own, so the long-calls/short-puts
    convention has to be imposed for `net_vex` to mean anything directional.
    Compare dex_by_strike, where delta arrives already signed and imposing it
    again would invert every put.

    Unit: vanna x open_interest x 100 x spot, i.e. dollars of delta per one
    point of IV. Positive net means dealers get LONGER delta as vol rises.

    `r` and `q` are required rather than defaulted: they are the assumption
    this whole measure rests on (config.RISK_FREE_RATE, config.DIVIDEND_YIELD)
    and a default here would hide it at every call site.
    """
    work, blank = _second_order_frame(chain_df, spot, expiry, VANNA_COLUMNS)
    if work is None:
        return blank

    work["_vanna"] = [
        iv_engine.vanna(spot, float(k), year_fraction(d, day_remainder),
                        float(v), r, q)
        for k, d, v in zip(work["strike"], work["dte"], work["iv"], strict=True)
    ]
    work = work[work["_vanna"].notna()]
    if work.empty:
        return blank

    work["vex"] = work["_vanna"] * work["_notional"]
    work["sign"] = work["right"].map(DEALER_SIGN).fillna(0)

    is_call = work["right"] == "C"
    out = pd.DataFrame({
        "call_vex": work["vex"].where(is_call, 0.0),
        "put_vex": work["vex"].where(work["right"] == "P", 0.0),
        "net_vex": work["vex"] * work["sign"],
        "strike": work["strike"],
    }).groupby("strike", as_index=False).sum()
    out["abs_vex"] = out["call_vex"].abs() + out["put_vex"].abs()
    return out.sort_values("strike", ignore_index=True)[VANNA_COLUMNS]


def charm_by_strike(chain_df: pd.DataFrame, spot: float, *,
                    r: float, q: float, expiry: ExpiryScope = None,
                    day_remainder: float = 0.0) -> pd.DataFrame:
    """Charm exposure per strike — dollars of delta that decay away per day.

    WHAT IT MEANS. The hedge a dealer put on today stops being the right hedge
    tomorrow even if nothing happens, because every option's delta drifts as
    expiry approaches: in-the-money contracts converge on 1.00, out-of-the-
    money ones on zero. Charm exposure is the size of that drift in dollars —
    the flow that has to happen overnight and into the following open purely
    because a day passed. It is the cleanest available explanation for the
    "nothing happened but the market drifted" open, and it is largest exactly
    where this collector looks: near the money, in the front expiries.

    THE DEALER SIGN IS DELIBERATELY NOT APPLIED, unlike vanna and gamma, for
    the same reason it is not applied in dex_by_strike: charm is a derivative
    of DELTA with respect to time, and delta is already signed by side. A
    put's charm is genuinely the opposite sign from a call's at the same
    moneyness; multiplying by another -1 would double-count it. Getting this
    wrong is silent — the magnitudes stay plausible and the answer inverts.

    So `net_cex` is the CHAIN's net delta decay, not an inferred dealer
    inventory. That is the description that needs no assumption to be true.

    SIGN READS AS: positive means the chain's net delta will be HIGHER
    tomorrow, matching iv_engine.charm's convention (and theta's direction of
    reading: what the passage of a day does TO you).

    Unit: charm x open_interest x 100 x spot, i.e. dollars of delta per day.
    """
    work, blank = _second_order_frame(chain_df, spot, expiry, CHARM_COLUMNS)
    if work is None:
        return blank

    work["_charm"] = [
        iv_engine.charm(spot, float(k), year_fraction(d, day_remainder),
                        float(v), r, q, str(rt))
        for k, d, v, rt in zip(work["strike"], work["dte"], work["iv"],
                               work["right"], strict=True)
    ]
    work = work[work["_charm"].notna()]
    if work.empty:
        return blank

    work["cex"] = work["_charm"] * work["_notional"]

    is_call = work["right"] == "C"
    out = pd.DataFrame({
        "call_cex": work["cex"].where(is_call, 0.0),
        "put_cex": work["cex"].where(work["right"] == "P", 0.0),
        "net_cex": work["cex"],
        "strike": work["strike"],
    }).groupby("strike", as_index=False).sum()
    out["abs_cex"] = out["call_cex"].abs() + out["put_cex"].abs()
    return out.sort_values("strike", ignore_index=True)[CHARM_COLUMNS]


def second_order_summary(vex_df: pd.DataFrame,
                         cex_df: pd.DataFrame) -> dict:
    """The headline figures for the two second-order panels.

    Deliberately fewer numbers than `summary`. There is no vendor definition
    of a "vanna ratio" or a "charm sentiment" to agree with, and inventing
    ones by analogy would put figures on screen that look like the GEX ones
    beside them and mean something nobody has checked. Totals and the peak
    strike are what the charts actually support.

    Every value is None when it cannot be computed, never 0 — a chain with no
    second-order exposure and a perfectly balanced one are different states.
    """
    out = dict(net_vex=None, abs_vex=None, peak_vex_strike=None,
               net_cex=None, abs_cex=None, peak_cex_strike=None)

    if vex_df is not None and not vex_df.empty:
        out["net_vex"] = float(vex_df["net_vex"].sum())
        out["abs_vex"] = float(vex_df["abs_vex"].sum())
        if out["abs_vex"] > 0:
            out["peak_vex_strike"] = float(
                vex_df.loc[vex_df["abs_vex"].idxmax(), "strike"])

    if cex_df is not None and not cex_df.empty:
        out["net_cex"] = float(cex_df["net_cex"].sum())
        out["abs_cex"] = float(cex_df["abs_cex"].sum())
        if out["abs_cex"] > 0:
            out["peak_cex_strike"] = float(
                cex_df.loc[cex_df["abs_cex"].idxmax(), "strike"])

    return out
