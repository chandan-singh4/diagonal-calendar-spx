"""The reads, served. One endpoint per function in `dataaccess/queries.py`.

THIS MODULE ADDS NO QUERIES OF ITS OWN. Every route below calls exactly one
`dataaccess` function and hands the frame to `serialize`. That is deliberate:
the moment an endpoint grows its own SQL, the read layer stops being the one
place the price history is read from, and the next person looking for "every
query in the system" finds two places and trusts the wrong one.

THE SNAPSHOT DEFAULT. Several reads take a `session_date`, and the honest
default is not "today" — on a Saturday there is no today, and at 02:00 the
last session was yesterday. The default is the session of the newest snapshot
in the record, converted to market time. A server that answered with an empty
frame every weekend because it asked for the wrong date would be technically
correct and useless.

WHY THE ROUTES ARE SYNCHRONOUS `def`. SQLite reads are blocking; declaring
them `async def` would run them on the event loop and stall every other
request for the duration of a chain query against a 3.7 GB file. FastAPI runs
plain `def` routes in a worker threadpool, which is what this wants. It is
also why `api/cache.py` takes a lock.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

import config
import db
from api import serialize
from api.cache import SnapshotCache
from dataaccess import queries

_MARKET_TZ = ZoneInfo(config.DISPLAY_TIMEZONE)

# Bounds on `days`. Not arbitrary: the history windows the dashboard offers
# top out well inside this, and an unbounded value would let one request sweep
# the whole record — the read pattern DATABASE.md documents as absent, and
# worth keeping absent.
_MAX_DAYS = 365


class ReadContext:
    """What every route needs: which database, and the cache in front of it.

    The api/ layer's one job (deciding the database) is done once in
    create_app and arrives here. Routes never name config.DB_PATH.
    """

    def __init__(self, db_path: str, cache: SnapshotCache) -> None:
        self.db_path = db_path
        self.cache = cache

    def generation(self) -> int | None:
        """The newest COMPLETE snapshot id — the cache key everything hangs on.

        Read on every request rather than held: it is one indexed row, and
        caching the invalidator against itself is how a cache serves data from
        a snapshot that has since been superseded.
        """
        row = db.get_latest_complete_snapshot(self.db_path)
        return row["snapshot_id"] if row else None

    def latest(self) -> sqlite3.Row | None:
        return db.get_latest_complete_snapshot(self.db_path)

    def default_session_date(self) -> str:
        """The session the newest snapshot belongs to, in market time.

        Stored stamps are UTC; a 20:01 UTC snapshot is 16:01 on the PREVIOUS
        calendar day in New York. Taking the UTC date here would name the
        wrong session for every afternoon snapshot ever recorded — the same
        confusion the `date(ts, '-4 hours')` clauses in db.py exist to avoid.
        """
        row = self.latest()
        if row is None:
            raise HTTPException(
                status_code=503,
                detail="The record holds no completed snapshot yet, so there "
                       "is no session to default to. Pass session_date "
                       "explicitly, or wait for the collector to run.",
            )
        stamp = _dt.datetime.strptime(
            row["snapshot_timestamp"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=_dt.UTC)
        return stamp.astimezone(_MARKET_TZ).date().isoformat()

    def cached(self, key: tuple[Any, ...], compute) -> Any:
        return self.cache.get_or_compute(self.generation(), key, compute)


def build_router(ctx: ReadContext) -> APIRouter:
    router = APIRouter(tags=["reads"])

    # ── the snapshot itself ────────────────────────────────────────────────

    @router.get("/snapshots/latest", summary="The newest completed snapshot")
    def latest_snapshot() -> dict[str, Any]:
        row = ctx.latest()
        if row is None:
            return {"snapshot": None}
        return {"snapshot": serialize.scrub(dict(row))}

    # ── the chain ──────────────────────────────────────────────────────────

    @router.get("/chain", summary="Full option chain for one snapshot")
    def chain(snapshot_id: int | None = Query(
        None, description="Defaults to the newest completed snapshot.")
    ) -> dict[str, Any]:
        """Around 3,000 rows — one snapshot, not a history sweep.

        `expiry` in the response is the DISPLAY KEY, not a date: the third
        Friday appears twice, as "2026-09-18" for the p.m. contract and
        "2026-09-18 (AM)" for the a.m. one. They are different options and
        the record says which (ADR-046, ADR-047). `expiry_date` carries the
        plain date alongside it — do not parse the key back into one.
        """
        target = snapshot_id if snapshot_id is not None else ctx.generation()
        if target is None:
            raise HTTPException(
                status_code=503,
                detail="No completed snapshot exists to read a chain from.")
        df = ctx.cached(("chain", target),
                        lambda: queries.load_chain_df(ctx.db_path, target))
        return serialize.frame_payload(df, snapshot_id=target)

    # ── implied volatility history ─────────────────────────────────────────

    @router.get("/atm-history", summary="At-the-money IV history for a contract")
    def atm_history(
        expiry: str = Query(..., description=
            "Display key, e.g. '2026-09-18' or '2026-09-18 (AM)'."),
        days: int = Query(5, ge=1, le=_MAX_DAYS),
        fallback: bool = Query(True, description=
            "With days=1 and nothing recorded today, widen to the last "
            "populated session rather than returning empty."),
    ) -> dict[str, Any]:
        """IV is returned as a PERCENT, converted at the load boundary."""
        def compute():
            if fallback:
                return queries.load_atm_hist_fb(ctx.db_path, expiry, days)
            return queries.load_atm_hist(ctx.db_path, expiry, days)

        df = ctx.cached(("atm_hist", expiry, days, fallback), compute)
        return serialize.frame_payload(df, expiry=expiry, days=days,
                                       iv_units="percent")

    @router.get("/contract-history", summary="IV history for one exact contract")
    def contract_history(
        expiry: str = Query(..., description="Display key, as above."),
        strike: float = Query(...),
        side: str = Query(..., pattern="^(CALL|PUT)$"),
        days: int = Query(1, ge=1, le=_MAX_DAYS),
    ) -> dict[str, Any]:
        df = ctx.cached(
            ("contract_hist", expiry, strike, side, days),
            lambda: queries.load_contract_hist(ctx.db_path, expiry, strike,
                                               side, days))
        return serialize.frame_payload(df, expiry=expiry, strike=strike,
                                       side=side, days=days,
                                       iv_units="percent")

    @router.get("/atm-iv/latest", summary="The n most recent ATM-IV records")
    def latest_atm_iv(
        expiry: str = Query(..., description="Display key, as above."),
        n: int = Query(2, ge=1, le=100),
    ) -> dict[str, Any]:
        rows = ctx.cached(("latest_atm_iv", expiry, n),
                          lambda: queries.load_latest_atm_iv(ctx.db_path,
                                                             expiry, n=n))
        scrubbed = [serialize.scrub(r) for r in rows]
        return {"count": len(scrubbed), "expiry": expiry, "rows": scrubbed}

    # ── the underlying ─────────────────────────────────────────────────────

    @router.get("/spx/intraday", summary="Intraday SPX path for one session")
    def spx_intraday(session_date: str | None = Query(None, description=
        "YYYY-MM-DD, market time. Defaults to the newest snapshot's session.")
    ) -> dict[str, Any]:
        target = session_date or ctx.default_session_date()
        df = ctx.cached(("spx_intraday", target),
                        lambda: queries.load_spx_intraday(ctx.db_path, target))
        return serialize.frame_payload(df, session_date=target)

    @router.get("/spx/prior-close", summary="Prior session close")
    def prior_close(session_date: str | None = Query(None)) -> dict[str, Any]:
        """Null when there is no prior session — the first day of collection.

        Deliberately not 0. A close of zero is not a thing the S&P 500 has
        ever done, and anything derived from it would be wrong rather than
        merely absent.
        """
        target = session_date or ctx.default_session_date()
        value = ctx.cached(("prior_close", target),
                           lambda: queries.load_prior_close(ctx.db_path, target))
        return {"session_date": target, "prior_close": serialize.scrub(value)}

    # ── the pair reads ─────────────────────────────────────────────────────

    @router.get("/pairs/transform-marks",
                summary="Transform mark history for one strike pair")
    def transform_marks(
        front: str = Query(..., description="Front expiry display key."),
        back: str = Query(..., description="Back expiry display key."),
        call_strike: float = Query(...),
        put_strike: float = Query(...),
        days: int = Query(5, ge=1, le=_MAX_DAYS),
    ) -> dict[str, Any]:
        df = ctx.cached(
            ("transform_marks", front, back, call_strike, put_strike, days),
            lambda: queries.load_transform_marks(ctx.db_path, front, back,
                                                 call_strike, put_strike,
                                                 days=days))
        return serialize.frame_payload(df, front=front, back=back,
                                       call_strike=call_strike,
                                       put_strike=put_strike, days=days)

    @router.get("/pairs/diagonal-history",
                summary="Diagonal net-debit history for one strike pair")
    def diagonal_history(
        front: str = Query(...),
        back: str = Query(...),
        call_strike: float = Query(...),
        put_strike: float = Query(...),
        days: int = Query(5, ge=1, le=_MAX_DAYS),
    ) -> dict[str, Any]:
        df = ctx.cached(
            ("diagonal_hist", front, back, call_strike, put_strike, days),
            lambda: queries.load_diagonal_hist(ctx.db_path, front, back,
                                               call_strike, put_strike,
                                               days=days))
        return serialize.frame_payload(df, front=front, back=back,
                                       call_strike=call_strike,
                                       put_strike=put_strike, days=days)

    # ── positioning ────────────────────────────────────────────────────────

    @router.get("/strikes/intraday-metrics",
                summary="Per-strike gamma, OI and volume through one session")
    def strike_metrics(
        session_date: str | None = Query(None),
        dte_max: int | None = Query(None, ge=0, description=
            "Limit to expiries within this many days."),
    ) -> dict[str, Any]:
        target = session_date or ctx.default_session_date()
        df = ctx.cached(
            ("strike_metrics", target, dte_max),
            lambda: queries.load_intraday_strike_metrics(ctx.db_path, target,
                                                         dte_max))
        return serialize.frame_payload(df, session_date=target,
                                       dte_max=dte_max)

    @router.get("/strikes/prior-session-oi",
                summary="Open interest per strike at the prior session's close")
    def prior_session_oi(
        session_date: str | None = Query(None),
        expiry: str | None = Query(None, description=
            "Scopes to one contract date. Must match the scope of whatever "
            "this is being subtracted from."),
    ) -> dict[str, Any]:
        """Empty on the first day of collection, and empty for a strike that
        is new today. Those are different stories and this endpoint does not
        pick between them — the caller has the context to say which."""
        target = session_date or ctx.default_session_date()
        df = ctx.cached(
            ("prior_oi", target, expiry),
            lambda: queries.load_prior_session_oi(ctx.db_path, target, expiry))
        return serialize.frame_payload(df, session_date=target, expiry=expiry)

    return router


def build_computed_router(ctx: ReadContext) -> APIRouter:
    """M4.3 — scanner, "New", and gamma exposure.

    Separate router, same context and same cache: these are more expensive
    than the reads (the sweep is 21 passes over the chain) and benefit most
    from being keyed on the snapshot rather than a clock.
    """
    from api import computed

    router = APIRouter(prefix="/mission", tags=["computed"])

    def _chain_and_spot(snapshot_id: int | None):
        target = snapshot_id if snapshot_id is not None else ctx.generation()
        if target is None:
            raise HTTPException(
                status_code=503,
                detail="No completed snapshot exists to compute from.")
        chain = ctx.cached(("chain", target),
                           lambda: queries.load_chain_df(ctx.db_path, target))
        if chain.empty:
            raise HTTPException(
                status_code=404,
                detail=f"Snapshot {target} holds no option rows.")

        # THE SPOT MUST COME FROM THE SNAPSHOT BEING COMPUTED, not from the
        # newest one. Every figure below is priced against it, so pairing last
        # Tuesday's chain with today's SPX would produce a full set of wrong
        # numbers that all look reasonable.
        row = db.get_snapshot_by_id(ctx.db_path, target)
        if row is None or row["underlying_price"] is None:
            raise HTTPException(
                status_code=422,
                detail=f"Snapshot {target} records no underlying price, so "
                       f"nothing priced against spot can be computed from it.")
        return target, chain, float(row["underlying_price"])

    @router.get("/scan", summary="The transform sweep across every expiry pair")
    def scan(snapshot_id: int | None = Query(None),
             limit: int = Query(100, ge=1, le=2000)) -> dict[str, Any]:
        """Phase A of Mission Control: 21 offsets across every valid pair.

        This is the expensive one, and the reason the cache is keyed on the
        snapshot rather than a TTL — recomputing 21 sweeps because a clock
        moved, to produce an identical answer, is the cost ENH-011 records on
        the page side.
        """
        target, chain, spot = _chain_and_spot(snapshot_id)
        sweep = ctx.cached(("sweep", target),
                           lambda: computed.scan(chain, spot, target))
        return serialize.frame_payload(
            sweep.head(limit), snapshot_id=target, spot=spot,
            returned=min(limit, len(sweep)),
            bands=computed.classify(sweep))

    @router.get("/new", summary="Pairs eligible now that were not before")
    def new_pairs(
        snapshot_id: int | None = Query(None),
        record: bool = Query(True, description=
            "Advance the comparison point. False looks without recording — "
            "which means the NEXT caller still compares against the older "
            "snapshot."),
    ) -> dict[str, Any]:
        """The "New" flag, anchored on the snapshot rather than a browser tab.

        `compared_against_snapshot` is in the response on purpose. Nothing
        records eligibility unless asked, so after a quiet night the
        comparison may reach back to yesterday — a truthful answer to "what
        has appeared since this was last looked at", and a misleading one to
        "what appeared in the last minute". The field says which question was
        actually answered; null means this is the first recording and nothing
        can be new yet.
        """
        target, chain, spot = _chain_and_spot(snapshot_id)
        sweep = ctx.cached(("sweep", target),
                           lambda: computed.scan(chain, spot, target))
        eligible = computed.eligible_from_sweep(sweep)
        try:
            return computed.new_since_previous(ctx.db_path, target, eligible,
                                               record=record)
        except sqlite3.OperationalError as exc:
            if "mc_eligible_keys" not in str(exc):
                raise
            # A database still on an older schema. Migrations are applied by
            # the collector or the dashboard on startup, deliberately NOT by
            # this server: a read-only process that quietly rewrote the shape
            # of the one irreplaceable file would be the opposite of what
            # ADR-051 set out to build. Say what is missing and what applies
            # it, rather than returning a stack trace or — worse — an empty
            # "nothing is new" that would look like a real answer.
            raise HTTPException(
                status_code=503,
                detail="This database predates schema v4, so the registry "
                       "table mc_eligible_keys does not exist yet and "
                       "nothing can be compared against. It is created on "
                       "the next collector start or dashboard open. The "
                       "scanner endpoints work regardless.",
            ) from exc

    @router.get("/gamma", summary="Gamma exposure by strike, and the flip level")
    def gamma(
        snapshot_id: int | None = Query(None),
        expiry: str | None = Query(None, description=
            "Display key. Omit for the whole board."),
    ) -> dict[str, Any]:
        target, chain, spot = _chain_and_spot(snapshot_id)
        result = ctx.cached(
            ("gamma", target, expiry),
            lambda: computed.gamma_exposure(chain, spot, expiry))
        by_strike = result.pop("by_strike")
        return serialize.frame_payload(by_strike, snapshot_id=target,
                                       **result)

    return router
