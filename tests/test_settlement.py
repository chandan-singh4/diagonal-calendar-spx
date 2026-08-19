"""AM vs PM settlement on the third-Friday monthly (BUG-023).

SPX lists two different options for the same third-Friday date and strike: the
traditional monthly, which settles at the OPENING price and stops trading the
evening before, and the weekly (SPXW), which trades all day and settles at the
CLOSE. Schwab returns both under one expiry key.

Before this fix the collector could not tell them apart and the database — whose
uniqueness rule had no room for the difference — silently discarded one. The
evidence was in plain sight for two months: exactly 160 rows dropped per cycle,
2,181 identical warnings, and an open-interest reading on the 17 July monthly
that fell from 266,366 to 99,194 overnight because the row had quietly stopped
describing the same contract.

These checks are about that: both contracts get stored, and nothing that reads
the old data notices the new arrival.
"""

import sqlite3

import pytest

import db
import schwab_client
from test_db import add_snapshot, opt, ts_ago

THIRD_FRIDAY = "2026-08-21"
STRIKE = 6050.0


# ── classification ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("contract,expected", [
    ({"settlementType": "A"},                             "AM"),
    ({"settlementType": "P"},                             "PM"),
    ({"settlementType": "a"},                             "AM"),
    ({"symbol": "SPXW  260821C06050000"},                 "PM"),
    ({"symbol": "SPX   260821C06050000"},                 "AM"),
    # the broker's own field outranks the naming convention
    ({"settlementType": "A", "symbol": "SPXW  260821C0"}, "AM"),
])
def test_settlement_is_read_from_the_contract(contract, expected):
    assert schwab_client.settlement_of(contract) == expected


@pytest.mark.parametrize("contract", [{}, {"settlementType": ""}, {"symbol": "QQQ 123"}])
def test_an_unrecognised_contract_is_blank_not_guessed(contract):
    """None means 'not recorded'. Defaulting to 'AM' would write a wrong answer
    into a record that cannot be re-collected — worse than an honest blank."""
    assert schwab_client.settlement_of(contract) is None


def test_the_parser_carries_settlement_off_the_chain():
    raw = {
        "callExpDateMap": {
            THIRD_FRIDAY + ":2": {
                str(STRIKE): [
                    {"symbol": "SPX   260821C06050000",
                     "settlementType": "A", "volatility": 12.0},
                    {"symbol": "SPXW  260821C06050000",
                     "settlementType": "P", "volatility": 13.0},
                ]
            }
        },
        "putExpDateMap": {},
    }
    frame = schwab_client.chain_to_dataframe(raw)
    assert sorted(frame["settlement"]) == ["AM", "PM"]


# ── the fix itself ───────────────────────────────────────────────────────────

def test_both_contracts_survive_the_same_snapshot(temp_db):
    """The bug, stated as a check. Before the fix the second row was discarded."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    stored = db.insert_option_rows(temp_db, [
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0, settlement="AM"),
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=9.0, settlement="PM"),
    ])
    assert stored == 2, "the p.m. contract was discarded — BUG-023 has returned"

    conn = sqlite3.connect(temp_db)
    try:
        rows = dict(conn.execute(
            "SELECT settlement, mark FROM option_rows").fetchall())
    finally:
        conn.close()
    assert rows == {"AM": 2.0, "PM": 9.0}


def test_a_genuine_duplicate_is_still_rejected(temp_db):
    """Widening uniqueness must not stop it doing its original job: the same
    contract twice in one snapshot is still the sawtooth bug."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    stored = db.insert_option_rows(temp_db, [
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0, settlement="PM"),
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=99.0, settlement="PM"),
    ])
    assert stored == 1


def test_legacy_blank_rows_are_still_deduplicated(temp_db):
    """SQLite treats every NULL in a UNIQUE index as distinct from every other
    NULL, so indexing the bare column would have quietly stopped deduplicating
    the pre-2026-08-19 rows. COALESCE(settlement,'?') is what prevents that."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    stored = db.insert_option_rows(temp_db, [
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0),
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=99.0),
    ])
    assert stored == 1


def test_a_caller_that_omits_settlement_gets_a_blank_not_a_crash(temp_db):
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    payload = opt(sid, THIRD_FRIDAY, STRIKE, "C")
    del payload["settlement"]
    assert db.insert_option_rows(temp_db, [payload]) == 1


# ── the readers must not notice ──────────────────────────────────────────────

def _both(sid):
    return [
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0, iv=0.18, settlement="AM"),
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=9.0, iv=0.44, settlement="PM"),
    ]


def test_the_chain_reader_returns_one_row_per_contract(temp_db):
    """An unguarded read returns TWO rows for this strike, and every six-leg
    join downstream fans out into the sawtooth."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, _both(sid))

    rows = db.get_option_chain(temp_db, sid)
    assert len(rows) == 1
    assert rows[0]["mark"] == 2.0, "the a.m. contract is what the dashboard shows"


ORDINARY = "2026-08-24"


def test_an_ordinary_pm_expiry_is_still_shown(temp_db):
    """BUG-026 — the mistake that reached production.

    Almost every SPX expiry is an SPXW weekly and therefore P.M.-settled; the
    a.m. contract exists ONLY on the third-Friday monthly. A guard of
    `settlement IS NOT 'PM'` therefore hides ~94% of the chain — on the live
    dashboard it cut a 2,986-row snapshot down to the 160 rows of one expiry.
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, ORDINARY, STRIKE, "C", mark=4.0, settlement="PM"),
    ])
    rows = db.get_option_chain(temp_db, sid)
    assert [r["mark"] for r in rows] == [4.0], (
        "a p.m.-settled weekly with no a.m. twin must still be shown")


def test_only_the_shadowed_pm_row_is_hidden(temp_db):
    """Both rules at once: the monthly shows a.m., the weekly shows p.m."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0, settlement="AM"),
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=9.0, settlement="PM"),
        opt(sid, ORDINARY,     STRIKE, "C", mark=4.0, settlement="PM"),
    ])
    shown = {(r["expiry_date"], r["mark"]) for r in db.get_option_chain(temp_db, sid)}
    assert shown == {(THIRD_FRIDAY, 2.0), (ORDINARY, 4.0)}


def test_a_pm_row_is_hidden_only_for_its_own_strike(temp_db):
    """The a.m. twin must match strike and side, not merely the expiry date —
    the monthly lists strikes the a.m. contract does not."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, THIRD_FRIDAY, STRIKE,   "C", mark=2.0, settlement="AM"),
        opt(sid, THIRD_FRIDAY, STRIKE,   "C", mark=9.0, settlement="PM"),
        opt(sid, THIRD_FRIDAY, STRIKE+5, "C", mark=7.0, settlement="PM"),
        opt(sid, THIRD_FRIDAY, STRIKE,   "P", mark=6.0, settlement="PM"),
    ])
    shown = sorted(r["mark"] for r in db.get_option_chain(temp_db, sid))
    assert shown == [2.0, 6.0, 7.0]


def test_the_iv_history_reader_ignores_the_shadowed_pm_contract(temp_db):
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, _both(sid))

    history = db.get_contract_iv_history(temp_db, THIRD_FRIDAY, STRIKE, "C")
    assert [r["iv"] for r in history] == [0.18]


def test_the_iv_history_reader_still_returns_an_ordinary_pm_expiry(temp_db):
    """BUG-026 at the chart that drives Strike Detail."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, ORDINARY, STRIKE, "C", iv=0.21, settlement="PM")])

    history = db.get_contract_iv_history(temp_db, ORDINARY, STRIKE, "C")
    assert [r["iv"] for r in history] == [0.21]


def test_legacy_rows_are_still_read(temp_db):
    """`IS NOT 'PM'` is NULL-safe; a plain `!=` would silently hide the entire
    pre-2026-08-19 history, which is most of the database."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0)])
    assert len(db.get_option_chain(temp_db, sid)) == 1


# ── the migration ────────────────────────────────────────────────────────────

def test_the_migration_adds_the_column_and_swaps_the_index(tmp_path):
    """Rehearsal of what will run against the live 2.7 GB file."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    try:
        conn.executescript(db._DDL)
        conn.execute("DROP TABLE option_rows")
        # option_rows exactly as it stood before BUG-023: no settlement column.
        conn.execute("""
            CREATE TABLE option_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL
                    REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
                expiry_date TEXT NOT NULL, dte INTEGER NOT NULL,
                strike REAL NOT NULL,
                right TEXT NOT NULL CHECK(right IN ('C','P')),
                bid REAL, ask REAL, mark REAL, last REAL, iv REAL,
                delta REAL, gamma REAL, theta REAL, vega REAL,
                volume INTEGER, open_interest INTEGER,
                intrinsic_value REAL, time_value REAL)
        """)
        conn.execute("CREATE UNIQUE INDEX uq_option_rows_contract "
                     "ON option_rows(snapshot_id, expiry_date, strike, right)")
        conn.execute("INSERT INTO snapshots (snapshot_timestamp, status) "
                     "VALUES ('2026-07-01 15:00:00', 'COMPLETE')")
        conn.execute("INSERT INTO option_rows "
                     "(snapshot_id, expiry_date, dte, strike, right, mark) "
                     "VALUES (1, ?, 7, ?, 'C', 2.0)", (THIRD_FRIDAY, STRIKE))
        conn.commit()
    finally:
        conn.close()

    db.init_db(path)

    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(option_rows)")}
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        kept = conn.execute("SELECT mark, settlement FROM option_rows").fetchall()
    finally:
        conn.close()

    assert "settlement" in cols
    assert "uq_option_rows_contract_settle" in names
    assert "uq_option_rows_contract" not in names
    # the whole point: the existing history is untouched, and honestly blank
    assert kept == [(2.0, None)]

def test_running_init_db_twice_does_not_resurrect_the_old_index(temp_db):
    """BUG-025 — the migration must survive its own migration.

    The legacy deduplication block was guarded on the presence of
    uq_option_rows_contract, which the BUG-023 migration drops once it is
    superseded. On the SECOND call the legacy block therefore read "never
    migrated", recreated the superseded index, and that index — which cannot
    tell a.m. from p.m. — rejected every p.m. row on arrival.

    This was not caught before deployment because every existing check called
    init_db() exactly once. The collector calls it on every start.
    """
    db.init_db(temp_db)          # second call: what a collector restart does
    db.init_db(temp_db)          # third, for good measure

    conn = sqlite3.connect(temp_db)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
    finally:
        conn.close()
    assert "uq_option_rows_contract" not in names, (
        "the superseded index came back — p.m. rows will be silently discarded")
    assert "uq_option_rows_contract_settle" in names


def test_both_contracts_still_store_after_a_restart(temp_db):
    """The symptom BUG-025 actually produced, stated end to end."""
    db.init_db(temp_db)          # simulate the collector restarting
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    stored = db.insert_option_rows(temp_db, [
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0, settlement="AM"),
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=9.0, settlement="PM"),
    ])
    assert stored == 2


def test_a_restart_never_deletes_the_pm_rows_it_already_collected(temp_db):
    """The dangerous half. The legacy DELETE groups WITHOUT settlement, so if it
    ever re-fires it removes the p.m. contract and keeps the a.m. one — silently,
    and reported in the log as routine deduplication."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0, settlement="AM"),
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=9.0, settlement="PM"),
    ])

    db.init_db(temp_db)          # the restart

    conn = sqlite3.connect(temp_db)
    try:
        marks = sorted(r[0] for r in conn.execute(
            "SELECT mark FROM option_rows"))
    finally:
        conn.close()
    assert marks == [2.0, 9.0], "a restart destroyed collected p.m. prices"


# --------------------------------------------------------------------------
# The collector's own copy of the rule (BUG-026)
#
# db.py enforces "prefer the a.m. contract" in SQL for the dashboard; the
# collector must apply the identical rule in pandas before it computes the
# daily A.T.M. summaries, or the two disagree. This helper had no direct
# check, which is how a broken version reached the live collector twice.
# --------------------------------------------------------------------------

import pandas as pd

import collector


def _chain(*rows):
    return pd.DataFrame(
        list(rows), columns=["expiry", "strike", "side", "settlement", "iv"])


def test_collector_keeps_every_ordinary_pm_expiry():
    df = _chain(
        (ORDINARY, STRIKE, "CALL", "PM", 0.21),
        ("2026-08-26", STRIKE, "CALL", "PM", 0.22),
    )
    assert len(collector._shown_contract_only(df)) == 2


def test_collector_drops_only_the_shadowed_pm_row():
    df = _chain(
        (THIRD_FRIDAY, STRIKE, "CALL", "AM", 0.18),
        (THIRD_FRIDAY, STRIKE, "CALL", "PM", 0.19),
        (THIRD_FRIDAY, STRIKE, "PUT",  "PM", 0.20),
        (ORDINARY,     STRIKE, "CALL", "PM", 0.21),
    )
    kept = collector._shown_contract_only(df)
    assert sorted(kept["iv"]) == [0.18, 0.20, 0.21]


def test_collector_handles_a_chain_with_no_am_contract_at_all():
    """The common case: on all but one date a month, nothing is a.m.-settled."""
    df = _chain((ORDINARY, STRIKE, "CALL", "PM", 0.21))
    assert collector._shown_contract_only(df).equals(df)


def test_collector_handles_an_empty_chain():
    assert collector._shown_contract_only(_chain()).empty


def test_collector_tolerates_rows_with_no_settlement_recorded():
    """Legacy rows carry NULL; they are contracts too and must not vanish."""
    df = _chain(
        (ORDINARY, STRIKE, "CALL", None, 0.21),
        (THIRD_FRIDAY, STRIKE, "CALL", "AM", 0.18),
        (THIRD_FRIDAY, STRIKE, "CALL", "PM", 0.19),
    )
    kept = collector._shown_contract_only(df)
    assert sorted(kept["iv"]) == [0.18, 0.21]


def test_collector_summary_covers_every_expiry_not_just_the_monthly():
    """BUG-026 as the live collector reported it: 'ATM IV computed for 1/20
    expiries'. Nineteen p.m.-settled dates had been filtered away."""
    rows = [(f"2026-09-{d:02d}", STRIKE, "CALL", "PM", 0.20) for d in range(1, 21)]
    rows.append((THIRD_FRIDAY, STRIKE, "CALL", "AM", 0.18))
    kept = collector._shown_contract_only(_chain(*rows))
    assert kept["expiry"].nunique() == 21
