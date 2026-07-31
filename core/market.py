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
    """The strike carrying the largest net gamma exposure, e.g. "6,000 (Call)".

    Net GEX per contract is gamma x open interest x 100 x spot, signed +1 for
    calls and -1 for puts, summed per strike; the label names the strike with
    the largest ABSOLUTE total and which side dominates there.

    Returns "N/A" when the chain carries no gamma or no open interest — both
    are genuinely absent for some snapshots, so this is a normal outcome
    rather than an error, and the header shows the string as-is.
    """
    if not (
        "gamma" in chain_df.columns
        and "open_interest" in chain_df.columns
        and chain_df["gamma"].notna().any()
    ):
        return "N/A"

    gex_work = chain_df[
        chain_df["gamma"].notna() & chain_df["open_interest"].notna()
    ].copy()
    if gex_work.empty:
        return "N/A"

    gex_work["net_gex"] = (
        gex_work["gamma"]
        * gex_work["open_interest"]
        * 100 * spx_price
        * gex_work["right"].map({"C": 1, "P": -1})
    )
    gex_by_strike = gex_work.groupby("strike")["net_gex"].sum()
    if gex_by_strike.empty:
        return "N/A"

    max_strike = gex_by_strike.abs().idxmax()
    max_val    = gex_by_strike[max_strike]
    dom        = "Call" if max_val > 0 else "Put"
    return f"{max_strike:,.0f} ({dom})"
