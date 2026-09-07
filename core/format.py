"""Number and duration formatting — the last step before a value reaches the eye.

Pure string production: same input, same output, always. Pinned by
tests/test_display_golden.py.
"""
from __future__ import annotations

import math

import pandas as pd

from core import contract

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


def fmt_money(value: float | None, unit: str = "") -> str:
    """A large figure at a readable magnitude, or an em dash.

    MOVED HERE FROM views/gex.py ON 2026-09-06 so the API can label the
    headline figures with the same function the page labels them with. It was
    private to the view, and a React tab needs "$12.2B" as much as the
    Streamlit one does — picking a divisor and a suffix is a rounding rule,
    and this project keeps rounding rules in Python.

    NO CURRENCY MARK BY DEFAULT. Exposure is derived from a notional — gamma
    times open interest times a hundred times spot squared — so the units are
    real but the number is not money anybody holds or pays, and a "$" invites
    it to be read as one. The magnitude suffix is the part that matters.

    An em dash rather than 0: absent and zero are different states, and the
    project's rule is that a missing number shows blank.
    """
    if value is None or pd.isna(value):
        return "—"
    sign = "-" if value < 0 else ""
    mag = abs(value)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if mag >= cutoff:
            return f"{sign}{unit}{mag / cutoff:,.1f}{suffix}"
    return f"{sign}{unit}{mag:,.0f}"


def money_ticks(values, unit: str = "") -> dict:
    """Axis ticks labelled the way the headline numbers are labelled.

    MOVED HERE FROM views/gex.py ON 2026-09-06, alongside `fmt_money` above, so the
    API can serve the tick positions and the React tab's axis reads the same
    as the Streamlit one's. Placing ticks IS a formula, so it stays in Python
    and the answer travels; a second implementation in TypeScript would drift
    on exactly the values nobody checks.

    IT LIVES IN format.py AND NOT charts.py DESPITE THE PLOTLY-SHAPED KEYS.
    core/charts.py imports plotly; api/ calls this to serve tick positions,
    and a read-only server has no business loading a plotting library to
    label an axis it will never draw. Nothing here needs plotly — the key
    names are a convenience for the caller that does.

    Plotly's SI format writes a billion as "G" — correct for engineers, wrong
    for anyone reading a magnitude, and different from the "29.3B" in the
    strip directly above the chart. Two notations for one number on one screen
    is a reader's problem, not a formatting preference, so the ticks are placed
    here and labelled with `fmt_money` above.

    The step is the largest of 1/2/2.5/5 x 10^k that still leaves about six
    ticks across the range, and the range always includes zero: on these charts
    the sign is the message, so an axis that cropped zero out would hide it.

    Returns {} only when there is genuinely nothing to place: an empty series,
    or one whose range collapses to a point ONCE ZERO IS INCLUDED — which in
    practice means all-zero, since any non-zero constant still spans from zero
    to itself. A caller passes the result straight into a Plotly axis, where an
    empty dict correctly means "decide for yourself".
    """
    series = pd.Series(list(values), dtype="float64").dropna()
    if series.empty:
        return {}
    lo, hi = min(0.0, float(series.min())), max(0.0, float(series.max()))
    if hi == lo:
        return {}

    rough = (hi - lo) / 6.0
    power = 10.0 ** math.floor(math.log10(rough))
    step = next((m * power for m in (1.0, 2.0, 2.5, 5.0) if m * power >= rough),
                10.0 * power)

    first = math.floor(lo / step)
    ticks = [(first + i) * step for i in range(int((hi - lo) / step) + 3)]
    ticks = [t for t in ticks if lo - step <= t <= hi + step]
    return dict(tickmode="array", tickvals=ticks,
                ticktext=[fmt_money(t, unit) for t in ticks])


def peak_label(summary: dict) -> str:
    """The peak-gamma strike with the side that owns it: "7,720 (Put)".

    ONE DEFINITION, THREE CALLERS -- the header line in `core/market.py`, the
    headline strip in `views/gex.py`, and the served labels in
    `api/computed.exposure_labels`. It was written out by hand in the first
    two and I added a third copy in the third before noticing; a strike and a
    side pasted together is small enough that nobody would think to hunt for
    the other copies on the day the wording changes, which is exactly how two
    screens end up disagreeing about the same number.

    Returns "N/A" when there is no peak. That wording, not an em dash, because
    `core/market.py` puts this string in a header where "N/A" is already the
    house word for an absent figure; callers that want a dash substitute one.
    """
    if summary.get("peak_strike") is None:
        return "N/A"
    side = summary.get("peak_side")
    strike = f"{summary['peak_strike']:,.0f}"
    return f"{strike} ({side})" if side else strike


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

    `expiry` is a display key, not always a date: the third Friday's morning
    contract arrives as "2026-08-21 (AM)" (core/contract.py). It is rendered as
    "· AM settled" rather than left in brackets, because the label already ends
    in a bracketed "(N DTE)" and two bracketed suffixes side by side read as one
    muddle. The ordinary contract is left unmarked, which is the whole point of
    that naming direction: almost every expiry is the ordinary kind.
    """
    d = dte_by_expiry.get(expiry)
    expiry_date, settlement = contract.parse(expiry)
    try:
        dt = pd.Timestamp(expiry_date)
        pretty = dt.strftime("%A, %b ") + str(dt.day) + dt.strftime(", %Y")
    except (ValueError, TypeError):
        pretty = expiry_date
    if settlement == contract.AM:
        pretty = f"{pretty} · AM settled"
    return f"{pretty}  ({d} DTE)" if d is not None else pretty
