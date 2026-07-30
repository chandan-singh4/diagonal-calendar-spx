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

import logging
import sys
from datetime import UTC, datetime
from datetime import time as dt_time
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

import config
import db
import iv_engine
import schwab_client

# ─── core/ — pure calculation, extracted from this file in M2 (ADR-032) ───────
# Imported by name rather than qualified (core_format._sparkline etc.) so the
# extraction stayed a pure move with no call-site churn. The names keep their
# leading underscores for the same reason; both are transitional — see ADR-032.
# core.charts is gone from here entirely: every chart site moved to views/ in
# step 2.4, which is what DEBT-030's fix has been waiting for.
from core.format import _fmt_duration, _fmt_eta, _sparkline
from core.ranking import _card_key, _rank_for_panel
from core.scanner import _APPROACHING_LOW, _TSCAN_THRESHOLD, _scan_all_offsets
from core.scanner import _compute_transform_scanner as _compute_transform_scanner_pure
from dataaccess import queries
from state import chart_colors, eligible_history, entry_locks

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

/* ── Attention Strip — persistent, visible on every tab ──────────────────── */
.attn-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  background: linear-gradient(90deg, rgba(16,212,163,.05), rgba(16,212,163,.01));
  border: 1px solid rgba(16,212,163,.16);
  border-radius: var(--r);
  padding: .55rem 1rem;
  margin-bottom: .7rem;
  font-family: var(--sans);
  animation: fadeUp .3s var(--ease) both;
}
.attn-counts { display: flex; align-items: center; gap: 1.1rem; flex-shrink: 0; }
.attn-count-item { display: flex; align-items: baseline; gap: .32rem; }
.attn-count-n {
  font-family: var(--mono); font-weight: 700; font-size: 1rem; color: var(--text);
}
.attn-count-n.green { color: var(--green); }
.attn-count-n.amber { color: var(--amber); }
.attn-count-n.blue  { color: var(--blue); }
.attn-count-l {
  font-size: .62rem; text-transform: uppercase; letter-spacing: .08em; color: var(--text-3);
}
.attn-divider { width: 1px; height: 18px; background: var(--border); flex-shrink: 0; }
.attn-best {
  display: flex; align-items: center; gap: .5rem; font-size: .76rem;
  color: var(--text-2); overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
.attn-best b { color: var(--text); font-family: var(--mono); }
.attn-best .gap-v { color: var(--green); font-family: var(--mono); font-weight: 600; }
.attn-empty { font-size: .76rem; color: var(--text-3); }

/* ── Custom top nav (replaces st.tabs for programmatic switching) ────────── */
div[class*="st-key-topnav"] {
  border-bottom: 1px solid var(--border);
  margin-bottom: .8rem;
}
div[class*="st-key-topnav"] .stButton > button {
  background: transparent !important;
  border: none !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 0 !important;
  color: var(--text-2) !important;
  font-size: .79rem !important;
  font-weight: 500 !important;
  padding: .6rem .2rem .55rem !important;
  box-shadow: none !important;
  transform: none !important;
  width: 100% !important;
}
div[class*="st-key-topnav"] .stButton > button:hover {
  color: var(--text) !important;
  background: transparent !important;
  box-shadow: none !important;
  transform: none !important;
}
div[class*="st-key-topnav"] .stButton > button[kind="primary"] {
  color: var(--blue) !important;
  font-weight: 600 !important;
  border-bottom: 2px solid var(--blue) !important;
  background: transparent !important;
}

/* ── Mission Control opportunity cards ───────────────────────────────────── */
div[class*="st-key-mc_card_"] {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: .8rem .95rem .85rem;
  margin-bottom: .6rem;
  box-shadow: var(--shadow);
  transition: border-color .2s var(--ease);
  display: flex;
  flex-direction: column;
}
div[class*="st-key-mc_card_"]:hover { border-color: var(--border-hi); }
.mc-actions-spacer { margin-top: .55rem; }
div[class*="st-key-mc_card_"] .stButton > button {
  padding: .32rem .7rem !important;
  font-size: .74rem !important;
  white-space: nowrap !important;
}
.mc-rank { font-size: .62rem; font-weight: 700; color: var(--text-3); letter-spacing: .08em; }
.mc-new-badge {
  display: inline-block; font-size: .54rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em; color: var(--amber);
  background: rgba(240,164,41,.1); border: 1px solid rgba(240,164,41,.25);
  border-radius: 4px; padding: .06rem .34rem; margin-left: .4rem;
}
.mc-live-badge {
  display: inline-block; font-size: .54rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em; color: var(--green);
  background: rgba(16,212,163,.1); border: 1px solid rgba(16,212,163,.25);
  border-radius: 4px; padding: .06rem .34rem; margin-left: .4rem;
}
.mc-stale-badge {
  display: inline-block; font-size: .54rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em; color: var(--text-2);
  background: rgba(255,255,255,.04); border: 1px solid var(--border);
  border-radius: 4px; padding: .06rem .34rem; margin-left: .4rem;
}
.mc-metric-v.gap-stale { color: var(--text-2); }
.mc-combo { font-family: var(--mono); font-size: 1rem; font-weight: 700; color: var(--text); margin-top: .15rem; }
.mc-expiry { font-size: .68rem; color: var(--text-2); margin-bottom: .35rem; }
.mc-metrics { display: flex; gap: 1.1rem; margin-bottom: .3rem; }
.mc-metric-l { font-size: .56rem; text-transform: uppercase; letter-spacing: .08em; color: var(--text-3); display: block; }
.mc-metric-v { font-family: var(--mono); font-size: .92rem; font-weight: 600; color: var(--text); }
.mc-metric-v.gap { color: var(--green); }
.mc-eta { font-size: .68rem; color: var(--amber); margin-top: .2rem; }
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

/* ── Chart cards — wrap any chart in st.container(key="chartcard_*") ─────── */
/* Reusable panel: gives a chart its own bordered container that sits above  */
/* the page background, matching the Calendar Edge mockup's visual hierarchy.*/
div[class*="st-key-chartcard_"] {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 1.05rem 1.15rem .8rem;
  margin-bottom: 1rem;
  box-shadow: var(--shadow);
  animation: fadeUp .4s var(--ease) both;
  transition: border-color .2s var(--ease), box-shadow .2s var(--ease);
}
div[class*="st-key-chartcard_"]:hover {
  border-color: rgba(255,255,255,.10);
  box-shadow: var(--shadow-up);
}
/* Chart caption strip below a chart card's plot */
.chart-cap {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  font-size: .64rem;
  color: var(--text-3);
  letter-spacing: .02em;
  padding: .1rem .15rem 0;
  margin-top: -.2rem;
}
.chart-cap .cap-legend { color: var(--text-2); }
.chart-cap .cap-tag {
  font-family: var(--mono);
  font-size: .58rem;
  color: var(--amber);
  background: rgba(240,164,41,.09);
  border: 1px solid rgba(240,164,41,.26);
  border-radius: 4px;
  padding: .08rem .4rem;
  letter-spacing: .03em;
}

/* ── Explanation note — self-explanatory context callout ────────────────── */
/* Unobtrusive "what am I looking at" panel. Use sparingly (one per chart at */
/* most) so density stays high. kind: info (blue) | good (green).           */
.note {
  display: flex;
  gap: .6rem;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-left: 2px solid var(--blue);
  border-radius: var(--r-sm);
  padding: .6rem .85rem;
  margin: .1rem 0 1rem;
  font-size: .74rem;
  line-height: 1.5;
  color: var(--text-2);
  animation: fadeUp .4s var(--ease) both;
}
.note.good { border-left-color: var(--green); }
.note .note-ic { font-size: .82rem; opacity: .9; line-height: 1.3; flex-shrink: 0; }
.note b, .note strong { color: var(--text); font-weight: 600; }

</style>
""", unsafe_allow_html=True)

# ─── Constants ────────────────────────────────────────────────────────────────
# _SPARK_BARS, the session rangebreaks and _break_sessions, and the IV-ratio
# bands now live in core/format.py and core/charts.py — imported at the top of
# this file. Only page-bound state stays here.

# ─── Chart Appearance — user-customizable line colors ─────────────────────────
# Persisted to a small JSON file (same pattern as pinned_pairs.json — this is
# a user-preference file, not market data, so writing it from app.py does not
# violate the collector/dashboard read-only split).
#
# To add color customization for a future line series: add one entry to
# DEFAULT_CHART_COLORS with a (label, hex) tuple. The sidebar picker and the
# reset button both iterate this dict automatically — no other code changes
# needed beyond using CHART_COLORS["your_key"] in the relevant trace.
# The colours themselves and their persistence are state/chart_colors.py.
# config.STATE_DIR is absolute, so these no longer depend on where the dashboard
# was launched from (DEBT-011, ADR-035).
DEFAULT_CHART_COLORS = chart_colors.DEFAULT_CHART_COLORS


def _load_chart_colors() -> dict[str, str]:
    return chart_colors.load(config.STATE_DIR)


def _save_chart_colors(colors: dict[str, str]) -> None:
    chart_colors.save(config.STATE_DIR, colors)


# ─── Entry Locks — post-entry position monitoring ─────────────────────────────
# Same exception-to-pure-reader pattern as chart_colors.json / eligible_history.json.
#
# Purpose: once a diagonal is actually filled, the "current diagonal mark" on
# Chart 1 stops being useful — the trader no longer cares where a *new*
# diagonal would price today, only how the *fixed* entry compares to the
# live Transform Order Mark. Locking a diagonal_mark value at entry time lets
# Chart 1 switch from "hypothetical entry" mode to "position management" mode
# for that specific strike/expiry combo.
#
# Forward-compat note (do not build yet — Journal is explicitly out of scope
# for this round of changes): each lock has a stable `lock_id` and a
# `journal_trade_id` field that stays null under "Monitor Only". When Journal
# integration is scoped, a "Monitor + Log Trade" path can create the Journal
# row and write its id back into this same lock record — the lock stays the
# single source of truth for entry price/time either way, rather than the
# Journal and this file drifting into two independent copies of the same fact.
# Persistence and the lock record itself are state/entry_locks.py; these
# wrappers only supply config.STATE_DIR (ADR-035).

def _entry_lock_key(front_expiry: str, back_expiry: str, put_strike: float, call_strike: float) -> str:
    return entry_locks.key(front_expiry, back_expiry, put_strike, call_strike)


def _load_entry_locks() -> dict:
    return entry_locks.load(config.STATE_DIR)


def _create_entry_lock(front_expiry: str, back_expiry: str, put_strike: float,
                        call_strike: float, diagonal_mark: float, mode: str) -> dict:
    return entry_locks.create(
        config.STATE_DIR, front_expiry, back_expiry, put_strike, call_strike,
        diagonal_mark=diagonal_mark, mode=mode,
        display_tz=config.DISPLAY_TIMEZONE,
    )


def _clear_entry_lock(front_expiry: str, back_expiry: str, put_strike: float, call_strike: float) -> None:
    entry_locks.clear(config.STATE_DIR, front_expiry, back_expiry, put_strike, call_strike)


def _update_entry_lock_mark(front_expiry: str, back_expiry: str, put_strike: float,
                             call_strike: float, new_mark: float) -> None:
    entry_locks.update_mark(config.STATE_DIR, front_expiry, back_expiry,
                             put_strike, call_strike, new_mark=new_mark)


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


def _get_entry_lock(front_expiry: str, back_expiry: str, put_strike: float, call_strike: float) -> dict | None:
    return _load_entry_locks().get(_entry_lock_key(front_expiry, back_expiry, put_strike, call_strike))


# ─── Eligibility registry — persisted log of non-ATM crossing events ──────────
# A small JSON file (same exception-to-pure-reader pattern as chart_colors.json)
# that accumulates which non-ATM combos have crossed Transform Gap >= 5 and
# when, so "recently eligible" opportunities survive even if no one was
# watching the dashboard when they crossed. Honest limitation: this only
# accumulates going forward from when the feature ships — there's no
# retroactive backfill, since that would require replaying full historical
# option chains, which is the expensive thing Mission Control caching was
# built specifically to avoid.
# Persistence is state/eligible_history.py. What BELONGS in the registry — the
# upsert below and the retention window — is Mission Control's business logic
# and stays here (ADR-035).
_ELIGIBLE_HISTORY_RETENTION_DAYS  = eligible_history.RETENTION_DAYS


def _load_eligible_history() -> dict:
    return eligible_history.load(config.STATE_DIR)


def _save_eligible_history(registry: dict) -> None:
    eligible_history.save(config.STATE_DIR, registry)


def _update_eligible_history(non_atm_df: pd.DataFrame, snapshot_ts: str) -> dict:
    """
    Upsert every non-ATM combo currently >= threshold into the registry.
    Only ever called from inside the snapshot-cached Mission Control core
    (see _compute_mc_core), so this fires once per NEW snapshot — not on
    every tab click or widget interaction.
    """
    registry = _load_eligible_history()
    if not non_atm_df.empty:
        eligible_now = non_atm_df[non_atm_df["Transform Diff"] >= _TSCAN_THRESHOLD]
        for _, row in eligible_now.iterrows():
            front_raw = row["Front Expiry"].split(" ")[0]
            back_raw  = row["Back Expiry"].split(" ")[0]
            put_s     = int(row["Put Strike"])
            call_s    = int(row["Call Strike"])
            gap       = float(row["Transform Diff"])
            iv_ratio  = row.get("IV Ratio")
            key = f"{front_raw}|{back_raw}|{put_s}|{call_s}"
            prev = registry.get(key, {})
            registry[key] = dict(
                front_raw=front_raw, back_raw=back_raw,
                put_strike=put_s, call_strike=call_s,
                iv_ratio=(None if pd.isna(iv_ratio) else iv_ratio),
                first_seen=prev.get("first_seen", snapshot_ts),
                last_seen=snapshot_ts,
                last_gap=gap,
                max_gap=max(prev.get("max_gap", 0.0), gap),
                hit_count=prev.get("hit_count", 0) + 1,
            )

    # Prune anything that hasn't been seen in a while so the file stays small.
    try:
        cutoff = pd.Timestamp(snapshot_ts) - pd.Timedelta(days=_ELIGIBLE_HISTORY_RETENTION_DAYS)
        registry = {
            k: v for k, v in registry.items()
            if pd.Timestamp(v.get("last_seen", snapshot_ts)) >= cutoff
        }
    except (ValueError, TypeError):
        pass

    _save_eligible_history(registry)
    return registry


def _backfill_eligible_history(front_raw: str, back_raw: str,
                                put_strike: float, call_strike: float,
                                gap_df: pd.DataFrame) -> None:
    """
    Opportunistic registry backfill — called from Calendar Edge whenever a
    user manually selects a strike/expiry pair and pulls its mark history.
    The Mission Control sweep only checks a fixed symmetric grid of offsets
    (see _SWEEP_OFFSETS), so a genuinely asymmetric or off-grid combo can
    show a real >= 5 crossing in its history without ever entering the
    registry through the automated scan. This catches it the moment someone
    actually looks — zero extra DB calls, since gap_df is already fetched
    for the Diagonal vs. Transform Order Mark chart.
    """
    if gap_df.empty or "gap" not in gap_df.columns:
        return
    crossings = gap_df[gap_df["gap"] >= _TSCAN_THRESHOLD].copy()
    if crossings.empty:
        return

    # gap_df's timestamps come from Calendar Edge converted to
    # config.DISPLAY_TIMEZONE and then stripped to NAIVE ET wall-clock (Plotly
    # rangebreaks require naive timestamps). The registry stores naive UTC
    # strings everywhere else (snapshot_ts from the live scan path). Normalize
    # to naive UTC here so the two paths stay comparable.
    _ts = crossings["timestamp"]
    if getattr(_ts.dt, "tz", None) is not None:
        crossings["timestamp"] = _ts.dt.tz_convert("UTC").dt.tz_localize(None)
    else:
        # Naive ET wall-clock in, naive UTC out — localize back to ET first,
        # otherwise ET times would be stored as UTC (a silent 4/5-hour skew).
        crossings["timestamp"] = (
            _ts.dt.tz_localize(config.DISPLAY_TIMEZONE)
            .dt.tz_convert("UTC").dt.tz_localize(None)
        )

    registry = _load_eligible_history()
    key = f"{front_raw}|{back_raw}|{int(put_strike)}|{int(call_strike)}"
    prev = registry.get(key, {})

    first_seen_ts = crossings["timestamp"].min()
    last_seen_ts  = crossings["timestamp"].max()
    if prev.get("first_seen"):
        first_seen_ts = min(first_seen_ts, pd.Timestamp(prev["first_seen"]))
    if prev.get("last_seen"):
        last_seen_ts = max(last_seen_ts, pd.Timestamp(prev["last_seen"]))

    registry[key] = dict(
        front_raw=front_raw, back_raw=back_raw,
        put_strike=int(put_strike), call_strike=int(call_strike),
        iv_ratio=prev.get("iv_ratio"),
        first_seen=str(first_seen_ts),
        last_seen=str(last_seen_ts),
        last_gap=float(crossings["gap"].iloc[-1]),
        max_gap=max(prev.get("max_gap", 0.0), float(crossings["gap"].max())),
        # Don't double-count if the automated sweep already had this combo —
        # approximate by taking whichever count is larger rather than summing.
        hit_count=max(prev.get("hit_count", 0), len(crossings)),
    )
    _save_eligible_history(registry)


# ─────────────────────────────────────────────────────────────────────────────
# Helper — unicode sparkline
# ─────────────────────────────────────────────────────────────────────────────

# _sparkline, _fmt_duration and _fmt_eta moved to core/format.py;
# _banded_ratio_traces moved to core/charts.py (ADR-032).


# ─────────────────────────────────────────────────────────────────────────────
# Helper — ATM IV history
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# The nine reads themselves are dataaccess/queries.py. What stays here is the
# memo and nothing else (ADR-033).
#
# Each wrapper below does three things and no more: apply @st.cache_data, supply
# config.DB_PATH, and carry the snapshot_id that keys the cache. A new snapshot
# is the ONLY thing that changes any of these results, so within a snapshot
# window every rerun — tab click, widget change, autorefresh tick — is a cache
# hit rather than a fresh query and DataFrame rebuild. st.cache_data returns a
# COPY on each call, so downstream code can safely mutate the frames it gets.
#
# The snapshot_id arguments never reach the query: they were always cache keys
# alone, which is why they do not appear in dataaccess/queries.py.

@st.cache_data(ttl=55, show_spinner=False, max_entries=48)
def _load_atm_hist(expiry: str, days: int) -> pd.DataFrame:
    return queries.load_atm_hist(config.DB_PATH, expiry, days)


@st.cache_data(ttl=55, show_spinner=False, max_entries=48)
def _load_atm_hist_fb(expiry: str, days: int) -> pd.DataFrame:
    # load= hands the fallback the MEMOISED loader above, so its second read
    # reuses saved results instead of querying again. See dataaccess/queries.py.
    return queries.load_atm_hist_fb(config.DB_PATH, expiry, days,
                                     load=_load_atm_hist)


@st.cache_data(ttl=55, show_spinner=False, max_entries=48)
def _load_contract_hist(expiry: str, strike: float,
                         side: str, days: int) -> pd.DataFrame:
    return queries.load_contract_hist(config.DB_PATH, expiry, strike, side, days)


@st.cache_data(ttl=120, show_spinner=False, max_entries=3)
def _load_chain_df(snapshot_id: int) -> pd.DataFrame:
    return queries.load_chain_df(config.DB_PATH, snapshot_id)


@st.cache_data(ttl=120, show_spinner=False, max_entries=3)
def _load_spx_intraday(session_date: str, snapshot_id: int) -> pd.DataFrame:
    return queries.load_spx_intraday(config.DB_PATH, session_date)


@st.cache_data(ttl=300, show_spinner=False, max_entries=3)
def _load_prior_close(session_date: str) -> "float | None":
    return queries.load_prior_close(config.DB_PATH, session_date)


@st.cache_data(ttl=55, show_spinner=False, max_entries=32)
def _load_transform_marks(front: str, back: str, call_s: float, put_s: float,
                           days: int, snapshot_id: int) -> pd.DataFrame:
    return queries.load_transform_marks(config.DB_PATH, front, back,
                                         call_s, put_s, days=days)


@st.cache_data(ttl=55, show_spinner=False, max_entries=32)
def _load_latest_atm_iv(exp_date: str, snapshot_id: int, n: int = 2) -> list:
    return queries.load_latest_atm_iv(config.DB_PATH, exp_date, n)


@st.cache_data(ttl=55, show_spinner=False, max_entries=32)
def _load_diagonal_hist(front: str, back: str, call_s: float, put_s: float,
                         days: int, snapshot_id: int) -> pd.DataFrame:
    return queries.load_diagonal_hist(config.DB_PATH, front, back,
                                       call_s, put_s, days=days)


# ─────────────────────────────────────────────────────────────────────────────
# The scanner itself is core/scanner.py. The memo stays HERE, because core/
# cannot import streamlit — and it is load-bearing: this wrapper's saved
# results are shared between the Scanner tab and the 21-offset Phase A sweep,
# so every sweep after the first is nearly free. _scan_all_offsets is handed
# this wrapper explicitly (see _compute_mc_core); left to its default it would
# call the uncached function and recompute all 21 offsets on every rerun.
_compute_transform_scanner = st.cache_data(
    ttl=120, show_spinner=False, max_entries=8
)(_compute_transform_scanner_pure)


# ═══════════════════════════════════════════════════════════════════════════════
# MISSION CONTROL — cross-sectional opportunity discovery
#
# Two-phase design, kept cheap on purpose:
#   Phase A (every refresh, every offset, all expiry pairs) — pure in-memory
#     pandas against the chain already loaded. Classifies every combo as
#     Eligible (gap >= 5), Approaching (gap in [_APPROACHING_LOW, 5)), or
#     neither. This is the only part that touches "thousands of rows."
#   Phase B (every refresh, but ONLY for the small Eligible+Approaching set —
#     typically tens of rows, capped at _MC_HISTORY_CAP) — pulls per-combo
#     history via db.get_transform_mark_history() to compute how long a gap
#     has been active and whether it's trending toward the threshold.
# Running Phase B against all combos would be the wrong trade — this keeps
# cost proportional to "things that matter," not "things that exist."
# ═══════════════════════════════════════════════════════════════════════════════

# The Phase A thresholds (_TSCAN_THRESHOLD, _APPROACHING_LOW, _SWEEP_OFFSETS)
# and _scan_all_offsets itself moved to core/scanner.py. _MC_HISTORY_CAP stays
# here: it caps how much DATABASE work Phase B does, which is not a core/
# concern and moves with the data layer.
_MC_HISTORY_CAP   = 20    # max candidates per tier to run Phase B history on


@st.cache_data(ttl=60, show_spinner=False, max_entries=64)
def _candidate_signals(front_raw: str, back_raw: str,
                        put_strike: float, call_strike: float,
                        days: int = 1, *, db_path=None) -> dict | None:
    """
    Phase B — for ONE candidate combo, compute:
      duration   — how long the gap has stayed continuously >= 5, ending now
                   (None if not currently eligible)
      eta_minutes — linear projection of minutes until gap crosses 5,
                   based on the slope of the last few snapshots
                   (None if flat/declining — no point showing a bogus ETA)
      spark      — unicode sparkline of the recent gap trajectory
      trend_up   — whether the last 3 readings are monotonically increasing
    Returns None if there isn't enough history to say anything useful.

    db_path — DEBT-027, fixed in M2 step 2.2 (ADR-033). This used to read
    config.DB_PATH directly, so no caller could aim it at another database and
    every test had to overwrite that global to get near it. It defaults to the
    global, so production is unchanged and existing callers need no edit.
    """
    rows = db.get_transform_mark_history(
        db_path if db_path is not None else config.DB_PATH,
        front_raw, back_raw, call_strike, put_strike, days=days,
    )
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["timestamp"] = (
        pd.to_datetime(df["snapshot_timestamp"], format="ISO8601", utc=True)
        .dt.tz_convert(config.DISPLAY_TIMEZONE)
        .dt.tz_localize(None)  # naive wall-clock: required by Plotly rangebreaks
    )
    df["diagonal_mark"] = (
        df["back_call_mark"] + df["back_put_mark"]
        - df["front_call_mark"] - df["front_put_mark"]
    )
    df["transform_mark"] = (
        df["back_call_mark"] + df["back_put_mark"]
        - df["front_wing_call_mark"] - df["front_wing_put_mark"]
    )
    df["gap"] = df["transform_mark"] - df["diagonal_mark"]
    df = df.sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        return None

    # Duration active — trailing contiguous streak where gap >= 5, ending now
    flag = (df["gap"] >= _TSCAN_THRESHOLD).tolist()
    duration = None
    if flag and flag[-1]:
        i = len(flag) - 1
        while i > 0 and flag[i - 1]:
            i -= 1
        duration = df["timestamp"].iloc[-1] - df["timestamp"].iloc[i]

    # ETA — slope of the last up-to-6 readings, projected to threshold
    eta_minutes = None
    tail = df.tail(6).dropna(subset=["gap"])
    tail = tail.drop_duplicates(subset=["timestamp"])
    if len(tail) >= 3:
        x_min = ((tail["timestamp"] - tail["timestamp"].iloc[0])
                 .dt.total_seconds() / 60.0).to_numpy()
        y_gap = tail["gap"].to_numpy()
        # polyfit needs at least 2 distinct x values and finite data, or
        # the underlying SVD can fail to converge (degenerate design matrix).
        if (
            np.isfinite(x_min).all() and np.isfinite(y_gap).all()
            and np.ptp(x_min) > 0
        ):
            try:
                slope, _ = np.polyfit(x_min, y_gap, 1)
            except np.linalg.LinAlgError:
                slope = None
            if slope is not None:
                current_gap = float(y_gap[-1])
                if slope > 0.01 and current_gap < _TSCAN_THRESHOLD:
                    eta_minutes = (_TSCAN_THRESHOLD - current_gap) / slope

    spark = _sparkline(df["gap"].tail(12).tolist())
    trend_up = bool(df["gap"].tail(3).is_monotonic_increasing) if len(df) >= 3 else False

    return dict(duration=duration, eta_minutes=eta_minutes, spark=spark, trend_up=trend_up)


# _rank_for_panel and _card_key moved to core/ranking.py (ADR-032).


@st.cache_data(ttl=120, show_spinner="Scanning transform opportunities…", max_entries=3)
def _compute_mc_core(_chain_df: pd.DataFrame, spx_price: float,
                      snapshot_id: int, snapshot_ts: str) -> dict:
    """
    Phase A + Phase B, cached as a unit on snapshot_id. No session_state
    access here — cached functions skip their body entirely on a cache hit,
    so any session_state writes would silently stop happening the moment
    this starts returning cached results. The "New" diff (which needs
    session_state) is handled by the uncached _run_mission_control wrapper
    below, outside this cache boundary.

    Also updates the persisted eligibility registry (eligible_history.json)
    for every non-ATM combo currently >= threshold — a plain file write is
    fine inside a cached function (unlike session_state), and since the
    function body only runs on a cache miss, this naturally fires once per
    NEW snapshot rather than on every rerun.
    """
    chain_df = _chain_df
    # compute= is not optional here in production: it hands the sweep the
    # memoised scanner so the 21 offsets share saved results with the Scanner
    # tab. Without it every rerun recomputes all 21. See core/scanner.py.
    all_combos = _scan_all_offsets(chain_df, spx_price, snapshot_id,
                                   compute=_compute_transform_scanner)
    if all_combos.empty:
        return dict(approaching_cards=[], likely_next=[], n_approaching=0,
                     non_atm_current=pd.DataFrame(), registry={})

    non_atm_current = all_combos[all_combos["Put Strike"] != all_combos["Call Strike"]].copy()
    registry = _update_eligible_history(non_atm_current, snapshot_ts)

    approaching_df = all_combos[
        (all_combos["Transform Diff"] >= _APPROACHING_LOW)
        & (all_combos["Transform Diff"] < _TSCAN_THRESHOLD)
    ].copy()
    n_approaching = len(approaching_df)

    # Rank for the panel BEFORE capping — otherwise asymmetric opportunities
    # sitting just below the top-by-raw-gap rows would get starved out of
    # the (necessarily limited, for cost reasons) Phase B history treatment.
    approaching_df = _rank_for_panel(approaching_df)

    def _build_cards(df: pd.DataFrame, cap: int) -> list[dict]:
        cards = []
        for _, row in df.head(cap).iterrows():
            front_raw = row["Front Expiry"].split(" ")[0]
            back_raw  = row["Back Expiry"].split(" ")[0]
            put_s     = float(row["Put Strike"])
            call_s    = float(row["Call Strike"])
            sig = _candidate_signals(front_raw, back_raw, put_s, call_s) or {}
            cards.append(dict(
                front_raw=front_raw, back_raw=back_raw,
                front_label=row["Front Expiry"], back_label=row["Back Expiry"],
                put_strike=put_s, call_strike=call_s,
                gap=float(row["Transform Diff"]),
                iv_ratio=row.get("IV Ratio"),
                duration=sig.get("duration"),
                eta_minutes=sig.get("eta_minutes"),
                spark=sig.get("spark", "─"),
                trend_up=sig.get("trend_up", False),
            ))
        return cards

    approaching_cards = _build_cards(approaching_df, _MC_HISTORY_CAP)

    # "Likely Next" — only candidates with a computable rising-trend ETA.
    # Same asymmetric-first principle, ETA ascending within each tier.
    likely_next = sorted(
        [c for c in approaching_cards if c["eta_minutes"] is not None],
        key=lambda c: (c["put_strike"] == c["call_strike"], c["eta_minutes"]),
    )

    return dict(
        approaching_cards=approaching_cards,
        likely_next=likely_next,
        n_approaching=n_approaching,
        non_atm_current=non_atm_current,
        registry=registry,
    )


def _build_non_atm_panel(non_atm_current: pd.DataFrame, registry: dict,
                          dte_by_expiry: dict, lookback_days: int,
                          snapshot_ts: str, cap: int = _MC_HISTORY_CAP,
                          min_display: int = 6):
    """
    The curated "non-ATM opportunities" panel — built from the persisted
    registry, NOT a slice of the Scanner. Includes any combo that is
    currently >= threshold OR appears in the registry within the lookback
    window (i.e. crossed >= 5 at some point recently, even if it isn't
    right now).

    Ranking — a transparent, inspectable multi-key sort, not a blended
    score (same principle as _rank_for_panel above):
      Tier 1 — currently live (>= 5 right now) outranks historical-only;
               an opportunity you can act on today beats a past one.
      Tier 2 — rank_gap descending: current gap for live combos, peak gap
               (max_gap ever observed) for historical-only ones.
      Tier 3 — hit_count descending — directly answers "which strikes
               repeatedly become transformable," not just "which spiked once."
      Tier 4 — most recent crossing first, as the final tiebreak.

    Never-empty guarantee: if fewer than min_display combos fall within the
    selected lookback window, the remaining slots are filled with the most
    recent registry entries regardless of window — flagged via
    outside_lookback=True so the UI can make that explicit rather than
    silently showing stale data as if it were in-range. Only true cold start
    (an empty registry — nothing has ever crossed threshold, automated or
    backfilled) can still produce an empty panel.

    Returns (capped_cards, in_window_total, fallback_used_count).
    """
    try:
        cutoff = pd.Timestamp(snapshot_ts) - pd.Timedelta(days=lookback_days)
    except (ValueError, TypeError):
        cutoff = None

    current_lookup: dict[str, dict] = {}
    if not non_atm_current.empty:
        for _, row in non_atm_current.iterrows():
            front_raw = row["Front Expiry"].split(" ")[0]
            back_raw  = row["Back Expiry"].split(" ")[0]
            k = f"{front_raw}|{back_raw}|{int(row['Put Strike'])}|{int(row['Call Strike'])}"
            current_lookup[k] = dict(
                gap=float(row["Transform Diff"]), iv_ratio=row.get("IV Ratio"),
            )

    def _card_from_entry(key: str, entry: dict, last_seen_ts: pd.Timestamp,
                          outside_lookback: bool) -> dict:
        cur = current_lookup.get(key)
        is_live  = cur is not None and cur["gap"] >= _TSCAN_THRESHOLD
        rank_gap = cur["gap"] if is_live else entry["max_gap"]
        front_raw, back_raw = entry["front_raw"], entry["back_raw"]
        # One table, checked and looked up. Before ADR-034 the guard read the
        # parameter and _exp_label read a global of the same name.
        front_label = (_exp_label(front_raw, dte_by_expiry)
                       if front_raw in dte_by_expiry else front_raw)
        back_label  = (_exp_label(back_raw, dte_by_expiry)
                       if back_raw in dte_by_expiry else back_raw)
        try:
            ago_str = _fmt_duration(pd.Timestamp(snapshot_ts) - last_seen_ts) + " ago"
        except (ValueError, TypeError):
            ago_str = "—"
        return dict(
            front_raw=front_raw, back_raw=back_raw,
            front_label=front_label, back_label=back_label,
            put_strike=entry["put_strike"], call_strike=entry["call_strike"],
            iv_ratio=(cur["iv_ratio"] if cur else entry.get("iv_ratio")),
            is_live=is_live,
            current_gap=(cur["gap"] if cur else None),
            max_gap=entry["max_gap"],
            gap=rank_gap,
            hit_count=entry["hit_count"],
            last_seen=last_seen_ts,
            last_seen_ago=ago_str,
            outside_lookback=outside_lookback,
        )

    candidates, used_keys = [], set()
    for key, entry in registry.items():
        try:
            last_seen_ts = pd.Timestamp(entry["last_seen"])
        except (ValueError, TypeError, KeyError):
            continue
        if cutoff is not None and last_seen_ts < cutoff:
            continue
        candidates.append(_card_from_entry(key, entry, last_seen_ts, outside_lookback=False))
        used_keys.add(key)

    candidates.sort(key=lambda c: (
        not c["is_live"], -c["gap"], -c["hit_count"], -c["last_seen"].timestamp()
    ))
    in_window_total = len(candidates)

    # Never-empty fallback: pull in the most recent entries from OUTSIDE the
    # selected window, clearly flagged, rather than show nothing.
    fallback_used = 0
    if len(candidates) < min_display:
        fallback_raw = []
        for key, entry in registry.items():
            if key in used_keys:
                continue
            try:
                last_seen_ts = pd.Timestamp(entry["last_seen"])
            except (ValueError, TypeError, KeyError):
                continue
            fallback_raw.append((key, entry, last_seen_ts))
        fallback_raw.sort(key=lambda t: t[2].timestamp(), reverse=True)

        needed = max(cap - len(candidates), min_display - len(candidates))
        for key, entry, last_seen_ts in fallback_raw[:needed]:
            candidates.append(_card_from_entry(key, entry, last_seen_ts, outside_lookback=True))
            fallback_used += 1

    capped = candidates[:cap]

    # Phase B (duration/spark/eta) only for the small final, capped set —
    # _candidate_signals is independently cached so repeat calls are cheap.
    for c in capped:
        sig = _candidate_signals(c["front_raw"], c["back_raw"],
                                  c["put_strike"], c["call_strike"]) or {}
        c["duration"]    = sig.get("duration") if c["is_live"] else None
        c["eta_minutes"] = sig.get("eta_minutes")
        c["spark"]       = sig.get("spark", "─")
        c["trend_up"]    = sig.get("trend_up", False)

    return capped, in_window_total, fallback_used


def _run_mission_control(chain_df: pd.DataFrame, spx_price: float,
                          snapshot_id: int, snapshot_ts: str,
                          dte_by_expiry: dict, lookback_days: int) -> dict:
    """
    Thin, UNCACHED wrapper around _compute_mc_core. Handles the cross-refresh
    "New" diff via session_state (which can't live inside a cached function)
    and builds the curated non-ATM panel from the registry + current state.
    On a cache hit (the common case — every tab click, every widget
    interaction within the same snapshot), the expensive Phase A/B work is
    skipped entirely; only cheap in-memory filtering/sorting runs here.
    """
    core = _compute_mc_core(chain_df, spx_price, snapshot_id, snapshot_ts)

    non_atm_panel, n_non_atm_in_window, n_fallback_used = _build_non_atm_panel(
        core["non_atm_current"], core["registry"], dte_by_expiry,
        lookback_days, snapshot_ts,
    )

    current_keys = {_card_key(c) for c in non_atm_panel if c["is_live"]}

    # Only advance the "previous" comparison set when a NEW snapshot has
    # actually landed — otherwise every widget-triggered rerun within the
    # same snapshot would keep relabeling things as "new."
    _prev_snap_id = st.session_state.get("mc_prev_snapshot_id")
    if _prev_snap_id != snapshot_id:
        _prev_keys = st.session_state.get("mc_prev_eligible_keys", set())
        new_keys = current_keys - _prev_keys
        st.session_state["mc_prev_eligible_keys"] = current_keys
        st.session_state["mc_prev_snapshot_id"]   = snapshot_id
        st.session_state["mc_new_keys"]           = new_keys
    else:
        new_keys = st.session_state.get("mc_new_keys", set())

    for c in non_atm_panel:
        c["is_new"] = c["is_live"] and (_card_key(c) in new_keys)
    for c in core["approaching_cards"]:
        c["is_new"] = False
    for c in core["likely_next"]:
        c["is_new"] = False

    n_live_non_atm = (
        int((core["non_atm_current"]["Transform Diff"] >= _TSCAN_THRESHOLD).sum())
        if not core["non_atm_current"].empty else 0
    )
    best = next((c for c in non_atm_panel if c["is_live"]),
                non_atm_panel[0] if non_atm_panel else None)

    return dict(
        non_atm=non_atm_panel,
        n_non_atm_total=n_non_atm_in_window,
        n_fallback_used=n_fallback_used,
        n_eligible=n_live_non_atm,
        approaching=core["approaching_cards"],
        likely_next=core["likely_next"],
        n_approaching=core["n_approaching"],
        new_keys=new_keys,
        n_new=len(new_keys),
        best=best,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

# The command is built from the RUNNING interpreter and this file's own location
# rather than hardcoded, so it stays correct if the project moves or the venv is
# rebuilt — the same reasoning as the %~dp0 paths in start_collector.bat (M0.13).
# It is rendered with st.code(), which gives a one-click copy button for free;
# the previous <code> block inside raw HTML could not be copied, which is the
# whole reason you end up retyping it.
def _reauth_command() -> str:
    project_dir = Path(__file__).resolve().parent
    return f'cd "{project_dir}"\n"{sys.executable}" scripts\\reauth.py'


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

# ── Live data refresh (silent, change-triggered) ─────────────────────────────
# Previously st_autorefresh forced a FULL-PAGE rerun every poll_interval, which
# reset charts (losing zoom/pan) and interrupted analysis even when NO new data
# had arrived. Instead, a background fragment polls for a new COMPLETE snapshot
# on a short timer and reruns the app ONLY when the snapshot_id actually changes.
# While the snapshot is unchanged — mid-analysis, or after hours when the
# collector is idle — nothing reruns, so the page stays put.
_LIVE_POLL_SECONDS = max(5, min(int(poll_interval), 20))
_fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)

if _fragment is not None:
    @_fragment(run_every=_LIVE_POLL_SECONDS)
    def _live_refresh_poller():
        # Cheap: one indexed lookup of the newest COMPLETE snapshot. Triggers a
        # full-app rerun only when new data has landed since the current render.
        try:
            _snap = db.get_latest_complete_snapshot(config.DB_PATH)
        except Exception:
            return
        _latest_id = _snap["snapshot_id"] if _snap else None
        if "_active_snapshot_id" not in st.session_state:
            # First check of a brand-new session: nothing has rendered yet,
            # so there's nothing to refresh. Adopt the latest snapshot
            # silently. (Rerunning here — before _active_snapshot_id is ever
            # set further down the script — would rerun forever and the
            # page would never render.)
            st.session_state["_active_snapshot_id"] = _latest_id
        elif (_latest_id is not None
                and _latest_id != st.session_state["_active_snapshot_id"]):
            st.rerun()

    _live_refresh_poller()
else:
    # Fallback for Streamlit builds without fragment support.
    st_autorefresh(interval=poll_interval * 1000, key="autorefresh")

st.sidebar.divider()
st.sidebar.markdown("**🔭 Transform Scanner**")

sc_max_rows = st.sidebar.number_input(
    "Max Results", min_value=10, max_value=200, value=50, step=10,
    key="sc_max_rows",
    help="Cap the number of rows returned (sorted by Transform Diff descending).",
)

st.sidebar.divider()
st.sidebar.markdown("**🎨 Chart Appearance**")

CHART_COLORS = _load_chart_colors()

with st.sidebar.expander("Line colors", expanded=False):
    _colors_changed = False
    for _key, (_label, _default) in DEFAULT_CHART_COLORS.items():
        _picked = st.color_picker(
            _label, value=CHART_COLORS.get(_key, _default), key=f"color_{_key}",
        )
        if _picked != CHART_COLORS.get(_key):
            CHART_COLORS[_key] = _picked
            _colors_changed = True
    if _colors_changed:
        _save_chart_colors(CHART_COLORS)

    if st.button("↺ Reset to Default Colors", key="reset_colors_btn",
                 use_container_width=True):
        CHART_COLORS = {k: v[1] for k, v in DEFAULT_CHART_COLORS.items()}
        _save_chart_colors(CHART_COLORS)
        for _key in DEFAULT_CHART_COLORS:
            st.session_state.pop(f"color_{_key}", None)
        st.rerun()

# ── Schwab connection ─────────────────────────────────────────────────────────
# Always available, not only once the token is nearly dead. The red banner at the
# top of the page still fires from day 6; this is here so the command can be
# copied at any time, and so "how long have I got?" has an answer that does not
# require opening a terminal to ask.
st.sidebar.divider()
st.sidebar.markdown("**🔑 Schwab Connection**")

_sb_age = schwab_client.get_token_age_days()
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
    st.code(_reauth_command(), language="powershell")
    st.caption(
        "Opens the Schwab login in your browser, then asks you to paste the "
        "redirected URL back — it will look like an error page, which is expected. "
        "Cannot be automated: Schwab requires the interactive login. Your current "
        "token is restored automatically if you cancel partway through."
    )

# ─────────────────────────────────────────────────────────────────────────────
# Database init + latest complete snapshot
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _init_db_once(_db_path: str) -> bool:
    """Ensure schema exists — but only ONCE per dashboard process.

    Previously db.init_db() ran on every script rerun (every tab click, widget
    change, and autorefresh tick). init_db() executes the full DDL script and
    COMMITS a write transaction, so the dashboard — nominally a pure reader —
    was issuing a database write on every interaction, contending with the
    collector's write lock. @st.cache_resource runs this exactly once and
    caches the result for the life of the process.
    """
    db.init_db(_db_path)
    return True


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


def _exp_label(expiry: str, dte_by_expiry: dict) -> str:
    """Pretty expiry label, e.g. "Friday, Aug 21, 2026  (23 DTE)".

    dte_by_expiry — DEBT-027 site 2, fixed in M2 (ADR-034). This used to read a
    module global of the same name while its ONLY caller checked membership
    against the parameter it had been handed. Identical objects in production,
    so it worked; the day they differed, the guard would pass and the lookup
    return nothing, dropping "(N DTE)" from the label with no error anywhere.
    """
    d = dte_by_expiry.get(expiry)
    try:
        dt = pd.Timestamp(expiry)
        pretty = dt.strftime("%A, %b ") + str(dt.day) + dt.strftime(", %Y")
    except (ValueError, TypeError):
        pretty = expiry
    return f"{pretty}  ({d} DTE)" if d is not None else pretty


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

# ─────────────────────────────────────────────────────────────────────────────
# Attention Strip — persistent, renders regardless of which tab is active
# ─────────────────────────────────────────────────────────────────────────────

if MC["best"] is not None:
    _b = MC["best"]
    if _b["is_live"]:
        _best_status = f'Active {_fmt_duration(_b["duration"])}'
    else:
        _best_status = f'Peak · seen {_b["last_seen_ago"]}'
    _attn_html = (
        '<div class="attn-strip">'
        '<div class="attn-counts">'
        '<span class="attn-count-item">'
        f'<span class="attn-count-n green">{MC["n_eligible"]}</span>'
        '<span class="attn-count-l">Eligible</span></span>'
        '<span class="attn-count-item">'
        f'<span class="attn-count-n amber">{MC["n_approaching"]}</span>'
        '<span class="attn-count-l">Approaching</span></span>'
        '<span class="attn-count-item">'
        f'<span class="attn-count-n blue">{MC["n_new"]}</span>'
        '<span class="attn-count-l">New</span></span>'
        '</div>'
        '<div class="attn-divider"></div>'
        '<div class="attn-best">'
        f'🔥 Best: <b>{int(_b["put_strike"])}P / {int(_b["call_strike"])}C</b>'
        f'&nbsp;·&nbsp;Gap <span class="gap-v">+{_b["gap"]:.2f}</span>'
        f'&nbsp;·&nbsp;{_best_status}'
        '</div>'
        '</div>'
    )
else:
    _attn_html = (
        '<div class="attn-strip">'
        '<div class="attn-counts">'
        '<span class="attn-count-item">'
        f'<span class="attn-count-n">{MC["n_eligible"]}</span>'
        '<span class="attn-count-l">Eligible</span></span>'
        '<span class="attn-count-item">'
        f'<span class="attn-count-n">{MC["n_approaching"]}</span>'
        '<span class="attn-count-l">Approaching</span></span>'
        '</div>'
        '<div class="attn-divider"></div>'
        '<span class="attn-empty">No non-ATM transform opportunities right now — '
        'scanning every refresh.</span>'
        '</div>'
    )
st.markdown(_attn_html, unsafe_allow_html=True)

# ── Token expiry warning banner ───────────────────────────────────────────────

_token_age = schwab_client.get_token_age_days()
if _token_age is not None and _token_age >= 6:
    if _token_age >= 7:
        st.markdown(
            f"""<div class="spx-token-emergency">
🚨 SCHWAB TOKEN EXPIRED {_token_age - 7:.1f} days ago — no new prices are being
collected. Paste this into a terminal to re-authenticate:
</div>""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""<div class="spx-token-warning">
⚠️ Schwab API token expires in <strong>{7 - _token_age:.1f} days</strong>.
Re-authenticate before then to avoid losing a session. Paste this into a terminal:
</div>""",
            unsafe_allow_html=True,
        )
    st.code(_reauth_command(), language="powershell")
    st.caption(
        "Opens a Schwab login in your browser, then asks you to paste the redirected "
        "URL back. The page will look like an error — that is expected. Your current "
        "token is restored automatically if you cancel partway through."
    )

# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENT CONTROLS BAR — front/back expiry + put/call strike
# Always visible above the tabs so every section can access these values.
# ═══════════════════════════════════════════════════════════════════════════════

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

# _exp_label takes the expiry table as an argument (ADR-034), and Streamlit
# calls format_func with the option alone — so the table is bound here rather
# than reached for inside the function.
def _expiry_option_label(expiry: str) -> str:
    return _exp_label(expiry, dte_by_expiry)


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


def _nearest_idx(strikes: list, target: float) -> int:
    if not strikes:
        return 0
    return min(range(len(strikes)), key=lambda i: abs(strikes[i] - target))


with c3:
    if _put_strikes:
        _ps_kwargs = (
            {} if "put_strike_select" in st.session_state
            else {"index": _nearest_idx(_put_strikes, spx_price - 100)}
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
            else {"index": _nearest_idx(_call_strikes, spx_price)}
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


# ─────────────────────────────────────────────────────────────────────────────
# Mission Control card renderer — used by the Scanner section below
# ─────────────────────────────────────────────────────────────────────────────

def _render_mc_section(cards: list[dict], section: str, title: str, icon: str,
                        show_duration: bool = True,
                        show_live_badge: bool = False) -> None:
    if not cards:
        return
    st.markdown(
        f'<div class="sh" style="margin-top:.3rem">'
        f'<span class="sh-ico">{icon}</span>'
        f'<span class="sh-ttl">{title}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    n_cols = 3
    for row_start in range(0, len(cards), n_cols):
        row_cards = cards[row_start:row_start + n_cols]
        cols = st.columns(n_cols)
        for c_idx, (card, col) in enumerate(zip(row_cards, cols)):
            gidx = row_start + c_idx
            with col:
                with st.container(key=f"mc_card_{section}_{gidx}"):
                    _new_badge = '<span class="mc-new-badge">NEW</span>' if card["is_new"] else ""
                    _is_live = card.get("is_live", True)
                    if show_live_badge:
                        if _is_live:
                            _live_badge = '<span class="mc-live-badge">LIVE</span>'
                        elif card.get("outside_lookback"):
                            _live_badge = '<span class="mc-stale-badge">OUTSIDE WINDOW</span>'
                        else:
                            _live_badge = '<span class="mc-stale-badge">PAST</span>'
                    else:
                        _live_badge = ""
                    _gap_label = "Gap" if _is_live else "Peak Gap"
                    _gap_class = "gap" if _is_live else "gap gap-stale"
                    # The trend arrow used to ride on the end of the sparkline.
                    # That was removed 2026-07-30 as clutter; the arrow moved
                    # here, onto the number it actually describes.
                    _trend_arrow = " ↑" if card["trend_up"] else ""
                    _metrics = (
                        f'<div><span class="mc-metric-l">{_gap_label}</span>'
                        f'<span class="mc-metric-v {_gap_class}">'
                        f'+{card["gap"]:.2f}{_trend_arrow}</span></div>'
                    )
                    if show_duration and _is_live:
                        _metrics += (
                            f'<div><span class="mc-metric-l">Active</span>'
                            f'<span class="mc-metric-v">{_fmt_duration(card["duration"])}</span></div>'
                        )
                    elif show_live_badge and not _is_live:
                        _metrics += (
                            f'<div><span class="mc-metric-l">Last Crossed</span>'
                            f'<span class="mc-metric-v">{card.get("last_seen_ago", "—")}</span></div>'
                        )
                    if show_live_badge and card.get("hit_count") is not None:
                        _metrics += (
                            f'<div><span class="mc-metric-l">Seen</span>'
                            f'<span class="mc-metric-v">{card["hit_count"]}×</span></div>'
                        )
                    if card["iv_ratio"] is not None:
                        _metrics += (
                            f'<div><span class="mc-metric-l">IV Ratio</span>'
                            f'<span class="mc-metric-v">{card["iv_ratio"]:.4f}</span></div>'
                        )
                    _eta_html = (
                        f'<div class="mc-eta">ETA {_fmt_eta(card["eta_minutes"])}</div>'
                        if card.get("eta_minutes") is not None else ""
                    )
                    # No sparkline row: the gap's shape over time is what "View
                    # Chart" opens, drawn properly and at a readable size, so a
                    # 10-glyph version on every card was clutter rather than
                    # information. Removed 2026-07-30 at Chandan's request.
                    st.markdown(
                        f'<div class="mc-rank">#{gidx + 1}{_live_badge}{_new_badge}</div>'
                        f'<div class="mc-combo">{int(card["put_strike"])}P / {int(card["call_strike"])}C</div>'
                        f'<div class="mc-expiry">{card["front_label"]} → {card["back_label"]}</div>'
                        f'<div class="mc-metrics">{_metrics}</div>'
                        f'{_eta_html}',
                        unsafe_allow_html=True,
                    )
                    st.markdown('<div class="mc-actions-spacer"></div>', unsafe_allow_html=True)
                    bcol1, bcol2 = st.columns(2)
                    with bcol1:
                        if st.button("View Chart", key=f"viewchart_{section}_{gidx}",
                                     use_container_width=True):
                            # Can't write directly to *_select keys here — those
                            # widgets already rendered earlier in this script run
                            # (Controls Bar comes before Scanner). Stage under
                            # pending_ keys instead; they get promoted to the real
                            # widget keys at the top of the NEXT run, before the
                            # Controls Bar widgets are instantiated.
                            st.session_state["pending_front_expiry"] = card["front_raw"]
                            st.session_state["pending_back_expiry"]  = card["back_raw"]
                            st.session_state["pending_put_strike"]   = card["put_strike"]
                            st.session_state["pending_call_strike"]  = card["call_strike"]
                            st.session_state["active_tab"] = "edge"
                            st.rerun()
                    with bcol2:
                        if st.button("📓 Journal", key=f"journal_{section}_{gidx}",
                                     use_container_width=True):
                            # Deep-link contract for pages/journal.py — that page
                            # should read st.session_state.get("journal_prefill")
                            # on load and pre-populate a new IC-transform entry.
                            st.session_state["journal_prefill"] = dict(
                                type="transform",
                                front_expiry=card["front_raw"], back_expiry=card["back_raw"],
                                put_strike=card["put_strike"], call_strike=card["call_strike"],
                                transform_gap=card["gap"], iv_ratio=card["iv_ratio"],
                                spx_price=spx_price, timestamp=snap_ts_str,
                            )
                            try:
                                st.switch_page("pages/journal.py")
                            except Exception:
                                st.info(
                                    "Trade details staged — open Journal from the "
                                    "sidebar to continue."
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
    chain_df=chain_df,
    front_expiry=front_expiry,
    back_expiry=back_expiry,
    front_dte=front_dte,
    back_dte=back_dte,
    call_strike=call_strike,
    put_strike=put_strike,
    strikes_set=strikes_set,
    ts_now=ts_now,
    diag_mark=_diag_mark,
    norm_deb=_norm_deb,
    straddle=_straddle,
    theta_diff=_theta_diff,
    ic_mark=_ic_mark,
    iv_pct=_iv_pct,
    liquidity=_liquidity,
    load_atm_hist_fb=_load_atm_hist_fb,
    load_diagonal_hist=_load_diagonal_hist,
    load_latest_atm_iv=_load_latest_atm_iv,
    load_contract_hist=_load_contract_hist,
    load_transform_marks=_load_transform_marks,
    compute_transform_scanner=_compute_transform_scanner,
    mc=MC,
    sc_max_rows=sc_max_rows,
    render_mc_section=_render_mc_section,
    entry_lock_key=_entry_lock_key,
    create_entry_lock=_create_entry_lock,
    clear_entry_lock=_clear_entry_lock,
    get_entry_lock=_get_entry_lock,
    render_all_locks_popover=_render_all_locks_popover,
    backfill_eligible_history=_backfill_eligible_history,
    chart_colors=CHART_COLORS,
)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB NAVIGATION — custom nav bar (not st.tabs) so Mission Control cards can
# jump straight to a pre-scoped tab programmatically. st.tabs() has no API
# for switching the active tab from code; this swaps in a session_state-driven
# button row instead, styled via CSS to look identical to the original tabs.
# ═══════════════════════════════════════════════════════════════════════════════

_TABS = [
    ("scanner",  "🔭  Scanner"),
    ("entry",    "📊  Entry Analysis"),
    ("edge",     "📈  Calendar Edge"),
    ("strike",   "🎯  Strike Detail"),
    ("hist",     "📉  Historical Stats"),
    ("research", "🔬  Research"),
]

if "active_tab" not in st.session_state:
    st.session_state["active_tab"] = "scanner"

with st.container(key="topnav"):
    _nav_cols = st.columns(len(_TABS))
    for (_tkey, _tlabel), _tcol in zip(_TABS, _nav_cols):
        with _tcol:
            _is_active = st.session_state["active_tab"] == _tkey
            if st.button(
                _tlabel, key=f"nav_{_tkey}", use_container_width=True,
                type="primary" if _is_active else "secondary",
            ):
                st.session_state["active_tab"] = _tkey
                st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# SCANNER — Mission Control + full opportunity table
# ═══════════════════════════════════════════════════════════════════════════════

if st.session_state["active_tab"] == "scanner":
    view_scanner.render(VIEW_CTX)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ENTRY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

if st.session_state["active_tab"] == "entry":
    view_entry.render(VIEW_CTX)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — CALENDAR EDGE
# ═══════════════════════════════════════════════════════════════════════════════

if st.session_state["active_tab"] == "edge":
    view_edge.render(VIEW_CTX)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB — STRIKE DETAIL
# Own period selector independent of Calendar Edge.
# ═══════════════════════════════════════════════════════════════════════════════

if st.session_state["active_tab"] == "strike":
    view_strike.render(VIEW_CTX)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — HISTORICAL STATISTICS
# ═══════════════════════════════════════════════════════════════════════════════

if st.session_state["active_tab"] == "hist":
    view_historical.render(VIEW_CTX)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — RESEARCH
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state["active_tab"] == "research":
    view_research.render(VIEW_CTX)
