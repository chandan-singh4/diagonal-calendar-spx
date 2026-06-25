"""
app.py — Dashboard.  Run with: streamlit run app.py

Pure reader — all writes are handled exclusively by collector.py.
No Schwab API calls. No DB writes.

Data sources (all read from dashboard.db):
  snapshots          → latest SPX price, VIX, snapshot timestamp
  option_rows        → current option chain for selectors, metrics, chain table
  atm_iv_by_expiry   → ATM IV history for charts, day-change metrics, range stats

IV SCALE NOTE
  option_rows and atm_iv_by_expiry store IVs as decimals (0.18 = 18%).
  This file multiplies by 100 at every load boundary so all downstream
  iv_engine calls and chart code continue to operate in percentage form
  (18.0 = 18%), matching the original chain_df structure.
"""
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config
import db
import iv_engine

logger = logging.getLogger(__name__)

st.set_page_config(page_title="SPX Diagonal Calendar Analyzer", layout="wide")

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _load_atm_hist(expiry: str, days: int) -> pd.DataFrame:
    """
    ATM IV history for one expiry over the last N days.
    Returns DataFrame with columns: timestamp (tz-aware ET), atm_iv (% form).
    Returns empty DataFrame if no data exists for that expiry / range.
    """
    rows = db.get_atm_iv_history(config.DB_PATH, expiry, days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={"snapshot_timestamp": "timestamp",
                              "atm_avg_iv": "atm_iv"})
    df["atm_iv"] = df["atm_iv"] * 100   # decimal → percentage
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
    )
    return df


def _load_contract_hist(expiry: str, strike: float,
                         side: str, days: int) -> pd.DataFrame:
    """
    Per-contract IV history for one leg over the last N days.
    side: 'CALL' or 'PUT' — converted to 'C'/'P' for the DB query.
    Returns DataFrame with columns: timestamp (tz-aware ET), iv (% form).
    Returns empty DataFrame if no data exists.
    """
    right_char = "C" if side == "CALL" else "P"
    rows = db.get_contract_iv_history(config.DB_PATH, expiry, strike, right_char, days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["iv"] = df["iv"] * 100   # decimal → percentage
    df["timestamp"] = (
        pd.to_datetime(df["snapshot_timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
    )
    return df


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("Settings")

event_mode = st.sidebar.toggle(
    "⚡ Event Mode (60s refresh)",
    value=False,
    help=(
        "Increases the dashboard refresh rate to 60s during high-impact events "
        "(FOMC, CPI, NFP, PPI, Powell speeches). "
        "The collector polls independently on its own schedule — "
        "this only controls how often the dashboard re-reads the database."
    ),
)
poll_interval = config.POLL_INTERVAL_EVENT if event_mode else config.POLL_INTERVAL_NORMAL
if event_mode:
    st.sidebar.caption("⚡ Event Mode active — dashboard refreshing every 60s.")

st_autorefresh(interval=poll_interval * 1000, key="autorefresh")

# ---------------------------------------------------------------------------
# Initialize DB and pull latest snapshot
# ---------------------------------------------------------------------------
db.init_db(config.DB_PATH)

latest_snap = db.get_latest_complete_snapshot(config.DB_PATH)
if latest_snap is None:
    st.error(
        "No complete snapshots found in the database. "
        "Make sure collector.py is running: `python collector.py`"
    )
    st.stop()

snapshot_id = latest_snap["snapshot_id"]
spx_price   = latest_snap["underlying_price"]
vix_value   = latest_snap["vix_value"]
snap_ts_str = latest_snap["snapshot_timestamp"]   # e.g. '2026-06-24 19:59:57'

# Compute data staleness — how old is the snapshot we're showing
snap_dt = datetime.strptime(snap_ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(
    tzinfo=timezone.utc
)
snap_age_secs = (datetime.now(timezone.utc) - snap_dt).total_seconds()

# ---------------------------------------------------------------------------
# Reconstruct chain_df from option_rows
#
# Column mapping:
#   option_rows.expiry_date → chain_df.expiry
#   option_rows.right ('C'/'P') → chain_df.right (kept) + chain_df.side ('CALL'/'PUT')
#   option_rows.iv (decimal) → chain_df.iv (×100 → percentage)
#
# iv_engine functions (atm_iv, strike_contract, etc.) expect percentage-form IVs
# and 'CALL'/'PUT' side values — both enforced here at the load boundary.
# ---------------------------------------------------------------------------
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
chain_df["iv"]   = chain_df["iv"] * 100   # decimal → percentage

available_expiries = sorted(chain_df["expiry"].unique())
if len(available_expiries) < 2:
    st.warning(
        "Fewer than 2 expirations in the latest snapshot. "
        "Collector may still be initializing."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("SPX Diagonal Calendar Analyzer")

if snap_age_secs < 600:
    staleness_str = f"🟢 {snap_age_secs:.0f}s ago"
elif snap_age_secs < 3600:
    staleness_str = f"🟡 Stale — {snap_age_secs/60:.0f}m ago"
else:
    staleness_str = f"🔴 Stale — {snap_age_secs/3600:.1f}h ago (collector offline?)"

vix_str = f"{vix_value:.2f}" if vix_value else "N/A"

st.caption(
    f"SPX: **{spx_price:,.2f}**  |  VIX: **{vix_str}**  |  "
    f"Snapshot: {snap_ts_str} UTC  |  {staleness_str}  |  "
    f"Auto-refresh: {poll_interval}s"
    + ("  |  ⚡ Event Mode" if event_mode else "")
)

# ---------------------------------------------------------------------------
# Two-column layout
# ---------------------------------------------------------------------------
left, right = st.columns([1, 3])

# ── LEFT PANEL ──────────────────────────────────────────────────────────────
with left:
    st.subheader("Expirations")

    default_back = min(1, len(available_expiries) - 1)
    front_expiry = st.selectbox(
        "Front", available_expiries, index=0, key="front_expiry_select"
    )
    back_expiry = st.selectbox(
        "Back", available_expiries, index=default_back, key="back_expiry_select"
    )
    if back_expiry <= front_expiry:
        st.warning("Back expiry ≤ Front — unusual for a diagonal, shown anyway.")

    front_dte = int(chain_df[chain_df["expiry"] == front_expiry]["dte"].iloc[0])
    back_dte  = int(chain_df[chain_df["expiry"] == back_expiry]["dte"].iloc[0])

    # Day-change metrics — pull last 2 ATM IV records from atm_iv_by_expiry
    for label, exp, dte in [
        ("Front", front_expiry, front_dte),
        ("Back",  back_expiry,  back_dte),
    ]:
        rows = db.get_latest_atm_iv_snapshots(config.DB_PATH, exp, n=2)
        if rows:
            latest_iv = rows[0]["atm_avg_iv"] * 100   # decimal → percentage
            change = (
                (rows[0]["atm_avg_iv"] - rows[1]["atm_avg_iv"]) * 100
                if len(rows) == 2 else 0.0
            )
            st.metric(f"{label} ({dte} DTE)", f"{latest_iv:.2f}%", f"{change:+.2f}")
        else:
            st.metric(f"{label} ({dte} DTE)", "N/A")

    st.divider()

    # ── Strike selector ──────────────────────────────────────────────────────
    st.subheader("Strike Selection")
    st.caption(
        "Same strikes applied to both front (short) and back (long) expiries."
    )

    default_call = float(round(spx_price / 5) * 5)
    default_put  = float(round((spx_price - 100) / 5) * 5)

    call_strike = st.number_input(
        "Call Strike (OTM / short call)",
        min_value=1000.0, max_value=15000.0,
        value=default_call, step=5.0, format="%.0f",
        key="call_strike_input",
        help="Strike for: Sell front CALL / Buy back CALL",
    )
    put_strike = st.number_input(
        "Put Strike (OTM / short put)",
        min_value=1000.0, max_value=15000.0,
        value=default_put, step=5.0, format="%.0f",
        key="put_strike_input",
        help="Strike for: Sell front PUT / Buy back PUT",
    )

    strikes_set = call_strike > 0 and put_strike > 0

    # Live contract data — sourced from the current snapshot's option_rows
    if strikes_set:
        st.caption("**Current contract data (latest snapshot):**")
        for side, strike, label in [
            ("CALL", call_strike, "Call"),
            ("PUT",  put_strike,  "Put"),
        ]:
            fc = iv_engine.strike_contract(chain_df, front_expiry, strike, side)
            bc = iv_engine.strike_contract(chain_df, back_expiry,  strike, side)
            if not fc.found_exact:
                st.warning(
                    f"{label} {strike:.0f} not found — showing nearest {fc.strike:.0f}"
                )
            f_iv_str  = f"{fc.iv:.2f}%"          if fc.iv             else "N/A"
            b_iv_str  = f"{bc.iv:.2f}%"          if bc.iv             else "N/A"
            ratio_str = f"{fc.iv / bc.iv:.4f}"   if (fc.iv and bc.iv) else "N/A"
            st.markdown(
                f"**{label} {strike:.0f}** — "
                f"Front IV: {f_iv_str} | Back IV: {b_iv_str} | Ratio: {ratio_str}"
            )


# ── RIGHT PANEL ──────────────────────────────────────────────────────────────
with right:

    # ── ATM IV term structure — top metric strip ─────────────────────────────
    front_iv = iv_engine.atm_iv(chain_df, front_expiry, spx_price)
    back_iv  = iv_engine.atm_iv(chain_df, back_expiry,  spx_price)
    ts       = iv_engine.term_structure(front_iv, back_iv)
    iv_index = float(chain_df.groupby("expiry")["iv"].mean().mean())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("IV Ratio (F/B) — ATM",    f"{ts.ratio:.4f}")
    m2.metric("Front IV % — ATM",         f"{ts.front_iv:.2f}%")
    m3.metric("Back IV % — ATM",          f"{ts.back_iv:.2f}%")
    m4.metric("IV Index (all expiries)",  f"{iv_index:.2f}%")
    st.info(iv_engine.interpret_curve(ts))

    # ── Time range selector ──────────────────────────────────────────────────
    period_label = st.radio(
        "Range", ["Today", "5D", "10D", "15D", "1M"],
        horizontal=True, label_visibility="collapsed"
    )
    period_days = {"Today": 1, "5D": 5, "10D": 10, "15D": 15, "1M": 30}[period_label]

    # ── Fetch ATM IV history ─────────────────────────────────────────────────
    front_hist = _load_atm_hist(front_expiry, period_days)
    back_hist  = _load_atm_hist(back_expiry,  period_days)

    atm_merged = pd.DataFrame()
    if not front_hist.empty and not back_hist.empty:
        atm_merged = pd.merge(
            front_hist[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "front_iv"}),
            back_hist[["timestamp",  "atm_iv"]].rename(columns={"atm_iv": "back_iv"}),
            on="timestamp", how="inner",
        )
        atm_merged["iv_ratio"] = atm_merged["front_iv"] / atm_merged["back_iv"]

    # ── TOP CHART — Selected-strike IV ──────────────────────────────────────
    if strikes_set:
        st.markdown("#### Selected-Strike IV  *(your actual trade contracts)*")

        fch = _load_contract_hist(front_expiry, call_strike, "CALL", period_days)
        bch = _load_contract_hist(back_expiry,  call_strike, "CALL", period_days)
        fph = _load_contract_hist(front_expiry, put_strike,  "PUT",  period_days)
        bph = _load_contract_hist(back_expiry,  put_strike,  "PUT",  period_days)

        call_history_ready = not fch.empty and not bch.empty
        put_history_ready  = not fph.empty and not bph.empty

        if call_history_ready or put_history_ready:
            fig_strike = go.Figure()

            if call_history_ready:
                call_merged = pd.merge(
                    fch[["timestamp", "iv"]].rename(columns={"iv": "f_call_iv"}),
                    bch[["timestamp", "iv"]].rename(columns={"iv": "b_call_iv"}),
                    on="timestamp", how="inner",
                )
                call_merged["call_ratio"] = (
                    call_merged["f_call_iv"] / call_merged["b_call_iv"]
                )
                fig_strike.add_trace(go.Scatter(
                    x=call_merged["timestamp"], y=call_merged["f_call_iv"],
                    name=f"Front {call_strike:.0f}C IV",
                    line=dict(color="#2ecc71", width=1.5), yaxis="y1",
                ))
                fig_strike.add_trace(go.Scatter(
                    x=call_merged["timestamp"], y=call_merged["b_call_iv"],
                    name=f"Back {call_strike:.0f}C IV",
                    line=dict(color="#3498db", width=1.5), yaxis="y1",
                ))
                fig_strike.add_trace(go.Scatter(
                    x=call_merged["timestamp"], y=call_merged["call_ratio"],
                    name="Call IV Ratio (F/B)",
                    line=dict(color="#e74c3c", width=1.5), yaxis="y2",
                ))

            if put_history_ready:
                put_merged = pd.merge(
                    fph[["timestamp", "iv"]].rename(columns={"iv": "f_put_iv"}),
                    bph[["timestamp", "iv"]].rename(columns={"iv": "b_put_iv"}),
                    on="timestamp", how="inner",
                )
                put_merged["put_ratio"] = (
                    put_merged["f_put_iv"] / put_merged["b_put_iv"]
                )
                fig_strike.add_trace(go.Scatter(
                    x=put_merged["timestamp"], y=put_merged["f_put_iv"],
                    name=f"Front {put_strike:.0f}P IV",
                    line=dict(color="#2ecc71", width=1.5, dash="dot"), yaxis="y1",
                ))
                fig_strike.add_trace(go.Scatter(
                    x=put_merged["timestamp"], y=put_merged["b_put_iv"],
                    name=f"Back {put_strike:.0f}P IV",
                    line=dict(color="#3498db", width=1.5, dash="dot"), yaxis="y1",
                ))
                fig_strike.add_trace(go.Scatter(
                    x=put_merged["timestamp"], y=put_merged["put_ratio"],
                    name="Put IV Ratio (F/B)",
                    line=dict(color="#e74c3c", width=1.5, dash="dot"), yaxis="y2",
                ))

            fig_strike.update_layout(
                height=340,
                margin=dict(l=20, r=20, t=10, b=20),
                yaxis=dict(title="IV %", side="left"),
                yaxis2=dict(title="Ratio", side="right",
                             overlaying="y", showgrid=False),
                legend=dict(orientation="h", yanchor="bottom",
                             y=1.02, xanchor="left", x=0),
                hovermode="x unified",
            )
            st.plotly_chart(fig_strike, use_container_width=True)
        else:
            st.info(
                f"No strike-specific history found for "
                f"{call_strike:.0f}C / {put_strike:.0f}P "
                f"in the selected range. The collector records all strikes — "
                f"try 'Today' or confirm these strikes exist in the chain."
            )
    else:
        st.markdown("#### Selected-Strike IV")
        st.caption(
            "Enter call and put strikes in the left panel "
            "to see strike-specific IV history here."
        )

    # ── BOTTOM CHART — ATM IV ────────────────────────────────────────────────
    st.markdown("#### ATM IV  *(macro context — floating strike nearest spot)*")

    if not atm_merged.empty:
        fig_atm = go.Figure()
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["front_iv"],
            name="Front ATM IV", line=dict(color="#2ecc71", width=1.5), yaxis="y1",
        ))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["back_iv"],
            name="Back ATM IV", line=dict(color="#3498db", width=1.5), yaxis="y1",
        ))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["iv_ratio"],
            name="IV Ratio (F/B)", line=dict(color="#e74c3c", width=1.5), yaxis="y2",
        ))
        fig_atm.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=10, b=20),
            yaxis=dict(title="IV %", side="left"),
            yaxis2=dict(title="Ratio", side="right",
                         overlaying="y", showgrid=False),
            legend=dict(orientation="h", yanchor="bottom",
                         y=1.02, xanchor="left", x=0),
            hovermode="x unified",
        )
        st.plotly_chart(fig_atm, use_container_width=True)

        # ── Historical range stats (ATM ratio) ────────────────────────────────
        st.subheader("Historical Stats — ATM IV Ratio")
        stat_cols = st.columns(5)
        for col, (label, days) in zip(
            stat_cols,
            [("Today", 1), ("5 Days", 5), ("10 Days", 10),
             ("15 Days", 15), ("1 Month", 30)],
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
                    rs = iv_engine.range_stats(pm["ratio"], ts.ratio)
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
                    st.caption("No data yet")

        warning = iv_engine.sample_size_warning(atm_merged["iv_ratio"])
        if warning:
            st.warning(warning)
    else:
        st.caption(
            f"No ATM IV history for {front_expiry} / {back_expiry} "
            f"in the selected range. Try 'Today' — data collection started 6/23."
        )

# ---------------------------------------------------------------------------
# Trade Quality Score
# ---------------------------------------------------------------------------
st.subheader("Trade Quality Score")
near_front = chain_df[chain_df["expiry"] == front_expiry]
atm_row = near_front.iloc[(near_front["strike"] - spx_price).abs().argsort()[:1]]
liquidity = iv_engine.liquidity_score(
    atm_row["volume"].fillna(0).mean(),
    atm_row["open_interest"].fillna(0).mean(),
)
iv_pct = (
    iv_engine.percentile_rank(atm_merged["iv_ratio"], ts.ratio)
    if not atm_merged.empty
    else 50
)
theta_advantage = 50   # placeholder — Phase 3
score = iv_engine.trade_quality_score(iv_pct, liquidity, theta_advantage)

s1, s2, s3, s4 = st.columns(4)
s1.metric("Overall Score",             f"{score:.0f} / 100")
s2.metric("IV Edge (ATM percentile)",  f"{iv_pct:.0f}")
s3.metric("Liquidity",                 f"{liquidity:.0f}")
s4.metric("Theta Adv. (placeholder)", f"{theta_advantage:.0f}")

# ---------------------------------------------------------------------------
# Options chain table
# ---------------------------------------------------------------------------
st.subheader(f"Options Chain — {front_expiry}")
display_cols = [
    "strike", "side", "bid", "ask", "iv",
    "volume", "open_interest", "delta",
]
chain_view = (
    chain_df[chain_df["expiry"] == front_expiry][display_cols]
    .sort_values("strike")
)
st.dataframe(chain_view, use_container_width=True, height=400)
