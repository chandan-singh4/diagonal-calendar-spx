"""M4.1 — the server stands up, and /health tells the truth about the record.

NONE OF THIS TOUCHES THE REAL DATABASE. Every test builds the app with
`create_app(db_path=temp_db)`, which is the whole reason the factory takes an
argument: pointing a server at a test file must not mean overwriting
config.DB_PATH (DEBT-027).

WHAT IS WORTH CHECKING HERE, given there is one endpoint. Not "does FastAPI
route a GET" — that is FastAPI's test suite, not ours. What matters is the
three things this project has actually been bitten by:

  * a timestamp leaving the system without its zone (stored stamps are naive
    UTC; read as local they are four hours wrong, and 20:01 becomes an
    evening quote instead of the settled close);
  * an empty result rendering as a number rather than as absence (missing
    price → blank, never 0);
  * a health check that conflates "the collector is quiet" with "something is
    broken", which on any weekend is a false alarm by construction.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import config
from api.app import create_app


@pytest.fixture
def client(temp_db) -> TestClient:
    return TestClient(create_app(db_path=temp_db))


def _insert_snapshot(db_path: str, snapshot_id: int, stamp: str,
                     price: float = 6000.0, status: str = "COMPLETE") -> None:
    """One snapshot row, written directly.

    Deliberately not via db.create_snapshot(): this is arranging a fixed,
    known timestamp, and the production writer supplies its own clock.
    """
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO snapshots (snapshot_id, snapshot_timestamp, status, "
            "underlying_price) VALUES (?, ?, ?, ?)",
            (snapshot_id, stamp, status, price),
        )
    conn.close()


def test_health_reports_ok_on_an_empty_database(client: TestClient):
    """An empty record is not a broken one — the collector may never have run."""
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["latest_snapshot"] is None, (
        "No snapshots must read as absence. A zero or an epoch here would be "
        "a number the record does not contain."
    )


def test_health_reports_the_latest_snapshot(client: TestClient, temp_db):
    _insert_snapshot(temp_db, 1, "2026-09-04 19:59:00", price=7700.0)
    _insert_snapshot(temp_db, 2, "2026-09-04 20:01:00", price=7718.36)

    latest = client.get("/health").json()["latest_snapshot"]

    assert latest["snapshot_id"] == 2
    assert latest["underlying_price"] == 7718.36


def test_the_timestamp_leaves_with_its_zone_attached(client: TestClient, temp_db):
    """Stored stamps are naive UTC. Unmarked, a client renders 20:01 as 20:01
    local — the settled close (ADR-049) turning into an evening quote four
    hours after the market shut."""
    _insert_snapshot(temp_db, 1, "2026-09-04 20:01:00")

    stamp = client.get("/health").json()["latest_snapshot"]["timestamp_utc"]

    assert stamp is not None
    assert stamp.startswith("2026-09-04T20:01:00")
    assert stamp.endswith("+00:00"), (
        f"{stamp!r} carries no UTC offset — the field name says utc and the "
        f"value must say so too"
    )


def test_an_incomplete_snapshot_is_not_the_latest(client: TestClient, temp_db):
    """A half-written snapshot is a row that exists and data that does not."""
    _insert_snapshot(temp_db, 1, "2026-09-04 20:00:00", price=7718.36)
    _insert_snapshot(temp_db, 2, "2026-09-04 20:01:00", price=0.0, status="PARTIAL")

    latest = client.get("/health").json()["latest_snapshot"]

    assert latest["snapshot_id"] == 1


def test_staleness_is_published_not_judged(client: TestClient, temp_db):
    """Every weekend the newest snapshot is two days old and nothing is wrong.

    A health check that went red on that would be red more often than green.
    It publishes age_seconds and leaves the verdict to the watchdog, which
    has the market calendar and watches without acting (ADR-045).
    """
    _insert_snapshot(temp_db, 1, "2020-01-01 15:00:00")

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["latest_snapshot"]["age_seconds"] > 60 * 60 * 24 * 365


def test_health_reports_the_schema_version(client: TestClient):
    """`schema_version` said 1 through ten applied changes once already
    (ADR-051). Publishing it is how a client can tell what shape it is
    reading rather than assuming."""
    assert client.get("/health").json()["schema_version"] >= 1


def test_an_unreadable_database_is_reported_not_raised(tmp_path):
    """A phone gets a sentence it can display, not a stack trace."""
    broken = tmp_path / "not-a-database.db"
    broken.write_text("this is not an SQLite file", encoding="utf-8")

    body = TestClient(create_app(db_path=str(broken))).get("/health").json()

    assert body["status"] == "unavailable"
    assert "error" in body


def test_the_factory_does_not_read_the_configured_database(temp_db):
    """The point of the argument (DEBT-027): a test server must be aimable
    without overwriting the global that names the 3.7 GB production file."""
    body = TestClient(create_app(db_path=temp_db)).get("/health").json()

    assert body["database"] == temp_db
    assert body["database"] != config.DB_PATH
