"""
app.py — Dashboard v3.4.  Run with: streamlit run app.py

Pure reader — all writes handled exclusively by collector.py. No Schwab API
calls, no DB writes. (The JSON sidecars store user preferences and derived
bookkeeping, not market data — see services/sidecars.py.)

WHAT THIS FILE IS, AFTER M2. Assembly and nothing else: load the snapshot,
derive what more than one tab needs, build the frozen ViewContext, dispatch
one tab. It was 4,283 lines at the start of the milestone and holds no
calculation, no stylesheet, no query and no tab body now. Each of those has
its own layer, and each layer's rules are enforced in
tests/test_layering.py — start there, not here, to find where something
lives:

  core/       pure calculation. No page, no database, no config.
  dataaccess/ the reads. Told which database; returns rows.
  state/      the JSON sidecars. Told which directory.
  services/   the page's data layer: memoisation, sidecar binding, Mission
              Control. The only layer that may import both config and
              streamlit.
  ui/         page chrome — theme, sidebar, header, controls bar, refresh.
  views/      one module per tab, each `render(ctx)` and nothing else.

THE ORDER OF THIS FILE IS LOAD-BEARING and does not survive rearranging:
the sidebar decides the poll interval the refresh poller needs; the Controls
Bar must promote its pending_ keys before its widgets are instantiated; and
VIEW_CTX is built last, frozen, once everything on it is known.

IV SCALE NOTE
  option_rows and atm_iv_by_expiry store IVs as decimals (0.18 = 18%).
  Multiply by 100 at every data load boundary — nowhere else.
"""

import logging
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import config
import db
import schwab_client

# ─── core/ — pure calculation, extracted from this file in M2 (ADR-032) ───────
# Imported by name rather than qualified (core_format.sparkline etc.) so the
# extraction stayed a pure move with no call-site churn. The names keep their
# leading underscores for the same reason; both are transitional — see ADR-032.
# core.charts is gone from here entirely: every chart site moved to views/ in
# step 2.4, which is what DEBT-030's fix has been waiting for.
from core import market, position
from core import session as core_session
from core.charts import to_display_time

# ─── services/ — the page's data layer, extracted in M2 step 2.5 ──────────────
# The memoised reads and the sidecar bindings: everything that has to know
# which database and which state directory. Imported by name, underscores
# intact, so each move stayed provable (DEBT-033 renames them).
from services.loaders import (
    _init_db_once,
    _load_atm_hist,
    _load_atm_hist_fb,
    _load_chain_df,
    _load_contract_hist,
    _load_diagonal_hist,
    _load_latest_atm_iv,
    _load_prior_close,
    _load_spx_intraday,
    _load_transform_marks,
    compute_transform_scanner,
)
from services.mission_control import _backfill_eligible_history, _run_mission_control
from services.sidecars import (
    DEFAULT_CHART_COLORS,
    _clear_entry_lock,
    _create_entry_lock,
    _entry_lock_key,
    _get_entry_lock,
    _load_chart_colors,
    _save_chart_colors,
)

# ─── ui/ — page chrome, extracted in M2 step 2.5 ──────────────────────────────
# Everything drawn outside a tab. Same qualified-import style as views/, and
# the same rule: each is handed what it needs and fetches nothing itself.
from ui import controls, header, locks, refresh, sidebar, theme

# ─── views/ — one module per tab, extracted in M2 step 2.4 ────────────────────
# Qualified (views.historical.render) rather than imported by name, because
# every tab exports the same `render`. Each is handed a ViewContext and reaches
# for nothing else; the @st.cache_data wrappers stay here and travel on it.
from views import edge as view_edge
from views import entry as view_entry
from views import historical as view_historical
from views import research as view_research
from views import scanner as view_scanner
from views import strike as view_strike
from views.context import ViewContext

logger = logging.getLogger(__name__)

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SPX Diagonal Analyzer",
    page_icon="📈",
    layout="wide",
)

# ─── Design system v3.4 ───────────────────────────────────────────────────────
# 757 lines of CSS lived here until M2 step 2.5. It is assets/theme.css now —
# see ui/theme.py for why, and for why the path is built from __file__.
theme.apply()


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar, then the live refresh
#
# Two calls with the poller between them, because that is the order the
# sidebar renders in and the poller needs the interval the toggle decides.
# See ui/sidebar.py.
# ─────────────────────────────────────────────────────────────────────────────

_token_age = schwab_client.get_token_age_days()

refresh_mode = sidebar.render_refresh_mode()
poll_interval = refresh_mode.interval
poll_label    = refresh_mode.label


# The lookup is passed in, not imported, so ui/ never learns where the
# database is. Read ui/refresh.py before touching it — BUG-020 lives there,
# and the order of two statements inside it is what stops the page freezing.
def _newest_snapshot_id() -> int | None:
    snap = db.get_latest_complete_snapshot(config.DB_PATH)
    return snap["snapshot_id"] if snap else None


refresh.install(poll_interval, _newest_snapshot_id)

_sidebar = sidebar.render_settings(
    default_chart_colors=DEFAULT_CHART_COLORS,
    load_chart_colors=_load_chart_colors,
    save_chart_colors=_save_chart_colors,
    token_age_days=_token_age,
    executable=sys.executable,
)
sc_max_rows  = _sidebar.sc_max_rows
CHART_COLORS = _sidebar.chart_colors

# ─────────────────────────────────────────────────────────────────────────────
# Database init + latest complete snapshot
# ─────────────────────────────────────────────────────────────────────────────

_init_db_once(config.DB_PATH)
latest_snap = db.get_latest_complete_snapshot(config.DB_PATH)

if latest_snap is None:
    st.error(
        "No complete snapshots found in the database. "
        "Make sure collector.py is running: `python collector.py`"
    )
    st.stop()

snapshot_id   = latest_snap["snapshot_id"]
# The poller (defined above) reruns the app only when a newer snapshot appears.
st.session_state["_active_snapshot_id"] = snapshot_id
spx_price     = latest_snap["underlying_price"]
vix_value     = latest_snap["vix_value"]
snap_ts_str   = latest_snap["snapshot_timestamp"]

snap_dt = datetime.strptime(snap_ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(
    tzinfo=UTC
)
snap_age_secs = (datetime.now(UTC) - snap_dt).total_seconds()
session_date  = snap_ts_str[:10]

# How old the newest price is ALLOWED to be right now, in seconds — 60 in the
# first and last half hour, 300 midday, None when the market is shut. This is
# the collector's own polling interval, read from the same pure function the
# collector uses, so the header cannot start disagreeing with the thing it is
# reporting on (core/session.py). Not to be confused with `poll_interval` above,
# which is how often the DASHBOARD looks for new data — a display preference.
_expected_interval = core_session.expected_interval(
    core_session.session_of(
        datetime.now(UTC).astimezone(ZoneInfo(config.DISPLAY_TIMEZONE)),
        config.MARKET_HOLIDAYS,
    ),
    config.POLL_INTERVAL_EVENT,
    config.POLL_INTERVAL_NORMAL,
)

# ─────────────────────────────────────────────────────────────────────────────
# Load option chain
# ─────────────────────────────────────────────────────────────────────────────

chain_df = _load_chain_df(snapshot_id)
if chain_df.empty:
    st.error(
        f"Snapshot {snapshot_id} exists but has no option rows. "
        "The database may be in an inconsistent state."
    )
    st.stop()

available_expiries = sorted(chain_df["expiry"].unique())
dte_by_expiry = chain_df.groupby("expiry")["dte"].first().astype(int).to_dict()


if len(available_expiries) < 2:
    st.warning(
        "Fewer than 2 expirations in the latest snapshot. "
        "Collector may still be initializing."
    )
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# SPX intraday price series + daily change vs prior session close
# ─────────────────────────────────────────────────────────────────────────────

spx_intraday = _load_spx_intraday(session_date, snapshot_id)

if not spx_intraday.empty:
    spx_intraday["ts_et"] = (
        pd.to_datetime(
            spx_intraday["snapshot_timestamp"], format="ISO8601", utc=True
        ).dt.tz_convert(config.DISPLAY_TIMEZONE)
        .dt.tz_localize(None)  # naive wall-clock: required by Plotly rangebreaks
    )

prev_close = _load_prior_close(session_date)

change = market.daily_change(
    spx_price,
    prev_close,
    float(spx_intraday["underlying_price"].iloc[0]) if not spx_intraday.empty else None,
)

gex_label = market.max_gex_label(chain_df, spx_price)

# ─────────────────────────────────────────────────────────────────────────────
# Mission Control — runs once per script execution, regardless of which tab
# is active, since the persistent Attention Strip in the header needs it too.
# ─────────────────────────────────────────────────────────────────────────────

_MC_LOOKBACK_DAYS_MAP = {"Today": 1, "5D": 5, "10D": 10, "20D": 20}
if "mc_lookback_select" not in st.session_state:
    st.session_state["mc_lookback_select"] = "Today"
_mc_lookback_days = _MC_LOOKBACK_DAYS_MAP[st.session_state["mc_lookback_select"]]

MC = _run_mission_control(
    chain_df, spx_price, snapshot_id, snap_ts_str, dte_by_expiry, _mc_lookback_days,
)

# ═══════════════════════════════════════════════════════════════════════════════
# HEADER — price bar, Attention Strip, token banner. All three render on every
# tab; the Attention Strip is why Mission Control runs above regardless of
# which tab is active.
# ═══════════════════════════════════════════════════════════════════════════════

header.render(
    spx_price=spx_price,
    vix_value=vix_value,
    gex_label=gex_label,
    poll_label=poll_label,
    poll_interval=poll_interval,
    snap_age_secs=snap_age_secs,
    snap_ts_str=snap_ts_str,
    change=change,
    expected_interval=_expected_interval,
)
header.render_attention_strip(MC)
header.render_token_banner(_token_age, sys.executable)

# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENT CONTROLS BAR — front/back expiry + put/call strike
# Always visible above the tabs so every section can access these values.
# Read ui/controls.py before reordering anything: three separate ordering
# constraints live in there, and two of them fail only at runtime.
# ═══════════════════════════════════════════════════════════════════════════════

selection = controls.render(
    chain_df=chain_df,
    available_expiries=available_expiries,
    dte_by_expiry=dte_by_expiry,
    spx_price=spx_price,
)
front_expiry = selection.front_expiry
back_expiry  = selection.back_expiry
put_strike   = selection.put_strike
call_strike  = selection.call_strike
front_dte    = selection.front_dte
back_dte     = selection.back_dte
strikes_set  = selection.strikes_set

# ─────────────────────────────────────────────────────────────────────────────
# Derived values (needed across more than one tab, so computed once)
# The arithmetic is core/position.py; the two reads that feed it stay here,
# because a memo belongs to the page (ADR-032).
# ─────────────────────────────────────────────────────────────────────────────

atm_ratios = position.atm_ratio_history(
    to_display_time(_load_atm_hist(front_expiry, 90), config.DISPLAY_TIMEZONE),
    to_display_time(_load_atm_hist(back_expiry,  90), config.DISPLAY_TIMEZONE),
)
metrics = position.derive(
    chain_df=chain_df,
    front_expiry=front_expiry,
    back_expiry=back_expiry,
    call_strike=call_strike,
    put_strike=put_strike,
    spx_price=spx_price,
    front_dte=front_dte,
    strikes_set=strikes_set,
    atm_ratios=atm_ratios,
)

# ─────────────────────────────────────────────────────────────────────────────
# What the extracted tabs are handed (M2 step 2.4)
#
# Built HERE, at the end of the prelude, because everything on it is computed
# above and nothing below may change it — which is the whole reason it is
# frozen. It grows a field per tab as the remaining tabs move out.
#
# The two loaders are passed as values, not imported by the views: they are
# the memoised wrappers defined further up, and a view importing
# dataaccess.queries directly would return identical numbers while re-querying
# on every rerun. Same seam, and same silent failure mode, as `compute=` in
# core/ and `load=` in dataaccess/ (ADR-032).
# ─────────────────────────────────────────────────────────────────────────────

VIEW_CTX = ViewContext(
    snapshot_id=snapshot_id,
    spx_price=spx_price,
    session_date=session_date,
    snapshot_ts=snap_ts_str,
    chain_df=chain_df,
    front_expiry=front_expiry,
    back_expiry=back_expiry,
    front_dte=front_dte,
    back_dte=back_dte,
    call_strike=call_strike,
    put_strike=put_strike,
    strikes_set=strikes_set,
    ts_now=metrics.ts_now,
    diag_mark=metrics.diag_mark,
    norm_deb=metrics.norm_deb,
    straddle=metrics.straddle,
    theta_diff=metrics.theta_diff,
    ic_mark=metrics.ic_mark,
    iv_pct=metrics.iv_pct,
    liquidity=metrics.liquidity,
    load_atm_hist_fb=_load_atm_hist_fb,
    load_diagonal_hist=_load_diagonal_hist,
    load_latest_atm_iv=_load_latest_atm_iv,
    load_contract_hist=_load_contract_hist,
    load_transform_marks=_load_transform_marks,
    compute_transform_scanner=compute_transform_scanner,
    mc=MC,
    sc_max_rows=sc_max_rows,
    entry_lock_key=_entry_lock_key,
    create_entry_lock=_create_entry_lock,
    clear_entry_lock=_clear_entry_lock,
    get_entry_lock=_get_entry_lock,
    render_all_locks_popover=locks._render_all_locks_popover,
    backfill_eligible_history=_backfill_eligible_history,
    chart_colors=CHART_COLORS,
)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB NAVIGATION — custom nav bar (not st.tabs) so Mission Control cards can
# jump straight to a pre-scoped tab programmatically. st.tabs() has no API
# for switching the active tab from code; this swaps in a session_state-driven
# button row instead, styled via CSS to look identical to the original tabs.
# ═══════════════════════════════════════════════════════════════════════════════

# Key, button label and renderer together in ONE table. They used to be a
# key/label table here and six near-identical `if` blocks under banner
# comments sixty lines below, so adding or renaming a tab meant editing two
# places that could not see each other. Nothing is dispatched that is not in
# this list, and nothing in this list goes undispatched.
_TABS = [
    ("scanner",  "🔭  Scanner",          view_scanner.render),
    ("entry",    "📊  Entry Analysis",   view_entry.render),
    ("edge",     "📈  Calendar Edge",    view_edge.render),
    ("strike",   "🎯  Strike Detail",    view_strike.render),
    ("hist",     "📉  Historical Stats", view_historical.render),
    ("research", "🔬  Research",         view_research.render),
]

if "active_tab" not in st.session_state:
    st.session_state["active_tab"] = "scanner"

with st.container(key="topnav"):
    _nav_cols = st.columns(len(_TABS))
    for (_tkey, _tlabel, _), _tcol in zip(_TABS, _nav_cols):
        with _tcol:
            _is_active = st.session_state["active_tab"] == _tkey
            if st.button(
                _tlabel, key=f"nav_{_tkey}", use_container_width=True,
                type="primary" if _is_active else "secondary",
            ):
                st.session_state["active_tab"] = _tkey
                st.rerun()

# Exactly one tab body runs per script execution — the custom nav means the
# others are not merely hidden, they are never executed.
for _tkey, _, _render_tab in _TABS:
    if st.session_state["active_tab"] == _tkey:
        _render_tab(VIEW_CTX)
