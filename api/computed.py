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

STILL NOT SERVED: the non-ATM panel — `_build_non_atm_panel`, the registry-
backed grid. It reads eligible_history.json through services/sidecars, which
is a second extraction rather than a line of the first. DEBT-041 has it, and
the Scanner tab is not fully replaceable until it is done.

WHAT THAT LEAVES, AND WHY IT IS THE USEFUL HALF ANYWAY. The panel is a way of
DISPLAYING the eligible set. The eligible set itself comes from the scanner
sweep, which is pure and is served below — and the "New" flag keys off exactly
that set: `_update_eligible_history` in services/ builds its registry key from
the scanner's own "front|back|put|call" at Transform Diff >= threshold, not
from anything the panel adds. So the flag can be computed here, correctly,
from the same source the page uses, without the page.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import config
import db
from core import gex
from core.format import sparkline
from core.ranking import rank_for_panel
from core.scanner import APPROACHING_LOW, TSCAN_THRESHOLD, scan_all_offsets


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
                   expiry: str | None = None) -> dict[str, Any]:
    """Gamma by strike, plus the flip level and the summary figures.

    `expiry` scopes to one contract by its DISPLAY KEY, so the third Friday's
    a.m. and p.m. contracts stay apart; without it this is the whole board.
    The scope matters enough that it is echoed back in the response — a gamma
    figure for one expiry and one for all twenty are different numbers and
    look identical on a screen.

    SUMMARY IS COMPUTED OVER EVERY STRIKE, matching views/gex.py. The ratio
    and sentiment are defined over the bars actually shown, and the dashboard
    shows the whole chain deliberately — a narrowable window gave a figure
    that changed under the reader every time they adjusted it. Serving a
    different scope here would make the API and the screen disagree for a
    reason neither could explain.
    """
    gex_df = gex.by_strike(chain_df, spot, expiry=expiry)
    return {
        "expiry": expiry,
        "spot": spot,
        "flip_strike": gex.flip_strike(gex_df),
        "summary": gex.summary(gex_df),
        "by_strike": gex_df,
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
# STILL NOT SERVED: the non-ATM panel (_build_non_atm_panel). It reads the
# eligible_history.json registry through services/sidecars, so it is a second
# extraction and not a line of this one. DEBT-041 has it.
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
    df["diagonal_mark"] = (
        df["back_call_mark"] + df["back_put_mark"]
        - df["front_call_mark"] - df["front_put_mark"]
    )
    df["transform_mark"] = (
        df["back_call_mark"] + df["back_put_mark"]
        - df["front_wing_call_mark"] - df["front_wing_put_mark"]
    )
    df["gap"] = df["transform_mark"] - df["diagonal_mark"]
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
                eta_minutes=sig.get("eta_minutes"),
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
