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

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

import config
import db
import iv_engine
from api import serialize
from api.cache import SnapshotCache
from core import (contract, dealer, expiry as expiry_rules, flow, gex,
                  ranges, scanner, series, session as core_session,
                  timelines)
from core.scanner import TSCAN_THRESHOLD
from dataaccess import queries
from state import eligible_history

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

    def __init__(self, db_path: str, cache: SnapshotCache,
                 *, state_dir: str | None = None) -> None:
        self.db_path = db_path
        self.cache = cache
        # Where the JSON sidecars live. Only the non-ATM panel reads it, and
        # only ever reads it. Defaulted rather than required so the dozens of
        # existing two-argument constructions in the tests stay valid — a
        # route that needs it says so by using it.
        self.state_dir = state_dir if state_dir is not None else str(config.STATE_DIR)

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
        # THE THREE DERIVED MARKS ARE SERVED, NOT LEFT TO THE CLIENT. The
        # Calendar Edge chart draws `diagonal_mark`, `transform_mark` and
        # `gap`, and all three are subtractions of the four raw marks below
        # them. A client that did its own subtraction would be the fifth copy
        # of the definition of a diagonal -- see core.scanner.add_mark_columns.
        #
        # The wall-clock `timestamp` and the session breaks come with it for
        # the same reason: the naive local time is a hard requirement of
        # Plotly's rangebreaks (DEBT-030), and where a line must break across
        # a weekend or an outage is a question about the record, not about
        # the drawing.
        rest: dict[str, Any] = {}
        if not df.empty:
            df = series.to_display_time(
                scanner.add_mark_columns(df), config.DISPLAY_TIMEZONE,
                ts_col="snapshot_timestamp")
            df = df.rename(columns={"snapshot_timestamp": "timestamp"})
            df = series.break_sessions(df.sort_values("timestamp")
                                       .reset_index(drop=True))
            rest["rangebreaks"] = series.SESSION_RANGEBREAKS
            # Where the underlying crossed a short strike, and which way.
            # Served rather than left to the client: a crossing is a directed
            # event with a boundary rule (see strike_crossings), and getting
            # that boundary wrong marks a chart with events that never
            # happened -- which reads as a volatile session, not as a bug.
            if "spx" in df.columns and df["spx"].notna().any():
                rest["crossings"] = series.strike_crossings(
                    df["timestamp"].astype(str).tolist(),
                    df["spx"].tolist(), (put_strike, call_strike))
        # The 5-point line, served rather than restated. It is already
        # duplicated in four places in Python (DEBT-031); the shading on the
        # React chart marks every stretch at or above it, and a fifth copy
        # written in TypeScript would be the one nobody remembers to change.
        # Where each trading day starts. Served rather than derived from the
        # rows, because "one session gets no line" is a decision (see
        # core.series.market_open_lines) and a client re-deriving it would
        # eventually draw a marker the page does not.
        return serialize.frame_payload(df, front=front, back=back,
                                       call_strike=call_strike,
                                       put_strike=put_strike, days=days,
                                       threshold=TSCAN_THRESHOLD,
                                       market_opens=series.market_open_lines(
                                           df["timestamp"] if not df.empty else []),
                                       # The window a single session's x-axis
                                       # is drawn on, or null across several
                                       # days (BUG-041). Served, not derived:
                                       # left to autorange the chart ended
                                       # where the DATA ended, so an afternoon
                                       # of missing marks read as a short
                                       # trading day. Same definition the old
                                       # screen draws on.
                                       session_axis_range=series.session_axis_range(
                                           df["timestamp"] if not df.empty else []),
                                       **rest)

    @router.get("/pairs/atm-pair",
                summary="Front and back ATM IV on one timeline, with the ratio")
    def atm_pair(
        front: str = Query(..., description="Front expiry display key."),
        back: str = Query(..., description="Back expiry display key."),
        days: int = Query(5, ge=1, le=_MAX_DAYS),
    ) -> dict[str, Any]:
        """The frame the Calendar Edge tab's IV charts are drawn from.

        SERVED AS ONE FRAME, NOT TWO SERIES. `/atm-history` already answers
        for a single expiry, and a client could ask twice and divide -- but
        the join has to be INNER and the division has to happen only on
        timestamps where both were observed. A client doing it by index, or
        by nearest timestamp, would produce a ratio between two different
        minutes: a number that reads perfectly and never existed. The rule
        is `core.series.merge_atm_pair`, and this endpoint and the Streamlit
        tab call the same one.

        `bands` are the regime boundaries the ratio is read against, and the
        `sample_warning` is present exactly when there is too little history
        to trust a percentile -- both served rather than restated, for the
        same reason.
        """
        def compute():
            return series.merge_atm_pair(
                queries.load_atm_hist_fb(ctx.db_path, front, days),
                queries.load_atm_hist_fb(ctx.db_path, back, days),
                config.DISPLAY_TIMEZONE,
            )

        df = ctx.cached(("atm_pair", front, back, days), compute)
        warning = (None if df.empty
                   else iv_engine.sample_size_warning(df["iv_ratio"]))

        # THE CACHED FRAME IS NOT MUTATED. `ctx.cached` hands back the same
        # object on every hit, so adding a column in place would leave `hod`
        # on the frame every later reader sees -- including the ones that
        # never asked for it.
        domain = None
        if not df.empty:
            df = df.assign(hod=series.hour_of_day(df))
            lo, hi = series.scatter_domain(df)
            domain = [lo, hi]

        return serialize.frame_payload(
            df, front=front, back=back, days=days, iv_units="percent",
            bands=series.ratio_bands(),
            rangebreaks=series.SESSION_RANGEBREAKS,
            scatter_domain=domain,
            market_opens=series.market_open_lines(
                df["timestamp"] if not df.empty else []),
            session_axis_range=series.session_axis_range(
                df["timestamp"] if not df.empty else []),
            sample_warning=warning)

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
            "Limit to expiries within this many days. A CUMULATIVE bound: it "
            "cannot express one contract. Use `expiry` for that."),
        expiry: str | None = Query(None, description=
            "Scopes to ONE contract by display key, and takes precedence over "
            "dte_max. The third Friday lists two contracts, so this is a "
            "display key rather than a bare date."),
    ) -> dict[str, Any]:
        target = session_date or ctx.default_session_date()
        # `expiry` is in the cache key as well as the call. It was added to
        # the query in ENH-014 and not here, so the API could still only ask
        # the two questions the old Streamlit control asked — and a key
        # missing an argument serves one scope's answer under another's label,
        # which is the fault api/cache.py's docstring already warns about.
        df = ctx.cached(
            ("strike_metrics", target, dte_max, expiry),
            lambda: queries.load_intraday_strike_metrics(ctx.db_path, target,
                                                         dte_max, expiry))
        return serialize.frame_payload(df, session_date=target,
                                       dte_max=dte_max, expiry=expiry)

    @router.get("/strikes/session-range",
                summary="Each strike's session high and low exposure (wicks)")
    def session_range(
        session_date: str | None = Query(None),
        expiry: list[str] | None = Query(None, description=
            "Scopes to one or more contracts by display key. MUST MATCH THE "
            "SCOPE THE BARS ARE DRAWN AT — a whole-board range behind a "
            "single-expiry bar would put the bar inside a wick belonging to "
            "twenty other contracts."),
        dte_max: int | None = Query(None, ge=0),
        measure: str = Query("gamma", description=
            "Which exposure the range is taken over — one of "
            "gamma, vgex, delta, vanna, charm. MUST MATCH THE MEASURE THE "
            "BARS ARE DRAWN AT: a gamma range behind a charm bar is a "
            "different Greek at a scale that still looks plausible."),
    ) -> dict[str, Any]:
        """The range each strike has travelled through today, for the wicks.

        A SEPARATE ADDRESS FROM `/mission/gamma`, deliberately. The bars are
        one snapshot; this walks every snapshot of the session — roughly a
        hundred times the rows — and the panel must not wait on it. The tab
        draws the bars first and the wicks when they arrive.

        EVERY MEASURE, NOT JUST GAMMA (Chandan, 2026-09-06: "I want that same
        wick to be present in all the six chart"). `measure` defaults to
        gamma, so every caller written before it existed keeps the answer it
        had — and that answer is unchanged in value as well as in shape: the
        generic path was cross-checked against the old gamma-only one over a
        full live session and agreed to floating-point precision.

        NOT THE CHANGE SINCE THE OPEN. That is
        `/mission/gamma/flow`'s question and a different number; see
        core.ranges.session_ranges.
        """
        if measure not in ranges.MEASURES:
            # 422 rather than a silent fall back to gamma, for the reason
            # `/mission/gamma` gives: a caller that asked for charm and was
            # handed gamma would draw the wrong wick under the right title.
            raise HTTPException(
                status_code=422,
                detail=f"measure must be one of {', '.join(ranges.MEASURES)}; "
                       f"got {measure!r}",
            )

        target = session_date or ctx.default_session_date()
        # ONE key per scope, `expiry` included, for the reason the comment in
        # strike_metrics above spells out: a key missing an argument serves
        # one scope's answer under another's label.
        scope = tuple(expiry) if expiry else None

        # THE SESSION IS LOADED ONCE AND SCOPED IN MEMORY, which is why this
        # cache entry carries neither the measure nor the expiry. It is the
        # expensive half — ~410,000 rows, a few seconds — and all five
        # measures at every scope are answered from the one frame. Reading it
        # per measure would multiply the only slow step by five to produce
        # five identical results.
        session_chain = ctx.cached(
            ("session_chain", target, dte_max),
            lambda: queries.load_session_chain_df(ctx.db_path, target, dte_max))

        # ...AND THE ANSWER'S KEY CARRIES BOTH. Five measures share one frame
        # and must not share one answer: gamma's range served under charm's
        # label is exactly what the 422 above refuses to do by another route.
        df = ctx.cached(
            ("session_range", target, measure, dte_max, scope),
            lambda: ranges.session_ranges(
                session_chain, measure,
                r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
                display_tz=config.DISPLAY_TIMEZONE, expiry=scope))
        return serialize.frame_payload(df, session_date=target, measure=measure,
                                       dte_max=dte_max, expiry=expiry)

    @router.get("/strikes/flow",
                summary="Contracts traded at one strike, bucketed through the day")
    def strike_flow(
        strike: float = Query(..., description="The strike to follow."),
        session_date: str | None = Query(None),
        expiry: str | None = Query(None, description=
            "Scopes to ONE contract by display key. Without it the figure is "
            "every expiry listing this strike, summed."),
        dte_max: int | None = Query(None, ge=0),
    ) -> dict[str, Any]:
        """How many contracts traded at this strike, minute by minute.

        NOT THE FOUR-WAY BOUGHT/SOLD SPLIT the reference panel draws, and it
        cannot be — see core/flow.py. This is contracts TRADED, which is what
        a chain snapshot can honestly support. The response says so in
        `basis`, so a caller cannot pick this up and label it as order flow.

        THE BUCKETS ARE THE COLLECTOR'S OWN CADENCE, handed down from config
        rather than chosen here: one minute in the first and last half hour,
        five midday. Every bucket therefore holds exactly one poll.
        """
        target = session_date or ctx.default_session_date()
        scope = expiry
        df = ctx.cached(
            ("strike_flow", target, strike, scope, dte_max),
            lambda: flow.strike_flow(
                queries.load_intraday_strike_metrics(ctx.db_path, target,
                                                     dte_max, scope),
                strike=strike,
                display_tz=config.DISPLAY_TIMEZONE,
                open_end=core_session.OPEN_END,
                midday_end=core_session.MIDDAY_END,
                event_secs=config.POLL_INTERVAL_EVENT,
                normal_secs=config.POLL_INTERVAL_NORMAL))
        return serialize.frame_payload(
            df, session_date=target, strike=strike, expiry=expiry,
            dte_max=dte_max,
            # THE HONEST LABEL, on the wire rather than in a comment. A client
            # that renders this as "calls bought / puts bought" is making a
            # claim the data cannot support, and this is what it has to
            # contradict to do so.
            basis="contracts traded, from the change in cumulative volume "
                  "between snapshots; not buyer- or seller-initiated")

    @router.get("/strikes/net-volume",
                summary="Net volume at the busiest strikes, through the session")
    def strikes_net_volume(
        session_date: str | None = Query(None),
        expiry: str | None = Query(None, description=
            "Scopes to ONE contract by display key. Without it each strike is "
            "every expiry listing it, summed."),
        dte_max: int | None = Query(None, ge=0),
        count: int = Query(timelines.VOLUME_LINES, ge=1, le=40, description=
            "How many strikes to draw. The default is what the panel uses."),
    ) -> dict[str, Any]:
        """Calls traded minus puts traded at each strike, minute by minute.

        THE RANKING IS THE POINT. A board has a hundred strikes and this
        returns the `count` whose net volume reached the largest ABSOLUTE
        reading at any moment in the day — a strike hammered at the open and
        quiet since is part of the session's story, so it is not ranked on
        where it finished. `/strikes/gex-timeline` deliberately ranks the
        other way; see core/timelines.py for why.

        The figure is a RUNNING TOTAL, not a per-bucket count: the exchange's
        `volume` is cumulative for the session, so these lines climb. For the
        per-bucket differencing, that is `/strikes/flow`.
        """
        target = session_date or ctx.default_session_date()
        scope = expiry
        df = ctx.cached(
            ("strikes_net_volume", target, scope, dte_max, count),
            lambda: timelines.net_volume_by_strike(
                queries.load_intraday_strike_metrics(ctx.db_path, target,
                                                     dte_max, scope),
                display_tz=config.DISPLAY_TIMEZONE,
                count=count))
        return serialize.frame_payload(
            df, session_date=target, expiry=expiry, dte_max=dte_max,
            count_requested=count,
            basis="cumulative contracts traded this session, calls minus "
                  "puts; not buyer- or seller-initiated")

    @router.get("/strikes/gex-timeline",
                summary="Net gamma exposure at the top strikes, through the session")
    def strikes_gex_timeline(
        session_date: str | None = Query(None),
        expiry: str | None = Query(None),
        dte_max: int | None = Query(None, ge=0, description=
            "0 for the 0DTE flow panel. Omitted, this is the whole board."),
        count: int = Query(timelines.GEX_LINES, ge=1, le=40),
    ) -> dict[str, Any]:
        """Net gamma exposure at each strike as the session ran.

        Asked with `dte_max=0` this is the 0DTE flow panel — the fastest
        options on the board traced through the day rather than frozen at this
        moment.

        RANKED AT THE LATEST SNAPSHOT, on magnitude so short-gamma strikes are
        not filtered out for being negative. The panel is about where gamma is
        sitting going into the close.

        `totals` carries the headline strip — the sum now, the sum at the
        session's first snapshot, and the change — computed from THE LINES
        RETURNED rather than from the board, so the strip and the chart cannot
        disagree by a number nobody can account for. All three are null when
        there is no reading, which is not the same as zero.
        """
        target = session_date or ctx.default_session_date()
        scope = expiry
        df = ctx.cached(
            ("strikes_gex_timeline", target, scope, dte_max, count),
            lambda: timelines.gex_by_strike_over_time(
                queries.load_intraday_strike_metrics(ctx.db_path, target,
                                                     dte_max, scope),
                display_tz=config.DISPLAY_TIMEZONE,
                count=count))
        return serialize.frame_payload(
            df, session_date=target, expiry=expiry, dte_max=dte_max,
            count_requested=count,
            totals=timelines.gex_totals(df),
            basis="gamma x open interest, dollars per 1% move, each snapshot "
                  "scaled by its own spot price")

    def _snapshot_chain_and_spot(snapshot_id: int | None):
        """The chain of one snapshot and THAT snapshot's own spot price.

        THE SPOT MUST COME FROM THE SNAPSHOT BEING READ. Everything below is
        measured as a distance from it — the band around spot, the moneyness
        of a wall — so pairing last Tuesday's chain with today's SPX would
        produce a full set of wrong numbers that all look reasonable.

        The same helper exists on the /mission router. It is not shared,
        because the two routers raise different messages for the same
        conditions and folding them together would mean one of the two
        endpoints explaining itself in the other's words.
        """
        target = snapshot_id if snapshot_id is not None else ctx.generation()
        if target is None:
            raise HTTPException(
                status_code=503,
                detail="No completed snapshot exists to read from.")
        chain = ctx.cached(("chain", target),
                           lambda: queries.load_chain_df(ctx.db_path, target))
        if chain.empty:
            raise HTTPException(
                status_code=404,
                detail=f"Snapshot {target} holds no option rows.")
        row = db.get_snapshot_by_id(ctx.db_path, target)
        if row is None or row["underlying_price"] is None:
            raise HTTPException(
                status_code=422,
                detail=f"Snapshot {target} records no underlying price, so "
                       f"nothing measured against spot can be read from it.")
        return target, chain, float(row["underlying_price"])

    @router.get("/dealer/bubbles",
                summary="Volume across expiry and strike, as sized points")
    def dealer_bubbles(
        snapshot_id: int | None = Query(None),
        trim: bool = Query(True, description=
            "Keep only the busiest strikes of the nearest expiries. Off, this "
            "is every point in the band — about 120 strikes per expiry, which "
            "fuses the columns into solid bars."),
    ) -> dict[str, Any]:
        """Where today's trading happened, across BOTH expiry and strike.

        NO EXPIRY PARAMETER, deliberately. This panel exists to COMPARE
        expiries; scoped to one it is a single column, which is the chart it
        is not. `dte_max` would be the same mistake more slowly.

        `radius` arrives computed, and is relative to the busiest point on the
        WHOLE band rather than on what survived the trim — rescaling to the
        survivors would make an expiry look busier simply because its
        neighbours were dropped. `flow` is the call/put verdict for the point
        and `pcr` the ratio behind it, infinite where puts traded and calls
        did not. All of it is core/dealer.py's; none of it is recomputed by a
        caller.
        """
        target, chain, spot = _snapshot_chain_and_spot(snapshot_id)
        points = ctx.cached(
            ("dealer_bubbles", target, trim),
            lambda: (dealer.most_traded(dealer.bubble_points(chain, spot))
                     if trim else dealer.bubble_points(chain, spot)))
        return serialize.frame_payload(
            points, snapshot_id=target, spot=spot, trimmed=trim,
            band_percent=dealer.BAND_PERCENT,
            basis="contracts traded this session within "
                  f"{dealer.BAND_PERCENT}% of spot; notional is mark x volume "
                  "x 100, blank where the chain carries no mark")

    @router.get("/dealer/positioning",
                summary="Volume against the overnight change in open interest")
    def dealer_positioning(
        session_date: str | None = Query(None),
        expiry: str | None = Query(None, description=
            "Scopes BOTH sides of the comparison. All-expiry open interest "
            "minus one expiry's would report the rest of the board as an "
            "overnight liquidation."),
        snapshot_id: int | None = Query(None),
    ) -> dict[str, Any]:
        """Whether the trading at each strike left real positions behind.

        **THE VERDICT IS ABOUT YESTERDAY, AND IT HAS TO BE.** Open interest is
        republished once, overnight, so today's figure minus yesterday's is
        what was opened or closed during YESTERDAY's session. The ratio behind
        every verdict divides that change by the PRIOR session's volume — the
        trading it actually came from. `total_volume` is today's and is
        context, not evidence: a live churn reading for today cannot be had
        from this data at all.

        With no prior session `delta_oi` is null and every verdict is blank.
        That is the first collected day, and reporting "unknown" as zero
        change would call the whole board churn.
        """
        target, chain, spot = _snapshot_chain_and_spot(snapshot_id)
        day = session_date or ctx.default_session_date()
        scope = expiry

        def compute():
            today = gex.by_strike(chain, spot, expiry=scope)
            prior = queries.load_prior_session_oi(ctx.db_path, day, scope)
            return dealer.positioning(today, prior, spot)

        rows = ctx.cached(
            ("dealer_positioning", target, day, scope), compute)

        # WHAT TO CALL EACH COLUMN, decided here rather than in the browser.
        # The data does not change when the bell rings; what it MEANS does —
        # today's volume is a number still being written at 11:00 and a
        # finished total at 16:30, and only the label can tell the reader
        # which. `market_open` reads the same pure function the collector
        # schedules on, so the word cannot start disagreeing with whether data
        # is actually arriving. NOT computed client-side: it is a clock
        # comparison against market holidays in market time, which in a
        # browser would run in the VIEWER's timezone.
        now_et = _dt.datetime.now(_dt.UTC).astimezone(
            ZoneInfo(config.DISPLAY_TIMEZONE))
        market_open = core_session.session_of(
            now_et, config.MARKET_HOLIDAYS) is not None

        # The strike nearest spot, so the row can be marked without the
        # browser deciding what "nearest" means. Null on an empty board.
        atm = (None if rows.empty
               else float(rows.loc[(rows["strike"] - spot).abs().idxmin(),
                                   "strike"]))

        return serialize.frame_payload(
            rows, snapshot_id=target, session_date=day, expiry=expiry,
            spot=spot, market_open=market_open,
            day_labels=dealer.day_labels(market_open), atm_strike=atm,
            # The legend under the panel. SERVED, not written in the browser:
            # the wording IS the definition of each band, and two tabs
            # describing the same threshold in their own words would drift
            # apart with no test able to see it.
            glossary=[{"verdict": v, "tone": t, "meaning": m}
                      for v, t, m in dealer.VERDICT_MEANINGS],
            basis="the change in open interest is the PRIOR session's, "
                  "measured against the prior session's own volume; "
                  "total_volume is today's and is context, not evidence")

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

    def _countdown_anchor(target: int, chain: pd.DataFrame) -> _dt.date:
        """Which day this snapshot's expiry countdowns are measured from.

        SHARED BY THE TWO TABS THAT SHOW A COUNTDOWN -- the Gamma tab's expiry
        picker and Calendar Edge's front/back dropdowns. Chandan asked for the
        live clock on the first (2026-09-06) and then for the same on the
        second (2026-09-07), which is the moment a second copy of this would
        have been written; the rule itself is core.expiry.countdown_anchor and
        all that lives here is the one input it cannot know -- whether the
        snapshot being served is the newest one there is.

        On the current board the anchor is the reader's own date, so "8 Sep"
        reads 1 DTE on Monday rather than the 4 DTE it was when Friday's
        snapshot was taken. On a board replayed from an older session it stays
        that session's date, or every window would come back empty.
        """
        newest = ctx.latest()
        snap_day = (computed.snapshot_date(chain)
                    or _dt.date.fromisoformat(ctx.default_session_date()))
        return expiry_rules.countdown_anchor(
            snap_day, _dt.datetime.now(_dt.UTC),
            is_latest=newest is not None and newest["snapshot_id"] == target)

    @router.get("/controls",
                summary="The expiry and strike selections a pair tab offers")
    def pair_controls(
        snapshot_id: int | None = Query(None),
        front: str | None = Query(None, description=
            "Chosen front expiry; the first available one if omitted."),
        back: str | None = Query(None, description=
            "Chosen back expiry; the next date after the front if omitted."),
    ) -> dict[str, Any]:
        """What the Streamlit sidebar's four controls offer, and why.

        SERVED BECAUSE THE NARROWING IS A RULE, NOT A CONVENIENCE. Back
        expiries exclude anything not strictly later than the front, and the
        strike lists are the intersection of both legs. A client that listed
        every expiry and every strike would put twenty wrong answers one
        click away -- which is the state this narrowing was introduced to
        end (Chandan, 2026-08-19). See api.computed.pair_controls.
        """
        target, chain, spot = _chain_and_spot(snapshot_id)
        # THE COUNTDOWNS FOLLOW THE CLOCK, not the snapshot's stored `dte`.
        # "DTE following the live clock should be applicable for Front Expiry
        # and Back Expiry dropdown as well under Calendar Edge" (Chandan,
        # 2026-09-07). Nothing is cached here, so unlike the Gamma tab there is
        # no key to carry the anchor into -- every request recomputes, and a
        # cache added later must add the anchor to its key or it will freeze
        # the first reader's date in for the rest of the day.
        anchor = _countdown_anchor(target, chain)
        body = computed.pair_controls(chain, spot, front=front, back=back,
                                      today=anchor)
        return {"snapshot_id": target, "spot": spot, **body}

    @router.get("/header",
                summary="The strip above every tab: SPX, change, VIX, freshness")
    def header() -> dict[str, Any]:
        """One request for the chrome every tab shares.

        ALWAYS THE NEWEST SNAPSHOT, never a chosen one. This strip answers
        "is the dashboard looking at live data right now", and pinning it to
        a snapshot under inspection would make it answer a different question
        in the same words.

        `age_seconds` is computed HERE, at request time, from the snapshot
        stamp -- and the client ticks it upward from there rather than
        recomputing it, because the browser's clock is the viewer's and the
        stamp is the record's. See api.computed.header for what "late" means.
        """
        row = ctx.latest()
        if row is None:
            raise HTTPException(
                status_code=503,
                detail="The record holds no completed snapshot yet.")
        target = row["snapshot_id"]
        chain = ctx.cached(("chain", target),
                           lambda: queries.load_chain_df(ctx.db_path, target))
        stamp = _dt.datetime.strptime(
            row["snapshot_timestamp"][:19], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=_dt.UTC)
        session_date = stamp.astimezone(_MARKET_TZ).date().isoformat()

        prev_close = ctx.cached(
            ("prior_close", session_date),
            lambda: queries.load_prior_close(ctx.db_path, session_date))
        intraday = ctx.cached(
            ("spx_intraday", session_date),
            lambda: queries.load_spx_intraday(ctx.db_path, session_date))
        session_open = (None if intraday.empty
                        else float(intraday["underlying_price"].iloc[0]))

        body = computed.header(
            spx_price=row["underlying_price"],
            vix_value=row["vix_value"],
            prev_close=prev_close,
            session_open=session_open,
            chain_df=chain,
            snap_age_secs=(_dt.datetime.now(_dt.UTC) - stamp).total_seconds(),
            now_et=_dt.datetime.now(_dt.UTC).astimezone(_MARKET_TZ),
        )
        return serialize.scrub({
            "snapshot_id": target,
            "snapshot_timestamp": row["snapshot_timestamp"],
            "session_date": session_date,
            **body,
        })

    @router.get("/historical-stats",
                summary="Where today's IV ratio sits in four windows of history")
    def historical_stats(
        front: str = Query(..., description="Front expiry display key."),
        back: str = Query(..., description="Back expiry display key."),
        snapshot_id: int | None = Query(None),
    ) -> dict[str, Any]:
        """The panel beneath Strike Detail -- `views/historical.py`.

        FOUR WINDOWS OF THE SAME QUESTION: is today's front/back ratio high
        or low compared with the last day, week, fortnight and month of its
        own readings. `current` is the ratio from the chain right now; each
        window carries the range it sits in, where in that range it falls,
        and what percentile that is.

        THE JOIN IS THE SAME ONE the charts use, minus the session breaks --
        this panel takes a min, a max and a percentile, and has no time axis
        for a gap row to gap. See `core.series.merge_iv_pair`.

        A window with no overlapping history comes back with nulls, not
        zeros. "The ratio has never been lower" and "we have nothing to
        compare against" are different answers.
        """
        target, chain, spot = _chain_and_spot(snapshot_id)
        head = computed.edge_headline(chain, spot, front, back)
        current = head["ratio"]

        windows = []
        for label, days in series.HISTORICAL_WINDOWS:
            merged = ctx.cached(
                ("hist_ratio", front, back, days),
                lambda days=days: series.merge_iv_pair(
                    queries.load_atm_hist_fb(ctx.db_path, front, days),
                    queries.load_atm_hist_fb(ctx.db_path, back, days),
                    config.DISPLAY_TIMEZONE, insert_breaks=False),
            )
            if merged.empty or current is None:
                windows.append({"label": label, "days": days, "low": None,
                                "high": None, "position_pct": 50.0,
                                "percentile": None, "band": "MID",
                                "colour": "#6d8fa8", "observations": 0})
            else:
                windows.append({"label": label, "days": days,
                                **computed.historical_window(
                                    merged["iv_ratio"], current)})

        return {"snapshot_id": target, "front": front, "back": back,
                "current": current, "windows": windows}

    @router.get("/edge-headline",
                summary="The four figures above the Calendar Edge charts")
    def edge_headline(
        front: str = Query(..., description="Front expiry display key."),
        back: str = Query(..., description="Back expiry display key."),
        snapshot_id: int | None = Query(None),
    ) -> dict[str, Any]:
        """ATM IV either side of the pair, their ratio, and the chain's index.

        SERVED BECAUSE NONE OF THE FOUR IS A DIVISION. See
        `api.computed.edge_headline` -- an ATM IV is a call/put mean at the
        nearest strike, and the index is a mean of per-expiry means chosen so
        a heavily-quoted weekly cannot outvote a thin monthly.
        """
        target, chain, spot = _chain_and_spot(snapshot_id)
        return {"snapshot_id": target, "spot": spot,
                **computed.edge_headline(chain, spot, front, back)}

    @router.get("/strike-detail",
                summary="The four legs of the diagonal, priced, with ATM context")
    def strike_detail(
        snapshot_id: int | None = Query(None),
        front: str = Query(...), back: str = Query(...),
        put_strike: float = Query(...), call_strike: float = Query(...),
    ) -> dict[str, Any]:
        """The left column of the Strike Detail tab.

        `found_exact` rides along on every contract. The chain does not always
        list the strike asked for, and `iv_engine.strike_contract` falls back
        to the nearest one -- a screen that did not say so would show a
        neighbouring contract's price under the requested strike's heading.
        """
        target, chain, _ = _chain_and_spot(snapshot_id)
        return {
            "snapshot_id": target,
            "legs": computed.strike_legs(chain, front, back,
                                         put_strike, call_strike),
            "expiries": [
                {"role": role, "expiry": expiry,
                 **computed.atm_headline(
                     ctx.cached(("atm_latest", target, expiry),
                                lambda e=expiry: queries.load_latest_atm_iv(
                                    ctx.db_path, e, n=2)))}
                for role, expiry in (("Front", front), ("Back", back))
            ],
        }

    @router.get("/strike-iv",
                summary="Front vs back IV at the two trade strikes, over time")
    def strike_iv(
        front: str = Query(...), back: str = Query(...),
        put_strike: float = Query(...), call_strike: float = Query(...),
        days: int = Query(5, ge=1, le=_MAX_DAYS),
    ) -> dict[str, Any]:
        """The Strike Detail chart's two frames, one per side.

        THE SAME JOIN AS `/pairs/atm-pair`, one level down: two contract
        histories inner-joined on timestamp with the ratio between them.
        Both go through `core.series.merge_iv_pair`, which is also where the
        BUG-002 ordering lives -- the ratio must be computed BEFORE the
        session breaks are inserted, or the ratio line draws a straight
        connector across a weekend and invents IV movement.

        Either side can be empty on its own: a strike can have history in one
        expiry and none in the other, and half a ratio is not an answer.
        """
        def side(strike: float, kind: str):
            return series.merge_iv_pair(
                queries.load_contract_hist(ctx.db_path, front, strike, kind, days),
                queries.load_contract_hist(ctx.db_path, back, strike, kind, days),
                config.DISPLAY_TIMEZONE, value_col="iv")

        calls = ctx.cached(("strike_iv", front, back, call_strike, "CALL", days),
                           lambda: side(call_strike, "CALL"))
        puts = ctx.cached(("strike_iv", front, back, put_strike, "PUT", days),
                          lambda: side(put_strike, "PUT"))
        return {
            "front": front, "back": back, "days": days, "iv_units": "percent",
            "call_strike": call_strike, "put_strike": put_strike,
            "calls": serialize.frame_payload(calls)["rows"],
            "puts": serialize.frame_payload(puts)["rows"],
            "rangebreaks": series.SESSION_RANGEBREAKS,
        }

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
            computed.add_raw_expiries(sweep.head(limit)),
            snapshot_id=target, spot=spot,
            returned=min(limit, len(sweep)),
            bands=computed.classify(sweep))

    def _new_pairs(snapshot_id: int | None, *, record: bool) -> dict[str, Any]:
        """The "New" flag, anchored on the snapshot rather than a browser tab.

        BUG-040. Two routes share this body because the two verbs answer the
        same question and only one of them may change anything. It used to be
        one GET with `record=True` as its DEFAULT, so reading the endpoint
        advanced the comparison point — I recorded snapshot 6387 into a
        previously empty registry on 2026-09-05 by calling it to look at its
        fields, which is how this was found.

        A GET that writes is not merely untidy here. HTTP promises that GET is
        safe and repeatable, and every layer in front of it believes that:
        TanStack Query retries failed GETs and refetches on window focus, so
        the React scanner would have advanced the registry every time Chandan
        tabbed back to the browser, with no user action behind it. Proxies and
        prefetchers make the same assumption.

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

    @router.get("/cards", summary="Mission Control cards: approaching, likely next")
    def cards(snapshot_id: int | None = Query(None),
              cap: int = Query(computed.MC_HISTORY_CAP, ge=1, le=100,
                               description="How many candidates get Phase B "
                                           "history. Cost scales with this.")
              ) -> dict[str, Any]:
        """The cards above the Scanner table, which views/scanner.py calls
        "the strategy's whole point".

        NOT the whole panel. The registry-backed non-ATM grid is still only in
        services/mission_control.py (DEBT-041), so a front end built on this
        endpoint alone has the Approaching and Likely Next grids and not that
        one. Said here rather than only in the plan, because the gap is
        invisible from the response — it looks like a complete answer.

        Phase B reads per-candidate history, so this is the expensive endpoint
        of the three. It shares the snapshot-keyed sweep with /mission/scan
        rather than recomputing 21 offsets.
        """
        target, chain, spot = _chain_and_spot(snapshot_id)
        sweep = ctx.cached(("sweep", target),
                           lambda: computed.scan(chain, spot, target))
        panel = ctx.cached(
            ("cards", target, cap),
            lambda: computed.approaching_panel(sweep, db_path=ctx.db_path,
                                               cap=cap))
        return {"snapshot_id": target, "spot": spot, **panel}

    @router.get("/new", summary="Pairs eligible now that were not before")
    def new_pairs(snapshot_id: int | None = Query(None)) -> dict[str, Any]:
        """Look without advancing the comparison point. Never writes.

        Safe to retry, prefetch and refetch. Because it does not record, the
        NEXT caller still compares against the same older snapshot, so calling
        this repeatedly keeps returning the same pairs as new — which is the
        honest behaviour for a question that changes nothing.
        """
        return _new_pairs(snapshot_id, record=False)

    @router.post("/new/record",
                 summary="Record eligibility now, advancing the comparison point")
    def record_new_pairs(snapshot_id: int | None = Query(None)) -> dict[str, Any]:
        """The one write in this package, now behind the verb that means it.

        Returns the same payload as the GET. It has to be called by something,
        or the comparison point never moves and every pair reads as new
        forever — but it must be called deliberately, which is the entire
        point of the split.
        """
        return _new_pairs(snapshot_id, record=True)

    @router.get("/gamma", summary=
                "Gamma, vanna or charm exposure by strike, and the flip level")
    def gamma(
        snapshot_id: int | None = Query(None),
        expiry: list[str] | None = Query(None, description=
            "Display key. Repeat the parameter to combine several — the "
            "expiry picker lets a trader tick more than one, and their "
            "exposures add. Omit for the whole board. A single "
            "`?expiry=2026-09-18` behaves exactly as it did before."),
        measure: str = Query("gamma", description=
            "gamma (default), vgex, delta, vanna or charm — one per view on "
            "the "
            "Gamma Exposure tab. The default keeps existing callers "
            "unchanged."),
    ) -> dict[str, Any]:
        """One address for the tab's strike panel, three measures.

        WHY A PARAMETER AND NOT THREE ROUTES. They answer the same question —
        "what does the dealer's book look like across these strikes" — and
        differ only in which derivative of delta is being weighted. Three
        addresses would make the caller choose one before knowing which, and
        the tab switches between them with a dropdown.

        THE DEFAULT IS LOAD-BEARING. `measure=gamma` reproduces exactly what
        this endpoint returned before 2026-09-06, columns and all, so nothing
        already reading it has to know this parameter exists.

        VANNA AND CHARM ARE FRONT-MONTH FIGURES and the caller must say so on
        screen. Vega lives in long-dated options and this record stops at ~28
        days, so these will not agree with a vendor's market-wide VEX and the
        difference is the data, not the arithmetic. core/gex.py carries the
        full statement of that limitation; the response carries the
        assumptions each figure rests on.
        """
        if measure not in computed.GAMMA_MEASURES:
            # 422 rather than a silent fall back to gamma: a client that asked
            # for charm and was quietly handed gamma would draw the wrong
            # chart under the right title, which is the failure this whole
            # endpoint's echoing of `measure` and `expiry` exists to prevent.
            raise HTTPException(
                status_code=422,
                detail=f"measure must be one of "
                       f"{', '.join(computed.GAMMA_MEASURES)}; got {measure!r}",
            )

        target, chain, spot = _chain_and_spot(snapshot_id)
        # THE SELECTOR'S OPTIONS TRAVEL WITH EVERY ANSWER. `expiry` is a
        # display key and there was no way to discover the valid ones short of
        # pulling the whole chain. Returned on all four measures because the
        # control is on screen whichever view is selected, and it is a
        # twenty-row list off a frame already in memory.
        anchor = _countdown_anchor(target, chain)
        # THE ANCHOR IS IN THE KEY. Without it the first request of the day
        # freezes the countdowns into the cache and every later reader is
        # served yesterday's numbers under today's date — the exact failure
        # this change exists to fix, reintroduced one layer up and harder to
        # see, because the endpoint would be computing the right answer and
        # then declining to use it.
        expiries = ctx.cached(
            ("expiry-board", target, anchor),
            lambda: computed.expiry_board(chain, spot, today=anchor))
        # The filter names travel with the board they filter. "This OpEx
        # Cycle" is a rule with a wording, and both belong to whoever owns
        # the rule; a hard-coded list in the tab would be a second place to
        # edit when a window is added, and would drift silently if it wasn't.
        expiry_filters = [{"key": k, "label": label}
                          for k, label in contract.EXPIRY_FILTERS]
        # WHICH EXPIRY THE TAB OPENS ON, decided here because it is a clock
        # rule. See core.expiry.default_scope: a browser comparing 8 PM
        # against its own timezone would roll the default forward five hours
        # early in London and say nothing about it.
        #
        # NOT CACHED, and that is the point of computing it out here: the
        # answer changes at 8 PM while the snapshot underneath it does not,
        # so a value keyed on the session date alone would still be saying
        # "0 DTE" at midnight.
        default_expiry = expiry_rules.default_scope(
            expiries, _dt.datetime.now(_dt.UTC),
            computed.snapshot_date(chain) or _dt.date.fromisoformat(target))
        # A LIST IS NOT HASHABLE and every lookup below is a cache key. The
        # tuple is also ORDER-SENSITIVE, so ticking A then B and B then A are
        # two entries for one answer — a little duplicated work, and much
        # better than sorting here and pretending the caller's order carried
        # meaning it did not.
        scope = tuple(expiry) if expiry else None

        if measure == "vgex":
            # Placed BEFORE the second-order branch and given its own block
            # rather than folded into the gamma one below: it shares gamma's
            # column names on purpose (so the panel needs no new code), and
            # that is exactly why the two must not share a cache key. One
            # entry serving both would hand a caller the open-interest figures
            # under the volume label, which is the failure this endpoint's
            # echoing of `measure` exists to prevent.
            result = ctx.cached(
                ("vgex", target, scope),
                lambda: computed.volume_gamma_exposure(chain, spot, scope))
            frame = result["by_strike"]
            rest = {k: v for k, v in result.items() if k != "by_strike"}
            return serialize.frame_payload(
                frame, snapshot_id=target, expiries=expiries,
                expiry_filters=expiry_filters,
                default_expiry=default_expiry,
                labels=computed.exposure_labels(result["summary"]),
                ticks=computed.axis_ticks(frame,
                                          ["call_gex", "put_gex", "net_gex"]),
                **rest)

        if measure == "delta":
            result = ctx.cached(
                ("delta", target, scope),
                lambda: computed.delta_exposure(chain, spot, scope))
            frame = result["by_strike"]
            rest = {k: v for k, v in result.items() if k != "by_strike"}
            return serialize.frame_payload(
                frame, snapshot_id=target, expiries=expiries,
                expiry_filters=expiry_filters,
                default_expiry=default_expiry,
                ticks=computed.axis_ticks(frame, ["call_dex", "put_dex"]),
                **rest)

        if measure != "gamma":
            row = db.get_snapshot_by_id(ctx.db_path, target)
            snapshot_ts = row["snapshot_timestamp"]
            second = ctx.cached(
                ("second-order", target, scope, measure),
                lambda: computed.second_order_exposure(
                    chain, spot, measure, snapshot_ts,
                    r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
                    display_tz=config.DISPLAY_TIMEZONE, expiry=scope))
            frame = second["by_strike"]
            cols = (["call_vex", "put_vex"] if measure == "vanna"
                    else ["call_cex", "put_cex"])
            rest = {k: v for k, v in second.items() if k != "by_strike"}
            return serialize.frame_payload(
                frame, snapshot_id=target, expiries=expiries,
                expiry_filters=expiry_filters,
                default_expiry=default_expiry,
                labels=computed.exposure_labels(second["summary"]),
                ticks=computed.axis_ticks(frame, cols),
                **rest)

        result = ctx.cached(
            ("gamma", target, scope),
            lambda: computed.gamma_exposure(chain, spot, scope))
        # COPY, DO NOT POP. `ctx.cached` returns the SAME dict on every hit,
        # so mutating it here emptied the cache entry the first time through
        # and raised KeyError on the second request in the same process.
        frame = result["by_strike"]
        rest = {"measure": "gamma",
                **{k: v for k, v in result.items() if k != "by_strike"}}
        # The gamma panel's axis spans whichever of the three gamma views is
        # drawn — call/put, absolute, or net — so it is placed over all three
        # columns. An axis that moved when the view changed would make two
        # views of one snapshot look like two different days.
        return serialize.frame_payload(
            frame, snapshot_id=target, expiries=expiries,
                expiry_filters=expiry_filters,
                default_expiry=default_expiry,
            labels=computed.exposure_labels(result["summary"]),
            ticks=computed.axis_ticks(frame,
                                      ["call_gex", "put_gex", "net_gex"]),
            **rest)

    @router.get("/non-atm",
                summary="The curated non-ATM opportunities panel (DEBT-041)")
    def non_atm(
        snapshot_id: int | None = Query(None),
        lookback: int = Query(1, ge=1, le=_MAX_DAYS, description=
            "How many SESSIONS ON RECORD to look back over, not calendar "
            "days. The dashboard offers 1, 5, 10 and 20."),
        cap: int = Query(computed.MC_HISTORY_CAP, ge=1, le=100),
    ) -> dict[str, Any]:
        """The third card grid, and the one that closes DEBT-041.

        WHAT MAKES THIS DIFFERENT FROM /mission/cards. Those cards are a slice
        of the current sweep — everything they say is computable from today's
        chain. These are built from the PERSISTED REGISTRY, which is how a
        card can say "seen 4x" or surface a setup that was live an hour ago
        while nobody was watching. That is also why this endpoint needed the
        server to learn where the sidecar files are (see create_app).

        THE REGISTRY IS READ, NEVER WRITTEN, and the consequence is worth
        knowing before building on this: it only advances while the STREAMLIT
        dashboard is running, because the upsert fires inside that page's
        snapshot-cached core. With the page closed, this endpoint keeps
        answering — truthfully — from a registry that stopped moving when the
        page did. `registry_entries` is in the response so a caller can see
        the size of what it was answered from rather than having to guess.
        DEBT-042 records the decision of where that upsert should eventually
        live; adding a write here would have been a second exception to a
        read-only package, which is a decision and not a detail.

        `lookback` counts SESSIONS ON RECORD, not calendar days — BUG-035.
        Subtracting days made "20D" fifteen sessions on a Friday while the
        reader was told twenty, and this panel shares one label with the
        charts beneath it on the page, so both must mean one window.
        """
        target, chain, spot = _chain_and_spot(snapshot_id)
        row = db.get_snapshot_by_id(ctx.db_path, target)
        snapshot_ts = row["snapshot_timestamp"]

        sweep = ctx.cached(("sweep", target),
                           lambda: computed.scan(chain, spot, target))
        # The same split the page makes: a combo whose put and call strikes
        # are equal is at the money, and this panel is the OTHER one.
        non_atm_current = (
            sweep[sweep["Put Strike"] != sweep["Call Strike"]].copy()
            if not sweep.empty else sweep
        )

        # CACHED ON THE REGISTRY FILE AS WELL AS THE SNAPSHOT, and the second
        # half of that key is not decoration. Measured on the live record
        # 2026-09-06: the build walks all 1,288 registry entries and produces
        # a card for each of the 898 inside a 20-session window before capping
        # to 20, which is 0.4-1.0s of pure pandas and dict work, plus twenty
        # per-candidate history queries. Uncached, this endpoint answered in
        # 1.67s EVERY time while /mission/cards beside it answered in 0.085s.
        # ENH-012 records the same measurement and the same fix on the page.
        #
        # The snapshot alone would be the wrong key. The registry is rewritten
        # by a DIFFERENT process — the Streamlit page's snapshot-cached core —
        # so it moves without the snapshot moving, and freezes for days across
        # many snapshots when that page is never opened. Keying on both means
        # an answer is kept for exactly as long as everything it was built
        # from has held still. The fingerprint is one stat() (state/store.py),
        # not a hash of 700 KB.
        stamp = eligible_history.fingerprint(ctx.state_dir)

        def _build() -> dict[str, Any]:
            registry = eligible_history.load(ctx.state_dir)
            dte_by_expiry = (chain.groupby("expiry")["dte"].first()
                             .astype(int).to_dict())
            panel = computed.non_atm_panel(
                non_atm_current, registry, dte_by_expiry,
                db.session_window_start(ctx.db_path, lookback),
                snapshot_ts, db_path=ctx.db_path, cap=cap,
            )
            # Carried in the cached value rather than counted again outside
            # it: the count has to describe the registry this panel was built
            # from, and re-reading the file to count it could report a
            # different one.
            return {"registry_entries": len(registry), **panel}

        panel = ctx.cached(("non-atm", target, lookback, cap, stamp), _build)
        return {"snapshot_id": target, "spot": spot, "lookback": lookback,
                **panel}

    return router
