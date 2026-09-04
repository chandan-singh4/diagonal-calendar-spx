"""
Unit and integration tests for db.py — the database layer.

WHY THIS MODULE (M1.5): db.py is the single point every other component reads
through. collector.py writes nothing except through it; app.py and journal.py
read nothing except through it. It carried zero tests (DEBT-001), which meant
the module guarding 1.42 GB of irreplaceable history had no automated proof that
its read-only guarantee held, that its cascade deletes were armed, or that any
of its twenty-odd queries returned what their docstrings claim.

The risks that motivated the specific tests below, in priority order:

  1. The reader/writer split is a SAFETY property, not a style choice. If
     get_conn() ever stopped setting PRAGMA query_only, the dashboard could take
     a write lock and contend with the collector mid-session. Tested directly.
  2. Silent wrong answers. Every read here is a filter over history — a status
     filter dropped, a boundary flipped from < to <=, an ORDER BY reversed —
     and none of those crash. They just quietly return the wrong slice, which
     then renders as a chart nobody can tell is wrong.
  3. The UNIQUE-index migration in init_db() deletes rows. It is guarded so it
     runs once, and it is the only destructive statement in the module.

CONVENTION (as in test_iv_engine.py and test_journal_pl.py): behaviour that
looks wrong is PINNED rather than silently fixed, and cross-referenced to a
backlog ID. A test that changes production behaviour to suit itself proves
nothing — see STATUS.md, "Settled — don't reopen".

SCALE REMINDER: db.py stores and returns IV in DECIMAL form (0.18 = 18%).
That is the opposite of the iv_engine fixtures in conftest.py, which are in
percentage form. Every IV in this file is a decimal, deliberately.

Every test here uses the temp_db / trades_db fixtures, which build a throwaway
database under tmp_path and assert they are not the production path.
"""
from __future__ import annotations

import logging
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

import config
import db

pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

FRONT = "2026-08-07"
# An ordinary weekly, deliberately NOT the third Friday. These fixtures write
# legacy rows (no settlement recorded), and on the third Friday such a row is
# attributed to the a.m. contract, so reading it back with a bare date would
# correctly return nothing — see core/contract.py. The two-contract case is
# pinned in test_contract_key.py and test_settlement.py; here it would only
# obscure what these tests are actually about.
BACK = "2026-08-28"
CALL_STRIKE = 6050.0
PUT_STRIKE = 5950.0


def ts_ago(*, days: int = 0, minutes: int = 0) -> str:
    """A UTC timestamp string in db.py's stored format, offset into the past.

    Several read queries filter on datetime('now', '-N days'), so fixtures that
    hardcoded a calendar date would fall out of the window and start returning
    zero rows on some future date. Timestamps here are always relative to now.
    """
    when = datetime.now(UTC) - timedelta(days=days, minutes=minutes)
    return when.strftime("%Y-%m-%d %H:%M:%S")


def add_snapshot(path: str, ts: str, *, status: str = "COMPLETE",
                 spx: float | None = 6000.0, vix: float | None = 15.0,
                 session: str = "OPEN") -> int:
    """Create and (unless PARTIAL) finalize a snapshot. Returns snapshot_id."""
    sid = db.create_snapshot(
        path, ts, session, 60,
        underlying_price=spx, underlying_bid=spx, underlying_ask=spx,
        vix_value=vix,
    )
    if status != "PARTIAL":
        db.finalize_snapshot(path, sid, status, 10, 2, 500)
    return sid


def opt(sid: int, expiry: str, strike: float, right: str, *,
        bid: float | None = 1.0, ask: float | None = 3.0,
        mark: float | None = 2.0, iv: float | None = 0.18,
        dte: int = 7, settlement: str | None = None) -> dict:
    """One option_rows insert payload. All 19 bound parameters must be present.

    settlement defaults to None — the legacy value — so every fixture written
    before BUG-023 keeps describing exactly the row it always described.
    """
    return {
        "snapshot_id": sid, "expiry_date": expiry, "dte": dte,
        "strike": float(strike), "right": right, "settlement": settlement,
        "bid": bid, "ask": ask, "mark": mark, "last": mark,
        "iv": iv, "delta": 0.5, "gamma": 0.01, "theta": -0.5, "vega": 1.0,
        "volume": 100, "open_interest": 1000,
        "intrinsic_value": 0.0, "time_value": mark,
    }


def four_legs(sid: int, **kw) -> list[dict]:
    """The four diagonal legs: front/back x call/put, at the standard strikes."""
    return [
        opt(sid, FRONT, CALL_STRIKE, "C", **kw),
        opt(sid, BACK, CALL_STRIKE, "C", **kw),
        opt(sid, FRONT, PUT_STRIKE, "P", **kw),
        opt(sid, BACK, PUT_STRIKE, "P", **kw),
    ]


def wing_legs(sid: int, **kw) -> list[dict]:
    """The two extra front-expiry wings the Transform Order Mark needs."""
    return [
        opt(sid, FRONT, CALL_STRIKE + 5, "C", **kw),
        opt(sid, FRONT, PUT_STRIKE - 5, "P", **kw),
    ]


def atm(sid: int, expiry: str, avg_iv: float, *, dte: int = 7,
        settlement: str | None = None) -> dict:
    return {
        "snapshot_id": sid, "expiry_date": expiry,
        "settlement": settlement, "dte": dte,
        "atm_strike": 6000.0,
        "atm_call_iv": avg_iv, "atm_put_iv": avg_iv, "atm_avg_iv": avg_iv,
        "iv_spread_to_front": 0.0, "iv_ratio_to_front": 1.0,
    }


def table_names(path: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# The safety net itself
#
# If this fails, nothing else in this file can be trusted — it would mean the
# suite is capable of pointing at production.
# ─────────────────────────────────────────────────────────────────────────────

def test_temp_db_is_not_the_production_database(temp_db):
    assert temp_db != config.DB_PATH
    assert "dashboard.db" not in temp_db.replace("test_dashboard.db", "")


# ─────────────────────────────────────────────────────────────────────────────
# init_db — schema creation, idempotence, version gate
# ─────────────────────────────────────────────────────────────────────────────

def test_init_db_creates_every_expected_table(temp_db):
    assert {"schema_version", "snapshots", "option_rows",
            "atm_iv_by_expiry", "collection_gaps"} <= table_names(temp_db)


def test_init_db_now_creates_the_trades_table_too(temp_db):
    """REVERSED at M3.3 (ADR-051), deliberately — this test used to assert the
    opposite and was rewritten with the rule it pinned.

    The old separation kept the journal's schema out of the version number so
    that "the collector's schema path is unaffected by it". The cost was that
    no version could describe the database: ten columns had been added to the
    live file while `schema_version` still said 1. One database, one version.
    """
    assert "trades" in table_names(temp_db)


def test_init_db_is_idempotent_and_records_each_version_exactly_once(temp_db):
    db.init_db(temp_db)
    db.init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    try:
        rows = [r[0] for r in conn.execute(
            "SELECT version FROM schema_version ORDER BY version")]
    finally:
        conn.close()

    assert rows == list(range(1, db.SCHEMA_VERSION + 1)),         "one row per migration, in order, and no repeats however often it runs"


def test_init_db_refuses_a_database_from_newer_code(temp_db):
    """Old code opening a newer database must fail loudly, not half-work."""
    conn = sqlite3.connect(temp_db)
    try:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (?, ?, ?)",
            (db.SCHEMA_VERSION + 1, "2026-07-26 00:00:00", "from the future"),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="newer than"):
        db.init_db(temp_db)


def test_init_db_creates_the_uniqueness_index_on_option_rows(temp_db):
    """The uniqueness guarantee now spans settlement (BUG-023).

    uq_option_rows_contract is superseded by uq_option_rows_contract_settle and
    dropped: the old rule could not tell the a.m. and p.m. third-Friday
    contracts apart and silently discarded one of them. Both names are asserted
    so a half-applied migration — new index created, old one still present, each
    fighting the other on every insert — fails here rather than in production.
    """
    conn = sqlite3.connect(temp_db)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )}
    finally:
        conn.close()
    assert "uq_option_rows_contract_settle" in names
    assert "uq_option_rows_contract" not in names, "superseded index must be dropped"


def test_init_db_deduplicates_pre_existing_option_rows_once(tmp_path):
    """The one destructive statement in db.py.

    A database predating the UNIQUE index can hold the same contract twice in a
    snapshot; those duplicates fan out across the six-leg mark joins and render
    as a sawtooth. init_db() must delete them, keep the EARLIEST row (MIN(id)),
    and then make recurrence impossible.
    """
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    try:
        # Build the schema WITHOUT the uniqueness index, as the old code did.
        conn.executescript(db._DDL)
        conn.execute(
            "INSERT INTO snapshots (snapshot_timestamp, status) "
            "VALUES ('2026-07-01 15:00:00', 'COMPLETE')"
        )
        for mark in (2.0, 99.0, 99.0):        # first row wins; 2.0 is earliest
            conn.execute(
                "INSERT INTO option_rows "
                "(snapshot_id, expiry_date, dte, strike, right, mark) "
                "VALUES (1, ?, 7, 6050.0, 'C', ?)", (FRONT, mark)
            )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM option_rows").fetchone()[0] == 3
    finally:
        conn.close()

    db.init_db(path)

    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT mark FROM option_rows").fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == [2.0], "kept row must be the earliest (MIN(id))"


def test_uniqueness_index_prevents_a_contract_appearing_twice(temp_db):
    """The guarantee the migration exists to establish, tested at the writer.

    insert_option_rows uses INSERT OR IGNORE, so the second copy is dropped
    rather than raising. This is what makes the GROUP BY s.snapshot_id in the
    mark-history queries a belt-and-braces measure on a fresh database rather
    than the load-bearing fix it was written as.
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C", mark=2.0)])
    db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C", mark=99.0)])

    chain = db.get_option_chain(temp_db, sid)
    assert len(chain) == 1
    assert chain[0]["mark"] == 2.0, "the first write wins; OR IGNORE drops the rest"


# ─────────────────────────────────────────────────────────────────────────────
# Connection management — the reader/writer split
#
# "The read path is enforced, not merely conventional" (db.py docstring).
# These tests are what make that sentence true rather than aspirational.
# ─────────────────────────────────────────────────────────────────────────────

def test_get_conn_physically_cannot_write(temp_db):
    with db.get_conn(temp_db) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute(
            "INSERT INTO collection_gaps "
            "(gap_start, gap_end, gap_minutes, detected_at) "
            "VALUES ('a', 'b', 1.0, 'c')"
        )


def test_get_conn_returns_rows_addressable_by_column_name(temp_db):
    """row_factory = sqlite3.Row. Every caller in app.py indexes by name."""
    add_snapshot(temp_db, ts_ago(minutes=5), spx=6123.45)
    row = db.get_latest_complete_snapshot(temp_db)
    assert row["underlying_price"] == 6123.45


def test_managed_conn_commits_on_success(temp_db):
    with db.managed_conn(temp_db) as conn:
        conn.execute(
            "INSERT INTO snapshots (snapshot_timestamp, status) "
            "VALUES ('2026-07-01 15:00:00', 'COMPLETE')"
        )
    assert db.get_last_snapshot_timestamp(temp_db) == "2026-07-01 15:00:00"


def test_managed_conn_rolls_back_on_exception(temp_db):
    """A crash mid-cycle must leave no partial write behind."""
    class Boom(Exception):
        pass

    with pytest.raises(Boom), db.managed_conn(temp_db) as conn:
        conn.execute(
            "INSERT INTO snapshots (snapshot_timestamp, status) "
            "VALUES ('2026-07-01 15:00:00', 'COMPLETE')"
        )
        raise Boom

    assert db.get_last_snapshot_timestamp(temp_db) is None


def test_writer_connection_enables_cascade_delete(temp_db):
    """PRAGMA foreign_keys = ON is what makes ON DELETE CASCADE real.

    SQLite defaults foreign keys OFF. Without the pragma the REFERENCES clauses
    in the DDL are decorative, and deleting a snapshot would orphan its option
    rows rather than removing them — the exact failure mode a future retention
    job (M3) would hit.
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, four_legs(sid))
    db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, 0.18)])
    assert len(db.get_option_chain(temp_db, sid)) == 4

    with db.managed_conn(temp_db) as conn:
        conn.execute("DELETE FROM snapshots WHERE snapshot_id = ?", (sid,))

    assert db.get_option_chain(temp_db, sid) == []
    with db.get_conn(temp_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM atm_iv_by_expiry"
        ).fetchone()["n"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Write operations
# ─────────────────────────────────────────────────────────────────────────────

def test_create_snapshot_opens_as_partial(temp_db):
    """Created at cycle START so a record survives a crash during insertion."""
    sid = db.create_snapshot(temp_db, ts_ago(minutes=1), "OPEN", 60,
                             underlying_price=6000.0)
    with db.get_conn(temp_db) as conn:
        row = conn.execute(
            "SELECT status FROM snapshots WHERE snapshot_id = ?", (sid,)
        ).fetchone()
    assert row["status"] == "PARTIAL"


def test_create_snapshot_returns_increasing_ids(temp_db):
    a = db.create_snapshot(temp_db, ts_ago(minutes=2), "OPEN", 60)
    b = db.create_snapshot(temp_db, ts_ago(minutes=1), "OPEN", 60)
    assert b > a


def test_finalize_snapshot_seals_the_record(temp_db):
    sid = db.create_snapshot(temp_db, ts_ago(minutes=1), "OPEN", 60)
    db.finalize_snapshot(temp_db, sid, "COMPLETE", 160, 20, 10500)
    with db.get_conn(temp_db) as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ?", (sid,)
        ).fetchone()
    assert row["status"] == "COMPLETE"
    assert row["strikes_fetched"] == 160
    assert row["expiries_fetched"] == 20
    assert row["collection_latency_ms"] == 10500
    assert row["error_message"] is None


def test_finalize_snapshot_records_a_failure_message(temp_db):
    sid = db.create_snapshot(temp_db, ts_ago(minutes=1), "OPEN", 60)
    db.finalize_snapshot(temp_db, sid, "FAILED", 0, 0, 900,
                         error_message="Schwab 401")
    with db.get_conn(temp_db) as conn:
        row = conn.execute(
            "SELECT status, error_message FROM snapshots WHERE snapshot_id = ?",
            (sid,)).fetchone()
    assert row["status"] == "FAILED"
    assert row["error_message"] == "Schwab 401"


@pytest.mark.parametrize("bad_status", ["DONE", "complete", "OK", ""])
def test_snapshot_status_is_constrained_by_the_schema(temp_db, bad_status):
    """CHECK(status IN (...)) — a typo in a status string must not persist."""
    sid = db.create_snapshot(temp_db, ts_ago(minutes=1), "OPEN", 60)
    with pytest.raises(sqlite3.IntegrityError):
        db.finalize_snapshot(temp_db, sid, bad_status, 1, 1, 1)


def test_option_right_check_constraint_rejects_anything_but_c_or_p(temp_db):
    """The constraint itself is real — proven against a PLAIN insert.

    'C'/'P', not 'CALL'/'PUT'. Both conventions coexist in this codebase (the
    iv_engine chain fixtures use CALL/PUT), so the boundary deserves a hard
    constraint rather than a docstring. It has one. The next test shows why
    that is not worth as much as it looks.
    """
    add_snapshot(temp_db, ts_ago(minutes=5))
    with db.managed_conn(temp_db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO option_rows (snapshot_id, expiry_date, dte, strike, right) "
            "VALUES (1, ?, 7, 6000.0, 'CALL')", (FRONT,)
        )


@pytest.mark.parametrize("bad_right", ["CALL", "c", "X"])
def test_insert_option_rows_still_discards_a_row_failing_the_check(temp_db, bad_right):
    """STILL PINNED — the DISCARD is unchanged; only the reporting was fixed.

    `INSERT OR IGNORE` is not scoped to uniqueness: SQLite applies the conflict
    clause to EVERY constraint on the statement, so a CHECK or NOT NULL
    violation skips the row rather than raising. Verified directly against
    sqlite3 — a plain INSERT of the same row raises IntegrityError (previous
    test); through insert_option_rows it vanishes.

    That behaviour is DELIBERATELY still here, and M3.6 (ADR-050) did NOT
    change it. Raising instead would abort the whole batch, discarding the
    several thousand GOOD rows beside the bad one — a far larger loss than the
    one being reported. What M3.6 changed is that the loss is now told apart
    from a harmless duplicate and logged as an ERROR; see the tests below.
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))

    db.insert_option_rows(temp_db, [opt(sid, FRONT, 6000, bad_right)])

    assert db.get_option_chain(temp_db, sid) == [], "the row is still dropped"


def test_insert_option_rows_reports_the_rows_actually_stored(temp_db):
    """FIXED — DEBT-008 step 1 (ADR-022). Was: returned len(rows) regardless.

    The old behaviour returned the count computed BEFORE the statement ran, so
    a row the database threw away was reported to the caller as stored. This
    test previously asserted `reported == 1` against an empty table and was
    marked PINNED; it now asserts the honest count. Changing it was the point
    of the fix, not a concession to it.
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))

    good = db.insert_option_rows(temp_db, four_legs(sid))
    assert good == 4, "all four legs stored"

    mixed = db.insert_option_rows(temp_db, [
        opt(sid, FRONT, 6200, "C"),          # new, valid
        opt(sid, FRONT, 6300, "CALL"),       # fails the CHECK
        opt(sid, FRONT, CALL_STRIKE, "C"),   # duplicate of a leg above
    ])
    assert mixed == 1, "offered 3, stored 1 — the caller is told the truth"
    assert len(db.get_option_chain(temp_db, sid)) == 5


# ─────────────────────────────────────────────────────────────────────────────
# Telling the two kinds of discard apart — M3.6, ADR-050
#
# The M1.5 version logged one WARNING for any shortfall. That made a benign
# duplicate look exactly like data gone for good, and for eight weeks it did:
# 2,181 identical warnings, every one of them the third-Friday contract being
# thrown away (ADR-046). A warning you see every cycle is a warning you stop
# reading, which is precisely when the real one arrives.
#
# The pinning test these replace asserted the old single wording. It is
# rewritten rather than made to pass, because the behaviour it pinned is the
# behaviour this task exists to change — the only circumstance in which a pin
# may be rewritten.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_row_the_database_refuses_is_reported_as_loss_with_sqlites_reason(
        temp_db, caplog):
    """The case that matters. These prices are gone and cannot be re-fetched."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))

    with caplog.at_level(logging.WARNING, logger="db"):
        stored = db.insert_option_rows(temp_db, [
            opt(sid, FRONT, 6200, "C"),          # valid
            opt(sid, FRONT, 6300, "CALL"),       # fails the CHECK
        ])

    assert stored == 1
    assert "1 of 2 rows were REFUSED" in caplog.text
    assert "GONE" in caplog.text
    # SQLite's own words, not this module's guess at them.
    assert "CHECK constraint failed" in caplog.text
    # And enough to identify the row without going back to the broker.
    assert "strike=6300" in caplog.text
    assert "right=CALL" in caplog.text
    assert "ADR-050" in caplog.text
    assert any(r.levelname == "ERROR" for r in caplog.records),         "unrecoverable loss is an ERROR, not a WARNING"


def test_a_duplicate_is_reported_as_benign_not_as_loss(temp_db, caplog):
    """The half that matters more. Nothing is missing — the contract that was
    dropped is identical to the one that was kept.

    This is also the test that catches the sqlite3.Row-vs-tuple trap in
    _rows_the_database_kept: with that wrong, every duplicate reads as
    catastrophic loss and the ERROR above fires on an ordinary cycle."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C")])

    with caplog.at_level(logging.WARNING, logger="db"):
        stored = db.insert_option_rows(temp_db, [
            opt(sid, FRONT, CALL_STRIKE, "C", mark=99.0)])

    assert stored == 0
    assert "duplicates" in caplog.text
    assert "Nothing is missing" in caplog.text
    assert "GONE" not in caplog.text
    assert not any(r.levelname == "ERROR" for r in caplog.records),         "a duplicate must never be logged as data loss"


def test_a_mixed_batch_separates_the_two(temp_db, caplog):
    """Both in one batch, each counted as itself — the loss is not inflated by
    the duplicates beside it, and the duplicates are not hidden by the loss."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C")])

    with caplog.at_level(logging.WARNING, logger="db"):
        db.insert_option_rows(temp_db, [
            opt(sid, FRONT, 6200, "C"),                    # stored
            opt(sid, FRONT, CALL_STRIKE, "C", mark=1.0),   # duplicate
            opt(sid, FRONT, 6300, "CALL"),                 # refused
        ])

    assert "1 of 3 rows were REFUSED" in caplog.text
    assert "the other 1 discarded rows were duplicates" in caplog.text


def test_asking_why_stores_nothing(temp_db):
    """The diagnosis replays a refused row as a plain INSERT to learn SQLite's
    reason. If its savepoint ever failed to roll back, the act of REPORTING a
    problem would create one.

    Aimed at the branch that can actually write: a row that raises on replay
    leaves nothing behind whether or not the rollback runs, so going through
    insert_option_rows proves nothing here — the first version of this test did
    exactly that and passed with the rollback deleted. So the valid row is
    handed straight to the diagnosis, which is the only case where the replay
    succeeds and the savepoint is the only thing undoing it.
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    row = {"settlement": None, **opt(sid, FRONT, 6200, "C")}

    conn = db._make_conn(temp_db)
    try:
        reason = db._why_the_database_refused(conn, row)
        conn.commit()
    finally:
        conn.close()

    assert "no error on replay" in reason
    assert db.get_option_chain(temp_db, sid) == [],         "the replay must leave the table exactly as it found it"


def test_insert_option_rows_stays_quiet_on_a_clean_write(temp_db, caplog):
    """No warning on the happy path — a log that cries wolf every cycle is a
    log nobody reads (the lesson already recorded against BUG-005)."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))

    with caplog.at_level(logging.WARNING, logger="db"):
        db.insert_option_rows(temp_db, four_legs(sid))

    assert caplog.text == ""


def test_insert_option_rows_on_empty_list_is_a_no_op(temp_db):
    assert db.insert_option_rows(temp_db, []) == 0


def test_insert_option_rows_keeps_the_good_rows_when_one_is_bad(temp_db):
    """STILL PINNED — the practical consequence of the OR IGNORE behaviour.

    "Either all rows commit or none do" is true of a crash mid-statement, but
    NOT of a constraint violation: the four valid legs commit and only the
    malformed fifth is dropped. So a partially corrupt fetch still produces a
    partially stored snapshot marked COMPLETE, which the six-leg history
    queries then exclude one snapshot at a time.

    Unchanged by the M1.5 fix, and deliberately so — that is M3.6 (ADR-022
    step 2). What the fix added is that the shortfall is now returned and
    logged instead of passing unnoticed.
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    rows = four_legs(sid) + [opt(sid, FRONT, 6000, "INVALID")]

    reported = db.insert_option_rows(temp_db, rows)

    assert reported == 4, "offered 5, stored 4 — reported honestly"
    assert len(db.get_option_chain(temp_db, sid)) == 4, "the bad row alone was dropped"


def test_insert_option_rows_reports_zero_for_an_all_duplicate_batch(temp_db):
    """FIXED — DEBT-008 step 1. Was pinned as "reports 1, stored 0"."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C")])

    reported = db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C")])

    assert reported == 0, "nothing was stored, and the caller is told so"
    assert len(db.get_option_chain(temp_db, sid)) == 1


def test_insert_atm_iv_records_on_empty_list_is_a_no_op(temp_db):
    assert db.insert_atm_iv_records(temp_db, []) is None
    with db.get_conn(temp_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM atm_iv_by_expiry").fetchone()["n"] == 0


def test_record_gap_stamps_its_own_detection_time(temp_db):
    db.record_gap(temp_db, "2026-07-24 20:00:00", "2026-07-26 13:30:00",
                  2490.0, 498, "COLLECTOR_OFFLINE", notes="weekend")
    rows = db.get_gaps(temp_db, "2026-07-01", "2026-08-01")
    assert len(rows) == 1
    assert rows[0]["gap_minutes"] == 2490.0
    assert rows[0]["reason"] == "COLLECTOR_OFFLINE"
    assert rows[0]["notes"] == "weekend"
    assert rows[0]["detected_at"], "detected_at is NOT NULL and set by db.py"


# ─────────────────────────────────────────────────────────────────────────────
# get_latest_complete_snapshot
#
# Called once per dashboard refresh. If the status filter were lost, the whole
# dashboard would render from a PARTIAL snapshot — one that exists precisely
# because its option rows may be missing.
# ─────────────────────────────────────────────────────────────────────────────

def test_latest_complete_snapshot_is_none_on_an_empty_database(temp_db):
    assert db.get_latest_complete_snapshot(temp_db) is None


def test_latest_complete_snapshot_ignores_partial_and_failed(temp_db):
    add_snapshot(temp_db, ts_ago(minutes=30), spx=1.0)
    add_snapshot(temp_db, ts_ago(minutes=20), status="PARTIAL", spx=2.0)
    add_snapshot(temp_db, ts_ago(minutes=10), status="FAILED", spx=3.0)
    row = db.get_latest_complete_snapshot(temp_db)
    assert row["underlying_price"] == 1.0


def test_latest_complete_snapshot_orders_by_timestamp_not_insertion(temp_db):
    """A gap-backfill or a clock adjustment can insert an older snapshot last."""
    add_snapshot(temp_db, ts_ago(minutes=5), spx=6100.0)
    add_snapshot(temp_db, ts_ago(minutes=90), spx=6000.0)   # inserted later
    assert db.get_latest_complete_snapshot(temp_db)["underlying_price"] == 6100.0


# ─────────────────────────────────────────────────────────────────────────────
# get_last_snapshot_timestamp — collector gap detection on startup
# ─────────────────────────────────────────────────────────────────────────────

def test_last_snapshot_timestamp_is_none_when_empty(temp_db):
    assert db.get_last_snapshot_timestamp(temp_db) is None


def test_last_snapshot_timestamp_counts_every_status(temp_db):
    """Unlike the dashboard reads, this one must NOT filter on COMPLETE: a
    FAILED cycle still proves the collector was alive at that moment, so
    treating it as missing would log a gap that never happened."""
    add_snapshot(temp_db, "2026-07-01 15:00:00")
    add_snapshot(temp_db, "2026-07-02 15:00:00", status="FAILED")
    assert db.get_last_snapshot_timestamp(temp_db) == "2026-07-02 15:00:00"


# ─────────────────────────────────────────────────────────────────────────────
# get_option_chain
# ─────────────────────────────────────────────────────────────────────────────

def test_option_chain_is_scoped_to_one_snapshot(temp_db):
    a = add_snapshot(temp_db, ts_ago(minutes=20))
    b = add_snapshot(temp_db, ts_ago(minutes=10))
    db.insert_option_rows(temp_db, four_legs(a))
    db.insert_option_rows(temp_db, [opt(b, FRONT, CALL_STRIKE, "C")])
    assert len(db.get_option_chain(temp_db, a)) == 4
    assert len(db.get_option_chain(temp_db, b)) == 1


def test_option_chain_orders_by_expiry_then_strike_then_right(temp_db):
    """MUTATION NOTE — this assertion cannot currently fail, by design of the
    schema rather than weakness of the test.

    Deleting `right` from the ORDER BY leaves the suite green. The reason is
    idx_option_rows_contract_snap(expiry_date, strike, right, snapshot_id):
    SQLite satisfies the query with a covering-index scan, which already emits
    rows in `right` order, so the tiebreak is unobservable. Verified with
    EXPLAIN QUERY PLAN.

    The clause is still worth keeping: it is the only thing guaranteeing this
    ordering if that index is ever dropped or changed, and the index policy in
    db.py's DDL comment shows indexes here DO get dropped for size (DEBT-016
    removed two). Recorded as a known equivalent mutant so a future audit does
    not mistake it for an untested branch.
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, BACK, 6000, "P"),
        opt(sid, FRONT, 6050, "C"),
        opt(sid, FRONT, 6000, "P"),
        opt(sid, FRONT, 6000, "C"),
    ])
    got = [(r["expiry_date"], r["strike"], r["right"])
           for r in db.get_option_chain(temp_db, sid)]
    assert got == [
        (FRONT, 6000.0, "C"),
        (FRONT, 6000.0, "P"),
        (FRONT, 6050.0, "C"),
        (BACK, 6000.0, "P"),
    ]


def test_option_chain_of_an_unknown_snapshot_is_empty(temp_db):
    assert db.get_option_chain(temp_db, 424242) == []


# ─────────────────────────────────────────────────────────────────────────────
# get_latest_atm_iv_snapshots — the day-change metric
# ─────────────────────────────────────────────────────────────────────────────

def test_latest_atm_iv_returns_n_most_recent_newest_first(temp_db):
    for i, iv in enumerate([0.15, 0.16, 0.17, 0.18]):
        sid = add_snapshot(temp_db, ts_ago(minutes=40 - 10 * i))
        db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, iv)])

    rows = db.get_latest_atm_iv_snapshots(temp_db, FRONT, n=2)
    assert [r["atm_avg_iv"] for r in rows] == [0.18, 0.17]


def test_latest_atm_iv_is_scoped_to_the_requested_expiry(temp_db):
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, 0.18), atm(sid, BACK, 0.20)])
    rows = db.get_latest_atm_iv_snapshots(temp_db, BACK, n=5)
    assert [r["atm_avg_iv"] for r in rows] == [0.20]


def test_latest_atm_iv_excludes_incomplete_snapshots(temp_db):
    sid = add_snapshot(temp_db, ts_ago(minutes=5), status="PARTIAL")
    db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, 0.99)])
    assert db.get_latest_atm_iv_snapshots(temp_db, FRONT) == []


# ─────────────────────────────────────────────────────────────────────────────
# get_atm_iv_history / get_contract_iv_history — the N-day window
# ─────────────────────────────────────────────────────────────────────────────

def test_atm_iv_history_excludes_rows_older_than_the_window(temp_db):
    recent = add_snapshot(temp_db, ts_ago(days=2))
    old = add_snapshot(temp_db, ts_ago(days=40))
    db.insert_atm_iv_records(temp_db, [atm(recent, FRONT, 0.18)])
    db.insert_atm_iv_records(temp_db, [atm(old, FRONT, 0.99)])

    rows = db.get_atm_iv_history(temp_db, FRONT, days=30)
    assert [r["atm_avg_iv"] for r in rows] == [0.18]


def test_atm_iv_history_is_ordered_oldest_first_for_charting(temp_db):
    for i, iv in enumerate([0.15, 0.16, 0.17]):
        sid = add_snapshot(temp_db, ts_ago(days=3 - i))
        db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, iv)])
    rows = db.get_atm_iv_history(temp_db, FRONT, days=30)
    assert [r["atm_avg_iv"] for r in rows] == [0.15, 0.16, 0.17]


def test_contract_iv_history_matches_one_exact_contract(temp_db):
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db, [
        opt(sid, FRONT, CALL_STRIKE, "C", iv=0.21),
        opt(sid, FRONT, CALL_STRIKE, "P", iv=0.31),   # same strike, other side
        opt(sid, BACK, CALL_STRIKE, "C", iv=0.41),    # same side, other expiry
        opt(sid, FRONT, 6100, "C", iv=0.51),          # same expiry, other strike
    ])
    rows = db.get_contract_iv_history(temp_db, FRONT, CALL_STRIKE, "C", days=30)
    assert [r["iv"] for r in rows] == [0.21]


def test_contract_iv_history_excludes_rows_older_than_the_window(temp_db):
    old = add_snapshot(temp_db, ts_ago(days=40))
    db.insert_option_rows(temp_db, [opt(old, FRONT, CALL_STRIKE, "C", iv=0.99)])
    assert db.get_contract_iv_history(temp_db, FRONT, CALL_STRIKE, "C", days=30) == []


def test_contract_iv_history_excludes_incomplete_snapshots(temp_db):
    sid = add_snapshot(temp_db, ts_ago(days=1), status="PARTIAL")
    db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C", iv=0.99)])
    assert db.get_contract_iv_history(temp_db, FRONT, CALL_STRIKE, "C") == []


# ─────────────────────────────────────────────────────────────────────────────
# get_prior_session_close / get_spx_intraday_today
#
# Both take a 'YYYY-MM-DD' date and compare it against 'YYYY-MM-DD HH:MM:SS'
# timestamps as STRINGS. That works only because the extra " HH:MM:SS" sorts
# after the bare date, which makes the boundary behaviour non-obvious and
# therefore worth pinning explicitly.
# ─────────────────────────────────────────────────────────────────────────────

def test_prior_session_close_takes_the_last_snapshot_before_the_date(temp_db):
    add_snapshot(temp_db, "2026-07-20 15:00:00", spx=6000.0)
    add_snapshot(temp_db, "2026-07-20 19:59:00", spx=6010.0)   # prior close
    add_snapshot(temp_db, "2026-07-21 13:30:00", spx=6020.0)   # same session
    assert db.get_prior_session_close(temp_db, "2026-07-21") == 6010.0


def test_prior_session_close_excludes_the_session_date_itself(temp_db):
    """Boundary: a 00:00:00 stamp ON the session date must not count as prior.

    MUTATION NOTE — flipping `<` to `<=` leaves the suite green, and no test
    can distinguish them. String comparison against a bare 'YYYY-MM-DD' can
    never find an equal: every stored timestamp carries ' HH:MM:SS', which
    sorts strictly after the date alone. Verified directly against sqlite3.

    So the boundary is correct by accident of the timestamp FORMAT, not by the
    operator. That invariant is undocumented in db.py and unenforced anywhere —
    if a caller ever stored a bare date (a migration, a backfill, a schema
    change at M3), `<` and `<=` would suddenly differ and this function would
    start counting the current session as the prior one. The test below pins
    the invariant the operator is silently relying on.
    """
    add_snapshot(temp_db, "2026-07-21 00:00:00", spx=6020.0)
    assert db.get_prior_session_close(temp_db, "2026-07-21") is None


def test_stored_timestamps_always_carry_a_time_component(temp_db):
    """The invariant the date-boundary comparisons above depend on."""
    add_snapshot(temp_db, ts_ago(minutes=5))
    db.record_gap(temp_db, ts_ago(minutes=10), ts_ago(minutes=5), 5.0, 1, "X")

    ts = db.get_last_snapshot_timestamp(temp_db)
    assert len(ts) == 19 and ts[10] == " ", f"not 'YYYY-MM-DD HH:MM:SS': {ts!r}"

    detected = db.get_gaps(temp_db, "2000-01-01", "2100-01-01")[0]["detected_at"]
    assert len(detected) == 19 and detected[10] == " ", (
        f"_utcnow() drifted from the stored format: {detected!r}")


def test_prior_session_close_is_none_on_the_first_ever_session(temp_db):
    assert db.get_prior_session_close(temp_db, "2026-07-21") is None


def test_prior_session_close_ignores_incomplete_snapshots(temp_db):
    add_snapshot(temp_db, "2026-07-20 19:59:00", status="PARTIAL", spx=6010.0)
    assert db.get_prior_session_close(temp_db, "2026-07-21") is None


def test_prior_session_close_reports_a_zero_price_as_zero(temp_db):
    """FIXED — BUG-014. Was pinned as "returns None for a stored 0.0".

    A truthiness test on a float cannot tell a real 0.0 from missing data.
    Immaterial for SPX, which is never 0 — but the same pattern is a live trap
    anywhere 0.0 is legitimate, and it was already logged once against
    iv_engine as BUG-007. Now compares `is not None`.
    """
    add_snapshot(temp_db, "2026-07-20 19:59:00", spx=0.0)
    assert db.get_prior_session_close(temp_db, "2026-07-21") == 0.0


def test_prior_session_close_still_reports_a_null_price_as_missing(temp_db):
    """The other half of the fix: NULL is genuinely absent and must stay None."""
    add_snapshot(temp_db, "2026-07-20 19:59:00", spx=None)
    assert db.get_prior_session_close(temp_db, "2026-07-21") is None


def test_eod_spx_reports_a_zero_price_as_zero(temp_db):
    """FIXED — BUG-014, second site."""
    add_snapshot(temp_db, "2026-07-21 19:59:00", spx=0.0)
    assert db.get_eod_spx(temp_db, "2026-07-21") == 0.0


def test_eod_spx_still_reports_a_null_price_as_missing(temp_db):
    add_snapshot(temp_db, "2026-07-21 19:59:00", spx=None)
    assert db.get_eod_spx(temp_db, "2026-07-21") is None


def test_spx_intraday_returns_the_session_in_chronological_order(temp_db):
    add_snapshot(temp_db, "2026-07-21 13:30:00", spx=6000.0)
    add_snapshot(temp_db, "2026-07-21 19:59:00", spx=6030.0)
    add_snapshot(temp_db, "2026-07-21 15:00:00", spx=6015.0)
    rows = db.get_spx_intraday_today(temp_db, "2026-07-21")
    assert [r["underlying_price"] for r in rows] == [6000.0, 6015.0, 6030.0]


def test_spx_intraday_excludes_earlier_sessions(temp_db):
    add_snapshot(temp_db, "2026-07-20 19:59:00", spx=5900.0)
    add_snapshot(temp_db, "2026-07-21 13:30:00", spx=6000.0)
    rows = db.get_spx_intraday_today(temp_db, "2026-07-21")
    assert [r["underlying_price"] for r in rows] == [6000.0]


def test_spx_intraday_excludes_incomplete_snapshots(temp_db):
    add_snapshot(temp_db, "2026-07-21 13:30:00", status="PARTIAL", spx=6000.0)
    assert db.get_spx_intraday_today(temp_db, "2026-07-21") == []


def test_spx_intraday_is_bounded_to_the_requested_session(temp_db):
    """FIXED — BUG-015. Was pinned as "returns three sessions as one series".

    The query had only a `>=` lower bound, so an older session came back
    concatenated with every session after it. app.py was safe by accident,
    always passing the latest snapshot's date. Now bounded at both ends.
    """
    add_snapshot(temp_db, "2026-07-21 13:30:00", spx=6000.0)
    add_snapshot(temp_db, "2026-07-22 13:30:00", spx=6100.0)
    add_snapshot(temp_db, "2026-07-23 13:30:00", spx=6200.0)

    rows = db.get_spx_intraday_today(temp_db, "2026-07-21")

    assert [r["underlying_price"] for r in rows] == [6000.0], "one session only"


def test_spx_intraday_includes_the_whole_requested_day(temp_db):
    """The upper bound must not clip the session it was asked for. The last
    write of a trading day lands at 15:59 ET — 19:59 UTC — and a naive
    `< session_date` or a `<= session_date` bound would drop the entire day."""
    add_snapshot(temp_db, "2026-07-21 00:00:00", spx=5990.0)
    add_snapshot(temp_db, "2026-07-21 19:59:33", spx=6050.0)
    add_snapshot(temp_db, "2026-07-21 23:59:59", spx=6060.0)
    add_snapshot(temp_db, "2026-07-22 00:00:00", spx=9999.0)

    rows = db.get_spx_intraday_today(temp_db, "2026-07-21")

    assert [r["underlying_price"] for r in rows] == [5990.0, 6050.0, 6060.0]


# ─────────────────────────────────────────────────────────────────────────────
# get_gaps
# ─────────────────────────────────────────────────────────────────────────────

def _seed_gaps(path):
    db.record_gap(path, "2026-07-10 20:00:00", "2026-07-11 13:30:00",
                  1050.0, 200, "MARKET_CLOSED")
    db.record_gap(path, "2026-07-15 14:00:00", "2026-07-15 14:05:00",
                  5.0, 1, "COLLECTOR_OFFLINE")
    db.record_gap(path, "2026-07-20 14:00:00", "2026-07-20 14:05:00",
                  5.0, 1, None)


def test_gaps_filters_by_date_range(temp_db):
    _seed_gaps(temp_db)
    rows = db.get_gaps(temp_db, "2026-07-12", "2026-07-18")
    assert [r["reason"] for r in rows] == ["COLLECTOR_OFFLINE"]


def test_gaps_are_ordered_by_start(temp_db):
    _seed_gaps(temp_db)
    starts = [r["gap_start"] for r in db.get_gaps(temp_db, "2026-07-01", "2026-08-01")]
    assert starts == sorted(starts)


def test_gaps_can_exclude_reasons(temp_db):
    """The mechanism OPS-005 needs to surface real gaps once BUG-005 makes the
    classifier trustworthy: routine closures filtered out, faults kept."""
    _seed_gaps(temp_db)
    rows = db.get_gaps(temp_db, "2026-07-01", "2026-08-01",
                       exclude_reasons=["MARKET_CLOSED"])
    assert [r["reason"] for r in rows] == ["COLLECTOR_OFFLINE", None]


def test_gaps_exclusion_keeps_null_reasons(temp_db):
    """`reason IS NULL OR reason NOT IN (...)` — the IS NULL half matters,
    because `NULL NOT IN (...)` is NULL, not true, and would drop the row."""
    _seed_gaps(temp_db)
    rows = db.get_gaps(temp_db, "2026-07-01", "2026-08-01",
                       exclude_reasons=["MARKET_CLOSED", "COLLECTOR_OFFLINE"])
    assert [r["reason"] for r in rows] == [None]


def test_gaps_with_an_empty_exclusion_list_returns_everything(temp_db):
    """Falsy list takes the unfiltered branch — not an empty IN () clause."""
    _seed_gaps(temp_db)
    assert len(db.get_gaps(temp_db, "2026-07-01", "2026-08-01",
                           exclude_reasons=[])) == 3


# ─────────────────────────────────────────────────────────────────────────────
# get_entry_iv_context — retroactive Regime Analysis
# ─────────────────────────────────────────────────────────────────────────────

def _seed_entry_context(path, *, front_call_iv=0.20, front_put_iv=0.22,
                        back_call_iv=0.10, back_put_iv=0.10):
    sid = add_snapshot(path, "2026-07-15 14:00:00")
    db.insert_option_rows(path, [
        opt(sid, FRONT, CALL_STRIKE, "C", iv=front_call_iv),
        opt(sid, FRONT, PUT_STRIKE, "P", iv=front_put_iv),
        opt(sid, BACK, CALL_STRIKE, "C", iv=back_call_iv),
        opt(sid, BACK, PUT_STRIKE, "P", iv=back_put_iv),
    ])
    db.insert_atm_iv_records(path, [atm(sid, FRONT, 0.24), atm(sid, BACK, 0.12)])
    return sid


def test_entry_iv_context_is_none_without_any_snapshot(temp_db):
    assert db.get_entry_iv_context(
        temp_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE
    ) is None


def test_entry_iv_context_averages_the_two_legs_per_side(temp_db):
    _seed_entry_context(temp_db)
    ctx = db.get_entry_iv_context(
        temp_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE)
    assert ctx["front_iv"] == pytest.approx(0.21)     # (0.20 + 0.22) / 2
    assert ctx["back_iv"] == pytest.approx(0.10)
    assert ctx["ratio"] == pytest.approx(2.1)         # front / back
    assert ctx["level"] == pytest.approx((0.21 * 0.10) ** 0.5)


def test_entry_iv_context_returns_decimals_not_percentages(temp_db):
    """DB convention: the caller multiplies by 100 at the load boundary."""
    _seed_entry_context(temp_db)
    ctx = db.get_entry_iv_context(
        temp_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE)
    assert 0 < ctx["front_iv"] < 1


def test_entry_iv_context_carries_atm_macro_context(temp_db):
    _seed_entry_context(temp_db)
    ctx = db.get_entry_iv_context(
        temp_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE)
    assert ctx["atm_front_iv"] == pytest.approx(0.24)
    assert ctx["atm_back_iv"] == pytest.approx(0.12)
    assert ctx["atm_ratio"] == pytest.approx(2.0)


def test_entry_iv_context_picks_the_nearest_snapshot_in_either_direction(temp_db):
    """ABS() distance — the nearest snapshot may be AFTER the entry moment.

    An entry recorded at 14:00 with snapshots at 13:00 and 14:10 must resolve to
    14:10. A naive "last snapshot at or before" would silently use the hour-old
    one, and the whole point of this function is reconstructing the conditions
    at the entry moment.
    """
    early = add_snapshot(temp_db, "2026-07-15 13:00:00")
    late = add_snapshot(temp_db, "2026-07-15 14:10:00")
    db.insert_option_rows(temp_db, [opt(early, FRONT, CALL_STRIKE, "C", iv=0.99)])
    db.insert_option_rows(temp_db, [opt(late, FRONT, CALL_STRIKE, "C", iv=0.11)])

    ctx = db.get_entry_iv_context(
        temp_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE)
    assert ctx["snapshot_id"] == late
    assert ctx["front_iv"] == pytest.approx(0.11)


def test_entry_iv_context_survives_a_missing_leg(temp_db):
    """Individual IV fields may be None; the call must still return a dict."""
    sid = add_snapshot(temp_db, "2026-07-15 14:00:00")
    db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C", iv=0.20)])
    ctx = db.get_entry_iv_context(
        temp_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE)
    assert ctx is not None
    assert ctx["front_iv"] == pytest.approx(0.20)   # averaged over what exists
    assert ctx["back_iv"] is None
    assert ctx["ratio"] is None
    assert ctx["level"] is None


def test_entry_iv_context_ignores_incomplete_snapshots(temp_db):
    add_snapshot(temp_db, "2026-07-15 14:00:00", status="PARTIAL")
    assert db.get_entry_iv_context(
        temp_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE
    ) is None


# ─────────────────────────────────────────────────────────────────────────────
# get_diagonal_history / get_transform_mark_history
#
# These two drive the charts the entry decision is actually made on. Their
# defining behaviour is EXCLUSION: a snapshot missing any required leg must be
# dropped entirely rather than plotted with a hole in it.
# ─────────────────────────────────────────────────────────────────────────────

def test_diagonal_history_returns_one_row_per_complete_snapshot(temp_db):
    for d in (3, 2, 1):
        sid = add_snapshot(temp_db, ts_ago(days=d))
        db.insert_option_rows(temp_db, four_legs(sid))
        db.insert_atm_iv_records(temp_db,
                                 [atm(sid, FRONT, 0.20), atm(sid, BACK, 0.10)])

    rows = db.get_diagonal_history(temp_db, FRONT, BACK,
                                   CALL_STRIKE, PUT_STRIKE, days=30)
    assert len(rows) == 3
    assert rows[0]["iv_ratio"] == pytest.approx(0.5)      # back / front
    assert rows[0]["front_iv"] == pytest.approx(0.20)


def test_diagonal_history_drops_a_snapshot_missing_one_leg(temp_db):
    complete = add_snapshot(temp_db, ts_ago(days=2))
    db.insert_option_rows(temp_db, four_legs(complete))
    db.insert_atm_iv_records(temp_db, [atm(complete, FRONT, 0.20),
                                       atm(complete, BACK, 0.10)])

    partial = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db, four_legs(partial)[:3])   # back put missing
    db.insert_atm_iv_records(temp_db, [atm(partial, FRONT, 0.20),
                                       atm(partial, BACK, 0.10)])

    rows = db.get_diagonal_history(temp_db, FRONT, BACK,
                                   CALL_STRIKE, PUT_STRIKE, days=30)
    assert len(rows) == 1


def test_diagonal_history_falls_back_to_the_bid_ask_midpoint(temp_db):
    """COALESCE(mark, (bid + ask) / 2.0). Roughly 2.5% of rows in the real
    database carry a NULL mark, so this branch runs constantly — and DEBT-014
    records that the scanner golden net does NOT cover it."""
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db,
                          four_legs(sid, mark=None, bid=4.0, ask=6.0))
    db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, 0.20), atm(sid, BACK, 0.10)])

    rows = db.get_diagonal_history(temp_db, FRONT, BACK,
                                   CALL_STRIKE, PUT_STRIKE, days=30)
    assert len(rows) == 1
    assert rows[0]["front_call_mark"] == pytest.approx(5.0)


def test_diagonal_history_prefers_a_stored_mark_over_the_midpoint(temp_db):
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db,
                          four_legs(sid, mark=7.0, bid=4.0, ask=6.0))
    db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, 0.20), atm(sid, BACK, 0.10)])

    rows = db.get_diagonal_history(temp_db, FRONT, BACK,
                                   CALL_STRIKE, PUT_STRIKE, days=30)
    assert rows[0]["front_call_mark"] == pytest.approx(7.0)


def test_diagonal_history_requires_atm_records_for_both_expiries(temp_db):
    """The atm_iv_by_expiry joins are INNER — no ATM row, no data point."""
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db, four_legs(sid))
    db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, 0.20)])   # BACK missing
    assert db.get_diagonal_history(temp_db, FRONT, BACK,
                                   CALL_STRIKE, PUT_STRIKE, days=30) == []


def test_diagonal_history_guards_division_by_a_zero_front_iv(temp_db):
    """CASE WHEN f.atm_avg_iv > 0 — a zero front IV yields NULL, not an error."""
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db, four_legs(sid))
    db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, 0.0), atm(sid, BACK, 0.10)])

    rows = db.get_diagonal_history(temp_db, FRONT, BACK,
                                   CALL_STRIKE, PUT_STRIKE, days=30)
    assert rows[0]["iv_ratio"] is None


def test_diagonal_history_excludes_snapshots_outside_the_window(temp_db):
    sid = add_snapshot(temp_db, ts_ago(days=120))
    db.insert_option_rows(temp_db, four_legs(sid))
    db.insert_atm_iv_records(temp_db, [atm(sid, FRONT, 0.20), atm(sid, BACK, 0.10)])
    assert db.get_diagonal_history(temp_db, FRONT, BACK,
                                   CALL_STRIKE, PUT_STRIKE, days=90) == []


def test_transform_mark_history_returns_all_six_legs(temp_db):
    """The two extra front wings at call+5 / put-5 are what distinguishes this
    query from get_diagonal_history. The ±5 offset is hardcoded in the SQL —
    DEBT-006 tracks lifting it into config, and this test pins the current
    contract so that move is verifiable rather than hopeful."""
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db, four_legs(sid, mark=2.0))
    db.insert_option_rows(temp_db, wing_legs(sid, mark=1.5))

    rows = db.get_transform_mark_history(temp_db, FRONT, BACK,
                                         CALL_STRIKE, PUT_STRIKE, days=30)
    assert len(rows) == 1
    r = rows[0]
    assert r["front_call_mark"] == pytest.approx(2.0)
    assert r["back_call_mark"] == pytest.approx(2.0)
    assert r["front_put_mark"] == pytest.approx(2.0)
    assert r["back_put_mark"] == pytest.approx(2.0)
    assert r["front_wing_call_mark"] == pytest.approx(1.5)
    assert r["front_wing_put_mark"] == pytest.approx(1.5)


def test_transform_mark_history_drops_a_snapshot_missing_a_wing(temp_db):
    """A snapshot good enough for the diagonal chart is NOT automatically good
    enough for the transform chart — the wings are further from the money and
    are the legs most likely to be absent."""
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db, four_legs(sid))
    db.insert_option_rows(temp_db, wing_legs(sid)[:1])   # put wing missing

    assert db.get_transform_mark_history(temp_db, FRONT, BACK,
                                         CALL_STRIKE, PUT_STRIKE, days=30) == []


def test_transform_mark_history_needs_no_atm_records(temp_db):
    """Unlike get_diagonal_history, this query never touches atm_iv_by_expiry."""
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db, four_legs(sid))
    db.insert_option_rows(temp_db, wing_legs(sid))
    assert len(db.get_transform_mark_history(
        temp_db, FRONT, BACK, CALL_STRIKE, PUT_STRIKE, days=30)) == 1


def test_transform_mark_history_falls_back_to_the_midpoint(temp_db):
    sid = add_snapshot(temp_db, ts_ago(days=1))
    db.insert_option_rows(temp_db, four_legs(sid, mark=None, bid=4.0, ask=6.0))
    db.insert_option_rows(temp_db, wing_legs(sid, mark=None, bid=1.0, ask=2.0))

    rows = db.get_transform_mark_history(temp_db, FRONT, BACK,
                                         CALL_STRIKE, PUT_STRIKE, days=30)
    assert rows[0]["front_call_mark"] == pytest.approx(5.0)
    assert rows[0]["front_wing_call_mark"] == pytest.approx(1.5)


def test_transform_mark_history_excludes_incomplete_snapshots(temp_db):
    sid = add_snapshot(temp_db, ts_ago(days=1), status="PARTIAL")
    db.insert_option_rows(temp_db, four_legs(sid))
    db.insert_option_rows(temp_db, wing_legs(sid))
    assert db.get_transform_mark_history(temp_db, FRONT, BACK,
                                         CALL_STRIKE, PUT_STRIKE, days=30) == []


def test_transform_mark_history_is_ordered_oldest_first(temp_db):
    for d in (1, 3, 2):
        sid = add_snapshot(temp_db, ts_ago(days=d))
        db.insert_option_rows(temp_db, four_legs(sid))
        db.insert_option_rows(temp_db, wing_legs(sid))
    stamps = [r["snapshot_timestamp"] for r in db.get_transform_mark_history(
        temp_db, FRONT, BACK, CALL_STRIKE, PUT_STRIKE, days=30)]
    assert stamps == sorted(stamps)


# ─────────────────────────────────────────────────────────────────────────────
# Trades table — schema, writes, reads
# ─────────────────────────────────────────────────────────────────────────────

def test_init_trades_table_creates_the_table(trades_db):
    assert "trades" in table_names(trades_db)


def test_init_trades_table_is_idempotent(trades_db):
    """Called on every journal.py startup, including the ALTER TABLE migrations
    for transform_commissions and close_type, which raise once the column
    exists and are swallowed by design."""
    db.init_trades_table(trades_db)
    db.init_trades_table(trades_db)
    assert "trades" in table_names(trades_db)


def test_init_trades_table_applies_the_v31_column_migrations(trades_db):
    with db.get_conn(trades_db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    assert {"transform_commissions", "close_type"} <= cols


@pytest.mark.parametrize("bad_status", ["open", "Cancelled", ""])
def test_trade_status_is_constrained_by_the_schema(trades_db, bad_status):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_trade(trades_db, {
            "trade_id": "T-100", "entry_date": "2026-07-01",
            "entry_time": "09:34", "status": bad_status,
            "contracts": 1, "initial_legs": "[]", "total_debit": 4.0,
        })


def test_insert_and_read_back_a_trade(trades_db):
    db.insert_trade(trades_db, {
        "trade_id": "T-100", "entry_date": "2026-07-01", "entry_time": "09:34",
        "status": "Open", "contracts": 2, "initial_legs": "[]",
        "total_debit": 4.25, "notes": "hello",
    })
    t = db.get_trade(trades_db, "T-100")
    assert t["total_debit"] == 4.25
    assert t["contracts"] == 2
    assert t["notes"] == "hello"


def test_insert_trade_stamps_created_and_updated(trades_db):
    """Both are NOT NULL and always overwritten to UTC now, whatever the caller
    passed — the docstring's promise."""
    db.insert_trade(trades_db, {
        "trade_id": "T-100", "entry_date": "2026-07-01", "entry_time": "09:34",
        "status": "Open", "contracts": 1, "initial_legs": "[]", "total_debit": 4.0,
    })
    t = db.get_trade(trades_db, "T-100")
    assert t["created_at"] and t["updated_at"]
    assert t["created_at"] == t["updated_at"]


def test_get_trade_is_none_when_absent(trades_db):
    assert db.get_trade(trades_db, "T-999") is None


def test_update_trade_changes_only_the_named_columns(trades_db):
    db.insert_trade(trades_db, {
        "trade_id": "T-100", "entry_date": "2026-07-01", "entry_time": "09:34",
        "status": "Open", "contracts": 1, "initial_legs": "[]",
        "total_debit": 4.0, "notes": "before",
    })
    db.update_trade(trades_db, "T-100", status="Transformed", credit_received=10.0)
    t = db.get_trade(trades_db, "T-100")
    assert t["status"] == "Transformed"
    assert t["credit_received"] == 10.0
    assert t["notes"] == "before"
    assert t["total_debit"] == 4.0


def test_update_trade_with_no_fields_is_a_no_op(trades_db):
    """The early return matters: without it the generated SQL would be
    `UPDATE trades SET updated_at = ...`, silently touching the row.

    MUTATION NOTE: the obvious version of this test — read updated_at, call
    update_trade(), compare — passes even with the early return removed,
    because _utcnow() has one-second resolution and the whole test runs inside
    the same second. It looked like a real test and proved nothing. Backdating
    updated_at to a value now() can never produce is what makes it bite.
    """
    db.insert_trade(trades_db, {
        "trade_id": "T-100", "entry_date": "2026-07-01", "entry_time": "09:34",
        "status": "Open", "contracts": 1, "initial_legs": "[]", "total_debit": 4.0,
    })
    with db.managed_conn(trades_db) as conn:
        conn.execute("UPDATE trades SET updated_at = '1999-01-01 00:00:00'")

    db.update_trade(trades_db, "T-100")

    assert db.get_trade(trades_db, "T-100")["updated_at"] == "1999-01-01 00:00:00"


def test_update_trade_on_an_unknown_id_affects_nothing(trades_db):
    db.update_trade(trades_db, "T-999", status="Closed")
    assert db.get_all_trades(trades_db) == []


def test_delete_trade_removes_only_that_trade(trades_db):
    for tid in ("T-100", "T-101"):
        db.insert_trade(trades_db, {
            "trade_id": tid, "entry_date": "2026-07-01", "entry_time": "09:34",
            "status": "Open", "contracts": 1, "initial_legs": "[]",
            "total_debit": 4.0,
        })
    db.delete_trade(trades_db, "T-100")
    assert [t["trade_id"] for t in db.get_all_trades(trades_db)] == ["T-101"]


def test_all_trades_are_newest_entry_first(trades_db):
    for tid, date, time in (("T-100", "2026-07-01", "09:34"),
                            ("T-101", "2026-07-03", "10:00"),
                            ("T-102", "2026-07-01", "14:00")):
        db.insert_trade(trades_db, {
            "trade_id": tid, "entry_date": date, "entry_time": time,
            "status": "Open", "contracts": 1, "initial_legs": "[]",
            "total_debit": 4.0,
        })
    assert [t["trade_id"] for t in db.get_all_trades(trades_db)] == [
        "T-101", "T-102", "T-100"]


def test_next_trade_id_starts_at_001_and_is_zero_padded(trades_db):
    assert db.get_next_trade_id(trades_db) == "T-001"
    db.insert_trade(trades_db, {
        "trade_id": "T-001", "entry_date": "2026-07-01", "entry_time": "09:34",
        "status": "Open", "contracts": 1, "initial_legs": "[]", "total_debit": 4.0,
    })
    assert db.get_next_trade_id(trades_db) == "T-002"


def test_next_trade_id_never_reuses_an_id_after_a_deletion(trades_db):
    """FIXED — BUG-016. Was pinned as "returns T-002, which then raises".

    COUNT(*) + 1 is not a sequence. Deleting any non-newest trade made the next
    generated ID collide with a surviving PRIMARY KEY, so insert_trade() raised
    and the trade being recorded was lost. Now derived from MAX(id), so the
    deleted number is retired permanently.

    This test previously asserted the collision AND the IntegrityError.
    Rewriting it was the point of the fix.
    """
    for tid in ("T-001", "T-002"):
        db.insert_trade(trades_db, {
            "trade_id": tid, "entry_date": "2026-07-01", "entry_time": "09:34",
            "status": "Open", "contracts": 1, "initial_legs": "[]",
            "total_debit": 4.0,
        })
    db.delete_trade(trades_db, "T-001")

    assert db.get_next_trade_id(trades_db) == "T-003", "T-002 survives; skip past it"

    db.insert_trade(trades_db, {
        "trade_id": "T-003", "entry_date": "2026-07-02", "entry_time": "09:34",
        "status": "Open", "contracts": 1, "initial_legs": "[]", "total_debit": 4.0,
    })
    assert {t["trade_id"] for t in db.get_all_trades(trades_db)} == {"T-002", "T-003"}


def test_next_trade_id_survives_discarding_every_practice_trade(trades_db):
    """The exact sequence STATUS.md commits to: wipe the 6 practice trades,
    then start recording real ones. Under the old code the first real trade
    after the wipe raised. The gap is intentional — a real T-001 must not be
    able to mean the same thing a discarded practice T-001 meant."""
    for i in range(1, 7):
        db.insert_trade(trades_db, {
            "trade_id": f"T-{i:03d}", "entry_date": "2026-07-01",
            "entry_time": "09:34", "status": "Open", "contracts": 1,
            "initial_legs": "[]", "total_debit": 4.0,
        })
    for i in range(1, 7):
        db.delete_trade(trades_db, f"T-{i:03d}")

    assert db.get_all_trades(trades_db) == []
    assert db.get_next_trade_id(trades_db) == "T-001", "empty table restarts at 1"


def test_next_trade_id_orders_numerically_not_as_text(trades_db):
    """'T-010' must outrank 'T-009'. A text MAX() gets that right only by luck
    of zero-padding, and stops working entirely past 'T-999'."""
    for tid in ("T-002", "T-010", "T-009"):
        db.insert_trade(trades_db, {
            "trade_id": tid, "entry_date": "2026-07-01", "entry_time": "09:34",
            "status": "Open", "contracts": 1, "initial_legs": "[]",
            "total_debit": 4.0,
        })
    assert db.get_next_trade_id(trades_db) == "T-011"


def test_next_trade_id_ignores_ids_that_are_not_trade_ids(trades_db):
    """Only 'T-' IDs count toward the sequence.

    `SEED` and `x` are harmless on their own — SQLite casts their tails to 0,
    so they lose the MAX() either way. `TX999` is the case that actually needs
    the `LIKE 'T-_%'` filter: `SUBSTR('TX999', 3)` is `'999'`, which casts to
    999 and would jump the sequence to T-1000 without it. Without this ID in
    the fixture the filter is unobservable and the test only appears to
    protect it (found by mutation-testing this very test).
    """
    for tid in ("T-004", "SEED", "x", "TX999"):
        db.insert_trade(trades_db, {
            "trade_id": tid, "entry_date": "2026-07-01", "entry_time": "09:34",
            "status": "Open", "contracts": 1, "initial_legs": "[]",
            "total_debit": 4.0,
        })
    assert db.get_next_trade_id(trades_db) == "T-005"


def test_seed_t001_inserts_the_first_live_trade(trades_db):
    db.seed_t001(trades_db)
    t = db.get_trade(trades_db, "T-001")
    assert t["status"] == "Transformed"
    assert t["profit_locked_in"] == 5.90
    assert t["credit_received"] == 18.90
    assert t["total_debit"] == 13.00
    assert t["ic_risk_free"] == 1


def test_seed_t001_stores_legs_as_valid_json(trades_db):
    import json
    db.seed_t001(trades_db)
    t = db.get_trade(trades_db, "T-001")
    initial = json.loads(t["initial_legs"])
    assert len(initial) == 4
    assert {leg["expiry"] for leg in initial} == {"2026-06-30", "2026-07-02"}
    assert len(json.loads(t["transform_legs"])) == 4


def test_seed_t001_is_a_no_op_on_a_second_call(trades_db):
    """Called on every journal.py startup — it must never duplicate or, worse,
    overwrite edits made to the row since."""
    db.seed_t001(trades_db)
    db.update_trade(trades_db, "T-001", notes="edited by hand")
    db.seed_t001(trades_db)

    trades = db.get_all_trades(trades_db)
    assert len(trades) == 1
    assert trades[0]["notes"] == "edited by hand"


# ─────────────────────────────────────────────────────────────────────────────
# get_eod_spx / get_ic_marks — journal reads
# ─────────────────────────────────────────────────────────────────────────────

def test_eod_spx_takes_the_last_snapshot_on_or_before_the_date(temp_db):
    add_snapshot(temp_db, "2026-07-20 19:59:00", spx=6000.0)
    add_snapshot(temp_db, "2026-07-21 13:30:00", spx=6010.0)
    add_snapshot(temp_db, "2026-07-21 19:59:00", spx=6020.0)
    add_snapshot(temp_db, "2026-07-22 13:30:00", spx=6030.0)
    assert db.get_eod_spx(temp_db, "2026-07-21") == 6020.0


def test_eod_spx_falls_back_to_an_earlier_session(temp_db):
    """Asking for a holiday or weekend date returns the previous session's
    close rather than None — the intended auto-suggest behaviour."""
    add_snapshot(temp_db, "2026-07-20 19:59:00", spx=6000.0)
    assert db.get_eod_spx(temp_db, "2026-07-25") == 6000.0


def test_eod_spx_is_none_before_any_data(temp_db):
    add_snapshot(temp_db, "2026-07-20 19:59:00", spx=6000.0)
    assert db.get_eod_spx(temp_db, "2026-07-01") is None


def test_eod_spx_ignores_incomplete_snapshots(temp_db):
    add_snapshot(temp_db, "2026-07-21 19:59:00", status="FAILED", spx=6020.0)
    assert db.get_eod_spx(temp_db, "2026-07-21") is None


SC, LC, SP, LP = 6050.0, 6100.0, 5950.0, 5900.0


def _seed_ic(path, ts, *, sc=3.0, lc=1.0, sp=4.0, lp=1.5, spx=6000.0):
    sid = add_snapshot(path, ts, spx=spx)
    db.insert_option_rows(path, [
        opt(sid, FRONT, SC, "C", mark=sc),
        opt(sid, FRONT, LC, "C", mark=lc),
        opt(sid, FRONT, SP, "P", mark=sp),
        opt(sid, FRONT, LP, "P", mark=lp),
    ])
    return sid


def test_ic_marks_computes_cost_to_close(temp_db):
    """cost = short_call + short_put - long_call - long_put, per share.
    3.0 + 4.0 - 1.0 - 1.5 = 4.5 — buy back the shorts, sell out the longs."""
    _seed_ic(temp_db, ts_ago(minutes=5))
    marks = db.get_ic_marks(temp_db, FRONT, SC, LC, SP, LP)
    assert marks["cost_to_close"] == pytest.approx(4.5)
    assert marks["short_call_mark"] == pytest.approx(3.0)
    assert marks["long_put_mark"] == pytest.approx(1.5)
    assert marks["spx"] == pytest.approx(6000.0)


def test_ic_marks_uses_the_latest_snapshot_by_default(temp_db):
    _seed_ic(temp_db, ts_ago(minutes=60), sc=9.0, spx=5000.0)
    _seed_ic(temp_db, ts_ago(minutes=5), sc=3.0, spx=6000.0)
    marks = db.get_ic_marks(temp_db, FRONT, SC, LC, SP, LP)
    assert marks["short_call_mark"] == pytest.approx(3.0)
    assert marks["spx"] == pytest.approx(6000.0)


def test_ic_marks_honours_an_end_of_day_date(temp_db):
    """eod_date enables 'unrealized P&L as of a past session close'."""
    _seed_ic(temp_db, "2026-07-20 19:59:00", sc=9.0)
    _seed_ic(temp_db, "2026-07-21 19:59:00", sc=3.0)
    marks = db.get_ic_marks(temp_db, FRONT, SC, LC, SP, LP, eod_date="2026-07-20")
    assert marks["short_call_mark"] == pytest.approx(9.0)
    assert marks["snapshot_ts"] == "2026-07-20 19:59:00"


def test_ic_marks_is_none_without_any_snapshot(temp_db):
    assert db.get_ic_marks(temp_db, FRONT, SC, LC, SP, LP) is None


def test_ic_marks_is_none_when_a_leg_is_missing(temp_db):
    """All four or nothing — a partial IC valuation would be quietly wrong
    rather than obviously absent."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, FRONT, SC, "C", mark=3.0),
        opt(sid, FRONT, LC, "C", mark=1.0),
        opt(sid, FRONT, SP, "P", mark=4.0),
    ])   # long put absent
    assert db.get_ic_marks(temp_db, FRONT, SC, LC, SP, LP) is None


def test_ic_marks_is_none_for_the_wrong_expiry(temp_db):
    _seed_ic(temp_db, ts_ago(minutes=5))
    assert db.get_ic_marks(temp_db, BACK, SC, LC, SP, LP) is None


def test_ic_marks_falls_back_to_the_midpoint_for_a_null_mark(temp_db):
    """FIXED — BUG-014, third site. Was pinned as "NULL becomes 0.0".

    `r["mark"] or 0.0` turned a missing quote into a real-looking 0.0, which
    flowed into cost_to_close and out to the unrealized-P&L figure — a wrong
    money number presented as a right one. It now falls back to the bid/ask
    midpoint in SQL, as every history query in the module already did
    (DEBT-012's fifth site).
    """
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, FRONT, SC, "C", mark=None, bid=2.9, ask=3.1),
        opt(sid, FRONT, LC, "C", mark=1.0),
        opt(sid, FRONT, SP, "P", mark=4.0),
        opt(sid, FRONT, LP, "P", mark=1.5),
    ])
    marks = db.get_ic_marks(temp_db, FRONT, SC, LC, SP, LP)
    assert marks is not None
    assert marks["short_call_mark"] == pytest.approx(3.0), "the midpoint, not 0.0"
    assert marks["cost_to_close"] == pytest.approx(4.5)


def test_ic_marks_is_none_when_a_leg_has_no_computable_mark(temp_db):
    """The other half of the fix: with no mark AND no bid/ask there is nothing
    honest to report, so the whole valuation is withheld rather than filled in
    with a zero. This function is already all-four-or-nothing."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, FRONT, SC, "C", mark=None, bid=None, ask=None),
        opt(sid, FRONT, LC, "C", mark=1.0),
        opt(sid, FRONT, SP, "P", mark=4.0),
        opt(sid, FRONT, LP, "P", mark=1.5),
    ])
    assert db.get_ic_marks(temp_db, FRONT, SC, LC, SP, LP) is None


def test_ic_marks_keeps_a_genuine_zero_mark(temp_db):
    """A far-OTM leg really can mark at 0.00. That is a fact about the market,
    not missing data, and must survive — which is precisely the distinction the
    old truthiness check could not make."""
    sid = add_snapshot(temp_db, ts_ago(minutes=5))
    db.insert_option_rows(temp_db, [
        opt(sid, FRONT, SC, "C", mark=3.0),
        opt(sid, FRONT, LC, "C", mark=0.0, bid=0.0, ask=0.0),
        opt(sid, FRONT, SP, "P", mark=4.0),
        opt(sid, FRONT, LP, "P", mark=1.5),
    ])
    marks = db.get_ic_marks(temp_db, FRONT, SC, LC, SP, LP)
    assert marks is not None
    assert marks["long_call_mark"] == 0.0
    assert marks["cost_to_close"] == pytest.approx(5.5)


# ─────────────────────────────────────────────────────────────────────────────
# Entry-IV snapshotting — the gate on retention (M3, ADR-044 / ADR-016)
#
# get_entry_iv_context() answers "what was the term structure when I opened
# this?" by reading historical option_rows. Retention deletes those rows, so
# the answer is copied onto the trade while they still exist. These tests pin
# the two properties that make pruning safe: the value IS stored at logging
# time, and it SURVIVES the rows it was derived from disappearing.
# ─────────────────────────────────────────────────────────────────────────────

# 10:00 America/New_York in July == 14:00 UTC, the moment _seed_entry_context
# places its snapshot.
ENTRY_DATE, ENTRY_TIME_ET = "2026-07-15", "10:00"

TRADE_LEGS = json.dumps([
    {"expiry": FRONT, "type": "Call", "action": "Sell to Open",
     "strike": CALL_STRIKE, "fill": 10.0},
    {"expiry": FRONT, "type": "Put",  "action": "Sell to Open",
     "strike": PUT_STRIKE,  "fill": 10.0},
    {"expiry": BACK,  "type": "Call", "action": "Buy to Open",
     "strike": CALL_STRIKE, "fill": 12.0},
    {"expiry": BACK,  "type": "Put",  "action": "Buy to Open",
     "strike": PUT_STRIKE,  "fill": 12.0},
])


def _log_trade(path, trade_id="T-100", **overrides):
    fields = {
        "trade_id": trade_id, "entry_date": ENTRY_DATE,
        "entry_time": ENTRY_TIME_ET, "status": "Open", "contracts": 1,
        "initial_legs": TRADE_LEGS, "total_debit": 4.0,
    }
    fields.update(overrides)
    db.insert_trade(path, fields)
    return db.get_trade(path, trade_id)


def _drop_option_rows(path):
    """What retention will eventually do to the rows behind the context."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM option_rows")
        conn.commit()
    finally:
        conn.close()


def test_init_trades_table_adds_every_entry_iv_column(trades_db):
    with db.get_conn(trades_db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)")}
    assert set(db.ENTRY_IV_COLUMNS) <= cols


def test_entry_iv_columns_are_added_to_a_pre_existing_trades_table(temp_db):
    """The migration path, not the fresh-schema path. An existing database has
    a trades table built before these columns existed; init_trades_table() must
    bring it forward without touching the rows already in it.

    Rewound to the state a REAL old database is in, which is the state the live
    3.5 GB file was in until M3.3: the old table, and a version of 1 because
    nothing recorded the columns the add-if-missing code had been adding."""
    conn = sqlite3.connect(temp_db)
    try:
        conn.executescript(
            "DROP TABLE IF EXISTS trades;"
            "DELETE FROM schema_version WHERE version > 1;"
            "CREATE TABLE trades (trade_id TEXT PRIMARY KEY, entry_date TEXT, "
            "entry_time TEXT, status TEXT, contracts INTEGER, initial_legs TEXT, "
            "total_debit REAL, created_at TEXT, updated_at TEXT);"
        )
        conn.execute(
            "INSERT INTO trades VALUES ('T-001','2026-07-01','10:00','Open',1,"
            "'[]',4.0,'x','x')"
        )
        conn.commit()
    finally:
        conn.close()

    db.init_trades_table(temp_db)

    with db.get_conn(temp_db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)")}
        assert set(db.ENTRY_IV_COLUMNS) <= cols
        row = conn.execute("SELECT * FROM trades WHERE trade_id='T-001'").fetchone()
    assert row["entry_date"] == "2026-07-01"      # untouched
    assert row["entry_front_iv"] is None          # nothing invented for it


def test_insert_trade_stores_the_entry_iv_context(trades_db):
    _seed_entry_context(trades_db)
    t = _log_trade(trades_db)
    assert t["entry_front_iv"] == pytest.approx(0.21)   # (0.20 + 0.22) / 2
    assert t["entry_back_iv"] == pytest.approx(0.10)
    assert t["entry_iv_ratio"] == pytest.approx(2.1)
    assert t["entry_atm_front_iv"] == pytest.approx(0.24)
    assert t["entry_iv_snapshot_ts"] == "2026-07-15 14:00:00"


def test_stored_context_matches_what_reconstruction_would_have_returned(trades_db):
    """The stored value is not a second, subtly different calculation. If these
    two ever diverge, every trade logged from now on carries a number that no
    longer means what the historical charts mean."""
    _seed_entry_context(trades_db)
    t = _log_trade(trades_db)
    ctx = db.get_entry_iv_context(
        trades_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE)
    assert t["entry_front_iv"] == pytest.approx(ctx["front_iv"])
    assert t["entry_back_iv"] == pytest.approx(ctx["back_iv"])
    assert t["entry_iv_ratio"] == pytest.approx(ctx["ratio"])
    assert t["entry_iv_level"] == pytest.approx(ctx["level"])
    assert t["entry_iv_snapshot_id"] == ctx["snapshot_id"]


def test_stored_context_survives_pruning_the_rows_it_came_from(trades_db):
    """THE POINT OF THE WHOLE EXERCISE. After retention deletes the option_rows,
    reconstruction can no longer answer — and the trade still can."""
    _seed_entry_context(trades_db)
    t = _log_trade(trades_db)
    stored = t["entry_front_iv"]

    _drop_option_rows(trades_db)

    gone = db.get_entry_iv_context(
        trades_db, "2026-07-15 14:00:00", FRONT, BACK, CALL_STRIKE, PUT_STRIKE)
    assert gone["front_iv"] is None               # reconstruction is now blind
    assert db.get_trade(trades_db, "T-100")["entry_front_iv"] == stored


def test_a_trade_is_still_recordable_with_no_snapshot_anywhere(trades_db):
    """Collector down, token expired, weekend — logging a trade must never fail
    because the context is unavailable. Missing context stores NULL, not 0."""
    t = _log_trade(trades_db)
    assert t is not None
    assert t["entry_front_iv"] is None
    assert t["entry_iv_snapshot_id"] is None


def test_unparseable_legs_do_not_prevent_logging_a_trade(trades_db):
    _seed_entry_context(trades_db)
    t = _log_trade(trades_db, initial_legs="not json at all")
    assert t is not None
    assert t["entry_front_iv"] is None


def test_an_explicit_context_is_not_overwritten(trades_db):
    """How a backfill supplies its own answer."""
    _seed_entry_context(trades_db)
    t = _log_trade(trades_db, entry_front_iv=0.5, entry_back_iv=0.25,
                   entry_iv_ratio=2.0, entry_iv_level=0.35,
                   entry_iv_snapshot_id=999, entry_iv_snapshot_ts="x",
                   entry_atm_front_iv=0.4, entry_atm_back_iv=0.2)
    assert t["entry_front_iv"] == pytest.approx(0.5)
    assert t["entry_iv_snapshot_id"] == 999


def test_editing_the_entry_time_recomputes_the_stored_context(trades_db):
    """A stored context describing the trade as it USED to be is worse than no
    context: it is confidently wrong, and nothing on screen would say so."""
    _seed_entry_context(trades_db)                        # 14:00 UTC, front 0.21
    late = add_snapshot(trades_db, "2026-07-15 18:00:00")  # 14:00 ET
    db.insert_option_rows(trades_db, [
        opt(late, FRONT, CALL_STRIKE, "C", iv=0.50),
        opt(late, FRONT, PUT_STRIKE, "P", iv=0.50),
        opt(late, BACK, CALL_STRIKE, "C", iv=0.25),
        opt(late, BACK, PUT_STRIKE, "P", iv=0.25),
    ])
    _log_trade(trades_db)
    assert db.get_trade(trades_db, "T-100")["entry_front_iv"] == pytest.approx(0.21)

    db.update_trade(trades_db, "T-100", entry_time="14:00")

    t = db.get_trade(trades_db, "T-100")
    assert t["entry_iv_snapshot_id"] == late
    assert t["entry_front_iv"] == pytest.approx(0.50)


def test_editing_an_unrelated_column_leaves_the_context_alone(trades_db):
    """Recomputation is not free — it reads option_rows — and an edit to the
    notes has no business re-deriving anything."""
    _seed_entry_context(trades_db)
    _log_trade(trades_db)
    _drop_option_rows(trades_db)          # recomputing now would blank it

    db.update_trade(trades_db, "T-100", notes="changed my mind", status="Closed")

    assert db.get_trade(trades_db, "T-100")["entry_front_iv"] == pytest.approx(0.21)


# ─────────────────────────────────────────────────────────────────────────────
# The Gamma Exposure tab's two reads
#
# Both aggregate in SQL, which is the whole point of them — the raw join is
# ~400,000 rows a session — and an aggregation is exactly where a wrong answer
# arrives silently. A dte filter applied after the GROUP BY, a status filter
# dropped, a prior session picked from the wrong end: none of those raise, they
# just draw a chart that is confidently wrong.
# ─────────────────────────────────────────────────────────────────────────────

def _gex_seed(temp_db) -> str:
    """Two snapshots on one session, plus a prior session to difference with."""
    # Fixed at midday rather than ts_ago(minutes=...): two timestamps a few
    # minutes apart straddle midnight UTC once a day, which would split this
    # "session" across two dates and fail on a schedule nobody would connect
    # back to here.
    day = (datetime.now(UTC) - timedelta(days=1)).date()
    yesterday = f"{day - timedelta(days=1)} 20:00:00"
    y = add_snapshot(temp_db, yesterday, spx=5990.0)
    db.insert_option_rows(temp_db, [
        opt(y, FRONT, CALL_STRIKE, "C", dte=8),
        opt(y, FRONT, PUT_STRIKE, "P", dte=8),
    ])

    early, late = f"{day} 14:00:00", f"{day} 14:30:00"
    a = add_snapshot(temp_db, early, spx=6000.0)
    b = add_snapshot(temp_db, late, spx=6010.0)
    for sid in (a, b):
        db.insert_option_rows(temp_db, [
            opt(sid, FRONT, CALL_STRIKE, "C", dte=0),
            opt(sid, FRONT, PUT_STRIKE, "P", dte=0),
            opt(sid, BACK, CALL_STRIKE, "C", dte=21),
        ])
    return early[:10]


def test_intraday_metrics_return_one_row_per_snapshot_and_strike(temp_db):
    session = _gex_seed(temp_db)
    rows = db.get_intraday_strike_metrics(temp_db, session)

    assert len(rows) == 4                       # 2 snapshots x 2 strikes
    assert [r["strike"] for r in rows] == [PUT_STRIKE, CALL_STRIKE] * 2
    first = rows[1]                             # the call strike, early snapshot
    assert first["underlying_price"] == pytest.approx(6000.0)
    # Both the 0DTE call and the 21DTE call sit at this strike and are summed:
    # gamma 0.01 x oi 1000, twice.
    assert first["call_gamma_oi"] == pytest.approx(20.0)
    assert first["put_gamma_oi"] == pytest.approx(0.0)
    assert first["call_oi"] == 2000
    assert first["call_volume"] == 200


def test_the_dte_bound_is_applied_before_the_grouping(temp_db):
    """The 0DTE flow chart's whole claim is that its lines are 0DTE. Filter
    after the GROUP BY and the longer-dated call at the same strike is folded
    in first — the number changes, the label does not, and nothing errors."""
    session = _gex_seed(temp_db)
    rows = db.get_intraday_strike_metrics(temp_db, session, dte_max=0)

    assert len(rows) == 4
    call = next(r for r in rows if r["strike"] == CALL_STRIKE)
    assert call["call_gamma_oi"] == pytest.approx(10.0)   # not 20 — one leg only
    assert call["call_oi"] == 1000


def test_intraday_metrics_ignore_a_partial_snapshot(temp_db):
    """A PARTIAL snapshot is a half-written chain. Counted here it would draw a
    cliff in the middle of the day that no market ever made."""
    session = _gex_seed(temp_db)
    torn = add_snapshot(temp_db, f"{session} 14:15:00", status="PARTIAL")
    db.insert_option_rows(temp_db, [opt(torn, FRONT, CALL_STRIKE, "C", dte=0)])

    ids = {r["snapshot_id"] for r in db.get_intraday_strike_metrics(temp_db, session)}
    assert torn not in ids


def test_intraday_metrics_are_empty_for_a_session_with_no_snapshots(temp_db):
    _gex_seed(temp_db)
    assert db.get_intraday_strike_metrics(temp_db, "1999-01-01") == []


def test_prior_session_oi_reads_yesterdays_last_snapshot_not_todays_first(temp_db):
    """Open interest does not move within a session, so differencing today
    against today's own first snapshot returns zeros forever — a dead chart
    that looks like a quiet market."""
    session = _gex_seed(temp_db)
    rows = db.get_prior_session_oi(temp_db, session)

    assert [r["strike"] for r in rows] == [PUT_STRIKE, CALL_STRIKE]
    call = rows[1]
    # Yesterday held ONE call leg at this strike; today holds two. Reading
    # today's snapshot by mistake would give 2000 here.
    assert call["call_oi"] == 1000
    assert call["put_oi"] == 0


def test_prior_session_oi_can_be_scoped_to_one_expiry(temp_db):
    """It has to be, and this is the bug that proved it.

    The caller differences ONE expiry's open interest against this. Summed
    across every expiry at a strike, the subtraction reports the rest of the
    board as having been liquidated overnight — on the live 2026-09-04 chain
    the 8 Sep expiry read -1,850,786 contracts when the true figure was
    +17,870, and every verdict on the panel flipped with it."""
    day = (datetime.now(UTC) - timedelta(days=1)).date()
    prior = add_snapshot(temp_db, f"{day - timedelta(days=1)} 20:00:00")
    db.insert_option_rows(temp_db, [
        opt(prior, FRONT, CALL_STRIKE, "C", dte=8),
        opt(prior, BACK, CALL_STRIKE, "C", dte=29),
    ])
    session = f"{day} 14:00:00"
    today = add_snapshot(temp_db, session)
    db.insert_option_rows(temp_db, [opt(today, FRONT, CALL_STRIKE, "C", dte=0)])

    everything = db.get_prior_session_oi(temp_db, session[:10])
    front_only = db.get_prior_session_oi(temp_db, session[:10], FRONT)

    assert everything[0]["call_oi"] == 2000      # both expiries summed
    assert front_only[0]["call_oi"] == 1000      # the FRONT leg alone


def test_prior_session_oi_without_an_expiry_still_sums_the_whole_board(temp_db):
    """The All-expiries selection depends on it: the filter is opt-in, and a
    None must not quietly narrow the read."""
    session = _gex_seed(temp_db)
    rows = db.get_prior_session_oi(temp_db, session, None)
    assert [r["strike"] for r in rows] == [PUT_STRIKE, CALL_STRIKE]


def test_prior_session_oi_for_an_expiry_that_did_not_exist_yesterday(temp_db):
    """Not an error — a contract listed today has no yesterday, and the caller
    reads that as open interest built from zero."""
    session = _gex_seed(temp_db)
    assert db.get_prior_session_oi(temp_db, session, "2099-01-15") == []


def test_prior_session_oi_is_empty_on_the_first_day_of_collection(temp_db):
    """The first collection day and the caller that assumed yesterday existed."""
    today = datetime.now(UTC).date().isoformat()
    sid = add_snapshot(temp_db, f"{today} 14:00:00")
    db.insert_option_rows(temp_db, [opt(sid, FRONT, CALL_STRIKE, "C")])
    assert db.get_prior_session_oi(temp_db, today) == []
