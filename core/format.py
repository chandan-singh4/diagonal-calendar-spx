"""Number and duration formatting — the last step before a value reaches the eye.

Pure string production: same input, same output, always. Pinned by
tests/test_display_golden.py.
"""
from __future__ import annotations

import pandas as pd

_SPARK_BARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float], width: int = 10) -> str:
    if not values:
        return "─"
    step = max(1, len(values) // width)
    sampled = values[::step][-width:]
    mn, mx = min(sampled), max(sampled)
    if mx == mn:
        return _SPARK_BARS[3] * len(sampled)
    return "".join(
        _SPARK_BARS[int((v - mn) / (mx - mn) * 7)] for v in sampled
    )


def _fmt_duration(td) -> str:
    """Format a pandas/python timedelta as '2h 12m' / '47m' / '8m'."""
    if td is None or pd.isna(td):
        return "—"
    total_min = int(td.total_seconds() // 60)
    if total_min < 1:
        return "<1m"
    h, m = divmod(total_min, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _fmt_eta(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"~{int(round(minutes))} min"
    h = minutes / 60.0
    return f"~{h:.1f} hr"
