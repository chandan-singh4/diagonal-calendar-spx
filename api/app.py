"""The server itself — the app factory and, for M4.1, one endpoint.

WHY A FACTORY AND NOT A MODULE-LEVEL `app`. `create_app(db_path=...)` takes
the database as an argument for the same reason every function in
`dataaccess/` does: so a check can point the whole server at a temporary file
without overwriting `config.DB_PATH`, which is a test modifying the thing it
is testing (DEBT-027). The module-level `app` at the bottom is the deployment
convenience for `uvicorn api.app:app`, and it is the ONLY place in this
package that reads the configured database.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

import config
import db
import schema
from api import auth, reads, watch
from api.cache import SnapshotCache

# The wire format for every timestamp this API emits. Stored timestamps are
# UTC and naive ("2026-09-04 20:01:00" is 16:01 New York, the settled close
# ADR-049 exists to capture). Naive UTC on the wire is how a client ends up
# rendering a 4-hour-old quote as current, so everything leaves here with an
# explicit offset and the field name says which zone it is in.
_STORED_FORMAT = "%Y-%m-%d %H:%M:%S"


def _as_utc(stamp: str | None) -> _dt.datetime | None:
    """Parse a stored timestamp as what it is: UTC, despite carrying no zone."""
    if not stamp:
        return None
    try:
        naive = _dt.datetime.strptime(stamp, _STORED_FORMAT)
    except ValueError:
        return None
    return naive.replace(tzinfo=_dt.UTC)


def create_app(db_path: str | None = None) -> FastAPI:
    """Build the server, bound to one database.

    `db_path` is resolved once, here, and closed over by the routes. This is
    the api/ layer doing its one job — deciding which database — so that
    nothing below it has to.
    """
    resolved = db_path or config.DB_PATH
    cache = SnapshotCache()
    watcher = watch.SnapshotWatcher(resolved)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """One poller for the whole process, started with the server.

        Started here rather than lazily on the first websocket connection: a
        watcher that only exists while someone is listening cannot tell a
        newly-connected client whether it missed anything, and the first
        client would always be told the current snapshot is "new".
        """
        await watcher.start()
        try:
            yield
        finally:
            await watcher.stop()

    app = FastAPI(
        lifespan=lifespan,
        title="SPX Diagonal Dashboard API",
        version="0.1.0",
        summary="Read-only access to the SPX option price history.",
        description=(
            "The historical record IS the product; this serves it without a "
            "page attached. Single-user by decision (ADR-012) — not a public "
            "API, and never exposed beyond localhost without the token and "
            "the tunnel that M4.5 adds."
        ),
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, Any]:
        """Can this process read the record, and how fresh is it?

        DELIBERATELY NOT A COLLECTOR LIVENESS CHECK. A stale snapshot is the
        normal and correct state every evening and all weekend, so a health
        endpoint that went red on staleness would be red more often than
        green and would teach exactly the habit that let 2,181 identical
        warnings go unread for eight weeks. Judging whether silence is a
        fault needs the market calendar and belongs to the watchdog, which
        already does it and which watches without acting (ADR-045).

        So this reports facts and draws one conclusion only: whether the
        database could be opened and read. `age_seconds` is published so a
        caller that DOES know the calendar can decide for itself.
        """
        now = _dt.datetime.now(_dt.UTC)
        payload: dict[str, Any] = {
            "status": "ok",
            "database": resolved,
            "server_time_utc": now.isoformat(),
        }

        try:
            with db.get_conn(resolved) as conn:
                payload["schema_version"] = schema.current_version(conn)
            latest = db.get_latest_complete_snapshot(resolved)
        except sqlite3.Error as exc:
            # The one thing this endpoint is actually entitled to fail on.
            # Reported rather than raised: a 200 saying "I cannot read the
            # database" is more useful to a phone than a stack trace, and the
            # status field is what a caller should be reading anyway.
            payload["status"] = "unavailable"
            payload["error"] = f"{type(exc).__name__}: {exc}"
            return payload

        stamp = _as_utc(latest["snapshot_timestamp"]) if latest else None
        payload["latest_snapshot"] = (
            None if latest is None else {
                "snapshot_id": latest["snapshot_id"],
                "timestamp_utc": stamp.isoformat() if stamp else None,
                "age_seconds": (
                    None if stamp is None else round((now - stamp).total_seconds())
                ),
                "underlying_price": latest["underlying_price"],
            }
        )
        payload["cache"] = cache.stats()
        payload["websocket_clients"] = watcher.client_count()
        return payload

    @app.middleware("http")
    async def require_token(request, call_next):
        """Every route, not a decorator per route (M4.5).

        A per-route dependency protects the routes someone remembered to
        decorate. A new endpoint added later would be open by default, and
        nothing would say so — the failure is invisible until it matters.
        """
        try:
            auth.check(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code,
                                content={"detail": exc.detail})
        return await call_next(request)

    ctx = reads.ReadContext(resolved, cache)
    app.include_router(reads.build_router(ctx))
    app.include_router(reads.build_computed_router(ctx))
    app.include_router(watch.build_router(watcher))
    app.state.watcher = watcher
    app.state.cache = cache
    return app


# For `uvicorn api.app:app`. The only config.DB_PATH read in the package —
# every other route reaches the database through the factory's argument.
app = create_app()
