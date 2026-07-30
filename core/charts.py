"""Chart geometry — the shape and colour of a line, decided from data alone.

These build plotly objects but draw nothing and read nothing: they take numbers
and return traces or a reshaped frame. Pinned by tests/test_chart_breaks.py and
tests/test_display_golden.py.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# NOTE (2026-07-07): Holidays are deliberately NOT collapsed by rangebreaks.
# Empirically isolated by toggling breaks one at a time: ANY per-date
# rangebreak -- `values=[dates]` AND per-day `bounds=[date, date]` variants
# were both tested -- corrupts Plotly's point positioning for all data after
# the break (ghost/duplicate lines, out-of-order hover, dead tooltips) the
# moment a holiday falls inside the viewed window (first hit: 2026-07-03).
# Only the weekday-name and hour-pattern bounds below are safe. A holiday
# therefore shows as one session-width of honest blank space, with the line
# cleanly broken across it by break_sessions().
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
    read layer, so anything else reading that data inherited it — fine while
    every consumer was a chart, wrong the moment one is not (M4's data
    service, M7's models, where a time with no zone is ambiguous).

    So the read layer now returns zoned UTC, and the stripping happens here,
    once, at the last moment before drawing.

    WHY STRIP AT ALL. Plotly's rangebreaks — the thing that collapses nights
    and weekends — mis-place points when handed zoned timestamps. The naive
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


_RATIO_THRESHOLDS = [0.70, 1.00, 1.30]
_RATIO_BANDS = [
    (1.30, float("inf"), "#1abc9c", "Strong backwardation (≥1.30)"),
    (1.00, 1.30,         "#2ecc71", "Backwardation 1.00–1.30 (front rich)"),
    (0.70, 1.00,         "#8e9bb5", "Contango 0.70–1.00 (normal)"),
    (float("-inf"), 0.70, "#d98841", "Deep contango <0.70 (likely 0DTE/EOD)"),
]


def banded_ratio_traces(x, y) -> list:
    """Build a continuous multicolor line for the IV ratio, colored by regime."""
    xs, ys = list(x), list(y)
    ax, ay = [], []
    for i in range(len(xs)):
        ax.append(xs[i])
        ay.append(ys[i])
        if i + 1 < len(xs):
            y0, y1, x0, x1 = ys[i], ys[i + 1], xs[i], xs[i + 1]
            if pd.isna(y0) or pd.isna(y1) or y0 == y1:
                continue
            crossed = [t for t in _RATIO_THRESHOLDS
                       if (y0 < t < y1) or (y1 < t < y0)]
            crossed.sort(reverse=(y0 > y1))
            for t in crossed:
                frac = (t - y0) / (y1 - y0)
                ax.append(x0 + (x1 - x0) * frac)
                ay.append(t)
    traces = []
    for low, high, color, label in _RATIO_BANDS:
        yb = [v if (v is not None and not pd.isna(v) and low <= v <= high)
              else None for v in ay]
        if any(v is not None for v in yb):
            traces.append(go.Scatter(
                x=ax, y=yb, mode="lines", name=label,
                line=dict(color=color, width=2), connectgaps=False,
                legendgroup=label,
                hovertemplate="R=%{y:.4f}<extra></extra>",
            ))
    return traces
