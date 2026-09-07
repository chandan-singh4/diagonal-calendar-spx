"""The data service's one new write: entry locks (ADR-054).

EVERY TEST HERE POINTS THE SERVER AT A TEMPORARY DIRECTORY. That is not
politeness — `entry_locks.json` in the project root is live state the
collector reads to choose which strikes it fetches (collector.py:649), and a
test that wrote it would change what tomorrow's record contains. `state_dir`
exists on `create_app` so this is possible; a route that reached for
`config.STATE_DIR` itself would defeat every fixture below, so one test pins
that too.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from state import entry_locks

COMBO = {
    "front_expiry": "2026-09-18",
    "back_expiry": "2026-09-25",
    "put_strike": 6400.0,
    "call_strike": 6500.0,
}


@pytest.fixture
def served(tmp_path, temp_db):
    """A client whose locks live in tmp_path, and the directory itself."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    client = TestClient(create_app(db_path=temp_db, state_dir=str(state_dir)))
    return client, state_dir


def _post(client, **overrides):
    body = {**COMBO, "diagonal_mark": 3.25, "mode": "monitor_only", **overrides}
    return client.post("/locks", json=body)


class TestCreating:

    def test_a_lock_reaches_the_file_the_collector_reads(self, served):
        """The whole point: not a response, a file on disk. The collector
        never sees this HTTP call — it reads entry_locks.json."""
        client, state_dir = served
        assert _post(client).status_code == 201

        stored = json.loads((state_dir / "entry_locks.json").read_text())
        assert len(stored) == 1
        record = next(iter(stored.values()))
        assert record["entry_diagonal_mark"] == 3.25
        assert record["put_strike"] == 6400.0
        assert record["front_expiry"] == "2026-09-18"

    def test_the_key_served_back_is_the_one_the_lock_is_filed_under(self, served):
        """A client that rebuilt the key itself would eventually address a
        lock under a name the file does not use."""
        client, state_dir = served
        served_key = _post(client).json()["key"]

        stored = json.loads((state_dir / "entry_locks.json").read_text())
        assert served_key in stored

    def test_monitor_and_log_creates_no_trade(self, served):
        """The Journal is out of scope (Chandan, 2026-09-07). The mode is
        recorded as an intention and journal_trade_id stays null."""
        client, _ = served
        body = _post(client, mode="monitor_and_log").json()
        assert body["mode"] == "monitor_and_log"
        assert body["journal_trade_id"] is None

    def test_an_invented_mode_is_refused(self, served):
        client, state_dir = served
        assert _post(client, mode="monitor_and_tweet").status_code == 422
        assert not (state_dir / "entry_locks.json").exists()

    def test_locking_twice_does_not_overwrite_when_the_position_was_taken(self, served):
        """A double-tapped button must not destroy locked_at. The second
        POST is refused and the first record stands untouched."""
        client, _ = served
        first = _post(client).json()

        second = _post(client, diagonal_mark=9.99)
        assert second.status_code == 409

        live = client.get("/locks").json()["locks"]
        assert len(live) == 1
        assert live[0]["lock_id"] == first["lock_id"]
        assert live[0]["locked_at"] == first["locked_at"]
        assert live[0]["entry_diagonal_mark"] == 3.25


class TestListingAndClearing:

    def test_nothing_locked_is_an_empty_list_not_an_error(self, served):
        client, _ = served
        assert client.get("/locks").json() == {"locks": [], "count": 0}

    def test_the_newest_position_is_listed_first(self, served):
        client, _ = served
        _post(client)
        _post(client, put_strike=6300.0, call_strike=6600.0)

        listed = client.get("/locks").json()["locks"]
        assert [lock["put_strike"] for lock in listed] == [6300.0, 6400.0]

    def test_clearing_removes_it_from_the_file(self, served):
        client, state_dir = served
        _post(client)

        body = client.request("DELETE", "/locks", params=COMBO).json()
        assert body["cleared"] is True
        assert json.loads((state_dir / "entry_locks.json").read_text()) == {}

    def test_a_repeated_delete_is_not_an_error(self, served):
        """The new screen retries. A retry landing after the first attempt
        succeeded must not report a failure for work that was done."""
        client, _ = served
        _post(client)
        client.request("DELETE", "/locks", params=COMBO)

        again = client.request("DELETE", "/locks", params=COMBO)
        assert again.status_code == 200
        assert again.json()["cleared"] is False

    def test_a_lock_whose_front_leg_expired_is_gone_from_every_answer(self, served):
        """BUG-021/ADR-039: purging only the list would tidy the display
        while create and delete carried on seeing the dead lock."""
        client, state_dir = served
        entry_locks.create(str(state_dir), "2020-01-17", "2020-01-24",
                           6400.0, 6500.0, diagonal_mark=1.0,
                           mode="monitor_only", display_tz="America/New_York")

        assert client.get("/locks").json()["count"] == 0
        # And the slot is free again -- not blocked by a 409 from a corpse.
        assert client.post("/locks", json={
            **COMBO, "front_expiry": "2020-01-17", "back_expiry": "2020-01-24",
            "diagonal_mark": 2.0, "mode": "monitor_only"}).status_code == 201


class TestTheBoundary:

    def test_the_write_never_touches_the_real_state_directory(self, served, tmp_path):
        """The route must use the directory create_app was given. If it read
        config.STATE_DIR instead, every test above would be writing the live
        file the collector reads and would still pass."""
        client, state_dir = served
        _post(client)
        assert (state_dir / "entry_locks.json").exists()

        import config
        assert str(config.STATE_DIR) != str(state_dir)

    def test_the_database_is_untouched_by_a_lock(self, served, temp_db):
        """ADR-054 narrowed ADR-052 by exactly one sidecar. The database
        stays read-only to this package."""
        import os
        before = os.path.getmtime(temp_db)
        client, _ = served
        _post(client)
        assert os.path.getmtime(temp_db) == before

    def test_writing_a_lock_requires_the_token(self, served, monkeypatch):
        """The one write must not be the one route that skips auth."""
        monkeypatch.setenv("SPX_API_TOKEN", "secret")
        client, state_dir = served
        assert _post(client).status_code == 401
        assert not (state_dir / "entry_locks.json").exists()
