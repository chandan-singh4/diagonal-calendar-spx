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

  4. The BUG-022 notice renders after BOTH guards, so one message can name
     every control that was dropped rather than one message per control. It
     also depends on (1) and (2) being in this order: what was staged is
     recorded during promotion and read during the guards, all inside this one
     call, and that single call IS the scoping — it is what distinguishes a
     value a lock click asked for from one the trader set by hand.

Extracted from app.py in M2 step 2.5, statement for statement. The notice
(ADR-042) is the only behaviour added since.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core import contract
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
    # WHAT WAS STAGED BY THIS CLICK, remembered so the guards below can tell a
    # value that came from a lock from one the trader set by hand (BUG-022).
    # A plain local is the whole scoping mechanism: promotion and both guards
    # run inside this one call, so anything recorded here belongs to the click
    # that triggered this rerun and nothing else.
    _staged: dict[str, object] = {}
    _dropped: list[str] = []

    for _pending, _widget in (
        ("pending_front_expiry", "front_expiry_select"),
        ("pending_back_expiry",  "back_expiry_select"),
        ("pending_put_strike",   "put_strike_select"),
        ("pending_call_strike",  "call_strike_select"),
    ):
        if _pending in st.session_state:
            _value = st.session_state.pop(_pending)
            st.session_state[_widget] = _value
            _staged[_widget] = _value

    _LABELS = {
        "front_expiry_select": "Front Expiry",
        "back_expiry_select":  "Back Expiry",
        "put_strike_select":   "Put Strike",
        "call_strike_select":  "Call Strike",
    }

    def _drop(widget_key: str) -> None:
        """Discard an unusable selection, and REMEMBER it if a lock asked for it.

        Dropping is not optional — Streamlit raises "not in options" and the
        page dies otherwise. The defect BUG-022 named was never the drop; it was
        that the app fell through to its defaults with no path where it admitted
        it could not honour what was clicked.
        """
        if widget_key in _staged:
            _value = _staged[widget_key]
            _shown = f"{int(_value):,}" if isinstance(_value, (int, float)) else _value
            _dropped.append(f"{_LABELS[widget_key]} ({_shown})")
        del st.session_state[widget_key]

    # If the stashed value isn't valid for the freshly-loaded chain, drop it so
    # the normal default logic below takes over instead of raising a
    # "not in options" error.
    if "front_expiry_select" in st.session_state and st.session_state["front_expiry_select"] not in available_expiries:
        _drop("front_expiry_select")
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
    # BACK EXPIRY IS NARROWED TO WHAT COMES AFTER THE FRONT (Chandan, 2026-08-19).
    # A diagonal whose back leg expires first is not a diagonal, and the old
    # code only warned about it after the fact — with 21 expiries listed, that
    # left 20 wrong answers one click away. So the wrong ones are not offered.
    #
    # The comparison is on the DATE, which is Chandan's call for the one case
    # where it is arguable: the third Friday's a.m. contract settles at that
    # morning's open and the p.m. one that afternoon, so the p.m. contract does
    # end later — but only by a few hours, and "the first back option is the
    # next date" is the rule he wants. Same date, either contract: not offered.
    _back_options = [e for e in available_expiries
                     if contract.date_of(e) > contract.date_of(front_expiry)]

    if not _back_options:
        # Only reachable by choosing the furthest expiry collected as the front
        # leg. Fall back to that same expiry so the page still renders — every
        # value below is then a zero-width diagonal, which is visibly odd, and
        # the warning says why rather than leaving it to be puzzled over.
        _back_options = [front_expiry]
        st.warning(
            "**No expiry later than this one is being recorded**, so there is "
            "no back leg to pair it with. Choose an earlier Front Expiry."
        )

    # A back leg staged by a lock, or left over from the previous front, may not
    # survive that narrowing — drop it here so the default below takes over
    # rather than Streamlit raising "not in options" and killing the page.
    if ("back_expiry_select" in st.session_state
            and st.session_state["back_expiry_select"] not in _back_options):
        _drop("back_expiry_select")

    with c2:
        # index 0 is the very next expiry after the front, which is what the
        # narrowing above makes the natural default.
        _be_kwargs = {} if "back_expiry_select" in st.session_state else {"index": 0}
        back_expiry = st.selectbox(
            "Back Expiry", _back_options,
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
        _drop("put_strike_select")
    if "call_strike_select" in st.session_state and st.session_state["call_strike_select"] not in _call_strikes:
        _drop("call_strike_select")

    # THE ADMISSION (BUG-022). Rendered after the strike guards so one notice
    # covers all four, and above the tabs so it sits with the controls it is
    # about — the chart below is drawn from the DEFAULTS named here, not from
    # the lock that was clicked.
    if _dropped:
        st.warning(
            "**Showing defaults — this lock could not be opened as saved.** "
            + ", ".join(_dropped)
            + " — not in the latest snapshot, so the control(s) fell back to a "
            "default. **The chart below is a different diagonal from the one you "
            "clicked.** A locked strike leaves the recorded window as SPX moves; "
            "newly locked legs are kept from the next snapshot on."
        )

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

    # The old "Back expiry ≤ Front — shown anyway" warning is gone: the option
    # list above can no longer produce that pair, so the warning could only
    # ever have fired on the no-later-expiry fallback, which says so itself in
    # plainer words. Nothing here should quietly tolerate the state any more.

    return Selection(
        front_expiry=front_expiry,
        back_expiry=back_expiry,
        put_strike=put_strike,
        call_strike=call_strike,
        front_dte=int(chain_df[chain_df["expiry"] == front_expiry]["dte"].iloc[0]),
        back_dte=int(chain_df[chain_df["expiry"] == back_expiry]["dte"].iloc[0]),
        strikes_set=call_strike > 0 and put_strike > 0,
    )
