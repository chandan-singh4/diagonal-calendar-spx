"""Chart geometry — the shape and colour of a line, decided from data alone.

These build plotly objects but draw nothing and read nothing: they take numbers
and return traces or a reshaped frame. Pinned by tests/test_chart_breaks.py and
tests/test_display_golden.py.
"""
from __future__ import annotations


import pandas as pd

import plotly.graph_objects as go

# Re-exported, not redefined. These three moved to `core/series.py` so the
# read-only API could use them without importing plotly (see that module's
# docstring); they are still imported from here by the views and by
# tests/test_chart_breaks.py and tests/test_display_golden.py, which is why
# the names stay available at this address.
from core.series import (  # noqa: F401
    RATIO_BANDS,
    RATIO_THRESHOLDS,
    SESSION_RANGEBREAKS,
    break_sessions,
    to_display_time,
)


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
            crossed = [t for t in RATIO_THRESHOLDS
                       if (y0 < t < y1) or (y1 < t < y0)]
            crossed.sort(reverse=(y0 > y1))
            for t in crossed:
                frac = (t - y0) / (y1 - y0)
                ax.append(x0 + (x1 - x0) * frac)
                ay.append(t)
    traces = []
    for low, high, color, label in RATIO_BANDS:
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
