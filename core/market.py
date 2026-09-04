"""Market context: the day's move, and where gamma is concentrated.

Two calculations that fed the header bar and lived in app.py's prelude as bare
statements. Pure: handed a chain and a price, they return values and touch
nothing. That is what makes them checkable at all — as prelude statements they
could only be exercised by rendering the whole page against the production
database.

Extracted in M2 step 2.5 (ADR-040).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core import gex

# The header's up/down colours. They are here rather than in the stylesheet
# because they are chosen per-value in Python and interpolated into inline
# styles; assets/theme.css cannot express "green when this number is positive".
GREEN = "#10d4a3"
RED = "#f05252"


@dataclass(frozen=True)
class DailyChange:
    """The day's move, and what it was measured against.

    `reference_label` says WHICH baseline was used, because the answer is not
    always yesterday's close: on the first ever collection day there is no
    prior session, and the session's own open is used instead. A change of
    "+12.4" means nothing without knowing which of the two it is.
    """
    reference_price: float
    reference_label: str
    points: float
    percent: float
    color: str
    arrow: str


def daily_change(spx_price: float, prev_close: float | None,
                 session_open: float | None) -> DailyChange:
    """Change from the last COMPLETE snapshot of the PRIOR session.

    That is approximately yesterday's official close. Falls back to the first
    intraday snapshot of today if no prior-session data exists (the first ever
    collection day), and finally to the current price — which reports a flat
    day rather than dividing by nothing.
    """
    if prev_close is not None:
        ref_price = prev_close
        ref_label = f"Prev Close {prev_close:,.0f}"
    elif session_open is not None:
        ref_price = float(session_open)
        ref_label = f"Session Open {ref_price:,.0f}"
    else:
        ref_price = spx_price
        ref_label = ""

    points = spx_price - ref_price
    percent = (points / ref_price * 100) if ref_price else 0.0
    return DailyChange(
        reference_price=ref_price,
        reference_label=ref_label,
        points=points,
        percent=percent,
        color=GREEN if points >= 0 else RED,
        arrow="▲" if points >= 0 else "▼",
    )


def max_gex_label(chain_df: pd.DataFrame, spx_price: float) -> str:
    """The strike carrying the largest gamma exposure, e.g. "6,000 (Call)".

    DELEGATES TO core/gex.py. This function used to carry its own copy of the
    GEX arithmetic, scaled per one-POINT move; the Gamma Exposure tab needs
    the same measure per one-PERCENT move, which is the industry-standard
    headline unit. Two copies of a formula that must agree is a formula that
    will eventually disagree -- the same reason core/session.py was extracted
    for the collector, the header and the watchdog.

    **The strike this names is unchanged by the move.** The two scalings differ
    by one positive constant applied to every row alike, and a constant cannot
    reorder magnitudes. `tests/test_gex.py` pins that directly rather than
    leaving it as an argument.

    Returns "N/A" when the chain carries no gamma or no open interest -- both
    are genuinely absent for some snapshots, so this is a normal outcome
    rather than an error, and the header shows the string as-is.
    """
    per_strike = gex.by_strike(chain_df, spx_price)
    if per_strike.empty:
        return "N/A"

    totals = gex.summary(per_strike)
    if totals["peak_strike"] is None:
        return "N/A"

    return f"{totals['peak_strike']:,.0f} ({totals['peak_side']})"
