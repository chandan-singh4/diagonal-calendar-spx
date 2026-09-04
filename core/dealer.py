"""core/dealer.py — what a session's flow says about dealer inventory.

TWO QUESTIONS, ONE MODULE. Both charts in the Dealer Positioning section ask
something core/gex.py cannot: gamma exposure describes what is LISTED, and
these describe what was TRADED today and whether it stayed on the books.

  * `bubble_points` spreads today's volume across expiry AND strike at once,
    so 0DTE gamma chasing separates visibly from monthly OPEX hedging instead
    of both landing in one per-strike total.
  * `positioning` reads volume against the overnight change in open interest.
    Volume alone cannot tell a position being opened from one contract
    changing hands forty times; open interest settles it, because it counts
    what still existed at the close.

WHAT IS ASSUMED, PLAINLY. The verdicts are heuristics with thresholds chosen
by convention, not measurement — "high volume" is the 75th percentile of the
strikes on screen, and the ratios are the ones the specification named. They
are named constants and every caller can override them, because a threshold
nobody can move is a threshold nobody can check.

WHAT IS NOT ASSUMED. Nothing here applies a dealer sign. `delta_oi` is the
chain's own change in open interest — contracts opened minus contracts closed
— and which side of them a dealer took is exactly what this data does not
say. The verdict labels describe the FLOW, not who is long it.

**OPEN INTEREST IS PUBLISHED ONCE, OVERNIGHT.** Every delta here is
today-against-yesterday and does not move during the session, so a verdict
computed at 09:35 and again at 15:55 differs only in the volume half. That is
the measure behaving correctly, not a stale read.
"""
from __future__ import annotations

import math
from datetime import date

import pandas as pd

from core import contract

# The strike band drawn around spot. The specification asks for 3% to 5%; 4%
# is the middle of it and about 310 points at SPX 7,700 — comfortably wider
# than the 300 the collector stores, so the band is never the thing hiding a
# strike that was collected.
BAND_PERCENT = 4.0

# Bubble radii in pixels, and the scale between them. Linear radius would
# make a monthly expiry invisible next to 0DTE: on a live session the busiest
# 0DTE strike carries many times the volume of the busiest monthly one, so
# the small end rounds to nothing. Square root maps volume to AREA, which is
# the comparison an eye makes anyway; log compresses harder still, for a
# session where one strike has run away from the rest.
MIN_RADIUS = 4.0
MAX_RADIUS = 22.0

# How much of the board the bubble chart may draw at once. These are not
# cosmetic. The 4% band around SPX 7,700 holds roughly 120 five-point strikes,
# and a panel is some hundreds of pixels tall: every strike drawn at once puts
# three or four pixels between neighbours whose bubbles are twenty across, so
# the columns fuse into solid bars and nothing can be read off them. Keeping
# the busiest strikes per expiry, and the nearest expiries, is what makes the
# chart a chart. What is dropped is always stated in the caption -- silent
# truncation would read as "this is the whole board" when it is not.
TOP_STRIKES_PER_EXPIRY = 8

# Expiries, on the other hand, are cheap. The crowding this filter exists to
# fix was VERTICAL -- a hundred-odd strikes stacked into one column -- and
# capping the columns as well merely cut the term structure short, which is
# the axis the chart is named after. The cap left here is a guard against a
# pathological chain, not a design choice: at 24 columns each still gets
# ~55px, wider than the largest bubble drawn.
MAX_EXPIRIES = 24

# Put/call volume ratio bands. Below 0.7 the flow is call-dominated, above
# 1.3 put-dominated, and between them balanced — which on this chart usually
# means straddles and strangles rather than a genuine standoff.
PCR_CALL_MAX = 0.7
PCR_PUT_MIN = 1.3

# Verdict thresholds, as ratios of the strike's own volume.
CHURN_RATIO = 0.15
ACCUMULATION_RATIO = 0.25
LIQUIDATION_RATIO = 0.20
HIGH_VOLUME_PERCENTILE = 75.0

# A wall is an out-of-the-money strike that GAINED open interest and is the
# largest such gain on its side. 1% is far enough out to exclude the
# at-the-money churn every session produces.
WALL_MONEYNESS = 0.01

CONTRACT_MULTIPLIER = 100

BUBBLE_COLUMNS = ("expiry", "expiry_label", "expiry_order", "strike",
                  "call_volume", "put_volume", "total_volume", "pcr",
                  "notional", "flow", "radius")

VERDICT_COLUMNS = ("strike", "call_volume", "put_volume", "total_volume",
                   "delta_oi", "verdict", "tone")


def _blank(columns) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


# ─────────────────────────────────────────────────────────────────────────────
# Expiry naming — the x axis of the bubble chart
# ─────────────────────────────────────────────────────────────────────────────

def expiry_label(expiry: str, dte: int) -> str:
    """The column heading a trader would use, not an ISO date.

    "0DTE (Today)" and "Monthly OPEX" say what the column IS; "2026-09-18"
    makes the reader work out that it is the third Friday. The distinctions
    worth drawing are the ones that BEHAVE differently: same-day gamma, the
    weekly, and the monthly and quarterly cycles carrying structural hedges.

    Ordinary weeklies keep their date rather than being numbered "2DTE",
    "3DTE" and so on — past tomorrow, the count stops being how anyone refers
    to them.
    """
    day = date.fromisoformat(contract.date_of(expiry))
    monthly = contract.is_third_friday(contract.date_of(expiry))

    if dte == 0:
        return "0DTE (Today)"
    if dte == 1:
        return "1DTE"
    if monthly:
        return "Quarterly" if day.month in (3, 6, 9, 12) else "Monthly OPEX"
    if day.weekday() == 4 and dte <= 7:
        return "W-OPEX (Fri)"
    if day.weekday() == 4 and dte <= 14:
        return "Next Fri"
    return f"{day:%a} {day.day} {day:%b}"


def flow_bucket(pcr) -> str:
    """Which side the volume at this point leaned, by put/call ratio."""
    if pcr is None or pd.isna(pcr):
        return "balanced"
    if pcr < PCR_CALL_MAX:
        return "call"
    if pcr > PCR_PUT_MIN:
        return "put"
    return "balanced"


def radius(volume: float, largest: float, *, scale: str = "sqrt",
           min_radius: float = MIN_RADIUS,
           max_radius: float = MAX_RADIUS) -> float:
    """Bubble radius in pixels for one strike's volume.

    NON-LINEAR ON PURPOSE, for a measurable reason rather than an aesthetic
    one: 0DTE routinely trades an order of magnitude more than the monthly at
    the same strike, so a linear radius puts the monthly under a pixel and
    the chart becomes a single column of dots.

    Everything lands between min_radius and max_radius, so a point is never
    invisible and never swallows its neighbours. Zero volume takes the
    minimum rather than vanishing: a listed strike that traded nothing is a
    fact worth seeing.
    """
    if largest <= 0 or volume <= 0:
        return min_radius
    if scale == "log":
        # log1p keeps log(0) out of it and anchors the smallest bubble at zero.
        share = math.log1p(volume) / math.log1p(largest)
    else:
        share = math.sqrt(min(1.0, volume / largest))
    return min_radius + min(1.0, share) * (max_radius - min_radius)


def most_traded(points: pd.DataFrame, *,
                per_expiry: int = TOP_STRIKES_PER_EXPIRY,
                max_expiries: int = MAX_EXPIRIES) -> pd.DataFrame:
    """The busiest `per_expiry` strikes of the nearest `max_expiries`.

    Expiries are kept by PROXIMITY (expiry_order, which is days to expiry),
    not by volume: the chart's subject is the term structure, and dropping a
    quiet middle expiry would leave a gap that reads as a date with no trade
    rather than as a column that was never drawn. Strikes within an expiry are
    kept by volume, because there the question is where the trade went.

    Radii are NOT recomputed. They are relative to the busiest point on the
    board, and rescaling to the survivors would make an expiry look busier
    simply because its neighbours were dropped.
    """
    if points is None or points.empty:
        return points
    keep = (points[["expiry_label", "expiry_order"]].drop_duplicates()
            .nsmallest(max_expiries, "expiry_order")["expiry_label"])
    trimmed = (points[points["expiry_label"].isin(set(keep))]
               .sort_values("total_volume", ascending=False))
    # rank-then-filter, NOT groupby.apply: apply consumes expiry_label as the
    # grouping key and hands back a frame without it, which the caller needs
    # to draw the columns.
    rank = trimmed.groupby("expiry_label").cumcount()
    return trimmed[rank < per_expiry].reset_index(drop=True)


def bubble_points(chain_df: pd.DataFrame, spot: float, *,
                  band_percent: float = BAND_PERCENT,
                  scale: str = "sqrt",
                  min_radius: float = MIN_RADIUS,
                  max_radius: float = MAX_RADIUS) -> pd.DataFrame:
    """One row per (expiry, strike) inside the band around spot.

    Strikes outside the band are DROPPED, not clamped to the edge: the whole
    claim of this chart is that height is a price, and a clamped point would
    sit at a price nothing traded at.

    `notional` is mark x volume x 100 — the premium that actually changed
    hands, which is what separates a thousand contracts of a five-cent
    lottery ticket from a thousand contracts of a forty-dollar hedge. It
    comes back NaN where the chain carries no mark, so the tooltip can show a
    dash instead of inventing a zero.
    """
    needed = {"expiry", "strike", "right", "volume"}
    if chain_df is None or chain_df.empty or not needed.issubset(chain_df.columns):
        return _blank(BUBBLE_COLUMNS)

    band = spot * band_percent / 100.0
    work = chain_df[(chain_df["strike"] >= spot - band)
                    & (chain_df["strike"] <= spot + band)].copy()
    if work.empty:
        return _blank(BUBBLE_COLUMNS)

    work["volume"] = pd.to_numeric(work["volume"], errors="coerce").fillna(0.0)
    mark = (pd.to_numeric(work["mark"], errors="coerce") if "mark" in work.columns
            else pd.Series(float("nan"), index=work.index))
    is_call = work["right"] == "C"

    grouped = pd.DataFrame({
        "expiry": work["expiry"],
        "strike": work["strike"],
        "call_volume": work["volume"].where(is_call, 0.0),
        "put_volume": work["volume"].where(work["right"] == "P", 0.0),
        "total_volume": work["volume"],
        "notional": mark * work["volume"] * CONTRACT_MULTIPLIER,
    }).groupby(["expiry", "strike"], as_index=False).sum(min_count=1)

    grouped = grouped[grouped["total_volume"] > 0].reset_index(drop=True)
    if grouped.empty:
        return _blank(BUBBLE_COLUMNS)

    # A strike with no call volume has an UNDEFINED ratio, not an infinite
    # one — but left as NaN it buckets as "balanced", which is wrong in the
    # one direction that matters. Put-dominated is stated explicitly.
    calls = grouped["call_volume"].where(grouped["call_volume"] > 0)
    grouped["pcr"] = grouped["put_volume"] / calls
    grouped.loc[(grouped["call_volume"] == 0) & (grouped["put_volume"] > 0),
                "pcr"] = float("inf")
    grouped["flow"] = grouped["pcr"].map(flow_bucket)

    largest = float(grouped["total_volume"].max())
    grouped["radius"] = grouped["total_volume"].map(
        lambda v: radius(v, largest, scale=scale,
                         min_radius=min_radius, max_radius=max_radius))

    dte_by_expiry = (chain_df.groupby("expiry")["dte"].first()
                     if "dte" in chain_df.columns else pd.Series(dtype="int64"))
    dte = grouped["expiry"].map(dte_by_expiry).fillna(0).astype(int)
    grouped["expiry_order"] = dte
    grouped["expiry_label"] = [expiry_label(e, d)
                               for e, d in zip(grouped["expiry"], dte)]

    return grouped.sort_values(["expiry_order", "strike"], ignore_index=True)[
        list(BUBBLE_COLUMNS)]


# ─────────────────────────────────────────────────────────────────────────────
# Volume against the overnight change in open interest
# ─────────────────────────────────────────────────────────────────────────────

def classify(volume: float, delta_oi: float, *, high_volume: bool,
             churn_ratio: float = CHURN_RATIO,
             accumulation_ratio: float = ACCUMULATION_RATIO,
             liquidation_ratio: float = LIQUIDATION_RATIO) -> tuple[str, str]:
    """The verdict for one strike, and the tone to draw it in.

    THE BANDS DO NOT OVERLAP, which is what makes the verdict unambiguous
    rather than an artefact of the order these are tested in. Churn wants
    |ratio| <= 0.15, accumulation wants >= 0.25, liquidation <= -0.20: no
    number satisfies two of them, so no strike can be given two names and
    reordering these branches changes nothing. (An earlier version of this
    docstring claimed the order was load-bearing. It is not — a mutation test
    that swapped the branches left every test passing, which is the evidence.)

    The gap between the churn ceiling and the accumulation floor is real and
    deliberate: 0.15 to 0.25 is more than scalping and less than a build, and
    it gets its own, weaker word rather than being rounded into a neighbour.

    Anything that is none of the named states returns an em dash. A verdict
    on every row would be a verdict on nothing — most strikes in a session
    are ordinary two-way trade, and saying so is the honest default.
    """
    if volume <= 0 or not high_volume:
        return "—", "quiet"
    if delta_oi is None or pd.isna(delta_oi):
        return "—", "quiet"

    ratio = delta_oi / volume
    if ratio >= accumulation_ratio:
        return "Heavy Accumulation", "accumulation"
    if ratio <= -liquidation_ratio:
        return "Position Liquidation", "liquidation"
    if abs(ratio) <= churn_ratio:
        return "Intraday Churn", "churn"
    if ratio > 0:
        # Between the churn ceiling and the accumulation floor: more than
        # scalping, less than a build. The reference calls it opening longs.
        return "Opening Longs", "accumulation"
    return "—", "quiet"


def positioning(today: pd.DataFrame, prior: pd.DataFrame, spot: float, *,
                strike_range_percent: float = 2.5,
                churn_ratio: float = CHURN_RATIO,
                accumulation_ratio: float = ACCUMULATION_RATIO,
                liquidation_ratio: float = LIQUIDATION_RATIO,
                high_volume_percentile: float = HIGH_VOLUME_PERCENTILE,
                wall_moneyness: float = WALL_MONEYNESS) -> pd.DataFrame:
    """Volume, overnight open-interest change and a verdict, per strike.

    `today` is core.gex.by_strike's frame — it already carries call and put
    volume and open interest per strike — and `prior` is the previous
    session's open interest. The two must come from DIFFERENT sessions: open
    interest does not move intraday, so differencing within one day returns
    zeros and every verdict collapses to churn.

    The high-volume cut is a percentile of the strikes IN RANGE, not of the
    whole chain. Taken over the far wings it would sit near zero and mark the
    entire screen as high volume.

    With no prior session the delta is NaN and every verdict is blank. That
    is the first collected day, and the alternative — treating "unknown" as
    zero change — would report the whole board as churn.
    """
    if today is None or today.empty:
        return _blank(VERDICT_COLUMNS)

    band = spot * strike_range_percent / 100.0
    work = today[(today["strike"] >= spot - band)
                 & (today["strike"] <= spot + band)].copy()
    if work.empty:
        return _blank(VERDICT_COLUMNS)

    work["total_volume"] = work["call_volume"] + work["put_volume"]

    if prior is None or prior.empty:
        work["delta_oi"] = float("nan")
        work["verdict"] = "—"
        work["tone"] = "quiet"
        return work.sort_values("strike", ignore_index=True)[list(VERDICT_COLUMNS)]

    prior_total = prior[["strike", "call_oi", "put_oi"]].assign(
        prior_oi=lambda d: d["call_oi"] + d["put_oi"])[["strike", "prior_oi"]]
    merged = work.merge(prior_total, on="strike", how="left")
    merged["prior_oi"] = merged["prior_oi"].fillna(0.0)
    merged["delta_oi"] = merged["call_oi"] + merged["put_oi"] - merged["prior_oi"]

    traded = merged.loc[merged["total_volume"] > 0, "total_volume"]
    cut = float(traded.quantile(high_volume_percentile / 100.0)) if len(traded) else 0.0

    verdicts = [
        classify(v, d, high_volume=(v >= cut and v > 0),
                 churn_ratio=churn_ratio,
                 accumulation_ratio=accumulation_ratio,
                 liquidation_ratio=liquidation_ratio)
        for v, d in zip(merged["total_volume"], merged["delta_oi"])
    ]
    merged["verdict"] = [v for v, _ in verdicts]
    merged["tone"] = [t for _, t in verdicts]

    # Wall defence overrides only where nothing louder was found. A strike
    # already called Heavy Accumulation is not improved by renaming it, and
    # the wall is the same fact told at a different resolution.
    found = walls(merged, spot, moneyness=wall_moneyness)
    for side, label in (("call_wall", "Call Wall Defense"),
                        ("put_wall", "Put Wall Defense")):
        level = found[side]
        if level is None:
            continue
        at = merged["strike"] == level
        blank = merged["verdict"] == "—"
        merged.loc[at & blank, "verdict"] = label
        merged.loc[at & blank, "tone"] = "wall"

    return merged.sort_values("strike", ignore_index=True)[list(VERDICT_COLUMNS)]


def walls(positions: pd.DataFrame, spot: float, *,
          moneyness: float = WALL_MONEYNESS) -> dict[str, float | None]:
    """The out-of-the-money strike that gained the most open interest, a side.

    A wall is a level where positions were OPENED rather than merely traded,
    far enough from spot not to be the at-the-money churn every session
    produces. Above spot it reads as a call wall, below as a put wall; which
    of them a dealer is short is, again, not in this data.

    None on a side with no qualifying gain — a real state, and one that says
    more than naming the nearest strike anyway would.
    """
    out: dict[str, float | None] = {"call_wall": None, "put_wall": None}
    if positions is None or positions.empty or "delta_oi" not in positions:
        return out

    gained = positions.dropna(subset=["delta_oi"])
    gained = gained[gained["delta_oi"] > 0]
    above = gained[gained["strike"] >= spot * (1 + moneyness)]
    below = gained[gained["strike"] <= spot * (1 - moneyness)]
    if not above.empty:
        out["call_wall"] = float(above.loc[above["delta_oi"].idxmax(), "strike"])
    if not below.empty:
        out["put_wall"] = float(below.loc[below["delta_oi"].idxmax(), "strike"])
    return out
