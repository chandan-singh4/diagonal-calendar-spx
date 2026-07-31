"""The Position Controls bar — front/back expiry and put/call strike.

ALWAYS VISIBLE, ABOVE THE TABS, and the single source of the selection every
tab reads. It returns that selection rather than leaving it in a module
namespace, which is the habit M2 exists to break.

THE ORDER OF WHAT HAPPENS HERE IS LOAD-BEARING, in three separate ways:

  1. The pending_ promotion must run BEFORE any widget below is instantiated.
     Streamlit forbids writing to a widget's key after that widget has
     rendered, and the Mission Control cards that stage these values render
     LATER in the script than this bar does — hence the two-step (stage under
     pending_, promote on the next run) rather than a direct write.

  2. The validity checks must run after promotion and before the widgets. A
     stashed value that is not in the freshly-loaded chain raises a "not in
     options" error, so it is dropped and the normal default logic takes over.

  3. The strike lists depend on the expiry selections, so the expiry
     selectboxes must be instantiated before the strike lists are computed —
     which is why the four columns are created together up front but written
     into in two passes.

Extracted from app.py in M2 step 2.5, statement for statement.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core.format import exp_label
from core.ranking import nearest_idx


@dataclass(frozen=True)
class Selection:
    front_expiry: str
    back_expiry: str
    put_strike: float
    call_strike: float
    front_dte: int
    back_dte: int
    strikes_set: bool


def render(*, chain_df: pd.DataFrame, available_expiries: list,
           dte_by_expiry: dict, spx_price: float) -> Selection:
    st.markdown(
        '<div class="ctrl-bar"><div class="ctrl-bar-title">Position Controls</div></div>',
        unsafe_allow_html=True,
    )

    # Mission Control drill-down (a card's "View Chart" click) stages values
    # under pending_ keys, since it can't write to a *_select key after that
    # widget has already rendered earlier in the same script run. Promote them
    # here — before any of the widgets below are instantiated — so the rerun
    # this triggers picks them up cleanly.
    if "pending_front_expiry" in st.session_state:
        st.session_state["front_expiry_select"] = st.session_state.pop("pending_front_expiry")
    if "pending_back_expiry" in st.session_state:
        st.session_state["back_expiry_select"] = st.session_state.pop("pending_back_expiry")
    if "pending_put_strike" in st.session_state:
        st.session_state["put_strike_select"] = st.session_state.pop("pending_put_strike")
    if "pending_call_strike" in st.session_state:
        st.session_state["call_strike_select"] = st.session_state.pop("pending_call_strike")

    # If the stashed value isn't valid for the freshly-loaded chain, drop it so
    # the normal default logic below takes over instead of raising a
    # "not in options" error.
    if "front_expiry_select" in st.session_state and st.session_state["front_expiry_select"] not in available_expiries:
        del st.session_state["front_expiry_select"]
    if "back_expiry_select" in st.session_state and st.session_state["back_expiry_select"] not in available_expiries:
        del st.session_state["back_expiry_select"]

    c1, c2, c3, c4 = st.columns(4)

    # exp_label takes the expiry table as an argument (ADR-034), and Streamlit
    # calls format_func with the option alone — so the table is bound here rather
    # than reached for inside the function.
    def _expiry_option_label(expiry: str) -> str:
        return exp_label(expiry, dte_by_expiry)

    with c1:
        _fe_kwargs = {} if "front_expiry_select" in st.session_state else {"index": 0}
        front_expiry = st.selectbox(
            "Front Expiry", available_expiries,
            format_func=_expiry_option_label, key="front_expiry_select", **_fe_kwargs,
        )
    with c2:
        _be_kwargs = (
            {} if "back_expiry_select" in st.session_state
            else {"index": min(1, len(available_expiries) - 1)}
        )
        back_expiry = st.selectbox(
            "Back Expiry", available_expiries,
            format_func=_expiry_option_label, key="back_expiry_select", **_be_kwargs,
        )

    _put_strikes = sorted(set(
        chain_df[(chain_df["expiry"] == front_expiry) & (chain_df["side"] == "PUT")]["strike"].unique()
    ) & set(
        chain_df[(chain_df["expiry"] == back_expiry)  & (chain_df["side"] == "PUT")]["strike"].unique()
    ))
    _call_strikes = sorted(set(
        chain_df[(chain_df["expiry"] == front_expiry) & (chain_df["side"] == "CALL")]["strike"].unique()
    ) & set(
        chain_df[(chain_df["expiry"] == back_expiry)  & (chain_df["side"] == "CALL")]["strike"].unique()
    ))

    if "put_strike_select" in st.session_state and st.session_state["put_strike_select"] not in _put_strikes:
        del st.session_state["put_strike_select"]
    if "call_strike_select" in st.session_state and st.session_state["call_strike_select"] not in _call_strikes:
        del st.session_state["call_strike_select"]

    with c3:
        if _put_strikes:
            _ps_kwargs = (
                {} if "put_strike_select" in st.session_state
                else {"index": nearest_idx(_put_strikes, spx_price - 100)}
            )
            put_strike = st.selectbox(
                "Put Strike",
                options=_put_strikes,
                format_func=lambda s: f"{int(s):,}",
                key="put_strike_select",
                help="Only strikes present in both front and back expiry are shown.",
                **_ps_kwargs,
            )
        else:
            st.warning("No PUT strikes available for this expiry pair.")
            put_strike = 0.0

    with c4:
        if _call_strikes:
            _cs_kwargs = (
                {} if "call_strike_select" in st.session_state
                else {"index": nearest_idx(_call_strikes, spx_price)}
            )
            call_strike = st.selectbox(
                "Call Strike",
                options=_call_strikes,
                format_func=lambda s: f"{int(s):,}",
                key="call_strike_select",
                help="Only strikes present in both front and back expiry are shown.",
                **_cs_kwargs,
            )
        else:
            st.warning("No CALL strikes available for this expiry pair.")
            call_strike = 0.0

    if back_expiry <= front_expiry:
        st.warning("Back expiry ≤ Front — unusual for a diagonal, shown anyway.")

    return Selection(
        front_expiry=front_expiry,
        back_expiry=back_expiry,
        put_strike=put_strike,
        call_strike=call_strike,
        front_dte=int(chain_df[chain_df["expiry"] == front_expiry]["dte"].iloc[0]),
        back_dte=int(chain_df[chain_df["expiry"] == back_expiry]["dte"].iloc[0]),
        strikes_set=call_strike > 0 and put_strike > 0,
    )
