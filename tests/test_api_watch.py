"""M4.4 — the push, and the ways a fan-out quietly stops working.

NO TEST HERE SLEEPS AND HOPES. `poll_once` is separated from the polling loop
precisely so a check can drive one tick deterministically; a test that waits
on a timer passes on a fast machine and fails on a busy one, and the usual
repair is a longer sleep, which makes the suite slower without making it more
honest.

WHAT IS AT RISK IN A FAN-OUT, and so what is checked below:

  * announcing the same snapshot twice — a client that alerts on every
    message would cry wolf every five seconds
  * one dead client blocking the others — the failure that makes a push
    system silently stop pushing
  * the client set growing forever as clients come and go
  * the first message being indistinguishable from a change, so a phone
    connecting on a Saturday reports Friday's close as breaking news
"""
from __future__ import annotations

import asyncio
import sqlite3

from api.watch import SnapshotWatcher


# Driven with asyncio.run rather than pytest-asyncio. The coroutines below are
# short and deterministic, and this keeps the suite's dependency list where it
# is — every bound in pyproject.toml is deliberate, and a plugin added for
# eight tests is a plugin to keep pinned forever.
def _run(coro):
    return asyncio.run(coro)


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


class FakeSocket:
    """A websocket that records what it was told."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail
        from starlette.websockets import WebSocketState
        self.client_state = WebSocketState.CONNECTED

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise ConnectionResetError("client went away")
        self.sent.append(message)


def test_a_new_snapshot_is_announced_once(temp_db):
    _run(_test_a_new_snapshot_is_announced_once(temp_db))


async def _test_a_new_snapshot_is_announced_once(temp_db):
    watcher = SnapshotWatcher(temp_db)
    client = FakeSocket()
    await watcher.add(client)

    # The first poll primes: whatever is in the record when the watcher starts
    # is the baseline, not an event. Here the record is empty, so the baseline
    # is "nothing" and the snapshot below is genuinely new.
    await watcher.poll_once()

    _snapshot(temp_db, 1, "2026-09-04 20:01:00")
    assert await watcher.poll_once() is not None
    assert await watcher.poll_once() is None, "the same snapshot is not news twice"

    assert len(client.sent) == 1
    assert client.sent[0]["event"] == "snapshot"
    assert client.sent[0]["snapshot_id"] == 1


def test_each_new_snapshot_is_announced(temp_db):
    _run(_test_each_new_snapshot_is_announced(temp_db))


async def _test_each_new_snapshot_is_announced(temp_db):
    watcher = SnapshotWatcher(temp_db)
    client = FakeSocket()
    await watcher.add(client)
    await watcher.poll_once()                      # prime on an empty record

    _snapshot(temp_db, 1, "2026-09-04 20:00:00")
    await watcher.poll_once()
    _snapshot(temp_db, 2, "2026-09-04 20:01:00")
    await watcher.poll_once()

    assert [m["snapshot_id"] for m in client.sent] == [1, 2]


def test_an_incomplete_snapshot_is_not_announced(temp_db):
    _run(_test_an_incomplete_snapshot_is_not_announced(temp_db))


async def _test_an_incomplete_snapshot_is_not_announced(temp_db):
    """A half-written snapshot is a row that exists and data that does not.
    Announcing it would send every client to fetch a chain that is still
    being written."""
    watcher = SnapshotWatcher(temp_db)
    client = FakeSocket()
    await watcher.add(client)
    await watcher.poll_once()                      # prime

    _snapshot(temp_db, 1, "2026-09-04 20:01:00", status="PARTIAL")
    assert await watcher.poll_once() is None
    assert client.sent == []


def test_the_announcement_carries_a_zoned_timestamp(temp_db):
    _run(_test_the_announcement_carries_a_zoned_timestamp(temp_db))


async def _test_the_announcement_carries_a_zoned_timestamp(temp_db):
    watcher = SnapshotWatcher(temp_db)
    client = FakeSocket()
    await watcher.add(client)
    await watcher.poll_once()                      # prime
    _snapshot(temp_db, 1, "2026-09-04 20:01:00")

    await watcher.poll_once()

    assert client.sent[0]["timestamp_utc"].endswith("+00:00")


def test_one_dead_client_does_not_stop_the_others(temp_db):
    _run(_test_one_dead_client_does_not_stop_the_others(temp_db))


async def _test_one_dead_client_does_not_stop_the_others(temp_db):
    """The failure that makes a push system silently stop pushing."""
    watcher = SnapshotWatcher(temp_db)
    dead, alive = FakeSocket(fail=True), FakeSocket()
    await watcher.add(dead)
    await watcher.add(alive)
    await watcher.poll_once()                      # prime
    _snapshot(temp_db, 1, "2026-09-04 20:01:00")

    await watcher.poll_once()

    assert len(alive.sent) == 1, "the live client must still be told"


def test_a_dead_client_is_dropped(temp_db):
    _run(_test_a_dead_client_is_dropped(temp_db))


async def _test_a_dead_client_is_dropped(temp_db):
    watcher = SnapshotWatcher(temp_db)
    await watcher.add(FakeSocket(fail=True))
    await watcher.poll_once()                      # prime
    _snapshot(temp_db, 1, "2026-09-04 20:00:00")

    await watcher.poll_once()

    assert watcher.client_count() == 0, (
        "a set that only ever grows is a leak in a long-running server"
    )


def test_removing_a_client_stops_its_messages(temp_db):
    _run(_test_removing_a_client_stops_its_messages(temp_db))


async def _test_removing_a_client_stops_its_messages(temp_db):
    watcher = SnapshotWatcher(temp_db)
    client = FakeSocket()
    await watcher.add(client)
    await watcher.poll_once()                      # prime
    await watcher.remove(client)

    _snapshot(temp_db, 1, "2026-09-04 20:01:00")
    await watcher.poll_once()

    assert client.sent == []


def test_an_empty_record_announces_nothing(temp_db):
    _run(_test_an_empty_record_announces_nothing(temp_db))


async def _test_an_empty_record_announces_nothing(temp_db):
    watcher = SnapshotWatcher(temp_db)
    client = FakeSocket()
    await watcher.add(client)

    assert await watcher.poll_once() is None
    assert client.sent == []


def test_the_loop_survives_a_failing_poll(temp_db):
    _run(_test_the_loop_survives_a_failing_poll(temp_db))


async def _test_the_loop_survives_a_failing_poll(temp_db):
    """A watcher that dies on one bad read stops notifying and says nothing
    about it — silence that looks exactly like a quiet market."""
    watcher = SnapshotWatcher(temp_db, poll_seconds=0.01)
    calls = []

    def explode():
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")

    watcher._read_latest = explode
    await watcher.start()
    await asyncio.sleep(0.08)
    await watcher.stop()

    assert len(calls) > 1, "the loop must keep trying after a failed read"


def test_the_socket_endpoint_sends_the_current_snapshot_on_connect(temp_db):
    """Labelled `current`, not `snapshot`. A phone connecting on a Saturday
    must not report Friday's close as breaking news."""
    from fastapi.testclient import TestClient

    from api.app import create_app

    _snapshot(temp_db, 1, "2026-09-04 20:01:00")

    with TestClient(create_app(db_path=temp_db)) as client, \
            client.websocket_connect("/ws/snapshot") as ws:
        first = ws.receive_json()

    assert first["event"] == "current"
    assert first["snapshot_id"] == 1


def test_health_reports_how_many_clients_are_listening(temp_db):
    from fastapi.testclient import TestClient

    from api.app import create_app

    with TestClient(create_app(db_path=temp_db)) as client:
        assert client.get("/health").json()["websocket_clients"] == 0


def test_a_restart_does_not_announce_what_was_already_there(temp_db):
    _run(_test_a_restart_does_not_announce_what_was_already_there(temp_db))


async def _test_a_restart_does_not_announce_what_was_already_there(temp_db):
    """The behaviour priming exists for.

    A server restarted on Monday morning finds Friday's close sitting in the
    record. That is where things stand, not something that just happened, and
    pushing it would alert every connected phone about week-old news.
    """
    _snapshot(temp_db, 6387, "2026-09-04 20:01:00")

    watcher = SnapshotWatcher(temp_db)          # as if freshly started
    client = FakeSocket()
    await watcher.add(client)

    assert await watcher.poll_once() is None
    assert client.sent == [], "the existing snapshot is the baseline"

    _snapshot(temp_db, 6388, "2026-09-08 13:31:00")
    await watcher.poll_once()

    assert [m["snapshot_id"] for m in client.sent] == [6388]
