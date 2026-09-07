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
from conftest import make_transform_history
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


def test_a_cached_route_survives_being_called_twice(tmp_path, temp_db):
    """A CACHE HIT HANDS BACK THE SAME OBJECT, NOT A COPY.

    /mission/gamma used to lift its frame out with `result.pop("by_strike")`.
    That reads as harmless and is not: `pop` mutated the dict the cache was
    still holding, so the first request answered correctly by emptying the
    entry and the second raised KeyError. Nothing caught it because no test
    had ever called the endpoint twice — a single call is exactly the case
    that works.

    So this asserts the second answer, not the first, and asserts it EQUALS
    the first. Any future route that mutates a cached value fails here.
    """
    make_transform_history(temp_db, [6.0, 6.5], interval_minutes=5)
    client = TestClient(create_app(db_path=temp_db,
                                   state_dir=str(tmp_path / "state")))

    first = client.get("/mission/gamma")
    second = client.get("/mission/gamma")

    assert first.status_code == 200
    assert second.status_code == 200, second.text
    assert second.json() == first.json(), (
        "a repeat request must not see a cache entry the first one damaged"
    )


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
        # /strikes/session-range?measure=... — the whole session's chain,
        # behind the wicks on every measure rather than gamma alone.
        "load_session_chain_df",
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


# The endpoint each read is served by. Kept beside the coverage test above,
# which proves every read HAS an endpoint but says nothing about whether that
# endpoint can ask the read everything it can answer.
_READ_ENDPOINTS = {
    "load_atm_hist": "/atm-history",
    "load_atm_hist_fb": "/atm-history",
    "load_contract_hist": "/contract-history",
    "load_chain_df": "/chain",
    "load_spx_intraday": "/spx/intraday",
    "load_prior_close": "/spx/prior-close",
    "load_transform_marks": "/pairs/transform-marks",
    "load_latest_atm_iv": "/atm-iv/latest",
    "load_diagonal_hist": "/pairs/diagonal-history",
    "load_intraday_strike_metrics": "/strikes/intraday-metrics",
    "load_prior_session_oi": "/strikes/prior-session-oi",
}


def test_every_read_parameter_is_reachable_from_its_endpoint(client: TestClient):
    """A read that grew an argument the API cannot pass is half-served.

    THE CASE THIS WAS WRITTEN FOR. ENH-014 added `expiry` scoping to
    `load_intraday_strike_metrics` for the Streamlit tab and did not add it to
    `/strikes/intraday-metrics`. Every test passed: the endpoint still worked,
    still returned correct rows, and simply could not be asked the new
    question. The test above did not see it because the endpoint existed.

    It matters more from M6 onward, not less: the React front end has no route
    to the database except this API, so an argument missing here is a feature
    that cannot be built rather than one that is merely awkward.
    """
    import inspect

    from dataaccess import queries

    spec = client.get("/openapi.json").json()
    missing = []
    for read, path in _READ_ENDPOINTS.items():
        params = inspect.signature(getattr(queries, read)).parameters
        wanted = {n for n, p in params.items()
                  if n not in ("db_path", "load")
                  and p.default is not inspect.Parameter.empty}
        exposed = {p["name"]
                   for p in spec["paths"][path]["get"].get("parameters", [])}
        for name in sorted(wanted - exposed):
            missing.append(f"{path} cannot pass {read}({name}=...)")

    assert missing == [], "reads the API cannot fully ask:\n  " + "\n  ".join(missing)


# ─────────────────────────────────────────────────────────────────────────────
# ENH-014 at the API layer — /strikes/intraday-metrics?expiry=
#
# What the read itself does with `expiry` is pinned in test_db.py and is not
# re-checked here. What only this layer can get wrong is the two things below:
# whether the argument arrives at the read at all, and whether it reaches the
# CACHE KEY. The second is the dangerous one, because an endpoint that scopes
# correctly and caches carelessly is right once and wrong afterwards.
# ─────────────────────────────────────────────────────────────────────────────

def _metrics(client: TestClient, session: str, **params) -> list[dict]:
    reply = client.get("/strikes/intraday-metrics",
                       params={"session_date": session, **params})
    assert reply.status_code == 200, reply.text
    return reply.json()["rows"]


def test_the_expiry_scope_survives_the_trip_through_the_endpoint(temp_db):
    """The seed puts a 0DTE and a 21DTE call at the SAME strike, so a scope
    that quietly did nothing would return a plausible chart with both legs
    summed into it rather than an error."""
    from test_db import BACK, CALL_STRIKE, PUT_STRIKE, _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    rows = _metrics(client, session, expiry=BACK)

    assert {r["strike"] for r in rows} == {CALL_STRIKE}, (
        f"the back-month board has no put leg; {PUT_STRIKE} means the scope "
        "was dropped somewhere between the query string and the read"
    )
    assert all(r["call_gamma_oi"] == pytest.approx(10.0) for r in rows), (
        "20.0 is both expiries summed — the scope arrived as None"
    )


def test_one_expiry_is_never_served_under_another_expiry_label(temp_db):
    """The cache key. Both requests name the same session and the same
    snapshot, so everything the old key knew about is identical between them;
    only `expiry` differs. Leave it out and the second caller is handed the
    first caller's frame — the right numbers, under the wrong contract, with
    nothing on screen to say so.

    Asked in this order deliberately: the whole-board answer is a SUPERSET, so
    a key that ignores `expiry` fails loudly here (extra strikes) instead of
    passing by luck.
    """
    from test_db import BACK, CALL_STRIKE, _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    whole_board = _metrics(client, session)
    back_month = _metrics(client, session, expiry=BACK)

    assert len(whole_board) > len(back_month), "the seed cannot tell these apart"
    assert {r["strike"] for r in back_month} == {CALL_STRIKE}, (
        "the back-month request was served the whole board out of the cache"
    )
    # And the other way round: the scoped answer must not become the cached
    # answer for the unscoped question either.
    assert _metrics(client, session) == whole_board


def test_the_expiry_is_echoed_back_so_a_chart_can_label_itself(temp_db):
    """The front end has no other way to know which contract it is holding.
    `dte_max` is already echoed for the same reason."""
    from test_db import BACK, _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    body = client.get("/strikes/intraday-metrics",
                      params={"session_date": session, "expiry": BACK}).json()

    assert body["expiry"] == BACK


# ─────────────────────────────────────────────────────────────────────────────
# Strike flow — contracts traded, and the label that stops it being read as
# order flow. The arithmetic is pinned in tests/test_strike_flow.py; what only
# this layer can get wrong is whether the strike and the expiry arrive at the
# read, whether they reach the CACHE KEY, and whether the honest `basis` label
# survives the trip to the wire.
# ─────────────────────────────────────────────────────────────────────────────

def _flow(client: TestClient, session: str, strike: float, **params) -> dict:
    reply = client.get("/strikes/flow",
                       params={"session_date": session, "strike": strike,
                               **params})
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_the_flow_response_says_what_the_numbers_are_not(temp_db):
    """The reference panel splits bought from sold. This cannot, and the
    response has to say so ON THE WIRE — a client that renders these as
    "calls bought" is making a claim the data cannot support, and it should
    have to contradict the payload to do it."""
    from test_db import CALL_STRIKE, _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    basis = _flow(client, session, CALL_STRIKE)["basis"]

    assert "contracts traded" in basis
    assert "not buyer- or seller-initiated" in basis


def test_the_first_bucket_reaches_the_wire_blank_and_not_zero(temp_db):
    """The project's oldest rule at the layer that breaks it. Zero here would
    read as "nothing traded in the first five minutes of every session"."""
    from test_db import CALL_STRIKE, _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    rows = _flow(client, session, CALL_STRIKE)["rows"]

    assert len(rows) == 2, "two snapshots, two midday buckets"
    assert rows[0]["call_volume"] is None
    assert rows[0]["total"] is None
    assert rows[1]["call_volume"] == 0, (
        "the seed's two snapshots carry the same running total, so nothing "
        "traded between them — a real 0, which must survive as 0"
    )


def test_the_endpoint_hands_the_read_the_collectors_own_cadence(temp_db):
    """The bucket width is config's, not this module's — but which constant
    goes to which argument is wiring only this layer can get wrong.

    The seed sits at 14:00 UTC, which is 10:00 in New York: midday, so five
    minutes. Hand `strike_flow` the wrong boundary and it buckets the session
    by the one-minute event cadence instead, and every bar afterwards is a
    fifth of the width its neighbours claim.
    """
    from test_db import CALL_STRIKE, _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    rows = _flow(client, session, CALL_STRIKE)["rows"]

    assert {r["bucket_secs"] for r in rows} == {300}


def test_the_strike_reaches_the_read_rather_than_being_ignored(temp_db):
    """A strike that was silently dropped would answer with the whole board's
    flow under one strike's heading — plausible, and wrong."""
    from test_db import _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    assert _flow(client, session, 1.0)["rows"] == []


def _two_expiry_seed(temp_db) -> tuple[str, float]:
    """Two snapshots, one strike, two expiries whose volumes DIFFER and grow.

    The difference is the point. The shared `_gex_seed` gives both expiries the
    same volume and never moves it, so a scope that was silently dropped would
    return a numerically identical answer and no test could tell. Here the
    whole board traded 30 contracts between the snapshots and the back month
    traded 20, so the two scopes cannot be confused.
    """
    import db as database
    from test_db import BACK, CALL_STRIKE, FRONT, add_snapshot, opt

    day = "2026-09-04"
    for stamp, front_vol, back_vol in ((f"{day} 14:00:00", 100, 500),
                                       (f"{day} 14:05:00", 110, 520)):
        sid = add_snapshot(temp_db, stamp, spx=6000.0)
        rows = []
        for expiry, dte, vol in ((FRONT, 0, front_vol), (BACK, 21, back_vol)):
            leg = opt(sid, expiry, CALL_STRIKE, "C", dte=dte)
            leg["volume"] = vol
            rows.append(leg)
        database.insert_option_rows(temp_db, rows)
    return day, CALL_STRIKE


def test_the_expiry_scope_changes_the_numbers_and_is_in_the_cache_key(temp_db):
    """An endpoint that scopes correctly and caches carelessly is right once
    and wrong afterwards — the trap the section above this one names.

    THE UNSCOPED CALL GOES FIRST, deliberately: that is the order that fills
    the cache with the wrong answer before the scoped question is asked, so a
    key missing `expiry` serves the board's flow under the back month's label.
    """
    from test_db import BACK

    session, strike = _two_expiry_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    whole = _flow(client, session, strike)["rows"]
    scoped = _flow(client, session, strike, expiry=BACK)["rows"]

    assert whole[1]["call_volume"] == 30, "10 front + 20 back"
    assert scoped[1]["call_volume"] == 20, (
        "30 is the whole board answering a back-month question — either the "
        "scope never reached the read, or it never reached the cache key"
    )


def test_two_strikes_asked_of_ONE_client_do_not_share_an_answer(temp_db):
    """The cache-key trap again, on the argument that is easiest to forget
    because it is the endpoint's headline parameter.

    ONE CLIENT, TWO STRIKES. Every other test here builds a fresh client, and
    a fresh client has an empty cache — so a key missing `strike` would pass
    all of them and fail only in the browser, where one tab asks about several
    strikes in a row and the second one silently answers with the first's
    numbers.
    """
    import db as database
    from test_db import FRONT, add_snapshot, opt

    day = "2026-09-04"
    for stamp, quiet, busy in ((f"{day} 14:00:00", 100, 900),
                               (f"{day} 14:05:00", 110, 960)):
        sid = add_snapshot(temp_db, stamp, spx=6000.0)
        rows = []
        for strike, vol in ((6000.0, quiet), (6100.0, busy)):
            leg = opt(sid, FRONT, strike, "C", dte=0)
            leg["volume"] = vol
            rows.append(leg)
        database.insert_option_rows(temp_db, rows)

    client = TestClient(create_app(db_path=temp_db))

    quiet_rows = _flow(client, day, 6000.0)["rows"]
    busy_rows = _flow(client, day, 6100.0)["rows"]

    assert quiet_rows[1]["call_volume"] == 10
    assert busy_rows[1]["call_volume"] == 60, (
        "10 is the first strike's answer served under the second strike's "
        "label — `strike` is missing from the cache key"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The two "through the session" panels. The arithmetic and the two ranking
# rules are pinned in tests/test_timelines.py and proved by mutation; what
# only this layer can get wrong is whether `expiry`, `dte_max` and `count`
# reach the read AND the cache key, and whether the headline totals travel.
# ─────────────────────────────────────────────────────────────────────────────

def _busy_board_seed(temp_db) -> str:
    """Three strikes of unequal activity across two snapshots, front and back.

    UNEQUAL ON PURPOSE, in three dimensions. The volumes differ per strike so
    a ranking that never ran is visible; the front and back months differ so a
    scope that was silently dropped changes the numbers rather than returning
    a plausible identical answer; and CALL AND PUT OPEN INTEREST DIFFER so the
    net gamma is not zero.

    That last one is not a nicety. The first version of this seed left `opt`'s
    defaults alone, so every leg carried the same gamma x open interest, every
    strike netted to exactly 0, and the dte_max check asserted `0 == 0 * 2/3`
    — a test that passed against any implementation at all.

    The open interest is laid out so the arithmetic in the tests below is
    checkable by hand: per strike, front nets 10 - 2 = 8 gamma-OI units and
    back nets 5 - 1 = 4, so the whole board is 12 and the 0DTE slice is 8 —
    two thirds of it.
    """
    import db as database
    from test_db import BACK, FRONT, add_snapshot, opt

    day = "2026-09-04"
    for stamp, step in ((f"{day} 14:00:00", 0), (f"{day} 14:05:00", 1)):
        sid = add_snapshot(temp_db, stamp, spx=6000.0)
        rows = []
        for strike, base in ((6000.0, 900), (6100.0, 300), (6200.0, 50)):
            for expiry, dte, share in ((FRONT, 0, 1.0), (BACK, 21, 0.5)):
                leg = opt(sid, expiry, strike, "C", dte=dte)
                leg["volume"] = int((base + 10 * step) * share)
                leg["open_interest"] = int(1000 * share)
                rows.append(leg)
                put = opt(sid, expiry, strike, "P", dte=dte)
                put["volume"] = 0
                put["open_interest"] = int(200 * share)
                rows.append(put)
        database.insert_option_rows(temp_db, rows)
    return day


def _net_volume(client: TestClient, session: str, **params) -> dict:
    reply = client.get("/strikes/net-volume",
                       params={"session_date": session, **params})
    assert reply.status_code == 200, reply.text
    return reply.json()


def _gex_timeline(client: TestClient, session: str, **params) -> dict:
    reply = client.get("/strikes/gex-timeline",
                       params={"session_date": session, **params})
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_BOTH_SESSION_PANELS_serve_the_market_clock_not_UTC(temp_db):
    """THE LAYER THE BROWSER ACTUALLY READS. core/timelines.py is proved to
    convert, but a chart is drawn from the STRING on the wire, and plotly
    takes the wall-clock part of that string and ignores the offset after it.
    So 14:00 UTC served as "14:00:00+00:00" draws at two in the afternoon —
    a session that appears to run to eight at night, which is what these two
    panels did (Chandan, 2026-09-06).

    The seed's first snapshot is 14:00 UTC, which is 10:00 in New York.
    Asserting the whole string rather than just the offset catches a frame
    that was relabelled instead of converted.
    """
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    for name, payload in (("net volume", _net_volume(client, session)),
                          ("gex timeline", _gex_timeline(client, session))):
        first = min(row["timestamp"] for row in payload["rows"])
        assert first == "2026-09-04T10:00:00-04:00", (
            f"the {name} panel is serving UTC; its x axis will read four "
            f"hours late"
        )


def test_the_net_volume_response_says_the_figure_is_cumulative(temp_db):
    """These lines climb all day because `volume` is a running session total,
    not a per-bucket count. A reader who takes a rising line for rising
    activity has it backwards, so the payload says which it is."""
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    basis = _net_volume(client, session)["basis"]

    assert "cumulative" in basis
    assert "not buyer- or seller-initiated" in basis


def test_count_limits_the_strikes_that_reach_the_wire(temp_db):
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    body = _net_volume(client, session, count=2)

    assert {r["strike"] for r in body["rows"]} == {6000.0, 6100.0}
    assert body["count_requested"] == 2


def test_TWO_COUNTS_asked_of_ONE_CLIENT_do_not_share_an_answer(temp_db):
    """The cache-key trap on the parameter added last, which is the one most
    easily left out of the key.

    ONE CLIENT, because a fresh one has an empty cache — a key missing `count`
    passes every test that builds its own client and fails only in the
    browser, where the same tab changes the line count and the chart does not
    move. THE NARROW CALL GOES FIRST so the cache is primed with too few
    strikes before the wider question is asked.
    """
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    narrow = _net_volume(client, session, count=1)["rows"]
    wide = _net_volume(client, session, count=3)["rows"]

    assert len({r["strike"] for r in narrow}) == 1
    assert len({r["strike"] for r in wide}) == 3, (
        "one strike answering a three-strike question — `count` is missing "
        "from the cache key"
    )


def test_the_net_volume_expiry_scope_changes_the_numbers_and_is_in_the_key(temp_db):
    """THE UNSCOPED CALL GOES FIRST, so a key missing `expiry` serves the
    whole board's numbers under the back month's label."""
    from test_db import BACK

    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    whole = _net_volume(client, session)["rows"]
    scoped = _net_volume(client, session, expiry=BACK)["rows"]

    busiest = [r for r in whole if r["strike"] == 6000.0][-1]["net_volume"]
    scoped_busiest = [r for r in scoped if r["strike"] == 6000.0][-1]["net_volume"]

    assert busiest == 1365, "910 front + 455 back, minus no put volume"
    assert scoped_busiest == 455, (
        "the whole board answering a back-month question — the scope never "
        "reached the read or never reached the cache key"
    )


def test_the_gex_timeline_carries_its_headline_totals(temp_db):
    """The strip above the chart is computed from the lines returned, so it
    travels WITH them rather than being recomputed by the browser — which is
    how the two come to disagree by a number nobody can account for."""
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    body = _gex_timeline(client, session)
    totals, rows = body["totals"], body["rows"]

    assert set(totals) == {"now", "at_open", "change", "levels"}
    assert totals["change"] == pytest.approx(totals["now"] - totals["at_open"])

    # THE CROSS-CHECK IS THE WHOLE TEST. Without it this asserts only that the
    # strip agrees with ITSELF — which it does when computed from any frame at
    # all, including one row of it, so a totals block built from the wrong
    # data passed. Summing the rows on the wire is the claim that matters:
    # the number above the chart is the number in the chart.
    assert rows, "the seed has snapshots; an empty chart proves nothing here"
    last = max(r["timestamp"] for r in rows)
    first = min(r["timestamp"] for r in rows)
    assert totals["now"] == pytest.approx(
        sum(r["net_gex"] for r in rows if r["timestamp"] == last))
    assert totals["at_open"] == pytest.approx(
        sum(r["net_gex"] for r in rows if r["timestamp"] == first))
    assert totals["levels"] == sorted({r["strike"] for r in rows})


def test_an_empty_session_gives_null_totals_and_not_zero(temp_db):
    """A headline reading "$0" claims the board is flat. Null says nobody has
    looked, which is the truth before the first snapshot of the day."""
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    body = _gex_timeline(client, "2026-01-02")

    assert body["rows"] == []
    assert body["totals"]["now"] is None
    assert body["totals"]["change"] is None
    assert session  # the seeded day exists; this asked about a different one


def test_dte_max_reaches_the_gex_timeline_read_and_its_cache_key(temp_db):
    """`dte_max=0` is what makes this the 0DTE panel rather than the board.
    THE BOARD GOES FIRST, so a key missing `dte_max` serves every expiry's
    gamma under the 0DTE heading — the exact mislabelling db.py's own
    docstring warns about."""
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    board = _gex_timeline(client, session)["totals"]["now"]
    zero_dte = _gex_timeline(client, session, dte_max=0)["totals"]["now"]

    # THE RATIO IS ONLY A TEST IF THE FIGURES ARE NOT ZERO. They were: the
    # seed gave calls and puts identical open interest, every strike netted to
    # nothing, and the assertion below read `0 == 0 * 2/3` — true against any
    # implementation. See _busy_board_seed.
    assert board != 0
    assert zero_dte == pytest.approx(board * 2 / 3), (
        "the seed's back month carries half the front month's gamma, so the "
        "0DTE slice is two thirds of the board — an equal figure means "
        "`dte_max` never reached the read or the cache key"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dealer structure and positioning. The arithmetic, the bands and every
# verdict boundary are core/dealer.py's and are tested there. What only this
# layer can get wrong: whether the SPOT comes from the snapshot being read,
# whether `expiry` scopes BOTH sides of the open-interest comparison, and
# whether either reaches the cache key.
# ─────────────────────────────────────────────────────────────────────────────

def _bubbles(client: TestClient, **params) -> dict:
    reply = client.get("/dealer/bubbles", params=params)
    assert reply.status_code == 200, reply.text
    return reply.json()


def _positioning(client: TestClient, **params) -> dict:
    reply = client.get("/dealer/positioning", params=params)
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_the_bubbles_are_priced_against_THEIR_OWN_snapshots_spot(temp_db):
    """The band is a distance from spot, so the wrong spot moves the whole
    window. The seed's two snapshots sit at 6000 and 6010; asking for the
    earlier one must not be answered with the later one's price."""
    from test_db import _gex_seed

    _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    assert _bubbles(client)["spot"] == 6010.0, (
        "the default is the newest snapshot, whose spot is 6010 — 6000 means "
        "the price came from a different snapshot than the chain did"
    )


def test_an_explicit_snapshot_gets_that_snapshots_spot(temp_db):
    from test_db import _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    body = client.get("/chain").json()
    newest = body["snapshot_id"]

    older = _bubbles(client, snapshot_id=newest - 1)
    assert older["spot"] == 6000.0
    assert older["snapshot_id"] == newest - 1
    assert session


def test_TRIM_changes_the_answer_and_is_in_the_cache_key(temp_db):
    """ONE CLIENT, trimmed first, so a key missing `trim` serves the trimmed
    board under the untrimmed question. The two differ only when the board is
    wide enough to trim, so this seeds more strikes than the shared seed."""
    import db as database
    from test_db import FRONT, add_snapshot, opt

    day = "2026-09-04"
    sid = add_snapshot(temp_db, f"{day} 14:00:00", spx=6000.0)
    rows = []
    for i in range(12):
        leg = opt(sid, FRONT, 5950.0 + i * 10, "C", dte=0)
        leg["volume"] = 100 + i
        rows.append(leg)
    database.insert_option_rows(temp_db, rows)

    client = TestClient(create_app(db_path=temp_db))

    trimmed = _bubbles(client, trim=True)["count"]
    whole = _bubbles(client, trim=False)["count"]

    assert trimmed < whole, (
        "equal counts mean either the trim never ran or `trim` is missing "
        "from the cache key"
    )


def test_the_bubbles_response_says_what_notional_is(temp_db):
    from test_db import _gex_seed

    _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    body = _bubbles(client)
    assert "mark x volume x 100" in body["basis"]
    assert body["band_percent"] > 0


def test_positioning_says_the_change_is_YESTERDAYS(temp_db):
    """The single most misreadable number on the panel. Today's trading is
    not in tonight's open interest yet, and the payload has to say so — a
    caller labelling this "today's churn" is making a claim the data cannot
    support."""
    from test_db import _gex_seed

    _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    basis = _positioning(client)["basis"]
    assert "PRIOR session" in basis
    assert "context, not evidence" in basis


def test_positioning_without_a_prior_session_is_BLANK_and_not_zero(temp_db):
    """The first collected day. Reporting "unknown" as no change would call
    the whole board churn — a verdict on every strike, from no evidence."""
    import db as database
    from test_db import FRONT, add_snapshot, opt

    day = "2026-09-04"
    sid = add_snapshot(temp_db, f"{day} 14:00:00", spx=6000.0)
    database.insert_option_rows(temp_db, [
        opt(sid, FRONT, 6000.0, "C", dte=0),
        opt(sid, FRONT, 6000.0, "P", dte=0),
    ])

    client = TestClient(create_app(db_path=temp_db))
    rows = _positioning(client, session_date=day)["rows"]

    assert rows, "the strike is in range and traded, so it must be listed"
    assert all(r["delta_oi"] is None for r in rows)
    assert all(r["verdict"] in (None, "", "—") for r in rows)


def test_the_positioning_expiry_scopes_BOTH_sides_and_is_in_the_cache_key(temp_db):
    """All-expiry open interest minus one expiry's would report the rest of
    the board as an overnight liquidation. THE UNSCOPED CALL GOES FIRST, so a
    key missing `expiry` serves the board's answer under one contract's
    label."""
    from test_db import BACK, _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    whole = _positioning(client, session_date=session)
    scoped = _positioning(client, session_date=session, expiry=BACK)

    def volume_at(body, strike):
        hit = [r for r in body["rows"] if r["strike"] == strike]
        return hit[0]["total_volume"] if hit else None

    from test_db import CALL_STRIKE
    assert volume_at(whole, CALL_STRIKE) != volume_at(scoped, CALL_STRIKE), (
        "the same figure under both scopes means `expiry` never reached the "
        "read or never reached the cache key"
    )

    # AND THE OPEN-INTEREST SIDE SEPARATELY, because the assertion above
    # cannot see it: `total_volume` comes from TODAY's frame alone, so an
    # endpoint that scoped today correctly and loaded the WHOLE BOARD's prior
    # open interest passed it — which is precisely the failure the endpoint's
    # own docstring warns about, the rest of the board reported as an
    # overnight liquidation.
    #
    # The seed's prior session lists the FRONT month only. Scoped to BACK
    # there is therefore nothing to difference against and every delta must be
    # blank; a number here means the front month's open interest was served
    # under the back month's name.
    def deltas(body, strike):
        return [r["delta_oi"] for r in body["rows"] if r["strike"] == strike]

    assert deltas(scoped, CALL_STRIKE) == [None], (
        "the prior session holds no back-month rows, so this must be blank — "
        "a figure means `expiry` never reached load_prior_session_oi"
    )
    assert deltas(whole, CALL_STRIKE) != [None], (
        "unscoped, the front month's prior open interest IS there to "
        "difference against, so a blank here means the seed changed"
    )


def test_positioning_names_the_days_and_marks_the_atm_strike(temp_db):
    """Three things the browser must NOT decide for itself.

    The day labels are a clock comparison against market holidays in market
    time; run in a browser they would resolve in the VIEWER's timezone, which
    is DEBT-030 exactly. "The strike nearest spot" is a rule, small but real.
    Both belong on the wire.
    """
    from test_db import _gex_seed

    session = _gex_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    body = _positioning(client, session_date=session)

    assert body["day_labels"]["prior"] == "Yesterday", (
        "the finished session never needs qualifying"
    )
    assert body["day_labels"]["current"] in {"Live", "Today"}, (
        "today's volume is 'Live' while it is still being written and "
        "'Today' once it is final — the word tracks the market"
    )
    assert isinstance(body["market_open"], bool)

    # The seed's spot is 6010; the nearest listed strike must win.
    strikes = [r["strike"] for r in body["rows"]]
    nearest = min(strikes, key=lambda k: abs(k - body["spot"]))
    assert body["atm_strike"] == nearest


def test_an_empty_positioning_board_has_no_atm_strike(temp_db):
    """Null, not the first row and not 0 — there is no nearest strike when
    there are no strikes."""
    import db as database
    from test_db import FRONT, add_snapshot, opt

    day = "2026-09-04"
    sid = add_snapshot(temp_db, f"{day} 14:00:00", spx=6000.0)
    # Listed far outside the 2.5% band, so the board comes back empty.
    database.insert_option_rows(temp_db, [
        opt(sid, FRONT, 9000.0, "C", dte=0),
    ])

    client = TestClient(create_app(db_path=temp_db))
    body = _positioning(client, session_date=day)

    assert body["rows"] == []
    assert body["atm_strike"] is None


# ─────────────────────────────────────────────────────────────────────────────
# The session-range wicks, on every measure (Chandan, 2026-09-06). The ranges
# themselves are pinned in tests/test_ranges.py and proved by mutation; what
# only this layer can get wrong is whether `measure` reaches the computation
# AND the cache key, and whether an unknown one is refused.
# ─────────────────────────────────────────────────────────────────────────────

def _range(client: TestClient, session: str, **params) -> dict:
    reply = client.get("/strikes/session-range",
                       params={"session_date": session, **params})
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_every_measure_has_wicks_and_says_which_one_it_drew(temp_db):
    """The request itself: the same wick on all six panels, not gamma alone."""
    from core import ranges as core_ranges

    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    for measure in core_ranges.MEASURES:
        body = _range(client, session, measure=measure)
        assert body["measure"] == measure
        assert body["rows"], measure


def test_TWO_MEASURES_asked_of_ONE_CLIENT_do_not_share_an_answer(temp_db):
    """THE CACHE-KEY TRAP. Every other test here builds a fresh client, and a
    fresh client has an empty cache — so a key missing `measure` passes the
    whole suite and fails only in the browser, where the first panel to load
    poisons the other four. These two requests deliberately share one client,
    and the broad question is asked FIRST so the specific one cannot be the
    thing that happens to be cached.
    """
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    gamma = _range(client, session, measure="gamma")["rows"]
    delta = _range(client, session, measure="delta")["rows"]

    assert [r["call_high"] for r in gamma] != [r["call_high"] for r in delta], (
        "delta was served gamma's range — `measure` is missing from the "
        "cache key"
    )


def test_an_unknown_measure_is_REFUSED_rather_than_quietly_given_gamma(temp_db):
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    reply = client.get("/strikes/session-range",
                       params={"session_date": session, "measure": "theta"})

    assert reply.status_code == 422


def test_the_default_measure_is_still_gamma(temp_db):
    """LOAD-BEARING. The two GEX panels read this endpoint without a `measure`
    and predate the parameter; they must not have to learn it exists."""
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    plain = _range(client, session)
    named = _range(client, session, measure="gamma")

    assert plain["measure"] == "gamma"
    assert plain["rows"] == named["rows"]


def test_the_expiry_scope_still_reaches_the_range_and_its_cache_key(temp_db):
    """A whole-board range behind a single-expiry bar would put the bar inside
    a wick belonging to twenty other contracts. Asked of ONE client, broad
    question first, for the reason the measure test above gives."""
    from test_db import FRONT

    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    board = _range(client, session)["rows"]
    front = _range(client, session, expiry=FRONT)["rows"]

    assert [r["call_high"] for r in board] != [r["call_high"] for r in front]


def test_dte_max_reaches_the_session_load_behind_the_wicks(temp_db):
    """The 0DTE panels scope by days-to-expiry rather than by key, and the
    bound is applied in SQL — before the session frame is built — so a request
    that dropped it would draw the whole board's range behind a 0DTE bar.

    ONE CLIENT, WHOLE BOARD FIRST, for the reason the measure test gives.
    """
    session = _busy_board_seed(temp_db)
    client = TestClient(create_app(db_path=temp_db))

    board = _range(client, session)["rows"]
    zero = _range(client, session, dte_max=0)["rows"]

    assert [r["call_high"] for r in board] != [r["call_high"] for r in zero], (
        "the 0DTE range equals the whole board's — `dte_max` never reached "
        "the read"
    )
