"""
pages/journal.py — SPX Trade Journal

Streamlit multi-page entry. Streamlit auto-discovers files in pages/ and
adds them to the sidebar navigation. Run the dashboard normally:

    streamlit run app.py

The journal appears as a navigation link automatically.

DATA FLOW
    Reads: db.trades (trade records), db.option_rows (live IC mark prices)
    Writes: db.trades only — via db.insert_trade, db.update_trade, db.delete_trade

NEVER touches snapshots, option_rows, atm_iv_by_expiry, or collection_gaps.
Those are owned by collector.py. This page is read-only for market data.

IC MARK PRICE MATH
    IC position: short short_call + short short_put + long long_call + long long_put
    Cost to close = mark(short_call) + mark(short_put) - mark(long_call) - mark(long_put)
    Unrealized P&L per share = profit_locked_in - cost_to_close
    Unrealized P&L per contract = unrealized_per_sh * 100 * contracts

CLOSE TYPE
    close_type = "transform"  → position converted to Iron Condor (existing path)
    close_type = "direct"     → all legs closed manually before transformation
    close_type = None         → legacy records created before this field was added
                                (treated as "transform" for display purposes)
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import db

# ─────────────────────────────────────────────────────────────────────────────
# Page Config (must be first Streamlit call)
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Trade Journal · SPX",
    page_icon="📒",
    layout="wide",
)

# ─────────────────────────────────────────────────────────────────────────────
# DB Init — idempotent, runs every page load
# ─────────────────────────────────────────────────────────────────────────────

db.init_trades_table(config.DB_PATH)
db.seed_t001(config.DB_PATH)

# ─────────────────────────────────────────────────────────────────────────────
# Navigation options (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

_NAV_OPTIONS = [
    "📊 Overview",
    "📈 Regime Analysis",
    "➕ Log a Trade",
    "🔄 Close / Transform",
    "⏰ Mark Expired",
    "✏️ Edit Notes",
]

# ─────────────────────────────────────────────────────────────────────────────
# Session State Init
# ─────────────────────────────────────────────────────────────────────────────

# Navigation intermediary — written before st.rerun(), applied to the keyed
# radio at the very top of the next script run before the widget renders.
if "_pending_nav" not in st.session_state:
    st.session_state["_pending_nav"] = None

# Close/Transform mode intermediary — same pattern as _pending_nav.
# Allows Edit buttons to pre-select "Transform to Iron Condor" or
# "Close Position Directly" without writing to a keyed widget after it renders.
if "_pending_close_mode" not in st.session_state:
    st.session_state["_pending_close_mode"] = None

# Trade CRUD
if "edit_trade_id" not in st.session_state:
    st.session_state["edit_trade_id"] = None
if "confirm_delete_id" not in st.session_state:
    st.session_state["confirm_delete_id"] = None

# Transform / Close CRUD
if "edit_transform_id" not in st.session_state:
    st.session_state["edit_transform_id"] = None
if "confirm_delete_transform_id" not in st.session_state:
    st.session_state["confirm_delete_transform_id"] = None

# Close mode radio default
if "close_mode_radio" not in st.session_state:
    st.session_state["close_mode_radio"] = "Transform to Iron Condor"

# Success banner
if "_success_msg" not in st.session_state:
    st.session_state["_success_msg"] = None

# ─────────────────────────────────────────────────────────────────────────────
# Apply pending navigation and close-mode BEFORE any widget renders
# ─────────────────────────────────────────────────────────────────────────────

if st.session_state["_pending_nav"]:
    st.session_state["page_mode_radio"] = st.session_state.pop("_pending_nav")

if st.session_state["_pending_close_mode"]:
    st.session_state["close_mode_radio"] = st.session_state.pop("_pending_close_mode")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def fmt_pl(val, decimals=0) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    fmt = f",.{decimals}f"
    return f"+${val:{fmt}}" if val >= 0 else f"−${abs(val):{fmt}}"


def fmt_f2(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return f"{val:.2f}"


def fmt_pct(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return f"{val:.1f}%"


def bool_icon(val) -> str:
    if val is None:
        return "—"
    return "✓" if int(val) == 1 else "✗"


def holding_days(t) -> int | None:
    try:
        entry = date.fromisoformat(t["entry_date"])
        end = date.fromisoformat(t["result_date"]) if t["result_date"] else date.today()
        return (end - entry).days
    except Exception:
        return None


def legs_df(legs_json: str | None) -> pd.DataFrame | None:
    if not legs_json:
        return None
    try:
        legs = json.loads(legs_json)
        df = pd.DataFrame(legs)[["expiry", "type", "action", "strike", "fill"]]
        df.columns = ["Expiry", "Type", "Action", "Strike", "Fill"]
        return df
    except Exception:
        return None


def get_close_type(t) -> str | None:
    """Safely read close_type from a sqlite3.Row (may not exist in legacy rows)."""
    try:
        return t["close_type"] if "close_type" in t.keys() else None
    except Exception:
        return None


def compute_stats(rows: list) -> dict:
    if not rows:
        return {}
    # "Expired" = IC reached expiration; "Closed" = manually closed before/without IC.
    # Both count as completed trades for all statistics.
    completed = [r for r in rows if r["status"] in ("Expired", "Closed") and r["final_pl"] is not None]
    pls = [float(r["final_pl"]) for r in completed]
    wins = [p for p in pls if p > 0]
    loss = [p for p in pls if p <= 0]
    transformed = [r for r in rows if r["transform_minutes"] is not None]
    total_wins = sum(wins)
    total_loss = sum(loss)
    win_rate = len(wins) / len(pls) * 100 if pls else None
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(loss) / len(loss) if loss else None
    pf = total_wins / abs(total_loss) if total_loss != 0 else None
    exp_val = None
    if win_rate is not None and avg_win is not None and avg_loss is not None:
        exp_val = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
    hold_days_list = [holding_days(r) for r in completed if holding_days(r) is not None]
    avg_hold = sum(hold_days_list) / len(hold_days_list) if hold_days_list else None
    t_mins = [r["transform_minutes"] for r in transformed if r["transform_minutes"]]
    avg_t_min = sum(t_mins) / len(t_mins) if t_mins else None
    debits = [float(r["total_debit"]) for r in rows]
    credits = [float(r["credit_received"]) for r in rows if r["credit_received"] is not None]
    fees = [float(r["commissions"]) for r in rows if r["commissions"] is not None]
    return {
        "Total Trades": len(rows),
        "Win Rate": win_rate,
        "Average Winner": avg_win,
        "Average Loser": avg_loss,
        "Profit Factor": pf,
        "Expectancy": exp_val,
        "Avg Entry Debit": sum(debits) / len(debits) if debits else None,
        "Avg Close Credit": sum(credits) / len(credits) if credits else None,
        "Avg Holding Time (days)": avg_hold,
        "Avg Time to Transform": avg_t_min,
        "Avg Max Drawdown": None,
        "Largest Winner": max(wins) if wins else None,
        "Largest Loser": min(loss) if loss else None,
        "Total Fees": sum(fees) if fees else None,
        "Total Net Profit": sum(pls) if pls else None,
    }


def derive_ic(init_legs_json: str, tf_legs: list, credit: float,
              total_debit: float, contracts: int) -> dict | None:
    try:
        init = json.loads(init_legs_json)
        sc = next((l["strike"] for l in init if "Sell" in l["action"] and l["type"] == "Call"), None)
        sp = next((l["strike"] for l in init if "Sell" in l["action"] and l["type"] == "Put"), None)
        lc = next((l["strike"] for l in tf_legs if "Buy" in l["action"] and l["type"] == "Call"), None)
        lp = next((l["strike"] for l in tf_legs if "Buy" in l["action"] and l["type"] == "Put"), None)
        ic_expiry = next((l["expiry"] for l in tf_legs if "Buy" in l["action"]), None)
        if not all([sc, sp, lc, lp, ic_expiry]):
            return None
        c_wing = abs(float(lc) - float(sc))
        p_wing = abs(float(sp) - float(lp))
        locked = credit - total_debit
        max_p = round(locked * 100 * contracts)
        max_ic = max(c_wing, p_wing) * 100 * contracts
        risk_f = max_p > max_ic
        return {
            "ic_expiry_date": ic_expiry,
            "ic_short_call": float(sc), "ic_long_call": float(lc),
            "ic_short_put": float(sp), "ic_long_put": float(lp),
            "ic_call_wing": c_wing, "ic_put_wing": p_wing,
            "ic_max_profit": float(max_p),
            "ic_worst_case": float(max_p - max_ic) if risk_f else float(max_ic - max_p),
            "ic_risk_free": 1 if risk_f else 0,
        }
    except Exception:
        return None


def render_regime_analysis(all_trades: list) -> None:
    st.subheader("📈 Regime Analysis — does IV Ratio add value beyond IV level?")
    st.caption(
        "Reconstructs the IV term structure at each trade's entry from stored "
        "snapshots, then asks whether structure (Ratio) carries outcome "
        "information after controlling for level (√(Front·Back))."
    )
    if not all_trades:
        st.info("No trades logged yet. This view activates as you log entries.")
        return
    et, utc = ZoneInfo(config.DISPLAY_TIMEZONE), ZoneInfo("UTC")
    recs, missing = [], 0
    for t in all_trades:
        try:
            legs = json.loads(t["initial_legs"])
            exps = sorted({l["expiry"] for l in legs})
            front_e, back_e = exps[0], exps[-1]
            call_k = next(l["strike"] for l in legs if l["type"] == "Call")
            put_k = next(l["strike"] for l in legs if l["type"] == "Put")
            dt_et = datetime.strptime(
                f"{t['entry_date']} {t['entry_time']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=et)
            ts_utc = dt_et.astimezone(utc).strftime("%Y-%m-%d %H:%M:%S")
            ctx = db.get_entry_iv_context(config.DB_PATH, ts_utc, front_e, back_e, call_k, put_k)
        except Exception:
            ctx = None
        if not ctx or ctx["front_iv"] is None or ctx["back_iv"] is None:
            missing += 1
            continue
        recs.append({
            "trade_id": t["trade_id"], "status": t["status"],
            "front_iv": ctx["front_iv"] * 100, "back_iv": ctx["back_iv"] * 100,
            "ratio": ctx["ratio"],
            "level": (ctx["level"] * 100) if ctx["level"] else None,
            "outcome": t["profit_locked_in"],
        })
    n_ctx = len(recs)
    st.markdown(
        f"**{n_ctx}** of **{len(all_trades)}** trades have reconstructable entry IV context"
        + (f" · {missing} not matched to a snapshot." if missing else ".")
    )
    if n_ctx == 0:
        st.warning("No trades matched a stored snapshot near their entry time.")
        return
    rf = pd.DataFrame(recs)
    med_ratio = float(rf["ratio"].median())
    med_level = float(rf["level"].median())
    lo = float(min(rf["back_iv"].min(), rf["front_iv"].min()))
    hi = float(max(rf["back_iv"].max(), rf["front_iv"].max()))
    pad = (hi - lo) * 0.08 or 1.0
    xlo, xhi = max(0.01, lo - pad), hi + pad
    st.markdown("##### Front vs Back IV at entry — quadrants by level x ratio")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[xlo, xhi], y=[xlo, xhi], mode="lines",
                             name="R = 1", line=dict(color="#888", dash="dash")))
    fig.add_trace(go.Scatter(x=[xlo, xhi], y=[med_ratio * xlo, med_ratio * xhi],
                             mode="lines", name=f"median R = {med_ratio:.3f}",
                             line=dict(color="#e67e22", dash="dot")))
    L2 = med_level ** 2
    xs = [xlo + (xhi - xlo) * i / 60 for i in range(61)]
    fig.add_trace(go.Scatter(x=xs, y=[L2 / x for x in xs], mode="lines",
                             name=f"median level = {med_level:.2f}%",
                             line=dict(color="#9b59b6", dash="dot")))
    have = rf["outcome"].notna()
    if have.any():
        d = rf[have]
        fig.add_trace(go.Scatter(
            x=d["back_iv"], y=d["front_iv"], mode="markers+text",
            text=d["trade_id"], textposition="top center", name="closed/transformed",
            marker=dict(size=13, color=d["outcome"], colorscale="RdYlGn", cmid=0,
                        showscale=True, colorbar=dict(title="Credit/sh"),
                        line=dict(width=1, color="#222")),
            customdata=d[["ratio", "level"]].to_numpy(),
            hovertemplate="%{text}<br>Back %{x:.2f}% Front %{y:.2f}%"
                          "<br>R=%{customdata[0]:.3f} level=%{customdata[1]:.2f}%<extra></extra>"))
    if (~have).any():
        d = rf[~have]
        fig.add_trace(go.Scatter(
            x=d["back_iv"], y=d["front_iv"], mode="markers+text",
            text=d["trade_id"], textposition="top center", name="open (no outcome)",
            marker=dict(size=12, color="#888", symbol="circle-open",
                        line=dict(width=2, color="#aaa")),
            customdata=d[["ratio", "level"]].to_numpy(),
            hovertemplate="%{text}<br>Back %{x:.2f}% Front %{y:.2f}%"
                          "<br>R=%{customdata[0]:.3f} level=%{customdata[1]:.2f}%<extra></extra>"))
    fig.update_layout(height=480, margin=dict(l=20, r=20, t=10, b=20),
                      xaxis_title="Back IV % (at entry)", yaxis_title="Front IV % (at entry)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=10)))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Dashed = R=1 (above it, front is richer). Orange ray splits high vs low **ratio**; "
        "purple hyperbola splits high vs low **level** (√(F·B))."
    )
    st.markdown("##### Quadrant outcomes (stratified)")
    wo = rf[rf["outcome"].notna()].copy()
    if wo.empty:
        st.info("No closed/transformed trades yet.")
    else:
        wo["Level"] = (wo["level"] >= med_level).map({True: "High", False: "Low"})
        wo["Ratio"] = (wo["ratio"] >= med_ratio).map({True: "High", False: "Low"})
        cells = []
        for lv in ("High", "Low"):
            for rt in ("High", "Low"):
                sub = wo[(wo["Level"] == lv) & (wo["Ratio"] == rt)]
                cells.append({"Level": lv, "Ratio": rt, "n": len(sub),
                               "m": round(float(sub["outcome"].mean()), 3) if len(sub) else None})
        cdf = pd.DataFrame(cells)
        grid = cdf.pivot(index="Level", columns="Ratio", values="m")
        ngrid = cdf.pivot(index="Level", columns="Ratio", values="n")
        disp = grid.astype("object").copy()
        for i in grid.index:
            for j in grid.columns:
                m, nn = grid.loc[i, j], ngrid.loc[i, j]
                disp.loc[i, j] = "—" if (m is None or pd.isna(m)) else f"{m:+.3f} (n={int(nn)})"
        st.write("Mean close credit / share — rows = Level, cols = Ratio:")
        st.table(disp)
        thin = int((ngrid.fillna(0) < 5).to_numpy().sum())
        if thin:
            st.warning(f"{thin} of 4 cells have n<5 — treat as framework filling in, not a result.")
        with st.expander("How to read this & what NOT to conclude", expanded=False):
            st.markdown(
                "- **The test:** compare High vs Low **Ratio** *within* each Level row.\n"
                "- **Sample size:** with a handful of trades, none of this is significant.\n"
                "- **Selection bias:** empty quadrant = 'never traded there', not 'bad'.\n"
                "- IVs are at-strike, from the nearest snapshot to your logged entry time."
            )

# ─────────────────────────────────────────────────────────────────────────────
# Load Data
# ─────────────────────────────────────────────────────────────────────────────

all_trades = db.get_all_trades(config.DB_PATH)
open_trades = [t for t in all_trades if t["status"] == "Open"]
active_trades = [t for t in all_trades if t["status"] in ("Open", "Transformed")]

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 📒 Trade Journal")
    st.caption("SPX Diagonal Calendar → Iron Condor")
    st.divider()

    page_mode = st.radio(
        "Navigation",
        _NAV_OPTIONS,
        label_visibility="collapsed",
        key="page_mode_radio",
    )

    if all_trades:
        st.divider()
        trade_options = {t["trade_id"]: f"{t['trade_id']} — {t['entry_date']} ({t['status']})"
                         for t in all_trades}
        selected_id = st.selectbox(
            "Inspect Trade",
            options=["—"] + list(trade_options.keys()),
            format_func=lambda x: trade_options.get(x, x),
        )
    else:
        selected_id = "—"

# ─────────────────────────────────────────────────────────────────────────────
# Page Header
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("## 📒 SPX Diagonal Calendar — Trade Journal")
st.caption("Diagonal Calendar → Iron Condor &nbsp;|&nbsp; Live marks sourced from dashboard.db (option_rows)")
st.divider()

if st.session_state["_success_msg"]:
    st.success(st.session_state["_success_msg"])
    st.session_state["_success_msg"] = None

# ─────────────────────────────────────────────────────────────────────────────
# ── MODE: OVERVIEW
# ─────────────────────────────────────────────────────────────────────────────

if page_mode == "📊 Overview":

    st.subheader("Strategy Statistics")
    stats = compute_stats(list(all_trades))

    def stat_cell(label, val, is_pl=False, is_pct=False, is_time=False, decimals=0):
        if val is None:
            display, color = "—", "color:#64748b"
        elif is_pct:
            display = fmt_pct(val)
            color = "color:#4ade80" if val >= 50 else "color:#f87171"
        elif is_time:
            display = f"{val:.0f}m" if val < 60 else f"{val/60:.1f}h"
            color = "color:#e2e8f0"
        elif is_pl:
            display = fmt_pl(val, decimals)
            color = "color:#4ade80" if val >= 0 else "color:#f87171"
        elif label == "Profit Factor":
            display = f"{val:.2f}x"
            color = "color:#4ade80" if val >= 1.5 else "color:#fbbf24"
        elif label == "Total Trades":
            display, color = str(int(val)), "color:#e2e8f0"
        else:
            display, color = f"{val:.2f}", "color:#e2e8f0"
        st.markdown(
            f"<div style='background:#1e293b;border:1px solid #334155;border-radius:8px;"
            f"padding:12px 14px;'>"
            f"<div style='color:#64748b;font-size:11px;text-transform:uppercase;"
            f"letter-spacing:.08em;margin-bottom:4px'>{label}</div>"
            f"<div style='font-family:monospace;font-size:18px;font-weight:600;{color}'>"
            f"{display}</div></div>",
            unsafe_allow_html=True,
        )

    r1, r2, r3 = st.columns(5), st.columns(5), st.columns(5)
    with r1[0]: stat_cell("Total Trades", stats.get("Total Trades"))
    with r1[1]: stat_cell("Win Rate", stats.get("Win Rate"), is_pct=True)
    with r1[2]: stat_cell("Average Winner", stats.get("Average Winner"), is_pl=True)
    with r1[3]: stat_cell("Average Loser", stats.get("Average Loser"), is_pl=True)
    with r1[4]: stat_cell("Profit Factor", stats.get("Profit Factor"))
    with r2[0]: stat_cell("Expectancy", stats.get("Expectancy"), is_pl=True)
    with r2[1]: stat_cell("Avg Entry Debit", stats.get("Avg Entry Debit"), decimals=2)
    with r2[2]: stat_cell("Avg Close Credit", stats.get("Avg Close Credit"), decimals=2)
    with r2[3]: stat_cell("Avg Holding (days)", stats.get("Avg Holding Time (days)"), decimals=1)
    with r2[4]: stat_cell("Avg Time to Transform", stats.get("Avg Time to Transform"), is_time=True)
    with r3[0]: stat_cell("Avg Max Drawdown", stats.get("Avg Max Drawdown"), is_pl=True)
    with r3[1]: stat_cell("Largest Winner", stats.get("Largest Winner"), is_pl=True)
    with r3[2]: stat_cell("Largest Loser", stats.get("Largest Loser"), is_pl=True)
    with r3[3]: stat_cell("Total Fees", stats.get("Total Fees"), is_pl=True)
    with r3[4]: stat_cell("Total Net Profit", stats.get("Total Net Profit"), is_pl=True)
    st.markdown("<div style='margin-top:4px;color:#475569;font-size:11px'>"
                "Avg Max Drawdown requires intraday mark history — available in a future update. "
                "Avg Close Credit covers both IC transform credits and direct-close proceeds.</div>",
                unsafe_allow_html=True)
    st.divider()

    # ── Master Log ────────────────────────────────────────────────────────────
    st.subheader("Master Log")

    if not all_trades:
        st.info("No trades logged yet. Use **➕ Log a Trade** in the sidebar to add your first trade.")
    else:
        log_rows = []
        for t in all_trades:
            _ct = get_close_type(t)
            log_rows.append({
                "ID": t["trade_id"],
                "Date": t["entry_date"],
                "Day": t["day_of_week"] or "—",
                "Time (ET)": t["entry_time"],
                "Status": t["status"],
                "Close Type": "Direct" if _ct == "direct" else ("IC Transform" if t["transform_date"] else "—"),
                "Qty": t["contracts"],
                "Debit/sh": f"−{fmt_f2(t['total_debit'])}",
                "Net Credit/sh": f"+{fmt_f2(t['profit_locked_in'])}" if t["profit_locked_in"] else "—",
                "IC Max Profit": fmt_pl(t["ic_max_profit"]) if t["ic_max_profit"] else "—",
                "Outcome": t["outcome"] or "Pending",
            })
        st.dataframe(pd.DataFrame(log_rows), use_container_width=True, hide_index=True)

        # ── Actions Row ───────────────────────────────────────────────────────
        st.markdown("")
        trade_ids = [t["trade_id"] for t in all_trades]
        ac1, ac2, ac3, _ = st.columns([3, 1, 1, 5])

        action_trade_id = ac1.selectbox(
            "Select trade", options=trade_ids,
            label_visibility="collapsed", key="action_trade_select",
        )
        if ac2.button("✏️ Edit", use_container_width=True, key="btn_edit_trade"):
            st.session_state["edit_trade_id"] = action_trade_id
            st.session_state["confirm_delete_id"] = None
            st.session_state["_pending_nav"] = "➕ Log a Trade"
            st.rerun()
        if ac3.button("🗑️ Delete", use_container_width=True, key="btn_delete_trade"):
            st.session_state["confirm_delete_id"] = action_trade_id

        if st.session_state["confirm_delete_id"]:
            del_id = st.session_state["confirm_delete_id"]
            st.warning(f"⚠️ Are you sure you want to delete **{del_id}**? This cannot be undone.")
            cc1, cc2, _ = st.columns([1, 1, 6])
            if cc1.button("Confirm Delete", type="primary", key="btn_confirm_del_trade"):
                db.delete_trade(config.DB_PATH, del_id)
                st.session_state["confirm_delete_id"] = None
                st.session_state["_success_msg"] = f"Trade {del_id} deleted."
                st.rerun()
            if cc2.button("Cancel", key="btn_cancel_del_trade"):
                st.session_state["confirm_delete_id"] = None
                st.rerun()

    # ── Trade Detail (if selected) ────────────────────────────────────────────
    if selected_id and selected_id != "—":
        st.divider()
        t = db.get_trade(config.DB_PATH, selected_id)
        if t:
            st.subheader(f"Trade Detail — {t['trade_id']}")
            status_color = {
                "Open": "#3b82f6", "Transformed": "#8b5cf6",
                "Expired": "#10b981", "Closed": "#64748b",
            }.get(t["status"], "#64748b")
            st.markdown(
                f"<span style='background:{status_color}22;color:{status_color};"
                f"border:1px solid {status_color}55;border-radius:4px;padding:2px 10px;"
                f"font-size:12px;font-family:monospace'>{t['status']}</span>"
                f"&nbsp;&nbsp;<span style='color:#94a3b8;font-size:13px;font-family:monospace'>"
                f"{t['entry_date']} · {t['day_of_week']} · {t['entry_time']} ET"
                f"{' · SPX ' + str(t['spx_at_entry']) if t['spx_at_entry'] else ''}</span>",
                unsafe_allow_html=True,
            )
            st.markdown("")

            # close_type is needed in multiple tabs — resolve once here
            _close_type = get_close_type(t)

            tabs = st.tabs(["Initial Position", "Transformation / Close", "Iron Condor", "Expiration", "Notes"])

            # ── Tab 0: Initial Position ───────────────────────────────────
            with tabs[0]:
                df = legs_df(t["initial_legs"])
                if df is not None:
                    st.dataframe(df, use_container_width=True, hide_index=True)
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Debit / share", f"−${fmt_f2(t['total_debit'])}")
                c2.metric("Total Debit / contract", f"−${t['total_debit']*100*t['contracts']:.0f}")
                c3.metric("Contracts", t["contracts"])
                if t["spx_at_entry"]:
                    st.markdown(f"**SPX at Entry:** `{t['spx_at_entry']:.2f}`")

            # ── Tab 1: Transformation / Close ─────────────────────────────
            with tabs[1]:
                if not t["transform_date"]:
                    st.info("Not yet closed or transformed. Use **🔄 Close / Transform** in the sidebar.")

                elif _close_type == "direct":
                    # ── Direct-Close display ──────────────────────────────
                    st.info("ℹ️ This position was closed directly — no Iron Condor transformation.")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Close Date", t["transform_date"])
                    c2.metric("Close Time (ET)", f"{t['transform_time']} ET" if t["transform_time"] else "—")
                    c3.metric("SPX at Close", fmt_f2(t["spx_at_transform"]) if t["spx_at_transform"] else "—")
                    st.markdown("")
                    c1, c2, c3, c4 = st.columns(4)
                    net_proceeds = t["credit_received"] or 0.0
                    c1.metric("Net Proceeds / share",
                              f"+{fmt_f2(net_proceeds)}" if net_proceeds >= 0 else f"−{fmt_f2(abs(net_proceeds))}")
                    c2.metric("Entry Debit / share", f"−{fmt_f2(t['total_debit'])}")
                    c3.metric("Net P&L / share", fmt_pl(t["profit_locked_in"], 2))
                    _dc_comm = t["transform_commissions"] if "transform_commissions" in t.keys() else None
                    c4.metric("Commissions", f"${fmt_f2(_dc_comm)}" if _dc_comm else "—")
                    if t["profit_locked_in"] is not None:
                        st.metric("Net P&L / contract",
                                  fmt_pl(t["profit_locked_in"] * 100 * t["contracts"]))

                    st.markdown("")
                    st.markdown("---")
                    btn_c1, btn_c2, _ = st.columns([1, 1, 4])
                    if btn_c1.button("✏️ Edit Close", key=f"edit_tf_{t['trade_id']}",
                                     use_container_width=True):
                        st.session_state["edit_transform_id"] = t["trade_id"]
                        st.session_state["confirm_delete_transform_id"] = None
                        st.session_state["_pending_close_mode"] = "Close Position Directly"
                        st.session_state["_pending_nav"] = "🔄 Close / Transform"
                        st.rerun()
                    if btn_c2.button("🗑️ Delete Close Record", key=f"del_tf_{t['trade_id']}",
                                     use_container_width=True):
                        st.session_state["confirm_delete_transform_id"] = t["trade_id"]

                    if st.session_state["confirm_delete_transform_id"] == t["trade_id"]:
                        st.warning(
                            f"⚠️ Delete the close record for **{t['trade_id']}**? "
                            "The trade will be reset to Open."
                        )
                        dc1, dc2, _ = st.columns([1, 1, 4])
                        if dc1.button("Confirm Delete", type="primary", key="btn_confirm_del_tf"):
                            db.update_trade(config.DB_PATH, t["trade_id"],
                                            status="Open", close_type=None,
                                            transform_date=None, transform_time=None,
                                            transform_minutes=None, spx_at_transform=None,
                                            transform_legs=None, credit_received=None,
                                            profit_locked_in=None, transform_commissions=None,
                                            result_date=None, final_pl=None, outcome=None,
                                            ic_expiry_date=None, ic_short_call=None,
                                            ic_long_call=None, ic_short_put=None,
                                            ic_long_put=None, ic_call_wing=None,
                                            ic_put_wing=None, ic_max_profit=None,
                                            ic_worst_case=None, ic_risk_free=None)
                            st.session_state["confirm_delete_transform_id"] = None
                            st.session_state["_success_msg"] = (
                                f"Close record for {t['trade_id']} deleted. Trade reset to Open."
                            )
                            st.rerun()
                        if dc2.button("Cancel", key="btn_cancel_del_tf"):
                            st.session_state["confirm_delete_transform_id"] = None
                            st.rerun()

                else:
                    # ── IC Transformation display ─────────────────────────
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Date", t["transform_date"])
                    c2.metric("Time", f"{t['transform_time']} ET")
                    c3.metric("Hold Time", f"{t['transform_minutes']}m")
                    c4.metric("SPX", t["spx_at_transform"] or "—")
                    st.markdown("")
                    df = legs_df(t["transform_legs"])
                    if df is not None:
                        st.dataframe(df, use_container_width=True, hide_index=True)
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Credit Received / share", f"+${fmt_f2(t['credit_received'])}")
                    c2.metric("Initial Debit / share", f"−${fmt_f2(t['total_debit'])}")
                    c3.metric("Net Profit Locked In / share", f"+${fmt_f2(t['profit_locked_in'])}")
                    _tf_comm = t["transform_commissions"] if "transform_commissions" in t.keys() else None
                    c4.metric("Transform Commissions", f"${fmt_f2(_tf_comm)}" if _tf_comm else "—")
                    st.metric("Net Profit Locked In / contract",
                              f"+${t['profit_locked_in']*100*t['contracts']:.0f}")

                    st.markdown("")
                    st.markdown("---")
                    btn_c1, btn_c2, _ = st.columns([1, 1, 4])
                    if btn_c1.button("✏️ Edit Transformation", key=f"edit_tf_{t['trade_id']}",
                                     use_container_width=True):
                        st.session_state["edit_transform_id"] = t["trade_id"]
                        st.session_state["confirm_delete_transform_id"] = None
                        st.session_state["_pending_close_mode"] = "Transform to Iron Condor"
                        st.session_state["_pending_nav"] = "🔄 Close / Transform"
                        st.rerun()
                    if btn_c2.button("🗑️ Delete Transformation", key=f"del_tf_{t['trade_id']}",
                                     use_container_width=True):
                        st.session_state["confirm_delete_transform_id"] = t["trade_id"]

                    if st.session_state["confirm_delete_transform_id"] == t["trade_id"]:
                        st.warning(
                            f"⚠️ Delete the transformation for **{t['trade_id']}**? "
                            "The trade will be reset to Open and all IC data will be cleared."
                        )
                        dc1, dc2, _ = st.columns([1, 1, 4])
                        if dc1.button("Confirm Delete", type="primary", key="btn_confirm_del_tf"):
                            db.update_trade(config.DB_PATH, t["trade_id"],
                                            status="Open", close_type=None,
                                            transform_date=None, transform_time=None,
                                            transform_minutes=None, spx_at_transform=None,
                                            transform_legs=None, credit_received=None,
                                            profit_locked_in=None, transform_commissions=None,
                                            ic_expiry_date=None, ic_short_call=None,
                                            ic_long_call=None, ic_short_put=None,
                                            ic_long_put=None, ic_call_wing=None,
                                            ic_put_wing=None, ic_max_profit=None,
                                            ic_worst_case=None, ic_risk_free=None)
                            st.session_state["confirm_delete_transform_id"] = None
                            st.session_state["_success_msg"] = (
                                f"Transformation for {t['trade_id']} deleted. Trade reset to Open."
                            )
                            st.rerun()
                        if dc2.button("Cancel", key="btn_cancel_del_tf"):
                            st.session_state["confirm_delete_transform_id"] = None
                            st.rerun()

            # ── Tab 2: Iron Condor ────────────────────────────────────────
            with tabs[2]:
                if _close_type == "direct":
                    st.info("N/A — this position was closed directly without transformation to an Iron Condor.")
                elif not t["ic_short_call"]:
                    st.info("No Iron Condor yet.")
                else:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Long Put", f"{int(t['ic_long_put'])}")
                    c2.metric("Short Put", f"{int(t['ic_short_put'])}")
                    c3.metric("Short Call", f"{int(t['ic_short_call'])}")
                    c4.metric("Long Call", f"{int(t['ic_long_call'])}")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Put Wing", f"{int(t['ic_put_wing'])} pts")
                    c2.metric("Call Wing", f"{int(t['ic_call_wing'])} pts")
                    c3.metric("Max Profit", f"+${t['ic_max_profit']:.0f}")
                    wc_label = "Worst Case (Guaranteed Profit)" if t["ic_risk_free"] else "Max Loss"
                    c4.metric(wc_label, f"{'+'if t['ic_risk_free'] else '−'}${t['ic_worst_case']:.0f}")
                    if t["ic_risk_free"]:
                        st.success(
                            f"✓ Risk-Free Structure — locked credit "
                            f"(+${t['profit_locked_in']*100*t['contracts']:.0f}/contract) "
                            f"exceeds max IC loss "
                            f"(${max(t['ic_call_wing'], t['ic_put_wing'])*100*t['contracts']:.0f}/contract)."
                        )
                    st.markdown("**P&L by Expiry Zone**")
                    sc = t["ic_short_call"]; lc = t["ic_long_call"]
                    sp = t["ic_short_put"]; lp = t["ic_long_put"]
                    mp = t["ic_max_profit"]; wc = t["ic_worst_case"]
                    rf_flag = bool(t["ic_risk_free"])
                    zone_data = [
                        {"Zone": f"SPX > {int(lc)} (call wing exceeded)",
                         "P&L": f"+${wc:.0f}" if rf_flag else f"−${wc:.0f}"},
                        {"Zone": f"{int(sc)} – {int(lc)} (call partial)",
                         "P&L": f"+${wc:.0f} → +${mp:.0f}" if rf_flag else f"$0 → +${mp:.0f}"},
                        {"Zone": f"{int(sp)} – {int(sc)} ★ MAX PROFIT ZONE", "P&L": f"+${mp:.0f}"},
                        {"Zone": f"{int(lp)} – {int(sp)} (put partial)",
                         "P&L": f"+${wc:.0f} → +${mp:.0f}" if rf_flag else f"$0 → +${mp:.0f}"},
                        {"Zone": f"SPX < {int(lp)} (put wing exceeded)",
                         "P&L": f"+${wc:.0f}" if rf_flag else f"−${wc:.0f}"},
                    ]
                    st.dataframe(pd.DataFrame(zone_data), use_container_width=True, hide_index=True)
                    st.markdown("")
                    st.markdown("**Live IC Marks** *(from option_rows — latest snapshot)*")
                    marks = db.get_ic_marks(
                        config.DB_PATH, t["ic_expiry_date"],
                        t["ic_short_call"], t["ic_long_call"],
                        t["ic_short_put"], t["ic_long_put"],
                    )
                    if marks:
                        ctc = marks["cost_to_close"]
                        locked = t["profit_locked_in"] or 0.0
                        unreal_sh = locked - ctc
                        unreal_ct = unreal_sh * 100 * t["contracts"]
                        st.caption(f"Snapshot: {marks['snapshot_ts']} UTC · SPX: {marks['spx']:.2f}")
                        mark_rows = [
                            {"Leg": f"Short Call {int(t['ic_short_call'])}C", "Role": "Short",
                             "Bid": fmt_f2(marks["short_call_bid"]), "Ask": fmt_f2(marks["short_call_ask"]),
                             "Mark": fmt_f2(marks["short_call_mark"])},
                            {"Leg": f"Long Call {int(t['ic_long_call'])}C", "Role": "Long",
                             "Bid": fmt_f2(marks["long_call_bid"]), "Ask": fmt_f2(marks["long_call_ask"]),
                             "Mark": fmt_f2(marks["long_call_mark"])},
                            {"Leg": f"Short Put {int(t['ic_short_put'])}P", "Role": "Short",
                             "Bid": fmt_f2(marks["short_put_bid"]), "Ask": fmt_f2(marks["short_put_ask"]),
                             "Mark": fmt_f2(marks["short_put_mark"])},
                            {"Leg": f"Long Put {int(t['ic_long_put'])}P", "Role": "Long",
                             "Bid": fmt_f2(marks["long_put_bid"]), "Ask": fmt_f2(marks["long_put_ask"]),
                             "Mark": fmt_f2(marks["long_put_mark"])},
                        ]
                        st.dataframe(pd.DataFrame(mark_rows), use_container_width=True, hide_index=True)
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Cost to Close / share", f"${ctc:.2f}")
                        c2.metric("Unrealized P&L / share", fmt_pl(unreal_sh, 2))
                        c3.metric("Unrealized P&L / contract", fmt_pl(unreal_ct))
                    else:
                        st.warning("No option_rows data found for IC strikes.")
                    entry_d = t["entry_date"]
                    if entry_d < date.today().isoformat():
                        st.markdown(f"**EOD Unrealized P&L — {entry_d}**")
                        eod = db.get_ic_marks(
                            config.DB_PATH, t["ic_expiry_date"],
                            t["ic_short_call"], t["ic_long_call"],
                            t["ic_short_put"], t["ic_long_put"],
                            eod_date=entry_d,
                        )
                        if eod:
                            eod_ctc = eod["cost_to_close"]
                            eod_un_sh = (t["profit_locked_in"] or 0) - eod_ctc
                            c1, c2, c3 = st.columns(3)
                            c1.metric("EOD Cost to Close / share", f"${eod_ctc:.2f}")
                            c2.metric("EOD Unrealized / share", fmt_pl(eod_un_sh, 2))
                            c3.metric("EOD Unrealized / contract",
                                      fmt_pl(eod_un_sh * 100 * t["contracts"]))
                            st.caption(f"Snapshot: {eod['snapshot_ts']} UTC · SPX: {eod['spx']:.2f}")
                        else:
                            st.caption("No EOD snapshot for entry date.")

            # ── Tab 3: Expiration ─────────────────────────────────────────
            with tabs[3]:
                if not t["result_date"]:
                    if t["ic_expiry_date"] and t["ic_expiry_date"] <= date.today().isoformat():
                        spx_s = db.get_eod_spx(config.DB_PATH, t["ic_expiry_date"])
                        if spx_s:
                            st.info(f"ℹ️ IC expired {t['ic_expiry_date']}. "
                                    f"Last recorded SPX: **{spx_s:.2f}**. "
                                    f"Use **⏰ Mark Expired** to record the result.")
                        else:
                            st.info("Not yet expired. Use **⏰ Mark Expired** in the sidebar.")
                    else:
                        st.info("Not yet expired / closed.")
                else:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Result Date", t["result_date"])
                    c2.metric("SPX at Expiry", f"{t['spx_at_expiry']:.2f}" if t["spx_at_expiry"] else "—")
                    c3.metric("Final P&L / contract", fmt_pl(t["final_pl"]))
                    c4.metric("Outcome", t["outcome"] or "—")
                    if t.get("expired_inside_wings") is not None:
                        c1, c2 = st.columns(2)
                        c1.metric("Expired Inside Wings", bool_icon(t["expired_inside_wings"]))
                        c2.metric("Expired Between Shorts", bool_icon(t["expired_between_shorts"]))
                    if t["ic_max_profit"] and t["final_pl"] is not None:
                        ror = t["final_pl"] / t["ic_max_profit"] * 100 if t["ic_max_profit"] else None
                        if ror:
                            st.metric("Return on Max Profit", fmt_pct(ror))

            # ── Tab 4: Notes ──────────────────────────────────────────────
            with tabs[4]:
                st.markdown(t["notes"] or "*No notes yet. Use ✏️ Edit Notes in the sidebar.*")
                st.caption(f"Last updated: {t['updated_at']} UTC")

# ─────────────────────────────────────────────────────────────────────────────
# ── MODE: LOG A TRADE
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "➕ Log a Trade":

    edit_id = st.session_state.get("edit_trade_id")
    is_edit = edit_id is not None
    edit_trade = db.get_trade(config.DB_PATH, edit_id) if is_edit else None

    if is_edit and edit_trade:
        tc, cc = st.columns([5, 1])
        tc.subheader(f"Log a Trade — Editing {edit_id}")
        tc.caption("Modifying the initial entry. Transformation, IC, and expiration data are unchanged.")
        if cc.button("← Cancel", key="cancel_edit"):
            st.session_state["edit_trade_id"] = None
            st.session_state["_pending_nav"] = "📊 Overview"
            st.rerun()
    else:
        st.subheader("Log a Trade")
        next_id = db.get_next_trade_id(config.DB_PATH)
        st.caption(f"Next trade ID: **{next_id}**")

    existing_legs: list[dict] = []
    if is_edit and edit_trade and edit_trade["initial_legs"]:
        try:
            existing_legs = json.loads(edit_trade["initial_legs"])
        except Exception:
            existing_legs = []
    while len(existing_legs) < 4:
        existing_legs.append({"expiry": date.today().isoformat(), "type": "Call",
                               "action": "Sell to Open", "strike": 0.0, "fill": 0.0})

    form_key = f"trade_form_{edit_id or 'new'}"
    with st.form(form_key):
        st.markdown("**Trade Summary**")
        c1, c2, c3, c4 = st.columns(4)
        _et = edit_trade if is_edit and edit_trade else None
        entry_date  = c1.date_input("Entry Date",
                                    value=date.fromisoformat(_et["entry_date"]) if _et else date.today())
        entry_time  = c2.text_input("Entry Time (ET)",
                                    value=(_et["entry_time"] or "") if _et else "", placeholder="09:34")
        spx_entry   = c3.number_input("SPX at Entry", min_value=0.0, step=0.01,
                                      value=float(_et["spx_at_entry"] or 0.0) if _et else 0.0)
        contracts   = c4.number_input("Contracts", min_value=1, step=1,
                                      value=int(_et["contracts"]) if _et else 1)
        c1, c2 = st.columns(2)
        total_debit  = c1.number_input("Total Debit / share ($)", min_value=0.0, step=0.01,
                                       value=float(_et["total_debit"]) if _et else 0.0)
        commissions  = c2.number_input("Commissions / fees ($, optional)", min_value=0.0, step=0.01,
                                       value=float(_et["commissions"] or 0.0) if _et else 0.0)
        st.markdown("**Initial Legs** — 4 required (2 front-month short, 2 back-month long)")
        ACTION_OPTS = ["Sell to Open", "Buy to Open", "Sell to Close", "Buy to Close"]
        leg_data = []
        for i in range(4):
            leg = existing_legs[i]
            st.markdown(f"*Leg {i+1}*")
            c1, c2, c3, c4, c5 = st.columns([2, 1, 2, 1, 1])
            try:
                leg_date = date.fromisoformat(leg["expiry"])
            except Exception:
                leg_date = date.today()
            expiry = c1.date_input("Expiry", value=leg_date, key=f"{form_key}_l{i}_exp")
            ltype  = c2.selectbox("Type", ["Call", "Put"],
                                  index=["Call", "Put"].index(leg.get("type", "Call")),
                                  key=f"{form_key}_l{i}_type")
            av = leg.get("action", "Sell to Open")
            action = c3.selectbox("Action", ACTION_OPTS,
                                  index=ACTION_OPTS.index(av) if av in ACTION_OPTS else 0,
                                  key=f"{form_key}_l{i}_act")
            strike = c4.number_input("Strike", min_value=0.0, step=1.0,
                                     value=float(leg.get("strike", 0.0)), key=f"{form_key}_l{i}_str")
            fill   = c5.number_input("Fill", min_value=0.0, step=0.01,
                                     value=float(leg.get("fill", 0.0)), key=f"{form_key}_l{i}_fill")
            leg_data.append({"expiry": expiry.isoformat(), "type": ltype,
                             "action": action, "strike": strike, "fill": fill})
        st.markdown("**Notes**")
        notes = st.text_area("Trade notes (rationale, market context, observations)",
                             value=(_et["notes"] or "") if _et else "", height=100)

        if st.form_submit_button("💾 Save Changes" if is_edit else "💾 Save Trade",
                                 use_container_width=True):
            if total_debit <= 0:
                st.error("Total debit must be greater than 0.")
            elif not entry_time:
                st.error("Entry time is required (HH:MM format).")
            else:
                fields = {
                    "entry_date":   entry_date.isoformat(),
                    "entry_time":   entry_time,
                    "day_of_week":  DAYS[(entry_date.weekday() + 1) % 7],
                    "spx_at_entry": spx_entry if spx_entry > 0 else None,
                    "contracts":    int(contracts),
                    "commissions":  commissions if commissions > 0 else None,
                    "initial_legs": json.dumps(leg_data),
                    "total_debit":  total_debit,
                    "notes":        notes or None,
                }
                if is_edit:
                    db.update_trade(config.DB_PATH, edit_id, **fields)
                    st.session_state["edit_trade_id"] = None
                    st.session_state["_pending_nav"] = "📊 Overview"
                    st.session_state["_success_msg"] = "Changes saved successfully."
                else:
                    fields["trade_id"] = next_id
                    fields["status"]   = "Open"
                    db.insert_trade(config.DB_PATH, fields)
                    st.session_state["_success_msg"] = "Trade logged successfully."
                st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# ── MODE: CLOSE / TRANSFORM
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "🔄 Close / Transform":

    edit_tf_id = st.session_state.get("edit_transform_id")
    is_tf_edit = edit_tf_id is not None
    edit_tf_trade = db.get_trade(config.DB_PATH, edit_tf_id) if is_tf_edit else None

    if is_tf_edit and edit_tf_trade:
        tc, cc = st.columns([5, 1])
        tc.subheader(f"Close / Transform — Editing {edit_tf_id}")
        tc.caption("Modifying close or transformation record.")
        if cc.button("← Cancel", key="cancel_tf_edit"):
            st.session_state["edit_transform_id"] = None
            st.session_state["_pending_nav"] = "📊 Overview"
            st.rerun()
        base = edit_tf_trade
        chosen_id = edit_tf_id
    else:
        st.subheader("Close / Transform")
        if not open_trades:
            st.info("No Open trades to close or transform. Add a trade first.")
            st.stop()
        trade_map = {t["trade_id"]: t for t in open_trades}
        chosen_id = st.selectbox("Select Open Trade", list(trade_map.keys()))
        base = trade_map[chosen_id]

    st.caption(
        f"Entry: {base['entry_date']} {base['entry_time']} ET · "
        f"Debit: ${base['total_debit']:.2f}/sh"
    )

    # ── Close mode toggle ─────────────────────────────────────────────────────
    close_mode = st.radio(
        "How are you closing this trade?",
        ["Transform to Iron Condor", "Close Position Directly"],
        horizontal=True,
        key="close_mode_radio",
    )
    st.markdown("")

    # ═════════════════════════════════════════════════════════════════════════
    # BRANCH A: Close Position Directly
    # ═════════════════════════════════════════════════════════════════════════

    if close_mode == "Close Position Directly":

        _etf = edit_tf_trade if is_tf_edit and edit_tf_trade else None
        _is_direct_edit = (
            is_tf_edit and _etf and get_close_type(_etf) == "direct"
        )

        default_dc_date = (date.fromisoformat(_etf["transform_date"])
                           if _is_direct_edit and _etf["transform_date"] else date.today())
        default_dc_time = (_etf["transform_time"] or "") if _is_direct_edit else ""
        default_dc_spx  = float(_etf["spx_at_transform"] or 0.0) if _is_direct_edit else 0.0
        default_dc_net  = float(_etf["credit_received"] or 0.0) if _is_direct_edit else 0.0
        default_dc_comm = float(
            _etf["transform_commissions"] or 0.0
        ) if (_is_direct_edit and _etf and "transform_commissions" in _etf.keys()
              and _etf["transform_commissions"]) else 0.0

        dc_form_key = f"dc_form_{edit_tf_id or chosen_id}"

        with st.form(dc_form_key):
            st.markdown("**Close Details**")
            c1, c2, c3 = st.columns(3)
            close_date     = c1.date_input("Close Date", value=default_dc_date)
            close_time     = c2.text_input("Close Time (ET)", value=default_dc_time, placeholder="09:47")
            spx_close      = c3.number_input("SPX at Close", min_value=0.0, step=0.01, value=default_dc_spx)

            c1, c2 = st.columns(2)
            net_credit = c1.number_input(
                "Net Proceeds / share ($)",
                step=0.01, value=default_dc_net,
                help="Net credit received from closing all legs. "
                     "Positive = net credit (profitable close). "
                     "Negative = net debit paid to close (loss).",
            )
            dc_commissions = c2.number_input(
                "Commissions / fees ($, optional)",
                min_value=0.0, step=0.01, value=default_dc_comm,
            )

            if net_credit != 0.0:
                locked = net_credit - base["total_debit"]
                pnl_ct = locked * 100 * int(base["contracts"])
                color  = "#4ade80" if locked >= 0 else "#f87171"
                st.markdown(
                    f"<span style='color:{color};font-family:monospace'>"
                    f"Net P&L / share: {'+'if locked>=0 else ''}${locked:.2f} &nbsp;·&nbsp; "
                    f"Net P&L / contract: {'+'if pnl_ct>=0 else ''}${pnl_ct:.0f}</span>",
                    unsafe_allow_html=True,
                )

            submit_lbl = "💾 Save Changes" if is_tf_edit else "💾 Record Close"
            if st.form_submit_button(submit_lbl, use_container_width=True):
                if not close_time:
                    st.error("Close time is required.")
                else:
                    locked = net_credit - base["total_debit"]
                    final_pl_ct = locked * 100 * int(base["contracts"])
                    outcome = (
                        "Closed at Profit" if locked > 0
                        else ("Break Even" if locked == 0 else "Closed at Loss")
                    )
                    updates = {
                        "status":                "Closed",
                        "close_type":            "direct",
                        "transform_date":        close_date.isoformat(),
                        "transform_time":        close_time,
                        "spx_at_transform":      spx_close if spx_close > 0 else None,
                        "credit_received":       net_credit,
                        "profit_locked_in":      locked,
                        "transform_commissions": dc_commissions if dc_commissions > 0 else None,
                        "result_date":           close_date.isoformat(),
                        "final_pl":              final_pl_ct,
                        "outcome":               outcome,
                    }
                    db.update_trade(config.DB_PATH, chosen_id, **updates)
                    if is_tf_edit:
                        st.session_state["edit_transform_id"] = None
                        st.session_state["_pending_nav"] = "📊 Overview"
                        st.session_state["_success_msg"] = "Close record updated successfully."
                    else:
                        st.session_state["_success_msg"] = (
                            f"Position closed for {chosen_id}. "
                            f"Net P&L: {'+'if locked>=0 else ''}${locked:.2f}/sh ({outcome})."
                        )
                    st.rerun()

    # ═════════════════════════════════════════════════════════════════════════
    # BRANCH B: Transform to Iron Condor
    # ═════════════════════════════════════════════════════════════════════════

    else:
        existing_tf_legs: list[dict] = []
        if is_tf_edit and edit_tf_trade and edit_tf_trade["transform_legs"]:
            try:
                existing_tf_legs = json.loads(edit_tf_trade["transform_legs"])
            except Exception:
                existing_tf_legs = []
        while len(existing_tf_legs) < 4:
            existing_tf_legs.append({"expiry": date.today().isoformat(), "type": "Call",
                                      "action": "Sell to Close", "strike": 0.0, "fill": 0.0})

        tf_form_key = f"tf_form_{edit_tf_id or 'new'}"
        _etf = edit_tf_trade if is_tf_edit and edit_tf_trade else None

        with st.form(tf_form_key):
            st.markdown("**Transformation Details**")
            c1, c2, c3, c4 = st.columns(4)
            default_tf_date = (date.fromisoformat(_etf["transform_date"])
                               if _etf and _etf["transform_date"] else date.today())
            default_tf_time = (_etf["transform_time"] or "") if _etf else ""
            default_spx_tf  = float(_etf["spx_at_transform"] or 0.0) if _etf else 0.0
            default_credit  = float(_etf["credit_received"] or 0.0) if _etf else 0.0
            default_tf_comm = float(_etf["transform_commissions"] or 0.0) if (
                _etf and "transform_commissions" in _etf.keys() and _etf["transform_commissions"]
            ) else 0.0

            tf_date = c1.date_input("Transform Date", value=default_tf_date)
            tf_time = c2.text_input("Transform Time (ET)", value=default_tf_time, placeholder="09:47")
            spx_tf  = c3.number_input("SPX at Transform", min_value=0.0, step=0.01, value=default_spx_tf)
            credit  = c4.number_input("Credit Received / share ($)", min_value=0.0, step=0.01,
                                      value=default_credit)
            c1, _ = st.columns(2)
            tf_commissions = c1.number_input("Commissions / fees ($, optional)",
                                              min_value=0.0, step=0.01, value=default_tf_comm)
            if credit > 0:
                locked = credit - base["total_debit"]
                st.markdown(
                    f"<span style='color:#a78bfa;font-family:monospace'>"
                    f"Net Locked In: {'+'if locked>=0 else ''}${locked:.2f}/sh · "
                    f"{'+'if locked>=0 else ''}${locked*100*base['contracts']:.0f}/contract</span>",
                    unsafe_allow_html=True,
                )

            st.markdown("**Transformation Legs** (Sell to Close back longs + Buy to Open protective wings)")
            TF_ACTIONS = ["Sell to Close", "Buy to Open", "Buy to Close", "Sell to Open"]
            tf_legs = []
            for i in range(4):
                leg = existing_tf_legs[i]
                st.markdown(f"*Leg {i+1}*")
                c1, c2, c3, c4, c5 = st.columns([2, 1, 2, 1, 1])
                try:
                    leg_date = date.fromisoformat(leg["expiry"])
                except Exception:
                    leg_date = date.today()
                expiry = c1.date_input("Expiry", value=leg_date, key=f"{tf_form_key}_l{i}_exp")
                ltype  = c2.selectbox("Type", ["Call", "Put"],
                                      index=["Call", "Put"].index(leg.get("type", "Call")),
                                      key=f"{tf_form_key}_l{i}_type")
                av = leg.get("action", "Sell to Close")
                action = c3.selectbox("Action", TF_ACTIONS,
                                      index=TF_ACTIONS.index(av) if av in TF_ACTIONS else 0,
                                      key=f"{tf_form_key}_l{i}_act")
                strike = c4.number_input("Strike", min_value=0.0, step=1.0,
                                         value=float(leg.get("strike", 0.0)), key=f"{tf_form_key}_l{i}_str")
                fill   = c5.number_input("Fill", min_value=0.0, step=0.01,
                                         value=float(leg.get("fill", 0.0)), key=f"{tf_form_key}_l{i}_fill")
                tf_legs.append({"expiry": expiry.isoformat(), "type": ltype,
                                 "action": action, "strike": strike, "fill": fill})

            submit_lbl = "💾 Save Changes" if is_tf_edit else "💾 Save Transformation"
            if st.form_submit_button(submit_lbl, use_container_width=True):
                if credit <= 0:
                    st.error("Credit received must be > 0.")
                elif not tf_time:
                    st.error("Transform time is required.")
                else:
                    try:
                        entry_dt = datetime.strptime(
                            f"{base['entry_date']} {base['entry_time']}", "%Y-%m-%d %H:%M")
                        tf_dt = datetime.strptime(
                            f"{tf_date.isoformat()} {tf_time}", "%Y-%m-%d %H:%M")
                        mins = max(0, int((tf_dt - entry_dt).total_seconds() / 60))
                    except Exception:
                        mins = 0
                    locked = credit - base["total_debit"]
                    ic = derive_ic(base["initial_legs"], tf_legs, credit,
                                   base["total_debit"], base["contracts"])
                    updates = {
                        "status":                "Transformed",
                        "close_type":            "transform",
                        "transform_date":        tf_date.isoformat(),
                        "transform_time":        tf_time,
                        "transform_minutes":     mins,
                        "spx_at_transform":      spx_tf if spx_tf > 0 else None,
                        "transform_legs":        json.dumps(tf_legs),
                        "credit_received":       credit,
                        "profit_locked_in":      locked,
                        "transform_commissions": tf_commissions if tf_commissions > 0 else None,
                    }
                    if ic:
                        updates.update(ic)
                    db.update_trade(config.DB_PATH, chosen_id, **updates)
                    if is_tf_edit:
                        st.session_state["edit_transform_id"] = None
                        st.session_state["_pending_nav"] = "📊 Overview"
                        st.session_state["_success_msg"] = "Transformation updated successfully."
                    else:
                        ic_note = (
                            f" IC: Max Profit ${ic['ic_max_profit']:.0f} · "
                            f"{'⚡ Risk-Free' if ic['ic_risk_free'] else 'Not risk-free'}."
                            if ic else ""
                        )
                        st.session_state["_success_msg"] = (
                            f"Transformation recorded for {chosen_id}.{ic_note}"
                        )
                    st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# ── MODE: MARK EXPIRED
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "⏰ Mark Expired":
    st.subheader("Mark Trade as Expired")
    if not active_trades:
        st.info("No active trades to expire.")
    else:
        trade_map = {t["trade_id"]: t for t in active_trades}
        chosen_id = st.selectbox("Select Active Trade", list(trade_map.keys()))
        base = trade_map[chosen_id]
        ic_exp = base["ic_expiry_date"]
        spx_s = db.get_eod_spx(config.DB_PATH, ic_exp) if ic_exp else None
        if spx_s:
            st.info(f"📈 Last recorded SPX on IC expiry date ({ic_exp}): **{spx_s:.2f}**")

        with st.form("expire_form"):
            c1, c2, c3 = st.columns(3)
            result_date = c1.date_input(
                "Expiration / Close Date",
                value=date.fromisoformat(ic_exp) if ic_exp else date.today()
            )
            spx_expiry = c2.number_input(
                "SPX at Expiry / Close", min_value=0.0, step=0.01,
                value=float(spx_s) if spx_s else 0.0
            )
            final_pl = c3.number_input("Final P&L / contract ($)", step=1.0)

            outcome = "—"
            exp_inside = exp_shorts = None
            if spx_expiry > 0 and base["ic_short_call"]:
                sc, lc = base["ic_short_call"], base["ic_long_call"]
                sp, lp = base["ic_short_put"], base["ic_long_put"]
                exp_inside  = 1 if (spx_expiry > lp and spx_expiry < lc) else 0
                exp_shorts  = 1 if (spx_expiry >= sp and spx_expiry <= sc) else 0
                outcome = ("Maximum Profit" if exp_shorts
                           else ("Minimum Profit (Risk-Free)" if (not exp_inside and base["ic_risk_free"])
                                 else ("Maximum Loss" if not exp_inside else "Partial Profit")))
            st.markdown(
                f"**Auto-detected outcome:** `{outcome}` · "
                f"Inside wings: {bool_icon(exp_inside)} · Between shorts: {bool_icon(exp_shorts)}"
            )
            if st.form_submit_button("⏰ Confirm Expiry", use_container_width=True):
                db.update_trade(config.DB_PATH, chosen_id,
                                status="Expired",
                                result_date=result_date.isoformat(),
                                spx_at_expiry=spx_expiry if spx_expiry > 0 else None,
                                final_pl=final_pl,
                                expired_inside_wings=exp_inside,
                                expired_between_shorts=exp_shorts,
                                outcome=outcome)
                st.session_state["_success_msg"] = f"{chosen_id} marked as Expired. Outcome: {outcome}"
                st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# ── MODE: EDIT NOTES
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "✏️ Edit Notes":
    st.subheader("Edit Trade Notes")
    if not all_trades:
        st.info("No trades yet.")
    else:
        trade_map = {t["trade_id"]: t for t in all_trades}
        chosen_id = st.selectbox("Select Trade", list(trade_map.keys()))
        base = trade_map[chosen_id]
        with st.form("notes_form"):
            new_notes = st.text_area(
                "Notes", value=base["notes"] or "", height=250,
                placeholder="Why did you enter? Market conditions, observations, lessons learned..."
            )
            if st.form_submit_button("💾 Save Notes", use_container_width=True):
                db.update_trade(config.DB_PATH, chosen_id, notes=new_notes)
                st.session_state["_success_msg"] = f"Notes saved for {chosen_id}."
                st.rerun()

elif page_mode == "📈 Regime Analysis":
    render_regime_analysis(all_trades)
