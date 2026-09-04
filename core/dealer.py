"""core/dealer.py — what a session's flow says about dealer inventory.

ONE QUESTION. `positioning` asks what core/gex.py cannot: gamma exposure
describes what is LISTED, this describes whether what TRADED today stayed on
the books. Volume alone cannot tell a position being opened from one contract
changing hands forty times; open interest settles it, because it counts what
still existed at the close.

A `bubble_points` lived here too, spreading volume across expiry and strike.
It was removed with the chart it fed, on the evidence of use: nothing was
read off that panel which this one does not say plainly.

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

import pandas as pd

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

VERDICT_COLUMNS = ("strike", "call_volume", "put_volume", "total_volume",
                   "delta_oi", "verdict", "tone")


def _blank(columns) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


# ─────────────────────────────────────────────────────────────────────────────
# Expiry naming — the x axis of the bubble chart
# ─────────────────────────────────────────────────────────────────────────────

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
