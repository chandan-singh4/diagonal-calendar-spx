"""The "All Locks" popover — position management across every tracked combo.

WHY IT IS PAGE CHROME AND NOT PART OF A CHART. It is deliberately placed in
the Calendar Edge page header rather than next to any one chart: it manages
positions across strike/expiry combos, so it should not read as belonging to
whichever chart happens to be showing right now.

WHY IT IS IN ui/ RATHER THAN views/. It draws, but it is not a tab, and it is
handed to Calendar Edge through the ViewContext exactly as before. A ui/
module may not fetch — this one calls services/sidecars.py, which is where
`config.STATE_DIR` gets bound, rather than reaching for the directory itself.

THE EXPIRY PURGE IS NOT HERE, AND THAT IS THE POINT (BUG-021, ADR-039). It
sits inside `load_entry_locks` in services/sidecars.py, because filtering the
list at THIS level would tidy the visible popover while the chart and the
current-combo lookup carried on seeing a lock whose front leg expired weeks
ago.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

# The leading underscores are inherited, not chosen. These names travelled
# from app.py's module namespace unchanged, so that the move could be proved
# character-identical to what it replaced — the same discipline, and the same
# order of operations, as DEBT-028: move first, rename in a separate step
# where the diff shows nothing but the rename. Recorded as DEBT-033.
from services.sidecars import (
    _clear_entry_lock,
    _load_entry_locks,
    _update_entry_lock_mark,
)


def _render_all_locks_popover(current_key: str | None) -> None:
    """Page-level position-management control: a compact trigger + popover
    listing every tracked entry lock, deliberately placed in the Calendar
    Edge page header rather than next to any one chart — it manages
    positions across strike/expiry combos, so it shouldn't read as part of
    whichever chart happens to be showing right now."""
    _locks = _load_entry_locks()
    with st.popover(f"🔒 All Locks ({len(_locks)})"):
        if not _locks:
            st.caption('Nothing locked yet. Use "Lock Entry Here" on the Diagonal vs. '
                       'Transform Order Mark chart once you\'re in a position.')
            return
        for _pk, _pl in sorted(_locks.items(), key=lambda kv: kv[1]["locked_at"], reverse=True):
            _pl_dt = pd.Timestamp(_pl["locked_at"])
            _is_current = (_pk == current_key)
            st.markdown(
                f'<div style="font-family:var(--mono);font-size:.78rem;line-height:1.5;'
                f'color:#dde6f1;{"font-weight:700;" if _is_current else ""}">'
                f'Put {_pl["put_strike"]:.0f} / Call {_pl["call_strike"]:.0f}'
                f'{" <span style=\'color:#10d4a3;font-size:.68rem;font-weight:600;\'>● viewing</span>" if _is_current else ""}'
                f'<br><span style="color:#6d8fa8;">{_pl["front_expiry"]} → {_pl["back_expiry"]}</span>'
                f'<br>Entry ${_pl["entry_diagonal_mark"]:.2f} · {_pl_dt.strftime("%m/%d %I:%M %p")}'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.markdown('<div style="margin-top:.35rem;"></div>', unsafe_allow_html=True)
            _edit_key = f"_editing_lock_{_pk}"
            _b1, _b2, _b3, _sp = st.columns([1, 1, 1, 3])
            with _b1:
                if st.button("👁", key=f"pop_view_{_pk}", help="View chart",
                             disabled=_is_current):
                    st.session_state["pending_front_expiry"] = _pl["front_expiry"]
                    st.session_state["pending_back_expiry"]  = _pl["back_expiry"]
                    st.session_state["pending_put_strike"]   = _pl["put_strike"]
                    st.session_state["pending_call_strike"]  = _pl["call_strike"]
                    st.session_state["active_tab"] = "edge"
                    st.rerun()
            with _b2:
                if st.button("✏️", key=f"pop_edit_{_pk}", help="Edit entry price"):
                    st.session_state[_edit_key] = not st.session_state.get(_edit_key, False)
            with _b3:
                if st.button("🗑️", key=f"pop_remove_{_pk}", help="Remove lock"):
                    _clear_entry_lock(_pl["front_expiry"], _pl["back_expiry"],
                                       _pl["put_strike"], _pl["call_strike"])
                    st.rerun()
            if st.session_state.get(_edit_key):
                _new_mark = st.number_input(
                    "Corrected entry Diagonal Mark", value=float(_pl["entry_diagonal_mark"]),
                    step=0.05, format="%.2f", key=f"pop_edit_val_{_pk}",
                )
                if st.button("Save", key=f"pop_save_{_pk}", type="primary", use_container_width=True):
                    _update_entry_lock_mark(_pl["front_expiry"], _pl["back_expiry"],
                                             _pl["put_strike"], _pl["call_strike"], _new_mark)
                    st.session_state[_edit_key] = False
                    st.rerun()
            st.markdown('<hr style="margin:.55rem 0;border-color:#1a2d45;">', unsafe_allow_html=True)
