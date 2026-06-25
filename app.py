"""
app.py — SPX Diagonal Calendar Analyzer · Dashboard v1
Run with:  streamlit run app.py

Pure reader — all writes handled exclusively by collector.py.
No Schwab API calls. No DB writes.

Dashboard v1 new panels (2026-06-25):
  1. IV Structure Panel  — per-strike IV ratio, regime color, 30-min sparkline
  2. Calendar Edge Panel — call edge vs put edge, independent side trend
  3. Transform Credit    — theoretical lock-in profit, threshold status, leg breakdown

Data sources (all read from dashboard.db):
  snapshots         → latest SPX price, VIX, snapshot timestamp
  option_rows       → current option chain (marks, greeks, IV)
  atm_iv_by_expiry  → ATM IV history for charts and stats

IV SCALE NOTE
  option_rows and atm_iv_by_expiry store IVs as decimals (0.18 = 18%).
  This file multiplies by 100 at every load boundary so all downstream
  iv_engine calls and chart code operate in percentage form (18.0 = 18%).
"""

import logging
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config
import db
import iv_engine

logger = logging.getLogger(__name__)

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SPX Diagonal Analyzer",
    layout="wide",
    page_icon="📈",
    initial_sidebar_state="expanded",
)

# ─── CSS — dark terminal aesthetic ───────────────────────────────────────────
# Design: GitHub-dark palette (#0d1117 / #161b22) with a periwinkle data accent
# (#7b8cde) for ratio sparklines. Regime status uses left accent borders rather
# than background fills — lets you read regime at a glance from across the room.
st.markdown("""
<style>
/* ── Panel tiles ── */
.panel-wrap {
    background: #161b22;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 4px;
    padding: 14px 16px 10px 16px;
    margin-bottom: 6px;
    min-height: 200px;
}
.panel-title {
    font-size: 0.72em;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: rgba(201,209,217,0.45);
    text-transform: uppercase;
    margin-bottom: 12px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    padding-bottom: 6px;
}
/* ── Regime badge ── */
.regime-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 0.75em;
    font-weight: 700;
    letter-spacing: 0.09em;
    font-family: 'JetBrains Mono', 'Consolas', monospace;
}
/* ── Big ratio number ── */
.ratio-num {
    font-size: 2.1em;
    font-weight: 700;
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    line-height: 1.05;
    letter-spacing: -0.02em;
}
/* ── Edge number ── */
.edge-num {
    font-size: 2.1em;
    font-weight: 700;
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    line-height: 1.05;
}
/* ── Credit number ── */
.credit-num {
    font-size: 2.4em;
    font-weight: 700;
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    line-height: 1.0;
}
/* ── Sub-labels ── */
.sub-label {
    font-size: 0.82em;
    color: rgba(201,209,217,0.5);
    margin-top: 2px;
}
/* ── Leg breakdown rows ── */
.leg-row {
    display: flex;
    justify-content: space-between;
    font-size: 0.86em;
    padding: 3px 0;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    color: #c9d1d9;
}
.leg-row:last-child { border-bottom: none; }
/* ── Side column divider within panels ── */
.side-label {
    font-size: 0.78em;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: rgba(201,209,217,0.6);
    text-transform: uppercase;
    margin-bottom: 6px;
}
/* ── Thin separator ── */
.mini-sep {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.06);
    margin: 8px 0;
}
</style>
""", unsafe_allow_html=True)


# ─── Helpers ─────────────────────────────────────────────────────────────────

_ET = pytz.timezone(config.DISPLAY_TIMEZONE)


def _load_atm_hist(expiry: str, days: int) -> pd.DataFrame:
    """ATM IV history for one expiry over the last N days → ET timestamps, IV in %."""
    rows = db.get_atm_iv_history(config.DB_PATH, expiry, days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={"snapshot_timestamp": "timestamp", "atm_avg_iv": "atm_iv"})
    df["atm_iv"] = df["atm_iv"] * 100
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
    )
    return df


def _load_contract_hist(expiry: str, strike: float, side: str, days: int) -> pd.DataFrame:
    """Per-contract IV history → ET timestamps, IV in %."""
    right_char = "C" if side == "CALL" else "P"
    rows = db.get_contract_iv_history(config.DB_PATH, expiry, strike, right_char, days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["iv"] = df["iv"] * 100
    df["timestamp"] = (
        pd.to_datetime(df["snapshot_timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
    )
    return df


def _mini_ratio_chart(front_df: pd.DataFrame, back_df: pd.DataFrame) -> go.Figure | None:
    """Compact ratio sparkline for IV Structure panel (periwinkle line, no axes)."""
    if front_df.empty or back_df.empty:
        return None
    m = pd.merge(
        front_df[["timestamp", "iv"]].rename(columns={"iv": "f"}),
        back_df [["timestamp", "iv"]].rename(columns={"iv": "b"}),
        on="timestamp", how="inner",
    )
    if len(m) < 2:
        return None
    m["ratio"] = m["f"] / m["b"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=m["timestamp"], y=m["ratio"],
        mode="lines",
        line=dict(color="#7b8cde", width=1.8),
        fill="tozeroy", fillcolor="rgba(123,140,222,0.07)",
        hovertemplate="Ratio: %{y:.4f}<br>%{x|%H:%M}<extra></extra>",
    ))
    fig.add_hline(y=1.0, line_dash="dot",
                  line_color="rgba(255,255,255,0.18)", line_width=1)
    fig.update_layout(
        height=90, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
        yaxis=dict(showticklabels=True, showgrid=False, zeroline=False,
                   tickfont=dict(size=8, color="rgba(201,209,217,0.4)"),
                   side="right", tickformat=".3f"),
        hovermode="x unified",
    )
    return fig


def _mini_edge_chart(merged_df: pd.DataFrame, color: str) -> go.Figure | None:
    """Compact edge sparkline for Calendar Edge panel."""
    if merged_df.empty or len(merged_df) < 2:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=merged_df["timestamp"], y=merged_df["edge"],
        mode="lines",
        line=dict(color=color, width=1.8),
        hovertemplate="Edge: %{y:+.2f}%<br>%{x|%H:%M}<extra></extra>",
    ))
    fig.add_hline(y=0, line_dash="dot",
                  line_color="rgba(255,255,255,0.18)", line_width=1)
    fig.update_layout(
        height=90, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
        yaxis=dict(showticklabels=True, showgrid=False, zeroline=False,
                   tickfont=dict(size=8, color="rgba(201,209,217,0.4)"),
                   side="right", ticksuffix="%", tickformat="+.1f"),
        hovermode="x unified",
    )
    return fig


# ─── TERM-STRUCTURE LABELLING (NEUTRAL — see DOCUMENTATION.md v1.1) ───────────
# Favorability by IV ratio is an UNVALIDATED hypothesis (audit 2026-06-25).
# These helpers describe term-structure SHAPE only and never encode good/bad.
#
# Standard volatility term-structure terminology:
#   ratio > 1.0  → front IV > back IV  → FRONT-ELEVATED (backwardation / "inverted")
#   ratio < 1.0  → front IV < back IV  → BACK-ELEVATED  (contango / "normal")
#   ratio ≈ 1.0  → FLAT
#
# NOTE: As of 2026-06-25, iv_engine.iv_regime() and iv_engine.interpret_curve()
# have ALSO been corrected to neutral, terminologically-accurate output. The local
# helpers below are retained because they carry finer banding (↑↑ / ↓↓) tuned for
# the panel display. Engine and app now agree in meaning; neither implies good/bad.

_ACCENT_FRONT = "#7b8cde"   # periwinkle — front-elevated (neutral accent)
_ACCENT_BACK  = "#6aa0a8"   # slate-teal — back-elevated  (neutral accent)
_ACCENT_FLAT  = "#8b94a6"   # grey       — flat
_ACCENT_NA    = "#6b7280"   # muted grey — no data


def _neutral_regime(ratio: float | None) -> tuple[str, str]:
    """
    Returns (label, hex_color) describing term-structure shape only.
    No favorability is implied; colors are neutral accents, not green/red.
    """
    if ratio is None or (isinstance(ratio, float) and math.isnan(ratio)):
        return "N/A", _ACCENT_NA
    if ratio >= 1.10:
        return "FRONT-ELEVATED ↑↑", _ACCENT_FRONT
    elif ratio > 1.02:
        return "FRONT-ELEVATED ↑", _ACCENT_FRONT
    elif ratio >= 0.98:
        return "FLAT", _ACCENT_FLAT
    elif ratio > 0.90:
        return "BACK-ELEVATED ↓", _ACCENT_BACK
    else:
        return "BACK-ELEVATED ↓↓", _ACCENT_BACK


def _edge_color(edge_val: float | None) -> str:
    """
    Neutral accent color for the Calendar Edge panel.
    edge = front_iv - back_iv.  Positive = front-elevated; negative = back-elevated.
    Colors carry NO favorability judgment.
    """
    if edge_val is None:
        return _ACCENT_NA
    if edge_val > 0.10:
        return _ACCENT_FRONT     # front above back
    elif edge_val < -0.10:
        return _ACCENT_BACK      # back above front
    else:
        return _ACCENT_FLAT      # essentially flat


def _edge_label(edge_val: float | None) -> str:
    """Neutral directional label. edge = front_iv - back_iv."""
    if edge_val is None:
        return "—"
    if edge_val > 0.10:
        return "Front-Elevated ↑"   # front IV above back (backwardation)
    elif edge_val < -0.10:
        return "Back-Elevated ↓"    # front IV below back (contango)
    else:
        return "Flat"


def _describe_curve(ts) -> str:
    """
    Neutral, non-judgmental description of the ATM term-structure shape.
    Replaces iv_engine.interpret_curve(), which used favorable/unfavorable
    language now retracted (see DOCUMENTATION.md v1.1).
    """
    r = ts.ratio
    if math.isnan(r):
        return "Term structure unavailable (missing ATM IV on one expiry)."
    if r >= 1.10:
        shape = ("Strong backwardation — front ATM IV well above back. "
                 "Near-term IV is elevated relative to the back month.")
    elif r > 1.02:
        shape = ("Backwardation — front ATM IV above back. "
                 "Near-term IV sits above the back month.")
    elif r >= 0.98:
        shape = ("Flat term structure — front and back ATM IV are close. "
                 "Little IV differential across the two expiries.")
    elif r > 0.90:
        shape = ("Contango — front ATM IV below back. "
                 "Back-month IV sits above the near term.")
    else:
        shape = ("Steep contango — front ATM IV well below back. "
                 "Back-month IV is substantially above the near term.")
    return (
        f"{shape}  ·  Ratio {r:.4f}.  "
        "Whether this shape favors entry is an open, unvalidated question — "
        "treat as neutral context, not a buy/avoid signal."
    )


# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Settings")

    event_mode = st.toggle(
        "⚡ Event Mode (60s refresh)",
        value=False,
        help=(
            "Activate before FOMC, CPI, NFP, PPI or any high-impact event. "
            "Switches dashboard refresh to 60s. "
            "The collector polls on its own independent schedule."
        ),
    )
    if event_mode:
        st.caption("⚡ Event Mode active.")

    st.markdown("---")
    st.markdown("### 📐 Active Position")
    st.caption("Drives the Transform Credit panel.")

    if "entry_debit" not in st.session_state:
        st.session_state["entry_debit"] = 9.00
    if "transform_threshold" not in st.session_state:
        st.session_state["transform_threshold"] = 5.00

    entry_debit = st.number_input(
        "Entry Debit ($)",
        min_value=0.0, max_value=500.0,
        value=st.session_state["entry_debit"],
        step=0.25, format="%.2f",
        key="entry_debit_input",
        help="Net debit you paid to open the diagonal (e.g. 9.00).",
    )
    st.session_state["entry_debit"] = entry_debit

    transform_threshold = st.number_input(
        "Transform Threshold ($)",
        min_value=0.0, max_value=100.0,
        value=st.session_state["transform_threshold"],
        step=0.25, format="%.2f",
        key="threshold_input",
        help="Minimum theoretical credit to trigger Iron Condor transformation.",
    )
    st.session_state["transform_threshold"] = transform_threshold

    st.markdown("---")
    st.caption(
        "Entry Debit and Threshold persist across refreshes this session. "
        "Reset them each time you open a new trade."
    )

poll_interval = config.POLL_INTERVAL_EVENT if event_mode else config.POLL_INTERVAL_NORMAL
st_autorefresh(interval=poll_interval * 1000, key="autorefresh")

# ─── DB init + latest snapshot ───────────────────────────────────────────────
db.init_db(config.DB_PATH)
latest_snap = db.get_latest_complete_snapshot(config.DB_PATH)

if latest_snap is None:
    st.error(
        "No complete snapshots in the database. "
        "Start the collector: `python collector.py`"
    )
    st.stop()

snapshot_id = latest_snap["snapshot_id"]
spx_price   = latest_snap["underlying_price"]
vix_value   = latest_snap["vix_value"]
snap_ts_str = latest_snap["snapshot_timestamp"]

snap_dt = datetime.strptime(snap_ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(
    tzinfo=timezone.utc
)
snap_age_secs = (datetime.now(timezone.utc) - snap_dt).total_seconds()

# ─── Chain load ───────────────────────────────────────────────────────────────
chain_rows = db.get_option_chain(config.DB_PATH, snapshot_id)
if not chain_rows:
    st.error(
        f"Snapshot {snapshot_id} has no option rows. "
        "The database may be in an inconsistent state."
    )
    st.stop()

chain_df = pd.DataFrame([dict(r) for r in chain_rows])
chain_df = chain_df.rename(columns={"expiry_date": "expiry"})
chain_df["side"] = chain_df["right"].map({"C": "CALL", "P": "PUT"})
chain_df["iv"]   = chain_df["iv"] * 100   # decimal → %

# Ensure mark column (fallback to mid if pre-computed column absent)
if "mark" not in chain_df.columns or chain_df["mark"].isna().all():
    if "bid" in chain_df.columns and "ask" in chain_df.columns:
        chain_df["mark"] = (chain_df["bid"].fillna(0) + chain_df["ask"].fillna(0)) / 2

available_expiries = sorted(chain_df["expiry"].unique())

if len(available_expiries) < 2:
    st.warning(
        "Fewer than 2 expirations in the latest snapshot. "
        "Collector may still be initializing."
    )
    st.stop()


# ─── HEADER ──────────────────────────────────────────────────────────────────
if snap_age_secs < 600:
    stale_dot = "🟢"
    stale_txt = f"{snap_age_secs:.0f}s ago"
elif snap_age_secs < 3600:
    stale_dot = "🟡"
    stale_txt = f"STALE  {snap_age_secs/60:.0f}m ago"
else:
    stale_dot = "🔴"
    stale_txt = f"OFFLINE?  {snap_age_secs/3600:.1f}h ago"

vix_str     = f"{vix_value:.2f}" if vix_value else "N/A"
refresh_str = ("60s ⚡" if event_mode else f"{poll_interval}s") + " refresh"

h1, h2, h3, h4 = st.columns([3, 1.2, 1.4, 2])
h1.markdown(
    f"<span style='font-size:1.6em;font-weight:700;font-family:monospace'>"
    f"SPX&nbsp;&nbsp;"
    f"<span style='color:#00d97e'>{spx_price:,.2f}</span>"
    f"</span>",
    unsafe_allow_html=True,
)
h2.metric("VIX",  vix_str)
h3.metric("Data", f"{stale_dot} {stale_txt}")
h4.metric("Snapshot", snap_ts_str[:16] + " UTC  ·  " + refresh_str)

st.markdown(
    "<hr style='margin:8px 0 14px 0;border:none;border-top:1px solid rgba(255,255,255,0.08)'>",
    unsafe_allow_html=True,
)

# ─── SELECTORS ROW  (expiry + strike) ────────────────────────────────────────
sc1, sc2, sc3, sc4 = st.columns([1.5, 1.5, 1.5, 1.5])

with sc1:
    front_expiry = st.selectbox(
        "Front Expiry  *(short leg)*",
        available_expiries, index=0, key="front_expiry_select",
    )
with sc2:
    back_expiry = st.selectbox(
        "Back Expiry  *(long leg)*",
        available_expiries,
        index=min(1, len(available_expiries) - 1),
        key="back_expiry_select",
    )
    if back_expiry <= front_expiry:
        st.warning("Back ≤ Front — unusual structure.")
with sc3:
    default_call = float(round(spx_price / 5) * 5)
    call_strike = st.number_input(
        "Call Strike  *(sell front / buy back)*",
        min_value=1000.0, max_value=15000.0,
        value=default_call, step=5.0, format="%.0f",
        key="call_strike_input",
    )
with sc4:
    default_put = float(round((spx_price - 100) / 5) * 5)
    put_strike = st.number_input(
        "Put Strike  *(sell front / buy back)*",
        min_value=1000.0, max_value=15000.0,
        value=default_put, step=5.0, format="%.0f",
        key="put_strike_input",
    )

strikes_set = call_strike > 0 and put_strike > 0

front_dte = int(chain_df[chain_df["expiry"] == front_expiry]["dte"].iloc[0])
back_dte  = int(chain_df[chain_df["expiry"] == back_expiry ]["dte"].iloc[0])

# 30-minute cutoff (for sparklines)
_cutoff_30m = (datetime.now(timezone.utc) - timedelta(minutes=30)).astimezone(_ET)

st.markdown(
    "<hr style='margin:10px 0 14px 0;border:none;border-top:1px solid rgba(255,255,255,0.08)'>",
    unsafe_allow_html=True,
)

# ─── THREE ANALYTICS PANELS ───────────────────────────────────────────────────
pan1, pan2, pan3 = st.columns([1, 1, 1], gap="medium")


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 1 — IV STRUCTURE
# ══════════════════════════════════════════════════════════════════════════════
with pan1:
    st.markdown('<div class="panel-title">📊 IV STRUCTURE — per strike · 30-min</div>',
                unsafe_allow_html=True)

    if not strikes_set:
        st.caption("Set call and put strikes above.")
    else:
        # Compute call side
        fc_c = iv_engine.strike_contract(chain_df, front_expiry, call_strike, "CALL")
        bc_c = iv_engine.strike_contract(chain_df, back_expiry,  call_strike, "CALL")
        fc_p = iv_engine.strike_contract(chain_df, front_expiry, put_strike,  "PUT")
        bc_p = iv_engine.strike_contract(chain_df, back_expiry,  put_strike,  "PUT")

        c_ratio = (fc_c.iv / bc_c.iv) if (fc_c.iv and bc_c.iv) else None
        p_ratio = (fc_p.iv / bc_p.iv) if (fc_p.iv and bc_p.iv) else None

        c_label, c_color = _neutral_regime(c_ratio) if c_ratio else ("N/A", _ACCENT_NA)
        p_label, p_color = _neutral_regime(p_ratio) if p_ratio else ("N/A", _ACCENT_NA)

        iv_s1, iv_s2 = st.columns(2)

        # ── Call side ──
        with iv_s1:
            st.markdown(f'<div class="side-label">CALL {call_strike:.0f}</div>',
                        unsafe_allow_html=True)
            if c_ratio:
                st.markdown(
                    f'<div class="ratio-num" style="color:{c_color}">{c_ratio:.4f}</div>'
                    f'<span class="regime-badge" style="background:{c_color}22;color:{c_color}">'
                    f'{c_label}</span>'
                    f'<div class="sub-label" style="margin-top:5px">'
                    f'F&nbsp;{fc_c.iv:.2f}%&nbsp;&nbsp;·&nbsp;&nbsp;B&nbsp;{bc_c.iv:.2f}%'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("Strike not in chain")

            fch30 = _load_contract_hist(front_expiry, call_strike, "CALL", 1)
            bch30 = _load_contract_hist(back_expiry,  call_strike, "CALL", 1)
            if not fch30.empty:
                fch30 = fch30[fch30["timestamp"] >= _cutoff_30m]
            if not bch30.empty:
                bch30 = bch30[bch30["timestamp"] >= _cutoff_30m]
            fig_cs = _mini_ratio_chart(fch30, bch30)
            if fig_cs:
                st.plotly_chart(fig_cs, use_container_width=True,
                                config={"displayModeBar": False})
            else:
                st.caption("30m history building...")

        # ── Put side ──
        with iv_s2:
            st.markdown(f'<div class="side-label">PUT {put_strike:.0f}</div>',
                        unsafe_allow_html=True)
            if p_ratio:
                st.markdown(
                    f'<div class="ratio-num" style="color:{p_color}">{p_ratio:.4f}</div>'
                    f'<span class="regime-badge" style="background:{p_color}22;color:{p_color}">'
                    f'{p_label}</span>'
                    f'<div class="sub-label" style="margin-top:5px">'
                    f'F&nbsp;{fc_p.iv:.2f}%&nbsp;&nbsp;·&nbsp;&nbsp;B&nbsp;{bc_p.iv:.2f}%'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("Strike not in chain")

            fph30 = _load_contract_hist(front_expiry, put_strike, "PUT", 1)
            bph30 = _load_contract_hist(back_expiry,  put_strike, "PUT", 1)
            if not fph30.empty:
                fph30 = fph30[fph30["timestamp"] >= _cutoff_30m]
            if not bph30.empty:
                bph30 = bph30[bph30["timestamp"] >= _cutoff_30m]
            fig_ps = _mini_ratio_chart(fph30, bph30)
            if fig_ps:
                st.plotly_chart(fig_ps, use_container_width=True,
                                config={"displayModeBar": False})
            else:
                st.caption("30m history building...")


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 2 — CALENDAR EDGE
# ══════════════════════════════════════════════════════════════════════════════
with pan2:
    st.markdown('<div class="panel-title">⚡ CALENDAR EDGE — by side · today</div>',
                unsafe_allow_html=True)

    if not strikes_set:
        st.caption("Set call and put strikes above.")
    else:
        edge = iv_engine.calendar_edge(
            chain_df, front_expiry, back_expiry, call_strike, put_strike
        )

        ed_c1, ed_c2 = st.columns(2)

        # ── Call edge ──
        with ed_c1:
            st.markdown(f'<div class="side-label">CALL {call_strike:.0f}</div>',
                        unsafe_allow_html=True)
            c_clr = _edge_color(edge.call_edge)
            if edge.call_edge is not None:
                st.markdown(
                    f'<div class="edge-num" style="color:{c_clr}">'
                    f'{edge.call_edge:+.2f}%</div>'
                    f'<div class="sub-label">{_edge_label(edge.call_edge)}</div>'
                    f'<div class="sub-label">Ratio: {edge.call_ratio:.4f}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("N/A")

            fch_e = _load_contract_hist(front_expiry, call_strike, "CALL", 1)
            bch_e = _load_contract_hist(back_expiry,  call_strike, "CALL", 1)
            if not fch_e.empty and not bch_e.empty:
                em_c = pd.merge(
                    fch_e[["timestamp","iv"]].rename(columns={"iv":"f"}),
                    bch_e[["timestamp","iv"]].rename(columns={"iv":"b"}),
                    on="timestamp", how="inner",
                )
                if not em_c.empty:
                    em_c["edge"] = em_c["f"] - em_c["b"]
                    fig_ce = _mini_edge_chart(em_c, c_clr)
                    if fig_ce:
                        st.plotly_chart(fig_ce, use_container_width=True,
                                        config={"displayModeBar": False})
            else:
                st.caption("History building...")

        # ── Put edge ──
        with ed_c2:
            st.markdown(f'<div class="side-label">PUT {put_strike:.0f}</div>',
                        unsafe_allow_html=True)
            p_clr = _edge_color(edge.put_edge)
            if edge.put_edge is not None:
                st.markdown(
                    f'<div class="edge-num" style="color:{p_clr}">'
                    f'{edge.put_edge:+.2f}%</div>'
                    f'<div class="sub-label">{_edge_label(edge.put_edge)}</div>'
                    f'<div class="sub-label">Ratio: {edge.put_ratio:.4f}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("N/A")

            fph_e = _load_contract_hist(front_expiry, put_strike, "PUT", 1)
            bph_e = _load_contract_hist(back_expiry,  put_strike, "PUT", 1)
            if not fph_e.empty and not bph_e.empty:
                em_p = pd.merge(
                    fph_e[["timestamp","iv"]].rename(columns={"iv":"f"}),
                    bph_e[["timestamp","iv"]].rename(columns={"iv":"b"}),
                    on="timestamp", how="inner",
                )
                if not em_p.empty:
                    em_p["edge"] = em_p["f"] - em_p["b"]
                    fig_pe = _mini_edge_chart(em_p, p_clr)
                    if fig_pe:
                        st.plotly_chart(fig_pe, use_container_width=True,
                                        config={"displayModeBar": False})
            else:
                st.caption("History building...")


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 3 — TRANSFORM CREDIT
# ══════════════════════════════════════════════════════════════════════════════
with pan3:
    st.markdown('<div class="panel-title">💰 TRANSFORM CREDIT — theoretical lock-in</div>',
                unsafe_allow_html=True)

    if not strikes_set:
        st.caption("Set call and put strikes above.")
    elif entry_debit == 0:
        st.info("Set **Entry Debit** in the sidebar to activate this panel.")
    else:
        tc = iv_engine.transform_credit(
            chain_df=chain_df,
            front_expiry=front_expiry,
            back_expiry=back_expiry,
            call_strike=call_strike,
            put_strike=put_strike,
            entry_debit=entry_debit,
            threshold=transform_threshold,
        )

        if tc.theoretical_credit is not None:
            # Main credit number
            if tc.is_viable:
                cr_color = "#00d97e"
                status_icon = "✅"
            elif tc.theoretical_credit > 0:
                cr_color = "#ffd32a"
                status_icon = "⏳"
            else:
                cr_color = "#ff4757"
                status_icon = "⛔"

            st.markdown(
                f'<div class="credit-num" style="color:{cr_color}">'
                f'{status_icon}&nbsp;${tc.theoretical_credit:+.2f}'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Status line
            if tc.is_viable:
                excess = tc.theoretical_credit - tc.threshold
                st.markdown(
                    f'<div style="color:#00d97e;font-size:0.9em;margin:4px 0 8px 0">'
                    f'<b>TRANSFORM VIABLE</b> — ${excess:.2f} above threshold'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                gap_abs = abs(tc.gap_to_threshold)
                st.markdown(
                    f'<div style="color:#ffd32a;font-size:0.9em;margin:4px 0 8px 0">'
                    f'${gap_abs:.2f} below threshold (${tc.threshold:.2f})'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Leg breakdown table
            st.markdown('<hr class="mini-sep">', unsafe_allow_html=True)
            if tc.back_call_mark is not None and tc.back_put_mark is not None:
                st.markdown(
                    f'<div class="leg-row">'
                    f'<span>Back call mark</span>'
                    f'<span style="color:#c9d1d9">${tc.back_call_mark:.2f}</span>'
                    f'</div>'
                    f'<div class="leg-row">'
                    f'<span>Back put mark</span>'
                    f'<span style="color:#c9d1d9">${tc.back_put_mark:.2f}</span>'
                    f'</div>'
                    f'<div class="leg-row">'
                    f'<span>Back legs total</span>'
                    f'<span style="color:#fff;font-weight:600">${tc.back_legs_value:.2f}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            if tc.front_call_ask is not None and tc.front_put_ask is not None:
                st.markdown(
                    f'<div class="leg-row">'
                    f'<span>Front call close (ask)</span>'
                    f'<span style="color:#ff6b81">−${tc.front_call_ask:.2f}</span>'
                    f'</div>'
                    f'<div class="leg-row">'
                    f'<span>Front put close (ask)</span>'
                    f'<span style="color:#ff6b81">−${tc.front_put_ask:.2f}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            if tc.diagonal_mark is not None:
                st.markdown(
                    f'<div class="leg-row">'
                    f'<span>Diagonal mark</span>'
                    f'<span style="color:#fff;font-weight:600">${tc.diagonal_mark:.2f}</span>'
                    f'</div>'
                    f'<div class="leg-row">'
                    f'<span>− Entry debit</span>'
                    f'<span style="color:#ff6b81">−${entry_debit:.2f}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # NOTE: The rough "Theta ETA" indicator was removed (audit 2026-06-25).
            # It ignored back-leg theta, vega, delta, and gamma — an assumption-based
            # estimate inconsistent with the project's data-over-guesswork principle.
            # A proper time-to-viability metric is deferred to Phase 3, to be built
            # from stored per-leg Greeks. See DOCUMENTATION.md v1.1 §10.3.
        else:
            st.info(
                "Mark data unavailable for one or more legs. "
                "Confirm these strikes exist in the chain table below."
            )


# ─── ATM IV TERM STRUCTURE — metrics strip ────────────────────────────────────
st.markdown(
    "<hr style='margin:14px 0 10px 0;border:none;border-top:1px solid rgba(255,255,255,0.08)'>",
    unsafe_allow_html=True,
)

try:
    front_iv = iv_engine.atm_iv(chain_df, front_expiry, spx_price)
    back_iv  = iv_engine.atm_iv(chain_df, back_expiry,  spx_price)
    ts       = iv_engine.term_structure(front_iv, back_iv)
except ValueError as e:
    st.error(f"ATM IV calculation failed: {e}")
    st.stop()

iv_index   = float(chain_df.groupby("expiry")["iv"].mean().mean())
atm_regime_label, atm_regime_color = _neutral_regime(ts.ratio)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("IV Ratio (F/B) · ATM", f"{ts.ratio:.4f}")
m2.metric(f"Front ATM IV · {front_dte} DTE", f"{front_iv:.2f}%")
m3.metric(f"Back ATM IV · {back_dte} DTE",   f"{back_iv:.2f}%")
m4.metric("IV Index (all expiries)",          f"{iv_index:.2f}%")
m5.markdown(
    f'<div style="padding-top:6px">'
    f'<div style="font-size:0.72em;color:rgba(201,209,217,0.45);'
    f'letter-spacing:.1em;text-transform:uppercase;margin-bottom:5px">'
    f'ATM Structure</div>'
    f'<span class="regime-badge" style="background:{atm_regime_color}22;'
    f'color:{atm_regime_color};font-size:0.95em">{atm_regime_label}</span>'
    f'</div>',
    unsafe_allow_html=True,
)

st.info(_describe_curve(ts))

# ─── Time range selector ─────────────────────────────────────────────────────
period_label = st.radio(
    "Chart range", ["Today", "5D", "10D", "15D", "1M"],
    horizontal=True, label_visibility="collapsed",
)
period_days = {"Today": 1, "5D": 5, "10D": 10, "15D": 15, "1M": 30}[period_label]

# ─── ATM IV history (for charts + stats) ─────────────────────────────────────
front_atm_hist = _load_atm_hist(front_expiry, period_days)
back_atm_hist  = _load_atm_hist(back_expiry,  period_days)
atm_merged     = pd.DataFrame()

if not front_atm_hist.empty and not back_atm_hist.empty:
    atm_merged = pd.merge(
        front_atm_hist[["timestamp","atm_iv"]].rename(columns={"atm_iv":"front_iv"}),
        back_atm_hist [["timestamp","atm_iv"]].rename(columns={"atm_iv":"back_iv"}),
        on="timestamp", how="inner",
    )
    atm_merged["iv_ratio"] = atm_merged["front_iv"] / atm_merged["back_iv"]

# ─── CHART AREA — left (expiry/strike detail) + right (charts) ───────────────
left, right = st.columns([1, 3])

# ── LEFT: expiry + strike detail ─────────────────────────────────────────────
with left:
    st.markdown("#### Expiry Detail")

    for label, exp, dte in [
        ("Front", front_expiry, front_dte),
        ("Back",  back_expiry,  back_dte),
    ]:
        rows = db.get_latest_atm_iv_snapshots(config.DB_PATH, exp, n=2)
        if rows:
            latest_iv = rows[0]["atm_avg_iv"] * 100
            change = (
                (rows[0]["atm_avg_iv"] - rows[1]["atm_avg_iv"]) * 100
                if len(rows) == 2 else 0.0
            )
            st.metric(f"{label} ({dte} DTE)", f"{latest_iv:.2f}%", f"{change:+.2f}")
        else:
            st.metric(f"{label} ({dte} DTE)", "N/A")

    st.markdown("---")

    if strikes_set:
        st.markdown("#### Strike Detail")
        for side, strike, label in [
            ("CALL", call_strike, "Call"),
            ("PUT",  put_strike,  "Put"),
        ]:
            fc2 = iv_engine.strike_contract(chain_df, front_expiry, strike, side)
            bc2 = iv_engine.strike_contract(chain_df, back_expiry,  strike, side)
            if not fc2.found_exact:
                st.warning(f"{label} {strike:.0f} → nearest {fc2.strike:.0f}")

            f_iv_s   = f"{fc2.iv:.2f}%"    if fc2.iv   else "N/A"
            b_iv_s   = f"{bc2.iv:.2f}%"    if bc2.iv   else "N/A"
            ratio_s  = f"{fc2.iv/bc2.iv:.4f}" if (fc2.iv and bc2.iv) else "N/A"
            f_mark_s = f"${fc2.mark:.2f}"  if fc2.mark else "N/A"
            b_mark_s = f"${bc2.mark:.2f}"  if bc2.mark else "N/A"

            st.markdown(
                f"**{label} {strike:.0f}**  \n"
                f"IV → F `{f_iv_s}` / B `{b_iv_s}` · Ratio `{ratio_s}`  \n"
                f"Mark → F `{f_mark_s}` / B `{b_mark_s}`"
            )

# ── RIGHT: charts ─────────────────────────────────────────────────────────────
with right:

    # ── Selected-Strike IV chart ──
    if strikes_set:
        st.markdown("#### Selected-Strike IV")

        fch_full = _load_contract_hist(front_expiry, call_strike, "CALL", period_days)
        bch_full = _load_contract_hist(back_expiry,  call_strike, "CALL", period_days)
        fph_full = _load_contract_hist(front_expiry, put_strike,  "PUT",  period_days)
        bph_full = _load_contract_hist(back_expiry,  put_strike,  "PUT",  period_days)

        call_ready = not fch_full.empty and not bch_full.empty
        put_ready  = not fph_full.empty and not bph_full.empty

        if call_ready or put_ready:
            fig_sk = go.Figure()
            if call_ready:
                cm = pd.merge(
                    fch_full[["timestamp","iv"]].rename(columns={"iv":"f"}),
                    bch_full[["timestamp","iv"]].rename(columns={"iv":"b"}),
                    on="timestamp", how="inner",
                )
                cm["ratio"] = cm["f"] / cm["b"]
                fig_sk.add_trace(go.Scatter(
                    x=cm["timestamp"], y=cm["f"],
                    name=f"Front {call_strike:.0f}C",
                    line=dict(color="#00d97e", width=1.5), yaxis="y1",
                ))
                fig_sk.add_trace(go.Scatter(
                    x=cm["timestamp"], y=cm["b"],
                    name=f"Back {call_strike:.0f}C",
                    line=dict(color="#3498db", width=1.5), yaxis="y1",
                ))
                fig_sk.add_trace(go.Scatter(
                    x=cm["timestamp"], y=cm["ratio"],
                    name="Call Ratio",
                    line=dict(color="#e74c3c", width=1.5), yaxis="y2",
                ))
            if put_ready:
                pm = pd.merge(
                    fph_full[["timestamp","iv"]].rename(columns={"iv":"f"}),
                    bph_full[["timestamp","iv"]].rename(columns={"iv":"b"}),
                    on="timestamp", how="inner",
                )
                pm["ratio"] = pm["f"] / pm["b"]
                fig_sk.add_trace(go.Scatter(
                    x=pm["timestamp"], y=pm["f"],
                    name=f"Front {put_strike:.0f}P",
                    line=dict(color="#00d97e", width=1.5, dash="dot"), yaxis="y1",
                ))
                fig_sk.add_trace(go.Scatter(
                    x=pm["timestamp"], y=pm["b"],
                    name=f"Back {put_strike:.0f}P",
                    line=dict(color="#3498db", width=1.5, dash="dot"), yaxis="y1",
                ))
                fig_sk.add_trace(go.Scatter(
                    x=pm["timestamp"], y=pm["ratio"],
                    name="Put Ratio",
                    line=dict(color="#e74c3c", width=1.5, dash="dot"), yaxis="y2",
                ))
            fig_sk.add_hline(
                y=1.0, yref="y2", line_dash="dot",
                line_color="rgba(255,255,255,0.18)", line_width=1,
            )
            fig_sk.update_layout(
                height=300,
                margin=dict(l=20, r=20, t=10, b=20),
                yaxis=dict(title="IV %", side="left"),
                yaxis2=dict(title="Ratio", side="right",
                            overlaying="y", showgrid=False),
                legend=dict(orientation="h", yanchor="bottom",
                            y=1.02, xanchor="left", x=0),
                hovermode="x unified",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_sk, use_container_width=True)
        else:
            st.info(
                "No per-strike history found for this range. "
                "Try **Today** — the collector records all strikes per snapshot."
            )

    # ── ATM IV chart ──
    st.markdown("#### ATM IV  *(floating ATM — macro context)*")

    if not atm_merged.empty:
        fig_atm = go.Figure()
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["front_iv"],
            name="Front ATM IV", line=dict(color="#00d97e", width=1.5), yaxis="y1",
        ))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["back_iv"],
            name="Back ATM IV",  line=dict(color="#3498db", width=1.5), yaxis="y1",
        ))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["iv_ratio"],
            name="IV Ratio",    line=dict(color="#e74c3c", width=1.5), yaxis="y2",
        ))
        fig_atm.add_hline(
            y=1.0, yref="y2", line_dash="dot",
            line_color="rgba(255,255,255,0.18)", line_width=1,
        )
        fig_atm.update_layout(
            height=260,
            margin=dict(l=20, r=20, t=10, b=20),
            yaxis=dict(title="IV %", side="left"),
            yaxis2=dict(title="Ratio", side="right",
                        overlaying="y", showgrid=False),
            legend=dict(orientation="h", yanchor="bottom",
                        y=1.02, xanchor="left", x=0),
            hovermode="x unified",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_atm, use_container_width=True)
    else:
        st.caption(
            f"No ATM IV history for {front_expiry} / {back_expiry} "
            f"in the **{period_label}** range. Try **Today**."
        )


# ─── HISTORICAL RANGE STATS ───────────────────────────────────────────────────
st.markdown(
    "<hr style='margin:10px 0 10px 0;border:none;border-top:1px solid rgba(255,255,255,0.08)'>",
    unsafe_allow_html=True,
)
st.markdown("#### Historical ATM IV Ratio Range")
stat_cols = st.columns(5)

for col, (label, days) in zip(
    stat_cols,
    [("Today",1), ("5D",5), ("10D",10), ("15D",15), ("1M",30)],
):
    pf = _load_atm_hist(front_expiry, days)
    pb = _load_atm_hist(back_expiry,  days)
    with col:
        st.caption(label)
        if not pf.empty and not pb.empty:
            pm_ratio = pd.merge(
                pf[["timestamp","atm_iv"]].rename(columns={"atm_iv":"f"}),
                pb[["timestamp","atm_iv"]].rename(columns={"atm_iv":"b"}),
                on="timestamp",
            )
            pm_ratio["ratio"] = pm_ratio["f"] / pm_ratio["b"]
            rs = iv_engine.range_stats(pm_ratio["ratio"], ts.ratio)
            st.markdown(
                f'<div style="font-size:0.85em;color:#c9d1d9">{rs.low:.4f}'
                f'<div style="background:linear-gradient(90deg,#2c2f3a,#3c4255);'
                f'height:6px;border-radius:3px;position:relative;margin:5px 0;">'
                f'<div style="position:absolute;left:{rs.position_pct}%;top:-4px;'
                f'width:13px;height:13px;background:#e74c3c;border-radius:50%;'
                f'transform:translateX(-50%);border:2px solid #0d1117;"></div>'
                f'</div>{rs.high:.4f}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No data")

ratio_series = atm_merged["iv_ratio"] if not atm_merged.empty else pd.Series(dtype=float)
warning = iv_engine.sample_size_warning(ratio_series)
if warning:
    st.warning(warning)


# ─── CONTEXT METRICS  (neutral — NOT a buy signal) ───────────────────────────
# The former composite "Trade Quality Score" was retired (audit 2026-06-25):
#   • Composite 0–100 scores were rejected project-wide (raw numbers preferred).
#   • Its IV-edge component had no justified direction, because IV-ratio
#     favorability is an unvalidated hypothesis (DOCUMENTATION.md v1.1 §3.1).
#   • Its theta component is a Phase-3 placeholder.
# We now show the raw components only, as neutral context.
st.markdown(
    "<hr style='margin:10px 0 10px 0;border:none;border-top:1px solid rgba(255,255,255,0.08)'>",
    unsafe_allow_html=True,
)
st.markdown("#### Context Metrics  ·  *neutral — not a buy/avoid signal*")

near_front = chain_df[chain_df["expiry"] == front_expiry]
atm_row    = near_front.iloc[(near_front["strike"] - spx_price).abs().argsort()[:1]]
liquidity  = iv_engine.liquidity_score(
    atm_row["volume"].fillna(0).mean(),
    atm_row["open_interest"].fillna(0).mean(),
)
iv_pctile  = (
    iv_engine.percentile_rank(ratio_series, ts.ratio)
    if not ratio_series.empty else float("nan")
)

s1, s2, s3 = st.columns(3)
s1.metric("ATM IV Ratio percentile",
          f"{iv_pctile:.0f}" if not math.isnan(iv_pctile) else "—",
          help="Where the current ATM IV ratio sits within its history for the "
               "selected range. Direction of 'good' is NOT established — context only.")
s2.metric("ATM Liquidity (vol + OI)", f"{liquidity:.0f} / 100",
          help="Composite of front-ATM volume and open interest. SPX is usually "
               "highly liquid, so this is typically near 100.")
s3.metric("Net Theta Advantage", "Phase 3",
          help="Net $/day from time decay across all legs. Requires reliable "
               "per-leg theta in option_rows. Not yet implemented.")

st.caption(
    "These are raw context numbers, deliberately not combined into a single "
    "score. The IV-ratio percentile has no validated favorable direction — see "
    "DOCUMENTATION.md §3.1 (favorability is an open question pending trade data)."
)


# ─── OPTIONS CHAIN TABLE ─────────────────────────────────────────────────────
st.markdown(
    "<hr style='margin:10px 0 10px 0;border:none;border-top:1px solid rgba(255,255,255,0.08)'>",
    unsafe_allow_html=True,
)
st.markdown(f"#### Options Chain — **{front_expiry}** (front / short leg)")

_all_cols = ["strike","side","bid","ask","mark","iv","volume","open_interest","delta","dte"]
display_cols = [c for c in _all_cols if c in chain_df.columns]

chain_view = (
    chain_df[chain_df["expiry"] == front_expiry][display_cols]
    .sort_values(["strike", "side"])
    .reset_index(drop=True)
)
st.dataframe(chain_view, use_container_width=True, height=380)
