"""Number and duration formatting — the last step before a value reaches the eye.

Pure string production: same input, same output, always. Pinned by
tests/test_display_golden.py.
"""
from __future__ import annotations

import pandas as pd

SPARK_BARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 10) -> str:
    if not values:
        return "─"
    step = max(1, len(values) // width)
    sampled = values[::step][-width:]
    mn, mx = min(sampled), max(sampled)
    if mx == mn:
        return SPARK_BARS[3] * len(sampled)
    return "".join(
        SPARK_BARS[int((v - mn) / (mx - mn) * 7)] for v in sampled
    )


def fmt_duration(td) -> str:
    """Format a pandas/python timedelta as '2h 12m' / '47m' / '8m'."""
    if td is None or pd.isna(td):
        return "—"
    total_min = int(td.total_seconds() // 60)
    if total_min < 1:
        return "<1m"
    h, m = divmod(total_min, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def fmt_eta(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"~{int(round(minutes))} min"
    h = minutes / 60.0
    return f"~{h:.1f} hr"


def exp_label(expiry: str, dte_by_expiry: dict) -> str:
    """Pretty expiry label, e.g. "Friday, Aug 21, 2026  (23 DTE)".

    Moved out of app.py in M2 step 2.5 and de-underscored on the way, the
    same order DEBT-028 used for core/'s other names. It is pure string
    production over two arguments — exactly what this module is for — and
    it had two consumers left in app.py and one in the Mission Control
    pipeline, so it could not stay with either.

    dte_by_expiry — DEBT-027 site 2, fixed in M2 (ADR-034). This used to read a
    module global of the same name while its ONLY caller checked membership
    against the parameter it had been handed. Identical objects in production,
    so it worked; the day they differed, the guard would pass and the lookup
    return nothing, dropping "(N DTE)" from the label with no error anywhere.
    """
    d = dte_by_expiry.get(expiry)
    try:
        dt = pd.Timestamp(expiry)
        pretty = dt.strftime("%A, %b ") + str(dt.day) + dt.strftime(", %Y")
    except (ValueError, TypeError):
        pretty = expiry
    return f"{pretty}  ({d} DTE)" if d is not None else pretty
