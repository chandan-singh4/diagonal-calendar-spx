"""Mission Control — cross-sectional opportunity discovery.

TWO PHASES, KEPT CHEAP ON PURPOSE:

  Phase A (every refresh, every offset, all expiry pairs) — pure in-memory
    pandas against the chain already loaded. Classifies every combo as
    Eligible (gap >= 5), Approaching (gap in [APPROACHING_LOW, 5)), or
    neither. This is the only part that touches "thousands of rows."

  Phase B (every refresh, but ONLY for the small Eligible+Approaching set —
    typically tens of rows, capped at _MC_HISTORY_CAP) — pulls per-combo
    history to compute how long a gap has been active and whether it is
    trending toward the threshold.

Running Phase B against all combos would be the wrong trade: this keeps cost
proportional to "things that matter," not "things that exist."

WHY THIS IS IN services/ AND NOT core/. It reads the database and it reads
`st.session_state`. The session_state part is not incidental — the "New"
badge is a diff against what the PREVIOUS snapshot showed, so the state has
to persist across reruns, and a cached function cannot hold it (see
_run_mission_control). That rules out core/ absolutely.

THE CACHE BOUNDARY IS LOad-BEARING, AND SUBTLE. `_compute_mc_core` is cached
on snapshot_id and therefore SKIPS ITS BODY ENTIRELY on a hit — so no
session_state write may live inside it, or it would silently stop happening
the moment the cache started returning results. The registry file write IS
fine there, and is deliberate: it makes the upsert fire once per new
snapshot rather than on every rerun.

Moved here whole in M2 step 2.5. Pinned by tests/test_mc_pipeline_golden.py,
which found it through tests/app_loader.py by NAME and needed no change
beyond adding services/ to that loader's search path.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
import db
from api import computed

# WHAT IS NO LONGER IMPORTED HERE, and why the list shrank rather than a name
# being missed. Two milestones moved card-building bodies down into
# api/computed.py — M6.3 took approaching_panel, DEBT-041 took the non-ATM
# panel — and each left its collaborators imported by a module that had
# stopped calling them. Cleared together, in the change that made the last two
# of them dead: numpy, sparkline, rank_for_panel and APPROACHING_LOW were
# already unreachable before this edit, exp_label and fmt_duration became so
# in it, and leaving the older four would make the next reader unable to tell
# which move abandoned what. Nothing imports any of them THROUGH this module —
# app.py takes only _backfill_eligible_history and _run_mission_control.
from core.ranking import card_key
from core.scanner import TSCAN_THRESHOLD, scan_all_offsets
from services.loaders import compute_transform_scanner
from services.sidecars import (
    _ELIGIBLE_HISTORY_RETENTION_DAYS,
    _load_eligible_history,
    _save_eligible_history,
)


def _update_eligible_history(non_atm_df: pd.DataFrame, snapshot_ts: str) -> dict:
    """
    Upsert every non-ATM combo currently >= threshold into the registry.
    Only ever called from inside the snapshot-cached Mission Control core
    (see _compute_mc_core), so this fires once per NEW snapshot — not on
    every tab click or widget interaction.
    """
    registry = _load_eligible_history()
    if not non_atm_df.empty:
        eligible_now = non_atm_df[non_atm_df["Transform Diff"] >= TSCAN_THRESHOLD]
        for _, row in eligible_now.iterrows():
            front_raw = row["Front Expiry"].split(" ")[0]
            back_raw  = row["Back Expiry"].split(" ")[0]
            put_s     = int(row["Put Strike"])
            call_s    = int(row["Call Strike"])
            gap       = float(row["Transform Diff"])
            iv_ratio  = row.get("IV Ratio")
            key = f"{front_raw}|{back_raw}|{put_s}|{call_s}"
            prev = registry.get(key, {})
            registry[key] = dict(
                front_raw=front_raw, back_raw=back_raw,
                put_strike=put_s, call_strike=call_s,
                iv_ratio=(None if pd.isna(iv_ratio) else iv_ratio),
                first_seen=prev.get("first_seen", snapshot_ts),
                last_seen=snapshot_ts,
                last_gap=gap,
                max_gap=max(prev.get("max_gap", 0.0), gap),
                hit_count=prev.get("hit_count", 0) + 1,
            )

    # Prune anything that hasn't been seen in a while so the file stays small.
    try:
        cutoff = pd.Timestamp(snapshot_ts) - pd.Timedelta(days=_ELIGIBLE_HISTORY_RETENTION_DAYS)
        registry = {
            k: v for k, v in registry.items()
            if pd.Timestamp(v.get("last_seen", snapshot_ts)) >= cutoff
        }
    except (ValueError, TypeError):
        pass

    _save_eligible_history(registry)
    return registry

def _backfill_eligible_history(front_raw: str, back_raw: str,
                                put_strike: float, call_strike: float,
                                gap_df: pd.DataFrame) -> None:
    """
    Opportunistic registry backfill — called from Calendar Edge whenever a
    user manually selects a strike/expiry pair and pulls its mark history.
    The Mission Control sweep only checks a fixed symmetric grid of offsets
    (see SWEEP_OFFSETS), so a genuinely asymmetric or off-grid combo can
    show a real >= 5 crossing in its history without ever entering the
    registry through the automated scan. This catches it the moment someone
    actually looks — zero extra DB calls, since gap_df is already fetched
    for the Diagonal vs. Transform Order Mark chart.
    """
    if gap_df.empty or "gap" not in gap_df.columns:
        return
    crossings = gap_df[gap_df["gap"] >= TSCAN_THRESHOLD].copy()
    if crossings.empty:
        return

    # gap_df's timestamps come from Calendar Edge converted to
    # config.DISPLAY_TIMEZONE and then stripped to NAIVE ET wall-clock (Plotly
    # rangebreaks require naive timestamps). The registry stores naive UTC
    # strings everywhere else (snapshot_ts from the live scan path). Normalize
    # to naive UTC here so the two paths stay comparable.
    _ts = crossings["timestamp"]
    if getattr(_ts.dt, "tz", None) is not None:
        crossings["timestamp"] = _ts.dt.tz_convert("UTC").dt.tz_localize(None)
    else:
        # Naive ET wall-clock in, naive UTC out — localize back to ET first,
        # otherwise ET times would be stored as UTC (a silent 4/5-hour skew).
        crossings["timestamp"] = (
            _ts.dt.tz_localize(config.DISPLAY_TIMEZONE)
            .dt.tz_convert("UTC").dt.tz_localize(None)
        )

    registry = _load_eligible_history()
    key = f"{front_raw}|{back_raw}|{int(put_strike)}|{int(call_strike)}"
    prev = registry.get(key, {})

    first_seen_ts = crossings["timestamp"].min()
    last_seen_ts  = crossings["timestamp"].max()
    if prev.get("first_seen"):
        first_seen_ts = min(first_seen_ts, pd.Timestamp(prev["first_seen"]))
    if prev.get("last_seen"):
        last_seen_ts = max(last_seen_ts, pd.Timestamp(prev["last_seen"]))

    registry[key] = dict(
        front_raw=front_raw, back_raw=back_raw,
        put_strike=int(put_strike), call_strike=int(call_strike),
        iv_ratio=prev.get("iv_ratio"),
        first_seen=str(first_seen_ts),
        last_seen=str(last_seen_ts),
        last_gap=float(crossings["gap"].iloc[-1]),
        max_gap=max(prev.get("max_gap", 0.0), float(crossings["gap"].max())),
        # Don't double-count if the automated sweep already had this combo —
        # approximate by taking whichever count is larger rather than summing.
        hit_count=max(prev.get("hit_count", 0), len(crossings)),
    )
    _save_eligible_history(registry)

# ═══════════════════════════════════════════════════════════════════════════════
# MISSION CONTROL — cross-sectional opportunity discovery
#
# Two-phase design, kept cheap on purpose:
#   Phase A (every refresh, every offset, all expiry pairs) — pure in-memory
#     pandas against the chain already loaded. Classifies every combo as
#     Eligible (gap >= 5), Approaching (gap in [APPROACHING_LOW, 5)), or
#     neither. This is the only part that touches "thousands of rows."
#   Phase B (every refresh, but ONLY for the small Eligible+Approaching set —
#     typically tens of rows, capped at _MC_HISTORY_CAP) — pulls per-combo
#     history via db.get_transform_mark_history() to compute how long a gap
#     has been active and whether it's trending toward the threshold.
# Running Phase B against all combos would be the wrong trade — this keeps
# cost proportional to "things that matter," not "things that exist."
# ═══════════════════════════════════════════════════════════════════════════════

# The Phase A thresholds (TSCAN_THRESHOLD, APPROACHING_LOW, SWEEP_OFFSETS)
# and scan_all_offsets itself moved to core/scanner.py. _MC_HISTORY_CAP stays
# here: it caps how much DATABASE work Phase B does, which is not a core/
# concern and moves with the data layer.
_MC_HISTORY_CAP   = 20    # max candidates per tier to run Phase B history on

@st.cache_data(ttl=60, show_spinner=False, max_entries=64)
def _candidate_signals(front_raw: str, back_raw: str,
                        put_strike: float, call_strike: float,
                        days: int = 1, *, db_path=None) -> dict | None:
    """Phase B for one candidate combo. The body now lives in api/computed.py.

    Moved down a layer on 2026-09-05 so the server can serve the same cards
    the tab draws (M6). Nothing about the arithmetic changed. This wrapper
    stays for the one thing the moved version deliberately dropped: the
    config.DB_PATH default from DEBT-027 / ADR-033, which is right for a page
    that has exactly one database and wrong for a server handed whichever
    database create_app was given.
    """
    return computed.candidate_signals(
        front_raw, back_raw, put_strike, call_strike, days,
        db_path=db_path if db_path is not None else config.DB_PATH,
    )


# rank_for_panel and card_key moved to core/ranking.py (ADR-032).


@st.cache_data(ttl=120, show_spinner="Scanning transform opportunities…", max_entries=3)
def _compute_mc_core(_chain_df: pd.DataFrame, spx_price: float,
                      snapshot_id: int, snapshot_ts: str) -> dict:
    """
    Phase A + Phase B, cached as a unit on snapshot_id. No session_state
    access here — cached functions skip their body entirely on a cache hit,
    so any session_state writes would silently stop happening the moment
    this starts returning cached results. The "New" diff (which needs
    session_state) is handled by the uncached _run_mission_control wrapper
    below, outside this cache boundary.

    Also updates the persisted eligibility registry (eligible_history.json)
    for every non-ATM combo currently >= threshold — a plain file write is
    fine inside a cached function (unlike session_state), and since the
    function body only runs on a cache miss, this naturally fires once per
    NEW snapshot rather than on every rerun.
    """
    chain_df = _chain_df
    # compute= is not optional here in production: it hands the sweep the
    # memoised scanner so the 21 offsets share saved results with the Scanner
    # tab. Without it every rerun recomputes all 21. See core/scanner.py.
    all_combos = scan_all_offsets(chain_df, spx_price, snapshot_id,
                                   compute=compute_transform_scanner)
    if all_combos.empty:
        return dict(approaching_cards=[], likely_next=[], n_approaching=0,
                     non_atm_current=pd.DataFrame(), registry={})

    non_atm_current = all_combos[all_combos["Put Strike"] != all_combos["Call Strike"]].copy()
    registry = _update_eligible_history(non_atm_current, snapshot_ts)

    panel = computed.approaching_panel(all_combos, db_path=config.DB_PATH,
                                       cap=_MC_HISTORY_CAP)
    approaching_cards = panel["approaching_cards"]
    likely_next       = panel["likely_next"]
    n_approaching     = panel["n_approaching"]

    return dict(
        approaching_cards=approaching_cards,
        likely_next=likely_next,
        n_approaching=n_approaching,
        non_atm_current=non_atm_current,
        registry=registry,
    )

def _build_non_atm_panel(non_atm_current: pd.DataFrame, registry: dict,
                          dte_by_expiry: dict, window_start: str | None,
                          snapshot_ts: str, cap: int = _MC_HISTORY_CAP,
                          min_display: int = 6):
    """The curated non-ATM panel. The body now lives in api/computed.py.

    Moved down a layer on 2026-09-06 (DEBT-041) so the server can serve the
    third card grid the other two already had. Nothing about the arithmetic,
    the four-tier sort or the never-empty fallback changed.

    THIS WRAPPER EARNS ITS KEEP TWICE, which is why it is not just deleted and
    the call sites repointed:

      * `config.DB_PATH`, exactly as `_candidate_signals` above — right for a
        page with one database, wrong for a server handed whichever database
        create_app was given.
      * The TUPLE. The moved version returns a named payload, because it is
        serialised to JSON where a positional triple has no field names.
        Unpacking it here keeps `(cards, in_window_total, fallback_used)` for
        the page — and, more to the point, for the twelve golden tests in
        tests/test_mc_pipeline_golden.py, which call this name and unpack
        three values. They now measure the moved body through this line, so
        the move is covered by the tests written before it rather than by new
        ones asserting the same things a second time.
    """
    panel = computed.non_atm_panel(
        non_atm_current, registry, dte_by_expiry, window_start, snapshot_ts,
        db_path=config.DB_PATH, cap=cap, min_display=min_display,
    )
    return panel["cards"], panel["in_window_total"], panel["fallback_used"]

@st.cache_data(show_spinner=False, max_entries=8)
def _build_non_atm_panel_cached(_non_atm_current: pd.DataFrame, _registry: dict,
                                 _dte_by_expiry: dict, lookback_days: int,
                                 snapshot_ts: str, snapshot_id: int):
    """The panel build, memoised on the snapshot (ENH-012).

    WHY THIS IS WORTH CACHING, with a number. `_build_non_atm_panel` walks the
    WHOLE registry and builds a card for every entry, then shows the top 20.
    The registry holds 1,286 entries as of 2026-09-05 and only grows. Measured
    in M5.0: 0.46s, on every click, on every tab — including tabs that never
    display the panel, because the header's Attention Strip needs the result.

    Nothing in the build is time-dependent: given the same registry, the same
    expiry map, the same lookback and the same snapshot, it returns the same
    cards. So it is computed once per (snapshot, lookback) and read thereafter.

    THE UNDERSCORES ARE LOAD-BEARING. A leading underscore tells st.cache_data
    not to hash that argument — required for the DataFrame and dicts, which are
    unhashable, and safe here only because all three are derived from the
    snapshot that IS in the key. `snapshot_id` is what makes the entry stale,
    and `lookback_days` is in the key because the reader can change it without
    a new snapshot arriving.

    NOT cached inside _compute_mc_core: this depends on lookback_days, which
    the reader changes from the header, and folding it in would recompute the
    whole Phase A/B sweep every time that dropdown moved.

    st.cache_data returns a copy per call, so the caller may safely stamp
    is_new onto the cards it gets back — which it does, immediately below.
    """
    return _build_non_atm_panel(_non_atm_current, _registry, _dte_by_expiry,
                                 db.session_window_start(config.DB_PATH,
                                                         lookback_days),
                                 snapshot_ts)


def _run_mission_control(chain_df: pd.DataFrame, spx_price: float,
                          snapshot_id: int, snapshot_ts: str,
                          dte_by_expiry: dict, lookback_days: int) -> dict:
    """
    Thin, UNCACHED wrapper around _compute_mc_core. Handles the cross-refresh
    "New" diff via session_state (which can't live inside a cached function)
    and builds the curated non-ATM panel from the registry + current state.
    On a cache hit (the common case — every tab click, every widget
    interaction within the same snapshot), the expensive Phase A/B work is
    skipped entirely; only cheap in-memory filtering/sorting runs here.
    """
    core = _compute_mc_core(chain_df, spx_price, snapshot_id, snapshot_ts)

    non_atm_panel, n_non_atm_in_window, n_fallback_used = _build_non_atm_panel_cached(
        core["non_atm_current"], core["registry"], dte_by_expiry,
        lookback_days, snapshot_ts, snapshot_id,
    )

    current_keys = {card_key(c) for c in non_atm_panel if c["is_live"]}

    # Only advance the "previous" comparison set when a NEW snapshot has
    # actually landed — otherwise every widget-triggered rerun within the
    # same snapshot would keep relabeling things as "new."
    _prev_snap_id = st.session_state.get("mc_prev_snapshot_id")
    if _prev_snap_id != snapshot_id:
        _prev_keys = st.session_state.get("mc_prev_eligible_keys", set())
        new_keys = current_keys - _prev_keys
        st.session_state["mc_prev_eligible_keys"] = current_keys
        st.session_state["mc_prev_snapshot_id"]   = snapshot_id
        st.session_state["mc_new_keys"]           = new_keys
    else:
        new_keys = st.session_state.get("mc_new_keys", set())

    for c in non_atm_panel:
        c["is_new"] = c["is_live"] and (card_key(c) in new_keys)
    for c in core["approaching_cards"]:
        c["is_new"] = False
    for c in core["likely_next"]:
        c["is_new"] = False

    n_live_non_atm = (
        int((core["non_atm_current"]["Transform Diff"] >= TSCAN_THRESHOLD).sum())
        if not core["non_atm_current"].empty else 0
    )
    best = next((c for c in non_atm_panel if c["is_live"]),
                non_atm_panel[0] if non_atm_panel else None)

    return dict(
        non_atm=non_atm_panel,
        n_non_atm_total=n_non_atm_in_window,
        n_fallback_used=n_fallback_used,
        n_eligible=n_live_non_atm,
        approaching=core["approaching_cards"],
        likely_next=core["likely_next"],
        n_approaching=core["n_approaching"],
        new_keys=new_keys,
        n_new=len(new_keys),
        best=best,
    )
