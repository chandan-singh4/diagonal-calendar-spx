"""
Tests for the collection cycle in collector.py — `_run_cycle()`.

WHY THIS MODULE (M1.7): `_run_cycle` is the function that actually produces the
data. Every price in the 1.3 GB database arrived through it. Until now it had
no checks at all, while the gap classifier around it had 317 lines of them —
the alarm system was tested and the thing it guards was not.

WHAT IS BEING PROTECTED. Three properties matter more than the rest, because a
failure in any of them is silent — the collector keeps running, the log shows
nothing unusual, and the damage is only visible months later when the history
is read back:

  1. A snapshot is NEVER left PARTIAL. It is opened as PARTIAL on purpose so a
     crash leaves an auditable record, but every path out of the function must
     seal it — COMPLETE, PARTIAL-with-a-reason, or FAILED.
  2. IV is stored as a DECIMAL. Schwab sends 18.4 meaning 18.4%; the database
     holds 0.184. Getting this wrong by a factor of 100 would corrupt every
     downstream IV percentile, and nothing would raise.
  3. A failed cycle still finalizes its snapshot as FAILED, so a lost cycle
     leaves a trace instead of an orphaned PARTIAL row.

NO NETWORK, NO TOKEN, NO PRODUCTION DATABASE. The Schwab calls are patched via
the `patch_schwab` fixture and the writes go to `temp_db`. See the conftest
scope note: no test may touch data/dashboard.db.

ASSERTING ON THE DATABASE, NOT ON MOCKS. These tests read back what was stored
rather than checking that a function was called. Asserting on calls would only
confirm that the code does what it currently does; reading the rows confirms
the contract the dashboard actually depends on.
"""
from __future__ import annotations

import sqlite3

import pytest
from conftest import make_raw_chain

import collector

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rows(db_path: str, sql: str, *params) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def snapshot(db_path: str, snapshot_id: int) -> sqlite3.Row:
    return _rows(db_path, "SELECT * FROM snapshots WHERE snapshot_id = ?",
                 snapshot_id)[0]


def option_rows(db_path: str, snapshot_id: int) -> list[sqlite3.Row]:
    return _rows(db_path, "SELECT * FROM option_rows WHERE snapshot_id = ?",
                 snapshot_id)


def atm_rows(db_path: str, snapshot_id: int) -> list[sqlite3.Row]:
    return _rows(
        db_path,
        "SELECT * FROM atm_iv_by_expiry WHERE snapshot_id = ? ORDER BY dte",
        snapshot_id,
    )


def run(fake_client, temp_db, session="MIDDAY", poll_interval=300) -> int:
    return collector._run_cycle(fake_client, temp_db, session, poll_interval)


# ─────────────────────────────────────────────────────────────────────────────
# The healthy path
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthyCycle:

    def test_cycle_seals_the_snapshot_as_complete(self, fake_client, temp_db,
                                                   patch_schwab):
        patch_schwab()
        snap_id = run(fake_client, temp_db)

        snap = snapshot(temp_db, snap_id)
        assert snap["status"] == "COMPLETE"
        assert snap["error_message"] is None

    def test_no_snapshot_is_left_partial(self, fake_client, temp_db, patch_schwab):
        """PARTIAL is a transient state, never a resting one on a good cycle.

        The snapshot is created as PARTIAL before the chain is fetched so a
        crash mid-cycle leaves a record. If a healthy cycle ever ENDED there,
        the dashboard would treat complete data as suspect and nothing would
        warn about it.
        """
        patch_schwab()
        snap_id = run(fake_client, temp_db)

        assert snapshot(temp_db, snap_id)["status"] != "PARTIAL"

    def test_underlying_and_vix_are_recorded(self, fake_client, temp_db,
                                              patch_schwab):
        patch_schwab(
            quote={"bid": 5999.0, "ask": 6001.0, "last": 6000.5, "mark": 6000.0},
            vix=18.5,
        )
        snap = snapshot(temp_db, run(fake_client, temp_db))

        # mark is preferred over last as the underlying price.
        assert snap["underlying_price"] == 6000.0
        assert snap["underlying_bid"] == 5999.0
        assert snap["underlying_ask"] == 6001.0
        assert snap["vix_value"] == 18.5

    def test_session_and_poll_interval_are_recorded(self, fake_client, temp_db,
                                                     patch_schwab):
        """These two columns are what later analysis uses to tell a 60-second
        OPEN cadence from a 300-second MIDDAY one. Stored wrong, gap analysis
        judges every OPEN cycle against the wrong expectation."""
        patch_schwab()
        snap = snapshot(temp_db, run(fake_client, temp_db,
                                     session="OPEN", poll_interval=60))

        assert snap["market_session"] == "OPEN"
        assert snap["poll_interval_used"] == 60

    def test_counts_match_the_rows_actually_stored(self, fake_client, temp_db,
                                                    patch_schwab):
        """strikes_fetched must equal the real row count.

        db.insert_option_rows() can store FEWER rows than it was offered — its
        INSERT OR IGNORE silently drops rows failing any constraint (ADR-022).
        A count taken from the offered list rather than the stored one would
        overstate coverage in exactly the case worth knowing about.
        """
        patch_schwab()
        snap_id = run(fake_client, temp_db)
        snap = snapshot(temp_db, snap_id)

        assert snap["strikes_fetched"] == len(option_rows(temp_db, snap_id))
        assert snap["expiries_fetched"] == len(atm_rows(temp_db, snap_id))

    def test_count_reports_stored_rows_when_the_database_drops_some(
            self, fake_client, temp_db, patch_schwab):
        """strikes_fetched must never overstate what survived the write.

        BUG-017, recorded 2026-07-26. `db.insert_option_rows()` returns the
        count ACTUALLY STORED — its INSERT OR IGNORE silently discards any row
        failing a constraint, not just duplicates (ADR-022). The collector
        discarded that return value and recorded the OFFERED count instead, so
        a snapshot that lost rows still reported full coverage.

        Why it matters beyond tidiness: the whole point of the history is that
        it cannot be re-fetched. A snapshot claiming 3,096 rows while holding
        2,000 is not a cosmetic error — it is a hole in the record that reads
        as intact, and no later check could distinguish it from a real one.

        The other test in this class compares the two counts on healthy data,
        where they agree by construction and the bug is invisible. This one
        forces them apart with a duplicate contract, which is a shape Schwab
        can genuinely return.
        """
        patch_schwab(raw_chain=make_raw_chain(duplicate_contracts=True))
        snap_id = run(fake_client, temp_db)

        stored = len(option_rows(temp_db, snap_id))
        assert snapshot(temp_db, snap_id)["strikes_fetched"] == stored

    def test_latency_is_recorded(self, fake_client, temp_db, patch_schwab):
        patch_schwab()
        snap = snapshot(temp_db, run(fake_client, temp_db))

        assert snap["collection_latency_ms"] is not None
        assert snap["collection_latency_ms"] >= 0


# ─────────────────────────────────────────────────────────────────────────────
# Unit conversion — the silent-corruption risk
# ─────────────────────────────────────────────────────────────────────────────

class TestUnitsAndDerivedValues:

    def test_iv_is_stored_as_a_decimal_not_a_percentage(self, fake_client,
                                                         temp_db, patch_schwab):
        """Schwab sends 18.4 for 18.4%; the database holds 0.184.

        This is the single most dangerous conversion in the collector. A
        factor-of-100 error raises nothing, writes happily, and corrupts every
        IV percentile computed from the history afterwards.
        """
        patch_schwab(raw_chain=make_raw_chain(iv=18.4))
        snap_id = run(fake_client, temp_db)

        stored = option_rows(temp_db, snap_id)
        assert stored, "no rows stored — the assertion below would pass vacuously"
        for r in stored:
            assert r["iv"] == pytest.approx(0.184)

    def test_mark_is_the_bid_ask_midpoint(self, fake_client, temp_db,
                                           patch_schwab):
        patch_schwab()   # every contract quoted bid 9.0 / ask 11.0
        snap_id = run(fake_client, temp_db)

        for r in option_rows(temp_db, snap_id):
            assert r["mark"] == pytest.approx(10.0)

    def test_intrinsic_value_respects_the_side_of_the_contract(
            self, fake_client, temp_db, patch_schwab):
        """A call is worth spot - strike; a put, strike - spot; never below zero.

        Checked at a strike 100 points below a 6000 spot, where the two sides
        disagree — so a transposed formula cannot pass. At the money both are
        zero and the bug would hide.
        """
        patch_schwab(raw_chain=make_raw_chain(spot=6000.0))
        snap_id = run(fake_client, temp_db)

        rows = option_rows(temp_db, snap_id)
        itm_call = next(r for r in rows
                        if r["right"] == "C" and r["strike"] == 5900.0)
        otm_put = next(r for r in rows
                       if r["right"] == "P" and r["strike"] == 5900.0)

        assert itm_call["intrinsic_value"] == pytest.approx(100.0)
        assert otm_put["intrinsic_value"] == pytest.approx(0.0)

    def test_time_value_is_mark_minus_intrinsic(self, fake_client, temp_db,
                                                 patch_schwab):
        patch_schwab(raw_chain=make_raw_chain(spot=6000.0))
        snap_id = run(fake_client, temp_db)

        for r in option_rows(temp_db, snap_id):
            assert r["time_value"] == pytest.approx(
                r["mark"] - r["intrinsic_value"]
            )

    def test_atm_strike_is_the_one_nearest_the_underlying(self, fake_client,
                                                          temp_db, patch_schwab):
        patch_schwab(
            quote={"bid": None, "ask": None, "last": 6020.0, "mark": 6020.0},
            raw_chain=make_raw_chain(spot=6000.0),
        )
        snap_id = run(fake_client, temp_db)

        # Ladder is 5900/5950/6000/6050/6100; 6020 is nearest 6000.
        for rec in atm_rows(temp_db, snap_id):
            assert rec["atm_strike"] == 6000.0

    def test_front_expiry_has_no_spread_to_itself(self, fake_client, temp_db,
                                                   patch_schwab):
        """records[0] is the shortest-DTE expiry, and comparing it to itself is
        meaningless — those fields must stay NULL rather than 0.0, which would
        read as 'measured, and flat'."""
        patch_schwab()
        recs = atm_rows(temp_db, run(fake_client, temp_db))

        assert recs[0]["iv_spread_to_front"] is None
        assert recs[0]["iv_ratio_to_front"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Degraded data
# ─────────────────────────────────────────────────────────────────────────────

class TestDegradedData:

    def test_contracts_without_iv_are_skipped_not_stored_as_zero(
            self, fake_client, temp_db, patch_schwab):
        """A missing IV means no market, not zero volatility.

        This is the collector's half of the settled rule 'when a price is
        missing, show nothing rather than zero'. Storing 0.0 would drag every
        average IV down and look like real data.
        """
        patch_schwab(raw_chain=make_raw_chain(
            spot=6000.0, missing_iv_strikes={5900.0, 6100.0}))
        snap_id = run(fake_client, temp_db)

        stored = {r["strike"] for r in option_rows(temp_db, snap_id)}
        assert 5900.0 not in stored
        assert 6100.0 not in stored
        assert stored == {5950.0, 6000.0, 6050.0}

    def test_a_chain_with_no_usable_iv_is_marked_failed(self, fake_client,
                                                         temp_db, patch_schwab):
        patch_schwab(raw_chain=make_raw_chain(
            spot=6000.0,
            missing_iv_strikes={5900.0, 5950.0, 6000.0, 6050.0, 6100.0},
        ))
        snap_id = run(fake_client, temp_db)

        snap = snapshot(temp_db, snap_id)
        assert snap["status"] == "FAILED"
        assert snap["error_message"] is not None
        assert option_rows(temp_db, snap_id) == []

    def test_a_missing_vix_does_not_fail_the_cycle(self, fake_client, temp_db,
                                                    patch_schwab):
        """VIX is context, not the product. Losing it must not cost a snapshot
        of prices."""
        patch_schwab(vix=None)
        snap = snapshot(temp_db, run(fake_client, temp_db))

        assert snap["status"] == "COMPLETE"
        assert snap["vix_value"] is None

    def test_price_falls_back_to_last_when_there_is_no_mark(self, fake_client,
                                                             temp_db,
                                                             patch_schwab):
        """SPX is an index, so Schwab does not always publish a two-sided quote."""
        patch_schwab(quote={"bid": None, "ask": None, "last": 6000.5,
                            "mark": None})
        snap = snapshot(temp_db, run(fake_client, temp_db))

        assert snap["underlying_price"] == 6000.5
        assert snap["status"] == "COMPLETE"


# ─────────────────────────────────────────────────────────────────────────────
# Failure handling
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureHandling:

    def test_a_quote_failure_raises_and_writes_no_snapshot(self, fake_client,
                                                            temp_db,
                                                            patch_schwab):
        """The quote is fetched BEFORE the snapshot row is created, so there is
        nothing to seal — and nothing should be left behind either."""
        patch_schwab(quote_error=RuntimeError("Schwab timed out"))

        with pytest.raises(RuntimeError):
            run(fake_client, temp_db)

        assert _rows(temp_db, "SELECT * FROM snapshots") == []

    def test_a_priceless_quote_is_rejected(self, fake_client, temp_db,
                                            patch_schwab):
        patch_schwab(quote={"bid": None, "ask": None, "last": None,
                            "mark": None})

        with pytest.raises(ValueError):
            run(fake_client, temp_db)

    def test_a_chain_failure_seals_the_snapshot_as_failed(self, fake_client,
                                                           temp_db,
                                                           patch_schwab):
        """The dangerous one. The snapshot row already exists as PARTIAL by the
        time the chain is fetched. If the handler did not seal it, every failed
        cycle would leave an orphaned PARTIAL row that no code ever revisits.
        """
        patch_schwab(chain_error=RuntimeError("Schwab 500"))

        with pytest.raises(RuntimeError):
            run(fake_client, temp_db)

        snaps = _rows(temp_db, "SELECT * FROM snapshots")
        assert len(snaps) == 1
        assert snaps[0]["status"] == "FAILED"
        assert "Schwab 500" in snaps[0]["error_message"]

    def test_an_empty_chain_seals_the_snapshot_as_failed(self, fake_client,
                                                          temp_db, patch_schwab):
        patch_schwab(raw_chain={})

        with pytest.raises(ValueError):
            run(fake_client, temp_db)

        assert _rows(temp_db, "SELECT * FROM snapshots")[0]["status"] == "FAILED"

    def test_the_error_message_is_truncated_to_the_column_width(
            self, fake_client, temp_db, patch_schwab):
        """Schwab errors can carry a whole HTML page. The handler caps them at
        500 characters; an uncapped write would fail, and it would fail inside
        the error handler — losing the original error too."""
        patch_schwab(chain_error=RuntimeError("x" * 5000))

        with pytest.raises(RuntimeError):
            run(fake_client, temp_db)

        msg = _rows(temp_db, "SELECT * FROM snapshots")[0]["error_message"]
        assert len(msg) <= 500


# ─────────────────────────────────────────────────────────────────────────────
# Expiry trimming
# ─────────────────────────────────────────────────────────────────────────────

class TestExpiryTrimming:

    def test_only_the_nearest_configured_expiries_are_kept(self, fake_client,
                                                            temp_db,
                                                            patch_schwab,
                                                            monkeypatch):
        """The fetch casts a wide net (90 days) and the collector trims to the
        nearest MAX_EXPIRY_COUNT. Trimming from the wrong end would silently
        collect long-dated expiries and discard the front ones the strategy is
        actually built on."""
        monkeypatch.setattr(collector.config, "MAX_EXPIRY_COUNT", 2)
        patch_schwab(raw_chain=make_raw_chain(expiries=[
            ("2026-08-07", 7),
            ("2026-08-14", 14),
            ("2026-08-21", 21),
            ("2026-09-18", 49),
        ]))
        snap_id = run(fake_client, temp_db)

        kept = {r["expiry_date"] for r in option_rows(temp_db, snap_id)}
        assert kept == {"2026-08-07", "2026-08-14"}
