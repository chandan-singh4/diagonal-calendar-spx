"""M4.3 — the answers, not just the rows: scanner, gamma exposure, and "New".

WHAT IS SERVED HERE AND WHAT IS NOT, because the difference is a real
constraint rather than an oversight.

SERVED: the transform scanner and the gamma/delta exposure work. Both live in
`core/`, which is pure by rule — no database, no page, function of its
arguments — so this module loads a chain through `dataaccess/` and calls them.
Nothing is reimplemented.

PARTLY SERVED, AS OF 2026-09-05: the Mission Control CARDS. This paragraph
used to say the panel was not served at all, because every path through
`services/mission_control.py` imports streamlit, `api/` may not import
`services/`, and the choice was therefore between importing the page into a
server and keeping a second copy that would drift. It ended by saying the
extraction "belongs in its own task rather than being smuggled into this one".

That task was done. `candidate_signals` and `approaching_panel` at the foot of
this module are the ORIGINALS, moved down a layer, and services/ now calls
them — so there is still exactly one definition. What made it possible was not
that the code got easier: those two bodies never used streamlit, only the
module around them did.

SERVED SINCE 2026-09-06: the non-ATM panel too, which is what closed DEBT-041
and made the Scanner tab replaceable. `non_atm_panel` below is the original,
moved down; services/ calls it. It needed one thing the cards did not — the
registry it reads is a sidecar file, so the server had to be told where those
live (`create_app(state_dir=...)`), mirroring the `db_path` seam so `api/`
still imports nothing from `services/`.

ALSO SERVED SINCE 2026-09-06: vanna and charm by strike
(`second_order_exposure`), so the two views added to the Gamma Exposure tab
that day are not page-only. That needed `core.gex.day_remainder`, which had
been a private function on the page — the same one-definition-two-callers move
in miniature.

WHAT THAT LEAVES, AND WHY IT IS THE USEFUL HALF ANYWAY. The panel is a way of
DISPLAYING the eligible set. The eligible set itself comes from the scanner
sweep, which is pure and is served below — and the "New" flag keys off exactly
that set: `_update_eligible_history` in services/ builds its registry key from
the scanner's own "front|back|put|call" at Transform Diff >= threshold, not
from anything the panel adds. So the flag can be computed here, correctly,
from the same source the page uses, without the page.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

import config
import db
import iv_engine
from core import contract, gex, market
from core import session as core_session
from core.format import (
    exp_label,
    fmt_duration,
    fmt_eta,
    fmt_money,
    money_ticks,
    peak_label,
    sparkline,
)
from core.ranking import nearest_idx, rank_for_panel
from core.scanner import (
    APPROACHING_LOW,
    TSCAN_THRESHOLD,
    add_mark_columns,
    scan_all_offsets,
)


# The key format is not ours to choose: services/mission_control.py has been
# writing "front|back|put|call" into eligible_history.json since M2, with the
# strikes as ints. A key that differs by so much as a decimal point would make
# every pair look new forever while comparing two vocabularies that never
# intersect.
def pair_key(front_raw: str, back_raw: str, put_strike: float,
             call_strike: float) -> str:
    return f"{front_raw}|{back_raw}|{int(put_strike)}|{int(call_strike)}"


def _raw_expiry(label: str) -> str:
    """The scanner returns "2026-09-18 (14d)"; the registry key uses the date.

    Matches `.split(" ")[0]` in services/mission_control.py exactly. Note this
    also strips a settlement marker, which is correct here only because the
    existing registry does the same — see the note in api/__init__.py about
    the two third-Friday contracts if this key ever grows a settlement field.
    """
    return label.split(" ", maxsplit=1)[0]


def eligible_from_sweep(sweep: pd.DataFrame,
                        threshold: float = TSCAN_THRESHOLD) -> dict[str, float]:
    """Every pair at or above the threshold, as {pair_key: gap}.

    The `>=` is deliberate and matches services/: a gap of exactly the
    threshold is eligible, not approaching.
    """
    if sweep is None or sweep.empty:
        return {}
    hits = sweep[sweep["Transform Diff"] >= threshold]
    return {
        pair_key(_raw_expiry(row["Front Expiry"]), _raw_expiry(row["Back Expiry"]),
                 row["Put Strike"], row["Call Strike"]): float(row["Transform Diff"])
        for _, row in hits.iterrows()
    }


def scan(chain_df: pd.DataFrame, spot: float, snapshot_id: int) -> pd.DataFrame:
    """Phase A: sweep every offset across every valid expiry pair.

    `scan_all_offsets` defaults its `compute` argument to the UNCACHED
    scanner, which is the right choice here — this whole result is cached by
    api/cache.py on the snapshot, one layer up, so caching the 21 individual
    sweeps underneath it would be a second memo keyed on the same thing.
    """
    return scan_all_offsets(chain_df=chain_df, spx_price=spot,
                            snapshot_id=snapshot_id)


def add_raw_expiries(sweep: pd.DataFrame) -> pd.DataFrame:
    """Add `Front Raw` and `Back Raw` -- the display KEYS behind the labels.

    THE SWEEP CARRIES LABELS, AND A LABEL IS NOT AN ADDRESS. "2026-09-23
    (19d)" is what the table shows; "2026-09-23" is what every endpoint
    taking an `expiry` wants, and passing the label gets a 500. The Streamlit
    drill-down recovers the key by splitting on the first space
    (`views/scanner.py`), and it is served here instead so no client has to
    know that rule -- or get it subtly wrong.

    Returns a COPY: the sweep is cached, and a caller that added columns in
    place would leave them on the frame every later reader sees.

    THE STRIP IS LOSSY FOR ONE CONTRACT AND THAT IS UNFIXED HERE. The third
    Friday lists twice, "2026-09-18" and "2026-09-18 (AM)", and both labels
    reduce to the same key -- so a drill-down from an a.m. row opens the p.m.
    contract. The existing registry has the same limitation and the same
    `.split()` behind it (see `_raw_expiry`), and making this one function
    disagree with the registry would trade a visible flaw for an invisible
    one. It is recorded rather than papered over.
    """
    out = sweep.copy()
    out["Front Raw"] = out["Front Expiry"].map(_raw_expiry)
    out["Back Raw"] = out["Back Expiry"].map(_raw_expiry)
    return out


def classify(sweep: pd.DataFrame) -> dict[str, Any]:
    """Counts by band, so a caller can ask "is anything happening" cheaply."""
    if sweep is None or sweep.empty:
        return {"eligible": 0, "approaching": 0, "total": 0}
    gaps = sweep["Transform Diff"]
    return {
        "eligible": int((gaps >= TSCAN_THRESHOLD).sum()),
        "approaching": int(((gaps >= APPROACHING_LOW)
                            & (gaps < TSCAN_THRESHOLD)).sum()),
        "total": len(sweep),
        "threshold": TSCAN_THRESHOLD,
        "approaching_from": APPROACHING_LOW,
    }


def new_since_previous(db_path: str, snapshot_id: int,
                       eligible_now: dict[str, float],
                       *, record: bool = True) -> dict[str, Any]:
    """Which pairs are eligible now and were not at the previous recording.

    RECORDING IS THE DEFAULT AND IS THE ONLY WRITE THIS MILESTONE MAKES. It
    has to happen for the next call to have anything to compare against; a
    read-only mode that never recorded would report the same pairs as new
    forever. `record=False` exists for checks and for a caller that genuinely
    wants to look without advancing the comparison.

    On the FIRST ever recording nothing is new — there is no "before" for
    anything to have been absent from, and calling everything new then is the
    same false alarm the browser-tab version raises on every reopened tab.
    """
    previous = db.get_previous_recorded_snapshot(db_path, snapshot_id)
    already = db.get_eligible_keys(db_path, snapshot_id)

    if previous is None and not already:
        new_keys: set[str] = set()
        baseline = None
    else:
        # If THIS snapshot was already recorded, the comparison that produced
        # its answer used `previous`; recomputing against the same baseline
        # keeps a repeated request idempotent rather than reporting nothing
        # new the second time round.
        baseline = previous
        prior_keys = (db.get_eligible_keys(db_path, previous)
                      if previous is not None else set())
        new_keys = set(eligible_now) - prior_keys

    if record:
        db.record_eligible_keys(db_path, snapshot_id, eligible_now)

    return {
        "snapshot_id": snapshot_id,
        "compared_against_snapshot": baseline,
        "eligible_count": len(eligible_now),
        "new_count": len(new_keys),
        "new_keys": sorted(new_keys),
        "recorded": record,
    }


def gamma_exposure(chain_df: pd.DataFrame, spot: float,
                   expiry: gex.ExpiryScope = None, *,
                   r: float, q: float,
                   snapshot_ts: str, display_tz: str) -> dict[str, Any]:
    """Gamma by strike, plus the flip level and the summary figures.

    `expiry` scopes to one contract by its DISPLAY KEY, so the third Friday's
    a.m. and p.m. contracts stay apart; without it this is the whole board.
    The scope matters enough that it is echoed back in the response — a gamma
    figure for one expiry and one for all twenty are different numbers and
    look identical on a screen.

    `r`, `q`, `snapshot_ts` and `display_tz` ARE REQUIRED, AND THEY ARE NEW
    (2026-09-07, BUG-044). Nothing in the per-strike frame needs them; the
    zero-gamma level does, because it re-prices the whole chain at
    hypothetical spots rather than accumulating the strikes it was given
    (`core.gex.zero_gamma_spot`). Required rather than defaulted for the same
    reason `second_order_exposure` requires them: they are assumptions this
    figure rests on and are not in the record, and a default would hide that
    at every call site. The timestamp buys the same thing it buys charm --
    the fraction of the last day still to run, which on a 0DTE expiry is the
    difference between a level and a blank.

    SUMMARY IS COMPUTED OVER EVERY STRIKE, matching views/gex.py. The ratio
    and sentiment are defined over the bars actually shown, and the dashboard
    shows the whole chain deliberately — a narrowable window gave a figure
    that changed under the reader every time they adjusted it. Serving a
    different scope here would make the API and the screen disagree for a
    reason neither could explain.
    """
    gex_df = gex.by_strike(chain_df, spot, expiry=expiry)
    # THE FLIP IS THE WHOLE CHAIN'S, NOT THIS PANEL'S -- note the absent
    # `expiry`, which is the one difference between this call and the bars
    # above it. The published definition Chandan brought on 2026-09-07 says
    # "all available strikes AND EXPIRATION DATES in the chain", and it is the
    # figure commentary means: a single-expiry flip is not a number anyone
    # quotes. Scoping it to the selection put the line 145 points from where
    # he expected it (7,545 for Sep 18 alone against 7,671 for the board),
    # and he was reading the chart correctly.
    #
    # The consequence has to be carried on screen rather than hidden: the
    # dashed line describes MORE contracts than the bars it is drawn over. The
    # headline calls it "Gamma flip (chain)" for that reason.
    #
    # Computed ONCE and passed into the summary rather than computed in both
    # places: the headline strip and the dashed line must be the same level,
    # and two calls to a search this expensive would be two chances for them
    # to differ as well as twice the work.
    flip = gex.zero_gamma_spot(
        chain_df, spot, r=r, q=q,
        day_remainder=gex.day_remainder(snapshot_ts, display_tz))
    return {
        "expiry": expiry,
        "spot": spot,
        "flip_strike": flip,
        "summary": gex.summary(gex_df, flip_strike=flip),
        "by_strike": gex_df,
    }


def volume_gamma_exposure(chain_df: pd.DataFrame, spot: float,
                          expiry: gex.ExpiryScope = None, *,
                          r: float, q: float,
                          snapshot_ts: str,
                          display_tz: str) -> dict[str, Any]:
    """vGEX -- gamma weighted by TODAY'S VOLUME instead of open interest.

    ADDED 2026-09-06 at Chandan's request. GEX describes the structure that is
    installed: every contract still open, whenever it was written. vGEX
    describes what has traded THIS SESSION. Read side by side, a divergence
    says levels are being repriced before open interest catches up, and
    agreement says the installed structure is holding. That comparison is the
    entire point, which is why this returns the same column names and the same
    summary shape as `gamma_exposure` above and is scaled identically -- see
    `core.gex.by_strike`'s note on the vendor card's scale.

    `flip_ratio` is the vendor's own vGEX ratio and is NOT `summary["ratio"]`;
    core.gex.flow_ratio says why both exist.

    NO `assumptions` BLOCK, for the same reason gamma and delta have none:
    this weights two columns the broker sent and rests on nothing inferred.
    THE FLIP LEVEL IS THE EXCEPTION and always was -- see `gamma_exposure` on
    why `r` and `q` are required here too. It is a re-pricing, so it rests on
    them; the bars do not.

    THE FLIP HERE IS WEIGHTED BY VOLUME, matching the measure it belongs to.
    A level found from open interest, quoted on a panel of today's flow, would
    be the one figure on the screen describing a different book. Its SCOPE,
    though, is the whole chain -- see `gamma_exposure`.
    """
    gex_df = gex.by_strike(chain_df, spot, expiry=expiry,
                           weight=gex.WEIGHTS["vgex"])
    # WHOLE CHAIN, exactly as the gamma one -- see its note. Still weighted by
    # VOLUME, because the comparison between the two flips is the entire use
    # of vGEX and a weight difference is the only thing that may separate them.
    flip = gex.zero_gamma_spot(
        chain_df, spot, r=r, q=q,
        day_remainder=gex.day_remainder(snapshot_ts, display_tz),
        weight=gex.WEIGHTS["vgex"])
    return {
        "measure": "vgex",
        "expiry": expiry,
        "spot": spot,
        "flip_strike": flip,
        "summary": gex.summary(gex_df, flip_strike=flip),
        "flow_ratio": gex.flow_ratio(gex_df),
        "by_strike": gex_df,
    }


# The five measures /mission/gamma can draw a strike panel for — one per view
# on the Gamma Exposure tab, minus the three that are all read off `gamma`.
# Named here rather than in the route so the endpoint's parameter and the
# guards below cannot drift into disagreeing about what is valid.
#
# THEY DO NOT ALL COST THE SAME. `gamma` and `delta` weight a column the
# broker already sent; `vanna` and `charm` walk the chain through iv_engine
# one contract at a time and rest on two constants that are not in the record.
# That difference is why they are separate functions rather than one with a
# switch — the second-order pair returns an `assumptions` block and the other
# two would have nothing truthful to put in it.
GAMMA_MEASURES = ("gamma", "vgex", "delta", "vanna", "charm")


def exposure_labels(summary: dict[str, Any] | None) -> dict[str, str]:
    """The headline strip's figures, formatted once, in Python.

    WHY THESE TRAVEL RATHER THAN THE RAW NUMBERS ALONE. Turning
    12,202,473,345 into "12.2B" chooses a divisor and a suffix — a rounding
    rule — and docs/m6_migration_plan.md forbids those in the new language.
    The raw values stay in `summary` for anything that needs to compare or
    sort; these are what goes on screen.

    THE OUTPUT MIRRORS `summary`'S SHAPE EXACTLY, key for key, and that is
    worth stating because it produces something that looks like a mistake:
    ask for vanna and the labels include `net_cex: "—"`. That is not this
    function inventing a figure. `core.gex.second_order_summary` returns one
    fixed six-key dict with the unasked half set to None — the page's own
    shape — and an em dash is the correct rendering of a None under the
    project's blank-not-zero rule.

    Filtering those out here would make `labels` and `summary` disagree about
    which keys exist, which is a worse trap than a spare em dash a caller
    never reads. A caller reads the keys for the measure it asked for.
    """
    if not summary:
        return {}
    money = ("net_gex", "call_gex", "put_gex", "abs_gex", "net_vex",
             "abs_vex", "net_cex", "abs_cex")
    out = {k: fmt_money(summary[k]) for k in money if k in summary}

    for k in ("peak_strike", "peak_vex_strike", "peak_cex_strike",
              "flip_strike"):
        if k in summary:
            v = summary[k]
            out[k] = "—" if v is None else f"{v:,.0f}"

    # The peak strike carries its SIDE, matching the page's "7,720 (Put)".
    # `core.format.peak_label` is the page's own joiner -- see its docstring
    # for why this is not written out here.
    if summary.get("peak_strike") is not None:
        out["peak_strike"] = peak_label(summary)

    if "ratio" in summary:
        r = summary["ratio"]
        out["ratio"] = "—" if r is None else f"{r:+,.1f}x"
    if "sentiment" in summary:
        v = summary["sentiment"]
        # The bar count rides with it because the percentage alone reads as a
        # confidence: "51%" from 101 strikes and "51%" from 2 are different
        # statements, and the page shows both for that reason.
        bars = ("" if summary.get("total_bars") is None
                else f" ({summary['positive_bars']}/{summary['total_bars']})")
        out["sentiment"] = "—" if v is None else f"{v:,.0f}%{bars}"
    return out


def axis_ticks(frame: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    """Where the exposure axis's ticks go, and what they say.

    SERVED FOR THE SAME REASON THE LABELS ARE. `core.charts.money_ticks`
    places them so a billion reads "12.2B" and not Plotly's SI "12.2G" —
    matching the strip directly above the chart. Two notations for one number
    on one screen is a reader's problem, and it would be guaranteed if the
    React axis were left to Plotly's default while the Streamlit one was not.

    `columns` rather than one series because a panel draws call and put
    together and the axis has to span both.
    """
    present = [c for c in columns if frame is not None and c in frame.columns]
    if not present:
        return {}
    values = pd.concat([frame[c] for c in present], ignore_index=True)
    ticks = money_ticks(values)
    # Plotly's own key names, so a caller spreads this straight onto an axis.
    return {"tickvals": ticks.get("tickvals", []),
            "ticktext": ticks.get("ticktext", [])}


def strike_legs(chain_df, front: str, back: str,
                put_strike: float, call_strike: float) -> list[dict[str, Any]]:
    """The four contracts of the diagonal, priced, with the F/B IV ratio.

    Two legs, each with a front and a back contract. `iv_engine.strike_contract`
    is the same lookup the Strike Detail tab makes, including its fallback to
    the nearest strike when the exact one is not in the chain -- `found_exact`
    rides along so the screen can say so rather than silently showing a
    different contract's price.

    MISSING IS NULL, NOT ZERO. A contract with no IV or no mark returns None
    here and the tab prints "N/A"; a zero would read as a real measurement of
    a worthless option, which is a different claim entirely.
    """
    legs = []
    for label, strike, side in (("Put", put_strike, "PUT"),
                                ("Call", call_strike, "CALL")):
        f = iv_engine.strike_contract(chain_df, front, strike, side)
        b = iv_engine.strike_contract(chain_df, back, strike, side)
        ratio = (f.iv / b.iv) if (f.iv and b.iv) else None
        legs.append({
            "label": label,
            "strike": strike,
            "front_iv": f.iv, "back_iv": b.iv,
            "front_mark": f.mark, "back_mark": b.mark,
            "iv_ratio": ratio,
            "front_exact": f.found_exact, "back_exact": b.found_exact,
        })
    return legs


def atm_headline(rows: list) -> dict[str, Any]:
    """The latest ATM IV for one expiry and how far it has moved.

    `rows` are the two most recent ATM records, newest first. The change is
    against the PREVIOUS RECORD, not against the session open -- it is the
    "what just happened" figure the tab shows beside the level.

    IV IS RETURNED AS A PERCENT, matching every other IV this API serves; the
    stored column is a fraction. With one record there is no change to state,
    and `change` is None rather than 0.0 -- a flat reading and an unknown one
    are different, and only one of them should draw a green arrow.
    """
    if not rows:
        return {"atm_iv": None, "change": None}
    now = rows[0]["atm_avg_iv"] * 100
    change = ((rows[0]["atm_avg_iv"] - rows[1]["atm_avg_iv"]) * 100
              if len(rows) > 1 else None)
    return {"atm_iv": now, "change": change}


def header(*, spx_price, vix_value, prev_close, session_open, chain_df,
           snap_age_secs: float, now_et) -> dict[str, Any]:
    """The strip that sits above every tab, as data.

    NOTHING HERE IS A SUBTRACTION EITHER, and the day's change is the one
    that looks most like it. "Change" is measured from the last COMPLETE
    snapshot of the PRIOR session, falling back to today's first reading on
    the first day of collection and to the current price after that -- which
    reports a flat day rather than dividing by nothing
    (`core.market.daily_change`). A client subtracting from whatever it had
    on screen a moment ago would produce a number that changes meaning at
    midnight, at the open, and on the first day of any new instrument.

    WHAT "LATE" MEANS IS ALSO A RULE, NOT A CONSTANT. The expected gap
    between prices is the collector's own polling interval for the session
    happening right now -- 60 seconds in the first and last half hour, 300
    midday, and NOTHING at all when the market is shut, because a collector
    that is idle by design is not late. That comes from `core.session`, the
    same module the collector and the watchdog read, so this strip cannot
    start disagreeing with the thing it reports on.

    `amber_at` and `red_at` are seconds, and `market_closed` says the two
    are meaningless rather than merely large. The client counts upward and
    recolours as it passes them; the boundaries are not its to choose.
    """
    change = market.daily_change(spx_price, prev_close, session_open)
    session = core_session.session_of(now_et, config.MARKET_HOLIDAYS)
    expected = core_session.expected_interval(
        session, config.POLL_INTERVAL_EVENT, config.POLL_INTERVAL_NORMAL)
    amber_at, red_at, closed = core_session.staleness_thresholds(expected)
    return {
        "spx_price": spx_price,
        "vix": vix_value,
        "change": {
            "points": change.points,
            "percent": change.percent,
            "arrow": change.arrow,
            "colour": change.color,
            "reference_label": change.reference_label,
        },
        "gex_label": market.max_gex_label(chain_df, spx_price),
        "age_seconds": int(max(0, snap_age_secs)),
        "dot": core_session.staleness_level(snap_age_secs),
        "session": session,
        "expected_interval": expected,
        "amber_at": amber_at,
        "red_at": red_at,
        "market_closed": closed,
    }


def historical_window(ratios, current: float) -> dict[str, Any]:
    """Where today's ratio sits inside one window of its own history.

    THE PANEL IS A CONTEXT CLAIM, NOT A MEASUREMENT, and that is why every
    part of it is served. `low` and `high` bound the window, `position_pct`
    places the marker between them, and `percentile` says how much of the
    record sits below today -- three different questions, and a client
    deriving any of them from the other two would get a plausible wrong
    answer.

    NO HISTORY IS NOT A ZEROTH PERCENTILE. An empty window comes back with
    nulls and the MID band, because "the ratio has never been lower" and "we
    have nothing to compare against" must not paint the same colour.
    """
    stats = iv_engine.range_stats(ratios, current)
    pct = iv_engine.percentile_rank(ratios, current)
    label, colour = iv_engine.percentile_band(pct)
    return {
        "low": None if stats.low != stats.low else stats.low,
        "high": None if stats.high != stats.high else stats.high,
        "position_pct": stats.position_pct,
        "percentile": None if pct != pct else pct,
        "band": label,
        "colour": colour,
        "observations": int(ratios.notna().sum()) if ratios is not None else 0,
    }


def edge_headline(chain_df, spot, front: str, back: str) -> dict[str, Any]:
    """The four figures above the Calendar Edge charts.

    NOT ONE OF THESE IS A SUBTRACTION THE CLIENT COULD DO. The ATM IV of an
    expiry is the mean of the call and the put at the strike nearest spot
    (`iv_engine.atm_iv`) -- averaged because put-call parity says the two
    should agree at the money, so averaging drops a stale quote on one side
    rather than believing it. The ratio is `iv_engine.term_structure`'s, and
    the index is `iv_engine.iv_index`'s mean-of-means. A client dividing two
    served IVs would get the ratio right by luck and the other three wrong.

    MISSING IS NULL. `atm_iv` raises when an expiry has no IV at its ATM
    strike, and that is a real state on a thin chain near the close. It
    becomes None here, so the tab prints "N/A" rather than a figure invented
    to fill the box.

    Every value is a percent except the ratio, because `chain_df["iv"]` is
    stored as a percent -- see `iv_engine.iv_index`.
    """
    def _atm(expiry: str) -> float | None:
        try:
            return float(iv_engine.atm_iv(chain_df, expiry, spot))
        except (ValueError, KeyError):
            return None

    front_iv = _atm(front)
    back_iv = _atm(back)
    ratio = None
    if front_iv is not None and back_iv is not None:
        r = iv_engine.term_structure(front_iv, back_iv).ratio
        ratio = float(r) if r == r else None  # NaN when back_iv is 0
    return {
        "front_iv": front_iv,
        "back_iv": back_iv,
        "ratio": ratio,
        "iv_index": iv_engine.iv_index(chain_df),
    }


def pair_controls(chain_df, spot, front=None, back=None, *,
                  today: date | None = None) -> dict[str, Any]:
    """The four selections every pair-based tab is drawn from, and their rules.

    THE RULES ARE THE POINT, not the lists. Three of them are decisions that
    were made once and would be re-guessed differently by a client:

      * BACK EXPIRIES ARE NARROWED TO STRICTLY LATER DATES (Chandan,
        2026-08-19). A diagonal whose back leg expires first is not a
        diagonal. The comparison is on the DATE, so the third Friday's a.m.
        and p.m. contracts do not pair with each other even though the p.m.
        one does settle a few hours later -- "the first back option is the
        next date" is the rule he asked for.
      * STRIKES ARE THE INTERSECTION of what both expiries list. A strike
        present in only one leg cannot be a diagonal at all, and offering it
        produces an empty chart with nothing saying why.
      * THE DEFAULTS ARE NOT ARBITRARY: the call lands nearest spot and the
        put a hundred points below it, which is the shape of the trade this
        dashboard is for. `core.ranking.nearest_idx` picks them, ties going
        to the lower strike.

    `front`/`back` narrow the strike lists to a chosen pair; omitted, the
    defaults above decide. Returns display keys throughout -- the third
    Friday's two contracts are two different options, not one (BUG-028).

    `today` is the day the countdowns in the labels are measured FROM, and
    means what it means on `expiry_board`: the reader's own date on the
    current board, the snapshot's date on a replayed one, None for the
    snapshot's own. Chandan, 2026-09-07: "DTE following the live clock should
    be applicable for Front Expiry and Back Expiry dropdown as well." These
    two dropdowns are how a pair is CHOSEN, so a stale countdown here picks
    the wrong contract rather than merely mislabelling one -- a "4 DTE" front
    leg that is really 1 DTE is a different trade.

    THE NARROWING IS UNAFFECTED, and deliberately so. Back expiries are
    filtered by DATE against the front leg's date, not by countdown, so
    re-basing changes every label and no membership. That is the right
    outcome: which contracts can pair with which is a fact about the
    contracts, not about when they are being looked at.
    """
    expiries = restated(expiry_options(chain_df), today or snapshot_date(chain_df))
    keys = [e["key"] for e in expiries]
    if not keys:
        return {"expiries": [], "back_expiries": [], "put_strikes": [],
                "call_strikes": [], "front": None, "back": None,
                "put_strike": None, "call_strike": None}

    front = front if front in keys else keys[0]
    back_keys = [k for k in keys
                 if contract.date_of(k) > contract.date_of(front)]
    # Only reachable by choosing the furthest expiry collected as the front
    # leg. The page falls back to the front itself and says so; here the list
    # is simply empty, and `back` stays None rather than naming a pair that
    # is not one.
    back = back if back in back_keys else (back_keys[0] if back_keys else None)

    def strikes_for(side: str) -> list[float]:
        if back is None:
            return []
        f = set(chain_df[(chain_df["expiry"] == front)
                         & (chain_df["side"] == side)]["strike"].unique())
        b = set(chain_df[(chain_df["expiry"] == back)
                         & (chain_df["side"] == side)]["strike"].unique())
        return sorted(float(x) for x in (f & b))

    puts, calls = strikes_for("PUT"), strikes_for("CALL")
    return {
        "expiries": expiries,
        "back_expiries": [e for e in expiries if e["key"] in back_keys],
        "front": front,
        "back": back,
        "put_strikes": puts,
        "call_strikes": calls,
        "put_strike": puts[nearest_idx(puts, spot - 100)] if puts else None,
        "call_strike": calls[nearest_idx(calls, spot)] if calls else None,
    }


def expiry_options(chain_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Every expiry in this snapshot, in the order and wording the page uses.

    SERVED BECAUSE THE SELECTOR CANNOT BE BUILT WITHOUT IT. `/mission/gamma`
    takes `expiry` as a DISPLAY KEY, and until now nothing told a caller which
    keys exist — the only way to find out was to pull the whole chain, several
    thousand rows, to read one column of twenty distinct values.

    THE ORDER AND THE LABEL BOTH COME FROM core/, deliberately. Sorting these
    as plain strings puts the third Friday's two contracts in whichever order
    their suffixes happen to fall, and re-deriving "Friday, Sep 18, 2026
    (14 DTE)" in a second language would be a second formatter to keep in step
    with `core.format.exp_label`. Both already exist; this only calls them.

    `dte` is returned beside the label because a caller may want to pick a
    default — the nearest expiry, say — without parsing it back out of the
    text it was formatted into.
    """
    if chain_df is None or chain_df.empty or "expiry" not in chain_df.columns:
        return []
    dte_by_expiry = (chain_df.groupby("expiry")["dte"].first()
                     .astype(int).to_dict())
    keys = sorted(chain_df["expiry"].dropna().unique(), key=contract.sort_key)
    return [{"key": str(k),
             "label": exp_label(str(k), dte_by_expiry),
             "dte": int(dte_by_expiry[k])}
            for k in keys]


def snapshot_date(chain_df: pd.DataFrame) -> date | None:
    """The trading date this snapshot was taken on, read off the chain itself.

    NOT `date.today()`, AND THAT IS THE POINT. The expiry filters below are
    relative to "now", but a snapshot pulled from the record is a picture of a
    past "now": grouping last Tuesday's chain against this morning's calendar
    would drop every contract that has since expired out of every bucket and
    show an empty board. Reading the date out of the data keeps a replayed
    snapshot grouped the way it was on the day.

    `dte` is whole days from the snapshot to the expiry, so `expiry - dte`
    recovers the snapshot's own date, and every row in the frame agrees on it.
    The nearest expiry is used because it is the one whose `dte` is least
    likely to have been rounded across a weekend.
    """
    if chain_df is None or chain_df.empty:
        return None
    if not {"expiry", "dte"}.issubset(chain_df.columns):
        return None
    dte_by_expiry = chain_df.groupby("expiry")["dte"].first().dropna()
    if dte_by_expiry.empty:
        return None
    nearest = dte_by_expiry.idxmin()
    try:
        return (date.fromisoformat(contract.date_of(str(nearest)))
                - timedelta(days=int(dte_by_expiry[nearest])))
    except ValueError:
        return None


def _day_label(expiry_date: str) -> str:
    """"Wed, Sep 9" -- weekday and date, no year, no leading zero."""
    ts = pd.Timestamp(expiry_date)
    return f"{ts:%a, %b} {ts.day}"


def _countdown(option: dict[str, Any], anchor: date | None) -> int:
    """Whole days from `anchor` to this expiry.

    FALLS BACK TO THE STORED FIGURE, never to a guess. With no anchor — a
    chain whose own date could not be read — the snapshot's `dte` is the only
    honest answer available, and it is the answer this function has always
    given. The same applies to a display key that will not parse: the third
    Friday's "(AM)" suffix is handled by `contract.date_of`, but a malformed
    key must not take the whole board down over a caption.

    NEGATIVE IS A REAL ANSWER and is deliberately not clamped. An expiry that
    has already settled reads "-2 DTE", which is the truth about a contract
    still sitting in a snapshot taken before it expired; showing it as 0 would
    put it in the one bucket a trader acts on.
    """
    if anchor is None:
        return int(option["dte"])
    try:
        return (date.fromisoformat(contract.date_of(option["key"])) - anchor).days
    except ValueError:
        return int(option["dte"])


def restated(options: list[dict[str, Any]],
             anchor: date | None) -> list[dict[str, Any]]:
    """`expiry_options` with every countdown measured from `anchor`.

    SHARED BY THE TWO PLACES A COUNTDOWN IS SHOWN — the Gamma tab's expiry
    picker and Calendar Edge's front/back dropdowns. It began as four lines
    inside `expiry_board`, and Chandan asked for the same behaviour on the
    pair dropdowns the next day; a second copy would have been a second
    chance for the number and the sentence it is written into to disagree.

    BOTH `dte` AND `label` MOVE TOGETHER. `label` is prose with the countdown
    baked into it -- "Friday, Sep 18, 2026 (14 DTE)" -- so restating the
    number and leaving the sentence puts two different countdowns for one
    contract on one line. `exp_label` is handed the WHOLE re-based map rather
    than one value, because that is the argument it takes.

    Returns new dictionaries; the input is not modified.
    """
    dte_by_key = {o["key"]: _countdown(o, anchor) for o in options}
    return [{**o, "dte": dte_by_key[o["key"]],
             "label": exp_label(o["key"], dte_by_key)} for o in options]


def expiry_board(chain_df: pd.DataFrame, spot: float, *,
                 today: date | None = None) -> list[dict[str, Any]]:
    """`expiry_options` plus everything the Gamma tab's expiry picker shows.

    WHY THE PICKER NEEDS MORE THAN A LABEL. Chandan asked for a picker that
    shows each expiry's call and put gamma beside it, so the choice can be
    made by looking rather than by selecting one, reading the chart, and
    selecting the next. That turns the control into a small table, and every
    column in it is a figure or a rule -- a total, a magnitude suffix, a
    weekday name, whether a date is a monthly, which filter window it falls
    in. None of those may be re-derived in the browser.

    THE FILTERS ARE MEMBERSHIP, NOT A BOUND. Each row carries the list of
    filter windows it belongs to (`core.contract.EXPIRY_FILTERS`), so the tab
    filters with a set lookup. Handing over four cutoff dates instead would
    have put a date comparison in TypeScript, evaluated in the VIEWER'S
    timezone -- the same class of bug DEBT-030 records for timestamps.

    `put_gex` is positive here, as everywhere in core/gex.py; `put_label`
    carries the minus sign because the picker draws puts as the negative
    side, the same convention the "Call vs Put" panel uses. The SIGN CHOICE
    IS MADE HERE rather than in the tab for the ordinary reason: it is a
    presentation rule with a formula behind it.

    `separate_options` is not offered. A caller wanting one expiry's exposure
    asks `/mission/gamma?expiry=...`; this is the index, not the answer.
    """
    options = expiry_options(chain_df)
    if not options:
        return []

    # THE DATE EVERY COUNTDOWN AND EVERY WINDOW IS MEASURED FROM. Defaults to
    # the snapshot's own date, which is what a replayed board needs; the API
    # passes the reader's own date instead when the board on screen is the
    # current one. See core.expiry.countdown_anchor for which is which and
    # why the choice cannot be made in the browser.
    #
    # ONE ANCHOR FOR ALL THREE — the number, the label it is written into, and
    # the filter windows. They were previously three uses of one variable and
    # so could not disagree; they must still not, because a row reading "2
    # DTE" while sitting outside "This Week" is a contradiction on one line.
    anchor = today or snapshot_date(chain_df)
    options = restated(options, anchor)
    totals = gex.by_expiry(chain_df, spot).set_index("expiry")

    board = []
    for opt in options:
        key = opt["key"]
        expiry_date = contract.date_of(key)
        call = float(totals["call_gex"].get(key, 0.0)) if not totals.empty else 0.0
        put = float(totals["put_gex"].get(key, 0.0)) if not totals.empty else 0.0
        board.append({
            **opt,
            "date": expiry_date,
            # "Wed, Sep 9" -- the picker's right-hand caption. Short because
            # it sits beside a DTE that already says which week it is.
            # Built rather than strftime'd: "%-d" strips the leading zero on
            # Linux and raises on Windows, and this runs on both.
            "day_label": _day_label(expiry_date),
            # `opt` already carries the re-based countdown and label —
            # `restated` above — so only the caption built FROM the number
            # is added here, and it reads the re-based one.
            "dte_label": f"{opt['dte']} DTE",
            "is_third_friday": contract.is_third_friday(expiry_date),
            "is_am": contract.is_am(key),
            "filters": contract.filters_for(key, anchor) if anchor else [],
            "call_gex": call,
            "put_gex": put,
            "call_label": fmt_money(call),
            "put_label": fmt_money(-put),
        })
    return board


def delta_exposure(chain_df: pd.DataFrame, spot: float,
                   expiry: gex.ExpiryScope = None) -> dict[str, Any]:
    """Delta exposure by strike — the tab's fourth view.

    MISSED WHEN VANNA AND CHARM WERE SERVED on 2026-09-06, and worth recording
    as a mistake rather than quietly added: the M6 plan was updated that day to
    say the Gamma Exposure tab was fully covered, and it was not. Delta
    Exposure had been a view on that tab since long before, drawn from
    `gex.dex_by_strike`, and no endpoint returned it. The claim was checked
    against the two views that had just been added rather than against the
    list of views the tab actually has.

    NO `assumptions` BLOCK, unlike vanna and charm, and its absence is the
    honest signal: this weights `delta` as the broker sent it, so there is no
    rate, no yield and no fraction of a day standing behind the number. An
    empty assumptions block would suggest the question had been considered and
    the answer was "none", which is a different claim from "not applicable".

    `net_dex` is the CHAIN's net delta, not an inferred dealer inventory — see
    core.gex.dex_by_strike for why the dealer sign is deliberately NOT applied
    here when it is applied to gamma.
    """
    frame = gex.dex_by_strike(chain_df, spot, expiry=expiry)
    return {
        "measure": "delta",
        "expiry": expiry,
        "spot": spot,
        "by_strike": frame,
    }


def second_order_exposure(chain_df: pd.DataFrame, spot: float, measure: str,
                          snapshot_ts: str, *, r: float, q: float,
                          display_tz: str,
                          expiry: gex.ExpiryScope = None) -> dict[str, Any]:
    """Vanna or charm by strike — the two views added to the tab on 2026-09-06.

    WHY THIS IS NOT PART OF gamma_exposure. Each of these walks the entire
    chain through iv_engine one contract at a time, so computing all three to
    answer a request for one would triple the work for two frames nobody asked
    for. views/gex.py takes the same decision for the same reason and says so
    ("Only the selected one is built").

    `r` and `q` ARE REQUIRED, not defaulted, and that is deliberate at every
    level: they are the assumption the whole measure rests on. They are not in
    the record and are not recoverable from it — put-call parity implied a 65%
    rate off these mid-prices, and inverting the stored delta and gamma
    implied +/-200% because `gamma` is saved to three decimals — so they are
    stated constants in config.py. A default here would hide that at the one
    place a reader looks to find out what a number assumed.

    THE SNAPSHOT'S CLOCK IS AN ARGUMENT because charm needs the fraction of
    the day left before the close, and on a 0DTE contract that fraction is the
    difference between a blank column and the biggest bar on the chart. See
    core.gex.day_remainder, which both this and the page now call — it was a
    private function on the page until this endpoint needed it.

    The response echoes `measure` and `expiry` for the same reason
    gamma_exposure echoes the scope: a vanna figure and a charm figure are
    different numbers of similar size, and on a screen they look identical.
    """
    if measure not in ("vanna", "charm"):
        raise ValueError(f"second_order_exposure got {measure!r}, "
                         f"expected 'vanna' or 'charm'")

    remainder = gex.day_remainder(snapshot_ts, display_tz)
    kwargs = dict(r=r, q=q, expiry=expiry, day_remainder=remainder)

    if measure == "vanna":
        frame = gex.vanna_by_strike(chain_df, spot, **kwargs)
        totals = gex.second_order_summary(frame, None)
    else:
        frame = gex.charm_by_strike(chain_df, spot, **kwargs)
        totals = gex.second_order_summary(None, frame)

    return {
        "measure": measure,
        "expiry": expiry,
        "spot": spot,
        # STATED, NOT IMPLIED. Two of these three are assumptions and the
        # third is a rounding of the clock; a reader comparing this against a
        # vendor's vanna needs to know all three before the difference means
        # anything. Returning them costs nothing and is the only way the
        # response is self-describing.
        "assumptions": {
            "risk_free_rate": r,
            "dividend_yield": q,
            "day_remainder": remainder,
            "snapshot_timestamp": snapshot_ts,
        },
        "summary": totals,
        "by_strike": frame,
    }

# ─────────────────────────────────────────────────────────────────────────────
# The Mission Control cards — extracted so both front ends share ONE definition
#
# The note at the top of this module said the panel was not served because
# doing so would mean importing the page into a server or copying five hundred
# lines into a second home that would immediately start drifting from the
# first, and that extracting it "belongs in its own task rather than being
# smuggled into this one". This is that task, for the half the cards need.
#
# WHAT MADE IT POSSIBLE, and it is not that the code got easier: neither body
# below ever used streamlit. Only the module around them did, through its
# @st.cache_data wrappers. Moving them changes no arithmetic — it moves them
# under the layer boundary so api/ can reach them without reaching the page.
# services/mission_control.py now calls DOWN into these, which is what stops
# the cards on the Streamlit tab and the cards served over HTTP from drifting.
#
# THE NON-ATM PANEL followed on 2026-09-06 and is below (`non_atm_panel`),
# closing DEBT-041. It needed a second seam this one did not: the registry it
# reads is a sidecar file, so `create_app` grew a `state_dir` the way it
# already had a `db_path`. What is left is DEBT-042 — the served panel READS
# that registry and never advances it.
# ─────────────────────────────────────────────────────────────────────────────

def candidate_signals(front_raw: str, back_raw: str,
                      put_strike: float, call_strike: float,
                      days: int = 1, *, db_path: str) -> dict | None:
    """
    Phase B — for ONE candidate combo, compute:
      duration   — how long the gap has stayed continuously >= 5, ending now
                   (None if not currently eligible)
      eta_minutes — linear projection of minutes until gap crosses 5,
                   based on the slope of the last few snapshots
                   (None if flat/declining — no point showing a bogus ETA)
      spark      — unicode sparkline of the recent gap trajectory
      trend_up   — whether the last 3 readings are monotonically increasing
    Returns None if there isn't enough history to say anything useful.

    db_path is REQUIRED, unlike the services/ version this was lifted from
    (DEBT-027 / ADR-033 gave that one a config.DB_PATH default so existing
    callers needed no edit). A default is wrong in this layer: the server
    answers for whichever database create_app was handed, and a forgotten
    argument would silently answer from the production record instead.
    """
    rows = db.get_transform_mark_history(
        db_path,
        front_raw, back_raw, call_strike, put_strike, days=days,
    )
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["timestamp"] = (
        pd.to_datetime(df["snapshot_timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
        .dt.tz_localize(None)  # naive wall-clock: required by Plotly rangebreaks
    )
    df = add_mark_columns(df)  # one definition -- see its docstring
    df = df.sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        return None

    # Duration active — trailing contiguous streak where gap >= 5, ending now
    flag = (df["gap"] >= TSCAN_THRESHOLD).tolist()
    duration = None
    if flag and flag[-1]:
        i = len(flag) - 1
        while i > 0 and flag[i - 1]:
            i -= 1
        duration = df["timestamp"].iloc[-1] - df["timestamp"].iloc[i]

    # ETA — slope of the last up-to-6 readings, projected to threshold
    eta_minutes = None
    tail = df.tail(6).dropna(subset=["gap"])
    tail = tail.drop_duplicates(subset=["timestamp"])
    if len(tail) >= 3:
        x_min = ((tail["timestamp"] - tail["timestamp"].iloc[0])
                 .dt.total_seconds() / 60.0).to_numpy()
        y_gap = tail["gap"].to_numpy()
        # polyfit needs at least 2 distinct x values and finite data, or
        # the underlying SVD can fail to converge (degenerate design matrix).
        if (
            np.isfinite(x_min).all() and np.isfinite(y_gap).all()
            and np.ptp(x_min) > 0
        ):
            try:
                slope, _ = np.polyfit(x_min, y_gap, 1)
            except np.linalg.LinAlgError:
                slope = None
            if slope is not None:
                current_gap = float(y_gap[-1])
                if slope > 0.01 and current_gap < TSCAN_THRESHOLD:
                    eta_minutes = (TSCAN_THRESHOLD - current_gap) / slope

    spark = sparkline(df["gap"].tail(12).tolist())
    trend_up = bool(df["gap"].tail(3).is_monotonic_increasing) if len(df) >= 3 else False

    return dict(duration=duration, eta_minutes=eta_minutes, spark=spark, trend_up=trend_up)


# How many candidates get Phase B history. The cost of the panel is
# proportional to this, and Phase B is the expensive half.
MC_HISTORY_CAP = 20


def approaching_panel(all_combos: pd.DataFrame, *, db_path: str,
                      cap: int = MC_HISTORY_CAP) -> dict[str, Any]:
    """The Approaching cards and the Likely Next list, from one sweep.

    Takes the SWEEP rather than a chain, so the caller decides how it was
    computed: the page hands in one already memoised across its tabs, the
    server hands in one cached on the snapshot. Computing 21 offsets in here
    would quietly double the most expensive thing either of them does.
    """
    if all_combos.empty:
        return {"approaching_cards": [], "likely_next": [], "n_approaching": 0}

    approaching_df = all_combos[
        (all_combos["Transform Diff"] >= APPROACHING_LOW)
        & (all_combos["Transform Diff"] < TSCAN_THRESHOLD)
    ].copy()
    n_approaching = len(approaching_df)

    # Rank for the panel BEFORE capping — otherwise asymmetric opportunities
    # sitting just below the top-by-raw-gap rows would get starved out of
    # the (necessarily limited, for cost reasons) Phase B history treatment.
    approaching_df = rank_for_panel(approaching_df)

    def _build_cards(df: pd.DataFrame, cap: int) -> list[dict]:
        cards = []
        for _, row in df.head(cap).iterrows():
            front_raw = row["Front Expiry"].split(" ")[0]
            back_raw  = row["Back Expiry"].split(" ")[0]
            put_s     = float(row["Put Strike"])
            call_s    = float(row["Call Strike"])
            sig = candidate_signals(front_raw, back_raw, put_s, call_s,
                                    db_path=db_path) or {}
            cards.append(dict(
                front_raw=front_raw, back_raw=back_raw,
                front_label=row["Front Expiry"], back_label=row["Back Expiry"],
                put_strike=put_s, call_strike=call_s,
                gap=float(row["Transform Diff"]),
                iv_ratio=row.get("IV Ratio"),
                duration=sig.get("duration"),
                # BOTH FORMS, ON PURPOSE. The raw values stay because a chart
                # or a sort needs numbers; the *_label pair is here because
                # `duration` reaches JSON as ISO-8601 ("PT2H12M") and
                # `eta_minutes` as a float, and turning either into the text
                # the card shows is core/format.py's job, not TypeScript's.
                # See docs/m6_migration_plan.md: no formula in the new
                # language, and a rounding rule is a formula.
                duration_label=fmt_duration(sig.get("duration")),
                eta_minutes=sig.get("eta_minutes"),
                eta_label=fmt_eta(sig.get("eta_minutes")),
                spark=sig.get("spark", "─"),
                trend_up=sig.get("trend_up", False),
            ))
        return cards

    approaching_cards = _build_cards(approaching_df, cap)

    # "Likely Next" — only candidates with a computable rising-trend ETA.
    # Same asymmetric-first principle, ETA ascending within each tier.
    likely_next = sorted(
        [c for c in approaching_cards if c["eta_minutes"] is not None],
        key=lambda c: (c["put_strike"] == c["call_strike"], c["eta_minutes"]),
    )

    return {
        "approaching_cards": approaching_cards,
        "likely_next": likely_next,
        "n_approaching": n_approaching,
    }


# ─────────────────────────────────────────────────────────────────────────────
# The non-ATM opportunities panel — DEBT-041, the third card grid
#
# WHY THIS ONE CAME DOWN LAST, and it is not that it is harder. The other two
# grids are a slice of the sweep: hand them a chain and they are computable
# from nothing else. This one is built from the PERSISTED REGISTRY —
# eligible_history.json, ~1,300 entries — which is how a card can say "seen 4x"
# or show a setup that was live an hour ago while nobody was watching. Serving
# it therefore meant the server had to learn a second process-level fact
# (which state directory), and that is a decision rather than a move, which is
# why M6.3 stopped short of it and opened DEBT-041 instead.
#
# THE REGISTRY IS READ HERE, NEVER WRITTEN. `api/` is read-only by design and
# its one documented exception is the "New" registry (api/__init__.py); this is
# not a second one. The consequence is real and must not be discovered later:
# the registry only advances when the STREAMLIT dashboard runs, because
# `_update_eligible_history` fires inside its snapshot-cached core. A front end
# built on this endpoint alone sees a registry frozen at whenever the page was
# last open. That is the honest behaviour for a reader, and closing it is a
# separate decision about where the upsert should live — see DEBT-042.
#
# MOVED VERBATIM from services/mission_control._build_non_atm_panel, with
# exactly three changes: db_path is explicit, Phase B calls the plain
# candidate_signals rather than the page's memoised wrapper, and the return is
# a named payload rather than a positional triple. The arithmetic, the
# four-tier sort and the never-empty fallback are untouched — and the twelve
# golden tests in tests/test_mc_pipeline_golden.py still reach it through the
# page's wrapper, so they measure this body.
# ─────────────────────────────────────────────────────────────────────────────

def non_atm_panel(non_atm_current: pd.DataFrame, registry: dict,
                  dte_by_expiry: dict, window_start: str | None,
                  snapshot_ts: str, *, db_path: str,
                  cap: int = MC_HISTORY_CAP,
                  min_display: int = 6) -> dict[str, Any]:
    """
    The curated "non-ATM opportunities" panel — built from the persisted
    registry, NOT a slice of the Scanner. Includes any combo that is
    currently >= threshold OR appears in the registry within the lookback
    window (i.e. crossed >= 5 at some point recently, even if it isn't
    right now).

    Ranking — a transparent, inspectable multi-key sort, not a blended
    score (same principle as rank_for_panel above):
      Tier 1 — currently live (>= 5 right now) outranks historical-only;
               an opportunity you can act on today beats a past one.
      Tier 2 — rank_gap descending: current gap for live combos, peak gap
               (max_gap ever observed) for historical-only ones.
      Tier 3 — hit_count descending — directly answers "which strikes
               repeatedly become transformable," not just "which spiked once."
      Tier 4 — most recent crossing first, as the final tiebreak.

    Never-empty guarantee: if fewer than min_display combos fall within the
    selected lookback window, the remaining slots are filled with the most
    recent registry entries regardless of window — flagged via
    outside_lookback=True so the UI can make that explicit rather than
    silently showing stale data as if it were in-range. Only true cold start
    (an empty registry — nothing has ever crossed threshold, automated or
    backfilled) can still produce an empty panel.

    Returns (capped_cards, in_window_total, fallback_used_count).
    """
    # `window_start` arrives already resolved to the date of the Nth most
    # recent SESSION ON RECORD — it is not computed here, and not derived from
    # a day count. Subtracting a Timedelta made "20D" fifteen sessions on a
    # Friday while the reader was told twenty (BUG-035); this panel and the
    # charts beneath it carry one label, so they must mean one window.
    #
    # It is a parameter rather than a lookup because resolving it needs the
    # database, and a panel that reads config.DB_PATH to decide its own window
    # cannot be exercised without the real record beside it.
    if window_start is not None and not isinstance(window_start, str):
        # This argument used to be a day count, and pd.Timestamp accepts an
        # int — as nanoseconds since 1970. A caller left on the old signature
        # would therefore get a cutoff in 1970 and a panel that windowed
        # nothing, silently and while looking entirely healthy.
        raise TypeError(
            f"window_start must be a date string, not {type(window_start).__name__}"
        )
    try:
        cutoff = pd.Timestamp(window_start) if window_start else None
    except ValueError:
        cutoff = None

    current_lookup: dict[str, dict] = {}
    if not non_atm_current.empty:
        for _, row in non_atm_current.iterrows():
            front_raw = row["Front Expiry"].split(" ")[0]
            back_raw  = row["Back Expiry"].split(" ")[0]
            # `pair_key`, not a second copy of its format. The comment above
            # that function explains why a key differing by a decimal point
            # would make every pair look new forever.
            k = pair_key(front_raw, back_raw,
                         row["Put Strike"], row["Call Strike"])
            current_lookup[k] = dict(
                gap=float(row["Transform Diff"]), iv_ratio=row.get("IV Ratio"),
            )

    def _card_from_entry(key: str, entry: dict, last_seen_ts: pd.Timestamp,
                          outside_lookback: bool) -> dict:
        cur = current_lookup.get(key)
        is_live  = cur is not None and cur["gap"] >= TSCAN_THRESHOLD
        rank_gap = cur["gap"] if is_live else entry["max_gap"]
        front_raw, back_raw = entry["front_raw"], entry["back_raw"]
        # One table, checked and looked up. Before ADR-034 the guard read the
        # parameter and _exp_label read a global of the same name.
        front_label = (exp_label(front_raw, dte_by_expiry)
                       if front_raw in dte_by_expiry else front_raw)
        back_label  = (exp_label(back_raw, dte_by_expiry)
                       if back_raw in dte_by_expiry else back_raw)
        try:
            ago_str = fmt_duration(pd.Timestamp(snapshot_ts) - last_seen_ts) + " ago"
        except (ValueError, TypeError):
            ago_str = "—"
        return dict(
            # SHIPPED SO THE CLIENT NEED NOT REBUILD IT. `/mission/new`
            # answers in these keys, and a React card has to know whether it
            # is one of them. Re-deriving the format in TypeScript would put
            # a formula in the new language — the one thing
            # docs/m6_migration_plan.md forbids — and it is exactly the kind
            # that fails silently: a mismatched key does not error, it just
            # reports nothing as new, forever.
            key=key,
            front_raw=front_raw, back_raw=back_raw,
            front_label=front_label, back_label=back_label,
            put_strike=entry["put_strike"], call_strike=entry["call_strike"],
            iv_ratio=(cur["iv_ratio"] if cur else entry.get("iv_ratio")),
            is_live=is_live,
            current_gap=(cur["gap"] if cur else None),
            max_gap=entry["max_gap"],
            gap=rank_gap,
            hit_count=entry["hit_count"],
            last_seen=last_seen_ts,
            last_seen_ago=ago_str,
            outside_lookback=outside_lookback,
        )

    candidates, used_keys = [], set()
    for key, entry in registry.items():
        try:
            last_seen_ts = pd.Timestamp(entry["last_seen"])
        except (ValueError, TypeError, KeyError):
            continue
        if cutoff is not None and last_seen_ts < cutoff:
            continue
        candidates.append(_card_from_entry(key, entry, last_seen_ts, outside_lookback=False))
        used_keys.add(key)

    candidates.sort(key=lambda c: (
        not c["is_live"], -c["gap"], -c["hit_count"], -c["last_seen"].timestamp()
    ))
    in_window_total = len(candidates)

    # Never-empty fallback: pull in the most recent entries from OUTSIDE the
    # selected window, clearly flagged, rather than show nothing.
    fallback_used = 0
    if len(candidates) < min_display:
        fallback_raw = []
        for key, entry in registry.items():
            if key in used_keys:
                continue
            try:
                last_seen_ts = pd.Timestamp(entry["last_seen"])
            except (ValueError, TypeError, KeyError):
                continue
            fallback_raw.append((key, entry, last_seen_ts))
        fallback_raw.sort(key=lambda t: t[2].timestamp(), reverse=True)

        needed = max(cap - len(candidates), min_display - len(candidates))
        for key, entry, last_seen_ts in fallback_raw[:needed]:
            candidates.append(_card_from_entry(key, entry, last_seen_ts, outside_lookback=True))
            fallback_used += 1

    capped = candidates[:cap]

    # Phase B (duration/spark/eta) only for the small final, capped set.
    # On the page this went through a @st.cache_data wrapper; here it is the
    # plain function and the CALLER decides what to memoise — the server keys
    # its cache on the snapshot, the page on a 60-second TTL.
    for c in capped:
        sig = candidate_signals(c["front_raw"], c["back_raw"],
                                c["put_strike"], c["call_strike"],
                                db_path=db_path) or {}
        c["duration"]    = sig.get("duration") if c["is_live"] else None
        c["eta_minutes"] = sig.get("eta_minutes")
        # See the same pair in _build_cards above for why the labels travel.
        c["duration_label"] = fmt_duration(c["duration"])
        c["eta_label"]      = fmt_eta(c["eta_minutes"])
        c["spark"]       = sig.get("spark", "─")
        c["trend_up"]    = sig.get("trend_up", False)

    return {
        "cards": capped,
        "in_window_total": in_window_total,
        "fallback_used": fallback_used,
    }
