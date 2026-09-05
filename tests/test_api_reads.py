"""M4.2 — the reads, the cache, and the one rule that is easy to break here.

WHAT IS ACTUALLY AT RISK IN THIS TASK, and therefore what is checked:

  * **Missing price → blank, never 0.** Every convenient serialisation path
    breaks this. It is the project's oldest data rule and the failure is
    invisible: a JSON body full of zeros parses perfectly.
  * **The cache keyed on the snapshot, not the clock.** An entry that
    survives a new snapshot is a server confidently answering with
    yesterday's prices.
  * **The session default in market time.** A 20:01 UTC snapshot belongs to
    the PREVIOUS calendar day in New York. Getting this wrong names the wrong
    session for every afternoon snapshot in the record.

None of it touches the real database.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.cache import SnapshotCache
from api.serialize import frame_to_records


@pytest.fixture
def client(temp_db) -> TestClient:
    return TestClient(create_app(db_path=temp_db))


def _snapshot(db_path: str, snapshot_id: int, stamp: str,
              price: float = 7718.36, status: str = "COMPLETE") -> None:
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO snapshots (snapshot_id, snapshot_timestamp, status, "
            "underlying_price) VALUES (?, ?, ?, ?)",
            (snapshot_id, stamp, status, price),
        )
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Missing price → blank, never 0
# ─────────────────────────────────────────────────────────────────────────────

def test_a_missing_number_serialises_as_null_not_zero():
    """The rule, at the only layer that can break it.

    A missing price means the broker returned nothing. A price of 0 means the
    market says it is worthless. On a long leg those are opposite conclusions.
    """
    df = pd.DataFrame({"mark": [1.25, np.nan, 0.0]})

    marks = [row["mark"] for row in frame_to_records(df)]

    assert marks == [1.25, None, 0.0], (
        "NaN must become null and a real 0.0 must survive as 0.0 — fillna(0) "
        "would collapse both into the same lie"
    )


def test_a_missing_timestamp_serialises_as_null():
    df = pd.DataFrame({"timestamp": [pd.Timestamp("2026-09-04 20:01", tz="UTC"),
                                     pd.NaT]})

    stamps = [row["timestamp"] for row in frame_to_records(df)]

    assert stamps[1] is None
    assert stamps[0].endswith("+00:00"), "the offset must survive the wire"


def test_numpy_scalars_survive_serialisation():
    """int64/float64 are not the Python builtins and `json` cannot encode
    them; the failure is a 500 on a route that queried perfectly."""
    df = pd.DataFrame({"volume": np.array([12, 0], dtype="int64"),
                       "gamma": np.array([0.5, np.nan], dtype="float64")})

    rows = frame_to_records(df)

    assert rows[0] == {"volume": 12, "gamma": 0.5}
    assert rows[1] == {"volume": 0, "gamma": None}


def test_an_empty_frame_is_an_empty_list_not_a_row_of_zeros():
    assert frame_to_records(pd.DataFrame()) == []


def test_a_read_with_no_rows_returns_an_empty_list(client: TestClient, temp_db):
    _snapshot(temp_db, 1, "2026-09-04 20:01:00")

    body = client.get("/atm-history", params={"expiry": "2026-09-18"}).json()

    assert body["count"] == 0
    assert body["rows"] == []


# ─────────────────────────────────────────────────────────────────────────────
# The cache — keyed on the snapshot, and on nothing else
# ─────────────────────────────────────────────────────────────────────────────

def test_the_cache_returns_the_same_answer_within_one_snapshot():
    cache = SnapshotCache()
    calls = []

    def compute():
        calls.append(1)
        return "answer"

    assert cache.get_or_compute(6387, ("k",), compute) == "answer"
    assert cache.get_or_compute(6387, ("k",), compute) == "answer"
    assert len(calls) == 1, "the second call must not recompute"


def test_a_new_snapshot_invalidates_everything_from_the_old_one():
    """Not per-entry expiry. Every entry is stale by the same event, and
    holding two generations means a caller can mix them — reading the chain
    from 6387 and the metrics from 6386, which is how the churn verdicts
    ended up comparing two different days."""
    cache = SnapshotCache()
    cache.get_or_compute(6387, ("a",), lambda: "old")
    cache.get_or_compute(6387, ("b",), lambda: "old")

    assert cache.get_or_compute(6388, ("a",), lambda: "new") == "new"
    assert cache.stats()["entries"] == 1, "6387's entries must all be gone"


def test_the_cache_does_not_expire_on_time():
    """There is no TTL, deliberately. The page's memo has 55/120/300-second
    TTLs on entries keyed by snapshot_id, so a request after a lapse redoes a
    full query to produce a byte-identical answer (ENH-011). M4 does not
    inherit that."""
    cache = SnapshotCache()
    calls = []
    cache.get_or_compute(6387, ("k",), lambda: calls.append(1))

    for _ in range(50):
        cache.get_or_compute(6387, ("k",), lambda: calls.append(1))

    assert len(calls) == 1


def test_an_answer_computed_under_a_superseded_snapshot_is_not_filed():
    """The snapshot can advance while a slow query runs. That answer is still
    correct for the caller who asked, but filing it under the NEW generation
    would serve 6387's data labelled 6388."""
    cache = SnapshotCache()

    def slow_compute():
        # The collector lands a new snapshot mid-query.
        cache.get_or_compute(6388, ("other",), lambda: "newer")
        return "computed under 6387"

    result = cache.get_or_compute(6387, ("k",), slow_compute)

    assert result == "computed under 6387", "the caller still gets its answer"
    assert cache.get_or_compute(6388, ("k",), lambda: "recomputed") == "recomputed"


def test_the_cache_is_bounded():
    cache = SnapshotCache(max_entries=4)
    for i in range(12):
        # `value=i` binds now. A bare `lambda: i` closes over the loop
        # variable and every entry would end up as 11 — the assertion would
        # still pass, while checking something other than what it says.
        cache.get_or_compute(1, (i,), lambda value=i: value)

    assert cache.stats()["entries"] <= 4


def test_health_publishes_cache_statistics(client: TestClient):
    stats = client.get("/health").json()["cache"]

    assert set(stats) >= {"generation", "entries", "hits", "misses"}


# ─────────────────────────────────────────────────────────────────────────────
# The session default has to be market time
# ─────────────────────────────────────────────────────────────────────────────

def test_the_default_session_is_the_market_day_not_the_utc_day(
        client: TestClient, temp_db):
    """20:01 UTC is 16:01 on 4 September in New York — the settled close.
    In UTC it is still the 4th here, but at 00:30 UTC it would be the 5th
    while the session is plainly the 4th."""
    _snapshot(temp_db, 1, "2026-09-05 00:30:00")

    body = client.get("/spx/intraday").json()

    assert body["session_date"] == "2026-09-04", (
        "a 00:30 UTC snapshot is 20:30 on the previous evening in New York"
    )


def test_an_explicit_session_date_is_honoured(client: TestClient, temp_db):
    _snapshot(temp_db, 1, "2026-09-04 20:01:00")

    body = client.get("/spx/intraday",
                      params={"session_date": "2026-08-01"}).json()

    assert body["session_date"] == "2026-08-01"


def test_a_read_needing_a_session_says_so_when_the_record_is_empty(
        client: TestClient):
    """Not a 500, and not a silently wrong date."""
    response = client.get("/spx/intraday")

    assert response.status_code == 503
    assert "session" in response.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Routing and contract
# ─────────────────────────────────────────────────────────────────────────────

def test_the_chain_defaults_to_the_newest_snapshot(client: TestClient, temp_db):
    _snapshot(temp_db, 1, "2026-09-04 20:00:00")
    _snapshot(temp_db, 2, "2026-09-04 20:01:00")

    assert client.get("/chain").json()["snapshot_id"] == 2


def test_the_chain_reports_a_missing_record_rather_than_failing(client: TestClient):
    assert client.get("/chain").status_code == 503


def test_prior_close_is_null_when_there_is_no_prior_session(
        client: TestClient, temp_db):
    """Not 0. The S&P 500 has never closed at zero, and anything derived from
    that number would be wrong rather than merely absent."""
    _snapshot(temp_db, 1, "2026-09-04 20:01:00")

    assert client.get("/spx/prior-close").json()["prior_close"] is None


def test_days_is_bounded(client: TestClient, temp_db):
    _snapshot(temp_db, 1, "2026-09-04 20:01:00")

    response = client.get("/atm-history",
                          params={"expiry": "2026-09-18", "days": 100000})

    assert response.status_code == 422, (
        "an unbounded window would let one request sweep the whole record"
    )


def test_side_only_accepts_call_or_put(client: TestClient, temp_db):
    _snapshot(temp_db, 1, "2026-09-04 20:01:00")

    response = client.get("/contract-history", params={
        "expiry": "2026-09-18", "strike": 7700, "side": "SIDEWAYS", "days": 1})

    assert response.status_code == 422


def test_every_dataaccess_read_has_an_endpoint():
    """A read that exists but is not served is a hole nobody notices until a
    client needs it. Pinned to the source so adding a query to dataaccess/
    without an endpoint fails here rather than in six months."""
    import inspect

    from dataaccess import queries

    served = {
        "load_atm_hist", "load_atm_hist_fb", "load_contract_hist",
        "load_chain_df", "load_spx_intraday", "load_prior_close",
        "load_transform_marks", "load_latest_atm_iv", "load_diagonal_hist",
        "load_intraday_strike_metrics", "load_prior_session_oi",
    }
    defined = {
        name for name, obj in inspect.getmembers(queries, inspect.isfunction)
        if obj.__module__ == queries.__name__ and not name.startswith("_")
    }

    assert defined == served, (
        f"dataaccess/queries.py and the API have drifted.\n"
        f"  unserved reads: {sorted(defined - served)}\n"
        f"  served but gone: {sorted(served - defined)}"
    )
