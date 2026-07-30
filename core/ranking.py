"""Ranking and identity — the ORDER cards appear in, and what makes a card itself.

Pinned by tests/test_display_golden.py. The sort in _rank_for_panel is the most
consequential few lines in the display layer: reversed, the asymmetric setups
this trader actually takes drop below the degenerate symmetric ones and fall off
the end of the panel (DEBT-026).
"""
from __future__ import annotations

import pandas as pd


def _rank_for_panel(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tiered ranking for the Mission Control panel — deliberately NOT a single
    blended score (composite 0-100 scores were already rejected for this
    project; this stays as a transparent, inspectable multi-key sort):

      Tier 1 — asymmetric strikes (Put Strike != Call Strike) bubble above
               symmetric/ATM ones. An offset=0 sweep produces put==call,
               which is a degenerate straddle, not this trader's actual
               strangle-diagonal structure — those aren't false positives,
               just real combos that don't represent a tradeable setup here.
      Tier 2 — Transform Diff (the gap), descending — the core economic
               value of the opportunity.

    Duration Active and Trend stay visible on every card as supporting
    context rather than folded into this sort, so the trader can apply
    their own judgment rather than trust a hidden weighting.
    """
    if df.empty:
        return df
    d = df.copy()
    d["_asymmetric"] = d["Put Strike"] != d["Call Strike"]
    return (
        d.sort_values(["_asymmetric", "Transform Diff"], ascending=[False, False])
        .drop(columns="_asymmetric")
        .reset_index(drop=True)
    )


def _card_key(card: dict) -> str:
    return f"{card['front_raw']}|{card['back_raw']}|{int(card['put_strike'])}|{int(card['call_strike'])}"
