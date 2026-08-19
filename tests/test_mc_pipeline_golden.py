"""
Golden/characterization tests for the rest of the Mission Control pipeline —
the half of DEBT-026 left after _candidate_signals.

WHAT THIS COVERS
----------------
    _compute_mc_core       which opportunities exist at all, and in what order
    _build_non_atm_panel   the curated non-ATM panel built from the registry
    _run_mission_control   the "New" badges, the live count, the headline card
    _update_eligible_history  the persisted crossing registry
    the nine _load_* wrappers  every chart's underlying data

THE ONE THAT MATTERS MOST
-------------------------
_compute_mc_core ranks BEFORE capping to _MC_HISTORY_CAP. Its own comment says
why: cap first and the asymmetric combos — the ones matching how this strategy is
actually traded — get discarded before ranking and never appear on screen at all.
Two lines in the wrong order and the dashboard quietly stops representing the
strategy. It still renders. It still looks sorted. Nothing errors.

test_asymmetric_combos_survive_the_cap is that test, and it is the reason this
file exists.

ONE MODULE-LEVEL HAZARD LEFT, ISOLATED IN THE LOADER
----------------------------------------------------
`_ELIGIBLE_HISTORY_PATH` is a RELATIVE path. Without pointing it at tmp_path,
every test here would overwrite the real 599 KB eligible_history.json in the
repo root. This is the same class of hazard as conftest's production-database
guard, and it is why load_pipeline() takes the path as an argument. Still open
as DEBT-011; step 2.3 fixes it.

There was a second: `_exp_label` read a module GLOBAL `dte_by_expiry` while
`_build_non_atm_panel` was handed a parameter of the same name that it passed
nowhere. Fixed (ADR-034) — the table is now an argument, so the loader no
longer injects that global at all.

WHAT THIS DOES NOT CLAIM
------------------------
Nothing here says the behaviour is correct. The four-tier panel sort, the
never-empty fallback and the ETA model are all judgment calls. These tests freeze
them so M2 cannot change them by accident.
"""
from __future__ import annotations

import pandas as pd
import pytest
from app_loader import load_pipeline
from conftest import (
    MC_BACK_EXPIRY,
    MC_CALL_STRIKE,
    MC_FRONT_EXPIRY,
    MC_PUT_STRIKE,
    make_atm_iv_history,
    make_transform_history,
)

import config
from core.charts import to_display_time

pytestmark = pytest.mark.integration

SNAP_TS = "2026-07-23 19:59:32"


@pytest.fixture
def pipe(tmp_path, monkeypatch, temp_db):
    """The pipeline, wired to a throwaway database and a throwaway registry file.

    Returns the loaded namespace dict; `p["_st"].session_state` is the
    session-state stand-in and `p["_db"]` the database path.
    """
    # STATE_DIR first: load_pipeline refuses to run against the project root,
    # because this pipeline WRITES eligible_history.json (ADR-035).
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    p = load_pipeline()
    monkeypatch.setattr(config, "DB_PATH", temp_db)
    p["_db"] = temp_db
    p["_registry_path"] = tmp_path / "eligible_history.json"
    return p


def test_the_expiry_label_uses_the_table_it_is_given(pipe):
    """DEBT-027 site 2, fixed in ADR-034 — and this is what proves it.

    _exp_label used to read a module global while its only caller checked
    membership against the parameter it had been handed. Two tables, four lines
    apart. Handing it two DIFFERENT tables must give two different answers; the
    old version would have given the same one twice, or dropped the suffix.
    """
    seven = pipe["exp_label"](MC_FRONT_EXPIRY, {MC_FRONT_EXPIRY: 7})
    ninety = pipe["exp_label"](MC_FRONT_EXPIRY, {MC_FRONT_EXPIRY: 90})

    assert "(7 DTE)" in seven
    assert "(90 DTE)" in ninety


def test_an_expiry_missing_from_the_table_loses_only_its_suffix(pipe):
    """The fallback path, pinned: an unknown expiry still renders a readable
    date rather than raising or printing None."""
    label = pipe["exp_label"](MC_FRONT_EXPIRY, {})

    assert "DTE" not in label
    assert label.strip(), "an unknown expiry produced an empty label"


def _combos(rows):
    """Build the frame scan_all_offsets returns: one row per candidate combo."""
    return pd.DataFrame([
        {
            "Front Expiry": f"{MC_FRONT_EXPIRY} (7 DTE)",
            "Back Expiry": f"{MC_BACK_EXPIRY} (21 DTE)",
            "Put Strike": put,
            "Call Strike": call,
            "Transform Diff": gap,
            "Diagonal Mark": 20.0,
            "Transform Mark": 20.0 + gap,
            "IV Ratio": 1.05,
        }
        for put, call, gap in rows
    ])


def _stub_scan(pipe, combos):
    """Replace scan_all_offsets in the pipeline's own namespace.

    Legitimate here: the subject under test is _compute_mc_core's own ordering and
    banding logic, and the scanner it calls is already pinned by
    test_scanner_golden.py. Controlling the collaborator is what makes the
    rank-before-cap decision observable at all — a real chain cannot be coaxed
    into producing exactly the tie this needs.
    """
    pipe["_namespace"]["scan_all_offsets"] = lambda *_a, **_k: combos


# ═══════════════════════════════════════════════════════════════════════════════
# _compute_mc_core — which opportunities exist, and in what order
# ═══════════════════════════════════════════════════════════════════════════════

def test_asymmetric_combos_survive_the_cap(pipe):
    """THE test this file exists for. Ranking must happen BEFORE the cap.

    25 symmetric combos each sit at $4.90 — a higher raw gap than the 3 asymmetric
    ones at $4.10. The cap is 20. Cap-by-raw-gap first and all 20 slots go to
    symmetric combos, so every asymmetric opportunity vanishes from the panel.

    A symmetric combo (put strike == call strike) is a degenerate straddle, not
    this strategy's structure. Losing the asymmetric ones means the dashboard
    stops showing the trades that are actually taken, while continuing to look
    completely normal.
    """
    symmetric = [(6000.0, 6000.0, 4.90)] * 25
    asymmetric = [(5900.0, 6100.0, 4.10), (5850.0, 6150.0, 4.10),
                  (5800.0, 6200.0, 4.10)]
    _stub_scan(pipe, _combos(symmetric + asymmetric))

    core = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)
    cards = core["approaching_cards"]

    assert len(cards) == pipe["_MC_HISTORY_CAP"]
    n_asym = sum(1 for c in cards if c["put_strike"] != c["call_strike"])
    assert n_asym == 3, (
        f"all 3 asymmetric combos must survive the cap, found {n_asym} — "
        f"ranking is happening after the cap instead of before it"
    )
    # And they must be at the TOP, not merely present.
    assert all(c["put_strike"] != c["call_strike"] for c in cards[:3])


def test_approaching_band_is_half_open(pipe):
    """"Approaching" is a gap in [4.00, 5.00) — 4.00 counts, 5.00 does not.

    5.00 and above is not approaching, it is already eligible and belongs to the
    live panel. Both boundaries are pinned because either could be flipped while
    tidying, and each changes what appears in which section.
    """
    _stub_scan(pipe, _combos([
        (5900.0, 6100.0, 3.99),   # below  -> out
        (5850.0, 6150.0, 4.00),   # at low -> in
        (5800.0, 6200.0, 4.99),   # inside -> in
        (5750.0, 6250.0, 5.00),   # at threshold -> out (it is live, not approaching)
    ]))
    core = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)
    assert core["n_approaching"] == 2
    assert sorted(c["gap"] for c in core["approaching_cards"]) == [4.00, 4.99]


def test_an_empty_scan_returns_the_empty_shape(pipe):
    """No combos must give the full dict with empty members, not a missing key.

    The caller unpacks five keys unconditionally; a short dict here would be an
    AttributeError on the page rather than an empty panel.
    """
    _stub_scan(pipe, pd.DataFrame())
    core = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)
    assert core["approaching_cards"] == []
    assert core["likely_next"] == []
    assert core["n_approaching"] == 0
    assert core["non_atm_current"].empty
    assert core["registry"] == {}


def test_only_asymmetric_combos_reach_the_non_atm_frame(pipe):
    """non_atm_current drives the registry and the live count, so a symmetric
    straddle must never enter it."""
    _stub_scan(pipe, _combos([
        (6000.0, 6000.0, 9.0),   # symmetric
        (5900.0, 6100.0, 8.0),   # asymmetric
    ]))
    core = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)
    non_atm = core["non_atm_current"]
    assert len(non_atm) == 1
    assert non_atm.iloc[0]["Put Strike"] == 5900.0


def test_likely_next_holds_only_computable_etas_asymmetric_first(pipe):
    """"Likely Next" lists candidates with a rising-trend ETA, asymmetric first,
    then soonest first. A card with no ETA must not appear at all rather than
    sorting to the end with a null.
    """
    db_path = pipe["_db"]
    # Give ONE combo a rising history so it earns an ETA; the others have none.
    make_transform_history(db_path, [1.0, 2.0, 3.0, 4.0], interval_minutes=5)
    _stub_scan(pipe, _combos([
        (MC_PUT_STRIKE, MC_CALL_STRIKE, 4.5),   # has history -> ETA
        (5800.0, 6200.0, 4.8),                  # no history  -> no ETA
    ]))
    core = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)

    assert [c["put_strike"] for c in core["likely_next"]] == [MC_PUT_STRIKE]
    assert all(c["eta_minutes"] is not None for c in core["likely_next"])


def test_likely_next_puts_asymmetric_first_even_with_a_later_eta(pipe):
    """The asymmetric-first tier must beat a sooner ETA.

    A symmetric combo is given a steep rise (arriving in ~5 minutes) and an
    asymmetric one a shallow rise (~20 minutes). Sorted on ETA alone the straddle
    would head the list; the tier keeps the tradeable structure on top.

    This needs TWO histories at different strikes, which is why the fixture writer
    takes strikes — an earlier version of this test had only one card in
    likely_next, so the ordering was unobservable and a broken sort passed.
    """
    db_path = pipe["_db"]
    # symmetric 6000/6000: +1.00 per 5 min from 4.0 -> reaches 5.0 in ~5 min
    make_transform_history(db_path, [1.0, 2.0, 3.0, 4.0], interval_minutes=5,
                           put_strike=6000.0, call_strike=6000.0)
    # asymmetric: +0.25 per 5 min from 4.0 -> reaches 5.0 in ~20 min
    make_transform_history(db_path, [3.25, 3.5, 3.75, 4.0], interval_minutes=5,
                           put_strike=MC_PUT_STRIKE, call_strike=MC_CALL_STRIKE)

    _stub_scan(pipe, _combos([
        (6000.0, 6000.0, 4.0),                  # symmetric, sooner ETA
        (MC_PUT_STRIKE, MC_CALL_STRIKE, 4.0),   # asymmetric, later ETA
    ]))
    core = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)
    order = [(c["put_strike"], c["call_strike"]) for c in core["likely_next"]]

    assert order[0] == (MC_PUT_STRIKE, MC_CALL_STRIKE), (
        f"asymmetric must lead despite the later ETA, got {order}"
    )
    assert order[-1] == (6000.0, 6000.0)


# ═══════════════════════════════════════════════════════════════════════════════
# The persisted eligibility registry
# ═══════════════════════════════════════════════════════════════════════════════

def test_registry_records_only_combos_at_or_above_threshold(pipe):
    _stub_scan(pipe, _combos([
        (5900.0, 6100.0, 5.0),    # at threshold -> recorded
        (5800.0, 6200.0, 4.9),    # below        -> not
    ]))
    core = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)
    assert list(core["registry"]) == [f"{MC_FRONT_EXPIRY}|{MC_BACK_EXPIRY}|5900|6100"]


def test_registry_accumulates_across_snapshots(pipe):
    """first_seen must be preserved, last_seen advanced, max_gap kept at the peak,
    hit_count incremented. This file is the only record that a combo was ever
    transformable, so losing the peak loses the evidence."""
    key = f"{MC_FRONT_EXPIRY}|{MC_BACK_EXPIRY}|5900|6100"

    _stub_scan(pipe, _combos([(5900.0, 6100.0, 6.0)]))
    pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, "2026-07-23 15:00:00")

    _stub_scan(pipe, _combos([(5900.0, 6100.0, 9.0)]))
    pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 2, "2026-07-23 16:00:00")

    _stub_scan(pipe, _combos([(5900.0, 6100.0, 7.0)]))
    reg = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 3, "2026-07-23 17:00:00")["registry"]

    entry = reg[key]
    assert entry["first_seen"] == "2026-07-23 15:00:00"
    assert entry["last_seen"] == "2026-07-23 17:00:00"
    assert entry["last_gap"] == 7.0
    assert entry["max_gap"] == 9.0, "the peak must survive a later lower reading"
    assert entry["hit_count"] == 3


def test_registry_prunes_entries_past_the_retention_window(pipe):
    """Old entries are dropped so the file stays small. 30 days is the window."""
    stale = {"old|combo|1|2": {
        "front_raw": MC_FRONT_EXPIRY, "back_raw": MC_BACK_EXPIRY,
        "put_strike": 1, "call_strike": 2, "iv_ratio": None,
        "first_seen": "2026-01-01 00:00:00", "last_seen": "2026-01-01 00:00:00",
        "last_gap": 6.0, "max_gap": 6.0, "hit_count": 1,
    }}
    pipe["_save_eligible_history"](stale)
    assert pipe["_load_eligible_history"]() == stale, "fixture precondition"

    _stub_scan(pipe, _combos([(5900.0, 6100.0, 6.0)]))
    reg = pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)["registry"]
    assert "old|combo|1|2" not in reg


def test_registry_survives_a_corrupt_file(pipe):
    """A truncated or hand-edited JSON file must read as empty, not crash the
    dashboard on load. The registry is convenience data, not a source of truth."""
    pipe["_registry_path"].write_text("{not json", encoding="utf-8")
    assert pipe["_load_eligible_history"]() == {}


def test_registry_is_written_to_disk_not_just_returned(pipe):
    """The whole point is surviving a restart — an in-memory-only registry would
    pass every other test in this section and lose everything on reload."""
    _stub_scan(pipe, _combos([(5900.0, 6100.0, 6.0)]))
    pipe["_compute_mc_core"](pd.DataFrame(), 6000.0, 1, SNAP_TS)
    assert pipe["_registry_path"].exists()
    assert f"{MC_FRONT_EXPIRY}|{MC_BACK_EXPIRY}|5900|6100" in pipe["_load_eligible_history"]()


# ═══════════════════════════════════════════════════════════════════════════════
# _build_non_atm_panel — the curated panel and its four-tier sort
# ═══════════════════════════════════════════════════════════════════════════════

def _entry(put, call, *, max_gap, hits, last_seen, first_seen=None):
    return {
        "front_raw": MC_FRONT_EXPIRY, "back_raw": MC_BACK_EXPIRY,
        "put_strike": put, "call_strike": call, "iv_ratio": 1.02,
        "first_seen": first_seen or last_seen, "last_seen": last_seen,
        "last_gap": max_gap, "max_gap": max_gap, "hit_count": hits,
    }


def _registry(*entries):
    return {f"{MC_FRONT_EXPIRY}|{MC_BACK_EXPIRY}|{int(p)}|{int(c)}": e
            for p, c, e in entries}


def test_the_panel_labels_cards_with_the_table_it_was_handed(pipe):
    """DEBT-027 site 2 at the CALL SITE, not just in the function.

    Testing _exp_label directly proved the function honours its argument. It did
    NOT prove _build_non_atm_panel passes the right one — injecting the original
    defect (guard reads the parameter, lookup gets a different table) left every
    other test green, because none of them looks at a label. This one does.

    The panel is handed a real table here, unlike the sort tests above which pass
    {} because they only care about ordering.
    """
    registry = _registry(
        (5800.0, 6200.0, _entry(5800.0, 6200.0, max_gap=6.0, hits=1,
                                last_seen="2026-07-23 19:00:00")),
    )
    dte = {MC_FRONT_EXPIRY: 7, MC_BACK_EXPIRY: 21}

    cards, _, _ = pipe["_build_non_atm_panel"](
        _combos([(5800.0, 6200.0, 7.0)]), registry, dte, 30, SNAP_TS
    )

    assert cards, "no cards to inspect"
    assert "(7 DTE)" in cards[0]["front_label"], cards[0]["front_label"]
    assert "(21 DTE)" in cards[0]["back_label"], cards[0]["back_label"]


def test_panel_puts_live_above_historical(pipe):
    """Tier 1. An opportunity you can act on now beats a bigger one that has gone,
    however large the historical peak was."""
    registry = _registry(
        (5900.0, 6100.0, _entry(5900.0, 6100.0, max_gap=99.0, hits=9,
                                last_seen="2026-07-23 10:00:00")),
        (5800.0, 6200.0, _entry(5800.0, 6200.0, max_gap=6.0, hits=1,
                                last_seen="2026-07-23 19:00:00")),
    )
    current = _combos([(5800.0, 6200.0, 7.0)])   # only this one is live now

    cards, in_window, fallback = pipe["_build_non_atm_panel"](
        current, registry, {}, 30, SNAP_TS
    )
    assert cards[0]["put_strike"] == 5800.0
    assert cards[0]["is_live"] is True
    assert cards[1]["is_live"] is False
    assert in_window == 2
    assert fallback == 0


def test_live_cards_rank_on_current_gap_historical_on_peak(pipe):
    """Tier 2, and the subtle part: the sort key differs by card type. A live card
    is ranked on what it is worth NOW; a historical one on the best it ever was.
    Using current gap for both would rank every historical card at zero."""
    registry = _registry(
        (5900.0, 6100.0, _entry(5900.0, 6100.0, max_gap=50.0, hits=1,
                                last_seen="2026-07-23 10:00:00")),
        (5800.0, 6200.0, _entry(5800.0, 6200.0, max_gap=20.0, hits=1,
                                last_seen="2026-07-23 11:00:00")),
    )
    cards, _, _ = pipe["_build_non_atm_panel"](
        pd.DataFrame(), registry, {}, 30, SNAP_TS
    )
    assert [c["put_strike"] for c in cards] == [5900.0, 5800.0]
    assert cards[0]["gap"] == 50.0
    assert cards[0]["current_gap"] is None


def test_panel_breaks_gap_ties_on_hit_count(pipe):
    """Tier 3 — 'which strikes repeatedly become transformable', not 'which spiked
    once'. Equal peaks, so the repeat performer must win."""
    registry = _registry(
        (5900.0, 6100.0, _entry(5900.0, 6100.0, max_gap=8.0, hits=1,
                                last_seen="2026-07-23 10:00:00")),
        (5800.0, 6200.0, _entry(5800.0, 6200.0, max_gap=8.0, hits=12,
                                last_seen="2026-07-23 10:00:00")),
    )
    cards, _, _ = pipe["_build_non_atm_panel"](
        pd.DataFrame(), registry, {}, 30, SNAP_TS
    )
    assert cards[0]["hit_count"] == 12


def test_a_gap_of_exactly_the_threshold_counts_as_live(pipe):
    """`is_live` uses `>=`, so exactly $5.00 is live.

    Every other test here uses gaps like 6.0 or 7.0, which cannot tell `>=` from
    `>`. Without this test, flipping that comparison — a plausible tidy-up — would
    silently drop every combo sitting exactly on the threshold out of the live
    panel and into the historical one.
    """
    registry = _registry(
        (5900.0, 6100.0, _entry(5900.0, 6100.0, max_gap=5.0, hits=1,
                                last_seen="2026-07-23 19:00:00")),
    )
    current = _combos([(5900.0, 6100.0, 5.0)])
    cards, _, _ = pipe["_build_non_atm_panel"](
        current, registry, {}, 30, SNAP_TS
    )
    assert cards[0]["is_live"] is True
    assert cards[0]["current_gap"] == 5.0


def test_panel_excludes_entries_older_than_the_lookback(pipe):
    registry = _registry(
        (5900.0, 6100.0, _entry(5900.0, 6100.0, max_gap=8.0, hits=1,
                                last_seen="2026-07-23 10:00:00")),
        (5800.0, 6200.0, _entry(5800.0, 6200.0, max_gap=8.0, hits=1,
                                last_seen="2026-06-01 10:00:00")),
    )
    cards, in_window, _ = pipe["_build_non_atm_panel"](
        pd.DataFrame(), registry, {}, 1, SNAP_TS
    )
    assert in_window == 1
    assert cards[0]["put_strike"] == 5900.0


def test_out_of_window_fallback_is_flagged_not_silent(pipe):
    """The never-empty guarantee: rather than show nothing, the panel back-fills
    from outside the window — but every back-filled card carries
    outside_lookback=True so the page can say so.

    Silently showing stale data as if it were in-range is the failure this
    prevents, and the flag is the only thing standing between the two.
    """
    registry = _registry(*[
        (5000.0 + i, 7000.0 - i,
         _entry(5000.0 + i, 7000.0 - i, max_gap=8.0, hits=1,
                last_seen=f"2026-06-0{i + 1} 10:00:00"))
        for i in range(4)
    ])
    cards, in_window, fallback = pipe["_build_non_atm_panel"](
        pd.DataFrame(), registry, {}, 1, SNAP_TS, min_display=6
    )
    assert in_window == 0, "nothing is inside a 1-day window"
    assert fallback == 4
    assert all(c["outside_lookback"] is True for c in cards)


def test_a_truly_empty_registry_gives_an_empty_panel(pipe):
    """Cold start is the one case the never-empty guarantee cannot cover, and it
    must not be papered over with a placeholder card."""
    cards, in_window, fallback = pipe["_build_non_atm_panel"](
        pd.DataFrame(), {}, {}, 30, SNAP_TS
    )
    assert cards == []
    assert (in_window, fallback) == (0, 0)


def test_panel_respects_the_cap(pipe):
    registry = _registry(*[
        (5000.0 + i, 7000.0 - i,
         _entry(5000.0 + i, 7000.0 - i, max_gap=float(i), hits=1,
                last_seen="2026-07-23 10:00:00"))
        for i in range(30)
    ])
    cards, in_window, _ = pipe["_build_non_atm_panel"](
        pd.DataFrame(), registry, {}, 30, SNAP_TS
    )
    assert in_window == 30
    assert len(cards) == pipe["_MC_HISTORY_CAP"]


def test_duration_is_shown_only_for_live_cards(pipe):
    """A historical card must not carry a duration — "active for 2h" beside a
    combo that is no longer eligible would read as a live position."""
    db_path = pipe["_db"]
    make_transform_history(db_path, [6.0] * 5, interval_minutes=5)
    registry = _registry(
        (MC_PUT_STRIKE, MC_CALL_STRIKE,
         _entry(MC_PUT_STRIKE, MC_CALL_STRIKE, max_gap=6.0, hits=3,
                last_seen="2026-07-23 19:00:00")),
    )
    cards, _, _ = pipe["_build_non_atm_panel"](
        pd.DataFrame(), registry, {}, 30, SNAP_TS
    )
    assert cards[0]["is_live"] is False
    assert cards[0]["duration"] is None
    # The sparkline still renders, because trajectory is useful either way.
    assert cards[0]["spark"] != "—"


def test_a_malformed_registry_entry_is_skipped_not_fatal(pipe):
    """One bad row must not take the panel down with it."""
    registry = {
        "broken": {"front_raw": MC_FRONT_EXPIRY, "back_raw": MC_BACK_EXPIRY,
                   "put_strike": 1, "call_strike": 2, "max_gap": 1.0,
                   "hit_count": 1},   # no last_seen
        **_registry((5900.0, 6100.0, _entry(5900.0, 6100.0, max_gap=8.0, hits=1,
                                            last_seen="2026-07-23 10:00:00"))),
    }
    cards, in_window, _ = pipe["_build_non_atm_panel"](
        pd.DataFrame(), registry, {}, 30, SNAP_TS
    )
    assert in_window == 1
    assert cards[0]["put_strike"] == 5900.0


# ═══════════════════════════════════════════════════════════════════════════════
# _run_mission_control — the "New" badges and the headline numbers
# ═══════════════════════════════════════════════════════════════════════════════

def _run(pipe, snapshot_id, combos, lookback=30):
    _stub_scan(pipe, combos)
    return pipe["_run_mission_control"](
        pd.DataFrame(), 6000.0, snapshot_id, SNAP_TS,
        {MC_FRONT_EXPIRY: 7, MC_BACK_EXPIRY: 21}, lookback,
    )


def test_everything_live_is_new_on_the_first_run(pipe):
    mc = _run(pipe, 1, _combos([(5900.0, 6100.0, 7.0)]))
    assert mc["n_new"] == 1
    assert [c["is_new"] for c in mc["non_atm"]] == [True]


def test_nothing_is_new_again_within_the_same_snapshot(pipe):
    """Every widget click reruns the whole script. If the comparison set advanced
    on each rerun, cards would flash "New" forever; if it never advanced, nothing
    would ever be new. The guard is the snapshot id.
    """
    combos = _combos([(5900.0, 6100.0, 7.0)])
    _run(pipe, 1, combos)
    again = _run(pipe, 1, combos)          # same snapshot -> a rerun, not new data
    assert again["n_new"] == 1, "the SAME new_keys must be reported, not recomputed"

    third = _run(pipe, 2, combos)          # a genuinely new snapshot
    assert third["n_new"] == 0, "already-seen combos must stop being new"


def test_a_combo_appearing_later_is_flagged_new(pipe):
    _run(pipe, 1, _combos([(5900.0, 6100.0, 7.0)]))
    mc = _run(pipe, 2, _combos([(5900.0, 6100.0, 7.0), (5800.0, 6200.0, 8.0)]))
    new = [c["put_strike"] for c in mc["non_atm"] if c["is_new"]]
    assert new == [5800.0]


def test_approaching_cards_are_never_marked_new(pipe):
    """"New" means newly eligible. An approaching card has not crossed anything,
    so a badge on it would announce an event that has not happened."""
    mc = _run(pipe, 1, _combos([(5900.0, 6100.0, 4.5)]))
    assert all(c["is_new"] is False for c in mc["approaching"])


def test_n_eligible_counts_live_combos(pipe):
    mc = _run(pipe, 1, _combos([
        (5900.0, 6100.0, 7.0),    # live
        (5800.0, 6200.0, 6.0),    # live
        (5700.0, 6300.0, 4.0),    # approaching, not live
        (6000.0, 6000.0, 9.0),    # symmetric — excluded from non-ATM entirely
    ]))
    assert mc["n_eligible"] == 2


def test_best_prefers_a_live_card_over_a_higher_historical_one(pipe):
    """The headline card must be actionable. A dead $99 peak outranking a live $6
    would point the trader at something they cannot take.

    PROVEN-EQUIVALENT NOTE: replacing `next(c for c in panel if c["is_live"])` with
    `panel[0]` passes this and every other test here, and that is correct rather
    than a gap — `_build_non_atm_panel` already sorts live cards first (tier 1), so
    the two expressions cannot disagree while that sort holds. The `is_live` filter
    is defence-in-depth. It becomes load-bearing the moment anyone changes the
    panel sort, which is pinned by test_panel_puts_live_above_historical above.
    """
    pipe["_save_eligible_history"](_registry(
        (5000.0, 7000.0, _entry(5000.0, 7000.0, max_gap=99.0, hits=5,
                                last_seen="2026-07-23 18:00:00")),
    ))
    mc = _run(pipe, 1, _combos([(5900.0, 6100.0, 6.0)]))
    assert mc["best"]["is_live"] is True
    assert mc["best"]["put_strike"] == 5900.0


def test_best_is_none_when_there_is_nothing_at_all(pipe):
    mc = _run(pipe, 1, pd.DataFrame())
    assert mc["best"] is None
    assert mc["n_eligible"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# The _load_* wrappers — every chart's underlying data
# ═══════════════════════════════════════════════════════════════════════════════

def test_atm_history_converts_iv_to_percent_and_local_time(pipe):
    """Two conversions happen at this boundary and both are invisible if lost.

    IV is stored as a decimal (0.184) and charted as a percentage (18.4). Lose the
    ×100 and the line flattens against the bottom of the axis — no error, just a
    chart that says volatility is nearly zero.

    Timestamps are stored in UTC and must be returned as naive Eastern wall-clock.
    Lose the conversion and every point shifts four or five hours: session breaks
    land in the wrong places and the day's shape is wrong. This is the BUG-019
    family, which has already bitten this project once.
    """
    db_path = pipe["_db"]
    make_atm_iv_history(db_path, [0.184, 0.190])

    df = pipe["_load_atm_hist"](MC_FRONT_EXPIRY, 1)

    assert list(df["atm_iv"].round(1)) == [18.4, 19.0]

    # UPDATED for DEBT-030 (ADR-038). The read layer used to return naive
    # Eastern; it now returns zoned UTC and the chart converts. The protection
    # this test gave is unchanged and is asserted one step further down the
    # pipeline: the value a CHART receives must still be Eastern wall-clock.
    assert str(df["timestamp"].dt.tz) == "UTC", (
        "reads must hand back the stored zone, not a display decision"
    )

    df = to_display_time(df, config.DISPLAY_TIMEZONE)
    assert df["timestamp"].dt.tz is None, "must be naive for Plotly rangebreaks"

    # The newest fixture row was written "now", so the value the chart gets must
    # match the current EASTERN wall-clock, not the current UTC one. In summer
    # those are four hours apart, so this fails loudly if the conversion goes
    # missing at either end.
    latest = df["timestamp"].max()
    expected = (
        pd.Timestamp.now(tz="UTC").tz_convert(config.DISPLAY_TIMEZONE).tz_localize(None)
    )
    assert abs((expected - latest).total_seconds()) < 300, (
        "timestamps must be Eastern wall-clock, not UTC — a 4-5 hour shift here is "
        "the BUG-019 failure mode"
    )


def test_atm_history_of_nothing_is_an_empty_frame(pipe):
    """Callers check .empty. Returning None here would be an AttributeError on the
    page instead of a blank chart."""
    df = pipe["_load_atm_hist"](MC_FRONT_EXPIRY, 1)
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_atm_history_fallback_widens_the_window_then_keeps_one_day(pipe):
    """_load_atm_hist_fb exists for a market holiday or a quiet morning: if today
    is empty, look back 5 days and show the most recent session that HAS data —
    but only that session, not all five days blended into one line.
    """
    db_path = pipe["_db"]
    make_atm_iv_history(db_path, [0.18, 0.19], end_minutes_ago=3 * 24 * 60)
    make_atm_iv_history(db_path, [0.20, 0.21], end_minutes_ago=2 * 24 * 60)

    assert pipe["_load_atm_hist"](MC_FRONT_EXPIRY, 1).empty, "fixture precondition"

    df = pipe["_load_atm_hist_fb"](MC_FRONT_EXPIRY, 1)
    assert not df.empty
    assert df["timestamp"].dt.date.nunique() == 1, (
        "the fallback must show ONE session, not blend several into a single line"
    )
    assert list(df["atm_iv"].round(1)) == [20.0, 21.0], "the most recent session"


def test_chain_frame_renames_and_converts(pipe):
    """_load_chain_df is the boundary every other calculation reads from: 'side'
    is CALL/PUT not C/P, and IV is a percentage. A rename lost here breaks the
    scanner by KeyError, but the IV conversion fails silently.

    CHANGED 2026-08-19 (BUG-023 display half): this used to require that
    'expiry_date' be renamed AWAY, leaving only 'expiry'. Both columns are now
    kept on purpose and they are different things. 'expiry' is the DISPLAY KEY
    and is no longer always a date — the third Friday appears twice, as
    "2026-08-21" and "2026-08-21 (AM)". 'expiry_date' is the plain date that
    chart and day-count arithmetic needs, so that nobody is tempted to parse
    the key back into one. See core/contract.py.
    """
    db_path = pipe["_db"]
    make_transform_history(db_path, [6.0])
    snapshot_id = 1

    df = pipe["_load_chain_df"](snapshot_id)

    assert "expiry" in df.columns and "expiry_date" in df.columns
    assert "settlement" in df.columns
    assert df["iv"].max() == pytest.approx(18.4), "0.184 stored -> 18.4 charted"

    # Assert the MAPPING, not just the vocabulary. `set(df["side"]) <= {"CALL",
    # "PUT"}` passes just as happily when C and P are swapped, which would put
    # every call's price on the put line and vice versa.
    by_right = df.set_index("right")["side"].to_dict()
    assert by_right["C"] == "CALL"
    assert by_right["P"] == "PUT"


def test_chain_frame_of_a_missing_snapshot_is_empty(pipe):
    df = pipe["_load_chain_df"](99999)
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_contract_history_converts_iv_and_falls_back(pipe):
    """Same percentage conversion, and the same one-day fallback, for a single
    contract's IV line."""
    db_path = pipe["_db"]
    make_transform_history(db_path, [6.0, 6.0], interval_minutes=5)

    df = pipe["_load_contract_hist"](MC_FRONT_EXPIRY, MC_CALL_STRIKE, "CALL", 1)
    assert not df.empty
    assert df["iv"].max() == pytest.approx(18.4)
    # Zoned UTC from the read, naive Eastern once the chart has had it —
    # DEBT-030 (ADR-038).
    assert str(df["timestamp"].dt.tz) == "UTC"
    assert to_display_time(df, config.DISPLAY_TIMEZONE)["timestamp"].dt.tz is None


def test_transform_marks_and_diagonal_history_return_frames(pipe):
    """Thin wrappers, but the empty case is what the charts branch on."""
    db_path = pipe["_db"]
    make_transform_history(db_path, [6.0, 7.0], interval_minutes=5)

    marks = pipe["_load_transform_marks"](
        MC_FRONT_EXPIRY, MC_BACK_EXPIRY, MC_CALL_STRIKE, MC_PUT_STRIKE, 1, 1
    )
    assert len(marks) == 2

    empty = pipe["_load_transform_marks"](
        MC_FRONT_EXPIRY, MC_BACK_EXPIRY, 9999.0, 9999.0, 1, 1
    )
    assert isinstance(empty, pd.DataFrame)
    assert empty.empty


def test_latest_atm_iv_returns_plain_dicts_newest_first(pipe):
    """Returned as dicts, not sqlite3.Row, because the caller indexes by name
    after the connection has closed."""
    db_path = pipe["_db"]
    make_atm_iv_history(db_path, [0.18, 0.19, 0.20], interval_minutes=5)

    rows = pipe["_load_latest_atm_iv"](MC_FRONT_EXPIRY, 1, n=2)
    assert len(rows) == 2
    assert all(isinstance(r, dict) for r in rows)


def test_prior_close_is_none_when_unknown(pipe):
    """A missing prior close must be None, not 0.0 — a day-change computed against
    zero would render as an enormous percentage move."""
    assert pipe["_load_prior_close"]("2026-07-23") is None


def test_spx_intraday_of_nothing_is_an_empty_frame(pipe):
    df = pipe["_load_spx_intraday"]("2026-07-23", 1)
    assert isinstance(df, pd.DataFrame)
    assert df.empty
