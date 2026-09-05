"""M4.4 (ENH-002) — tell clients when a snapshot lands, instead of being asked.

WHAT THIS HONESTLY IS. Still polling — but ONE poller in this process instead
of one per client, which is the change ENH-002 is actually asking for. A phone
refreshing every five seconds asks the database every five seconds; ten phones
ask fifty times. Here the server asks once and fans the answer out.

WHY NOT LISTEN TO THE COLLECTOR DIRECTLY. It is a separate process writing to
the same SQLite file, and SQLite has no change notification worth relying on
across processes. Coupling the two — a socket, a signal file, an import —
would make the collector's success depend on the API being up, and the
collector is the one thing in this project that must never be made more
fragile: a missed minute cannot be re-fetched at any price. So the API watches
the file and the collector remains unaware it exists.

WHAT IS WATCHED IS `max(snapshot_id)` WHERE STATUS IS COMPLETE — the same
value api/cache.py keys on, deliberately. If the push said "new data" at a
different moment from when the cache invalidated, a client would fetch on the
notification and get the previous snapshot's answer from the cache. One
definition of "current", used by both.

THE POLL IS ONE INDEXED ROW. It runs in a thread so a blocking SQLite read
never sits on the event loop; that is the same reason the routes are `def`.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

import db

logger = logging.getLogger(__name__)

# The collector writes every 1–5 minutes. Five seconds is well inside that, so
# a client learns of a snapshot within a few seconds of it landing, while the
# cost is one indexed lookup — cheaper than a single client's own polling.
DEFAULT_POLL_SECONDS = 5.0


class SnapshotWatcher:
    """Watches for a new snapshot and fans the news out to connected clients."""

    def __init__(self, db_path: str,
                 poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
        self.db_path = db_path
        self.poll_seconds = poll_seconds
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_seen: int | None = None
        self._primed = False

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # ── clients ────────────────────────────────────────────────────────────

    async def add(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.add(websocket)

    async def remove(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    def client_count(self) -> int:
        return len(self._clients)

    # ── the poll ───────────────────────────────────────────────────────────

    def _read_latest(self) -> dict[str, Any] | None:
        """One row. Called in a worker thread — SQLite blocks."""
        row = db.get_latest_complete_snapshot(self.db_path)
        if row is None:
            return None
        return {
            "snapshot_id": row["snapshot_id"],
            "timestamp_utc": _iso_utc(row["snapshot_timestamp"]),
            "underlying_price": row["underlying_price"],
        }

    async def poll_once(self) -> dict[str, Any] | None:
        """Check for a new snapshot; broadcast and return it if there is one.

        Separated from the loop so a check can drive one tick deterministically
        rather than sleeping and hoping — a test that waits on a timer is a
        test that fails on a slow machine and passes on a fast one.

        THE FIRST SUCCESSFUL READ PRIMES AND ANNOUNCES NOTHING. Whatever is
        already in the record when this server starts is the baseline, not an
        event: without this, every restart would push the existing snapshot as
        though it had just landed, so a Monday morning restart would announce
        Friday's close as news. A client that wants to know where things
        stand gets the `current` message on connect, which is labelled
        differently for exactly this reason.

        Priming happens HERE rather than in `start()` so that a database
        momentarily locked by the collector defers it to the next tick instead
        of failing the server's startup. The collector holds the write lock
        several times a minute; refusing to boot during one of those would be
        a server that starts reliably only when nothing is happening.
        """
        latest = await asyncio.to_thread(self._read_latest)
        if not self._primed:
            # Primed even when the record is EMPTY, with a baseline of None.
            # Otherwise a server started before the very first collection of a
            # day would treat that day's first snapshot as its baseline and
            # swallow it — the one snapshot most worth announcing.
            self._primed = True
            self._last_seen = latest["snapshot_id"] if latest else None
            return None
        if latest is None or latest["snapshot_id"] == self._last_seen:
            return None
        self._last_seen = latest["snapshot_id"]
        await self.broadcast({"event": "snapshot", **latest})
        return latest

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A watcher that dies on one bad read stops notifying and says
                # nothing about it — silence indistinguishable from a quiet
                # market. Log and keep going; the next tick may well succeed.
                logger.exception("snapshot watcher: poll failed, continuing")
            await asyncio.sleep(self.poll_seconds)

    # ── fan-out ────────────────────────────────────────────────────────────

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._clients)

        dead = []
        for ws in targets:
            try:
                if ws.client_state is not WebSocketState.CONNECTED:
                    dead.append(ws)
                    continue
                await ws.send_json(message)
            except Exception:
                # A client that has gone away must not stop the others being
                # told. Collected and dropped after the loop rather than
                # during it, so the set is not mutated while iterating.
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


def _iso_utc(stamp: str | None) -> str | None:
    """Stored stamps are naive UTC; they leave here with the offset on."""
    if not stamp:
        return None
    try:
        naive = _dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=_dt.UTC).isoformat()


def build_router(watcher: SnapshotWatcher) -> APIRouter:
    router = APIRouter(tags=["push"])

    @router.websocket("/ws/snapshot")
    async def snapshot_socket(websocket: WebSocket) -> None:
        """Push a message each time a new snapshot completes.

        The first message is sent on connect and carries the CURRENT snapshot,
        labelled `event: "current"` rather than `"snapshot"`. Without it a
        client has no idea what it is looking at until the next write, which
        on a Saturday is Monday morning; distinguishing the two means a client
        can tell "here is where we are" from "something just changed" and not
        alert on the former.
        """
        await websocket.accept()
        await watcher.add(websocket)
        try:
            current = await asyncio.to_thread(watcher._read_latest)
            await websocket.send_json(
                {"event": "current", **(current or {"snapshot_id": None})})
            while True:
                # Nothing is expected from the client; this waits for the
                # disconnect. Without a receive the server never notices a
                # client that has gone, and the set grows forever.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await watcher.remove(websocket)

    return router
