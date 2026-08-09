"""
Retention — the only code in the project that deletes irreplaceable data.

WHY THIS FILE IS PARANOID. Every other test here protects a number on a screen;
a mistake there is visible and fixable. A mistake here is permanent, silent, and
discovered months later when a chart is short of history that nobody can get
back. So these tests are written against the *safety properties* — what must
never be deleted — at least as hard as against the feature.

The boundary tests inject `today` rather than waiting for the calendar. That
matters more than convenience: on the day this was written the real database
had nothing old enough to prune (ADR-044), so a test that used the real clock
would have passed by deleting nothing at all, forever, without anyone noticing.

Nothing here touches the production database — every test runs on temp_db.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import config
import db

pytestmark = pytest.mark.integration

# Two expiries either side of any cutoff used below, plus the strikes that make
# a leg. Concrete dates rather than offsets from today, so a test that fails
# fails at the same place next month.
OLD_EXPIRY = "2026-01-15"
NEWER_EXPIRY = "2026-06-30"
TODAY = "2026-08-09"          # OLD is 206 days past; NEWER is 40


def _add_snapshot(path: str, ts: str = "2026-01-10 15:00:00") -> int:
    sid = db.create_snapshot(path, ts, "OPEN", 60, underlying_price=6000.0,
                             underlying_bid=6000.0, underlying_ask=6000.0,
                             vix_value=15.0)
    db.finalize_snapshot(path, sid, "COMPLETE", 10, 2, 500)
    return sid


def _rows(sid: int, expiry: str, n: int) -> list[dict]:
    return [{
        "snapshot_id": sid, "expiry_date": expiry, "dte": 7,
        "strike": 6000.0 + i, "right": "C" if i % 2 else "P",
        "bid": 1.0, "ask": 1.2, "mark": 1.1, "last": 1.1, "iv": 0.2,
        "delta": 0.5, "gamma": 0.01, "theta": -0.5, "vega": 1.0,
        "volume": 10, "open_interest": 100,
        "intrinsic_value": 0.0, "time_value": 1.1,
    } for i in range(n)]


def _seed(path: str, **counts) -> int:
    """counts maps expiry_date -> number of option_rows to create."""
    sid = _add_snapshot(path)
    for expiry, n in counts.items():
        db.insert_option_rows(path, _rows(sid, expiry, n))
        db.insert_atm_iv_records(path, [{
            "snapshot_id": sid, "expiry_date": expiry, "dte": 7,
            "atm_strike": 6000.0, "atm_call_iv": 0.2, "atm_put_iv": 0.2,
            "atm_avg_iv": 0.2, "iv_spread_to_front": 0.0,
            "iv_ratio_to_front": 1.0,
        }])
    return sid


def _count(path: str, table: str) -> int:
    with db.get_conn(path) as conn:
        return conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]


def _log_trade(path: str, trade_id="T-001", *, expiries=(OLD_EXPIRY,),
               ic_expiry=None, transform_expiries=None):
    db.init_trades_table(path)
    legs = [{"expiry": e, "type": "Call", "action": "Sell to Open",
             "strike": 6000.0, "fill": 1.0} for e in expiries]
    fields = {
        "trade_id": trade_id, "entry_date": "2026-01-05", "entry_time": "10:00",
        "status": "Open", "contracts": 1, "initial_legs": json.dumps(legs),
        "total_debit": 4.0, "ic_expiry_date": ic_expiry,
    }
    if transform_expiries:
        fields["transform_legs"] = json.dumps(
            [{"expiry": e, "type": "Put", "action": "Buy to Open",
              "strike": 5900.0, "fill": 1.0} for e in transform_expiries])
    db.insert_trade(path, fields)


# ─────────────────────────────────────────────────────────────────────────────
# plan_prune — the cutoff, and what falls either side of it
# ─────────────────────────────────────────────────────────────────────────────

def test_plan_selects_only_expiries_older_than_the_cutoff(temp_db):
    _seed(temp_db, **{OLD_EXPIRY: 5, NEWER_EXPIRY: 3})
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert [e["expiry_date"] for e in plan["prunable"]] == [OLD_EXPIRY]
    assert plan["rows_to_delete"] == 5


def test_plan_reads_the_default_retention_from_config(temp_db):
    _seed(temp_db, **{OLD_EXPIRY: 5})
    assert db.plan_prune(temp_db, today=TODAY)["retention_days"] == config.RETENTION_DAYS


def test_the_cutoff_boundary_is_strictly_older_than(temp_db):
    """An expiry landing exactly ON the cutoff is KEPT.

    Off-by-one in the safe direction, deliberately: the difference between < and
    <= here is one day of irreplaceable data, and the version that keeps it is
    the version you can still change your mind about.
    """
    _seed(temp_db, **{"2026-05-11": 4})     # exactly 90 days before TODAY
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert plan["prunable"] == []
    assert plan["cutoff"] == "2026-05-11"


def test_a_day_past_the_boundary_is_prunable(temp_db):
    """The other side of the same boundary — without this, the test above is
    also satisfied by a pruner that never selects anything."""
    _seed(temp_db, **{"2026-05-10": 4})
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert [e["expiry_date"] for e in plan["prunable"]] == ["2026-05-10"]


def test_plan_is_read_only(temp_db):
    _seed(temp_db, **{OLD_EXPIRY: 5})
    before = _count(temp_db, "option_rows")
    db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert _count(temp_db, "option_rows") == before


def test_negative_retention_is_refused(temp_db):
    """A negative window means a cutoff in the future — every row prunable.
    Nothing should ever ask for that, so it raises rather than being clamped."""
    with pytest.raises(ValueError):
        db.plan_prune(temp_db, retention_days=-1, today=TODAY)


# ─────────────────────────────────────────────────────────────────────────────
# Expiries a trade used are never prunable — the safety rule of ADR-044
# ─────────────────────────────────────────────────────────────────────────────

def test_an_expiry_used_by_a_trade_is_held_back(temp_db):
    _seed(temp_db, **{OLD_EXPIRY: 5})
    _log_trade(temp_db, expiries=(OLD_EXPIRY,))
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert plan["prunable"] == []
    assert [e["expiry_date"] for e in plan["held_for_trades"]] == [OLD_EXPIRY]
    assert plan["rows_held"] == 5


def test_the_iron_condor_expiry_is_protected_too(temp_db):
    """A transformed trade has two structures. Keeping the diagonal's data and
    pruning the condor's leaves a record of half a trade."""
    _seed(temp_db, **{OLD_EXPIRY: 5})
    _log_trade(temp_db, expiries=("2026-03-01",), ic_expiry=OLD_EXPIRY)
    assert db.plan_prune(temp_db, retention_days=90, today=TODAY)["prunable"] == []


def test_transform_legs_are_protected_too(temp_db):
    _seed(temp_db, **{OLD_EXPIRY: 5})
    _log_trade(temp_db, expiries=("2026-03-01",), transform_expiries=(OLD_EXPIRY,))
    assert db.plan_prune(temp_db, retention_days=90, today=TODAY)["prunable"] == []


def test_protection_does_not_spill_onto_untraded_expiries(temp_db):
    """The exemption must be narrow. A pruner that protects everything is as
    useless as one that protects nothing, and looks identical in the tests
    above."""
    _seed(temp_db, **{OLD_EXPIRY: 5, "2026-02-20": 7})
    _log_trade(temp_db, expiries=(OLD_EXPIRY,))
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert [e["expiry_date"] for e in plan["prunable"]] == ["2026-02-20"]


def test_protected_expiries_are_empty_when_no_journal_exists(temp_db):
    """The collector's database has no trades table until journal.py opens."""
    assert db.get_protected_expiries(temp_db) == set()


def test_unparseable_legs_raise_rather_than_protect_nothing(temp_db):
    """FAIL CLOSED. If a legs blob cannot be read, the expiries it names are
    unknown — and 'unknown' must not arrive at the pruner disguised as 'none'.
    Loud failure is recoverable; a silent empty set deletes a real trade's data.
    """
    db.init_trades_table(temp_db)
    db.insert_trade(temp_db, {
        "trade_id": "T-001", "entry_date": "2026-01-05", "entry_time": "10:00",
        "status": "Open", "contracts": 1, "initial_legs": "{{not json",
        "total_debit": 4.0,
    })
    with pytest.raises(json.JSONDecodeError):
        db.get_protected_expiries(temp_db)


# ─────────────────────────────────────────────────────────────────────────────
# execute_prune — what it deletes, and everything it must not
# ─────────────────────────────────────────────────────────────────────────────

def test_execute_deletes_exactly_the_planned_rows(temp_db):
    _seed(temp_db, **{OLD_EXPIRY: 5, NEWER_EXPIRY: 3})
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert db.execute_prune(temp_db, plan) == 5
    with db.get_conn(temp_db) as conn:
        left = {r["expiry_date"] for r in
                conn.execute("SELECT DISTINCT expiry_date FROM option_rows")}
    assert left == {NEWER_EXPIRY}


def test_execute_never_touches_the_summaries_or_snapshots(temp_db):
    """atm_iv_by_expiry is what makes pruning acceptable — it is the history
    that survives. If it ever goes with the rows, the policy is a data loss."""
    _seed(temp_db, **{OLD_EXPIRY: 5})
    atm_before = _count(temp_db, "atm_iv_by_expiry")
    snaps_before = _count(temp_db, "snapshots")

    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    db.execute_prune(temp_db, plan)

    assert _count(temp_db, "option_rows") == 0
    assert _count(temp_db, "atm_iv_by_expiry") == atm_before
    assert _count(temp_db, "snapshots") == snaps_before


def test_execute_acts_on_the_plan_it_was_given_not_a_fresh_query(temp_db):
    """The report shown and the rows deleted must be the same set.

    Here the plan is built, then a second aged expiry appears. Executing that
    stale plan must delete only what the plan named — anything else means the
    user approved one number and a different one happened.
    """
    _seed(temp_db, **{OLD_EXPIRY: 5})
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    _seed(temp_db, **{"2026-02-20": 7})

    assert db.execute_prune(temp_db, plan) == 5
    with db.get_conn(temp_db) as conn:
        left = {r["expiry_date"] for r in
                conn.execute("SELECT DISTINCT expiry_date FROM option_rows")}
    assert left == {"2026-02-20"}


def test_execute_on_an_empty_plan_deletes_nothing(temp_db):
    """Today's real case: nothing is old enough yet."""
    _seed(temp_db, **{NEWER_EXPIRY: 3})
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert db.execute_prune(temp_db, plan) == 0
    assert _count(temp_db, "option_rows") == 3


def test_execute_is_all_or_nothing(temp_db):
    """One transaction. A failure part-way must not leave the database holding
    some expiries pruned and others not — that state is indistinguishable from
    a correct run and would never be noticed.

    The failure is injected with a trigger that aborts on the SECOND expiry, so
    the first DELETE has genuinely run before the error arrives. A rollback that
    did not work would leave 7 rows here, not 12.
    """
    _seed(temp_db, **{OLD_EXPIRY: 5, "2026-02-20": 7})
    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    assert [e["expiry_date"] for e in plan["prunable"]] == [OLD_EXPIRY, "2026-02-20"]

    with db.managed_conn(temp_db) as conn:
        conn.execute(
            "CREATE TRIGGER boom BEFORE DELETE ON option_rows "
            "WHEN OLD.expiry_date = '2026-02-20' "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )

    with pytest.raises(sqlite3.Error):
        db.execute_prune(temp_db, plan)

    assert _count(temp_db, "option_rows") == 12


# ─────────────────────────────────────────────────────────────────────────────
# Retention and the entry-IV gate, together (ADR-044)
# ─────────────────────────────────────────────────────────────────────────────

def test_a_trades_own_context_survives_a_prune_of_everything_around_it(temp_db):
    """End to end: the two halves of M3 doing their job at once.

    The trade's own expiry is held back by the protection rule, and its entry-IV
    context is on the row regardless — so even a prune that ignored protection
    could not make the Regime Analysis forget this trade.
    """
    db.init_trades_table(temp_db)
    sid = _seed(temp_db, **{OLD_EXPIRY: 5, "2026-02-20": 7})
    with db.managed_conn(temp_db) as conn:
        conn.execute("UPDATE snapshots SET snapshot_timestamp = ? "
                     "WHERE snapshot_id = ?", ("2026-01-05 15:00:00", sid))
    db.insert_trade(temp_db, {
        "trade_id": "T-001", "entry_date": "2026-01-05", "entry_time": "10:00",
        "status": "Open", "contracts": 1, "total_debit": 4.0,
        "initial_legs": json.dumps([
            {"expiry": OLD_EXPIRY, "type": "Call", "action": "Sell to Open",
             "strike": 6001.0, "fill": 1.0},
            {"expiry": OLD_EXPIRY, "type": "Put", "action": "Sell to Open",
             "strike": 6000.0, "fill": 1.0},
        ]),
    })
    before = db.get_trade(temp_db, "T-001")["entry_front_iv"]
    assert before is not None, "fixture failed to produce a context to protect"

    plan = db.plan_prune(temp_db, retention_days=90, today=TODAY)
    db.execute_prune(temp_db, plan)

    assert db.get_trade(temp_db, "T-001")["entry_front_iv"] == before
