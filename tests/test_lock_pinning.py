"""A locked diagonal keeps being recorded after SPX has moved away from it.

WHAT WENT WRONG (BUG-022). Both of the collector's narrowings are centred on
TODAY — the nearest N expiries, and strikes within ±width of spot. A lock is
centred on the fill and never moves. So a locked strike drifts to the edge of
the recorded window and then out of it, and the legs of a position actually
being held stop being stored. The dashboard's "View Chart" then found the
strike absent from today's chain, dropped it (it must — Streamlit raises
otherwise), and fell back to the nearest-to-spot default. The trader clicked a
position they held and was shown a DIFFERENT diagonal, drawn with the same
confidence and nothing saying so.

WHAT IS PINNED HERE
  1. The extraction rule, from a locks dict alone — no file, no collector
  2. Malformed locks cost that lock its protection and never the snapshot
  3. The strike filter keeps a locked strike outside the window, and keeps the
     no-locks case byte-identical to before
  4. Exemption widens what is KEPT, never what is ASKED FOR — a locked strike
     the broker never sent cannot be conjured into the data
  5. The dashboard SAYS SO when it cannot honour a click

Number 5 is the one that stops this regressing into a silent substitution
again. Pinning removes the two known causes; it cannot remove the class. Any
future reason a staged value goes missing — an outage, a gap day, a lock edited
by hand — lands back on the guard, and the guard must keep admitting it.
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest
from conftest import make_raw_chain

import collector
import config
import schwab_client
from core import pins as core_pins


def _lock(front="2026-08-07", back="2026-09-18", put=7200.0, call=7800.0):
    return {
        "lock_id": "abc", "front_expiry": front, "back_expiry": back,
        "put_strike": put, "call_strike": call,
        "entry_diagonal_mark": 12.5, "locked_at": "2026-08-01T10:00:00-04:00",
        "mode": "monitor_only", "journal_trade_id": None,
    }


def _chain(strikes, expiry="2026-08-07"):
    return pd.DataFrame([
        {"expiry": expiry, "strike": float(s), "side": side, "dte": 6}
        for s in strikes for side in ("PUT", "CALL")
    ])


# ─────────────────────────────────────────────────────────────────────────────
# 1. The extraction rule, with no file and no collector anywhere near it
# ─────────────────────────────────────────────────────────────────────────────

def test_no_locks_pins_nothing():
    assert not core_pins.from_locks({})


def test_both_expiries_and_both_strikes_of_a_lock_are_pinned():
    pins = core_pins.from_locks({"k": _lock()})
    assert pins.expiries == frozenset({"2026-08-07", "2026-09-18"})
    assert pins.strikes == frozenset({7200.0, 7800.0})


def test_two_locks_sharing_an_expiry_pin_it_once():
    pins = core_pins.from_locks({
        "a": _lock(put=7200.0, call=7800.0),
        "b": _lock(put=7100.0, call=7800.0),
    })
    assert pins.expiries == frozenset({"2026-08-07", "2026-09-18"})
    assert pins.strikes == frozenset({7100.0, 7200.0, 7800.0})


def test_a_strike_stored_as_a_string_is_still_pinned():
    """The sidecar is JSON and has been hand-edited before."""
    assert core_pins.from_locks({"k": _lock(put="7200")}).strikes == frozenset(
        {7200.0, 7800.0}
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. A malformed lock costs that lock its protection, never the snapshot
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("junk", [
    {"k": None},
    {"k": "not a record"},
    {"k": {}},
    {"k": {"front_expiry": None, "put_strike": None}},
    {"k": {"put_strike": "not a number"}},
    {"k": {"put_strike": True}},
    "not a dict at all",
    None,
])
def test_a_malformed_lock_file_never_raises(junk):
    core_pins.from_locks(junk)


def test_one_bad_lock_does_not_cost_a_good_one_its_pins():
    pins = core_pins.from_locks({"bad": "junk", "good": _lock()})
    assert pins.strikes == frozenset({7200.0, 7800.0})


# ─────────────────────────────────────────────────────────────────────────────
# 3. The strike filter honours the exemption
# ─────────────────────────────────────────────────────────────────────────────

def test_without_locks_the_filter_is_unchanged():
    """The no-locks path must stay exactly what it was — this is the case that
    runs on every snapshot, and BUG-022 is not worth a regression in it."""
    chain = _chain([7000, 7400, 7500, 7900])
    out = schwab_client.filter_chain_by_strike_window(chain, 7500.0, width=300)
    assert sorted(out["strike"].unique()) == [7400.0, 7500.0]


def test_a_locked_strike_outside_the_window_is_kept():
    chain = _chain([7000, 7400, 7500, 7900])
    out = schwab_client.filter_chain_by_strike_window(
        chain, 7500.0, width=300, keep_strikes=frozenset({7000.0}),
    )
    assert sorted(out["strike"].unique()) == [7000.0, 7400.0, 7500.0]


def test_an_unlocked_strike_outside_the_window_is_still_dropped():
    """The exemption is narrow: it must not turn into a wider window."""
    chain = _chain([7000, 7400, 7500, 7900])
    out = schwab_client.filter_chain_by_strike_window(
        chain, 7500.0, width=300, keep_strikes=frozenset({7000.0}),
    )
    assert 7900.0 not in set(out["strike"])


def test_a_locked_strike_the_broker_never_sent_is_not_invented():
    """Exemption widens what we KEEP, never what we ASK FOR. A lock on a strike
    outside the fetch cannot conjure rows — it can only be reported (and the
    collector logs a warning), never fabricated."""
    chain = _chain([7400, 7500])
    out = schwab_client.filter_chain_by_strike_window(
        chain, 7500.0, width=300, keep_strikes=frozenset({6000.0}),
    )
    assert sorted(out["strike"].unique()) == [7400.0, 7500.0]


def test_an_empty_exemption_behaves_like_no_exemption():
    chain = _chain([7000, 7400, 7500])
    both = [
        sorted(schwab_client.filter_chain_by_strike_window(
            chain, 7500.0, width=300, keep_strikes=k)["strike"].unique())
        for k in (None, frozenset())
    ]
    assert both[0] == both[1] == [7400.0, 7500.0]


# ─────────────────────────────────────────────────────────────────────────────
# 4. End to end: the locked legs reach the DATABASE
#
# Asserting on stored rows, not on calls — per this suite's convention. A test
# that checked "filter was called with keep_strikes" would pass even if the
# collector then discarded the result; the dashboard depends on the ROWS.
# ─────────────────────────────────────────────────────────────────────────────

# spot is 6000 in the standard fixture, so ±300 spans 5700–6300.
FAR_STRIKE  = 5000.0     # a lock that has drifted a long way out of the window
NEAR_STRIKE = 6000.0
FAR_EXPIRY  = "2027-06-18"


def _write_locks(state_dir, locks: dict) -> None:
    (state_dir / "entry_locks.json").write_text(json.dumps(locks), encoding="utf-8")


def _stored(db_path, snap_id, column="strike"):
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute(
            f"SELECT DISTINCT {column} FROM option_rows WHERE snapshot_id = ?",
            (snap_id,))}
    finally:
        conn.close()


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setattr(config, "STATE_DIR", d)
    return d


def test_a_drifted_locked_strike_is_stored_when_an_unlocked_one_is_not(
        fake_client, temp_db, patch_schwab, state_dir):
    """The defect, end to end. Same chain, same spot, same window — the only
    difference is whether a lock depends on the strike."""
    chain = make_raw_chain(strikes=[FAR_STRIKE, 5900.0, NEAR_STRIKE, 6100.0])

    patch_schwab(raw_chain=chain)
    unpinned = collector._run_cycle(fake_client, temp_db, "MIDDAY", 300)
    assert FAR_STRIKE not in _stored(temp_db, unpinned)

    _write_locks(state_dir, {"k": _lock(front="2026-08-07", back="2026-08-21",
                                        put=FAR_STRIKE, call=NEAR_STRIKE)})
    patch_schwab(raw_chain=chain)
    pinned = collector._run_cycle(fake_client, temp_db, "MIDDAY", 300)
    assert FAR_STRIKE in _stored(temp_db, pinned)


def test_a_locked_expiry_beyond_the_nearest_n_is_stored(
        fake_client, temp_db, patch_schwab, state_dir, monkeypatch):
    """The second narrowing. Trim set to 2 so the fixture stays readable —
    the rule under test is "beyond the cut", not the production number."""
    monkeypatch.setattr(config, "MAX_EXPIRY_COUNT", 2)
    chain = make_raw_chain(expiries=[("2026-08-07", 7), ("2026-08-21", 21),
                                     (FAR_EXPIRY, 686)])

    patch_schwab(raw_chain=chain)
    unpinned = collector._run_cycle(fake_client, temp_db, "MIDDAY", 300)
    assert FAR_EXPIRY not in _stored(temp_db, unpinned, "expiry_date")

    _write_locks(state_dir, {"k": _lock(front="2026-08-07", back=FAR_EXPIRY,
                                        put=NEAR_STRIKE, call=NEAR_STRIKE)})
    patch_schwab(raw_chain=chain)
    pinned = collector._run_cycle(fake_client, temp_db, "MIDDAY", 300)
    assert FAR_EXPIRY in _stored(temp_db, pinned, "expiry_date")


def test_a_lock_read_that_raises_does_not_stop_the_snapshot(
        fake_client, temp_db, patch_schwab, state_dir, monkeypatch):
    """THE CYCLE IS WORTH MORE THAN THE PROTECTION, for the cases nobody
    predicted.

    Written because a mutation SURVIVED: narrowing `_load_pins`'s `except
    Exception` to a type that never occurs broke nothing, which proved the
    corrupt-file test below was exercising state.store's quarantine and never
    the guard in the collector. The guard is worth keeping — it is the collector
    depending on a file the DASHBOARD owns — but a guard no test can fail is
    not evidence of anything. So the failure is injected at the seam instead.
    """
    def _boom(_state_dir):
        raise RuntimeError("lock file went sideways in a way nobody predicted")

    monkeypatch.setattr(collector.entry_locks, "load", _boom)

    patch_schwab(raw_chain=make_raw_chain(strikes=[5900.0, NEAR_STRIKE, 6100.0]))
    snap_id = collector._run_cycle(fake_client, temp_db, "MIDDAY", 300)

    conn = sqlite3.connect(temp_db)
    try:
        status = conn.execute(
            "SELECT status FROM snapshots WHERE snapshot_id = ?", (snap_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "COMPLETE"


def test_a_corrupt_lock_file_does_not_stop_the_snapshot(
        fake_client, temp_db, patch_schwab, state_dir):
    """The same guarantee end to end, one layer lower: `state.store.read_json`
    quarantines an unreadable sidecar and returns {}, so the corruption never
    even reaches the guard above. Both paths are pinned because they fail for
    different reasons and either could regress alone."""
    (state_dir / "entry_locks.json").write_text("{not json", encoding="utf-8")

    patch_schwab(raw_chain=make_raw_chain(strikes=[5900.0, NEAR_STRIKE, 6100.0]))
    snap_id = collector._run_cycle(fake_client, temp_db, "MIDDAY", 300)

    conn = sqlite3.connect(temp_db)
    try:
        status = conn.execute(
            "SELECT status FROM snapshots WHERE snapshot_id = ?", (snap_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "COMPLETE"
    assert _stored(temp_db, snap_id) == {5900.0, NEAR_STRIKE, 6100.0}


def test_a_missing_lock_file_collects_exactly_as_before(
        fake_client, temp_db, patch_schwab, state_dir):
    """No locks is the case that runs on every snapshot today."""
    patch_schwab(raw_chain=make_raw_chain(strikes=[5900.0, NEAR_STRIKE, 6100.0]))
    snap_id = collector._run_cycle(fake_client, temp_db, "MIDDAY", 300)
    assert _stored(temp_db, snap_id) == {5900.0, NEAR_STRIKE, 6100.0}


# ---------------------------------------------------------------------------
# Display keys (BUG-023). A lock may name the third Friday's a.m. contract, and
# the collector narrows a chain keyed on the date alone.
# ---------------------------------------------------------------------------

def test_a_labelled_lock_still_pins_its_expiry_date():
    """The failure this guards against is silent: the pin simply stops
    matching, the locked legs drop out of the narrowing, and the prices behind
    an open position stop being recorded — BUG-022, all over again."""
    locks = {"a": {"front_expiry": "2026-08-21 (AM)",
                   "back_expiry": "2026-09-16",
                   "put_strike": 7500.0, "call_strike": 7550.0}}
    assert core_pins.from_locks(locks).expiry_dates == {"2026-08-21", "2026-09-16"}


def test_the_raw_keys_are_kept_as_they_were():
    """`expiries` still holds what the lock says, label and all — only the
    date-shaped view strips it."""
    locks = {"a": {"front_expiry": "2026-08-21 (AM)", "back_expiry": "2026-09-16"}}
    assert core_pins.from_locks(locks).expiries == {"2026-08-21 (AM)", "2026-09-16"}


def test_both_contracts_of_one_date_pin_that_date_once():
    locks = {
        "a": {"front_expiry": "2026-08-21 (AM)", "back_expiry": "2026-09-16"},
        "b": {"front_expiry": "2026-08-21",      "back_expiry": "2026-09-16"},
    }
    assert core_pins.from_locks(locks).expiry_dates == {"2026-08-21", "2026-09-16"}
