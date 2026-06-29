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
    page_title="SPX Diagonal Calendar Analyzer",
    layout="wide",
)

# ─── v3 compaction: tighter top padding + section rhythm (not crushed) ─────────
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
      div[data-testid="stVerticalBlock"] { gap: 0.6rem; }
      hr { margin: 0.4rem 0; opacity: 0.18; }
      div[data-testid="stMetricLabel"] { opacity: 0.78; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─── Constants ────────────────────────────────────────────────────────────────
PINNED_PAIRS_FILE = Path(__file__).parent / "pinned_pairs.json"
_SPARK_BARS = "▁▂▃▄▅▆▇█"
_TABLE_DISPLAY_COLS = ["Front", "Back", "Ratio", "Day Chg", "Drop%", "Rise%", "Chart"]

# Collapse non-trading time on multi-day time-series charts so sessions sit
# adjacent and the line stays continuous across the (now-removed) empty bands.
# Bounds are in DISPLAY_TIMEZONE (America/New_York), so they are DST-safe.
#   - weekends:  Sat 00:00 → Mon 00:00
#   - overnight: 16:00 → 09:30 ET each trading day
#   - holidays:  full-day closures from config.MARKET_HOLIDAYS
# With all expected gaps collapsed, the across-session connector is a short,
# continuous segment rather than a long diagonal ramp.
_SESSION_RANGEBREAKS = [
    dict(bounds=["sat", "mon"]),
    dict(bounds=[16, 9.5], pattern="hour"),
    dict(values=sorted(config.MARKET_HOLIDAYS)),
]

# Regime bands for the IV Ratio (F/B). Colors are regime LABELS, not validated
# favorability — green for ≥1 is requested visual shorthand for "backwardation",
# not "enter". The <0.70 amber band is mostly the 0DTE end-of-day artifact, so it
# reads as a caution zone, not a tradeable signal. Thresholds: 0.70 / 1.00 / 1.30.
_RATIO_THRESHOLDS = [0.70, 1.00, 1.30]
_RATIO_BANDS = [
    # (low, high, color, label)
    (1.30, float("inf"), "#1abc9c", "Strong backwardation (≥1.30)"),
    (1.00, 1.30,         "#2ecc71", "Backwardation 1.00–1.30 (front rich)"),
    (0.70, 1.00,         "#8e9bb5", "Contango 0.70–1.00 (normal)"),
    (float("-inf"), 0.70, "#d98841", "Deep contango <0.70 (likely 0DTE/EOD)"),
]


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

def _banded_ratio_traces(x, y) -> list:
    """Build a *continuous* multicolor line for the IV ratio, colored by regime
    band. Where the series crosses a threshold (0.70 / 1.00 / 1.30) the exact
    crossing point is interpolated and inserted, then each band emits one trace
    that is non-None only inside its band — but boundary points belong to BOTH
    adjacent bands (inclusive), so the colored segments touch and the line has
    no gaps at crossings.
    """
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
    """
    Load ATM IV history with weekend/gap fallback.
    When days==1 returns empty (no intraday data — weekend, holiday, pre-open)
    retry with 5 days and trim to the most recent session date, so
    Saturday/Sunday always shows Friday's last data.
    """
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
        # Weekend / holiday fallback: retry with 5 days and trim to last session
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

    Nearest-strike resolution is used for the main legs so the scan always
    finds something close to the requested offsets.  Wing strikes (±5 from
    the resolved actual strike) require exact matches — same rule as Entry
    Analysis.  The table shows ACTUAL resolved strikes, never target offsets.
    """
    import bisect

    if chain_df.empty:
        return pd.DataFrame()

    expiries   = sorted(chain_df["expiry"].unique())
    dte_by_exp = chain_df.groupby("expiry")["dte"].first().to_dict()

    # ── Build per-(expiry, side) sorted strike + mark lists (built once) ──
    # Structure: {(expiry, side): ([sorted_strikes], [marks])}
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
        """Return mark if the exact strike exists, else None."""
        key = (expiry, side)
        if key not in _cache:
            return None
        strikes, marks = _cache[key]
        idx = bisect.bisect_left(strikes, target)
        if idx < len(strikes) and strikes[idx] == target:
            return marks[idx]
        return None

    def _nearest_common(exp1: str, exp2: str, target: float, side: str):
        """
        Find the strike nearest to target that exists in BOTH expiries.
        Returns (actual_strike, mark_exp1, mark_exp2) or (None, None, None).
        Using the intersection guarantees both legs of the diagonal can be
        filled at the same strike.
        """
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

    # ── ATM IV per expiry (for IV Ratio column) ───────────────────────────
    atm_iv_cache: dict[str, float | None] = {
        exp: iv_engine.atm_iv(chain_df, exp, spx_price)
        for exp in expiries
    }

    # ── Target strikes based on requested offsets ─────────────────────────
    atm_rounded  = round(spx_price / 5) * 5
    target_put   = float(atm_rounded - put_offset)
    target_call  = float(atm_rounded + call_offset)

    # ── Main loop — one row per expiry pair ───────────────────────────────
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

            # Resolve nearest common put and call strikes
            put_s,  fp, bp = _nearest_common(front, back, target_put,  "PUT")
            call_s, fc, bc = _nearest_common(front, back, target_call, "CALL")

            if any(v is None for v in (put_s, call_s, fp, bp, fc, bc)):
                continue

            diag_mark = (bc + bp) - (fc + fp)

            # Wing strikes: exact match on front expiry (same as Entry Analysis)
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

# Detect the collector's high-frequency session windows (both 60s):
#   OPEN  → 9:30–10:00 AM ET
#   CLOSE → 3:30–4:00  PM ET
# Dashboard auto-matches these so it never lags behind the collector.
# Event Mode overrides everything.
_now_et = pd.Timestamp.now(tz="America/New_York")
_t = _now_et.time()
_open_session  = dt_time(9, 30) <= _t < dt_time(10, 0)
_close_session = dt_time(15, 30) <= _t < dt_time(16, 0)

if event_mode:
    poll_interval = config.POLL_INTERVAL_EVENT   # 60s — manual override
    poll_label    = "60s ⚡ Event Mode"
    st.sidebar.caption("⚡ Event Mode active — refreshing every 60s.")
elif _open_session:
    poll_interval = config.POLL_INTERVAL_EVENT   # 60s — matches collector OPEN session
    poll_label    = "60s (OPEN session)"
    st.sidebar.caption("📈 OPEN session — auto-matched to collector (60s).")
elif _close_session:
    poll_interval = config.POLL_INTERVAL_EVENT   # 60s — matches collector CLOSE session
    poll_label    = "60s (CLOSE session)"
    st.sidebar.caption("📉 CLOSE session — auto-matched to collector (60s).")
else:
    poll_interval = config.POLL_INTERVAL_NORMAL  # 300s
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

# Map expiry → DTE for dropdown labels, e.g. "2026-06-29  (3D)"
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

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — HEADER
# Left column: SPX price + daily change (static: ▲ +64.0 (0.87%))
# ═════════════════════════════════════════════════════════════════════════════

sign = "+" if daily_chg_pts >= 0 else ""
chg_display = f"{sign}{daily_chg_pts:.1f} ({sign}{daily_chg_pct:.2f}%)"

# staleness dot retained for h_status display only (no text label)
if snap_age_secs < 600:
    _stale_dot = "🟢"
elif snap_age_secs < 3600:
    _stale_dot = "🟡"
else:
    _stale_dot = "🔴"

h_spx, h_vix, h_gex, h_status = st.columns([5, 2, 2, 4])

with h_spx:
    # SPX price + static combined change display (▲ +64.0 (0.87%))
    st.markdown(
        f"<h2 style='margin:0;padding:0;color:{day_color};line-height:1.1;'>"
        f"SPX {spx_price:,.2f} "
        f"<span style='font-size:0.65em;font-weight:400;'>"
        f"{day_arrow} {chg_display}</span></h2>",
        unsafe_allow_html=True,
    )

with h_vix:
    vix_str = f"{vix_value:.2f}" if vix_value else "N/A"
    st.metric("VIX", vix_str)

with h_gex:
    st.metric("Max |GEX| Strike", gex_label)

with h_status:
    st.caption(
        f"{_stale_dot}  {snap_ts_str} UTC  |  "
        f"Refresh: {poll_label}"
    )
    # secs_remaining is anchored to the collector's last DB write (snap_age_secs),
    # not the Streamlit session.  Opening/closing/refreshing the browser never
    # resets the clock — only a new collector snapshot does.
    secs_remaining = max(0, int(poll_interval - snap_age_secs))
    overdue = snap_age_secs > poll_interval * 1.5
    countdown_init = "overdue" if overdue else f"{secs_remaining}s"
    components.html(
        f"""
        <div style="font-family:sans-serif;font-size:0.78em;color:#888;
                    padding:0;margin:-6px 0 0 0;">
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
        height=28,
    )

# ── Token expiry warning banner ───────────────────────────────────────────────
# Shown only when token is 6+ days old.  Schwab refresh tokens expire at 7 days.
_token_age = schwab_client.get_token_age_days()
if _token_age is not None and _token_age >= 6:
    if _token_age >= 7:
        st.markdown(
            """
<style>
@keyframes _spx_flash {
    0%,100% { background-color:#922b21; }
    50%     { background-color:#e74c3c; }
}
.spx-token-emergency {
    animation: _spx_flash 0.8s ease-in-out infinite;
    color:#fff; padding:12px 18px; border-radius:6px;
    font-weight:600; font-size:0.95em; margin:8px 0;
}
</style>
<div class="spx-token-emergency">
🚨 SCHWAB TOKEN EXPIRED — Collector is offline. Re-authenticate now:<br>
<code style="background:rgba(0,0,0,0.3);padding:2px 6px;border-radius:3px;">
python -c "import schwab_client; schwab_client.get_client()"
</code>
</div>
""",
            unsafe_allow_html=True,
        )
    else:
        # Day 6 — pulsing yellow warning
        st.markdown(
            """
<style>
@keyframes _spx_pulse {
    0%,100% { opacity:1; }
    50%     { opacity:0.35; }
}
.spx-token-warning {
    animation: _spx_pulse 1.6s ease-in-out infinite;
    background-color:#b7770d; color:#fff;
    padding:10px 18px; border-radius:6px;
    font-weight:500; font-size:0.9em; margin:8px 0;
}
</style>
<div class="spx-token-warning">
⚠️ Schwab API token expires <strong>tomorrow</strong>.
Re-authenticate today to avoid collector downtime:<br>
<code style="background:rgba(0,0,0,0.25);padding:2px 6px;border-radius:3px;">
python -c "import schwab_client; schwab_client.get_client()"
</code>
</div>
""",
            unsafe_allow_html=True,
        )

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TRANSFORMATION OPPORTUNITY SCANNER
# Runs immediately after the header — first thing visible after market data.
# Evaluates every valid (front, back, symmetric strike pair) in the current
# snapshot and surfaces the highest Transform Diff candidates.
# Math is identical to Entry Analysis — single source of truth.
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("🔭 Transformation Opportunity Scanner")

# Strike controls sit inline directly above the table — same visual position
# as the Put Strike / Call Strike columns they target.
_offset_options = [0] + list(range(5, 205, 5))
_offset_fmt     = lambda v: "ATM" if v == 0 else f"−{v}" if False else ("ATM" if v == 0 else str(v))

_sc_c1, _sc_c2, _sc_c3, _sc_c4 = st.columns([2, 1, 1, 2])
with _sc_c1:
    st.caption("**Strike Selection**")
with _sc_c2:
    sc_put_offset = st.selectbox(
        "Put Strike (offset from ATM)",
        options=_offset_options,
        format_func=lambda v: "ATM" if v == 0 else f"ATM − {v}",
        index=0,
        key="sc_put_offset",
    )
with _sc_c3:
    sc_call_offset = st.selectbox(
        "Call Strike (offset from ATM)",
        options=_offset_options,
        format_func=lambda v: "ATM" if v == 0 else f"ATM + {v}",
        index=0,
        key="sc_call_offset",
    )
with _sc_c4:
    sc_gap_pts = sc_put_offset + sc_call_offset
    _sym = "symmetric" if sc_put_offset == sc_call_offset else "asymmetric"
    st.caption(f"**Strike gap: {sc_gap_pts} pts** ({_sym})")

with st.spinner("Scanning combinations…"):
    _ts_df = _compute_transform_scanner(
        chain_df     = chain_df,
        spx_price    = spx_price,
        put_offset   = int(sc_put_offset),
        call_offset  = int(sc_call_offset),
        max_rows     = int(sc_max_rows),
    )

# _TSCAN_THRESHOLD is a visual signal only — never used as a filter.
# The table always shows every valid combination the scanner found,
# sorted by Transform Diff descending.  Green rows simply mark the
# combinations that are immediately actionable.
_TSCAN_THRESHOLD = 5.0

if _ts_df.empty:
    st.caption(
        "No valid combinations found — this means the current chain has no "
        "strike/expiry pairs with marks available for all four diagonal legs "
        "plus the two wing strikes. "
        "The collector may not have run yet, or try widening the Strike Window "
        "or reducing the Liquidity threshold in the sidebar."
    )
else:
    def _ts_row_style(row):
        if row["Transform Diff"] >= _TSCAN_THRESHOLD:
            return ["background-color: #0d3320; color: #2ecc71"] * len(row)
        return [""] * len(row)

    _ts_display = _ts_df.style.apply(_ts_row_style, axis=1).format({
        "Diagonal Mark":  "{:.2f}",
        "Transform Mark": "{:.2f}",
        "Transform Diff": "{:+.2f}",
        "IV Ratio":       lambda v: f"{v:.4f}" if v is not None else "—",
    })

    _ready_count = int((_ts_df["Transform Diff"] >= _TSCAN_THRESHOLD).sum())
    if _ready_count:
        st.markdown(
            f"<div style='margin-bottom:8px;padding:8px 14px;border-radius:6px;"
            f"background:#0d3320;border:1px solid #2ecc71;display:inline-block;'>"
            f"<span style='color:#2ecc71;font-weight:600;'>✓ {_ready_count} combination"
            f"{'s' if _ready_count > 1 else ''} ready to transform</span>"
            f"<span style='color:#aaa;font-size:0.85em;'>"
            f"  ·  Transform Diff ≥ {_TSCAN_THRESHOLD}</span></div>",
            unsafe_allow_html=True,
        )

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
        f"{len(_ts_df)} combinations shown  ·  "
        "Sorted by Transform Diff (descending)  ·  "
        "Green = ready to transform (≥ 5)  ·  "
        "Click any column header to re-sort"
    )

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — CONTROLS ROW
# 4 columns: Front/Back Expiry, Call/Put Strike
# Max Gap lives in the Pair Scanner filter row — not here
# ═════════════════════════════════════════════════════════════════════════════

c1, c2, c3, c4 = st.columns(4)

with c1:
    front_expiry = st.selectbox(
        "Front Expiry", available_expiries, index=0,
        format_func=_exp_label, key="front_expiry_select"
    )
with c2:
    back_expiry = st.selectbox(
        "Back Expiry", available_expiries,
        index=min(1, len(available_expiries) - 1),
        format_func=_exp_label, key="back_expiry_select",
    )

# Build strike lists from actual chain data for the selected expiry pair.
# Only strikes present in BOTH front and back expiry are offered — a
# diagonal requires the same strike in both legs, so showing strikes that
# exist in only one expiry would guarantee a broken calculation.
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
    """Return the index of the strike closest to target, or 0 if list is empty."""
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

front_dte = int(chain_df[chain_df["expiry"] == front_expiry]["dte"].iloc[0])
back_dte  = int(chain_df[chain_df["expiry"] == back_expiry]["dte"].iloc[0])
strikes_set = call_strike > 0 and put_strike > 0

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Compute ts_now early — needed by Calendar Edge (which now comes before
# Historical Stats) and by the Historical Stats range bars themselves.
# ─────────────────────────────────────────────────────────────────────────────

front_iv_atm = iv_engine.atm_iv(chain_df, front_expiry, spx_price)
back_iv_atm  = iv_engine.atm_iv(chain_df, back_expiry,  spx_price)
ts_now       = iv_engine.term_structure(front_iv_atm, back_iv_atm)

# ─────────────────────────────────────────────────────────────────────────────
# atm_merged_90d — fixed 90-day window used by Entry Analysis for
# IV Ratio Percentile.  Period-independent so the percentile reflects
# long-run context regardless of the chart zoom the user has selected.
# ─────────────────────────────────────────────────────────────────────────────

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

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — ENTRY ANALYSIS
# First thing after controls: what is this position offering right now?
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("Entry Analysis")

# ── ATM Straddle (computable without strikes) ────────────────────────────
_straddle = iv_engine.atm_straddle_price(spx_price, front_iv_atm, front_dte)

# ── Position metrics (require strikes) ──────────────────────────────────
_diag_mark: float | None = None
_norm_deb:  float | None = None
_theta_diff: "iv_engine.ThetaDifferential | None" = None
_ic_mark:   float | None = None   # Transform-to-IC Mark

if strikes_set:
    _efc = iv_engine.strike_contract(chain_df, front_expiry, call_strike, "CALL")
    _ebc = iv_engine.strike_contract(chain_df, back_expiry,  call_strike, "CALL")
    _efp = iv_engine.strike_contract(chain_df, front_expiry, put_strike,  "PUT")
    _ebp = iv_engine.strike_contract(chain_df, back_expiry,  put_strike,  "PUT")

    if all(m is not None for m in [_efc.mark, _ebc.mark, _efp.mark, _ebp.mark]):
        _diag_mark = (_ebc.mark + _ebp.mark) - (_efc.mark + _efp.mark)
        _norm_deb  = iv_engine.normalized_debit(_diag_mark, _straddle)

    # Transform-to-IC wings (fixed width = 5, on FRONT expiry — same expiry as the IC shorts)
    # Full transformation order:
    #   Sell to Close: back call + back put  (bc marks already fetched)
    #   Buy to Open:   front call wing +5, front put wing -5  (new fetches below)
    _fc_wing_call = iv_engine.strike_contract(chain_df, front_expiry, call_strike + 5, "CALL")
    _fc_wing_put  = iv_engine.strike_contract(chain_df, front_expiry, put_strike  - 5, "PUT")
    if all(m is not None for m in [_ebc.mark, _ebp.mark, _fc_wing_call.mark, _fc_wing_put.mark]):
        # Transform Mark = credit from closing backs − cost of buying front wings
        _ic_mark = (_ebc.mark + _ebp.mark) - (_fc_wing_call.mark + _fc_wing_put.mark)

    _theta_diff = iv_engine.theta_differential(
        chain_df, front_expiry, back_expiry, call_strike, put_strike
    )

# ── Market condition metrics ─────────────────────────────────────────────
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

st.markdown("<div style='margin-bottom:2px'></div>", unsafe_allow_html=True)

# ── Row 2: Transform-to-IC + market conditions ───────────────────────────
r2a, r2b, r2c, r2d = st.columns(4)

with r2a:
    if _ic_mark is not None and _diag_mark is not None:
        _ic_signal = _ic_mark - _diag_mark
        _ic_color  = "#2ecc71" if _ic_signal > 5 else "#c8d0dc"
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
                "<div style='margin-top:2px;padding:6px 10px;border-radius:6px;"
                "background:#0d3320;border:1px solid #2ecc71;'>"
                "<span style='color:#2ecc71;font-size:0.85em;font-weight:600;'>"
                "✓ Transformation threshold reached</span><br>"
                f"<span style='color:#aaa;font-size:0.78em;'>"
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
                      help=f"Transform Order Mark − Diagonal Mark. "
                           f"Green when ≥ {_THRESHOLD}.")
            st.markdown(
                f"<div style='margin-top:4px;font-size:0.78em;color:#aaa;'>"
                f"<span style='color:#f59e0b;font-family:monospace;'>{_bar}</span>"
                f"&nbsp;{_progress*100:.0f}%<br>"
                f"<span style='color:#64748b;'>{_remaining:.2f} pts until threshold</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.metric("Transform Difference", "— (set strikes)")
        st.caption(f"Needs {_THRESHOLD} pts to trigger transformation signal.")

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — CALENDAR EDGE
# Period radio lives here. atm_merged_period is computed from that selection.
# session_date is used to pin the x-axis on "Today" view from 09:30 → 16:15.
# ═════════════════════════════════════════════════════════════════════════════

_ce_hdr, _ce_radio = st.columns([3, 1])
with _ce_hdr:
    st.subheader("Calendar Edge")
with _ce_radio:
    period_label = st.radio(
        "Chart Range",
        ["Today", "5D", "10D", "20D"],
        horizontal=True,
        label_visibility="collapsed",
        key="period_radio",
    )
period_days = {"Today": 1, "5D": 5, "10D": 10, "20D": 20}[period_label]

# Build atm_merged_period — respects weekend/gap fallback for "Today"
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

iv_index = float(chain_df.groupby("expiry")["iv"].mean().mean())
m1, m2, m3, m4 = st.columns(4)
m1.metric("ATM IV Ratio (F/B)", f"{ts_now.ratio:.4f}")
m2.metric("Front ATM IV",       f"{ts_now.front_iv:.2f}%")
m3.metric("Back ATM IV",        f"{ts_now.back_iv:.2f}%")
m4.metric("IV Index (avg)",     f"{iv_index:.2f}%")

st.info(iv_engine.interpret_curve(ts_now))

if not atm_merged.empty:
    # x-axis bounds: on "Today" pin from 09:30 to 16:15 of session_date
    _xaxis_today = (
        dict(range=[f"{session_date} 09:30", f"{session_date} 16:15"],
             rangebreaks=_SESSION_RANGEBREAKS)
        if period_label == "Today"
        else dict(rangebreaks=_SESSION_RANGEBREAKS)
    )

    # ── Primary: Front/Back IV + Ratio on dual axis ───────────────────────
    fig_atm = go.Figure()
    fig_atm.add_trace(go.Scatter(x=atm_merged["timestamp"], y=atm_merged["front_iv"],
        name="Front ATM IV", line=dict(color="#2ecc71", width=1.5), yaxis="y1"))
    fig_atm.add_trace(go.Scatter(x=atm_merged["timestamp"], y=atm_merged["back_iv"],
        name="Back ATM IV",  line=dict(color="#3498db", width=1.5), yaxis="y1"))
    fig_atm.add_trace(go.Scatter(x=atm_merged["timestamp"], y=atm_merged["iv_ratio"],
        name="IV Ratio (F/B)", line=dict(color="#e74c3c", width=1.5), yaxis="y2"))
    fig_atm.update_layout(
        height=300, margin=dict(l=20, r=20, t=10, b=20),
        xaxis=_xaxis_today,
        yaxis=dict(title="IV %", side="left"),
        yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    st.plotly_chart(fig_atm, use_container_width=True)

    samp_warn = iv_engine.sample_size_warning(atm_merged["iv_ratio"])
    if samp_warn:
        st.warning(samp_warn)

    # ── Stacked: same-axis IV + regime-colored ratio ──────────────────────
    st.markdown("**Front vs Back ATM IV — same axis · IV Ratio by regime**")
    fig_stack = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.62, 0.38], vertical_spacing=0.06,
        subplot_titles=("Front vs Back ATM IV — same axis (the gap IS the spread)",
                        "IV Ratio (F/B) — colored by regime"),
    )
    fig_stack.add_trace(go.Scatter(
        x=atm_merged["timestamp"], y=atm_merged["front_iv"],
        name="Front ATM IV", line=dict(color="#2ecc71", width=1.5)), row=1, col=1)
    fig_stack.add_trace(go.Scatter(
        x=atm_merged["timestamp"], y=atm_merged["back_iv"],
        name="Back ATM IV", line=dict(color="#3498db", width=1.5)), row=1, col=1)
    for tr in _banded_ratio_traces(atm_merged["timestamp"], atm_merged["iv_ratio"]):
        fig_stack.add_trace(tr, row=2, col=1)
    for thr, dash in [(1.00, "solid"), (0.70, "dot"), (1.30, "dot")]:
        fig_stack.add_hline(y=thr, line=dict(color="#777", width=1, dash=dash), row=2, col=1)
    if period_label == "Today":
        fig_stack.update_xaxes(
            range=[f"{session_date} 09:30", f"{session_date} 16:15"],
            rangebreaks=_SESSION_RANGEBREAKS,
        )
    else:
        fig_stack.update_xaxes(rangebreaks=_SESSION_RANGEBREAKS)
    fig_stack.update_yaxes(title_text="IV %", row=1, col=1)
    fig_stack.update_yaxes(title_text="Ratio", row=2, col=1)
    fig_stack.update_layout(
        height=520, margin=dict(l=20, r=20, t=40, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=-0.18,
                    xanchor="left", x=0, font=dict(size=10)),
    )
    st.plotly_chart(fig_stack, use_container_width=True)
    st.caption(
        "Top: front and back ATM IV share one axis — the vertical gap IS the spread. "
        "Bottom: ratio colored by regime at 0.70 / 1.00 / 1.30. "
        "Green (≥1) = backwardation (front rich). Amber (<0.70) = usually 0DTE decay artifact."
    )

    # ── Intraday Front vs Back IV scatter ────────────────────────────────
    st.markdown("**Front vs Back IV scatter — intraday trajectory**")
    _sc = atm_merged.copy()
    _sc["hod"] = _sc["timestamp"].dt.hour + _sc["timestamp"].dt.minute / 60.0
    _lo = float(min(_sc["back_iv"].min(), _sc["front_iv"].min()))
    _hi = float(max(_sc["back_iv"].max(), _sc["front_iv"].max()))
    _pad = (_hi - _lo) * 0.05 or 1.0
    fig_intra = go.Figure()
    fig_intra.add_trace(go.Scatter(
        x=[_lo - _pad, _hi + _pad], y=[_lo - _pad, _hi + _pad], mode="lines",
        name="R = 1  (Front = Back)", line=dict(color="#888", dash="dash")))
    fig_intra.add_trace(go.Scatter(
        x=_sc["back_iv"], y=_sc["front_iv"], mode="markers", name="snapshots",
        marker=dict(size=6, color=_sc["hod"], colorscale="Viridis",
                    showscale=True, colorbar=dict(title="Hour ET"), line=dict(width=0)),
        customdata=_sc["iv_ratio"],
        hovertemplate="Back %{x:.2f}%<br>Front %{y:.2f}%<br>R=%{customdata:.4f}<extra></extra>"))
    fig_intra.update_layout(
        height=420, margin=dict(l=20, r=20, t=10, b=20),
        xaxis_title="Back ATM IV %", yaxis_title="Front ATM IV %",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    st.plotly_chart(fig_intra, use_container_width=True)
    st.caption(
        "Each dot is one snapshot. Above the dashed line = backwardation (R>1); below = contango. "
        "Color = time of day. A cloud hugging one ray → ratio ≈ constant; "
        "fanning across angles → ratio varies independently of vol level."
    )

else:
    st.caption(f"No ATM IV history for {front_expiry} / {back_expiry} in the selected range.")

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
# SECTION 5 — HISTORICAL STATISTICS
# Weekend/gap fallback: if Today returns empty, show last available session.
# Each column now shows: range bar, current percentile, min/max/current values.
# ═════════════════════════════════════════════════════════════════════════════

st.subheader(
    f"Historical Statistics — ATM IV Ratio  ·  "
    f"{front_expiry} ({front_dte}d)  /  {back_expiry} ({back_dte}d)"
)

stat_cols = st.columns(4)
for col, (label, days) in zip(
    stat_cols,
    [("Today", 1), ("5 Days", 5), ("10 Days", 10), ("20 Days", 20)],
):
    # Weekend / gap fallback: if Today (days=1) is empty, use last session
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
            rs = iv_engine.range_stats(pm["ratio"], ts_now.ratio)
            pct_rank = iv_engine.percentile_rank(pm["ratio"], ts_now.ratio)
            _is_low  = pct_rank < 25
            _is_high = pct_rank > 75
            _ctx_color = "#2ecc71" if _is_high else ("#e74c3c" if _is_low else "#aaa")
            _ctx_label = "HIGH" if _is_high else ("LOW" if _is_low else "MID")
            st.markdown(
                f"""<div style="font-size:0.83em;line-height:1.6;">
  <span style="color:#aaa;">Min</span> {rs.low:.4f}
  <div style="background:linear-gradient(90deg,#333,#666);height:6px;border-radius:3px;position:relative;margin:4px 0;">
    <div style="position:absolute;left:{rs.position_pct:.1f}%;top:-4px;width:14px;height:14px;background:#e74c3c;border-radius:50%;transform:translateX(-50%);border:2px solid #0e1117;"></div>
  </div>
  <span style="color:#aaa;">Max</span> {rs.high:.4f}<br>
  <span style="color:#aaa;">Now</span> <b>{ts_now.ratio:.4f}</b>
  &nbsp;<span style="color:{_ctx_color};font-size:0.9em;">{pct_rank:.0f}th pct · {_ctx_label}</span>
</div>""",
                unsafe_allow_html=True,
            )
        else:
            st.caption("No data")

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — STRIKE DETAIL + IV STRUCTURE CHART
# Period selector already rendered above; period_days already defined.
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("Strike Detail")

left_col, right_col = st.columns([1, 3])

with left_col:

    st.markdown("**Expiry Detail**")

    for exp_label, exp_date, dte_val in [
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
            chg_color = "#2ecc71" if atm_chg >= 0 else "#e74c3c"
            chg_arrow = "↑" if atm_chg >= 0 else "↓"
            st.markdown(
                f"<p style='margin:0;font-size:0.8em;color:#aaa;'>"
                f"{exp_label} · {exp_date} · {dte_val} DTE</p>"
                f"<p style='margin:0;font-size:1.6em;font-weight:600;'>"
                f"{atm_now:.2f}%</p>"
                f"<p style='margin:0 0 10px 0;font-size:0.85em;color:{chg_color};'>"
                f"{chg_arrow} {atm_chg:+.2f}%</p>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<p style='margin:0;font-size:0.8em;color:#aaa;'>"
                f"{exp_label} · {exp_date} · {dte_val} DTE</p>"
                f"<p style='margin:0 0 10px 0;color:#666;'>N/A</p>",
                unsafe_allow_html=True,
            )

    st.markdown("<hr style='margin:8px 0;opacity:0.2;'>", unsafe_allow_html=True)

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
                f"<p style='margin:6px 0 2px 0;font-weight:600;'>{leg_label}</p>"
                f"<p style='margin:0;font-size:0.82em;'>"
                f"IV → F <span style='color:#2ecc71;'>{f_iv_str}</span> "
                f"/ B <span style='color:#3498db;'>{b_iv_str}</span> "
                f"&nbsp;·&nbsp; Ratio <span style='color:#e74c3c;'>{ratio_str}</span></p>"
                f"<p style='margin:0 0 6px 0;font-size:0.82em;'>"
                f"Mark → F <span style='color:#2ecc71;'>{f_mk_str}</span> "
                f"/ B <span style='color:#3498db;'>{b_mk_str}</span></p>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("Set call and put strikes in Controls above.")

with right_col:
    st.subheader("Selected-Strike IV")
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
            _str_xaxis = (
                dict(range=[f"{session_date} 09:30", f"{session_date} 16:15"],
                     rangebreaks=_SESSION_RANGEBREAKS)
                if period_label == "Today"
                else dict(rangebreaks=_SESSION_RANGEBREAKS)
            )
            fig_str.update_layout(
                height=360, margin=dict(l=20, r=20, t=10, b=20),
                xaxis=_str_xaxis,
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
# PAIR SCANNER DATA — computed once, shared by Pinned Pairs and Pair Scanner
# ═════════════════════════════════════════════════════════════════════════════

full_scanner_df = _compute_pair_scanner(session_date)
scanner_total   = len(full_scanner_df)
scanner_snaps   = (
    int(full_scanner_df["snapshots"].max())
    if not full_scanner_df.empty else 0
)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — PINNED PAIRS
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
# SECTION 8 — PAIR SCANNER
# ═════════════════════════════════════════════════════════════════════════════

sc_hdr, sc_meta = st.columns([3, 2])
with sc_hdr:
    st.subheader("🔍 Pair Scanner")
with sc_meta:
    st.caption(f"{scanner_total} pairs  ·  {scanner_snaps} snapshots today")

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

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 10 — RESEARCH: IV Ratio vs. Normalized Debit
# Observation tool placed at bottom — not part of the primary decision flow.
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("Research — IV Ratio vs. Normalized Debit")
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
            marker=dict(color="#38b2ac", size=7, opacity=0.55,
                        line=dict(color="#234e52", width=0.5)),
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
                line=dict(color="#718096", width=1.5, dash="dash"),
                showlegend=True, name="OLS trend (descriptive)", hoverinfo="skip",
            ))

    if _norm_deb is not None and ts_now.ratio is not None:
        fig_sc.add_trace(go.Scatter(
            x=[ts_now.ratio], y=[_norm_deb], mode="markers",
            marker=dict(symbol="diamond", color="#f59e0b", size=14,
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

    fig_sc.add_vline(x=1.0, line=dict(color="#4a5568", width=1, dash="dot"),
                     annotation_text="ratio = 1.0",
                     annotation_font=dict(color="#718096", size=10),
                     annotation_position="top right")
    if not _has_data and _norm_deb is None:
        fig_sc.add_annotation(
            x=0.5, y=0.5, xref="paper", yref="paper",
            text="No data yet — scatter populates as snapshots accumulate.",
            showarrow=False, font=dict(color="#718096", size=13),
        )
    fig_sc.update_layout(
        height=380, paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
        margin=dict(l=60, r=20, t=20, b=44),
        xaxis=dict(title="ATM IV Ratio (Front / Back)",
                   title_font=dict(color="#c8d0dc", size=11),
                   tickfont=dict(color="#c8d0dc", size=11),
                   gridcolor="#1e2530", showgrid=True, zeroline=False),
        yaxis=dict(title="Normalized Debit (diagonal mark ÷ ATM straddle)",
                   title_font=dict(color="#c8d0dc", size=11),
                   tickfont=dict(color="#c8d0dc", size=11),
                   gridcolor="#1e2530", showgrid=True, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
                    font=dict(color="#c8d0dc", size=11), bgcolor="rgba(0,0,0,0)"),
        hovermode="closest",
        hoverlabel=dict(bgcolor="#1a2035", bordercolor="#334155",
                        font=dict(color="#c8d0dc", size=13)),
    )
    if not _has_data:
        st.caption(
            "Fewer than 5 complete snapshots found for this strike/expiry pair. "
            "Scatter populates as more data is collected."
        )
    st.plotly_chart(fig_sc, use_container_width=True, config={"displayModeBar": False})
