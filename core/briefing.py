"""
briefing.py — the Gamma Exposure tab, arranged as a trader reads it.

WHAT THIS IS FOR. A language model is going to be asked to explain the tab in
plain words. This module decides what it is shown and in what order. It does
no explaining itself and calls no model: it turns one snapshot into a small,
ordered, ALREADY-FORMATTED set of figures, and stops.

THE ORDER IS THE POINT, and it is Chandan's (2026-09-08):

    volume -> delta -> gamma -> vanna -> charm

Each rung is a derivative of the one above it. Gamma is meaningless until you
know the delta it is changing; vanna and charm are meaningless until you know
the gamma they are eroding. Handed all five at once a model leads with charm
because charm sounds sophisticated. Handed them as a ladder it has to earn its
way up, and a reader can see which rung an argument fell off.

NOTHING HERE IS A JUDGEMENT. `pin_reading` looks like one and is not: the
candidate strike, the range around it and the verdict on whether a pin is even
possible are all arithmetic over `core.gex` output, computed HERE precisely so
that the model is never the thing that chose a number. That is the whole
guardrail. A model that picks its own strike can defend it with an
explanation that sounds exactly like an explanation of a real one.

EVERY FIGURE LEAVES AS A STRING. `fmt_money` chooses the divisor and the
suffix, `peak_label` joins the side onto the strike — the same functions the
screen uses, so the briefing and the panel above it cannot quote one number
two ways. The raw values stay in the frames for anything that has to compare;
what crosses into a prompt is what a reader would have seen.

BLANK IS NOT ZERO, here as everywhere. A measure that could not be computed
leaves as an em dash. A model told "0" invents a reason for the zero.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from core.format import fmt_money, peak_label

# The rungs, in reading order. Named once so the assembler, the prompt builder
# and any test asking "did we show all five" cannot drift into disagreeing
# about what the ladder is.
LADDER = ("volume", "delta", "gamma", "vanna", "charm")

# How far either side of spot a strike may sit and still count as a pin
# candidate, as a fraction of spot. A magnet four percent away is not pinning
# anything today; it is next week's story.
PIN_BAND = 0.01

# The wider band the support and resistance walls are looked for in. Wider
# than PIN_BAND on purpose: the walls are the edges of the range, so they are
# expected to sit outside the pin, not next to it.
WALL_BAND = 0.03

_DASH = "—"


def _blank(value: Any) -> str:
    """One None-to-em-dash rule, applied everywhere rather than at each site."""
    return _DASH if value is None or pd.isna(value) else value


def _strike(value: float | None) -> str:
    return _DASH if value is None or pd.isna(value) else f"{value:,.0f}"


def gamma_figures(summary: dict[str, Any] | None,
                  flip_strike: float | None) -> dict[str, str]:
    """The headline strip, formatted — net, calls, puts, peak, flip, ratio.

    This deliberately mirrors `api.computed.exposure_labels` rather than
    calling it: that function serves a JSON payload and returns every key of
    the six-key second-order dict including the half that is None. A prompt
    carrying `net_cex: "—"` under a heading that says GAMMA is a model's
    invitation to explain a figure that was never asked for.
    """
    if not summary:
        return {}
    out: dict[str, str] = {}
    for key in ("net_gex", "call_gex", "put_gex", "abs_gex"):
        if key in summary:
            out[key] = fmt_money(summary[key])
    if summary.get("peak_strike") is not None:
        out["peak_strike"] = peak_label(summary)
    out["flip_strike"] = _strike(flip_strike)
    ratio = summary.get("ratio")
    out["ratio"] = _DASH if ratio is None else f"{ratio:+,.1f}x"
    sentiment = summary.get("sentiment")
    if sentiment is None:
        out["sentiment"] = _DASH
    else:
        bars = ("" if summary.get("total_bars") is None
                else f" ({summary['positive_bars']}/{summary['total_bars']})")
        out["sentiment"] = f"{sentiment:,.0f}%{bars}"
    return out


def volume_figures(gex_df: pd.DataFrame, *, top: int = 5) -> dict[str, Any]:
    """Rung one — where the trading actually went today.

    READ OFF THE GEX FRAME rather than the bubble panel's own points, because
    the two must agree. `core.gex.by_strike` already carries `call_volume` and
    `put_volume` per strike; going to `core.dealer` for a second opinion on
    the same broker column would be a second place for the totals to be
    computed and so a second place for them to differ.

    THIS IS A COUNT OF CONTRACTS, NOT A DIRECTION. The chain records how many
    traded, never who was the aggressor — see the header of `core/flow.py`.
    The basis sentence says so, and it travels into the prompt, because a
    model shown "calls 210k / puts 340k" will otherwise narrate it as buying
    and selling.
    """
    if gex_df.empty:
        return {"busiest": [], "call_volume": _DASH, "put_volume": _DASH,
                "put_call_ratio": _DASH}

    frame = gex_df.copy()
    frame["total_volume"] = (frame["call_volume"].fillna(0)
                             + frame["put_volume"].fillna(0))
    calls = float(frame["call_volume"].sum())
    puts = float(frame["put_volume"].sum())
    busiest = frame.nlargest(top, "total_volume")

    return {
        "busiest": [
            {"strike": _strike(row.strike),
             "contracts": f"{row.total_volume:,.0f}",
             "calls": f"{row.call_volume:,.0f}" if pd.notna(row.call_volume)
                      else _DASH,
             "puts": f"{row.put_volume:,.0f}" if pd.notna(row.put_volume)
                     else _DASH}
            for row in busiest.itertuples()
        ],
        "call_volume": f"{calls:,.0f}",
        "put_volume": f"{puts:,.0f}",
        # Blank rather than infinite when nothing traded on the call side: a
        # ratio with a zero denominator is not a large ratio, it is no ratio.
        "put_call_ratio": f"{puts / calls:,.2f}" if calls else _DASH,
    }


def strike_rows(gex_df: pd.DataFrame, dex_df: pd.DataFrame,
                vanna_df: pd.DataFrame, charm_df: pd.DataFrame) -> list[dict]:
    """Every strike on the board, with all five measures side by side.

    WHY THIS EXISTS. Without it the ladder carries only totals and peaks, so
    the tutor could name 7,500 as the peak delta strike and then, asked what
    the delta AT 7,500 actually was, could only answer that it had not been
    told — which is what happened (2026-09-09). The rungs above are the
    reading; this is the board the reading was taken from, and a reader
    learning the Greeks asks about single strikes constantly.

    JOINED ON STRIKE, NOT CONCATENATED. The four frames are computed
    separately and are not guaranteed to carry the same strikes in the same
    order — an outer join keeps a strike that appears in one and not another
    rather than silently pairing row seven of one with row seven of the next.

    STILL FORMATTED, NOT RAW. Every value goes through the same `fmt_money`
    the panels use, so a figure quoted back from this table matches the one on
    screen character for character. A model handed raw floats would round them
    its own way and the two would disagree in the reader's eyes.
    """
    if gex_df.empty:
        return []

    frame = gex_df[["strike", "net_gex", "call_volume", "put_volume"]].copy()
    for other, cols in ((dex_df, ["net_dex"]), (vanna_df, ["net_vex"]),
                        (charm_df, ["net_cex"])):
        if other is not None and len(other) and "strike" in other:
            keep = ["strike"] + [c for c in cols if c in other]
            frame = frame.merge(other[keep], on="strike", how="outer")

    frame = frame.sort_values("strike")
    rows = []
    for row in frame.itertuples():
        entry = {"strike": _strike(row.strike)}
        volume = sum(v for v in (getattr(row, "call_volume", None),
                                 getattr(row, "put_volume", None))
                     if pd.notna(v))
        entry["volume"] = f"{volume:,.0f}" if volume else _DASH
        for key, column in (("delta", "net_dex"), ("gamma", "net_gex"),
                            ("vanna", "net_vex"), ("charm", "net_cex")):
            value = getattr(row, column, None)
            # FORMATTED THROUGH fmt_money, not left as a float. A raw
            # 23060021923.705006 in the prompt is a number the model rounds
            # its own way, and "23.1 billion" quoted back at a reader whose
            # screen says "23.1B" is a figure they cannot check.
            entry[key] = (_DASH if value is None or pd.isna(value)
                          else fmt_money(float(value)))
        rows.append(entry)
    return rows


def delta_figures(dex_df: pd.DataFrame) -> dict[str, Any]:
    """Rung two — the chain's net delta, and where it is concentrated.

    `net_dex` is the CHAIN's, not an inferred dealer inventory: the dealer
    sign that gamma carries is deliberately not applied to delta upstream (see
    `core.gex.dex_by_strike`). The basis sentence carries that distinction
    into the prompt, because "net delta" said without it reads as a claim
    about dealer positioning that this number does not make.
    """
    if dex_df.empty or "net_dex" not in dex_df:
        return {"net_dex": _DASH, "peak_dex_strike": _DASH}
    net = float(dex_df["net_dex"].sum())
    magnitude = dex_df["net_dex"].abs()
    peak = (None if magnitude.empty or magnitude.isna().all()
            else float(dex_df.loc[magnitude.idxmax(), "strike"]))
    return {"net_dex": fmt_money(net), "peak_dex_strike": _strike(peak)}


def second_order_figures(summary: dict[str, Any] | None,
                         measure: str) -> dict[str, str]:
    """Rungs four and five — vanna or charm, and only the half that was asked.

    `core.gex.second_order_summary` returns one fixed six-key dict with the
    unasked half set to None. Serving all six is right for the API, where the
    shape is the contract; carrying all six into a prompt hands a model three
    em dashes under a heading and lets it wonder aloud what they mean.
    """
    if not summary:
        return {}
    keys = (("net_vex", "abs_vex", "peak_vex_strike") if measure == "vanna"
            else ("net_cex", "abs_cex", "peak_cex_strike"))
    out: dict[str, str] = {}
    for key in keys:
        value = summary.get(key)
        if key.startswith("peak"):
            out[key] = _strike(value)
        else:
            out[key] = fmt_money(value)
    return out


def pin_reading(gex_df: pd.DataFrame, spot: float,
                flip_strike: float | None) -> dict[str, Any]:
    """Is anything holding price here, and if so, between what?

    **THIS IS COMPUTED SO THAT THE MODEL DOES NOT COMPUTE IT.** Everything
    below is arithmetic over columns `core.gex` produced. Asked for a pin
    every thirty minutes a model will supply one every thirty minutes,
    including on the days when the honest answer is that nothing is holding
    anything — and it will explain that invented level as fluently as a real
    one. The only defence is to hand it the level, or hand it the news that
    there isn't one.

    THE SIGN DECIDES WHETHER THE QUESTION IS EVEN ASKED. Net gamma positive
    means dealers are long gamma: they sell into rallies and buy dips, and
    price gets pulled back toward the big strikes. Negative means the hedge
    pushes WITH the move and there is no magnet at all — so `pin_possible` is
    False and `candidate` is None, not the least-bad strike.

    THE RANGE IS TWO WALLS, NOT A GUESS. The largest positive-gamma strike
    below spot and the largest above it are where hedging turns price back
    inward; between them is the band that behaviour implies. Either can be
    absent (nothing positive on that side), and an absent edge stays None
    rather than falling back to spot, which would draw a range out of no data.
    """
    # EVERY BRANCH BELOW RETURNS THE SAME SHAPE, and the levels are em dashes
    # rather than None in all of them. Caught by a test: the short-gamma path
    # returned `candidate: null` while the no-strike-near-enough path returned
    # `candidate: "—"`, which is two spellings of one answer arriving from one
    # function. A model shown both across a day would have every reason to
    # read them as different findings, and neither of them is a level.
    #
    # `pin_possible` is the machine-readable flag and stays a bool. The three
    # level fields are display figures, so they follow the project's
    # blank-not-zero rule and blank as an em dash.
    empty = {"pin_possible": False, "reason": "no strikes to read",
             "candidate": _DASH, "candidate_gex": _DASH,
             "support": _DASH, "resistance": _DASH,
             "flip_inside_range": None}
    if gex_df.empty or "net_gex" not in gex_df:
        return empty

    frame = gex_df.dropna(subset=["net_gex", "strike"])
    if frame.empty:
        return empty

    net_total = float(frame["net_gex"].sum())
    positive = frame[frame["net_gex"] > 0]

    if net_total <= 0:
        # The negative case is not a failure to find a pin — it is a finding,
        # and the wording matters because it is what the model will paraphrase.
        return {**empty, "pin_possible": False,
                "reason": "net gamma is negative: dealer hedging follows the "
                          "move rather than fading it, so nothing is pinning",
                "net_gex_sign": "short"}

    if positive.empty:
        return {**empty, "reason": "no strike carries positive net gamma",
                "net_gex_sign": "long"}

    near = positive[(positive["strike"] - spot).abs() <= spot * PIN_BAND]
    candidate = (None if near.empty
                 else float(near.loc[near["net_gex"].idxmax(), "strike"]))
    candidate_gex = (None if near.empty
                     else float(near["net_gex"].max()))

    wide = positive[(positive["strike"] - spot).abs() <= spot * WALL_BAND]
    below = wide[wide["strike"] < spot]
    above = wide[wide["strike"] > spot]
    support = (None if below.empty
               else float(below.loc[below["net_gex"].idxmax(), "strike"]))
    resistance = (None if above.empty
                  else float(above.loc[above["net_gex"].idxmax(), "strike"]))

    # WHETHER THE FLIP IS INSIDE THE RANGE IS THE CAVEAT ON THE WHOLE READING.
    # A flip level sitting between the two walls means the regime changes
    # partway across the band, so the lower half of it is not behaving like
    # the upper half and the range should not be quoted as one thing.
    inside = None
    if flip_strike is not None and support is not None and resistance is not None:
        inside = bool(support <= flip_strike <= resistance)

    return {
        "pin_possible": candidate is not None,
        "reason": ("net gamma is positive: dealer hedging fades moves, so the "
                   "large positive strikes act as magnets"
                   if candidate is not None else
                   f"no positive-gamma strike within "
                   f"{PIN_BAND:.0%} of spot"),
        "net_gex_sign": "long",
        "candidate": _strike(candidate),
        "candidate_gex": fmt_money(candidate_gex),
        "support": _strike(support),
        "resistance": _strike(resistance),
        "flip_inside_range": inside,
    }


# What each rung rests on, in one sentence, travelling WITH the figures. These
# are the same claims the panels carry under their own titles; they are here
# because a number in a prompt with no statement of what it measures is a
# number a model will describe in whatever terms sound most confident.
BASIS = {
    "volume": "contracts traded this session, from the chain's running "
              "totals. It records HOW MANY traded, never who was the buyer "
              "or the seller — this data cannot say whether volume was "
              "aggressive buying or aggressive selling.",
    "delta": "the chain's net delta, weighted by open interest. This is the "
             "chain's delta, not an inferred dealer inventory.",
    "gamma": "gamma x open interest, in dollars per 1% move in SPX. This is "
             "the structure already installed, built up over weeks. The flip "
             "strike is the whole chain's zero-gamma level, so it covers more "
             "contracts than a single selected expiry.",
    "vgex": "gamma x TODAY'S volume, same scale as gamma. This is the gamma "
            "this session added, so it is empty at the open and fills through "
            "the day. Divergence from the open-interest figure means levels "
            "are being repriced before open interest catches up.",
    "vanna": "how gamma changes as implied volatility moves. A FRONT-MONTH "
             "figure only — this record stops around 28 days and vega lives "
             "in longer-dated options, so it will not match a vendor's "
             "market-wide number.",
    "charm": "how delta decays as the clock runs to expiry. Front-month only, "
             "same limitation as vanna. It grows sharply into the close on "
             "0DTE contracts.",
    "by_strike": "every strike on the board with all five measures side by "
                 "side, in the same units and the same formatting as the "
                 "panels. This is the table the rungs above were summarised "
                 "FROM, so a figure quoted from here will match the screen. "
                 "The columns are net values per strike; a blank is a measure "
                 "that could not be computed at that strike, not a zero.",
    "pin": "computed from the gamma frame, not judged: the candidate is the "
           "largest positive-gamma strike within 1% of spot, and the walls "
           "are the largest positive-gamma strikes within 3% either side.",
}


def assemble(*, spot: float, session_time: str,
             gamma: dict[str, Any], vgex: dict[str, Any],
             delta: dict[str, Any], vanna: dict[str, Any],
             charm: dict[str, Any], expiry_scope: Any = None) -> dict[str, Any]:
    """One snapshot, as five rungs plus the computed pin reading.

    The five arguments are the payloads `api.computed` already builds for the
    tab — passed in rather than computed here so that this module cannot
    become a second opinion about what the tab shows. If the endpoint and the
    briefing ever disagree, it will be because someone gave them different
    snapshots, not because they did different arithmetic.
    """
    gex_df = gamma["by_strike"]
    return {
        "as_of": session_time,
        "spot": f"{spot:,.2f}",
        "expiry_scope": expiry_scope or "whole board",
        "ladder": [
            {"rung": 1, "name": "volume",
             "figures": volume_figures(gex_df), "basis": BASIS["volume"]},
            {"rung": 2, "name": "delta",
             "figures": delta_figures(delta["by_strike"]),
             "basis": BASIS["delta"]},
            {"rung": 3, "name": "gamma",
             "figures": gamma_figures(gamma["summary"], gamma["flip_strike"]),
             "basis": BASIS["gamma"]},
            {"rung": 3.5, "name": "vgex",
             "figures": gamma_figures(vgex["summary"], vgex["flip_strike"]),
             "basis": BASIS["vgex"]},
            {"rung": 4, "name": "vanna",
             "figures": second_order_figures(vanna["summary"], "vanna"),
             "basis": BASIS["vanna"]},
            {"rung": 5, "name": "charm",
             "figures": second_order_figures(charm["summary"], "charm"),
             "basis": BASIS["charm"]},
        ],
        "pin": {**pin_reading(gex_df, spot, gamma["flip_strike"]),
                "basis": BASIS["pin"]},
        # THE BOARD ITSELF, under the reading of it. The rungs above are
        # summaries and peaks; this is every strike, so a question about one
        # strike has an answer instead of an apology.
        "by_strike": {
            "basis": BASIS["by_strike"],
            # FETCHED WITH .get, because a caller may hand in a summary
            # without its frame — `strike_rows` drops a missing measure from
            # the table rather than refusing to build one, so a partial
            # snapshot still answers the questions it can.
            "rows": strike_rows(gex_df, delta.get("by_strike"),
                                vanna.get("by_strike") if vanna else None,
                                charm.get("by_strike") if charm else None),
        },
    }
