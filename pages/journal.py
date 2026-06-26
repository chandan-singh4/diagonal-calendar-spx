"""
pages/journal.py — SPX Trade Journal

Streamlit multi-page entry. Streamlit auto-discovers files in pages/ and
adds them to the sidebar navigation. Run the dashboard normally:
    streamlit run app.py
The journal appears as a navigation link automatically.

DATA FLOW
  Reads:  db.trades (trade records), db.option_rows (live IC mark prices)
  Writes: db.trades only — via db.insert_trade, db.update_trade

NEVER touches snapshots, option_rows, atm_iv_by_expiry, or collection_gaps.
Those are owned by collector.py. This page is read-only for market data.

IC MARK PRICE MATH
  IC position: short short_call + short short_put + long long_call + long long_put
  Cost to close = mark(short_call) + mark(short_put) - mark(long_call) - mark(long_put)
  Unrealized P&L per share = profit_locked_in - cost_to_close
  Unrealized P&L per contract = unrealized_per_sh * 100 * contracts
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from datetime import date, datetime, timedelta

import pandas as pd
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
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

DAYS = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]

def day_of_week(date_str: str) -> str:
    try:
        return DAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday() + 1 % 7]
    except Exception:
        return "—"

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
        end   = date.fromisoformat(t["result_date"]) if t["result_date"] else date.today()
        return (end - entry).days
    except Exception:
        return None

def legs_df(legs_json: str | None) -> pd.DataFrame | None:
    if not legs_json:
        return None
    try:
        legs = json.loads(legs_json)
        df = pd.DataFrame(legs)[["expiry","type","action","strike","fill"]]
        df.columns = ["Expiry","Type","Action","Strike","Fill"]
        return df
    except Exception:
        return None

def compute_stats(rows: list) -> dict:
    if not rows:
        return {}
    expired = [r for r in rows if r["status"] == "Expired" and r["final_pl"] is not None]
    pls = [float(r["final_pl"]) for r in expired]
    wins  = [p for p in pls if p > 0]
    loss  = [p for p in pls if p <= 0]
    transformed = [r for r in rows if r["transform_minutes"] is not None]

    total_wins  = sum(wins)
    total_loss  = sum(loss)
    win_rate    = len(wins) / len(pls) * 100 if pls else None
    avg_win     = sum(wins)  / len(wins)  if wins  else None
    avg_loss    = sum(loss)  / len(loss)  if loss  else None
    pf          = total_wins / abs(total_loss) if total_loss != 0 else None
    exp_val     = None
    if win_rate is not None and avg_win is not None and avg_loss is not None:
        exp_val = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)

    hold_days_list = [holding_days(r) for r in expired if holding_days(r) is not None]
    avg_hold = sum(hold_days_list)/len(hold_days_list) if hold_days_list else None

    t_mins = [r["transform_minutes"] for r in transformed if r["transform_minutes"]]
    avg_t_min = sum(t_mins)/len(t_mins) if t_mins else None

    debits  = [float(r["total_debit"]) for r in rows]
    credits = [float(r["credit_received"]) for r in rows if r["credit_received"] is not None]
    fees    = [float(r["commissions"])  for r in rows if r["commissions"]  is not None]

    return {
        "Total Trades":              len(rows),
        "Win Rate":                  win_rate,
        "Average Winner":            avg_win,
        "Average Loser":             avg_loss,
        "Profit Factor":             pf,
        "Expectancy":                exp_val,
        "Avg Entry Debit":           sum(debits)/len(debits)   if debits  else None,
        "Avg Transform Credit":      sum(credits)/len(credits) if credits else None,
        "Avg Holding Time (days)":   avg_hold,
        "Avg Time to Transform":     avg_t_min,
        "Avg Max Drawdown":          None,  # requires intraday data
        "Largest Winner":            max(wins)       if wins else None,
        "Largest Loser":             min(loss)       if loss else None,
        "Total Fees":                sum(fees)       if fees else None,
        "Total Net Profit":          sum(pls)        if pls  else None,
    }

def derive_ic(init_legs_json: str, tf_legs: list, credit: float,
               total_debit: float, contracts: int) -> dict | None:
    """Auto-compute IC structure from initial + transformation legs."""
    try:
        init = json.loads(init_legs_json)
        sc = next((l["strike"] for l in init if "Sell" in l["action"] and l["type"] == "Call"), None)
        sp = next((l["strike"] for l in init if "Sell" in l["action"] and l["type"] == "Put"), None)
        lc = next((l["strike"] for l in tf_legs if "Buy" in l["action"] and l["type"] == "Call"), None)
        lp = next((l["strike"] for l in tf_legs if "Buy" in l["action"] and l["type"] == "Put"), None)
        ic_expiry = next((l["expiry"] for l in tf_legs if "Buy" in l["action"]), None)
        if not all([sc, sp, lc, lp, ic_expiry]):
            return None
        c_wing  = abs(float(lc) - float(sc))
        p_wing  = abs(float(sp) - float(lp))
        locked  = credit - total_debit
        max_p   = round(locked * 100 * contracts)
        max_ic  = max(c_wing, p_wing) * 100 * contracts
        risk_f  = max_p > max_ic
        return {
            "ic_expiry_date": ic_expiry,
            "ic_short_call":  float(sc), "ic_long_call": float(lc),
            "ic_short_put":   float(sp), "ic_long_put":  float(lp),
            "ic_call_wing":   c_wing,    "ic_put_wing":  p_wing,
            "ic_max_profit":  float(max_p),
            "ic_worst_case":  float(max_p - max_ic) if risk_f else float(max_ic - max_p),
            "ic_risk_free":   1 if risk_f else 0,
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Load Data
# ─────────────────────────────────────────────────────────────────────────────

all_trades = db.get_all_trades(config.DB_PATH)
open_trades        = [t for t in all_trades if t["status"] == "Open"]
active_trades      = [t for t in all_trades if t["status"] in ("Open", "Transformed")]
transformed_trades = [t for t in all_trades if t["status"] == "Transformed"]

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 📒 Trade Journal")
    st.caption("SPX Diagonal Calendar → Iron Condor")
    st.divider()

    page_mode = st.radio(
        "Navigation",
        ["📊 Overview", "➕ New Trade", "🔄 Record Transformation", "⏰ Mark Expired", "✏️ Edit Notes"],
        label_visibility="collapsed",
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

# ─────────────────────────────────────────────────────────────────────────────
# ── MODE: OVERVIEW
# ─────────────────────────────────────────────────────────────────────────────

if page_mode == "📊 Overview":

    # ── Strategy Statistics ───────────────────────────────────────────────────
    st.subheader("Strategy Statistics")
    stats = compute_stats(list(all_trades))

    def stat_cell(label, val, is_pl=False, is_pct=False, is_time=False, decimals=0):
        if val is None:
            display = "—"
            color   = "color:#64748b"
        elif is_pct:
            display = fmt_pct(val)
            color   = "color:#4ade80" if val >= 50 else "color:#f87171"
        elif is_time:
            display = f"{val:.0f}m" if val < 60 else f"{val/60:.1f}h"
            color   = "color:#e2e8f0"
        elif is_pl:
            display = fmt_pl(val, decimals)
            color   = "color:#4ade80" if val >= 0 else "color:#f87171"
        elif label in ("Profit Factor",):
            display = f"{val:.2f}x"
            color   = "color:#4ade80" if val >= 1.5 else "color:#fbbf24"
        elif label == "Total Trades":
            display = str(int(val))
            color   = "color:#e2e8f0"
        else:
            display = f"{val:.2f}"
            color   = "color:#e2e8f0"
        st.markdown(
            f"<div style='background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px 14px;'>"
            f"<div style='color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px'>{label}</div>"
            f"<div style='font-family:monospace;font-size:18px;font-weight:600;{color}'>{display}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    r1 = st.columns(5)
    r2 = st.columns(5)
    r3 = st.columns(5)

    with r1[0]: stat_cell("Total Trades",          stats.get("Total Trades"))
    with r1[1]: stat_cell("Win Rate",              stats.get("Win Rate"),              is_pct=True)
    with r1[2]: stat_cell("Average Winner",        stats.get("Average Winner"),        is_pl=True)
    with r1[3]: stat_cell("Average Loser",         stats.get("Average Loser"),         is_pl=True)
    with r1[4]: stat_cell("Profit Factor",         stats.get("Profit Factor"))

    with r2[0]: stat_cell("Expectancy",            stats.get("Expectancy"),            is_pl=True)
    with r2[1]: stat_cell("Avg Entry Debit",       stats.get("Avg Entry Debit"),       decimals=2)
    with r2[2]: stat_cell("Avg Transform Credit",  stats.get("Avg Transform Credit"),  decimals=2)
    with r2[3]: stat_cell("Avg Holding (days)",    stats.get("Avg Holding Time (days)"), decimals=1)
    with r2[4]: stat_cell("Avg Time to Transform", stats.get("Avg Time to Transform"), is_time=True)

    with r3[0]: stat_cell("Avg Max Drawdown",      stats.get("Avg Max Drawdown"),      is_pl=True)
    with r3[1]: stat_cell("Largest Winner",        stats.get("Largest Winner"),        is_pl=True)
    with r3[2]: stat_cell("Largest Loser",         stats.get("Largest Loser"),         is_pl=True)
    with r3[3]: stat_cell("Total Fees",            stats.get("Total Fees"),            is_pl=True)
    with r3[4]: stat_cell("Total Net Profit",      stats.get("Total Net Profit"),      is_pl=True)

    st.markdown("<div style='margin-top:4px;color:#475569;font-size:11px'>"
                "Avg Max Drawdown requires intraday mark history — available in a future update.</div>",
                unsafe_allow_html=True)
    st.divider()

    # ── Master Log ────────────────────────────────────────────────────────────
    st.subheader("Master Log")

    if not all_trades:
        st.info("No trades logged yet. Use **➕ New Trade** in the sidebar to add your first trade.")
    else:
        log_rows = []
        for t in all_trades:
            log_rows.append({
                "ID":             t["trade_id"],
                "Date":           t["entry_date"],
                "Day":            t["day_of_week"] or "—",
                "Time (ET)":      t["entry_time"],
                "Status":         t["status"],
                "Qty":            t["contracts"],
                "Debit/sh":       f"−{fmt_f2(t['total_debit'])}",
                "Net Credit/sh":  f"+{fmt_f2(t['profit_locked_in'])}" if t["profit_locked_in"] else "—",
                "IC Max Profit":  fmt_pl(t["ic_max_profit"]) if t["ic_max_profit"] else "—",
                "Worst Case":     (f"+{fmt_pl(t['ic_worst_case'])}" if t["ic_risk_free"] else
                                   f"−{fmt_pl(t['ic_worst_case'])}") if t["ic_worst_case"] else "—",
                "∈ Wings":        bool_icon(t["expired_inside_wings"]),
                "∈ Shorts":       bool_icon(t["expired_between_shorts"]),
                "Outcome":        t["outcome"] or "Pending",
            })
        log_df = pd.DataFrame(log_rows)
        st.dataframe(log_df, use_container_width=True, hide_index=True)

    # ── Trade Detail (if selected) ────────────────────────────────────────────
    if selected_id and selected_id != "—":
        st.divider()
        _show_detail = True
    else:
        _show_detail = False

    if _show_detail:
        t = db.get_trade(config.DB_PATH, selected_id)
        if t:
            st.subheader(f"Trade Detail — {t['trade_id']}")

            # Summary strip
            status_color = {
                "Open": "#3b82f6", "Transformed": "#8b5cf6",
                "Expired": "#10b981", "Closed": "#64748b"
            }.get(t["status"], "#64748b")

            st.markdown(
                f"<span style='background:{status_color}22;color:{status_color};"
                f"border:1px solid {status_color}55;border-radius:4px;padding:2px 10px;"
                f"font-size:12px;font-family:monospace'>{t['status']}</span>"
                f"&nbsp;&nbsp;<span style='color:#94a3b8;font-size:13px;font-family:monospace'>"
                f"{t['entry_date']} · {t['day_of_week']} · {t['entry_time']} ET"
                f"{'  ·  SPX ' + str(t['spx_at_entry']) if t['spx_at_entry'] else ''}</span>",
                unsafe_allow_html=True,
            )
            st.markdown("")

            tab_names = ["Initial Position", "Transformation", "Iron Condor", "Expiration", "Notes"]
            tabs = st.tabs(tab_names)

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

            # ── Tab 1: Transformation ─────────────────────────────────────
            with tabs[1]:
                if not t["transform_date"]:
                    st.info("Not yet transformed. Use **🔄 Record Transformation** in the sidebar.")
                else:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Date",      t["transform_date"])
                    c2.metric("Time",      f"{t['transform_time']} ET")
                    c3.metric("Hold Time", f"{t['transform_minutes']}m")
                    c4.metric("SPX",       t["spx_at_transform"] or "—")
                    st.markdown("")
                    df = legs_df(t["transform_legs"])
                    if df is not None:
                        st.dataframe(df, use_container_width=True, hide_index=True)
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Credit Received / share", f"+${fmt_f2(t['credit_received'])}")
                    c2.metric("Initial Debit / share",   f"−${fmt_f2(t['total_debit'])}")
                    c3.metric("Net Profit Locked In / share", f"+${fmt_f2(t['profit_locked_in'])}")
                    st.metric(
                        "Net Profit Locked In / contract",
                        f"+${t['profit_locked_in']*100*t['contracts']:.0f}"
                    )

            # ── Tab 2: Iron Condor ────────────────────────────────────────
            with tabs[2]:
                if not t["ic_short_call"]:
                    st.info("No Iron Condor yet.")
                else:
                    # IC Structure
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Long Put",   f"{int(t['ic_long_put'])}")
                    c2.metric("Short Put",  f"{int(t['ic_short_put'])}")
                    c3.metric("Short Call", f"{int(t['ic_short_call'])}")
                    c4.metric("Long Call",  f"{int(t['ic_long_call'])}")

                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Put Wing",   f"{int(t['ic_put_wing'])} pts")
                    c2.metric("Call Wing",  f"{int(t['ic_call_wing'])} pts")
                    c3.metric("Max Profit", f"+${t['ic_max_profit']:.0f}")
                    wc_label = "Worst Case (Guaranteed Profit)" if t["ic_risk_free"] else "Max Loss"
                    wc_sign  = "+" if t["ic_risk_free"] else "−"
                    c4.metric(wc_label, f"{wc_sign}${t['ic_worst_case']:.0f}")

                    if t["ic_risk_free"]:
                        st.success(
                            f"✓ Risk-Free Structure — Net locked credit "
                            f"(+${t['profit_locked_in']*100*t['contracts']:.0f}/contract) "
                            f"exceeds maximum IC loss "
                            f"(${max(t['ic_call_wing'], t['ic_put_wing'])*100*t['contracts']:.0f}/contract). "
                            f"Cannot lose money at any expiry."
                        )

                    # P&L Zone Table
                    st.markdown("**P&L by Expiry Zone**")
                    sc = t["ic_short_call"]; lc = t["ic_long_call"]
                    sp = t["ic_short_put"];  lp = t["ic_long_put"]
                    mp = t["ic_max_profit"]; wc = t["ic_worst_case"]
                    rf = bool(t["ic_risk_free"])
                    zone_data = [
                        {"Zone": f"SPX > {int(lc)}  (call wing exceeded)",
                         "P&L": f"+${wc:.0f}" if rf else f"−${wc:.0f}"},
                        {"Zone": f"{int(sc)} – {int(lc)}  (call partial)",
                         "P&L": f"+${wc:.0f} → +${mp:.0f}" if rf else f"$0 → +${mp:.0f}"},
                        {"Zone": f"{int(sp)} – {int(sc)}  ★ MAX PROFIT ZONE",
                         "P&L": f"+${mp:.0f}"},
                        {"Zone": f"{int(lp)} – {int(sp)}  (put partial)",
                         "P&L": f"+${wc:.0f} → +${mp:.0f}" if rf else f"$0 → +${mp:.0f}"},
                        {"Zone": f"SPX < {int(lp)}  (put wing exceeded)",
                         "P&L": f"+${wc:.0f}" if rf else f"−${wc:.0f}"},
                    ]
                    st.dataframe(pd.DataFrame(zone_data), use_container_width=True, hide_index=True)
                    st.markdown("")

                    # ── Live IC Marks from option_rows ─────────────────────
                    st.markdown("**Live IC Marks** *(from option_rows — latest snapshot)*")
                    marks = db.get_ic_marks(
                        config.DB_PATH, t["ic_expiry_date"],
                        t["ic_short_call"], t["ic_long_call"],
                        t["ic_short_put"],  t["ic_long_put"],
                    )
                    if marks:
                        snap_ts  = marks["snapshot_ts"]
                        snap_spx = marks["spx"]
                        ctc      = marks["cost_to_close"]
                        locked   = t["profit_locked_in"] or 0.0
                        unreal_sh  = locked - ctc
                        unreal_ct  = unreal_sh * 100 * t["contracts"]

                        st.caption(f"Snapshot: {snap_ts} UTC  ·  SPX: {snap_spx:.2f}")

                        mark_rows = [
                            {"Leg":    f"Short Call {int(t['ic_short_call'])}C",
                             "Role":   "Short (sold)",
                             "Bid":    fmt_f2(marks["short_call_bid"]),
                             "Ask":    fmt_f2(marks["short_call_ask"]),
                             "Mark":   fmt_f2(marks["short_call_mark"])},
                            {"Leg":    f"Long Call  {int(t['ic_long_call'])}C",
                             "Role":   "Long (bought)",
                             "Bid":    fmt_f2(marks["long_call_bid"]),
                             "Ask":    fmt_f2(marks["long_call_ask"]),
                             "Mark":   fmt_f2(marks["long_call_mark"])},
                            {"Leg":    f"Short Put  {int(t['ic_short_put'])}P",
                             "Role":   "Short (sold)",
                             "Bid":    fmt_f2(marks["short_put_bid"]),
                             "Ask":    fmt_f2(marks["short_put_ask"]),
                             "Mark":   fmt_f2(marks["short_put_mark"])},
                            {"Leg":    f"Long Put   {int(t['ic_long_put'])}P",
                             "Role":   "Long (bought)",
                             "Bid":    fmt_f2(marks["long_put_bid"]),
                             "Ask":    fmt_f2(marks["long_put_ask"]),
                             "Mark":   fmt_f2(marks["long_put_mark"])},
                        ]
                        st.dataframe(pd.DataFrame(mark_rows), use_container_width=True, hide_index=True)

                        c1, c2, c3 = st.columns(3)
                        c1.metric("Cost to Close / share", f"${ctc:.2f}")
                        c2.metric("Unrealized P&L / share", fmt_pl(unreal_sh, 2))
                        c3.metric("Unrealized P&L / contract", fmt_pl(unreal_ct))
                    else:
                        st.warning(
                            f"No option_rows data found for {t['ic_expiry_date']} IC strikes. "
                            "The collector may not have data for this expiry yet."
                        )

                    # ── EOD Marks (entry day, if not today) ────────────────
                    entry_d = t["entry_date"]
                    today_d = date.today().isoformat()
                    if entry_d < today_d:
                        st.markdown(f"**EOD Unrealized P&L — {entry_d}** *(end-of-entry-day marks)*")
                        eod = db.get_ic_marks(
                            config.DB_PATH, t["ic_expiry_date"],
                            t["ic_short_call"], t["ic_long_call"],
                            t["ic_short_put"],  t["ic_long_put"],
                            eod_date=entry_d,
                        )
                        if eod:
                            eod_ctc    = eod["cost_to_close"]
                            eod_un_sh  = (t["profit_locked_in"] or 0) - eod_ctc
                            eod_un_ct  = eod_un_sh * 100 * t["contracts"]
                            c1, c2, c3 = st.columns(3)
                            c1.metric("EOD Cost to Close / share", f"${eod_ctc:.2f}")
                            c2.metric("EOD Unrealized / share", fmt_pl(eod_un_sh, 2))
                            c3.metric("EOD Unrealized / contract", fmt_pl(eod_un_ct))
                            st.caption(
                                f"EOD snapshot: {eod['snapshot_ts']} UTC  ·  SPX: {eod['spx']:.2f}"
                            )
                        else:
                            st.caption("No EOD snapshot available for entry date.")

            # ── Tab 3: Expiration ─────────────────────────────────────────
            with tabs[3]:
                if not t["result_date"]:
                    # Auto-suggest SPX close from DB if IC expiry date has passed
                    if t["ic_expiry_date"] and t["ic_expiry_date"] <= date.today().isoformat():
                        spx_suggest = db.get_eod_spx(config.DB_PATH, t["ic_expiry_date"])
                        if spx_suggest:
                            st.info(
                                f"ℹ️ IC expired {t['ic_expiry_date']}. "
                                f"Last recorded SPX on that date: **{spx_suggest:.2f}**. "
                                f"Use **⏰ Mark Expired** in the sidebar to record the result."
                            )
                        else:
                            st.info("Not yet expired. Mark the result using **⏰ Mark Expired** in the sidebar.")
                    else:
                        st.info("Not yet expired.")
                else:
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Result Date",  t["result_date"])
                    c2.metric("SPX at Expiry", f"{t['spx_at_expiry']:.2f}" if t["spx_at_expiry"] else "—")
                    c3.metric("Final P&L / contract", fmt_pl(t["final_pl"]))
                    c4.metric("Outcome", t["outcome"] or "—")

                    c1, c2 = st.columns(2)
                    c1.metric("Expired Inside Wings",    bool_icon(t["expired_inside_wings"]))
                    c2.metric("Expired Between Shorts",  bool_icon(t["expired_between_shorts"]))

                    if t["ic_max_profit"] and t["final_pl"] is not None:
                        ror = t["final_pl"] / t["ic_max_profit"] * 100 if t["ic_max_profit"] else None
                        if ror:
                            st.metric("Return on Max Profit", fmt_pct(ror))

            # ── Tab 4: Notes ──────────────────────────────────────────────
            with tabs[4]:
                st.markdown(t["notes"] or "*No notes yet. Use ✏️ Edit Notes in the sidebar.*")
                st.caption(f"Last updated: {t['updated_at']} UTC")


# ─────────────────────────────────────────────────────────────────────────────
# ── MODE: NEW TRADE
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "➕ New Trade":
    st.subheader("Log New Trade")
    next_id = db.get_next_trade_id(config.DB_PATH)
    st.caption(f"Next trade ID: **{next_id}**")

    with st.form("new_trade_form"):
        st.markdown("**Trade Summary**")
        c1, c2, c3, c4 = st.columns(4)
        entry_date  = c1.date_input("Entry Date",  value=date.today())
        entry_time  = c2.text_input("Entry Time (ET)", placeholder="09:34")
        spx_entry   = c3.number_input("SPX at Entry", min_value=0.0, step=0.01, value=0.0)
        contracts   = c4.number_input("Contracts", min_value=1, step=1, value=1)

        c1, c2 = st.columns(2)
        total_debit = c1.number_input("Total Debit / share ($)", min_value=0.0, step=0.01)
        commissions = c2.number_input("Commissions / fees ($, optional)", min_value=0.0, step=0.01)

        st.markdown("**Initial Legs** — 4 required (2 front-month short, 2 back-month long)")
        leg_data = []
        for i in range(4):
            st.markdown(f"*Leg {i+1}*")
            c1,c2,c3,c4,c5 = st.columns([2,1,2,1,1])
            expiry = c1.date_input(f"Expiry", key=f"l{i}_exp")
            ltype  = c2.selectbox("Type", ["Call","Put"], key=f"l{i}_type")
            action = c3.selectbox("Action", ["Sell to Open","Buy to Open","Sell to Close","Buy to Close"], key=f"l{i}_act")
            strike = c4.number_input("Strike", min_value=0.0, step=1.0, key=f"l{i}_str")
            fill   = c5.number_input("Fill",   min_value=0.0, step=0.01, key=f"l{i}_fill")
            leg_data.append({"expiry": expiry.isoformat(), "type": ltype,
                             "action": action, "strike": strike, "fill": fill})

        st.markdown("**Notes**")
        notes = st.text_area("Trade notes (rationale, market context, observations)", height=100)

        if st.form_submit_button("💾 Save Trade", use_container_width=True):
            if total_debit <= 0:
                st.error("Total debit must be greater than 0.")
            elif not entry_time:
                st.error("Entry time is required (HH:MM format).")
            else:
                trade_dict = {
                    "trade_id":    next_id,
                    "entry_date":  entry_date.isoformat(),
                    "entry_time":  entry_time,
                    "day_of_week": DAYS[(entry_date.weekday() + 1) % 7],
                    "spx_at_entry": spx_entry if spx_entry > 0 else None,
                    "status":      "Open",
                    "contracts":   int(contracts),
                    "commissions": commissions if commissions > 0 else None,
                    "initial_legs": json.dumps(leg_data),
                    "total_debit": total_debit,
                    "notes":       notes or None,
                }
                db.insert_trade(config.DB_PATH, trade_dict)
                st.success(f"✓ Trade {next_id} saved.")
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# ── MODE: RECORD TRANSFORMATION
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "🔄 Record Transformation":
    st.subheader("Record Transformation")

    if not open_trades:
        st.info("No Open trades to transform. Add a trade first.")
    else:
        trade_map = {t["trade_id"]: t for t in open_trades}
        chosen_id = st.selectbox("Select Open Trade", list(trade_map.keys()))
        base = trade_map[chosen_id]

        st.caption(
            f"Entry: {base['entry_date']} {base['entry_time']} ET  ·  "
            f"Debit: ${base['total_debit']:.2f}/sh"
        )

        with st.form("tf_form"):
            st.markdown("**Transformation Details**")
            c1,c2,c3,c4 = st.columns(4)
            tf_date   = c1.date_input("Transform Date", value=date.today())
            tf_time   = c2.text_input("Transform Time (ET)", placeholder="09:47")
            spx_tf    = c3.number_input("SPX at Transform", min_value=0.0, step=0.01)
            credit    = c4.number_input("Credit Received / share ($)", min_value=0.0, step=0.01)

            if credit > 0:
                locked = credit - base["total_debit"]
                st.markdown(
                    f"<span style='color:#a78bfa;font-family:monospace'>"
                    f"Net Locked In: {'+'if locked>=0 else ''}${locked:.2f}/sh  ·  "
                    f"{'+'if locked>=0 else ''}${locked*100*base['contracts']:.0f}/contract</span>",
                    unsafe_allow_html=True,
                )

            st.markdown("**Transformation Legs** (Sell to Close back longs + Buy to Open protective wings)")
            tf_legs = []
            for i in range(4):
                st.markdown(f"*Leg {i+1}*")
                c1,c2,c3,c4,c5 = st.columns([2,1,2,1,1])
                expiry = c1.date_input("Expiry", key=f"tf{i}_exp")
                ltype  = c2.selectbox("Type", ["Call","Put"], key=f"tf{i}_type")
                action = c3.selectbox("Action",
                    ["Sell to Close","Buy to Open","Buy to Close","Sell to Open"], key=f"tf{i}_act")
                strike = c4.number_input("Strike", min_value=0.0, step=1.0, key=f"tf{i}_str")
                fill   = c5.number_input("Fill", min_value=0.0, step=0.01, key=f"tf{i}_fill")
                tf_legs.append({"expiry": expiry.isoformat(), "type": ltype,
                                "action": action, "strike": strike, "fill": fill})

            if st.form_submit_button("💾 Save Transformation", use_container_width=True):
                if credit <= 0:
                    st.error("Credit received must be > 0.")
                elif not tf_time:
                    st.error("Transform time is required.")
                else:
                    # Compute minutes since entry
                    try:
                        entry_dt = datetime.strptime(
                            f"{base['entry_date']} {base['entry_time']}", "%Y-%m-%d %H:%M")
                        tf_dt    = datetime.strptime(
                            f"{tf_date.isoformat()} {tf_time}", "%Y-%m-%d %H:%M")
                        mins = max(0, int((tf_dt - entry_dt).total_seconds() / 60))
                    except Exception:
                        mins = 0

                    locked = credit - base["total_debit"]
                    ic = derive_ic(base["initial_legs"], tf_legs, credit,
                                   base["total_debit"], base["contracts"])

                    updates = {
                        "status":             "Transformed",
                        "transform_date":     tf_date.isoformat(),
                        "transform_time":     tf_time,
                        "transform_minutes":  mins,
                        "spx_at_transform":   spx_tf if spx_tf > 0 else None,
                        "transform_legs":     json.dumps(tf_legs),
                        "credit_received":    credit,
                        "profit_locked_in":   locked,
                    }
                    if ic:
                        updates.update(ic)

                    db.update_trade(config.DB_PATH, chosen_id, **updates)
                    st.success(f"✓ Transformation recorded for {chosen_id}.")
                    if ic:
                        st.info(
                            f"IC auto-derived — Max Profit: ${ic['ic_max_profit']:.0f}  ·  "
                            f"Worst Case: {'+'if ic['ic_risk_free'] else '−'}${ic['ic_worst_case']:.0f}  ·  "
                            f"{'⚡ Risk-Free' if ic['ic_risk_free'] else 'Not risk-free'}"
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

        # Auto-suggest SPX close from DB
        ic_exp = base["ic_expiry_date"]
        spx_suggest = None
        if ic_exp:
            spx_suggest = db.get_eod_spx(config.DB_PATH, ic_exp)
            if spx_suggest:
                st.info(f"📈 Last recorded SPX on IC expiry date ({ic_exp}): **{spx_suggest:.2f}**")

        with st.form("expire_form"):
            c1, c2, c3 = st.columns(3)
            result_date = c1.date_input(
                "Expiration / Close Date",
                value=date.fromisoformat(ic_exp) if ic_exp else date.today()
            )
            spx_expiry = c2.number_input(
                "SPX at Expiry / Close",
                min_value=0.0, step=0.01,
                value=float(spx_suggest) if spx_suggest else 0.0
            )
            final_pl = c3.number_input("Final P&L / contract ($)", step=1.0)

            # Preview outcome
            outcome = "—"
            exp_inside = None
            exp_shorts = None
            if spx_expiry > 0 and base["ic_short_call"]:
                sc = base["ic_short_call"]; lc = base["ic_long_call"]
                sp = base["ic_short_put"];  lp = base["ic_long_put"]
                exp_inside = 1 if (spx_expiry > lp and spx_expiry < lc) else 0
                exp_shorts = 1 if (spx_expiry >= sp and spx_expiry <= sc) else 0
                if exp_shorts:
                    outcome = "Maximum Profit"
                elif not exp_inside:
                    outcome = "Minimum Profit (Risk-Free)" if base["ic_risk_free"] else "Maximum Loss"
                else:
                    outcome = "Partial Profit"
                st.markdown(
                    f"**Auto-detected outcome:** `{outcome}`  ·  "
                    f"Inside wings: {bool_icon(exp_inside)}  ·  Between shorts: {bool_icon(exp_shorts)}"
                )

            if st.form_submit_button("⏰ Confirm Expiry", use_container_width=True):
                db.update_trade(config.DB_PATH, chosen_id,
                    status="Expired",
                    result_date=result_date.isoformat(),
                    spx_at_expiry=spx_expiry if spx_expiry > 0 else None,
                    final_pl=final_pl,
                    expired_inside_wings=exp_inside,
                    expired_between_shorts=exp_shorts,
                    outcome=outcome,
                )
                st.success(f"✓ {chosen_id} marked as Expired. Outcome: {outcome}")
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
                "Notes",
                value=base["notes"] or "",
                height=250,
                placeholder="Why did you enter? Market conditions, observations, lessons learned..."
            )
            if st.form_submit_button("💾 Save Notes", use_container_width=True):
                db.update_trade(config.DB_PATH, chosen_id, notes=new_notes)
                st.success(f"✓ Notes saved for {chosen_id}.")
                st.rerun()
