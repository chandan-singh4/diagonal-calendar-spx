"""M4.3 — the answers, not just the rows: scanner, gamma exposure, and "New".

WHAT IS SERVED HERE AND WHAT IS NOT, because the difference is a real
constraint rather than an oversight.

SERVED: the transform scanner and the gamma/delta exposure work. Both live in
`core/`, which is pure by rule — no database, no page, function of its
arguments — so this module loads a chain through `dataaccess/` and calls them.
Nothing is reimplemented.

NOT SERVED: the Mission Control PANEL — the cards, the sparklines, the
"likely next" list, the duration-of-gap figures. That logic is
`services/mission_control.py`, 528 lines of it, and every path through it
imports streamlit. `api/` may not import `services/` (see api/__init__.py, and
the guard in tests/test_layering.py), so serving the panel would mean either
importing the page into a server or copying five hundred lines into a second
home that would immediately start drifting from the first. Both are worse than
not serving it yet. Extracting the panel into a layer both callers can share
is real work of the kind M2 did, and it belongs in its own task rather than
being smuggled into this one.

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

import pandas as pd

import db
from core import gex
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
