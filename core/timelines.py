"""The "through the session" panels: a handful of strikes, traced across time.

TWO CHARTS, ONE SHAPE. Both pick a few strikes out of a board of a hundred and
draw one line each through the day — net volume on one, 0DTE net gamma on the
other. Drawn with every strike they are a solid block of colour, so the
selection is not a nicety: it is what makes the panel legible at all.

WHY THIS IS A MODULE AND NOT TWO FIGURES. Both of these lived inside
`views/gex.py` as part of the plotly figure that drew them — `net_volume =
call_volume - put_volume` on one line, `nlargest(8)` on the next, the dollar
scaling of gamma on a third. That is precisely the arrangement
docs/m6_migration_plan.md exists to end: a formula reachable only by rendering
a chart cannot be tested, cannot be served to a second front end, and quietly
becomes a second definition the moment anyone needs the same number elsewhere.
The React tab needs exactly these numbers, so they move here first.

THE TWO SELECTION RULES DIFFER, AND THE DIFFERENCE IS DELIBERATE. Net volume
ranks by the largest reading a strike reached AT ANY POINT in the day, because
a strike that was hammered at the open and went quiet is part of the day's
story. Net GEX ranks by the position RIGHT NOW, because that panel is about
where gamma is sitting going into the close. Collapsing them into one rule
would quietly change what one of the two charts is about.

TIMESTAMPS COME BACK IN MARKET TIME, and that is not a nicety. The read layer
hands out zoned UTC, and a browser handed "13:30+00:00" draws a session that
appears to start in the afternoon and run to eight at night — which is exactly
what these two panels did until they were told the zone. Plotly reads the
WALL-CLOCK part of an ISO string and ignores the offset, so the conversion has
to happen before the string is made, and it has to happen HERE rather than in
the browser: `toLocaleString` in a browser resolves in the VIEWER's timezone,
which is a chart that is right for one reader and an hour wrong for the next
(DEBT-030). core/flow.py already did this; these did not.

PURE, AND HANDED EVERYTHING — no config, no clock, no plotly. `display_tz` is
passed in for the same reason flow.py takes it: the zone is a deployment
decision, and a module that reads config cannot be tested against another one.
The session is whatever frame arrives; scoping is the query's job, as it is
everywhere else in core/.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

from core import gex

VOLUME_COLUMNS = ["timestamp", "strike", "net_volume", "call_volume",
                  "put_volume"]
GEX_COLUMNS = ["timestamp", "strike", "net_gex"]

#: How many strikes each panel draws. Eight lines is about where a legend
#: stops being readable; the 0DTE panel takes one fewer because its lines
#: cross far more often, which costs more legibility per line.
VOLUME_LINES = 8
GEX_LINES = 7


def _per_snapshot(intraday: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Sum the given columns within (timestamp, strike).

    A STRIKE LISTS SEVERAL EXPIRIES and these panels draw their total. Ranking
    the unaggregated rows gives one expiry's figure under the strike's name.

    THE CURRENT CALLER DOES NOT NEED THIS — `db.get_intraday_strike_metrics`
    already does `GROUP BY snapshot_id, strike`, so on that frame this is a
    no-op and no test can show it doing anything. It stays for the same reason
    core/flow.py keeps its sort: the guarantee lives in a SQL string two layers
    away, and the failure if it ever changes is not an error but a plausible
    wrong number. It costs one pass over ~13,000 rows.
    """
    return (intraday.groupby(["timestamp", "strike"], as_index=False)[columns]
                    .sum())


def _in_market_time(frame: pd.DataFrame, display_tz: str) -> pd.DataFrame:
    """The timestamp column, converted from zoned UTC to the market's zone.

    CONVERTED, NOT STRIPPED. The offset stays on the value, so anything
    reading the frame still knows which zone it is looking at; what changes is
    the wall clock, which is the part a chart axis draws.
    """
    out = frame.copy()
    out["timestamp"] = out["timestamp"].dt.tz_convert(ZoneInfo(display_tz))
    return out


def net_volume_by_strike(intraday: pd.DataFrame, *, display_tz: str,
                         strikes: list[float] | None = None,
                         count: int = VOLUME_LINES) -> pd.DataFrame:
    """Calls traded minus puts traded at each strike, through the session.

    NO CUMULATIVE SUM OF ITS OWN. The broker's `volume` is already a running
    total for the session, so the accumulation is what the field IS. What this
    adds is the shape of it: which strikes were worked early, which came alive
    after lunch, and where the call side handed over to the put side.

    `strikes` narrows to the strikes the bars above are drawn at, so the panel
    cannot answer about a strike the reader cannot see. None means the board.

    The `count` strikes kept are those whose net volume reached the largest
    ABSOLUTE reading at any point in the day — see the module docstring for why
    that is not the same rule the gamma panel uses.

    Returns tidy rows, one per strike per snapshot, sorted by strike then time
    so a caller can group without re-sorting. Empty in, empty out — with
    columns.
    """
    needed = {"timestamp", "strike", "call_volume", "put_volume"}
    if intraday is None or intraday.empty or not needed.issubset(intraday.columns):
        return pd.DataFrame(columns=VOLUME_COLUMNS)

    work = intraday
    if strikes is not None:
        work = work[work["strike"].isin(strikes)]
    if work.empty:
        return pd.DataFrame(columns=VOLUME_COLUMNS)

    work = _per_snapshot(work, ["call_volume", "put_volume"])
    work["net_volume"] = work["call_volume"] - work["put_volume"]

    keep = (work.groupby("strike")["net_volume"]
                .apply(lambda s: s.abs().max())
                .nlargest(count).index)
    work = work[work["strike"].isin(keep)]
    return _in_market_time(
        work.sort_values(["strike", "timestamp"], ignore_index=True),
        display_tz)[VOLUME_COLUMNS]


def gex_by_strike_over_time(intraday: pd.DataFrame, *, display_tz: str,
                            count: int = GEX_LINES) -> pd.DataFrame:
    """Net gamma exposure at each strike, through the session.

    Fed the 0DTE slice this is the "0DTE flow" panel: the fastest-moving
    options on the board, traced through the day instead of frozen at this
    moment.

    EACH SNAPSHOT IS SCALED BY ITS OWN SPOT, for the reason `gex.net_flow_by_
    strike` gives: `dollar_scale` is quadratic in spot, so scaling this
    morning's reading by this afternoon's price would fold the index's own move
    into every line and tilt the whole chart on a trending day.

    The `count` strikes kept are those carrying the most absolute net gamma AT
    THE LATEST SNAPSHOT — where gamma is sitting now, not where it has been.

    Returns tidy rows, sorted by strike then time. Empty in, empty out.
    """
    needed = {"timestamp", "strike", "call_gamma_oi", "put_gamma_oi",
              "underlying_price"}
    if intraday is None or intraday.empty or not needed.issubset(intraday.columns):
        return pd.DataFrame(columns=GEX_COLUMNS)

    work = intraday.copy()
    scale = work["underlying_price"].map(gex.dollar_scale)
    work["net_gex"] = (work["call_gamma_oi"] - work["put_gamma_oi"]) * scale
    work = _per_snapshot(work, ["net_gex"])

    latest = work[work["timestamp"] == work["timestamp"].max()]
    keep = latest.assign(mag=latest["net_gex"].abs()).nlargest(count, "mag")["strike"]
    work = work[work["strike"].isin(keep)]
    return _in_market_time(
        work.sort_values(["strike", "timestamp"], ignore_index=True),
        display_tz)[GEX_COLUMNS]


def gex_totals(lines: pd.DataFrame) -> dict:
    """The headline figures above the gamma panel: now, at the open, and the
    change between them.

    COMPUTED FROM THE LINES THAT ARE DRAWN, not from the board, and that is
    the point of it living here. Taken separately, the strip and the chart
    answer slightly different questions and eventually disagree by a number
    nobody can account for.

    All three are None on an empty frame — "no reading", which is not zero.
    """
    if lines is None or lines.empty:
        return {"now": None, "at_open": None, "change": None,
                "levels": []}
    now = float(lines[lines["timestamp"] == lines["timestamp"].max()]["net_gex"].sum())
    then = float(lines[lines["timestamp"] == lines["timestamp"].min()]["net_gex"].sum())
    return {
        "now": now,
        "at_open": then,
        "change": now - then,
        # Sorted so the caption reads low to high rather than in whatever
        # order the ranking happened to produce.
        "levels": sorted(float(s) for s in lines["strike"].unique()),
    }
