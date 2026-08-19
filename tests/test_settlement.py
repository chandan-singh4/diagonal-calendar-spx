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

from datetime import date

import db
from dataaccess import queries
import schwab_client
from test_db import add_snapshot, atm, opt, ts_ago

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


# ── the readers keep the two contracts apart ─────────────────────────────────
#
# CHANGED 2026-08-19. These checks used to require that every read COLLAPSE the
# pair down to one row — "store both, show one" — which was the deliberate first
# step: it got the p.m. prices recorded without disturbing any screen. That step
# is over. The requirement is now that both survive the read and are told apart
# by a display key, "2026-08-21" for the p.m. contract and "2026-08-21 (AM)" for
# the a.m. one (core/contract.py). The old expectations are replaced rather than
# deleted, so the reversal stays visible to whoever reads this file next.

def _both(sid):
    return [
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=2.0, iv=0.18, settlement="AM"),
        opt(sid, THIRD_FRIDAY, STRIKE, "C", mark=9.0, iv=0.44, settlement="PM"),
    ]


ORDINARY = "2026-08-24"


def test_the_chain_reader_returns_both_contracts(temp_db):
    """The whole point of the display half: neither price may be dropped."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, _both(sid))

    rows = db.get_option_chain(temp_db, sid)
    assert sorted(r["mark"] for r in rows) == [2.0, 9.0]
    assert sorted(r["settlement"] for r in rows) == ["AM", "PM"]


def test_the_chain_reader_leaves_an_ordinary_expiry_with_one_row(temp_db):
    """A weekly lists one contract and must not sprout a second."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, ORDINARY, STRIKE, "C", mark=4.0, settlement="PM")])
    assert len(db.get_option_chain(temp_db, sid)) == 1


def test_the_load_boundary_gives_the_two_contracts_two_display_keys(temp_db):
    """Where a row becomes something the screen can offer in a dropdown."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, _both(sid) + [
        opt(sid, ORDINARY, STRIKE, "C", mark=4.0, settlement="PM")])

    df = queries.load_chain_df(temp_db, sid)
    assert sorted(df["expiry"].unique()) == [
        "2026-08-21", "2026-08-21 (AM)", "2026-08-24"]
    # the p.m. contract keeps the bare key, so nothing saved earlier moves
    bare = df[df["expiry"] == THIRD_FRIDAY]
    assert bare["mark"].tolist() == [9.0]


def test_the_load_boundary_keeps_a_real_date_alongside_the_key(temp_db):
    """'expiry' stops being parseable as a date; 'expiry_date' must remain so,
    or every chart that does day arithmetic breaks at once."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, _both(sid))

    df = queries.load_chain_df(temp_db, sid)
    assert set(df["expiry_date"]) == {THIRD_FRIDAY}
    for value in df["expiry_date"]:
        date.fromisoformat(value)


def test_the_iv_history_reader_separates_the_two_contracts(temp_db):
    """One chart each, not one blended chart. Passing the display key straight
    through is what keeps them apart."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, _both(sid))

    am = queries.load_contract_hist(temp_db, "2026-08-21 (AM)", STRIKE, "CALL", 5)
    pm = queries.load_contract_hist(temp_db, "2026-08-21", STRIKE, "CALL", 5)
    assert am["iv"].tolist() == [18.0]
    assert pm["iv"].tolist() == [44.0]


def test_the_iv_history_reader_still_returns_an_ordinary_pm_expiry(temp_db):
    """BUG-026 at the chart that drives Strike Detail: nearly every expiry is
    p.m.-settled, so this is the common case, not the exotic one."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, ORDINARY, STRIKE, "C", iv=0.21, settlement="PM")])

    hist = queries.load_contract_hist(temp_db, ORDINARY, STRIKE, "CALL", 5)
    assert hist["iv"].tolist() == [21.0]


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


# ── the two mark-history charts ──────────────────────────────────────────────
#
# These are the reads behind the Entry Analysis charts, and they are where a
# display key does the most work: each takes a front AND a back contract, and
# the front may be the a.m. one while the back is an ordinary weekly. Until
# 2026-08-19 they collapsed the pair, so the a.m. row won and the p.m. prices
# were invisible on every chart at once.

BACK = "2026-09-16"          # an ordinary weekly, one contract only
CALL_S, PUT_S = 6050.0, 5950.0


def _six_legs(sid, *, front, front_settlement, front_call_mark):
    """The six legs both charts need, at one snapshot."""
    def leg(expiry, strike, right, mark, settlement):
        return opt(sid, expiry, strike, right, mark=mark, bid=mark - 0.5,
                   ask=mark + 0.5, settlement=settlement)
    return [
        leg(front, CALL_S, "C", front_call_mark, front_settlement),
        leg(front, PUT_S, "P", 10.0, front_settlement),
        leg(BACK, CALL_S, "C", 30.0, "PM"),
        leg(BACK, PUT_S, "P", 30.0, "PM"),
        leg(front, CALL_S + 5, "C", 5.0, front_settlement),
        leg(front, PUT_S - 5, "P", 5.0, front_settlement),
    ]


@pytest.fixture()
def two_front_contracts(temp_db):
    """One snapshot carrying BOTH third-Friday contracts as the front leg,
    priced differently — the a.m. one cheaper, as it is in the real chain,
    because it has a day less of life left."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db,
        _six_legs(sid, front=THIRD_FRIDAY, front_settlement="AM",
                  front_call_mark=6.95)
        + _six_legs(sid, front=THIRD_FRIDAY, front_settlement="PM",
                    front_call_mark=9.05))
    return temp_db, sid


def test_the_mark_history_chart_prices_the_two_contracts_apart(two_front_contracts):
    """The number Chandan reads off the screen. One key must not return the
    other contract's price, and neither may return a blend of the two."""
    db_path, _ = two_front_contracts

    def front_call(key):
        rows = db.get_transform_mark_history(db_path, key, BACK,
                                             CALL_S, PUT_S, days=5)
        assert len(rows) == 1, f"{key!r} gave {len(rows)} rows, expected 1"
        return rows[0]["front_call_mark"]

    assert front_call(THIRD_FRIDAY) == 9.05
    assert front_call(THIRD_FRIDAY + " (AM)") == 6.95


def test_the_diagonal_scatter_prices_the_two_contracts_apart(two_front_contracts):
    """Same again for the scatter, which additionally joins the daily ATM-IV
    summary. That table carries its own settlement column since BUG-028, so the
    two contracts get two summary rows — and the two IVs below are deliberately
    different, because a join that ignored settlement would still find A row and
    return a chart that looked perfectly fine while quoting the wrong contract.
    """
    db_path, sid = two_front_contracts
    db.insert_atm_iv_records(db_path, [
        atm(sid, THIRD_FRIDAY, 0.30, settlement="AM"),
        atm(sid, THIRD_FRIDAY, 0.20, settlement="PM"),
        atm(sid, BACK, 0.20, settlement="PM"),
    ])

    def front_call(key):
        rows = db.get_diagonal_history(db_path, key, BACK,
                                       CALL_S, PUT_S, days=5)
        assert len(rows) == 1, f"{key!r} gave {len(rows)} rows, expected 1"
        return rows[0]["front_call_mark"], rows[0]["front_iv"]

    assert front_call(THIRD_FRIDAY) == (9.05, 0.20)
    assert front_call(THIRD_FRIDAY + " (AM)") == (6.95, 0.30)


def test_an_ordinary_weekly_front_is_unaffected(temp_db):
    """Nearly every chart Chandan draws has a weekly on both legs. The label
    machinery must be invisible there."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db,
        _six_legs(sid, front=ORDINARY, front_settlement="PM",
                  front_call_mark=7.0))

    rows = db.get_transform_mark_history(temp_db, ORDINARY, BACK,
                                         CALL_S, PUT_S, days=5)
    assert [r["front_call_mark"] for r in rows] == [7.0]


def test_a_weekly_front_has_no_am_contract_to_chart(temp_db):
    """Asking for an a.m. contract where none was ever listed returns nothing,
    rather than quietly handing back the p.m. one under the wrong name."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db,
        _six_legs(sid, front=ORDINARY, front_settlement="PM",
                  front_call_mark=7.0))

    assert db.get_transform_mark_history(temp_db, ORDINARY + " (AM)", BACK,
                                         CALL_S, PUT_S, days=5) == []


# ---------------------------------------------------------------------------
# The daily A.T.M. summary (BUG-028)
#
# atm_iv_by_expiry held one row per DATE, so on the third Friday the two
# contracts fought over one slot and whichever the collector wrote last won.
# Every IV chart, every day-change figure and the scatter's IV ratio read that
# table, so the number they showed was a coin toss with no way to tell.
#
# The column and the reads below close it. These checks run against a real
# temporary database rather than comparing SQL strings, because a predicate
# that READS right and SELECTS the wrong row is exactly the failure this
# session already shipped once.
# ---------------------------------------------------------------------------

def _atm_chain(*rows):
    return pd.DataFrame(
        list(rows),
        columns=["expiry", "settlement", "strike", "side", "iv", "dte"])


def test_the_collector_summarises_both_third_friday_contracts():
    """One row per CONTRACT, not per date. Before this the p.m. contract had
    no summary row at all and could not be charted."""
    df = _atm_chain(
        (THIRD_FRIDAY, "AM", STRIKE, "CALL", 18.0, 2),
        (THIRD_FRIDAY, "AM", STRIKE, "PUT",  18.0, 2),
        (THIRD_FRIDAY, "PM", STRIKE, "CALL", 22.0, 2),
        (THIRD_FRIDAY, "PM", STRIKE, "PUT",  22.0, 2),
    )
    records = collector._compute_atm_iv_records(df, STRIKE, snapshot_id=1)

    assert {(r["expiry_date"], r["settlement"]) for r in records} == {
        (THIRD_FRIDAY, "AM"), (THIRD_FRIDAY, "PM")}
    assert {round(r["atm_avg_iv"], 4) for r in records} == {0.18, 0.22}


def test_an_ordinary_expiry_still_gets_exactly_one_summary_row():
    """Nearly every SPX expiry is a p.m. weekly. Splitting by settlement must
    not multiply those — the count is the whole coverage check the collector
    uses to decide a snapshot is COMPLETE."""
    df = _atm_chain(
        (ORDINARY, "PM", STRIKE, "CALL", 21.0, 5),
        (ORDINARY, "PM", STRIKE, "PUT",  21.0, 5),
    )
    records = collector._compute_atm_iv_records(df, STRIKE, snapshot_id=1)
    assert len(records) == 1
    assert records[0]["settlement"] == "PM"


def test_a_chain_with_no_settlement_recorded_still_summarises():
    """pandas' groupby drops null keys unless told not to. If that bit were
    wrong the collector would write NOTHING on any chain that arrived without
    settlement, and the snapshot would be marked FAILED with no explanation."""
    df = _atm_chain(
        (ORDINARY, None, STRIKE, "CALL", 21.0, 5),
        (ORDINARY, None, STRIKE, "PUT",  21.0, 5),
    )
    records = collector._compute_atm_iv_records(df, STRIKE, snapshot_id=1)
    assert len(records) == 1
    assert records[0]["settlement"] is None


def test_the_front_of_the_term_structure_is_the_am_contract():
    """The two share a DTE. Everything downstream is measured as a spread FROM
    records[0], so which of them holds that slot decides what every spread and
    ratio in the table means.

    This pins the PROPERTY, not the line that delivers it: pandas' groupby
    already emits "AM" before "PM", so the explicit tie-break in the collector
    can be deleted without this failing. Said plainly in collector.py too."""
    df = _atm_chain(
        (THIRD_FRIDAY, "PM", STRIKE, "CALL", 22.0, 2),
        (THIRD_FRIDAY, "PM", STRIKE, "PUT",  22.0, 2),
        (THIRD_FRIDAY, "AM", STRIKE, "CALL", 18.0, 2),
        (THIRD_FRIDAY, "AM", STRIKE, "PUT",  18.0, 2),
        (BACK,         "PM", STRIKE, "CALL", 24.0, 28),
        (BACK,         "PM", STRIKE, "PUT",  24.0, 28),
    )
    records = collector._compute_atm_iv_records(df, STRIKE, snapshot_id=1)

    assert records[0]["settlement"] == "AM"
    assert records[0]["iv_spread_to_front"] is None
    assert round(records[1]["iv_spread_to_front"], 4) == 0.04   # 0.22 - 0.18


def _two_summaries(temp_db):
    """One snapshot carrying both third-Friday summary rows, priced apart."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_atm_iv_records(temp_db, [
        atm(sid, THIRD_FRIDAY, 0.18, settlement="AM"),
        atm(sid, THIRD_FRIDAY, 0.22, settlement="PM"),
    ])
    db.finalize_snapshot(temp_db, sid, status="COMPLETE", strikes_fetched=4,
                         expiries_fetched=1, collection_latency_ms=100)
    return sid


def test_the_iv_history_chart_reads_the_contract_it_was_asked_for(temp_db):
    _two_summaries(temp_db)

    def iv(key):
        rows = db.get_atm_iv_history(temp_db, key, days=5)
        assert len(rows) == 1, f"{key!r} gave {len(rows)} rows, expected 1"
        return round(rows[0]["atm_avg_iv"], 4)

    assert iv(THIRD_FRIDAY) == 0.22
    assert iv(THIRD_FRIDAY + " (AM)") == 0.18


def test_the_day_change_figure_reads_the_contract_it_was_asked_for(temp_db):
    _two_summaries(temp_db)

    def iv(key):
        rows = db.get_latest_atm_iv_snapshots(temp_db, key, n=2)
        assert len(rows) == 1, f"{key!r} gave {len(rows)} rows, expected 1"
        return round(rows[0]["atm_avg_iv"], 4)

    assert iv(THIRD_FRIDAY) == 0.22
    assert iv(THIRD_FRIDAY + " (AM)") == 0.18


def test_the_read_layer_passes_the_label_through_rather_than_stripping_it(temp_db):
    """dataaccess/queries.py used to call date_of() here, which threw the label
    away before the query ever saw it. Both contracts then returned the same
    series and the split above would have been invisible from the screen."""
    _two_summaries(temp_db)

    am = queries.load_atm_hist(temp_db, THIRD_FRIDAY + " (AM)", 5)
    pm = queries.load_atm_hist(temp_db, THIRD_FRIDAY, 5)
    assert round(am["atm_iv"].iloc[0], 2) == 18.0
    assert round(pm["atm_iv"].iloc[0], 2) == 22.0

    am_latest = queries.load_latest_atm_iv(temp_db, THIRD_FRIDAY + " (AM)", n=2)
    pm_latest = queries.load_latest_atm_iv(temp_db, THIRD_FRIDAY, n=2)
    assert round(am_latest[0]["atm_avg_iv"], 4) == 0.18
    assert round(pm_latest[0]["atm_avg_iv"], 4) == 0.22


def test_a_legacy_summary_row_is_attributed_the_same_way_the_prices_are(temp_db):
    """Two months of summaries carry no settlement. They must land on the same
    contract as the option_rows taken in the same cycle, or the IV chart and the
    price chart would disagree about which contract they are showing."""
    before = add_snapshot(temp_db, "2026-08-19 15:30:00")
    on_the_day = add_snapshot(temp_db, "2026-08-21 15:30:00")
    for sid, iv_val in ((before, 0.18), (on_the_day, 0.22)):
        db.insert_atm_iv_records(temp_db, [atm(sid, THIRD_FRIDAY, iv_val)])
        db.finalize_snapshot(temp_db, sid, status="COMPLETE", strikes_fetched=2,
                             expiries_fetched=1, collection_latency_ms=100)

    def ivs(key):
        return sorted(round(r["atm_avg_iv"], 4)
                      for r in db.get_atm_iv_history(temp_db, key, days=3650))

    assert ivs(THIRD_FRIDAY + " (AM)") == [0.18]
    assert ivs(THIRD_FRIDAY) == [0.22]


def test_the_two_contracts_cannot_share_a_summary_slot_again(temp_db):
    """The constraint, not the convention. Without it a future change that
    grouped by date again would corrupt the table silently, exactly as before —
    the whole point of BUG-028 is that nothing complained."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_atm_iv_records(temp_db, [atm(sid, THIRD_FRIDAY, 0.18, settlement="AM")])

    with pytest.raises(sqlite3.IntegrityError):
        db.insert_atm_iv_records(temp_db,
                                 [atm(sid, THIRD_FRIDAY, 0.19, settlement="AM")])
