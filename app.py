"""
app.py — Dashboard v3.4  Run with: streamlit run app.py

Pure reader — all writes handled exclusively by collector.py.
No Schwab API calls.  No DB writes.
(pinned_pairs.json stores user preferences only — not market data.)

Design System v3.4 — Premium Trading Terminal
Aesthetic: Bloomberg Terminal × TradingView × Modern SaaS Analytics
Typography: Inter (UI) + JetBrains Mono (numbers)
Signature element: live-pulsing green glow on the best-diff KPI card

Tab-based navigation:
  🔭 Scanner      — Transformation opportunity scanner (primary)
  📊 Entry        — Entry analysis: position cost, theta, transform signal
  📈 Edge         — Calendar edge: ATM IV charts + regime analysis
  📉 Historical   — ATM IV ratio range stats across 4 periods
  🔬 Research     — IV Ratio vs Normalized Debit scatter

Persistent controls bar (always visible, above tabs):
  Front Expiry / Back Expiry / Put Strike / Call Strike
  Scanner-specific controls (put/call offset) live inside the Scanner tab.

DAILY CHANGE
  change = current SPX price − last COMPLETE snapshot from the PRIOR session
  (≈ yesterday's official close).  Falls back to first intraday snapshot
  if no prior-session data exists (first ever collection day).

IV SCALE NOTE
  option_rows and atm_iv_by_expiry store IVs as decimals (0.18 = 18%).
  Multiply by 100 at every data load boundary — nowhere else.
"""

import json
import logging
from datetime import date, datetime, timezone, time as dt_time
from pathlib import Path

import streamlit.components.v1 as components

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config
import db
import iv_engine
import schwab_client

logger = logging.getLogger(__name__)

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SPX Diagonal Analyzer",
    page_icon="📈",
    layout="wide",
)

# ─── Design system v3.4 ───────────────────────────────────────────────────────
# Principles:
#   1. SPACE is hierarchy — sections breathe; no decorative dividers.
#   2. SIZE is hierarchy — critical values 3× larger than labels.
#   3. COLOR signals STATE — green/red/amber only for meaning.
#   4. ONE bold move — the pulsing green KPI card when diff ≥ 5.
#      Everything else is quiet and disciplined around it.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

/* ── Tokens ───────────────────────────────────────────────────────────── */
:root {
  --bg:            #060b12;
  --bg-card:       #0c1421;
  --bg-raised:     #111c2e;
  --bg-hover:      #172340;
  --bg-input:      #080e18;
  --border:        rgba(255,255,255,.056);
  --border-hi:     rgba(93,163,255,.42);
  --border-green:  rgba(16,212,163,.3);
  --text:          #dde6f1;
  --text-2:        #6d8fa8;
  --text-3:        #2f4459;
  --green:         #10d4a3;
  --red:           #f05252;
  --blue:          #5b9cff;
  --amber:         #f0a429;
  --purple:        #9575cd;
  --mono:  'JetBrains Mono', 'Roboto Mono', monospace;
  --sans:  'Inter', system-ui, sans-serif;
  --r:     10px;
  --r-sm:  6px;
  --r-lg:  14px;
  --ease:  cubic-bezier(.4,0,.2,1);
  --shadow:    0 1px 4px rgba(0,0,0,.65), 0 4px 18px rgba(0,0,0,.35);
  --shadow-up: 0 8px 32px rgba(0,0,0,.6);
  --glow-blue:  0 0 22px rgba(91,156,255,.14);
  --glow-green: 0 0 24px rgba(16,212,163,.18);
}

/* ── Base ─────────────────────────────────────────────────────────────── */
.stApp { background: var(--bg) !important; font-family: var(--sans) !important; }
.main .block-container {
  padding: 0 2rem 4rem !important;
  max-width: 1720px !important;
}
* { box-sizing: border-box; }
::selection { background: rgba(91,156,255,.22); color: var(--text); }

/* ── Scrollbar ────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--bg-raised); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #2a3f56; }

/* ── Animations ───────────────────────────────────────────────────────── */
@keyframes fadeUp {
  from { opacity:0; transform:translateY(8px); }
  to   { opacity:1; transform:none; }
}
@keyframes fadeIn { from { opacity:0; } to { opacity:1; } }
@keyframes pulseGreen {
  0%,100% { box-shadow: 0 0 0 0 rgba(16,212,163,.4), var(--shadow); }
  50%     { box-shadow: 0 0 0 6px rgba(16,212,163,.0), var(--shadow); }
}
@keyframes _spx_flash {
  0%,100% { background-color:#7f1d1d; }
  50%     { background-color:#991b1b; }
}
@keyframes _spx_pulse {
  0%,100% { opacity:1; } 50% { opacity:.4; }
}
@keyframes dotPulse {
  0%,100% { transform: scale(1); opacity:1; }
  50%     { transform: scale(1.4); opacity:.7; }
}

/* ── Typography — size IS the hierarchy ──────────────────────────────── */
h1,h2,h3,h4,h5 {
  font-family: var(--sans) !important;
  color: var(--text) !important;
  letter-spacing: -.02em !important;
  border: none !important;
  padding: 0 !important;
  margin: 0 0 .2rem 0 !important;
}
h1 { font-size:1.8rem !important; font-weight:800 !important; }
h2 { font-size:1.05rem !important; font-weight:600 !important; }
h3 {
  font-size:.6rem !important; font-weight:700 !important;
  text-transform:uppercase !important; letter-spacing:.12em !important;
  color:var(--text-3) !important;
}
p, li { font-family:var(--sans) !important; color:var(--text-2); font-size:.875rem; }
hr { display:none !important; }
[data-testid="stCaptionContainer"] p, .stCaption p, .stCaption {
  color:var(--text-3) !important; font-size:.68rem !important;
}

/* ── Tabs — main navigation ───────────────────────────────────────────── */
div[data-baseweb="tab-list"] {
  background: transparent !important;
  gap: 0 !important;
  border-bottom: 1px solid var(--border) !important;
  margin-bottom: 0 !important;
}
button[data-baseweb="tab"] {
  background: transparent !important;
  color: var(--text-2) !important;
  font-size: .79rem !important;
  font-weight: 500 !important;
  padding: .62rem 1.1rem .58rem !important;
  border-radius: 0 !important;
  transition: color .15s var(--ease) !important;
  font-family: var(--sans) !important;
  letter-spacing: .005em !important;
}
button[data-baseweb="tab"]:hover { color: var(--text) !important; }
button[aria-selected="true"][data-baseweb="tab"] {
  color: var(--blue) !important;
  font-weight: 600 !important;
  background: transparent !important;
}
div[data-baseweb="tab-highlight"] {
  background: var(--blue) !important;
  height: 2px !important;
  bottom: 0 !important;
}
div[data-baseweb="tab-border"] { background: transparent !important; }
[data-testid="stTabContent"] {
  animation: fadeIn .2s var(--ease) both;
  padding-top: .75rem !important;
}

/* ── Metric cards ─────────────────────────────────────────────────────── */
[data-testid="metric-container"] {
  background: var(--bg-card) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r) !important;
  padding: 1rem 1.2rem .9rem !important;
  box-shadow: var(--shadow) !important;
  transition: border-color .2s var(--ease), box-shadow .2s var(--ease),
              transform .2s var(--ease) !important;
  animation: fadeUp .35s var(--ease) both;
}
[data-testid="metric-container"]:hover {
  border-color: var(--border-hi) !important;
  box-shadow: var(--shadow-up), var(--glow-blue) !important;
  transform: translateY(-2px) !important;
}
[data-testid="stMetricLabel"] > div {
  color: var(--text-3) !important;
  font-size: .6rem !important;
  font-weight: 700 !important;
  text-transform: uppercase !important;
  letter-spacing: .12em !important;
}
[data-testid="stMetricValue"] {
  font-family: var(--mono) !important;
  font-size: 1.9rem !important;
  font-weight: 600 !important;
  color: var(--text) !important;
  line-height: 1.15 !important;
  letter-spacing: -.03em !important;
}
[data-testid="stMetricDelta"] {
  font-family: var(--mono) !important;
  font-size: .72rem !important;
}

/* ── Tables ───────────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] {
  border: 1px solid var(--border) !important;
  border-radius: var(--r) !important;
  overflow: hidden !important;
  box-shadow: var(--shadow) !important;
  animation: fadeUp .4s var(--ease) both;
}

/* ── Selects ──────────────────────────────────────────────────────────── */
[data-baseweb="select"] > div:first-child {
  background: var(--bg-card) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r-sm) !important;
  color: var(--text) !important;
  font-size: .82rem !important;
  transition: border-color .15s ease, box-shadow .15s ease !important;
}
[data-baseweb="select"] > div:first-child:hover {
  border-color: rgba(255,255,255,.11) !important;
}
[data-baseweb="select"] > div:first-child:focus-within {
  border-color: var(--border-hi) !important;
  box-shadow: 0 0 0 3px rgba(91,156,255,.1) !important;
}
[data-baseweb="select"] span { color: var(--text) !important; }

/* ── Inputs ───────────────────────────────────────────────────────────── */
input[type="number"], input[type="text"] {
  background: var(--bg-input) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r-sm) !important;
  color: var(--text) !important;
  font-family: var(--mono) !important;
  font-size: .82rem !important;
  transition: border-color .15s ease, box-shadow .15s ease !important;
}
input:focus {
  border-color: var(--border-hi) !important;
  box-shadow: 0 0 0 3px rgba(91,156,255,.1) !important;
  outline: none !important;
}
[data-testid="stNumberInput"] button {
  background: var(--bg-raised) !important;
  border-color: var(--border) !important;
  color: var(--text-2) !important;
}
[data-testid="stNumberInput"] button:hover {
  border-color: var(--border-hi) !important;
  color: var(--text) !important;
}

/* ── Buttons ──────────────────────────────────────────────────────────── */
.stButton > button {
  background: var(--bg-raised) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r-sm) !important;
  color: var(--text-2) !important;
  font-family: var(--sans) !important;
  font-size: .78rem !important;
  font-weight: 500 !important;
  padding: .38rem 1rem !important;
  transition: all .15s var(--ease) !important;
}
.stButton > button:hover {
  border-color: var(--border-hi) !important;
  color: var(--text) !important;
  background: var(--bg-hover) !important;
  transform: translateY(-1px) !important;
  box-shadow: 0 4px 12px rgba(0,0,0,.4) !important;
}
.stButton > button:active { transform: none !important; }

/* ── Sidebar ──────────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #05080e 0%, #070c16 100%) !important;
  border-right: 1px solid var(--border) !important;
}
section[data-testid="stSidebar"] .block-container { padding: 1.25rem .85rem !important; }
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  font-size: .6rem !important;
  text-transform: uppercase !important;
  letter-spacing: .12em !important;
  color: var(--text-3) !important;
  margin-bottom: .5rem !important;
}
section[data-testid="stSidebar"] label {
  font-size: .75rem !important;
  color: var(--text-2) !important;
  font-weight: 500 !important;
}
section[data-testid="stSidebar"] hr { display: none !important; }

/* ── Progress ─────────────────────────────────────────────────────────── */
[data-testid="stProgress"] > div > div {
  border-radius: 999px !important;
  transition: width .6s var(--ease) !important;
}

/* ── Alerts ───────────────────────────────────────────────────────────── */
[data-testid="stAlert"] { border-radius: var(--r) !important; }
[data-testid="stSpinner"] p { color: var(--blue) !important; }

/* ── Columns ──────────────────────────────────────────────────────────── */
[data-testid="column"] { padding: 0 .3rem !important; }
[data-testid="column"]:first-child { padding-left: 0 !important; }
[data-testid="column"]:last-child  { padding-right: 0 !important; }
[data-testid="stVerticalBlock"] { gap: .35rem !important; }
[data-testid="stHeading"] { margin-top: 1.2rem !important; }

/* ── Code ─────────────────────────────────────────────────────────────── */
code {
  background: var(--bg-raised) !important;
  border: 1px solid var(--border) !important;
  border-radius: 4px !important;
  color: #7cb9ff !important;
  font-family: var(--mono) !important;
  font-size: .8em !important;
  padding: .1em .4em !important;
}
iframe { border: none !important; }

/* ═══════════════════════════════════════════════════════════════════════
   CUSTOM COMPONENT CLASSES
   ═══════════════════════════════════════════════════════════════════════ */

/* ── Header bar ───────────────────────────────────────────────────────── */
.spx-hdr {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.5rem;
  padding: 1rem 0 .85rem;
  border-bottom: 1px solid var(--border);
  margin-bottom: .9rem;
}
.spx-price-block { display: flex; align-items: baseline; gap: .45rem; }
.spx-ticker {
  font-family: var(--mono);
  font-size: .68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .14em;
  color: var(--text-3);
}
.spx-price {
  font-family: var(--mono);
  font-size: 2.15rem;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -.04em;
}
.spx-chg {
  font-family: var(--mono);
  font-size: .85rem;
  font-weight: 500;
  opacity: .9;
}
.hdr-chips { display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; }
.hdr-chip {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: .28rem .65rem .25rem;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 70px;
  transition: border-color .15s var(--ease);
}
.hdr-chip:hover { border-color: rgba(255,255,255,.1); }
.chip-lbl {
  font-size: .54rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .12em;
  color: var(--text-3);
  line-height: 1;
  margin-bottom: .18rem;
}
.chip-val {
  font-family: var(--mono);
  font-size: .9rem;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -.02em;
  line-height: 1;
}
.hdr-status { display: flex; align-items: center; gap: .4rem; }
.st-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.st-dot.green { background: var(--green); box-shadow: 0 0 6px rgba(16,212,163,.6); animation: dotPulse 2s ease-in-out infinite; }
.st-dot.amber { background: var(--amber); box-shadow: 0 0 6px rgba(240,164,41,.6); }
.st-dot.red   { background: var(--red);   box-shadow: 0 0 6px rgba(240,82,82,.6);  }
.st-text { font-size: .7rem; color: var(--text-2); font-family: var(--sans); }

/* ── Controls bar ─────────────────────────────────────────────────────── */
.ctrl-bar {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: .8rem 1.2rem .65rem;
  margin-bottom: .85rem;
  box-shadow: var(--shadow);
  animation: fadeUp .3s var(--ease) both;
}
.ctrl-bar-title {
  font-size: .56rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .14em;
  color: var(--text-3);
  margin-bottom: .5rem;
}

/* ── KPI grid ─────────────────────────────────────────────────────────── */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: .65rem;
  margin-bottom: 1rem;
  animation: fadeUp .35s var(--ease) both;
}
.kpi-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: .85rem 1rem .75rem;
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
  transition: border-color .2s var(--ease), transform .2s var(--ease),
              box-shadow .2s var(--ease);
}
.kpi-card::after {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: var(--kpi-top, transparent);
}
.kpi-card:hover {
  border-color: var(--border-hi);
  transform: translateY(-2px);
  box-shadow: var(--shadow-up), var(--glow-blue);
}
.kpi-card.kpi-hl {
  border-color: rgba(16,212,163,.28) !important;
  background: linear-gradient(145deg, rgba(16,212,163,.06) 0%, var(--bg-card) 55%) !important;
  animation: pulseGreen 2.5s ease-in-out infinite, fadeUp .35s var(--ease) both;
  --kpi-top: var(--green);
}
.kpi-card.kpi-hl .kpi-v { color: var(--green); }
.kpi-icon { font-size: .9rem; display: block; margin-bottom: .28rem; line-height: 1; }
.kpi-v {
  font-family: var(--mono);
  font-size: 1.65rem;
  font-weight: 700;
  color: var(--text);
  line-height: 1.1;
  letter-spacing: -.035em;
  display: block;
}
.kpi-v.c-blue  { color: var(--blue); }
.kpi-v.c-amber { color: var(--amber); }
.kpi-v.c-green { color: var(--green); }
.kpi-l {
  font-size: .56rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--text-3);
  margin-top: .22rem;
  display: block;
}
.kpi-sub {
  font-size: .66rem;
  color: var(--text-2);
  font-family: var(--mono);
  display: block;
  margin-top: .08rem;
}

/* ── Filter panel ─────────────────────────────────────────────────────── */
.filter-panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: .9rem 1.2rem .75rem;
  margin-bottom: .85rem;
  box-shadow: var(--shadow);
  animation: fadeUp .3s var(--ease) both;
}
.fp-title {
  font-size: .56rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .14em;
  color: var(--text-3);
  margin-bottom: .5rem;
}

/* ── Section headers ──────────────────────────────────────────────────── */
.sh {
  display: flex;
  align-items: center;
  gap: .45rem;
  margin: .1rem 0 .6rem;
}
.sh-ico { font-size: .9rem; opacity: .85; }
.sh-ttl {
  font-size: .92rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -.02em;
}
.sh-bdg {
  font-size: .56rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--blue);
  background: rgba(91,156,255,.1);
  border: 1px solid rgba(91,156,255,.18);
  border-radius: 4px;
  padding: .1rem .36rem;
}
.sh-bdg.g {
  color: var(--green);
  background: rgba(16,212,163,.08);
  border-color: rgba(16,212,163,.2);
}

/* ── Ready badge ──────────────────────────────────────────────────────── */
.ready-badge {
  display: inline-flex;
  align-items: center;
  gap: .38rem;
  background: rgba(16,212,163,.07);
  border: 1px solid rgba(16,212,163,.22);
  border-radius: 999px;
  padding: .26rem .7rem;
  font-size: .7rem;
  font-weight: 600;
  color: var(--green);
  margin-bottom: .65rem;
}
.rdot {
  width: 5px; height: 5px;
  background: var(--green);
  border-radius: 50%;
  animation: dotPulse 1.6s ease-in-out infinite;
}

/* ── Side panel cards ─────────────────────────────────────────────────── */
.spanel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: .75rem .85rem .65rem;
  margin-bottom: .6rem;
  box-shadow: var(--shadow);
  animation: fadeUp .45s var(--ease) both;
}
.spanel-ttl {
  font-size: .56rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .14em;
  color: var(--text-3);
  margin-bottom: .5rem;
}

/* ── Market snapshot mini-grid ────────────────────────────────────────── */
.mkt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .32rem; }
.mkt-cell {
  background: var(--bg-raised);
  border-radius: var(--r-sm);
  padding: .42rem .5rem;
}
.mc-l {
  font-size: .54rem; text-transform: uppercase;
  letter-spacing: .1em; color: var(--text-3); display: block;
}
.mc-v {
  font-family: var(--mono); font-size: .88rem; font-weight: 600;
  color: var(--text); letter-spacing: -.02em; line-height: 1.25; display: block;
}
.mc-c { font-family: var(--mono); font-size: .62rem; margin-top: .05rem; display: block; }
.mc-c.p { color: var(--green); }
.mc-c.n { color: var(--red); }

/* ── Token warnings ───────────────────────────────────────────────────── */
.spx-token-emergency {
  animation: _spx_flash 0.8s ease-in-out infinite;
  border-radius: var(--r);
  color:#fff;
  padding:12px 18px;
  font-weight:600;
  font-size:.88em;
  margin:6px 0;
}
.spx-token-warning {
  animation: _spx_pulse 1.6s ease-in-out infinite;
  background: linear-gradient(135deg, rgba(61,38,0,.9), rgba(42,26,0,.9));
  border: 1px solid rgba(240,164,41,.38);
  border-radius: var(--r);
  color: #f5d49a;
  padding: 10px 18px;
  font-weight: 500;
  font-size: .85em;
  margin: 6px 0;
}
</style>
""", unsafe_allow_html=True)

# ─── Constants ────────────────────────────────────────────────────────────────
_SPARK_BARS = "▁▂▃▄▅▆▇█"

_SESSION_RANGEBREAKS = [
    dict(bounds=["sat", "mon"]),
    dict(bounds=[16, 9.5], pattern="hour"),
    dict(values=sorted(config.MARKET_HOLIDAYS)),
]

_RATIO_THRESHOLDS = [0.70, 1.00, 1.30]
_RATIO_BANDS = [
    (1.30, float("inf"), "#1abc9c", "Strong backwardation (≥1.30)"),
    (1.00, 1.30,         "#2ecc71", "Backwardation 1.00–1.30 (front rich)"),
    (0.70, 1.00,         "#8e9bb5", "Contango 0.70–1.00 (normal)"),
    (float("-inf"), 0.70, "#d98841", "Deep contango <0.70 (likely 0DTE/EOD)"),
]


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

def _banded_ratio_traces(x, y) -> list:
    """Build a continuous multicolor line for the IV ratio, colored by regime."""
    xs, ys = list(x), list(y)
    ax, ay = [], []
    for i in range(len(xs)):
        ax.append(xs[i])
        ay.append(ys[i])
        if i + 1 < len(xs):
            y0, y1, x0, x1 = ys[i], ys[i + 1], xs[i], xs[i + 1]
            if pd.isna(y0) or pd.isna(y1) or y0 == y1:
                continue
            crossed = [t for t in _RATIO_THRESHOLDS
                       if (y0 < t < y1) or (y1 < t < y0)]
            crossed.sort(reverse=(y0 > y1))
            for t in crossed:
                frac = (t - y0) / (y1 - y0)
                ax.append(x0 + (x1 - x0) * frac)
                ay.append(t)
    traces = []
    for low, high, color, label in _RATIO_BANDS:
        yb = [v if (v is not None and not pd.isna(v) and low <= v <= high)
              else None for v in ay]
        if any(v is not None for v in yb):
            traces.append(go.Scatter(
                x=ax, y=yb, mode="lines", name=label,
                line=dict(color=color, width=2), connectgaps=False,
                legendgroup=label,
                hovertemplate="R=%{y:.4f}<extra></extra>",
            ))
    return traces


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


def _load_atm_hist_fb(expiry: str, days: int) -> pd.DataFrame:
    df = _load_atm_hist(expiry, days)
    if df.empty and days == 1:
        df = _load_atm_hist(expiry, 5)
        if not df.empty:
            last_date = df["timestamp"].dt.date.max()
            df = df[df["timestamp"].dt.date == last_date]
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
    if not rows and days == 1:
        rows = db.get_contract_iv_history(
            config.DB_PATH, expiry, strike, right_char, 5
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["iv"] = df["iv"] * 100
    df["timestamp"] = (
        pd.to_datetime(df["snapshot_timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
    )
    if days == 1 and not df.empty:
        last_date = df["timestamp"].dt.date.max()
        df = df[df["timestamp"].dt.date == last_date]
    return df


# ─────────────────────────────────────────────────────────────────────────────
def _compute_transform_scanner(
    chain_df: pd.DataFrame,
    spx_price: float,
    put_offset: int = 0,
    call_offset: int = 0,
    max_rows: int = 50,
) -> pd.DataFrame:
    """
    Batch version of Entry Analysis.

    For every valid expiry pair (back_DTE > front_DTE) find the nearest
    available strike to (ATM - put_offset) and (ATM + call_offset) that
    exists in BOTH expiries simultaneously, then compute:
        Diagonal Mark   = (back_call + back_put) - (front_call + front_put)
        Transform Mark  = (back_call + back_put) - (front_wing_call + front_wing_put)
        Transform Diff  = Transform Mark - Diagonal Mark
    """
    import bisect

    if chain_df.empty:
        return pd.DataFrame()

    expiries   = sorted(chain_df["expiry"].unique())
    dte_by_exp = chain_df.groupby("expiry")["dte"].first().to_dict()

    _cache: dict[tuple, tuple] = {}
    for (expiry, side), grp in chain_df.groupby(["expiry", "side"]):
        pairs = []
        for row in grp.itertuples(index=False):
            m = getattr(row, "mark", None)
            if m is None or (isinstance(m, float) and pd.isna(m)):
                bid = getattr(row, "bid", None)
                ask = getattr(row, "ask", None)
                if bid is not None and ask is not None:
                    try:
                        m = (float(bid) + float(ask)) / 2.0
                    except (TypeError, ValueError):
                        m = None
            if m is not None:
                try:
                    pairs.append((float(row.strike), float(m)))
                except (TypeError, ValueError):
                    pass
        if pairs:
            pairs.sort()
            _cache[(expiry, side)] = (
                [p[0] for p in pairs],
                [p[1] for p in pairs],
            )

    def _exact_mark(expiry: str, target: float, side: str):
        key = (expiry, side)
        if key not in _cache:
            return None
        strikes, marks = _cache[key]
        idx = bisect.bisect_left(strikes, target)
        if idx < len(strikes) and strikes[idx] == target:
            return marks[idx]
        return None

    def _nearest_common(exp1: str, exp2: str, target: float, side: str):
        key1, key2 = (exp1, side), (exp2, side)
        if key1 not in _cache or key2 not in _cache:
            return None, None, None
        common = sorted(set(_cache[key1][0]) & set(_cache[key2][0]))
        if not common:
            return None, None, None
        idx = bisect.bisect_left(common, target)
        if idx == 0:
            actual = common[0]
        elif idx == len(common):
            actual = common[-1]
        else:
            b, a = common[idx - 1], common[idx]
            actual = b if (target - b) <= (a - target) else a
        m1 = _exact_mark(exp1, actual, side)
        m2 = _exact_mark(exp2, actual, side)
        return actual, m1, m2

    atm_iv_cache: dict[str, float | None] = {
        exp: iv_engine.atm_iv(chain_df, exp, spx_price)
        for exp in expiries
    }

    atm_rounded  = round(spx_price / 5) * 5
    target_put   = float(atm_rounded - put_offset)
    target_call  = float(atm_rounded + call_offset)

    results = []

    for i, front in enumerate(expiries):
        front_dte = int(dte_by_exp.get(front, 0))
        if front_dte < 0:
            continue
        front_iv = atm_iv_cache.get(front)

        for back in expiries[i + 1:]:
            back_dte = int(dte_by_exp.get(back, 0))
            if back_dte <= front_dte:
                continue
            back_iv  = atm_iv_cache.get(back)
            iv_ratio = (
                round(front_iv / back_iv, 4)
                if (front_iv and back_iv and back_iv != 0) else None
            )

            put_s,  fp, bp = _nearest_common(front, back, target_put,  "PUT")
            call_s, fc, bc = _nearest_common(front, back, target_call, "CALL")

            if any(v is None for v in (put_s, call_s, fp, bp, fc, bc)):
                continue

            diag_mark = (bc + bp) - (fc + fp)

            wc = _exact_mark(front, call_s + 5, "CALL")
            wp = _exact_mark(front, put_s  - 5, "PUT")
            if wc is None or wp is None:
                continue

            transform_mark = (bc + bp) - (wc + wp)
            transform_diff = transform_mark - diag_mark

            results.append({
                "Front Expiry":   f"{front} ({front_dte}d)",
                "Back Expiry":    f"{back} ({back_dte}d)",
                "Put Strike":     int(put_s),
                "Call Strike":    int(call_s),
                "Diagonal Mark":  round(diag_mark,      2),
                "Transform Mark": round(transform_mark, 2),
                "Transform Diff": round(transform_diff, 2),
                "IV Ratio":       iv_ratio,
            })

    if not results:
        return pd.DataFrame()

    return (
        pd.DataFrame(results)
        .sort_values("Transform Diff", ascending=False)
        .head(max_rows)
        .reset_index(drop=True)
    )


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

st_autorefresh(interval=poll_interval * 1000, key="autorefresh")

st.sidebar.divider()
st.sidebar.markdown("**🔭 Transform Scanner**")

sc_max_rows = st.sidebar.number_input(
    "Max Results", min_value=10, max_value=200, value=50, step=10,
    key="sc_max_rows",
    help="Cap the number of rows returned (sorted by Transform Diff descending).",
)

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
session_date  = snap_ts_str[:10]

# ─────────────────────────────────────────────────────────────────────────────
# Load option chain
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
dte_by_expiry = chain_df.groupby("expiry")["dte"].first().astype(int).to_dict()


def _exp_label(expiry: str) -> str:
    d = dte_by_expiry.get(expiry)
    return f"{expiry}  ({d}D)" if d is not None else expiry


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

prev_close = db.get_prior_session_close(config.DB_PATH, session_date)

if prev_close is not None:
    ref_price = prev_close
    ref_label = f"Prev Close {prev_close:,.0f}"
elif not spx_intraday.empty:
    ref_price = float(spx_intraday["underlying_price"].iloc[0])
    ref_label = f"Session Open {ref_price:,.0f}"
else:
    ref_price = spx_price
    ref_label = ""

daily_chg_pts = spx_price - ref_price
daily_chg_pct = (daily_chg_pts / ref_price * 100) if ref_price else 0.0
day_color     = "#10d4a3" if daily_chg_pts >= 0 else "#f05252"
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

# ═══════════════════════════════════════════════════════════════════════════════
# HEADER — Premium top bar
# ═══════════════════════════════════════════════════════════════════════════════

sign         = "+" if daily_chg_pts >= 0 else ""
chg_display  = f"{sign}{daily_chg_pts:.1f} ({sign}{daily_chg_pct:.2f}%)"
vix_str      = f"{vix_value:.2f}" if vix_value else "N/A"

if snap_age_secs < 600:
    _dot_cls = "green"
elif snap_age_secs < 3600:
    _dot_cls = "amber"
else:
    _dot_cls = "red"

secs_remaining = max(0, int(poll_interval - snap_age_secs))
overdue        = snap_age_secs > poll_interval * 1.5
countdown_init = "overdue" if overdue else f"{secs_remaining}s"

h_left, h_right = st.columns([6, 5])

with h_left:
    st.markdown(
        f"""<div class="spx-hdr">
  <div class="spx-price-block">
    <span class="spx-ticker">SPX</span>
    <span class="spx-price" style="color:{day_color}">{spx_price:,.2f}</span>
    <span class="spx-chg" style="color:{day_color}">{day_arrow} {chg_display}</span>
  </div>
</div>""",
        unsafe_allow_html=True,
    )

with h_right:
    st.markdown(
        f"""<div class="spx-hdr" style="justify-content:flex-end">
  <div class="hdr-chips">
    <div class="hdr-chip">
      <span class="chip-lbl">VIX</span>
      <span class="chip-val">{vix_str}</span>
    </div>
    <div class="hdr-chip">
      <span class="chip-lbl">Max |GEX|</span>
      <span class="chip-val">{gex_label}</span>
    </div>
    <div class="hdr-chip">
      <span class="chip-lbl">Refresh</span>
      <span class="chip-val">{poll_label}</span>
    </div>
  </div>
  <div class="hdr-status">
    <span class="st-dot {_dot_cls}"></span>
    <span class="st-text">{snap_ts_str[:16]} UTC</span>
  </div>
</div>""",
        unsafe_allow_html=True,
    )

# Collector-anchored countdown
components.html(
    f"""
    <div style="font-family:'Inter',sans-serif;font-size:0.72em;
                color:#2f4459;padding:0;margin:-10px 0 4px 0;">
        <span id="spx-cd">⏱ Next update in: {countdown_init}</span>
    </div>
    <script>
    (function(){{
        var n = {secs_remaining};
        var overdue = {"true" if overdue else "false"};
        var el = document.getElementById('spx-cd');
        if (window.__spxCD) clearInterval(window.__spxCD);
        if (!overdue) {{
            window.__spxCD = setInterval(function(){{
                n = Math.max(0, n - 1);
                if (el) el.textContent = '\u23f1 Next update in: ' + n + 's';
            }}, 1000);
        }}
    }})();
    </script>
    """,
    height=22,
)

# ── Token expiry warning banner ───────────────────────────────────────────────
_token_age = schwab_client.get_token_age_days()
if _token_age is not None and _token_age >= 6:
    if _token_age >= 7:
        st.markdown(
            """<div class="spx-token-emergency">
🚨 SCHWAB TOKEN EXPIRED — Collector is offline. Re-authenticate now:<br>
<code style="background:rgba(0,0,0,0.3);padding:2px 6px;border-radius:3px;">
python -c "import schwab_client; schwab_client.get_client()"
</code>
</div>""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """<div class="spx-token-warning">
⚠️ Schwab API token expires <strong>tomorrow</strong>.
Re-authenticate today to avoid collector downtime:<br>
<code style="background:rgba(0,0,0,0.25);padding:2px 6px;border-radius:3px;">
python -c "import schwab_client; schwab_client.get_client()"
</code>
</div>""",
            unsafe_allow_html=True,
        )

# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENT CONTROLS BAR — front/back expiry + put/call strike
# Always visible above the tabs so every section can access these values.
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown(
    '<div class="ctrl-bar"><div class="ctrl-bar-title">Position Controls</div></div>',
    unsafe_allow_html=True,
)

c1, c2, c3, c4 = st.columns(4)

with c1:
    front_expiry = st.selectbox(
        "Front Expiry", available_expiries, index=0,
        format_func=_exp_label, key="front_expiry_select",
    )
with c2:
    back_expiry = st.selectbox(
        "Back Expiry", available_expiries,
        index=min(1, len(available_expiries) - 1),
        format_func=_exp_label, key="back_expiry_select",
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


def _nearest_idx(strikes: list, target: float) -> int:
    if not strikes:
        return 0
    return min(range(len(strikes)), key=lambda i: abs(strikes[i] - target))


with c3:
    if _put_strikes:
        _put_default_idx = _nearest_idx(_put_strikes, spx_price - 100)
        put_strike = st.selectbox(
            "Put Strike",
            options=_put_strikes,
            index=_put_default_idx,
            format_func=lambda s: f"{int(s):,}",
            key="put_strike_select",
            help="Only strikes present in both front and back expiry are shown.",
        )
    else:
        st.warning("No PUT strikes available for this expiry pair.")
        put_strike = 0.0

with c4:
    if _call_strikes:
        _call_default_idx = _nearest_idx(_call_strikes, spx_price)
        call_strike = st.selectbox(
            "Call Strike",
            options=_call_strikes,
            index=_call_default_idx,
            format_func=lambda s: f"{int(s):,}",
            key="call_strike_select",
            help="Only strikes present in both front and back expiry are shown.",
        )
    else:
        st.warning("No CALL strikes available for this expiry pair.")
        call_strike = 0.0

if back_expiry <= front_expiry:
    st.warning("Back expiry ≤ Front — unusual for a diagonal, shown anyway.")

front_dte   = int(chain_df[chain_df["expiry"] == front_expiry]["dte"].iloc[0])
back_dte    = int(chain_df[chain_df["expiry"] == back_expiry]["dte"].iloc[0])
strikes_set = call_strike > 0 and put_strike > 0

# ─────────────────────────────────────────────────────────────────────────────
# Derived values (needed across multiple tabs)
# ─────────────────────────────────────────────────────────────────────────────

front_iv_atm = iv_engine.atm_iv(chain_df, front_expiry, spx_price)
back_iv_atm  = iv_engine.atm_iv(chain_df, back_expiry,  spx_price)
ts_now       = iv_engine.term_structure(front_iv_atm, back_iv_atm)

_fh90 = _load_atm_hist(front_expiry, 90)
_bh90 = _load_atm_hist(back_expiry,  90)
atm_merged_90d = pd.DataFrame()
if not _fh90.empty and not _bh90.empty:
    atm_merged_90d = pd.merge(
        _fh90[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "front_iv"}),
        _bh90[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "back_iv"}),
        on="timestamp", how="inner",
    )
    atm_merged_90d["iv_ratio"] = atm_merged_90d["front_iv"] / atm_merged_90d["back_iv"]

# ─────────────────────────────────────────────────────────────────────────────
# Entry Analysis derived values (computed once, used in Entry tab and sidebar)
# ─────────────────────────────────────────────────────────────────────────────

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
    iv_engine.percentile_rank(atm_merged_90d["iv_ratio"], ts_now.ratio)
    if not atm_merged_90d.empty else None
)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB NAVIGATION
# ═══════════════════════════════════════════════════════════════════════════════

_TSCAN_THRESHOLD = 5.0

tab_scanner, tab_entry, tab_edge, tab_hist, tab_research = st.tabs([
    "🔭  Scanner",
    "📊  Entry Analysis",
    "📈  Calendar Edge",
    "📉  Historical Stats",
    "🔬  Research",
])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — TRANSFORMATION OPPORTUNITY SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

with tab_scanner:

    # ── Filter panel ─────────────────────────────────────────────────────────
    _offset_options = [0] + list(range(5, 205, 5))

    st.markdown('<div class="filter-panel"><div class="fp-title">Strike Selection</div></div>',
                unsafe_allow_html=True)

    _sc_c1, _sc_c2, _sc_c3 = st.columns([1, 1, 2])
    with _sc_c1:
        sc_put_offset = st.selectbox(
            "Put Offset from ATM",
            options=_offset_options,
            format_func=lambda v: "ATM" if v == 0 else f"ATM − {v}",
            index=0,
            key="sc_put_offset",
        )
    with _sc_c2:
        sc_call_offset = st.selectbox(
            "Call Offset from ATM",
            options=_offset_options,
            format_func=lambda v: "ATM" if v == 0 else f"ATM + {v}",
            index=0,
            key="sc_call_offset",
        )
    with _sc_c3:
        sc_gap_pts = sc_put_offset + sc_call_offset
        _sym = "symmetric" if sc_put_offset == sc_call_offset else "asymmetric"
        st.markdown(
            f"<p style='margin:.6rem 0 0;font-size:.78rem;color:#6d8fa8;'>"
            f"Strike gap: <span style='color:#dde6f1;font-family:var(--mono);font-weight:600;'>"
            f"{sc_gap_pts} pts</span> &nbsp;·&nbsp; {_sym}</p>",
            unsafe_allow_html=True,
        )

    # ── Scanner compute ───────────────────────────────────────────────────────
    with st.spinner("Scanning combinations…"):
        _ts_df = _compute_transform_scanner(
            chain_df     = chain_df,
            spx_price    = spx_price,
            put_offset   = int(sc_put_offset),
            call_offset  = int(sc_call_offset),
            max_rows     = int(sc_max_rows),
        )

    # ── KPI cards ─────────────────────────────────────────────────────────────
    if not _ts_df.empty:
        _ready_count  = int((_ts_df["Transform Diff"] >= _TSCAN_THRESHOLD).sum())
        _best_diff    = float(_ts_df["Transform Diff"].max())
        _best_row     = _ts_df.iloc[0]
        _best_label   = f"Put {int(_best_row['Put Strike'])} / Call {int(_best_row['Call Strike'])}"
        _avg_iv_ratio = (
            _ts_df["IV Ratio"].dropna().mean()
            if "IV Ratio" in _ts_df.columns else None
        )

        # Diff distribution for badge
        _diff_vals      = _ts_df["Transform Diff"]
        _gt5_count      = int((_diff_vals >= 5).sum())
        _best_diff_str  = f"{_best_diff:+.2f}"
        _avg_ratio_str  = f"{_avg_iv_ratio:.4f}" if _avg_iv_ratio else "—"

        # KPI 1 highlight check
        _kpi1_hl = " kpi-hl" if _best_diff >= _TSCAN_THRESHOLD else ""
        _kpi2_hl = " kpi-hl" if _ready_count > 0 else ""

        snap_ts_et = snap_ts_str[11:16] + " UTC"

        kpi_html = f"""
<div class="kpi-grid">
  <div class="kpi-card{_kpi2_hl}">
    <span class="kpi-icon">📡</span>
    <span class="kpi-v c-blue">{len(_ts_df):,}</span>
    <span class="kpi-l">Diagonals Scanned</span>
  </div>
  <div class="kpi-card{_kpi2_hl}">
    <span class="kpi-icon">🎯</span>
    <span class="kpi-v{'  c-green' if _ready_count > 0 else ''}">{_ready_count}</span>
    <span class="kpi-l">Diff &gt; {_TSCAN_THRESHOLD:.0f}</span>
  </div>
  <div class="kpi-card{_kpi1_hl}">
    <span class="kpi-icon">✦</span>
    <span class="kpi-v">{_best_diff_str}</span>
    <span class="kpi-l">Best Difference</span>
    <span class="kpi-sub">{_best_label}</span>
  </div>
  <div class="kpi-card">
    <span class="kpi-icon">⚡</span>
    <span class="kpi-v c-amber">{_avg_ratio_str}</span>
    <span class="kpi-l">Avg IV Ratio</span>
  </div>
  <div class="kpi-card">
    <span class="kpi-icon">🕐</span>
    <span class="kpi-v" style="font-size:1.1rem;letter-spacing:-.01em">{snap_ts_et}</span>
    <span class="kpi-l">Last Updated</span>
  </div>
  <div class="kpi-card">
    <span class="kpi-icon">🔄</span>
    <span class="kpi-v c-blue">{poll_interval}s</span>
    <span class="kpi-l">Auto Refresh</span>
    <span class="kpi-sub">{poll_label}</span>
  </div>
</div>"""
        st.markdown(kpi_html, unsafe_allow_html=True)

    # ── Ready badge ───────────────────────────────────────────────────────────
    if not _ts_df.empty and _ready_count > 0:
        st.markdown(
            f'<div class="ready-badge"><span class="rdot"></span>'
            f'{_ready_count} combination{"s" if _ready_count > 1 else ""} ready to transform'
            f'&nbsp;·&nbsp;Transform Diff ≥ {_TSCAN_THRESHOLD:.0f}</div>',
            unsafe_allow_html=True,
        )

    # ── Main content: table + side panel ─────────────────────────────────────
    if _ts_df.empty:
        st.caption(
            "No valid combinations found — the current chain has no strike/expiry pairs "
            "with marks available for all four diagonal legs plus the two wing strikes. "
            "The collector may not have run yet, or try adjusting the Strike Window "
            "or Liquidity threshold in the sidebar."
        )
    else:
        _tbl_col, _side_col = st.columns([4, 1.5])

        with _tbl_col:
            st.markdown(
                '<div class="sh">'
                '<span class="sh-ico">📋</span>'
                '<span class="sh-ttl">Transformation Opportunities</span>'
                '<span class="sh-bdg">Sorted by Diff ↓</span>'
                '</div>',
                unsafe_allow_html=True,
            )

            def _ts_row_style(row):
                if row["Transform Diff"] >= _TSCAN_THRESHOLD:
                    return ["background-color: #0a1d14; color: #10d4a3"] * len(row)
                elif row["Transform Diff"] < 0:
                    return ["color: #f05252"] * len(row)
                return [""] * len(row)

            _ts_display = _ts_df.style.apply(_ts_row_style, axis=1).format({
                "Diagonal Mark":  "{:.2f}",
                "Transform Mark": "{:.2f}",
                "Transform Diff": "{:+.2f}",
                "IV Ratio":       lambda v: f"{v:.4f}" if v is not None else "—",
            })

            st.dataframe(
                _ts_display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Put Strike":     st.column_config.NumberColumn("Put Strike",     format="%d"),
                    "Call Strike":    st.column_config.NumberColumn("Call Strike",    format="%d"),
                    "Diagonal Mark":  st.column_config.NumberColumn("Diag Mark",      format="%.2f"),
                    "Transform Mark": st.column_config.NumberColumn("Transform Mark", format="%.2f"),
                    "Transform Diff": st.column_config.NumberColumn("Transform Diff", format="%+.2f"),
                    "IV Ratio":       st.column_config.NumberColumn("IV Ratio",       format="%.4f"),
                },
            )
            st.caption(
                f"{len(_ts_df)} combinations  ·  "
                "Green = ready to transform (≥ 5)  ·  "
                "Click any header to re-sort"
            )

        with _side_col:

            # Market Snapshot
            _chg_cls = "p" if daily_chg_pts >= 0 else "n"
            _vix_chg_cls = ""  # VIX delta not tracked intraday here
            st.markdown(
                f"""<div class="spanel">
  <div class="spanel-ttl">Market Snapshot</div>
  <div class="mkt-grid">
    <div class="mkt-cell">
      <span class="mc-l">SPX</span>
      <span class="mc-v">{spx_price:,.1f}</span>
      <span class="mc-c {_chg_cls}">{day_arrow} {chg_display}</span>
    </div>
    <div class="mkt-cell">
      <span class="mc-l">VIX</span>
      <span class="mc-v">{vix_str}</span>
      <span class="mc-c" style="color:var(--text-3)">implied vol</span>
    </div>
    <div class="mkt-cell">
      <span class="mc-l">IV Ratio</span>
      <span class="mc-v">{ts_now.ratio:.4f}</span>
      <span class="mc-c" style="color:var(--text-3)">F/B ATM</span>
    </div>
    <div class="mkt-cell">
      <span class="mc-l">Max |GEX|</span>
      <span class="mc-v" style="font-size:.78rem">{gex_label}</span>
      <span class="mc-c" style="color:var(--text-3)">strike</span>
    </div>
  </div>
</div>""",
                unsafe_allow_html=True,
            )

            # IV Ratio snapshot donut
            if "IV Ratio" in _ts_df.columns:
                _iv_ratios = _ts_df["IV Ratio"].dropna()
                if len(_iv_ratios) > 0:
                    _gt15 = int((_iv_ratios >= 1.5).sum())
                    _mid  = int(((_iv_ratios >= 1.2) & (_iv_ratios < 1.5)).sum())
                    _lt12 = int((_iv_ratios < 1.2).sum())
                    _total_r = max(_gt15 + _mid + _lt12, 1)
                    fig_donut = go.Figure(go.Pie(
                        labels=["≥ 1.5", "1.2 – 1.5", "< 1.2"],
                        values=[_gt15, _mid, _lt12],
                        hole=.62,
                        marker=dict(colors=["#10d4a3", "#5b9cff", "#f05252"],
                                    line=dict(color="#060b12", width=2)),
                        textinfo="none",
                        hovertemplate="%{label}: %{value}<extra></extra>",
                        sort=False,
                    ))
                    fig_donut.add_annotation(
                        text=f"<b>{_avg_iv_ratio:.2f}</b>" if _avg_iv_ratio else "",
                        x=.5, y=.5, xref="paper", yref="paper",
                        showarrow=False,
                        font=dict(size=15, color="#dde6f1", family="JetBrains Mono"),
                    )
                    fig_donut.update_layout(
                        height=170,
                        margin=dict(l=4, r=4, t=4, b=4),
                        paper_bgcolor="#0c1421",
                        plot_bgcolor="#0c1421",
                        showlegend=False,
                        font=dict(family="Inter", color="#6d8fa8", size=10),
                    )
                    st.markdown('<div class="spanel"><div class="spanel-ttl">IV Ratio Snapshot</div>',
                                unsafe_allow_html=True)
                    st.plotly_chart(fig_donut, use_container_width=True,
                                    config={"displayModeBar": False})
                    st.markdown(
                        f"<div style='font-size:.64rem;color:#2f4459;margin:-8px 0 4px;'>"
                        f"<span style='color:#10d4a3'>■</span> ≥1.5: {_gt15} &nbsp;"
                        f"<span style='color:#5b9cff'>■</span> 1.2–1.5: {_mid} &nbsp;"
                        f"<span style='color:#f05252'>■</span> &lt;1.2: {_lt12}"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )

            # Diff distribution bar chart
            if "Transform Diff" in _ts_df.columns and len(_ts_df) > 0:
                _diffs = _ts_df["Transform Diff"]
                _bins  = [
                    ("≤ 0",   int((_diffs <= 0).sum()),           "#f05252"),
                    ("0–2",   int(((_diffs > 0)  & (_diffs <= 2)).sum()), "#6d8fa8"),
                    ("2–5",   int(((_diffs > 2)  & (_diffs <= 5)).sum()), "#5b9cff"),
                    ("5–10",  int(((_diffs > 5)  & (_diffs <= 10)).sum()), "#10d4a3"),
                    ("10–15", int(((_diffs > 10) & (_diffs <= 15)).sum()), "#10d4a3"),
                    ("15+",   int((_diffs > 15).sum()),           "#10d4a3"),
                ]
                _bl = [b[0] for b in _bins]
                _bv = [b[1] for b in _bins]
                _bc = [b[2] for b in _bins]

                fig_bar = go.Figure(go.Bar(
                    x=_bl, y=_bv,
                    marker=dict(color=_bc, line=dict(width=0)),
                    hovertemplate="%{x}: %{y}<extra></extra>",
                ))
                fig_bar.update_layout(
                    height=150,
                    margin=dict(l=4, r=4, t=6, b=20),
                    paper_bgcolor="#0c1421",
                    plot_bgcolor="#0c1421",
                    xaxis=dict(tickfont=dict(size=9, color="#2f4459"), gridcolor="transparent"),
                    yaxis=dict(tickfont=dict(size=9, color="#2f4459"), gridcolor="#0f1e30"),
                    bargap=.3,
                )
                st.markdown('<div class="spanel"><div class="spanel-ttl">Difference Distribution</div>',
                            unsafe_allow_html=True)
                st.plotly_chart(fig_bar, use_container_width=True,
                                config={"displayModeBar": False})
                st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ENTRY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

with tab_entry:

    st.markdown(
        '<div class="sh"><span class="sh-ico">📊</span>'
        '<span class="sh-ttl">Entry Analysis</span></div>',
        unsafe_allow_html=True,
    )

    # ── Row 1: position cost + theta ─────────────────────────────────────────
    r1a, r1b, r1c, r1d = st.columns(4)

    with r1a:
        if _diag_mark is not None:
            _diag_dollar = int(round(_diag_mark * 100))
            st.metric(
                "Diagonal Mark",
                f"{_diag_mark:.2f} pts  ·  ${_diag_dollar:,}",
                help="Per-share mark price of the diagonal × 100 = dollar cost per contract.",
            )
        else:
            st.metric("Diagonal Mark", "— (set strikes)")
        st.caption("What you'd pay to open this position right now.")

    with r1b:
        st.metric(
            "ATM Straddle",
            f"${_straddle:.2f}" if _straddle else "—",
            help="S × σ × √(2·DTE/365·π). The market's expected ±1σ move by front expiry.",
        )
        st.caption("How big a move the market expects by front expiry.")

    with r1c:
        st.metric(
            "Normalized Debit",
            f"{_norm_deb:.4f}" if _norm_deb is not None else "— (set strikes)",
            help="Diagonal Mark ÷ ATM Straddle. Removes SPX price-level and vol-regime "
                 "effects so entry cost is comparable across different dates. HYPOTHESIS.",
        )
        st.caption("Is this cheap or expensive relative to expected market movement?")

    with r1d:
        if _theta_diff is not None and _theta_diff.available:
            _net_ct_s = (
                f"+${_theta_diff.net_daily_theta_ct:.2f}"
                if _theta_diff.net_daily_theta_ct >= 0
                else f"−${abs(_theta_diff.net_daily_theta_ct):.2f}"
            )
            st.metric(
                "Net Daily θ / contract",
                _net_ct_s,
                help="Position earns this much per day from time decay alone. "
                     "Front decays faster than back — the difference is your daily gain. "
                     "HYPOTHESIS — not yet validated as entry predictor.",
            )
            st.caption(
                f"Front θ {_theta_diff.front_sum:+.3f} · "
                f"Back θ {_theta_diff.back_sum:+.3f} · "
                f"Net {_theta_diff.net_daily_theta:+.3f} /sh/day"
            )
        else:
            st.metric(
                "Net Daily θ / contract",
                "— (set strikes)" if not strikes_set else "— (Greeks N/A)",
            )
            st.caption("How much time decay earns you each calendar day.")

    st.markdown("<div style='margin-bottom:4px'></div>", unsafe_allow_html=True)

    # ── Row 2: Transform-to-IC + market conditions ────────────────────────────
    r2a, r2b, r2c, r2d = st.columns(4)

    with r2a:
        if _ic_mark is not None and _diag_mark is not None:
            _ic_signal = _ic_mark - _diag_mark
            _ic_color  = "#10d4a3" if _ic_signal > 5 else "#dde6f1"
            _ic_dollar = int(round(_ic_mark * 100))
            st.metric(
                "Transform Order Mark",
                f"{_ic_mark:.2f} pts  ·  ${_ic_dollar:,}",
                help="Credit value of the resulting IC after transformation: "
                     "short back legs minus long wings at ±5. "
                     "Green when IC Mark − Diagonal Mark > $5 (favorable to transform). "
                     "HYPOTHESIS — signal not yet validated.",
            )
            st.markdown(
                f"<p style='margin:0;font-size:0.78em;color:{_ic_color};'>"
                f"vs Diagonal: {_ic_signal:+.2f} pts"
                f"{'  ✓ Transformation favorable' if _ic_signal > 5 else ''}"
                f"</p>",
                unsafe_allow_html=True,
            )
        else:
            st.metric("Transform Order Mark", "— (set strikes)" if not strikes_set
                      else "— (wing strikes not in chain)")
            st.caption("Value of IC after transforming diagonal at these strikes.")

    with r2b:
        st.metric(
            "IV Ratio Percentile",
            f"{_iv_pct:.0f}th" if _iv_pct is not None else "— (need history)",
            help="Where today's IV ratio ranks within the last 90 days. "
                 "100th = front has never been this expensive relative to back.",
        )
        st.caption("Is today's term structure unusually steep or flat?")

    with r2c:
        st.metric(
            "Liquidity (ATM)",
            f"{_liquidity:.0f} / 100",
            help="Composite of ATM front-strike volume and open interest. "
                 "Higher = tighter bid/ask and easier fills. Below 50 = expect wider slippage.",
        )
        st.caption("How easy will it be to get filled near the mark price?")

    with r2d:
        _THRESHOLD = 5.0
        if _ic_mark is not None and _diag_mark is not None:
            _diff = _ic_mark - _diag_mark
            if _diff >= _THRESHOLD:
                st.metric("Transform Difference", f"+{_diff:.2f}")
                st.markdown(
                    "<div style='margin-top:2px;padding:6px 10px;border-radius:8px;"
                    "background:rgba(16,212,163,.08);border:1px solid rgba(16,212,163,.25);'>"
                    "<span style='color:#10d4a3;font-size:0.84em;font-weight:600;'>"
                    "✓ Transformation threshold reached</span><br>"
                    f"<span style='color:#6d8fa8;font-size:0.76em;'>"
                    f"Ready to transform · +{_diff:.2f} pts above threshold</span>"
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                _remaining = _THRESHOLD - _diff
                _progress  = max(0.0, min(1.0, _diff / _THRESHOLD))
                _filled    = int(_progress * 10)
                _bar       = "█" * _filled + "░" * (10 - _filled)
                st.metric("Transform Difference", f"{_diff:.2f}",
                          help=f"Transform Order Mark − Diagonal Mark. Green when ≥ {_THRESHOLD}.")
                st.markdown(
                    f"<div style='margin-top:4px;font-size:0.78em;color:#6d8fa8;'>"
                    f"<span style='color:#f0a429;font-family:JetBrains Mono,monospace;'>{_bar}</span>"
                    f"&nbsp;{_progress*100:.0f}%<br>"
                    f"<span style='color:#2f4459;'>{_remaining:.2f} pts until threshold</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.metric("Transform Difference", "— (set strikes)")
            st.caption(f"Needs {_THRESHOLD} pts to trigger transformation signal.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — CALENDAR EDGE
# ═══════════════════════════════════════════════════════════════════════════════

with tab_edge:

    st.markdown(
        '<div class="sh"><span class="sh-ico">📈</span>'
        f'<span class="sh-ttl">Calendar Edge</span>'
        f'<span class="sh-bdg">{iv_engine.interpret_curve(ts_now)[:30]}</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Metrics row
    iv_index = float(chain_df.groupby("expiry")["iv"].mean().mean())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("ATM IV Ratio (F/B)", f"{ts_now.ratio:.4f}")
    m2.metric("Front ATM IV",       f"{ts_now.front_iv:.2f}%")
    m3.metric("Back ATM IV",        f"{ts_now.back_iv:.2f}%")
    m4.metric("IV Index (avg)",     f"{iv_index:.2f}%")

    _info_col, _radio_col = st.columns([3, 1])
    with _info_col:
        st.info(iv_engine.interpret_curve(ts_now))
    with _radio_col:
        period_label = st.radio(
            "Chart Range",
            ["Today", "5D", "10D", "20D"],
            horizontal=False,
            label_visibility="collapsed",
            key="period_radio",
        )

    period_days = {"Today": 1, "5D": 5, "10D": 10, "20D": 20}[period_label]
    _fhp = _load_atm_hist_fb(front_expiry, period_days)
    _bhp = _load_atm_hist_fb(back_expiry,  period_days)
    atm_merged = pd.DataFrame()
    if not _fhp.empty and not _bhp.empty:
        atm_merged = pd.merge(
            _fhp[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "front_iv"}),
            _bhp[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "back_iv"}),
            on="timestamp", how="inner",
        )
        atm_merged["iv_ratio"] = atm_merged["front_iv"] / atm_merged["back_iv"]

    _PLOTLY_LAYOUT = dict(
        paper_bgcolor="#060b12",
        plot_bgcolor="#060b12",
        font=dict(family="Inter", color="#6d8fa8", size=11),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)", font=dict(size=10),
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                        font=dict(color="#dde6f1", size=12)),
        xaxis=dict(gridcolor="#0c1928", zeroline=False),
        yaxis=dict(gridcolor="#0c1928", zeroline=False),
    )

    if not atm_merged.empty:
        _xaxis_today = (
            dict(range=[f"{session_date} 09:30", f"{session_date} 16:15"],
                 rangebreaks=_SESSION_RANGEBREAKS, gridcolor="#0c1928")
            if period_label == "Today"
            else dict(rangebreaks=_SESSION_RANGEBREAKS, gridcolor="#0c1928")
        )

        # Primary dual-axis chart
        fig_atm = go.Figure()
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["front_iv"],
            name="Front ATM IV", line=dict(color="#10d4a3", width=1.8), yaxis="y1"))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["back_iv"],
            name="Back ATM IV",  line=dict(color="#5b9cff", width=1.8), yaxis="y1"))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["iv_ratio"],
            name="IV Ratio (F/B)", line=dict(color="#f05252", width=1.8), yaxis="y2"))
        fig_atm.update_layout(
            **_PLOTLY_LAYOUT,
            height=300,
            margin=dict(l=20, r=20, t=10, b=20),
            xaxis=_xaxis_today,
            yaxis=dict(title="IV %", side="left",  gridcolor="#0c1928"),
            yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False),
        )
        st.plotly_chart(fig_atm, use_container_width=True)

        samp_warn = iv_engine.sample_size_warning(atm_merged["iv_ratio"])
        if samp_warn:
            st.warning(samp_warn)

        # Stacked chart
        st.markdown("**Front vs Back ATM IV — same axis · IV Ratio by regime**")
        fig_stack = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.62, 0.38], vertical_spacing=0.06,
            subplot_titles=(
                "Front vs Back ATM IV — same axis (the gap IS the spread)",
                "IV Ratio (F/B) — colored by regime",
            ),
        )
        fig_stack.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["front_iv"],
            name="Front ATM IV", line=dict(color="#10d4a3", width=1.8)), row=1, col=1)
        fig_stack.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["back_iv"],
            name="Back ATM IV",  line=dict(color="#5b9cff", width=1.8)), row=1, col=1)
        for tr in _banded_ratio_traces(atm_merged["timestamp"], atm_merged["iv_ratio"]):
            fig_stack.add_trace(tr, row=2, col=1)
        for thr, dash in [(1.00, "solid"), (0.70, "dot"), (1.30, "dot")]:
            fig_stack.add_hline(
                y=thr, line=dict(color="#2a3f56", width=1, dash=dash), row=2, col=1)
        if period_label == "Today":
            fig_stack.update_xaxes(
                range=[f"{session_date} 09:30", f"{session_date} 16:15"],
                rangebreaks=_SESSION_RANGEBREAKS,
                gridcolor="#0c1928",
            )
        else:
            fig_stack.update_xaxes(rangebreaks=_SESSION_RANGEBREAKS, gridcolor="#0c1928")
        fig_stack.update_yaxes(title_text="IV %",    row=1, col=1, gridcolor="#0c1928")
        fig_stack.update_yaxes(title_text="Ratio",   row=2, col=1, gridcolor="#0c1928")
        fig_stack.update_layout(
            **_PLOTLY_LAYOUT,
            height=520,
            margin=dict(l=20, r=20, t=40, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=-0.18,
                        xanchor="left", x=0, font=dict(size=10),
                        bgcolor="rgba(0,0,0,0)"),
            paper_bgcolor="#060b12",
            plot_bgcolor="#060b12",
            font=dict(family="Inter", color="#6d8fa8", size=11),
            hovermode="x unified",
        )
        st.plotly_chart(fig_stack, use_container_width=True)
        st.caption(
            "Top: front and back ATM IV share one axis — the vertical gap IS the spread. "
            "Bottom: ratio colored by regime at 0.70 / 1.00 / 1.30. "
            "Green (≥1) = backwardation (front rich). Amber (<0.70) = usually 0DTE decay artifact."
        )

        # Intraday scatter
        st.markdown("**Front vs Back IV scatter — intraday trajectory**")
        _sc = atm_merged.copy()
        _sc["hod"] = _sc["timestamp"].dt.hour + _sc["timestamp"].dt.minute / 60.0
        _lo = float(min(_sc["back_iv"].min(), _sc["front_iv"].min()))
        _hi = float(max(_sc["back_iv"].max(), _sc["front_iv"].max()))
        _pad = (_hi - _lo) * 0.05 or 1.0
        fig_intra = go.Figure()
        fig_intra.add_trace(go.Scatter(
            x=[_lo - _pad, _hi + _pad], y=[_lo - _pad, _hi + _pad], mode="lines",
            name="R = 1  (Front = Back)", line=dict(color="#2a3f56", dash="dash")))
        fig_intra.add_trace(go.Scatter(
            x=_sc["back_iv"], y=_sc["front_iv"], mode="markers", name="snapshots",
            marker=dict(size=6, color=_sc["hod"], colorscale="Viridis",
                        showscale=True, colorbar=dict(title="Hour ET"),
                        line=dict(width=0)),
            customdata=_sc["iv_ratio"],
            hovertemplate="Back %{x:.2f}%<br>Front %{y:.2f}%<br>R=%{customdata:.4f}<extra></extra>"))
        fig_intra.update_layout(
            **_PLOTLY_LAYOUT,
            height=420,
            margin=dict(l=20, r=20, t=10, b=20),
            xaxis=dict(title="Back ATM IV %", gridcolor="#0c1928"),
            yaxis=dict(title="Front ATM IV %", gridcolor="#0c1928"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                        bgcolor="rgba(0,0,0,0)"),
        )
        st.plotly_chart(fig_intra, use_container_width=True)
        st.caption(
            "Each dot is one snapshot. Above the dashed line = backwardation (R>1); below = contango. "
            "Color = time of day. A cloud hugging one ray → ratio ≈ constant; "
            "fanning across angles → ratio varies independently of vol level."
        )

    else:
        st.caption(f"No ATM IV history for {front_expiry} / {back_expiry} in the selected range.")

    # Strike detail section
    st.markdown(
        f'<div class="sh" style="margin-top:1.25rem">'
        f'<span class="sh-ico">🎯</span>'
        f'<span class="sh-ttl">Strike Detail</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    left_col, right_col = st.columns([1, 3])

    with left_col:
        st.markdown("**Expiry Detail**")
        for exp_label_s, exp_date, dte_val in [
            ("Front", front_expiry, front_dte),
            ("Back",  back_expiry,  back_dte),
        ]:
            exp_rows = db.get_latest_atm_iv_snapshots(config.DB_PATH, exp_date, n=2)
            if exp_rows:
                atm_now = exp_rows[0]["atm_avg_iv"] * 100
                atm_chg = (
                    (exp_rows[0]["atm_avg_iv"] - exp_rows[1]["atm_avg_iv"]) * 100
                    if len(exp_rows) == 2 else 0.0
                )
                chg_color = "#10d4a3" if atm_chg >= 0 else "#f05252"
                chg_arrow = "↑" if atm_chg >= 0 else "↓"
                st.markdown(
                    f"<p style='margin:0;font-size:0.78em;color:#2f4459;'>"
                    f"{exp_label_s} · {exp_date} · {dte_val} DTE</p>"
                    f"<p style='margin:0;font-size:1.55em;font-weight:600;color:#dde6f1;'>"
                    f"{atm_now:.2f}%</p>"
                    f"<p style='margin:0 0 10px 0;font-size:0.82em;color:{chg_color};'>"
                    f"{chg_arrow} {atm_chg:+.2f}%</p>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<p style='margin:0;font-size:0.78em;color:#2f4459;'>"
                    f"{exp_label_s} · {exp_date} · {dte_val} DTE</p>"
                    f"<p style='margin:0 0 10px 0;color:#2f4459;'>N/A</p>",
                    unsafe_allow_html=True,
                )

        st.markdown("<hr style='margin:8px 0;opacity:0.1;'>", unsafe_allow_html=True)
        st.markdown("**Strike Detail**")

        if strikes_set:
            fc_call = iv_engine.strike_contract(chain_df, front_expiry, call_strike, "CALL")
            bc_call = iv_engine.strike_contract(chain_df, back_expiry,  call_strike, "CALL")
            fc_put  = iv_engine.strike_contract(chain_df, front_expiry, put_strike,  "PUT")
            bc_put  = iv_engine.strike_contract(chain_df, back_expiry,  put_strike,  "PUT")

            for leg_label, fc, bc in [
                (f"Put  {put_strike:.0f}",  fc_put,  bc_put),
                (f"Call {call_strike:.0f}", fc_call, bc_call),
            ]:
                ratio_str = f"{fc.iv / bc.iv:.4f}" if (fc.iv and bc.iv) else "N/A"
                f_iv_str  = f"{fc.iv:.2f}%"   if fc.iv   else "N/A"
                b_iv_str  = f"{bc.iv:.2f}%"   if bc.iv   else "N/A"
                f_mk_str  = f"${fc.mark:.2f}" if fc.mark  else "N/A"
                b_mk_str  = f"${bc.mark:.2f}" if bc.mark  else "N/A"
                st.markdown(
                    f"<p style='margin:6px 0 2px 0;font-weight:600;color:#dde6f1;'>{leg_label}</p>"
                    f"<p style='margin:0;font-size:0.8em;'>"
                    f"IV → F <span style='color:#10d4a3;'>{f_iv_str}</span> "
                    f"/ B <span style='color:#5b9cff;'>{b_iv_str}</span> "
                    f"&nbsp;·&nbsp; Ratio <span style='color:#f05252;'>{ratio_str}</span></p>"
                    f"<p style='margin:0 0 6px 0;font-size:0.8em;'>"
                    f"Mark → F <span style='color:#10d4a3;'>{f_mk_str}</span> "
                    f"/ B <span style='color:#5b9cff;'>{b_mk_str}</span></p>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Set call and put strikes in Controls above.")

    with right_col:
        st.markdown("**Selected-Strike IV**")
        st.caption("Front vs back IV at your trade strikes — ratio on right axis.")

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
                    fig_str.add_trace(go.Scatter(
                        x=cm["timestamp"], y=cm["f_call"],
                        name=f"Front {call_strike:.0f}C",
                        line=dict(color="#10d4a3", width=1.5), yaxis="y1"))
                    fig_str.add_trace(go.Scatter(
                        x=cm["timestamp"], y=cm["b_call"],
                        name=f"Back  {call_strike:.0f}C",
                        line=dict(color="#5b9cff", width=1.5), yaxis="y1"))
                    fig_str.add_trace(go.Scatter(
                        x=cm["timestamp"], y=cm["call_ratio"],
                        name="Call Ratio (F/B)",
                        line=dict(color="#f05252", width=1.5), yaxis="y2"))
                if put_ready:
                    pm = pd.merge(
                        fph[["timestamp", "iv"]].rename(columns={"iv": "f_put"}),
                        bph[["timestamp", "iv"]].rename(columns={"iv": "b_put"}),
                        on="timestamp", how="inner",
                    )
                    pm["put_ratio"] = pm["f_put"] / pm["b_put"]
                    fig_str.add_trace(go.Scatter(
                        x=pm["timestamp"], y=pm["f_put"],
                        name=f"Front {put_strike:.0f}P",
                        line=dict(color="#10d4a3", width=1.5, dash="dot"), yaxis="y1"))
                    fig_str.add_trace(go.Scatter(
                        x=pm["timestamp"], y=pm["b_put"],
                        name=f"Back  {put_strike:.0f}P",
                        line=dict(color="#5b9cff", width=1.5, dash="dot"), yaxis="y1"))
                    fig_str.add_trace(go.Scatter(
                        x=pm["timestamp"], y=pm["put_ratio"],
                        name="Put Ratio (F/B)",
                        line=dict(color="#f05252", width=1.5, dash="dot"), yaxis="y2"))
                _str_xaxis = (
                    dict(range=[f"{session_date} 09:30", f"{session_date} 16:15"],
                         rangebreaks=_SESSION_RANGEBREAKS, gridcolor="#0c1928")
                    if period_label == "Today"
                    else dict(rangebreaks=_SESSION_RANGEBREAKS, gridcolor="#0c1928")
                )
                fig_str.update_layout(
                    **_PLOTLY_LAYOUT,
                    height=360,
                    margin=dict(l=20, r=20, t=10, b=20),
                    xaxis=_str_xaxis,
                    yaxis=dict(title="IV %", side="left",  gridcolor="#0c1928"),
                    yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="left", x=0, bgcolor="rgba(0,0,0,0)"),
                )
                st.plotly_chart(fig_str, use_container_width=True)
            else:
                st.info(
                    f"No per-strike history for {call_strike:.0f}C / {put_strike:.0f}P "
                    f"in the selected range. Try 'Today'."
                )
        else:
            st.caption("Enter call and put strikes in the Controls row above.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — HISTORICAL STATISTICS
# ═══════════════════════════════════════════════════════════════════════════════

with tab_hist:

    st.markdown(
        f'<div class="sh"><span class="sh-ico">📉</span>'
        f'<span class="sh-ttl">Historical Statistics — ATM IV Ratio</span>'
        f'<span class="sh-bdg">{front_expiry} ({front_dte}d) / {back_expiry} ({back_dte}d)</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    stat_cols = st.columns(4)
    for col, (label, days) in zip(
        stat_cols,
        [("Today", 1), ("5 Days", 5), ("10 Days", 10), ("20 Days", 20)],
    ):
        pf = _load_atm_hist_fb(front_expiry, days)
        pb = _load_atm_hist_fb(back_expiry,  days)
        with col:
            st.caption(label)
            if not pf.empty and not pb.empty:
                pm = pd.merge(
                    pf[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "f"}),
                    pb[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "b"}),
                    on="timestamp",
                )
                pm["ratio"] = pm["f"] / pm["b"]
                rs       = iv_engine.range_stats(pm["ratio"], ts_now.ratio)
                pct_rank = iv_engine.percentile_rank(pm["ratio"], ts_now.ratio)
                _is_low  = pct_rank < 25
                _is_high = pct_rank > 75
                _ctx_color = "#10d4a3" if _is_high else ("#f05252" if _is_low else "#6d8fa8")
                _ctx_label = "HIGH" if _is_high else ("LOW" if _is_low else "MID")
                st.markdown(
                    f"""<div style="font-size:0.83em;line-height:1.6;">
  <span style="color:#2f4459;">Min</span> {rs.low:.4f}
  <div style="background:linear-gradient(90deg,#0f1e30,#1a2d45);height:5px;border-radius:3px;position:relative;margin:5px 0;">
    <div style="position:absolute;left:{rs.position_pct:.1f}%;top:-4px;width:13px;height:13px;background:#f05252;border-radius:50%;transform:translateX(-50%);border:2px solid #060b12;"></div>
  </div>
  <span style="color:#2f4459;">Max</span> {rs.high:.4f}<br>
  <span style="color:#2f4459;">Now</span> <b style="color:#dde6f1;">{ts_now.ratio:.4f}</b>
  &nbsp;<span style="color:{_ctx_color};font-size:0.88em;">{pct_rank:.0f}th · {_ctx_label}</span>
</div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No data")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — RESEARCH
# ═══════════════════════════════════════════════════════════════════════════════

with tab_research:

    st.markdown(
        '<div class="sh"><span class="sh-ico">🔬</span>'
        '<span class="sh-ttl">Research — IV Ratio vs. Normalized Debit</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Each point is one intraday snapshot. X = ATM IV Ratio (F/B); "
        "Y = Normalized Debit (diagonal mark ÷ ATM straddle). "
        "Amber diamond = current observation. No predictive claim is made."
    )

    if not strikes_set:
        st.info("Set call and put strikes in Controls to populate the scatter.")
    else:
        _hist_rows = db.get_diagonal_history(
            config.DB_PATH, front_expiry, back_expiry,
            call_strike, put_strike, days=90,
        )
        _hist = pd.DataFrame([dict(r) for r in _hist_rows]) if _hist_rows else pd.DataFrame()
        if not _hist.empty:
            _hist["net_debit"] = (
                _hist["back_call_mark"] + _hist["back_put_mark"]
                - _hist["front_call_mark"] - _hist["front_put_mark"]
            )
            _hist["atm_straddle_hist"] = (
                _hist["spx"] * _hist["front_iv"]
                * np.sqrt(2.0 * _hist["front_dte"] / (365.0 * np.pi))
            )
            _hist = _hist[_hist["atm_straddle_hist"] > 0].copy()
            _hist["norm_debit_hist"] = _hist["net_debit"] / _hist["atm_straddle_hist"]
            _hist["ts"] = pd.to_datetime(_hist["snapshot_timestamp"])
            _hist["hover_date"] = _hist["ts"].dt.strftime("%Y-%m-%d %H:%M UTC")

        _has_data = not _hist.empty and len(_hist) >= 5
        fig_sc = go.Figure()
        if _has_data:
            fig_sc.add_trace(go.Scatter(
                x=_hist["iv_ratio"], y=_hist["norm_debit_hist"], mode="markers",
                marker=dict(color="#5b9cff", size=7, opacity=0.5,
                            line=dict(color="#1e3a5f", width=0.5)),
                showlegend=True, name="Historical",
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>SPX: %{customdata[1]:.0f}<br>"
                    "IV Ratio: %{x:.4f}<br>Norm. Debit: %{y:.4f}<br>"
                    "Raw Debit: $%{customdata[2]:.2f}<extra></extra>"
                ),
                customdata=list(zip(_hist["hover_date"], _hist["spx"], _hist["net_debit"])),
            ))
            _valid = _hist[["iv_ratio", "norm_debit_hist"]].dropna()
            if len(_valid) >= 5:
                _m_sc, _b_sc = np.polyfit(_valid["iv_ratio"], _valid["norm_debit_hist"], 1)
                _x_tr = np.linspace(_valid["iv_ratio"].min(), _valid["iv_ratio"].max(), 100)
                fig_sc.add_trace(go.Scatter(
                    x=_x_tr, y=_m_sc * _x_tr + _b_sc, mode="lines",
                    line=dict(color="#2a3f56", width=1.5, dash="dash"),
                    showlegend=True, name="OLS trend (descriptive)", hoverinfo="skip",
                ))

        if _norm_deb is not None and ts_now.ratio is not None:
            fig_sc.add_trace(go.Scatter(
                x=[ts_now.ratio], y=[_norm_deb], mode="markers",
                marker=dict(symbol="diamond", color="#f0a429", size=14,
                            line=dict(color="#78350f", width=1.5)),
                showlegend=True, name="Current",
                hovertemplate=(
                    "<b>Current observation</b><br>"
                    f"SPX: {spx_price:.0f}<br>"
                    "IV Ratio: %{x:.4f}<br>Norm. Debit: %{y:.4f}<br>"
                    + (f"Diagonal Mark: ${_diag_mark:.2f}" if _diag_mark else "")
                    + "<extra></extra>"
                ),
            ))

        fig_sc.add_vline(
            x=1.0, line=dict(color="#2a3f56", width=1, dash="dot"),
            annotation_text="ratio = 1.0",
            annotation_font=dict(color="#2f4459", size=10),
            annotation_position="top right",
        )
        if not _has_data and _norm_deb is None:
            fig_sc.add_annotation(
                x=0.5, y=0.5, xref="paper", yref="paper",
                text="No data yet — scatter populates as snapshots accumulate.",
                showarrow=False, font=dict(color="#2f4459", size=13),
            )
        fig_sc.update_layout(
            height=400,
            paper_bgcolor="#060b12",
            plot_bgcolor="#060b12",
            margin=dict(l=60, r=20, t=20, b=44),
            font=dict(family="Inter", color="#6d8fa8", size=11),
            xaxis=dict(title="ATM IV Ratio (Front / Back)",
                       title_font=dict(color="#6d8fa8", size=11),
                       tickfont=dict(color="#6d8fa8", size=11),
                       gridcolor="#0c1928", showgrid=True, zeroline=False),
            yaxis=dict(title="Normalized Debit (diagonal mark ÷ ATM straddle)",
                       title_font=dict(color="#6d8fa8", size=11),
                       tickfont=dict(color="#6d8fa8", size=11),
                       gridcolor="#0c1928", showgrid=True, zeroline=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
                        font=dict(color="#6d8fa8", size=11), bgcolor="rgba(0,0,0,0)"),
            hovermode="closest",
            hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                            font=dict(color="#dde6f1", size=13)),
        )
        if not _has_data:
            st.caption(
                "Fewer than 5 complete snapshots found for this strike/expiry pair. "
                "Scatter populates as more data is collected."
            )
        st.plotly_chart(fig_sc, use_container_width=True, config={"displayModeBar": False})
