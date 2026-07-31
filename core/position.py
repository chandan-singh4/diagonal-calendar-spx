"""What the current strike/expiry selection is worth, derived once.

EVERY VALUE HERE IS COMPUTED ONCE PER SCRIPT RUN and read by more than one
tab, which is why it sits in app.py's prelude rather than inside whichever
tab happens to need it. This module holds the arithmetic; app.py keeps the
two database reads that feed it.

OPTIONALITY IS THE BEHAVIOUR, NOT AN OVERSIGHT. Every field on
`PositionMetrics` is None under some real condition — no strikes chosen, wing
strikes missing from the chain, fewer than 90 days of history — and the Entry
tab has a distinct message for each. So they stay visible in the types rather
than being defaulted to zero, which would silently show a real-looking number
where there is no answer. ("Missing price -> blank, not 0" is a standing rule
in this project.)

Extracted from app.py in M2 step 2.5 (ADR-040), statement for statement.
Pure: handed a chain and a selection, it returns numbers. `iv_engine` is the
only thing it calls, and that module is itself pure and fully covered by
tests/test_iv_engine.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

import iv_engine


@dataclass(frozen=True)
class PositionMetrics:
    # `ts_now` is iv_engine.term_structure(...) and `theta_diff` is
    # iv_engine.theta_differential(...); both typed loosely so this module
    # states no opinion about shapes it does not own.
    ts_now: Any
    straddle: float
    diag_mark: float | None
    norm_deb: float | None
    theta_diff: Any
    ic_mark: float | None
    iv_pct: float | None
    liquidity: float


def atm_ratio_history(front_hist: pd.DataFrame,
                      back_hist: pd.DataFrame) -> pd.DataFrame:
    """Front/back ATM IV joined on timestamp, with their ratio.

    An INNER join on purpose: a ratio needs both legs at the same instant, and
    an outer join would manufacture rows where one side is missing.

    DEBT-030 note: the two frames are converted to display time by the caller.
    They feed a merge and a percentile here, not an axis, so the zone would
    not change a displayed number — converted anyway so that every consumer of
    these reads sees exactly what it saw before that change.
    """
    if front_hist.empty or back_hist.empty:
        return pd.DataFrame()

    merged = pd.merge(
        front_hist[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "front_iv"}),
        back_hist[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "back_iv"}),
        on="timestamp", how="inner",
    )
    merged["iv_ratio"] = merged["front_iv"] / merged["back_iv"]
    return merged


def derive(*, chain_df: pd.DataFrame, front_expiry: str, back_expiry: str,
           call_strike: float, put_strike: float, spx_price: float,
           front_dte: int, strikes_set: bool,
           atm_ratios: pd.DataFrame) -> PositionMetrics:
    """Term structure, position cost, theta and liquidity for the selection."""
    front_iv_atm = iv_engine.atm_iv(chain_df, front_expiry, spx_price)
    back_iv_atm  = iv_engine.atm_iv(chain_df, back_expiry,  spx_price)
    ts_now       = iv_engine.term_structure(front_iv_atm, back_iv_atm)

    _straddle = iv_engine.atm_straddle_price(spx_price, front_iv_atm, front_dte)
    _diag_mark: float | None = None
    _norm_deb:  float | None = None
    _theta_diff = None
    _ic_mark:   float | None = None

    if strikes_set:
        _efc = iv_engine.strike_contract(chain_df, front_expiry, call_strike, "CALL")
        _ebc = iv_engine.strike_contract(chain_df, back_expiry,  call_strike, "CALL")
        _efp = iv_engine.strike_contract(chain_df, front_expiry, put_strike,  "PUT")
        _ebp = iv_engine.strike_contract(chain_df, back_expiry,  put_strike,  "PUT")

        if all(m is not None for m in [_efc.mark, _ebc.mark, _efp.mark, _ebp.mark]):
            _diag_mark = (_ebc.mark + _ebp.mark) - (_efc.mark + _efp.mark)
            _norm_deb  = iv_engine.normalized_debit(_diag_mark, _straddle)

        _fc_wing_call = iv_engine.strike_contract(chain_df, front_expiry, call_strike + 5, "CALL")
        _fc_wing_put  = iv_engine.strike_contract(chain_df, front_expiry, put_strike  - 5, "PUT")
        if all(m is not None for m in [_ebc.mark, _ebp.mark, _fc_wing_call.mark, _fc_wing_put.mark]):
            _ic_mark = (_ebc.mark + _ebp.mark) - (_fc_wing_call.mark + _fc_wing_put.mark)

        _theta_diff = iv_engine.theta_differential(
            chain_df, front_expiry, back_expiry, call_strike, put_strike
        )

    near_front = chain_df[chain_df["expiry"] == front_expiry]
    atm_row    = near_front.iloc[(near_front["strike"] - spx_price).abs().argsort()[:1]]
    _liquidity = iv_engine.liquidity_score(
        atm_row["volume"].fillna(0).mean(),
        atm_row["open_interest"].fillna(0).mean(),
    )
    _iv_pct = (
        iv_engine.percentile_rank(atm_ratios["iv_ratio"], ts_now.ratio)
        if not atm_ratios.empty else None
    )

    return PositionMetrics(
        ts_now=ts_now,
        straddle=_straddle,
        diag_mark=_diag_mark,
        norm_deb=_norm_deb,
        theta_diff=_theta_diff,
        ic_mark=_ic_mark,
        iv_pct=_iv_pct,
        liquidity=_liquidity,
    )
