"""
app.py — Dashboard v2.  Run with: streamlit run app.py

Pure reader — all writes handled exclusively by collector.py.
No Schwab API calls.  No DB writes.
(pinned_pairs.json stores user preferences only — not market data.)

Data sources (all read from dashboard.db):
  snapshots         → SPX price, VIX, intraday price series, prior-session close
  option_rows       → current option chain; per-strike IV; GEX
  atm_iv_by_expiry  → ATM IV history for charts, range stats, pair scanner

Layout (top to bottom):
  1. Header         — SPX price + daily change + MINI intraday sparkline,
                      pts/% toggle, VIX, Max|GEX| Strike, staleness
  2. Controls       — Front/Back expiry, Call/Put strike  (4 columns — no Max Gap here)
  3. IV Structure   — Front vs Back IV chart at selected strikes; period radio above it
  4. Historical Stats — Today / 5D / 10D / 20D ratio range bars
  5. Pinned Pairs   — Persisted pair watchlist (pinned_pairs.json)
  6. Pair Scanner   — All valid (front, back) pairs; filter row has Min DTE / Max DTE / Max Gap
  7. Calendar Edge  — ATM IV chart + ratio metrics + day-change
  8. Transform Credit — Trade quality score (very bottom)

DAILY CHANGE
  change = current SPX price − last COMPLETE snapshot from the PRIOR session
  (≈ yesterday's official close).  Falls back to first intraday snapshot
  if no prior-session data exists (first ever collection day).

MAX GAP
  Lives in the Pair Scanner filter row (not the Controls Row).
  Calendar days between front and back expiry dates.
  Mon→Tue = 1, Fri→Mon = 3.

IV SCALE NOTE
  option_rows and atm_iv_by_expiry store IVs as decimals (0.18 = 18%).
  Multiply by 100 at every data load boundary — nowhere else.
"""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config
import db
import iv_engine

logger = logging.getLogger(__name__)

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SPX Diagonal Calendar Analyzer",
    layout="wide",
)

# ─── Constants ────────────────────────────────────────────────────────────────
PINNED_PAIRS_FILE = Path(__file__).parent / "pinned_pairs.json"
_SPARK_BARS = "▁▂▃▄▅▆▇█"
_TABLE_DISPLAY_COLS = ["Front", "Back", "Ratio", "Day Chg", "Drop%", "Rise%", "Chart"]


# ─────────────────────────────────────────────────────────────────────────────
# Helper — pinned pairs persistence
# ─────────────────────────────────────────────────────────────────────────────

def _load_pinned() -> list[dict]:
    try:
        if PINNED_PAIRS_FILE.exists():
            return json.loads(PINNED_PAIRS_FILE.read_text())
    except Exception:
        pass
    return []


def _save_pinned(pairs: list[dict]) -> None:
    try:
        PINNED_PAIRS_FILE.write_text(json.dumps(pairs, indent=2))
    except Exception as e:
        st.error(f"Could not save pinned pairs: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Helper — unicode sparkline
# ─────────────────────────────────────────────────────────────────────────────

def _sparkline(values: list[float], width: int = 10) -> str:
    if not values:
        return "─"
    step = max(1, len(values) // width)
    sampled = values[::step][-width:]
    mn, mx = min(sampled), max(sampled)
    if mx == mn:
        return _SPARK_BARS[3] * len(sampled)
    return "".join(
        _SPARK_BARS[int((v - mn) / (mx - mn) * 7)] for v in sampled
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper — ATM IV history
# ─────────────────────────────────────────────────────────────────────────────

def _load_atm_hist(expiry: str, days: int) -> pd.DataFrame:
    rows = db.get_atm_iv_history(config.DB_PATH, expiry, days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={"snapshot_timestamp": "timestamp",
                             "atm_avg_iv": "atm_iv"})
    df["atm_iv"] = df["atm_iv"] * 100
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helper — per-contract IV history
# ─────────────────────────────────────────────────────────────────────────────

def _load_contract_hist(expiry: str, strike: float,
                         side: str, days: int) -> pd.DataFrame:
    right_char = "C" if side == "CALL" else "P"
    rows = db.get_contract_iv_history(
        config.DB_PATH, expiry, strike, right_char, days
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["iv"] = df["iv"] * 100
    df["timestamp"] = (
        pd.to_datetime(df["snapshot_timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helper — pair scanner computation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_pair_scanner(session_date: str) -> pd.DataFrame:
    """
    Build the IV-ratio scanner DataFrame for the given trading session.

    session_date: 'YYYY-MM-DD' UTC date string derived from the latest
    snapshot's timestamp so the scanner shows data even after-hours.
    """
    rows = db.get_all_expiry_atm_iv_today(config.DB_PATH, session_date)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df["atm_avg_iv"] = df["atm_avg_iv"] * 100
    df = df[df["atm_avg_iv"].notna() & (df["atm_avg_iv"] > 0)]

    if df.empty:
        return pd.DataFrame()

    pivot = df.pivot_table(
        index="snapshot_timestamp",
        columns="expiry_date",
        values="atm_avg_iv",
        aggfunc="mean",
    )
    if pivot.empty:
        return pd.DataFrame()

    dte_map = (
        df.sort_values("snapshot_timestamp")
        .groupby("expiry_date")["dte"]
        .last()
        .to_dict()
    )

    expiries = sorted(pivot.columns.tolist())
    results = []

    for i, front in enumerate(expiries):
        for back in expiries[i + 1:]:
            try:
                front_date = date.fromisoformat(front)
                back_date  = date.fromisoformat(back)
            except ValueError:
                continue

            gap       = (back_date - front_date).days
            front_dte = dte_map.get(front, 0)
            back_dte  = dte_map.get(back, 0)

            if front_dte < 0 or back_dte < 0:
                continue

            ratio_s = (
                pivot[front] / pivot[back]
            ).replace(
                [float("inf"), float("-inf")], float("nan")
            ).dropna()

            if ratio_s.empty:
                continue

            vals          = ratio_s.tolist()
            current_ratio = vals[-1]
            today_high    = max(vals)
            today_low     = min(vals)
            day_change    = current_ratio - vals[0]
            drop_pct = (current_ratio - today_high) / today_high * 100 if today_high != 0 else 0.0
            rise_pct = (current_ratio - today_low)  / today_low  * 100 if today_low  != 0 else 0.0

            results.append({
                "Front":        f"{front} ({front_dte}d)",
                "Back":         f"{back} ({back_dte}d)",
                "front_expiry": front,
                "back_expiry":  back,
                "front_dte":    front_dte,
                "back_dte":     back_dte,
                "gap":          gap,
                "Ratio":        round(current_ratio, 4),
                "Day Chg":      round(day_change, 4),
                "Drop%":        round(drop_pct, 2),
                "Rise%":        round(rise_pct, 2),
                "Chart":        _sparkline(vals),
                "snapshots":    len(vals),
            })

    return pd.DataFrame(results) if results else pd.DataFrame()


def _table_col_config() -> dict:
    return {
        "Ratio":    st.column_config.NumberColumn(format="%.4f"),
        "Day Chg":  st.column_config.NumberColumn(format="%.4f"),
        "Drop%":    st.column_config.NumberColumn("Drop %", format="%.2f"),
        "Rise%":    st.column_config.NumberColumn("Rise %", format="%.2f"),
        "Chart":    st.column_config.TextColumn("Chart", width="small"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

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
poll_interval = config.POLL_INTERVAL_EVENT if event_mode else config.POLL_INTERVAL_NORMAL
if event_mode:
    st.sidebar.caption("⚡ Event Mode active — refreshing every 60s.")

st_autorefresh(interval=poll_interval * 1000, key="autorefresh")

# ─────────────────────────────────────────────────────────────────────────────
# Database init + latest complete snapshot
# ─────────────────────────────────────────────────────────────────────────────

db.init_db(config.DB_PATH)
latest_snap = db.get_latest_complete_snapshot(config.DB_PATH)

if latest_snap is None:
    st.error(
        "No complete snapshots found in the database. "
        "Make sure collector.py is running: `python collector.py`"
    )
    st.stop()

snapshot_id   = latest_snap["snapshot_id"]
spx_price     = latest_snap["underlying_price"]
vix_value     = latest_snap["vix_value"]
snap_ts_str   = latest_snap["snapshot_timestamp"]

snap_dt = datetime.strptime(snap_ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(
    tzinfo=timezone.utc
)
snap_age_secs = (datetime.now(timezone.utc) - snap_dt).total_seconds()

# Session date derived from the snapshot's own timestamp (not UTC clock)
# so the scanner works after-hours and pre-open.
session_date = snap_ts_str[:10]   # 'YYYY-MM-DD' UTC

# ─────────────────────────────────────────────────────────────────────────────
# Load option chain for latest snapshot
# ─────────────────────────────────────────────────────────────────────────────

chain_rows = db.get_option_chain(config.DB_PATH, snapshot_id)
if not chain_rows:
    st.error(
        f"Snapshot {snapshot_id} exists but has no option rows. "
        "The database may be in an inconsistent state."
    )
    st.stop()

chain_df = pd.DataFrame([dict(r) for r in chain_rows])
chain_df = chain_df.rename(columns={"expiry_date": "expiry"})
chain_df["side"] = chain_df["right"].map({"C": "CALL", "P": "PUT"})
chain_df["iv"]   = chain_df["iv"] * 100

available_expiries = sorted(chain_df["expiry"].unique())
if len(available_expiries) < 2:
    st.warning(
        "Fewer than 2 expirations in the latest snapshot. "
        "Collector may still be initializing."
    )
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# SPX intraday price series + daily change vs prior session close
# ─────────────────────────────────────────────────────────────────────────────

_intraday_rows = db.get_spx_intraday_today(config.DB_PATH, session_date)
spx_intraday = (
    pd.DataFrame([dict(r) for r in _intraday_rows])
    if _intraday_rows else pd.DataFrame()
)

if not spx_intraday.empty:
    spx_intraday["ts_et"] = (
        pd.to_datetime(
            spx_intraday["snapshot_timestamp"], format="ISO8601", utc=True
        ).dt.tz_convert(config.DISPLAY_TIMEZONE)
    )

# Daily change: current price vs prior session's last COMPLETE snapshot.
# Falls back to first intraday snapshot only if no prior data exists.
prev_close = db.get_prior_session_close(config.DB_PATH, session_date)

if prev_close is not None:
    ref_price   = prev_close
    ref_label   = f"Prev Close {prev_close:,.0f}"
elif not spx_intraday.empty:
    ref_price   = float(spx_intraday["underlying_price"].iloc[0])
    ref_label   = f"Session Open {ref_price:,.0f}"
else:
    ref_price   = spx_price
    ref_label   = ""

daily_chg_pts = spx_price - ref_price
daily_chg_pct = (daily_chg_pts / ref_price * 100) if ref_price else 0.0
day_color     = "#2ecc71" if daily_chg_pts >= 0 else "#e74c3c"
day_arrow     = "▲" if daily_chg_pts >= 0 else "▼"

# ─────────────────────────────────────────────────────────────────────────────
# GEX
# ─────────────────────────────────────────────────────────────────────────────

gex_label = "N/A"
if (
    "gamma" in chain_df.columns
    and "open_interest" in chain_df.columns
    and chain_df["gamma"].notna().any()
):
    gex_work = chain_df[
        chain_df["gamma"].notna() & chain_df["open_interest"].notna()
    ].copy()
    if not gex_work.empty:
        gex_work["net_gex"] = (
            gex_work["gamma"]
            * gex_work["open_interest"]
            * 100 * spx_price
            * gex_work["right"].map({"C": 1, "P": -1})
        )
        gex_by_strike = gex_work.groupby("strike")["net_gex"].sum()
        if not gex_by_strike.empty:
            max_strike = gex_by_strike.abs().idxmax()
            max_val    = gex_by_strike[max_strike]
            dom        = "Call" if max_val > 0 else "Put"
            gex_label  = f"{max_strike:,.0f} ({dom})"

# ─────────────────────────────────────────────────────────────────────────────
# Session-state defaults
# ─────────────────────────────────────────────────────────────────────────────

if "show_pct" not in st.session_state:
    st.session_state["show_pct"] = False


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — HEADER
# Left column: SPX price + daily change + mini intraday sparkline
# ═════════════════════════════════════════════════════════════════════════════

sign = "+" if daily_chg_pts >= 0 else ""
chg_display = (
    f"{sign}{daily_chg_pct:.2f}%"
    if st.session_state["show_pct"]
    else f"{sign}{daily_chg_pts:.1f} pts"
)

if snap_age_secs < 600:
    staleness = f"🟢 {snap_age_secs:.0f}s ago"
elif snap_age_secs < 3600:
    staleness = f"🟡 {snap_age_secs / 60:.0f}m ago — stale"
else:
    staleness = f"🔴 {snap_age_secs / 3600:.1f}h ago — collector offline?"

h_spx, h_btn, h_vix, h_gex, h_status = st.columns([4, 1, 2, 2, 4])

with h_spx:
    # SPX price + change text
    st.markdown(
        f"<h2 style='margin:0;padding:0;color:{day_color};line-height:1.1;'>"
        f"SPX {spx_price:,.2f} "
        f"<span style='font-size:0.65em;font-weight:400;'>"
        f"{day_arrow} {chg_display}</span></h2>",
        unsafe_allow_html=True,
    )
    # Mini intraday sparkline embedded in header
    if not spx_intraday.empty:
        mini_fig = go.Figure()
        mini_fig.add_trace(go.Scatter(
            x=spx_intraday["ts_et"],
            y=spx_intraday["underlying_price"],
            mode="lines",
            line=dict(color=day_color, width=1.5),
            showlegend=False,
            hoverinfo="skip",
        ))
        mini_fig.update_layout(
            height=60,
            margin=dict(l=0, r=0, t=4, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
        )
        st.plotly_chart(
            mini_fig, use_container_width=True,
            config={"displayModeBar": False}, key="mini_spx_chart"
        )

with h_btn:
    st.write("")   # vertical alignment spacer
    if st.button("pts ↔ %", key="toggle_chg"):
        st.session_state["show_pct"] = not st.session_state["show_pct"]
        st.rerun()

with h_vix:
    vix_str = f"{vix_value:.2f}" if vix_value else "N/A"
    st.metric("VIX", vix_str)

with h_gex:
    st.metric("Max |GEX| Strike", gex_label)

with h_status:
    st.caption(
        f"{staleness}  |  {snap_ts_str} UTC  |  "
        f"Refresh: {poll_interval}s"
        + ("  |  ⚡ Event Mode" if event_mode else "")
    )

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — CONTROLS ROW
# 4 columns: Front/Back Expiry, Call/Put Strike
# Max Gap lives in the Pair Scanner filter row — not here
# ═════════════════════════════════════════════════════════════════════════════

c1, c2, c3, c4 = st.columns(4)

with c1:
    front_expiry = st.selectbox(
        "Front Expiry", available_expiries, index=0, key="front_expiry_select"
    )
with c2:
    back_expiry = st.selectbox(
        "Back Expiry", available_expiries,
        index=min(1, len(available_expiries) - 1),
        key="back_expiry_select",
    )
with c3:
    default_call = float(round(spx_price / 5) * 5)
    call_strike = st.number_input(
        "Call Strike", min_value=1000.0, max_value=15000.0,
        value=default_call, step=5.0, format="%.0f",
        key="call_strike_input",
        help="Sell front CALL / Buy back CALL",
    )
with c4:
    default_put = float(round((spx_price - 100) / 5) * 5)
    put_strike = st.number_input(
        "Put Strike", min_value=1000.0, max_value=15000.0,
        value=default_put, step=5.0, format="%.0f",
        key="put_strike_input",
        help="Sell front PUT / Buy back PUT",
    )

if back_expiry <= front_expiry:
    st.warning("Back expiry ≤ Front — unusual for a diagonal, shown anyway.")

front_dte = int(chain_df[chain_df["expiry"] == front_expiry]["dte"].iloc[0])
back_dte  = int(chain_df[chain_df["expiry"] == back_expiry]["dte"].iloc[0])
strikes_set = call_strike > 0 and put_strike > 0

# Live per-strike contract data beneath controls
if strikes_set:
    lc1, lc2 = st.columns(2)
    for col, side, strike, label in [
        (lc1, "CALL", call_strike, "Call"),
        (lc2, "PUT",  put_strike,  "Put"),
    ]:
        fc = iv_engine.strike_contract(chain_df, front_expiry, strike, side)
        bc = iv_engine.strike_contract(chain_df, back_expiry,  strike, side)
        if not fc.found_exact:
            with col:
                st.warning(f"{label} {strike:.0f} not in chain — nearest {fc.strike:.0f}")
        f_iv_s = f"{fc.iv:.2f}%" if fc.iv else "N/A"
        b_iv_s = f"{bc.iv:.2f}%" if bc.iv else "N/A"
        rat_s  = f"{fc.iv / bc.iv:.4f}" if (fc.iv and bc.iv) else "N/A"
        with col:
            st.caption(
                f"**{label} {strike:.0f}** — "
                f"Front IV: {f_iv_s}  |  Back IV: {b_iv_s}  |  Ratio: {rat_s}"
            )

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# Period selector (placed here; controls both IV Structure and Calendar Edge)
# ═════════════════════════════════════════════════════════════════════════════

period_label = st.radio(
    "Chart Range",
    ["Today", "5D", "10D", "20D"],
    horizontal=True,
    label_visibility="collapsed",
    key="period_radio",
)
period_days = {"Today": 1, "5D": 5, "10D": 10, "20D": 20}[period_label]

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — IV STRUCTURE PER STRIKE  (main chart — moved up from Section 7)
# Front vs back IV at the selected trade strikes; ratio on right axis.
# This is the primary "is now a good time to enter?" visual.
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("IV Structure per Strike")
st.caption(
    "IV of your actual trade contracts — "
    "not floating ATM; reflects the diagonal legs precisely."
)

if strikes_set:
    fch = _load_contract_hist(front_expiry, call_strike, "CALL", period_days)
    bch = _load_contract_hist(back_expiry,  call_strike, "CALL", period_days)
    fph = _load_contract_hist(front_expiry, put_strike,  "PUT",  period_days)
    bph = _load_contract_hist(back_expiry,  put_strike,  "PUT",  period_days)

    call_ready = not fch.empty and not bch.empty
    put_ready  = not fph.empty and not bph.empty

    if call_ready or put_ready:
        fig_str = go.Figure()

        if call_ready:
            cm = pd.merge(
                fch[["timestamp", "iv"]].rename(columns={"iv": "f_call"}),
                bch[["timestamp", "iv"]].rename(columns={"iv": "b_call"}),
                on="timestamp", how="inner",
            )
            cm["call_ratio"] = cm["f_call"] / cm["b_call"]
            fig_str.add_trace(go.Scatter(x=cm["timestamp"], y=cm["f_call"],
                name=f"Front {call_strike:.0f}C",
                line=dict(color="#2ecc71", width=1.5), yaxis="y1"))
            fig_str.add_trace(go.Scatter(x=cm["timestamp"], y=cm["b_call"],
                name=f"Back  {call_strike:.0f}C",
                line=dict(color="#3498db", width=1.5), yaxis="y1"))
            fig_str.add_trace(go.Scatter(x=cm["timestamp"], y=cm["call_ratio"],
                name="Call Ratio (F/B)",
                line=dict(color="#e74c3c", width=1.5), yaxis="y2"))

        if put_ready:
            pm = pd.merge(
                fph[["timestamp", "iv"]].rename(columns={"iv": "f_put"}),
                bph[["timestamp", "iv"]].rename(columns={"iv": "b_put"}),
                on="timestamp", how="inner",
            )
            pm["put_ratio"] = pm["f_put"] / pm["b_put"]
            fig_str.add_trace(go.Scatter(x=pm["timestamp"], y=pm["f_put"],
                name=f"Front {put_strike:.0f}P",
                line=dict(color="#2ecc71", width=1.5, dash="dot"), yaxis="y1"))
            fig_str.add_trace(go.Scatter(x=pm["timestamp"], y=pm["b_put"],
                name=f"Back  {put_strike:.0f}P",
                line=dict(color="#3498db", width=1.5, dash="dot"), yaxis="y1"))
            fig_str.add_trace(go.Scatter(x=pm["timestamp"], y=pm["put_ratio"],
                name="Put Ratio (F/B)",
                line=dict(color="#e74c3c", width=1.5, dash="dot"), yaxis="y2"))

        fig_str.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=10, b=20),
            yaxis=dict(title="IV %", side="left"),
            yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            hovermode="x unified",
        )
        st.plotly_chart(fig_str, use_container_width=True)
    else:
        st.info(
            f"No per-strike history for {call_strike:.0f}C / {put_strike:.0f}P "
            f"in the selected range. Try 'Today'."
        )
else:
    st.caption("Enter call and put strikes in the Controls row above.")

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — HISTORICAL STATISTICS  (always Today / 5D / 10D / 20D)
# ═════════════════════════════════════════════════════════════════════════════

front_iv_atm = iv_engine.atm_iv(chain_df, front_expiry, spx_price)
back_iv_atm  = iv_engine.atm_iv(chain_df, back_expiry,  spx_price)
ts_now       = iv_engine.term_structure(front_iv_atm, back_iv_atm)

st.subheader(
    f"Historical Statistics — ATM IV Ratio  ·  "
    f"{front_expiry} ({front_dte}d)  /  {back_expiry} ({back_dte}d)"
)

stat_cols = st.columns(4)
for col, (label, days) in zip(
    stat_cols,
    [("Today", 1), ("5 Days", 5), ("10 Days", 10), ("20 Days", 20)],
):
    pf = _load_atm_hist(front_expiry, days)
    pb = _load_atm_hist(back_expiry,  days)
    with col:
        st.caption(label)
        if not pf.empty and not pb.empty:
            pm = pd.merge(
                pf[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "f"}),
                pb[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "b"}),
                on="timestamp",
            )
            pm["ratio"] = pm["f"] / pm["b"]
            rs = iv_engine.range_stats(pm["ratio"], ts_now.ratio)
            st.markdown(
                f"""<div style="font-size:0.85em">{rs.low:.4f}
<div style="background:linear-gradient(90deg,#444,#888);
height:6px;border-radius:3px;position:relative;margin:4px 0;">
<div style="position:absolute;left:{rs.position_pct}%;top:-3px;
width:12px;height:12px;background:#e74c3c;border-radius:50%;
transform:translateX(-50%);"></div></div>
{rs.high:.4f}</div>""",
                unsafe_allow_html=True,
            )
        else:
            st.caption("No data")

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# PAIR SCANNER DATA — computed once, shared by Sections 5 and 6
# ═════════════════════════════════════════════════════════════════════════════

full_scanner_df = _compute_pair_scanner(session_date)
scanner_total   = len(full_scanner_df)
scanner_snaps   = (
    int(full_scanner_df["snapshots"].max())
    if not full_scanner_df.empty else 0
)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — PINNED PAIRS
# ═════════════════════════════════════════════════════════════════════════════

pinned = _load_pinned()
st.subheader(f"⭐ Pinned Pairs  ({len(pinned)} pinned)")

if pinned and not full_scanner_df.empty:
    pinned_keys = {(p["front_expiry"], p["back_expiry"]) for p in pinned}
    pinned_df = full_scanner_df[
        full_scanner_df.apply(
            lambda r: (r["front_expiry"], r["back_expiry"]) in pinned_keys, axis=1
        )
    ].copy()

    if not pinned_df.empty:
        pin_event = st.dataframe(
            pinned_df[_TABLE_DISPLAY_COLS],
            use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="multi-row",
            key="pinned_table", column_config=_table_col_config(),
        )
        sel_pinned = (
            pin_event.selection.rows if hasattr(pin_event, "selection") else []
        )
        if sel_pinned:
            to_remove_df = pinned_df.iloc[sel_pinned]
            remove_keys  = set(zip(to_remove_df["front_expiry"], to_remove_df["back_expiry"]))
            if st.button(f"🗑️ Unpin {len(sel_pinned)} Selected", key="unpin_btn"):
                _save_pinned([p for p in pinned
                              if (p["front_expiry"], p["back_expiry"]) not in remove_keys])
                st.rerun()
    else:
        st.caption(
            "Pinned pairs have no data for the current session "
            "(may have expired or collector hasn't run yet)."
        )
elif pinned:
    st.caption("No scanner data yet for this session.")
else:
    st.caption(
        "No pinned pairs yet. "
        "Select rows in the Pair Scanner below and click **Pin Selected**."
    )

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — PAIR SCANNER
# Filter row: Min DTE | Max DTE | Max Gap  ← Max Gap lives here, not in Controls
# ═════════════════════════════════════════════════════════════════════════════

sc_hdr, sc_meta = st.columns([3, 2])
with sc_hdr:
    st.subheader("🔍 Pair Scanner")
with sc_meta:
    st.caption(
        f"{scanner_total} pairs  ·  {scanner_snaps} snapshots today"
    )

sf1, sf2, sf3, sf4 = st.columns([2, 2, 2, 1])
with sf1:
    min_dte = st.number_input(
        "Min DTE", min_value=0, max_value=60, value=0, step=1, key="min_dte"
    )
with sf2:
    max_dte_filter = st.number_input(
        "Max DTE", min_value=1, max_value=60, value=20, step=1, key="max_dte"
    )
with sf3:
    max_gap = st.number_input(
        "Max Gap (days)", min_value=1, max_value=30, value=1, step=1,
        key="max_gap_input",
        help=(
            "Maximum calendar days between front and back expiry.\n"
            "SPX daily expirations: Mon→Tue = 1d, Fri→Mon = 3d."
        ),
    )
with sf4:
    st.button("↺ Rescan", key="rescan_btn")

if not full_scanner_df.empty:
    filtered_df = full_scanner_df[
        (full_scanner_df["front_dte"] >= min_dte)
        & (full_scanner_df["front_dte"] <= max_dte_filter)
        & (full_scanner_df["gap"] <= max_gap)
    ].copy()

    filtered_df = filtered_df.sort_values("Drop%", ascending=True)

    if not filtered_df.empty:
        scan_event = st.dataframe(
            filtered_df[_TABLE_DISPLAY_COLS],
            use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="multi-row",
            key="scanner_table", column_config=_table_col_config(),
        )
        sel_scan = (
            scan_event.selection.rows if hasattr(scan_event, "selection") else []
        )
        if sel_scan:
            to_pin_df           = filtered_df.iloc[sel_scan]
            current_pinned      = _load_pinned()
            current_pinned_keys = {
                (p["front_expiry"], p["back_expiry"]) for p in current_pinned
            }
            new_entries = [
                {"front_expiry": r["front_expiry"], "back_expiry": r["back_expiry"]}
                for _, r in to_pin_df.iterrows()
                if (r["front_expiry"], r["back_expiry"]) not in current_pinned_keys
            ]
            if new_entries:
                if st.button(f"📌 Pin {len(new_entries)} New", key="pin_btn"):
                    _save_pinned(current_pinned + new_entries)
                    st.rerun()
            else:
                st.caption("All selected pairs are already pinned.")
    else:
        st.caption(
            f"No pairs match: Min DTE {min_dte}, Max DTE {max_dte_filter}, "
            f"Max Gap {int(max_gap)}d.  Try increasing Max Gap or DTE range."
        )
else:
    st.caption(
        "No scanner data for this session yet. "
        "Make sure collector.py is running and has completed at least one cycle."
    )

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — CALENDAR EDGE
# ATM IV chart + ratio metrics + day-change for the selected pair.
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("Calendar Edge")

iv_index = float(chain_df.groupby("expiry")["iv"].mean().mean())
m1, m2, m3, m4 = st.columns(4)
m1.metric("ATM IV Ratio (F/B)", f"{ts_now.ratio:.4f}")
m2.metric("Front ATM IV",       f"{ts_now.front_iv:.2f}%")
m3.metric("Back ATM IV",        f"{ts_now.back_iv:.2f}%")
m4.metric("IV Index (avg)",     f"{iv_index:.2f}%")

st.info(iv_engine.interpret_curve(ts_now))

front_hist = _load_atm_hist(front_expiry, period_days)
back_hist  = _load_atm_hist(back_expiry,  period_days)

atm_merged = pd.DataFrame()
if not front_hist.empty and not back_hist.empty:
    atm_merged = pd.merge(
        front_hist[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "front_iv"}),
        back_hist [["timestamp", "atm_iv"]].rename(columns={"atm_iv": "back_iv"}),
        on="timestamp", how="inner",
    )
    atm_merged["iv_ratio"] = atm_merged["front_iv"] / atm_merged["back_iv"]

if not atm_merged.empty:
    fig_atm = go.Figure()
    fig_atm.add_trace(go.Scatter(x=atm_merged["timestamp"], y=atm_merged["front_iv"],
        name="Front ATM IV", line=dict(color="#2ecc71", width=1.5), yaxis="y1"))
    fig_atm.add_trace(go.Scatter(x=atm_merged["timestamp"], y=atm_merged["back_iv"],
        name="Back ATM IV",  line=dict(color="#3498db", width=1.5), yaxis="y1"))
    fig_atm.add_trace(go.Scatter(x=atm_merged["timestamp"], y=atm_merged["iv_ratio"],
        name="IV Ratio (F/B)", line=dict(color="#e74c3c", width=1.5), yaxis="y2"))
    fig_atm.update_layout(
        height=300,
        margin=dict(l=20, r=20, t=10, b=20),
        yaxis=dict(title="IV %", side="left"),
        yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    st.plotly_chart(fig_atm, use_container_width=True)

    samp_warn = iv_engine.sample_size_warning(atm_merged["iv_ratio"])
    if samp_warn:
        st.warning(samp_warn)
else:
    st.caption(
        f"No ATM IV history for {front_expiry} / {back_expiry} in the selected range."
    )

dc1, dc2 = st.columns(2)
for col, label, exp, dte in [
    (dc1, "Front", front_expiry, front_dte),
    (dc2, "Back",  back_expiry,  back_dte),
]:
    latest_rows = db.get_latest_atm_iv_snapshots(config.DB_PATH, exp, n=2)
    with col:
        if latest_rows:
            lat_iv = latest_rows[0]["atm_avg_iv"] * 100
            chg_iv = (
                (latest_rows[0]["atm_avg_iv"] - latest_rows[1]["atm_avg_iv"]) * 100
                if len(latest_rows) == 2 else 0.0
            )
            st.metric(f"{label} ATM IV  ({dte} DTE)", f"{lat_iv:.2f}%", f"{chg_iv:+.2f}%")
        else:
            st.metric(f"{label} ATM IV  ({dte} DTE)", "N/A")

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — TRANSFORM CREDIT  (very bottom)
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("Transform Credit")

near_front = chain_df[chain_df["expiry"] == front_expiry]
atm_row    = near_front.iloc[(near_front["strike"] - spx_price).abs().argsort()[:1]]
liquidity  = iv_engine.liquidity_score(
    atm_row["volume"].fillna(0).mean(),
    atm_row["open_interest"].fillna(0).mean(),
)
iv_pct = (
    iv_engine.percentile_rank(atm_merged["iv_ratio"], ts_now.ratio)
    if not atm_merged.empty else 50
)
theta_adv = 50
score     = iv_engine.trade_quality_score(iv_pct, liquidity, theta_adv)

s1, s2, s3, s4 = st.columns(4)
s1.metric("Overall Score",            f"{score:.0f} / 100")
s2.metric("IV Edge (percentile)",     f"{iv_pct:.0f}")
s3.metric("Liquidity",                f"{liquidity:.0f}")
s4.metric("Theta Adv. (placeholder)", f"{theta_adv:.0f}")

st.caption(
    "Full transform credit calculator (back-leg value − wing cost − entry debit) "
    "coming in Phase 3."
)
