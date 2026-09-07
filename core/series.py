"""Time-series reshaping for display — no plotly, no config, no clock.

SPLIT OUT OF `core/charts.py` SO THE READ-ONLY SERVER CAN REACH IT. Everything
here is pure pandas, but it used to live beside `plotly.graph_objects`, and
importing it would have pulled a charting library into the API process for the
sake of two functions that build no chart. `core/format.money_ticks` moved for
exactly this reason; this is the same move for the same reason.

`core/charts.py` re-exports all three names, so callers that always imported
them from there still work and the tests that pin them did not have to move.
"""
from __future__ import annotations

import pandas as pd

# NOTE (2026-07-07): Holidays are deliberately NOT collapsed by rangebreaks.
# Empirically isolated by toggling breaks one at a time: ANY per-date
# rangebreak -- `values=[dates]` AND per-day `bounds=[date, date]` variants
# were both tested -- corrupts Plotly's point positioning for all data after
# the break (ghost/duplicate lines, out-of-order hover, dead tooltips) the
# moment a holiday falls inside the viewed window (first hit: 2026-07-03).
# Only the weekday-name and hour-pattern bounds below are safe. A holiday
# therefore shows as one session-width of honest blank space, with the line
# cleanly broken across it by break_sessions().
#
# SERVED TO THE BROWSER AS WELL AS USED HERE. The React charts need the same
# breaks, and this comment is the whole reason they are these two and not
# others -- a second copy written from scratch in TypeScript would be a copy
# of the conclusion without the evidence, and the first person to "fix" it
# would reintroduce a bug that took a day of bisecting to find.
SESSION_RANGEBREAKS = [
    dict(bounds=["sat", "mon"]),
    dict(bounds=[16, 9.5], pattern="hour"),
]


def to_display_time(df: pd.DataFrame, display_tz: str,
                    ts_col: str = "timestamp") -> pd.DataFrame:
    """Turn stored UTC into the naive local wall-clock the charts require.

    THE DEBT-030 FIX LIVES HERE. `dataaccess/` used to end every timestamp
    with `.dt.tz_localize(None)`, handing out a bare "14:30" with nothing
    saying where. That is a DISPLAY decision, and it was being taken in the
    read layer, so anything else reading that data inherited it -- fine while
    every consumer was a chart, wrong the moment one is not (M4's data
    service, M7's models, where a time with no zone is ambiguous).

    So the read layer now returns zoned UTC, and the stripping happens here,
    once, at the last moment before drawing.

    WHY STRIP AT ALL. Plotly's rangebreaks -- the thing that collapses nights
    and weekends -- mis-place points when handed zoned timestamps. The naive
    value is a genuine requirement of the chart, not laziness. It is simply
    the chart's business, not the database's.

    `display_tz` is passed in rather than read from config: this is core/,
    and core/ is handed what it needs (see tests/test_layering.py).

    Returns a COPY. Callers hold frames that came out of a Streamlit memo,
    and mutating one of those in place would corrupt the cached object for
    every later reader.
    """
    if df.empty or ts_col not in df.columns:
        return df
    out = df.copy()
    ts = pd.to_datetime(out[ts_col], utc=True)
    out[ts_col] = ts.dt.tz_convert(display_tz).dt.tz_localize(None)
    return out


def break_sessions(df: pd.DataFrame, ts_col: str = "timestamp",
                   max_gap_minutes: int = 60) -> pd.DataFrame:
    """Insert a NaN row wherever consecutive points gap more than
    max_gap_minutes, so Plotly breaks the line instead of drawing a
    connector across holidays/weekends/collector outages. Rangebreaks
    (SESSION_RANGEBREAKS) collapse the empty axis SPACE; this handles the
    LINE across it -- they're complementary, not redundant."""
    if df.empty or len(df) < 2 or ts_col not in df.columns:
        return df
    ts = df[ts_col]
    gap = ts.diff() > pd.Timedelta(minutes=max_gap_minutes)
    if not gap.any():
        return df
    breakers = df.loc[gap, [ts_col]].copy()
    breakers[ts_col] = ts.shift(1)[gap] + pd.Timedelta(minutes=1)
    return (pd.concat([df, breakers], ignore_index=True)
            .sort_values(ts_col, kind="stable")
            .reset_index(drop=True))


# The IV-ratio regimes, and the boundaries between them.
#
# MOVED HERE FROM `core/charts.py` SO BOTH SCREENS READ ONE TABLE. These four
# bands are a claim about the market -- where backwardation starts, and that
# below 0.70 is nearly always an artefact of same-day options decaying rather
# than a signal -- and the Calendar Edge tab prints that claim as prose under
# the chart. A second copy in TypeScript would be a second opinion about what
# a regime is, and the two would be edited on different days.
#
# `banded_ratio_traces` in core/charts.py still builds the plotly traces from
# these; only the numbers and their names moved.
RATIO_THRESHOLDS = [0.70, 1.00, 1.30]
RATIO_BANDS = [
    (1.30, float("inf"), "#1abc9c", "Strong backwardation (≥1.30)"),
    (1.00, 1.30,         "#2ecc71", "Backwardation 1.00–1.30 (front rich)"),
    (0.70, 1.00,         "#8e9bb5", "Contango 0.70–1.00 (normal)"),
    (float("-inf"), 0.70, "#d98841", "Deep contango <0.70 (likely 0DTE/EOD)"),
]


def ratio_bands() -> list[dict]:
    """`RATIO_BANDS` as JSON-safe dicts, for serving.

    The infinities become None. JSON has no infinity -- Python's json module
    will happily write the bare token `Infinity`, which is not valid JSON and
    which some parsers accept and others reject, so the open ends are stated
    as "no bound" instead of as a number no reader can compare against.
    """
    out = []
    for low, high, colour, label in RATIO_BANDS:
        out.append({
            "low": None if low == float("-inf") else low,
            "high": None if high == float("inf") else high,
            "colour": colour,
            "label": label,
        })
    return out


def merge_iv_pair(front_df, back_df, display_tz: str, *, value_col: str = "atm_iv",
                  names: tuple[str, str, str] = ("front_iv", "back_iv", "iv_ratio"),
                  insert_breaks: bool = True):
    """Front and back IV on one row per shared timestamp, with the ratio.

    USED FOR BOTH THE EXPIRY-LEVEL AND THE PER-STRIKE SERIES. The Calendar
    Edge tab joins two ATM histories (`atm_iv`); the Strike Detail tab joins
    two contract histories (`iv`) and does it twice, once per side. The join,
    the division and the session breaks are the same rule in all three cases,
    and it was written out three times before this took a column name.

    THE INNER JOIN IS THE POINT. Two expiries are polled independently and
    either can miss a snapshot; an outer join would put a front reading beside
    a back reading from a different minute and divide them, producing a ratio
    that never existed. Only timestamps where BOTH were observed can carry a
    ratio, so only those survive.

    `iv_ratio` is front over back -- above 1.00 the front is richer than the
    back, which is the whole thing the strategy waits for. See RATIO_BANDS for
    what the levels mean.

    Session breaks are applied here, after the join: a NaN row inserted before
    the merge would be dropped by the inner join and the line would join across
    the weekend again. `insert_breaks=False` skips them, for the callers that
    SUMMARISE this frame rather than drawing it -- the Historical Statistics
    windows take a min, a max and a percentile, and gap rows are noise in a
    frame that has no time axis to gap.

    Returns an empty frame if either side is empty -- one leg of a ratio is not
    half an answer.
    """
    front_name, back_name, ratio_name = names
    if front_df is None or back_df is None or front_df.empty or back_df.empty:
        return pd.DataFrame()
    front = to_display_time(front_df, display_tz)
    back = to_display_time(back_df, display_tz)
    merged = pd.merge(
        front[["timestamp", value_col]].rename(columns={value_col: front_name}),
        back[["timestamp", value_col]].rename(columns={value_col: back_name}),
        on="timestamp", how="inner",
    )
    if merged.empty:
        return merged
    # THE RATIO IS COMPUTED BEFORE THE BREAKS, and that order is BUG-002.
    # `break_sessions` inserts rows carrying NaN; done first, those rows would
    # be given a ratio of NaN/NaN anyway, but done after, they carry NaN in
    # the ratio column too and the ratio line breaks with the other two.
    # Reversed, the ratio draws a straight connector across a weekend and
    # invents IV movement that never happened.
    merged[ratio_name] = merged[front_name] / merged[back_name]
    return break_sessions(merged) if insert_breaks else merged


def merge_atm_pair(front_df, back_df, display_tz: str):
    """`merge_iv_pair` at the expiry level -- the Calendar Edge tab's frame.

    Kept as its own name because that tab and its tests read it, and because
    "the ATM pair" is what the column names below say.
    """
    return merge_iv_pair(front_df, back_df, display_tz, value_col="atm_iv")


def strike_crossings(timestamps, prices, strikes) -> dict[str, list]:
    """Where the underlying crossed one of the short strikes, and which way.

    A CROSSING IS A DIRECTED EVENT, not a comparison. `prev < k <= curr` is an
    up-cross and `prev > k >= curr` a down-cross; the closed side of each
    inequality is what stops a price sitting exactly on the strike from being
    reported as a crossing on every subsequent reading. Getting that boundary
    wrong produces a chart densely marked with events that did not happen,
    which looks like a volatile session rather than like a bug.

    Returns two lists of {x, y} points, ready to plot as markers. Timestamps
    are passed through as given -- naive local wall-clock by the time this is
    called, because it is a display question.

    NaN-safe: a gap row inserted by `break_sessions` compares false either
    way, so a weekend is never reported as a crossing.
    """
    ts = list(timestamps)
    px = list(prices)
    up: list[dict] = []
    down: list[dict] = []
    for k in strikes:
        k = float(k)
        for i in range(1, len(px)):
            a, b = px[i - 1], px[i]
            if a is None or b is None or a != a or b != b:  # NaN
                continue
            if a < k <= b:
                up.append({"x": ts[i], "y": k})
            elif a > k >= b:
                down.append({"x": ts[i], "y": k})
    return {"up": up, "down": down}


def hour_of_day(df, ts_col: str = "timestamp"):
    """Each row's time of day as a decimal hour -- 10:30 is 10.5.

    THE COLOUR AXIS OF THE INTRADAY SCATTER, and served rather than derived
    on the client for one reason: the timestamps leave here as naive local
    wall-clock (DEBT-030), and a browser parsing that string applies the
    VIEWER'S zone to it. A reading taken at 10:30 in New York would colour as
    15:30 for anyone sitting in London, and the plot would still look
    perfectly plausible. Doing the arithmetic beside the conversion keeps the
    hour and the timestamp the same fact.

    ONE DEFINITION, TWO CALLERS -- the scatter in `views/edge.py` and the
    served column in `/pairs/atm-pair`. NaT rows (the gaps `break_sessions`
    inserts) come back as NaN, which no marker is drawn for.
    """
    ts = df[ts_col]
    return ts.dt.hour + ts.dt.minute / 60.0


def scatter_domain(df, cols=("front_iv", "back_iv"), pad_fraction: float = 0.05):
    """The shared low/high the R=1 reference line is drawn across.

    ONE RANGE ACROSS BOTH AXES, WHICH IS THE WHOLE POINT OF THE CHART. The
    scatter asks whether front IV sat above or below back IV, and that
    question is only readable when the diagonal is at 45 degrees; letting the
    two axes scale independently would tilt the line and turn a visual
    comparison into a misleading one.

    Returns (lo, hi) already padded. The fallback pad of 1.0 covers a frame
    where every reading is identical -- a real state on a quiet contract, and
    a zero-width range Plotly cannot draw.
    """
    values = [df[c] for c in cols]
    lo = float(min(v.min() for v in values))
    hi = float(max(v.max() for v in values))
    pad = (hi - lo) * pad_fraction or 1.0
    return lo - pad, hi + pad


# The four windows the Historical Statistics panel compares.
#
# HERE RATHER THAN IN EITHER SCREEN, so the React tab cannot offer a window
# the page does not -- the exact drift the Scanner's lookback picker caused,
# where one screen could show a different answer than the other from the same
# snapshot and the reader had no way to tell which was right. `views/` may not
# import `api/` (test_layering), so a set both read has to live below both.
#
# IN SESSIONS ON RECORD, NOT CALENDAR DAYS. BUG-035 was precisely that
# distinction going wrong: "10D" drawing eight sessions because a holiday and
# a weekend were counted as trading days.
HISTORICAL_WINDOWS = [("Today", 1), ("5 Days", 5), ("10 Days", 10), ("20 Days", 20)]


# When the trading day starts, once. 09:30 ET is the cash open; the timestamps
# it is compared against are naive local wall-clock by the time they reach a
# chart (DEBT-030), so this is a naive time too and the two match without a
# conversion at the drawing site.
MARKET_OPEN_TIME = "09:30"


def market_open_lines(timestamps) -> list[str]:
    """A 09:30 marker for each trading day present, as ISO strings.

    WHY EACH DAY AND NOT EACH ROW. On a multi-session chart the session
    boundary is invisible: `break_sessions` leaves a gap, but a gap looks the
    same as a quiet stretch, and the reader cannot tell Thursday's close from
    Friday's open. The line says where the day turned over.

    ONE MARKER IS NO MARKER, which is why a single-day window returns
    nothing. On a Today view every reading is after the same 09:30 and the
    line would sit against the left edge of the chart, adding a mark and no
    information. `views/edge.py` skipped it for exactly this reason; the rule
    is here now so the React chart cannot decide differently.

    ONE DEFINITION, TWO CALLERS -- the vlines in `views/edge.py` and the
    served `market_opens` on `/pairs/transform-marks`. NaT rows (the gaps
    `break_sessions` inserts) contribute no date.
    """
    import pandas as pd

    if timestamps is None or len(timestamps) == 0:
        return []
    days = sorted(pd.to_datetime(pd.Series(list(timestamps))).dt.date.dropna().unique())
    if len(days) < 2:
        return []
    return [f"{day}T{MARKET_OPEN_TIME}:00" for day in days]
