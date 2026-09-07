"""Each strike's session high and low, for EVERY measure on the Gamma tab.

WHAT A WICK IS FOR. The bar is one instant. A reader looking at a tall bar
cannot tell whether it has stood there since the open or arrived in the last
ten minutes, and the wick answers that without a second chart -- which is why
Chandan asked for them, and why on 2026-09-06 he asked for the same thing on
the other five panels: "I want that same wick to be present in all the six
chart, meaning net gamma, then delta exposure, vanna, charm, all the six of
it."

WHY THIS EXISTS ALONGSIDE gex.session_range_by_strike. That function answers
for gamma alone, and it does so from a frame the DATABASE has already summed
-- gamma x open interest, per snapshot, per strike. That shape cannot answer
for the rest. Vanna and charm are not stored and cannot be summed before they
are computed: they are Black-Scholes derivatives of each CONTRACT's own
strike, expiry and implied vol, so a strike's vanna is not a function of any
per-strike total. Delta and volume-weighted gamma could be summed in SQL, but
each would then carry its weighting rule in SQL while core/gex.py carries the
same rule for the bars -- two copies, drifting.

SO THE MEASURE'S OWN FUNCTION IS CALLED, ONCE PER SNAPSHOT. Whatever
`gex.by_strike` does to draw the bar is what happens here to draw the wick
behind it, including the parts that are easy to get wrong in a second
implementation:

  - vanna and charm impose the dealer long-calls/short-puts sign, because a
    call and a put at one strike have the SAME vanna and the number carries no
    side of its own;
  - delta deliberately does NOT, because a put's delta is already negative and
    imposing it again would turn every put into a positive contribution;
  - gamma is scaled quadratically in spot and the delta derivatives linearly;
  - each snapshot is scaled by ITS OWN spot, so a trending day does not widen
    every wick on the board.

None of that is restated here. It cannot drift, because it is not copied.

THE COST IS REAL AND IS THE PRICE OF THAT. A session is ~128 snapshots of
~3,200 rows, so this walks ~410,000 rows where the gamma-only path walked
13,000 pre-summed ones -- a few seconds against half of one. The endpoint
caches the answer and the tab draws its bars first and its wicks when they
arrive, which is the arrangement `/strikes/session-range` was built with
already.

PURE, AND HANDED EVERYTHING -- no config, no clock, no database. `r`, `q` and
`display_tz` arrive as arguments for the reason every other core module gives:
they are deployment decisions, and a module that reads config cannot be tested
against another value.
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from core import gex

#: The columns every measure's range comes back in, whatever it measures.
#: ONE SHAPE FOR ALL SIX PANELS: the browser draws a wick from a low and a
#: high and does not care which Greek produced them, so renaming these per
#: measure would buy nothing and cost a branch in the drawing code.
COLUMNS = ["strike", "call_low", "call_high", "put_low", "put_high",
           "net_low", "net_high"]

#: measure -> (the function that draws that measure's bars, its three columns).
#:
#: THE KEYS ARE THE ENDPOINT'S `measure` VALUES, deliberately the same strings
#: `/mission/gamma` already takes. A separate vocabulary for the wicks would
#: be a second mapping to keep in step, and the first time it fell behind the
#: tab would draw one measure's bars over another's range.
_MEASURES: dict[str, tuple[str, str, str]] = {
    "gamma": ("call_gex", "put_gex", "net_gex"),
    "vgex": ("call_gex", "put_gex", "net_gex"),
    "delta": ("call_dex", "put_dex", "net_dex"),
    "vanna": ("call_vex", "put_vex", "net_vex"),
    "charm": ("call_cex", "put_cex", "net_cex"),
}

MEASURES: tuple[str, ...] = tuple(_MEASURES)


def _empty() -> pd.DataFrame:
    """An empty frame WITH COLUMNS. A bare DataFrame() here becomes a KeyError
    in the serializer, and a session with no snapshots yet is a real state."""
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS})


def _bars_for(measure: str, snapshot: pd.DataFrame, spot: float,
              expiry: gex.ExpiryScope, r: float, q: float,
              display_tz: str) -> pd.DataFrame:
    """One snapshot's bars, drawn by the measure's own function.

    THE ARGUMENTS DIFFER PER MEASURE and that is why this is a function rather
    than a dict of callables: vanna and charm need the rate, the yield and how
    much of the trading day is left, and the first two panels differ from each
    other only by a `weight`. Flattening them into one signature would mean
    passing charm's day fraction to gamma, which does not take one.
    """
    if measure in ("gamma", "vgex"):
        weight = "volume" if measure == "vgex" else "open_interest"
        return gex.by_strike(snapshot, spot, expiry=expiry, weight=weight)
    if measure == "delta":
        return gex.dex_by_strike(snapshot, spot, expiry=expiry)

    # EACH SNAPSHOT GETS ITS OWN FRACTION OF THE DAY, not the session's. Charm
    # is the rate at which delta decays with time, so the time left is not a
    # constant of the session -- it is what is being differentiated against.
    # Handing every snapshot the same remainder would flatten the one thing
    # this panel exists to show.
    stamp = snapshot["snapshot_timestamp"].iloc[0]
    remainder = gex.day_remainder(stamp, display_tz)
    fn = gex.vanna_by_strike if measure == "vanna" else gex.charm_by_strike
    return fn(snapshot, spot, r=r, q=q, expiry=expiry,
              day_remainder=remainder)


def session_ranges(session_chain: pd.DataFrame, measure: str, *,
                   r: float, q: float, display_tz: str,
                   expiry: gex.ExpiryScope = None) -> pd.DataFrame:
    """Each strike's highest and lowest reading of `measure` so far today.

    `session_chain` is every snapshot of one session's chain in one frame --
    `dataaccess.queries.load_session_chain_df`. It must carry `snapshot_id`
    and `underlying_price`, because the rows are grouped by the first and each
    group is scaled by its own value of the second.

    NOT THE CHANGE SINCE THE OPEN. That is `gex.net_flow_by_strike`'s question
    and a different number: a strike sitting at +500 having been between -800
    and +900 is a different animal from one that has held +400 to +600 all
    day, and the flow figure is identical for both if they end where they
    started.

    THE SESSION IS WHATEVER `session_chain` HOLDS. Scoping to a day is the
    query's job, as it is everywhere else in core/.

    Returns one row per strike, sorted by strike. Empty in, empty out -- with
    columns. An unknown measure raises rather than falling back to gamma: a
    caller handed gamma's range under charm's name would draw a wick from a
    different Greek entirely, at a scale that looks plausible.
    """
    if measure not in _MEASURES:
        raise ValueError(
            f"measure must be one of {', '.join(MEASURES)}; got {measure!r}")
    if session_chain is None or session_chain.empty:
        return _empty()
    if not {"snapshot_id", "underlying_price"}.issubset(session_chain.columns):
        return _empty()

    call_col, put_col, net_col = _MEASURES[measure]

    frames = []
    for _, snapshot in session_chain.groupby("snapshot_id", sort=True):
        spot = snapshot["underlying_price"].iloc[0]
        if pd.isna(spot):
            # MISSING PRICE -> SKIP, NOT ZERO. Every measure here is scaled by
            # spot, so a zero would put that snapshot's whole board at or near
            # nothing and drag every wick down to it.
            continue
        bars = _bars_for(measure, snapshot, float(spot), expiry, r, q,
                         display_tz)
        if bars is not None and not bars.empty:
            frames.append(bars[["strike", call_col, put_col, net_col]])

    if not frames:
        return _empty()

    stacked = pd.concat(frames, ignore_index=True)
    out = stacked.groupby("strike").agg(
        call_low=(call_col, "min"), call_high=(call_col, "max"),
        put_low=(put_col, "min"), put_high=(put_col, "max"),
        net_low=(net_col, "min"), net_high=(net_col, "max"),
    ).reset_index()
    return out.sort_values("strike", ignore_index=True)[COLUMNS]
