"""The sidebar: refresh mode, scanner cap, chart colours, Schwab connection.

SPLIT INTO TWO CALLS, AND THE SPLIT IS NOT COSMETIC. In app.py the live
refresh poller was installed BETWEEN two stretches of sidebar code, because
it needs `poll_interval` and that is decided by the Event Mode toggle. Widget
order inside a Streamlit sidebar is call order, so collapsing this into one
function would have reordered the sidebar. Hence `render_refresh_mode()`
first, the poller, then `render_settings()`.

WHAT COMES BACK RATHER THAN BEING DRAWN. Three of these controls are read
elsewhere on the page — the poll interval and its label by the header
countdown, the scanner row cap and the chart colours by the tabs. They are
returned as plain values rather than left in the module namespace, which is
the whole habit M2 exists to break.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time
from pathlib import Path

import pandas as pd
import streamlit as st

import config

# The repo root, from THIS file's location — ui/sidebar.py, so up two.
#
# It was `Path(__file__).resolve().parent` while this lived in app.py, which
# sat at the root. Moving the code changes what `__file__` means, and the
# render comparison could not have caught a mistake here because it redacts
# the checkout path as known noise. So it is asserted directly instead, by
# tests/test_layering.py::test_the_reauth_command_points_at_the_project_root.
PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RefreshMode:
    interval: int
    label: str


@dataclass(frozen=True)
class SidebarSettings:
    sc_max_rows: int
    chart_colors: dict[str, str]


def reauth_command(executable: str) -> str:
    """The re-authentication command, ready to paste into a terminal.

    Built from the RUNNING interpreter and the project's location rather than
    hardcoded, so it stays correct if the project moves or the venv is rebuilt
    — the same reasoning as the %~dp0 paths in start_collector.bat (M0.13).
    It is rendered with st.code(), which gives a one-click copy button for
    free; the previous <code> block inside raw HTML could not be copied, which
    is the whole reason you end up retyping it.
    """
    return f'cd "{PROJECT_DIR}"\n"{executable}" scripts\\reauth.py'


def render_refresh_mode() -> RefreshMode:
    """Event Mode toggle plus automatic OPEN/CLOSE session matching."""
    st.sidebar.title("Settings")
    event_mode = st.sidebar.toggle(
        "⚡ Event Mode (60s refresh)",
        value=False,
        help=(
            "Increases dashboard refresh rate to 60s during high-impact events "
            "(FOMC, CPI, NFP, PPI, Powell speeches). "
            "Activate manually ~10–15 min before the announcement."
        ),
    )

    _now_et = pd.Timestamp.now(tz="America/New_York")
    _t = _now_et.time()
    _open_session  = dt_time(9, 30) <= _t < dt_time(10, 0)
    _close_session = dt_time(15, 30) <= _t < dt_time(16, 0)

    if event_mode:
        poll_interval = config.POLL_INTERVAL_EVENT
        poll_label    = "60s ⚡ Event Mode"
        st.sidebar.caption("⚡ Event Mode active — refreshing every 60s.")
    elif _open_session:
        poll_interval = config.POLL_INTERVAL_EVENT
        poll_label    = "60s (OPEN session)"
        st.sidebar.caption("📈 OPEN session — auto-matched to collector (60s).")
    elif _close_session:
        poll_interval = config.POLL_INTERVAL_EVENT
        poll_label    = "60s (CLOSE session)"
        st.sidebar.caption("📉 CLOSE session — auto-matched to collector (60s).")
    else:
        poll_interval = config.POLL_INTERVAL_NORMAL
        poll_label    = "300s"

    return RefreshMode(interval=poll_interval, label=poll_label)


def render_settings(
    *,
    default_chart_colors: dict,
    load_chart_colors,
    save_chart_colors,
    token_age_days: float | None,
    executable: str,
) -> SidebarSettings:
    """Scanner cap, chart colours, and the Schwab connection panel.

    The colour loader and saver are INJECTED for the same reason the tabs get
    their loaders injected: they bind `config.STATE_DIR`, and a ui/ module
    that reached for that could find the sidecar files itself (DEBT-011).
    `token_age_days` arrives the same way rather than via `schwab_client`,
    which nothing on a page should import.
    """
    st.sidebar.divider()
    st.sidebar.markdown("**🔭 Transform Scanner**")

    sc_max_rows = st.sidebar.number_input(
        "Max Results", min_value=10, max_value=200, value=50, step=10,
        key="sc_max_rows",
        help="Cap the number of rows returned (sorted by Transform Diff descending).",
    )

    st.sidebar.divider()
    st.sidebar.markdown("**🎨 Chart Appearance**")

    CHART_COLORS = load_chart_colors()

    with st.sidebar.expander("Line colors", expanded=False):
        _colors_changed = False
        for _key, (_label, _default) in default_chart_colors.items():
            _picked = st.color_picker(
                _label, value=CHART_COLORS.get(_key, _default), key=f"color_{_key}",
            )
            if _picked != CHART_COLORS.get(_key):
                CHART_COLORS[_key] = _picked
                _colors_changed = True
        if _colors_changed:
            save_chart_colors(CHART_COLORS)

        if st.button("↺ Reset to Default Colors", key="reset_colors_btn",
                     use_container_width=True):
            CHART_COLORS = {k: v[1] for k, v in default_chart_colors.items()}
            save_chart_colors(CHART_COLORS)
            for _key in default_chart_colors:
                st.session_state.pop(f"color_{_key}", None)
            st.rerun()

    # ── Schwab connection ─────────────────────────────────────────────────────
    # Always available, not only once the token is nearly dead. The red banner at
    # the top of the page still fires from day 6; this is here so the command can
    # be copied at any time, and so "how long have I got?" has an answer that does
    # not require opening a terminal to ask.
    st.sidebar.divider()
    st.sidebar.markdown("**🔑 Schwab Connection**")

    _sb_age = token_age_days
    if _sb_age is None:
        st.sidebar.error("No token — not authenticated.")
    else:
        _sb_left = 7 - _sb_age
        if _sb_left <= 0:
            st.sidebar.error(f"Token EXPIRED {abs(_sb_left):.1f} days ago — collector is blind.")
        elif _sb_left < 1:
            st.sidebar.warning(f"Token expires in {_sb_left * 24:.0f} hours.")
        elif _sb_left < 2:
            st.sidebar.warning(f"Token expires in {_sb_left:.1f} days.")
        else:
            st.sidebar.success(f"Token valid — {_sb_left:.1f} days left.")

    with st.sidebar.expander("Re-authenticate (every 7 days)", expanded=False):
        st.caption("Copy, then paste into a terminal:")
        st.code(reauth_command(executable), language="powershell")
        st.caption(
            "Opens the Schwab login in your browser, then asks you to paste the "
            "redirected URL back — it will look like an error page, which is expected. "
            "Cannot be automated: Schwab requires the interactive login. Your current "
            "token is restored automatically if you cancel partway through."
        )

    return SidebarSettings(sc_max_rows=sc_max_rows, chart_colors=CHART_COLORS)
