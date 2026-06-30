"""
pages/journal.py — SPX Trade Journal  v3.1

P&L TERMINOLOGY (enforced throughout)
    Realized P&L   = Closed/locked profit BEFORE fees.
                     For IC trades: profit_locked_in (transform credit − entry debit).
                     For direct closes: net_proceeds − entry debit.
    Unrealized P&L = Current mark value of any still-open IC position.
    Net P&L        = Realized P&L − Total Fees.
                     For open IC trades: Realized + Unrealized − Fees.

CLOSE TYPE
    close_type = "transform"  → IC conversion path
    close_type = "direct"     → all legs closed before transformation
    close_type = None         → legacy records (treated as "transform")

DATA FLOW
    Reads: db.trades, db.option_rows (live IC marks)
    Writes: db.trades only — via insert_trade / update_trade / delete_trade
    NEVER touches collector-owned tables.
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import db

# ─────────────────────────────────────────────────────────────────────────────
# Page Config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Trade Journal · SPX", page_icon="📒", layout="wide")

# ─────────────────────────────────────────────────────────────────────────────
# DB Init
# ─────────────────────────────────────────────────────────────────────────────

db.init_trades_table(config.DB_PATH)
db.seed_t001(config.DB_PATH)

# ─────────────────────────────────────────────────────────────────────────────
# Navigation options
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

_SS_DEFAULTS = {
    # Navigation
    "_pending_nav":             None,   # written before rerun; applied before radio renders
    "_pending_close_mode":      None,   # pre-selects close mode toggle on next render
    "_interrupted_nav_dest":    None,   # destination blocked by unsaved-changes guard
    "_show_leave_warning":      False,  # show unsaved-changes dialog on edit pages
    "_last_selected_id":        "—",    # tracks sidebar trade selector for auto-nav
    # Trade CRUD
    "edit_trade_id":            None,
    "confirm_delete_id":        None,
    # Transform / Close CRUD
    "edit_transform_id":        None,
    "confirm_delete_transform_id": None,
    # Wizard (guided edit flow: Step 1 → Log a Trade → Step 2 → Close/Transform)
    "_wizard_mode":             False,
    "_wizard_trade_id":         None,
    # Radio defaults
    "close_mode_radio":         "Transform to Iron Condor",
    # Banner
    "_success_msg":             None,
    "_show_no_data_warning":    False,
}
for _k, _v in _SS_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─────────────────────────────────────────────────────────────────────────────
# Apply pending nav / close-mode BEFORE any widget renders
# ─────────────────────────────────────────────────────────────────────────────

if st.session_state["_pending_nav"]:
    st.session_state["page_mode_radio"] = st.session_state.pop("_pending_nav")

if st.session_state["_pending_close_mode"]:
    st.session_state["close_mode_radio"] = st.session_state.pop("_pending_close_mode")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

DAYS = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]


def fmt_pl(val, decimals=0) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return (f"+${val:,.{decimals}f}" if val >= 0 else f"−${abs(val):,.{decimals}f}")


def fmt_f2(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return f"{val:.2f}"


def fmt_pct(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return f"{val:.1f}%"


def bool_icon(val) -> str:
    return "—" if val is None else ("✓" if int(val) == 1 else "✗")


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
        df = pd.DataFrame(json.loads(legs_json))[["expiry","type","action","strike","fill"]]
        df.columns = ["Expiry","Type","Action","Strike","Fill"]
        return df
    except Exception:
        return None


def get_close_type(t) -> str | None:
    """Safely reads close_type from a sqlite3.Row (column may not exist in legacy rows)."""
    try:
        return t["close_type"] if "close_type" in t.keys() else None
    except Exception:
        return None


def row_get(row, key, default=None):
    """
    sqlite3.Row objects do not have a .get() method.
    This helper mirrors dict.get() behaviour and is safe against
    both missing columns (legacy schema rows) and None values.
    """
    try:
        return row[key] if key in row.keys() else default
    except Exception:
        return default


def ic_expiry_pnl_per_share(spx: float, lp: float, sp: float,
                              sc: float, lc: float) -> float:
    """
    Per-share IC assignment cost at expiration. Always 0 or negative —
    the credit was already captured in profit_locked_in at transformation
    time, so this represents ONLY the assignment liability.

    IC structure:  lp (long put) < sp (short put) < sc (short call) < lc (long call)

    Regions:
      spx <= lp      → max put-wing loss:  −(sp − lp)
      lp < spx < sp  → partial put loss:   −(sp − spx)
      sp <= spx <= sc → both shorts worthless: 0  (full locked profit)
      sc < spx < lc  → partial call loss:  −(spx − sc)
      spx >= lc      → max call-wing loss: −(lc − sc)
    """
    if spx <= lp:
        return -(sp - lp)
    elif spx < sp:
        return -(sp - spx)
    elif spx <= sc:
        return 0.0
    elif spx < lc:
        return -(spx - sc)
    else:
        return -(lc - sc)


def auto_final_pl(t, spx_at_expiry: float) -> float | None:
    """
    Full lifecycle P&L per contract for a Transformed trade at expiration.

    Two components:
      1. Transformation gain:  profit_locked_in × 100 × contracts
         (already realized at transformation — cannot be lost regardless of IC outcome)
      2. IC expiry adjustment: per-share assignment cost × 100 × contracts
         (0 when SPX between shorts; negative when assignment occurs)

    Returns None when required data is missing (Open trades, missing IC strikes,
    zero SPX price) so the caller can fall back to manual entry.
    """
    if t["status"] != "Transformed":
        return None
    try:
        locked     = float(t["profit_locked_in"] or 0.0)
        contracts  = int(t["contracts"])
        lp = float(t["ic_long_put"]);  sp = float(t["ic_short_put"])
        sc = float(t["ic_short_call"]); lc = float(t["ic_long_call"])
    except (TypeError, KeyError):
        return None
    if spx_at_expiry <= 0:
        return None
    adj = ic_expiry_pnl_per_share(spx_at_expiry, lp, sp, sc, lc)
    return (locked + adj) * 100 * contracts


    """Sum of all commission/fee fields across the trade lifecycle."""
    entry = float(t["commissions"] or 0.0)
    tf_comm = 0.0
    try:
        if "transform_commissions" in t.keys():
            tf_comm = float(t["transform_commissions"] or 0.0)
    except Exception:
        pass
    return entry + tf_comm


def get_ic_fills(initial_legs_json, transform_legs_json) -> dict:
    """Extract fill prices for each IC leg from the stored leg JSON blobs."""
    fills = {"sc": None, "sp": None, "lc": None, "lp": None}
    try:
        init = json.loads(initial_legs_json or "[]")
        fills["sc"] = next((l["fill"] for l in init if l["type"]=="Call" and "Sell" in l["action"]), None)
        fills["sp"] = next((l["fill"] for l in init if l["type"]=="Put"  and "Sell" in l["action"]), None)
    except Exception:
        pass
    try:
        tf = json.loads(transform_legs_json or "[]")
        fills["lc"] = next((l["fill"] for l in tf if l["type"]=="Call" and "Buy" in l["action"]), None)
        fills["lp"] = next((l["fill"] for l in tf if l["type"]=="Put"  and "Buy" in l["action"]), None)
    except Exception:
        pass
    return fills


def _ic_payoff_chart(
    lp: float, sp: float, sc: float, lc: float,
    mp: float, wc: float, rf: bool,
    current_spx: float | None = None,
) -> go.Figure:
    """
    Iron Condor payoff-at-expiration chart.

    Strikes:  lp (long put) < sp (short put) < sc (short call) < lc (long call)
    mp        – max profit in dollars per contract
    wc        – max loss magnitude (worst-case) in dollars per contract
    rf        – True when the position is risk-free (locked credit > IC max loss)
    current_spx – optional live SPX price; draws an amber vertical marker when provided
    """
    # ── Colour palette (matches dashboard dark theme) ────────────────────────
    C_BG    = "#0e1117"
    C_GRID  = "#1e2530"
    C_TEXT  = "#c8d0dc"
    C_GREEN = "#2ecc71"
    C_RED   = "#e74c3c"
    C_ZERO  = "#4a5568"
    C_SPX   = "#f59e0b"
    C_BE    = "#94a3b8"
    C_STK   = "#2a3750"
    C_PROFIT_FILL = "rgba(46,204,113,0.13)"
    C_LOSS_FILL   = "rgba(231,76,60,0.13)"

    # ── Payoff function ───────────────────────────────────────────────────────
    wing_val = wc if rf else -wc  # P&L at either wing

    def _pnl(price: float) -> float:
        if price <= lp:
            return wing_val
        if price <= sp:
            t = (price - lp) / (sp - lp)
            return wing_val + t * (mp - wing_val)
        if price <= sc:
            return mp
        if price <= lc:
            t = (price - sc) / (lc - sc)
            return mp + t * (wing_val - mp)
        return wing_val

    # ── X range ──────────────────────────────────────────────────────────────
    wing_w = max(sp - lp, lc - sc, 50)
    pad    = wing_w * 1.8
    x_min, x_max = lp - pad, lc + pad
    x = np.linspace(x_min, x_max, 3000)
    y = np.vectorize(_pnl)(x)

    # ── Breakeven prices ─────────────────────────────────────────────────────
    # (only finite when the wings are at a loss)
    if not rf and (mp + wc) > 0:
        be_lo = lp + wc * (sp - lp) / (mp + wc)
        be_hi = sc + mp * (lc - sc) / (mp + wc)
    else:
        be_lo = be_hi = None

    # ── Colour-split curve ───────────────────────────────────────────────────
    y_pos = np.where(y >= 0, y, np.nan)
    y_neg = np.where(y <  0, y, np.nan)

    fig = go.Figure()

    # Profit fill
    fig.add_trace(go.Scatter(
        x=x, y=np.where(y >= 0, y, 0),
        fill="tozeroy", fillcolor=C_PROFIT_FILL,
        line=dict(color="rgba(0,0,0,0)"),
        showlegend=False, hoverinfo="skip",
    ))
    # Loss fill
    fig.add_trace(go.Scatter(
        x=x, y=np.where(y < 0, y, 0),
        fill="tozeroy", fillcolor=C_LOSS_FILL,
        line=dict(color="rgba(0,0,0,0)"),
        showlegend=False, hoverinfo="skip",
    ))
    # Profit curve
    fig.add_trace(go.Scatter(
        x=x, y=y_pos, mode="lines",
        line=dict(color=C_GREEN, width=2.5),
        showlegend=False, hoverinfo="skip",
    ))
    # Loss curve
    fig.add_trace(go.Scatter(
        x=x, y=y_neg, mode="lines",
        line=dict(color=C_RED, width=2.5),
        showlegend=False, hoverinfo="skip",
    ))
    # Wide invisible trace for hover hit-area
    hover_labels = [
        (f"+${v:.0f}" if v >= 0 else f"−${abs(v):.0f}") for v in y
    ]
    fig.add_trace(go.Scatter(
        x=x, y=y,
        mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=24),
        showlegend=False,
        hovertemplate=(
            "<span style='font-size:13px'><b>SPX %{x:.0f}</b></span><br>"
            "P&L: <b>%{customdata}</b><extra></extra>"
        ),
        customdata=hover_labels,
    ))

    # ── Strike dotted verticals + labels ─────────────────────────────────────
    strike_labels = [
        (lp, f"LP {int(lp)}"),
        (sp, f"SP {int(sp)}"),
        (sc, f"SC {int(sc)}"),
        (lc, f"LC {int(lc)}"),
    ]
    for sx, label in strike_labels:
        fig.add_vline(x=sx, line=dict(color=C_STK, width=1, dash="dot"))
        fig.add_annotation(
            x=sx, y=1.04, yref="paper",
            text=label, showarrow=False,
            font=dict(size=9, color="#64748b"),
            xanchor="center",
        )

    # ── Zero baseline ────────────────────────────────────────────────────────
    fig.add_hline(y=0, line=dict(color=C_ZERO, width=1))

    # ── Breakeven markers ────────────────────────────────────────────────────
    if be_lo is not None:
        fig.add_vline(x=be_lo, line=dict(color=C_BE, width=1.5, dash="dash"))
        fig.add_annotation(
            x=be_lo, y=0, ax=0, ay=28,
            text=f"BE {be_lo:.0f}", showarrow=True,
            arrowhead=0, arrowcolor=C_BE, arrowwidth=1,
            font=dict(size=10, color=C_BE),
            xanchor="right",
        )
    if be_hi is not None:
        fig.add_vline(x=be_hi, line=dict(color=C_BE, width=1.5, dash="dash"))
        fig.add_annotation(
            x=be_hi, y=0, ax=0, ay=28,
            text=f"BE {be_hi:.0f}", showarrow=True,
            arrowhead=0, arrowcolor=C_BE, arrowwidth=1,
            font=dict(size=10, color=C_BE),
            xanchor="left",
        )

    # ── Current SPX vertical ──────────────────────────────────────────────────
    if current_spx is not None:
        fig.add_vline(x=current_spx, line=dict(color=C_SPX, width=2))
        fig.add_annotation(
            x=current_spx, y=1.04, yref="paper",
            text=f"▼ {current_spx:.0f}",
            showarrow=False,
            font=dict(size=10, color=C_SPX, family="monospace"),
            xanchor="center",
            bgcolor="rgba(14,17,23,0.85)",
            borderpad=3,
        )

    # ── Max profit callout ───────────────────────────────────────────────────
    fig.add_annotation(
        x=(sp + sc) / 2, y=mp,
        ax=0, ay=-36,
        text=f"Max Profit  +${mp:.0f}",
        showarrow=True,
        arrowhead=0, arrowcolor="#10b981", arrowwidth=1,
        font=dict(size=11, color="#10b981"),
        bgcolor="rgba(14,17,23,0.9)",
        bordercolor="#10b981", borderwidth=1, borderpad=4,
    )

    # ── Max loss callout (wings only, not risk-free) ──────────────────────────
    if not rf:
        fig.add_annotation(
            x=lp - pad * 0.35, y=-wc,
            text=f"Max Loss  −${wc:.0f}",
            showarrow=False,
            font=dict(size=11, color=C_RED),
            bgcolor="rgba(14,17,23,0.9)",
            bordercolor=C_RED, borderwidth=1, borderpad=4,
            xanchor="center",
        )

    # ── Layout ────────────────────────────────────────────────────────────────
    y_lo = (-wc * 1.45) if not rf else (-mp * 0.15)
    y_hi = mp * 1.40

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        margin=dict(l=70, r=20, t=42, b=44),
        height=330,
        hovermode="x",
        xaxis=dict(
            title="SPX at Expiration",
            title_font=dict(color=C_TEXT, size=11),
            tickfont=dict(color=C_TEXT, size=11),
            gridcolor=C_GRID, showgrid=True, zeroline=False,
            range=[x_min, x_max],
        ),
        yaxis=dict(
            title="P&L per Contract ($)",
            title_font=dict(color=C_TEXT, size=11),
            tickfont=dict(color=C_TEXT, size=11),
            gridcolor=C_GRID, showgrid=True, zeroline=False,
            range=[y_lo, y_hi],
            tickprefix="$",
        ),
        hoverlabel=dict(
            bgcolor="#1a2035",
            bordercolor="#334155",
            font=dict(color=C_TEXT, size=13),
        ),
    )
    return fig


def compute_stats(rows: list) -> dict:
    if not rows:
        return {}
    # Completed = Expired (IC reached expiry) OR Closed (manually closed, with or without IC)
    completed = [r for r in rows if r["status"] in ("Expired","Closed") and r["final_pl"] is not None]
    pls   = [float(r["final_pl"]) for r in completed]
    wins  = [p for p in pls if p > 0]
    loss  = [p for p in pls if p <= 0]
    transformed = [r for r in rows if r["transform_minutes"] is not None]
    win_rate  = len(wins)/len(pls)*100 if pls else None
    avg_win   = sum(wins)/len(wins)   if wins else None
    avg_loss  = sum(loss)/len(loss)   if loss else None
    pf        = sum(wins)/abs(sum(loss)) if sum(loss)!=0 else None
    exp_val   = None
    if win_rate is not None and avg_win is not None and avg_loss is not None:
        exp_val = (win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss)
    hold_list  = [holding_days(r) for r in completed if holding_days(r) is not None]
    avg_hold   = sum(hold_list)/len(hold_list) if hold_list else None
    t_mins     = [r["transform_minutes"] for r in transformed if r["transform_minutes"]]
    avg_t_min  = sum(t_mins)/len(t_mins) if t_mins else None
    debits     = [float(r["total_debit"]) for r in rows]
    credits    = [float(r["credit_received"]) for r in rows if r["credit_received"] is not None]
    # Total fees: entry commissions + transform/close commissions across all trades
    fees_total = sum(total_fees(r) for r in rows)
    total_real = sum(pls) if pls else None
    total_net  = (total_real - fees_total) if total_real is not None else None
    return {
        "Total Trades":          len(rows),
        "Win Rate":              win_rate,
        "Average Winner":        avg_win,
        "Average Loser":         avg_loss,
        "Profit Factor":         pf,
        "Expectancy":            exp_val,
        "Avg Entry Debit":       sum(debits)/len(debits) if debits else None,
        "Avg Close Credit":      sum(credits)/len(credits) if credits else None,
        "Avg Holding (days)":    avg_hold,
        "Avg Time to Transform": avg_t_min,
        "Avg Max Drawdown":      None,
        "Largest Winner":        max(wins) if wins else None,
        "Largest Loser":         min(loss) if loss else None,
        "Total Fees":            fees_total if fees_total > 0 else None,
        "Total Net P&L":         total_net,
    }


def derive_ic(init_json, tf_legs, credit, total_debit, contracts) -> dict | None:
    try:
        init = json.loads(init_json)
        sc = next((l["strike"] for l in init if "Sell" in l["action"] and l["type"]=="Call"), None)
        sp = next((l["strike"] for l in init if "Sell" in l["action"] and l["type"]=="Put"),  None)
        lc = next((l["strike"] for l in tf_legs if "Buy" in l["action"] and l["type"]=="Call"), None)
        lp = next((l["strike"] for l in tf_legs if "Buy" in l["action"] and l["type"]=="Put"),  None)
        ic_exp = next((l["expiry"] for l in tf_legs if "Buy" in l["action"]), None)
        if not all([sc, sp, lc, lp, ic_exp]):
            return None
        cw = abs(float(lc)-float(sc)); pw = abs(float(sp)-float(lp))
        locked = credit - total_debit
        max_p  = round(locked * 100 * contracts)
        max_ic = max(cw, pw) * 100 * contracts
        rf = max_p > max_ic
        return {
            "ic_expiry_date": ic_exp,
            "ic_short_call": float(sc), "ic_long_call": float(lc),
            "ic_short_put":  float(sp), "ic_long_put":  float(lp),
            "ic_call_wing":  cw, "ic_put_wing": pw,
            "ic_max_profit": float(max_p),
            "ic_worst_case": float(max_p-max_ic) if rf else float(max_ic-max_p),
            "ic_risk_free":  1 if rf else 0,
        }
    except Exception:
        return None


def render_regime_analysis(all_trades: list) -> None:
    st.subheader("📈 Regime Analysis — does IV Ratio add value beyond IV level?")
    st.caption("Reconstructs IV term structure at each entry from stored snapshots.")
    if not all_trades:
        st.info("No trades logged yet.")
        return
    et, utc = ZoneInfo(config.DISPLAY_TIMEZONE), ZoneInfo("UTC")
    recs, missing = [], 0
    for t in all_trades:
        try:
            legs   = json.loads(t["initial_legs"])
            exps   = sorted({l["expiry"] for l in legs})
            front_e, back_e = exps[0], exps[-1]
            call_k = next(l["strike"] for l in legs if l["type"]=="Call")
            put_k  = next(l["strike"] for l in legs if l["type"]=="Put")
            dt_et  = datetime.strptime(f"{t['entry_date']} {t['entry_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=et)
            ts_utc = dt_et.astimezone(utc).strftime("%Y-%m-%d %H:%M:%S")
            ctx    = db.get_entry_iv_context(config.DB_PATH, ts_utc, front_e, back_e, call_k, put_k)
        except Exception:
            ctx = None
        if not ctx or ctx["front_iv"] is None or ctx["back_iv"] is None:
            missing += 1; continue
        recs.append({"trade_id": t["trade_id"], "status": t["status"],
                     "front_iv": ctx["front_iv"]*100, "back_iv": ctx["back_iv"]*100,
                     "ratio": ctx["ratio"],
                     "level": (ctx["level"]*100) if ctx["level"] else None,
                     "outcome": t["profit_locked_in"]})
    n_ctx = len(recs)
    st.markdown(f"**{n_ctx}** of **{len(all_trades)}** trades have reconstructable IV context"
                + (f" · {missing} not matched." if missing else "."))
    if n_ctx == 0:
        st.warning("No trades matched a stored snapshot near their entry time."); return
    rf = pd.DataFrame(recs)
    med_r = float(rf["ratio"].median()); med_l = float(rf["level"].median())
    lo = float(min(rf["back_iv"].min(), rf["front_iv"].min()))
    hi = float(max(rf["back_iv"].max(), rf["front_iv"].max()))
    pad = (hi-lo)*0.08 or 1.0; xlo, xhi = max(0.01, lo-pad), hi+pad
    st.markdown("##### Front vs Back IV at entry")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[xlo,xhi], y=[xlo,xhi], mode="lines", name="R=1", line=dict(color="#888",dash="dash")))
    fig.add_trace(go.Scatter(x=[xlo,xhi], y=[med_r*xlo,med_r*xhi], mode="lines",
                             name=f"median R={med_r:.3f}", line=dict(color="#e67e22",dash="dot")))
    L2 = med_l**2; xs = [xlo+(xhi-xlo)*i/60 for i in range(61)]
    fig.add_trace(go.Scatter(x=xs, y=[L2/x for x in xs], mode="lines",
                             name=f"median level={med_l:.2f}%", line=dict(color="#9b59b6",dash="dot")))
    hv = rf["outcome"].notna()
    if hv.any():
        d=rf[hv]
        fig.add_trace(go.Scatter(x=d["back_iv"],y=d["front_iv"],mode="markers+text",text=d["trade_id"],
                                 textposition="top center",name="closed/transformed",
                                 marker=dict(size=13,color=d["outcome"],colorscale="RdYlGn",cmid=0,
                                             showscale=True,colorbar=dict(title="Credit/sh"),
                                             line=dict(width=1,color="#222")),
                                 customdata=d[["ratio","level"]].to_numpy(),
                                 hovertemplate="%{text}<br>B=%{x:.2f}% F=%{y:.2f}% R=%{customdata[0]:.3f}<extra></extra>"))
    if (~hv).any():
        d=rf[~hv]
        fig.add_trace(go.Scatter(x=d["back_iv"],y=d["front_iv"],mode="markers+text",text=d["trade_id"],
                                 textposition="top center",name="open",
                                 marker=dict(size=12,color="#888",symbol="circle-open",line=dict(width=2,color="#aaa")),
                                 hovertemplate="%{text}<br>B=%{x:.2f}% F=%{y:.2f}%<extra></extra>"))
    fig.update_layout(height=480,margin=dict(l=20,r=20,t=10,b=20),
                      xaxis_title="Back IV % (at entry)",yaxis_title="Front IV % (at entry)",
                      legend=dict(orientation="h",yanchor="bottom",y=1.02,x=0,font=dict(size=10)))
    st.plotly_chart(fig, use_container_width=True)
    st.markdown("##### Quadrant outcomes")
    wo = rf[rf["outcome"].notna()].copy()
    if wo.empty:
        st.info("No closed/transformed trades yet."); return
    wo["Level"] = (wo["level"]>=med_l).map({True:"High",False:"Low"})
    wo["Ratio"] = (wo["ratio"]>=med_r).map({True:"High",False:"Low"})
    cells = []
    for lv in ("High","Low"):
        for rt in ("High","Low"):
            sub = wo[(wo["Level"]==lv)&(wo["Ratio"]==rt)]
            cells.append({"Level":lv,"Ratio":rt,"n":len(sub),
                          "m":round(float(sub["outcome"].mean()),3) if len(sub) else None})
    cdf=pd.DataFrame(cells); grid=cdf.pivot(index="Level",columns="Ratio",values="m")
    ngrid=cdf.pivot(index="Level",columns="Ratio",values="n"); disp=grid.astype("object").copy()
    for i in grid.index:
        for j in grid.columns:
            m,nn=grid.loc[i,j],ngrid.loc[i,j]
            disp.loc[i,j]="—" if (m is None or pd.isna(m)) else f"{m:+.3f} (n={int(nn)})"
    st.table(disp)
    thin=int((ngrid.fillna(0)<5).to_numpy().sum())
    if thin: st.warning(f"{thin}/4 cells have n<5 — treat as framework, not result.")

# ─────────────────────────────────────────────────────────────────────────────
# Load Data
# ─────────────────────────────────────────────────────────────────────────────

all_trades    = db.get_all_trades(config.DB_PATH)
open_trades   = [t for t in all_trades if t["status"] == "Open"]
active_trades = [t for t in all_trades if t["status"] in ("Open","Transformed")]

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 📒 Trade Journal")
    st.caption("SPX Diagonal Calendar → Iron Condor")
    st.divider()

    page_mode = st.radio("Navigation", _NAV_OPTIONS, label_visibility="collapsed", key="page_mode_radio")

    if all_trades:
        st.divider()
        trade_options = {t["trade_id"]: f"{t['trade_id']} — {t['entry_date']} ({t['status']})"
                         for t in all_trades}
        _prev_sel = st.session_state["_last_selected_id"]
        selected_id = st.selectbox(
            "Inspect Trade",
            options=["—"] + list(trade_options.keys()),
            format_func=lambda x: trade_options.get(x, x),
            key="inspect_trade_select",
        )
        st.session_state["_last_selected_id"] = selected_id

        # Auto-navigate to Overview when a different trade is selected from any other page.
        # Skips if the leave-warning is already showing to avoid infinite redirect loops.
        if (selected_id != "—"
                and selected_id != _prev_sel
                and page_mode != "📊 Overview"
                and not st.session_state["_show_leave_warning"]):
            st.session_state["_pending_nav"] = "📊 Overview"
            st.rerun()
    else:
        selected_id = "—"

# ─────────────────────────────────────────────────────────────────────────────
# Unsaved-Changes Guard (runs after sidebar, before page content)
#
# Detects when the user navigates away from an active edit form via the radio.
# Redirects them back to the edit page and shows a Leave / Stay dialog.
# Uses _pending_nav to re-set the radio (cannot write to a keyed widget after
# it renders in the same script run).
# ─────────────────────────────────────────────────────────────────────────────

_editing_trade = st.session_state.get("edit_trade_id")
_editing_tf    = st.session_state.get("edit_transform_id")
_current_edit_page = (
    "➕ Log a Trade"    if _editing_trade else
    "🔄 Close / Transform" if _editing_tf   else None
)

if _current_edit_page and page_mode != _current_edit_page:
    st.session_state["_interrupted_nav_dest"] = page_mode
    st.session_state["_show_leave_warning"]   = True
    st.session_state["_pending_nav"]          = _current_edit_page
    st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Page Header
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("## 📒 SPX Diagonal Calendar — Trade Journal")
st.caption("Diagonal Calendar → Iron Condor &nbsp;|&nbsp; Live marks sourced from dashboard.db")
st.divider()

if st.session_state["_success_msg"]:
    st.success(st.session_state["_success_msg"])
    st.session_state["_success_msg"] = None

# ─────────────────────────────────────────────────────────────────────────────
# ── OVERVIEW
# ─────────────────────────────────────────────────────────────────────────────

if page_mode == "📊 Overview":

    # ── Strategy Statistics ───────────────────────────────────────────────────
    st.subheader("Strategy Statistics")
    stats = compute_stats(list(all_trades))

    def stat_cell(label, val, is_pl=False, is_pct=False, is_time=False, decimals=0):
        if val is None:
            display, color = "—", "color:#64748b"
        elif is_pct:
            display = fmt_pct(val)
            color   = "color:#4ade80" if val >= 50 else "color:#f87171"
        elif is_time:
            display = f"{val:.0f}m" if val < 60 else f"{val/60:.1f}h"
            color   = "color:#e2e8f0"
        elif is_pl:
            display = fmt_pl(val, decimals)
            color   = "color:#4ade80" if val >= 0 else "color:#f87171"
        elif label == "Profit Factor":
            display = f"{val:.2f}x"
            color   = "color:#4ade80" if val >= 1.5 else "color:#fbbf24"
        elif label == "Total Trades":
            display, color = str(int(val)), "color:#e2e8f0"
        else:
            display, color = f"{val:.2f}", "color:#e2e8f0"
        st.markdown(
            f"<div style='background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px 14px;'>"
            f"<div style='color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.08em;"
            f"margin-bottom:4px'>{label}</div>"
            f"<div style='font-family:monospace;font-size:18px;font-weight:600;{color}'>{display}</div>"
            f"</div>", unsafe_allow_html=True)

    r1,r2,r3 = st.columns(5), st.columns(5), st.columns(5)
    with r1[0]: stat_cell("Total Trades",          stats.get("Total Trades"))
    with r1[1]: stat_cell("Win Rate",              stats.get("Win Rate"),        is_pct=True)
    with r1[2]: stat_cell("Average Winner",        stats.get("Average Winner"),  is_pl=True)
    with r1[3]: stat_cell("Average Loser",         stats.get("Average Loser"),   is_pl=True)
    with r1[4]: stat_cell("Profit Factor",         stats.get("Profit Factor"))
    with r2[0]: stat_cell("Expectancy",            stats.get("Expectancy"),      is_pl=True)
    with r2[1]: stat_cell("Avg Entry Debit",       stats.get("Avg Entry Debit"), decimals=2)
    with r2[2]: stat_cell("Avg Close Credit",      stats.get("Avg Close Credit"),decimals=2)
    with r2[3]: stat_cell("Avg Holding (days)",    stats.get("Avg Holding (days)"),decimals=1)
    with r2[4]: stat_cell("Avg Time to Transform", stats.get("Avg Time to Transform"), is_time=True)
    with r3[0]: stat_cell("Avg Max Drawdown",      stats.get("Avg Max Drawdown"), is_pl=True)
    with r3[1]: stat_cell("Largest Winner",        stats.get("Largest Winner"),  is_pl=True)
    with r3[2]: stat_cell("Largest Loser",         stats.get("Largest Loser"),   is_pl=True)
    with r3[3]: stat_cell("Total Fees",            stats.get("Total Fees"),      is_pl=True)
    with r3[4]: stat_cell("Total Net P&L",         stats.get("Total Net P&L"),   is_pl=True)

    st.markdown(
        "<div style='margin-top:4px;color:#475569;font-size:11px'>"
        "Avg Max Drawdown requires intraday mark history (future). "
        "Total Fees = entry + transform/close commissions across all trades. "
        "Total Net P&L = Total Realized P&L − Total Fees.</div>",
        unsafe_allow_html=True)
    st.divider()

    # ── Master Log ────────────────────────────────────────────────────────────
    st.subheader("Master Log")

    if not all_trades:
        st.info("No trades logged yet. Use **➕ Log a Trade** to add your first trade.")
    else:
        log_rows = []
        for t in all_trades:
            _ct      = get_close_type(t)
            _fees    = total_fees(t)
            _real_pl = None   # Realized P&L per contract
            _net_pl  = None   # Net P&L = Realized − Fees
            _max_loss = None  # Max possible / actual loss

            if t["status"] in ("Expired","Closed") and t["final_pl"] is not None:
                _real_pl = float(t["final_pl"])
                _net_pl  = _real_pl - _fees
                # For completed trades max loss is the actual loss (if any)
                _max_loss = _real_pl if _real_pl < 0 else None
            elif t["status"] in ("Transformed",) and t["profit_locked_in"] is not None:
                # IC still open — realized portion is locked, IC still has mark risk
                _real_pl = t["profit_locked_in"] * 100 * t["contracts"]
                _net_pl  = _real_pl - _fees
                # Max loss is worst-case IC outcome
                if t["ic_short_call"]:
                    if t["ic_risk_free"]:
                        _max_loss = None  # can't lose money
                    else:
                        _max_loss = -float(t["ic_worst_case"])
            elif t["status"] == "Open":
                # No transformation yet — max loss is full entry debit
                _max_loss = -(float(t["total_debit"]) * 100 * t["contracts"])

            log_rows.append({
                "ID":          t["trade_id"],
                "Date":        t["entry_date"],
                "Day":         t["day_of_week"] or "—",
                "Status":      t["status"],
                "Close Type":  ("Direct" if _ct=="direct" else
                                ("IC Transform" if t["transform_date"] else "—")),
                "Qty":         t["contracts"],
                "Debit/sh":    f"−{fmt_f2(t['total_debit'])}",
                "Fees ($)":    f"${_fees:.2f}" if _fees > 0 else "—",
                "Realized P&L": fmt_pl(_real_pl) if _real_pl is not None else "—",
                "Max Loss":    fmt_pl(_max_loss) if _max_loss is not None else ("Risk-Free" if t["ic_risk_free"] else "—"),
                "Net P&L":     fmt_pl(_net_pl)   if _net_pl  is not None else "—",
                "Outcome":     t["outcome"] or "Pending",
            })
        st.dataframe(pd.DataFrame(log_rows), use_container_width=True, hide_index=True)
        st.caption(
            "Realized P&L = locked/final profit before fees. "
            "Net P&L = Realized − Fees. "
            "Max Loss: open trades show max debit at risk; IC trades show worst-case wing outcome; "
            "completed trades show actual loss if negative."
        )

        # ── Actions ───────────────────────────────────────────────────────────
        st.markdown("")
        trade_ids = [t["trade_id"] for t in all_trades]
        ac1, ac2, ac3, _ = st.columns([3, 1, 1, 5])
        action_trade_id = ac1.selectbox(
            "Select trade", options=trade_ids, label_visibility="collapsed", key="action_trade_select")
        if ac2.button("✏️ Edit", use_container_width=True, key="btn_edit_trade"):
            # Launch wizard: Step 1 = Log a Trade → Step 2 = Close / Transform
            st.session_state["edit_trade_id"]    = action_trade_id
            st.session_state["_wizard_mode"]     = True
            st.session_state["_wizard_trade_id"] = action_trade_id
            st.session_state["confirm_delete_id"] = None
            st.session_state["_pending_nav"]     = "➕ Log a Trade"
            st.rerun()
        if ac3.button("🗑️ Delete", use_container_width=True, key="btn_delete_trade"):
            st.session_state["confirm_delete_id"] = action_trade_id

        if st.session_state["confirm_delete_id"]:
            del_id = st.session_state["confirm_delete_id"]
            st.warning(f"⚠️ Delete **{del_id}**? This cannot be undone.")
            cc1, cc2, _ = st.columns([1,1,6])
            if cc1.button("Confirm Delete", type="primary", key="btn_confirm_del_trade"):
                db.delete_trade(config.DB_PATH, del_id)
                st.session_state["confirm_delete_id"] = None
                st.session_state["_success_msg"] = f"Trade {del_id} deleted."
                st.rerun()
            if cc2.button("Cancel", key="btn_cancel_del_trade"):
                st.session_state["confirm_delete_id"] = None
                st.rerun()

    # ── Trade Detail ──────────────────────────────────────────────────────────
    if selected_id and selected_id != "—":
        st.divider()
        t = db.get_trade(config.DB_PATH, selected_id)
        if t:
            st.subheader(f"Trade Detail — {t['trade_id']}")
            _sc = {"Open":"#3b82f6","Transformed":"#8b5cf6","Expired":"#10b981","Closed":"#64748b"}.get(t["status"],"#64748b")
            st.markdown(
                f"<span style='background:{_sc}22;color:{_sc};border:1px solid {_sc}55;"
                f"border-radius:4px;padding:2px 10px;font-size:12px;font-family:monospace'>{t['status']}</span>"
                f"&nbsp;&nbsp;<span style='color:#94a3b8;font-size:13px;font-family:monospace'>"
                f"{t['entry_date']} · {t['day_of_week']} · {t['entry_time']} ET"
                f"{' · SPX '+str(t['spx_at_entry']) if t['spx_at_entry'] else ''}</span>",
                unsafe_allow_html=True)
            st.markdown("")

            _close_type = get_close_type(t)
            tabs = st.tabs(["Initial Position","Transformation / Close","Iron Condor","Expiration","Notes"])

            # ── Tab 0: Initial Position ───────────────────────────────────
            with tabs[0]:
                df = legs_df(t["initial_legs"])
                if df is not None:
                    st.dataframe(df, use_container_width=True, hide_index=True)
                c1,c2,c3 = st.columns(3)
                c1.metric("Total Debit / share",    f"−${fmt_f2(t['total_debit'])}")
                c2.metric("Total Debit / contract", f"−${t['total_debit']*100*t['contracts']:.0f}")
                c3.metric("Contracts",               t["contracts"])
                if t["spx_at_entry"]:
                    st.markdown(f"**SPX at Entry:** `{t['spx_at_entry']:.2f}`")

            # ── Tab 1: Transformation / Close ─────────────────────────────
            with tabs[1]:
                if not t["transform_date"]:
                    st.info("Not yet closed or transformed. Use **🔄 Close / Transform** in the sidebar.")

                elif _close_type == "direct":
                    st.info("ℹ️ This position was closed directly — no Iron Condor transformation.")
                    c1,c2,c3 = st.columns(3)
                    c1.metric("Close Date",    t["transform_date"])
                    c2.metric("Close Time",    f"{t['transform_time']} ET" if t["transform_time"] else "—")
                    c3.metric("SPX at Close",  fmt_f2(t["spx_at_transform"]) if t["spx_at_transform"] else "—")
                    st.markdown("")
                    net_proc = t["credit_received"] or 0.0
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Net Proceeds / share",
                              f"+{fmt_f2(net_proc)}" if net_proc>=0 else f"−{fmt_f2(abs(net_proc))}")
                    c2.metric("Entry Debit / share",    f"−{fmt_f2(t['total_debit'])}")
                    c3.metric("Realized P&L / share",   fmt_pl(t["profit_locked_in"], 2))
                    _dc_c = t["transform_commissions"] if "transform_commissions" in t.keys() else None
                    c4.metric("Commissions", f"${fmt_f2(_dc_c)}" if _dc_c else "—")
                    if t["profit_locked_in"] is not None:
                        _tf_r = total_fees(t)
                        c1,c2 = st.columns(2)
                        c1.metric("Realized P&L / contract",
                                  fmt_pl(t["profit_locked_in"]*100*t["contracts"]))
                        c2.metric("Net P&L / contract",
                                  fmt_pl(t["profit_locked_in"]*100*t["contracts"] - _tf_r))


                else:
                    # IC Transformation
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Date",      t["transform_date"])
                    c2.metric("Time",      f"{t['transform_time']} ET")
                    c3.metric("Hold Time", f"{t['transform_minutes']}m")
                    c4.metric("SPX",       t["spx_at_transform"] or "—")
                    st.markdown("")
                    df = legs_df(t["transform_legs"])
                    if df is not None:
                        st.dataframe(df, use_container_width=True, hide_index=True)
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Credit Received / share",    f"+${fmt_f2(t['credit_received'])}")
                    c2.metric("Entry Debit / share",        f"−${fmt_f2(t['total_debit'])}")
                    c3.metric("Realized P&L / share",       f"+${fmt_f2(t['profit_locked_in'])}")
                    _tf_c = t["transform_commissions"] if "transform_commissions" in t.keys() else None
                    c4.metric("Commissions", f"${fmt_f2(_tf_c)}" if _tf_c else "—")
                    _tf_fees = total_fees(t)
                    c1,c2 = st.columns(2)
                    c1.metric("Realized P&L / contract", f"+${t['profit_locked_in']*100*t['contracts']:.0f}")
                    c2.metric("Net P&L / contract (after fees)",
                              fmt_pl(t["profit_locked_in"]*100*t["contracts"] - _tf_fees))


            # ── Tab 2: Iron Condor ────────────────────────────────────────
            with tabs[2]:
                if _close_type == "direct":
                    st.info("N/A — position was closed directly without IC transformation.")
                elif not t["ic_short_call"]:
                    st.info("No Iron Condor yet.")
                else:
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Long Put",   f"{int(t['ic_long_put'])}")
                    c2.metric("Short Put",  f"{int(t['ic_short_put'])}")
                    c3.metric("Short Call", f"{int(t['ic_short_call'])}")
                    c4.metric("Long Call",  f"{int(t['ic_long_call'])}")
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Put Wing",   f"{int(t['ic_put_wing'])} pts")
                    c2.metric("Call Wing",  f"{int(t['ic_call_wing'])} pts")
                    c3.metric("Max Profit", f"+${t['ic_max_profit']:.0f}")
                    c4.metric("Worst Case (Guaranteed)" if t["ic_risk_free"] else "Max Loss",
                              f"{'+'if t['ic_risk_free'] else '−'}${t['ic_worst_case']:.0f}")
                    if t["ic_risk_free"]:
                        st.success("✓ Risk-Free — locked credit exceeds max IC loss at any expiry.")
                    sc,lc,sp,lp = t["ic_short_call"],t["ic_long_call"],t["ic_short_put"],t["ic_long_put"]
                    mp,wc,rf = t["ic_max_profit"],t["ic_worst_case"],bool(t["ic_risk_free"])

                    # ── Hoist marks query so current SPX is available for chart ──
                    marks = db.get_ic_marks(config.DB_PATH, t["ic_expiry_date"],
                                            sc,lc,sp,lp)
                    current_spx = marks["spx"] if marks else None

                    # ── Risk Profile chart ──────────────────────────────────────
                    st.markdown("**Risk Profile at Expiration**")
                    st.plotly_chart(
                        _ic_payoff_chart(lp, sp, sc, lc, mp, wc, rf, current_spx),
                        use_container_width=True,
                        config={"displayModeBar": False},
                    )

                    # Live IC Marks
                    st.markdown("**Live IC Marks & Position P&L**")
                    if marks:
                        ctc    = marks["cost_to_close"]
                        locked = t["profit_locked_in"] or 0.0
                        contracts = t["contracts"]
                        st.caption(f"Snapshot: {marks['snapshot_ts']} UTC · SPX: {marks['spx']:.2f}")

                        # Per-leg table with Fill / Mark / Unrealized P&L
                        fills = get_ic_fills(t["initial_legs"], t["transform_legs"])
                        leg_defs = [
                            ("Short Call", f"{int(sc)}C", fills["sc"], marks["short_call_mark"], "short"),
                            ("Long Call",  f"{int(lc)}C", fills["lc"], marks["long_call_mark"],  "long"),
                            ("Short Put",  f"{int(sp)}P", fills["sp"], marks["short_put_mark"],  "short"),
                            ("Long Put",   f"{int(lp)}P", fills["lp"], marks["long_put_mark"],   "long"),
                        ]
                        leg_rows = []
                        ic_unreal_sh = 0.0
                        for leg_name, strike_str, fill, mark, side in leg_defs:
                            if fill is not None and mark is not None:
                                u_sh  = (fill - mark) if side == "short" else (mark - fill)
                                u_ct  = u_sh * 100 * contracts
                                ic_unreal_sh += u_sh
                            else:
                                u_sh = u_ct = None
                            leg_rows.append({
                                "Leg":    leg_name,
                                "Strike": strike_str,
                                "Fill":   fmt_f2(fill),
                                "Mark":   fmt_f2(mark),
                                "Bid":    fmt_f2(marks.get(f"{'short' if side=='short' else 'long'}_{'call' if 'Call' in leg_name else 'put'}_bid")),
                                "Ask":    fmt_f2(marks.get(f"{'short' if side=='short' else 'long'}_{'call' if 'Call' in leg_name else 'put'}_ask")),
                                "Unreal P&L /sh": (f"+{u_sh:.2f}" if u_sh is not None and u_sh>=0 else
                                                   (f"−{abs(u_sh):.2f}" if u_sh is not None else "—")),
                                "Unreal P&L /ct": fmt_pl(u_ct) if u_ct is not None else "—",
                            })
                        st.dataframe(pd.DataFrame(leg_rows), use_container_width=True, hide_index=True)

                        # Summary P&L row
                        ic_unreal_ct  = ic_unreal_sh * 100 * contracts
                        real_ct       = locked * 100 * contracts
                        net_total_ct  = real_ct + ic_unreal_ct - total_fees(t)
                        c1,c2,c3,c4 = st.columns(4)
                        c1.metric("Realized P&L / ct",         fmt_pl(real_ct))
                        c2.metric("IC Unrealized P&L / ct",    fmt_pl(ic_unreal_ct))
                        c3.metric("Total Fees",                f"${total_fees(t):.2f}")
                        c4.metric("Net P&L / ct (R+U−Fees)",   fmt_pl(net_total_ct))
                        st.caption(
                            "Realized = locked-in transform credit − entry debit. "
                            "IC Unrealized = current mark vs fill for each IC leg. "
                            "Net = Realized + Unrealized − Fees."
                        )

                        # EOD
                        if t["entry_date"] < date.today().isoformat():
                            st.markdown(f"**EOD Unrealized — {t['entry_date']}**")
                            eod = db.get_ic_marks(config.DB_PATH, t["ic_expiry_date"], sc,lc,sp,lp,
                                                  eod_date=t["entry_date"])
                            if eod:
                                eod_ctc = eod["cost_to_close"]
                                eod_u   = locked - eod_ctc
                                c1,c2,c3 = st.columns(3)
                                c1.metric("EOD Cost to Close /sh", f"${eod_ctc:.2f}")
                                c2.metric("EOD Unrealized /sh",    fmt_pl(eod_u, 2))
                                c3.metric("EOD Unrealized /ct",    fmt_pl(eod_u*100*contracts))
                                st.caption(f"Snapshot: {eod['snapshot_ts']} UTC · SPX: {eod['spx']:.2f}")
                            else:
                                st.caption("No EOD snapshot for entry date.")
                    else:
                        st.warning("No option_rows data found for IC strikes.")

            # ── Tab 3: Expiration ─────────────────────────────────────────
            with tabs[3]:
                if not t["result_date"]:
                    if t["ic_expiry_date"] and t["ic_expiry_date"] <= date.today().isoformat():
                        spx_s = db.get_eod_spx(config.DB_PATH, t["ic_expiry_date"])
                        st.info(f"IC expired {t['ic_expiry_date']}."
                                + (f" Last SPX: **{spx_s:.2f}**." if spx_s else "")
                                + " Use **⏰ Mark Expired** in the sidebar.")
                    else:
                        st.info("Not yet expired / closed.")
                else:
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Result Date",        t["result_date"])
                    c2.metric("SPX at Expiry",      f"{t['spx_at_expiry']:.2f}" if t["spx_at_expiry"] else "—")
                    c3.metric("Final Realized P&L", fmt_pl(t["final_pl"]))
                    c4.metric("Outcome",            t["outcome"] or "—")
                    # row_get() used here because expired_inside_wings and
                    # expired_between_shorts were added after initial schema —
                    # legacy sqlite3.Row objects don't have .get().
                    _eiw = row_get(t, "expired_inside_wings")
                    _ebs = row_get(t, "expired_between_shorts")
                    if _eiw is not None:
                        c1,c2 = st.columns(2)
                        c1.metric("Inside Wings",   bool_icon(_eiw))
                        c2.metric("Between Shorts", bool_icon(_ebs))
                    _tf = total_fees(t)
                    if t["final_pl"] is not None and _tf > 0:
                        st.metric("Net P&L (after fees)", fmt_pl(t["final_pl"] - _tf))

                    # ── P&L lifecycle breakdown (transformed trades only) ────
                    if t["status"] == "Expired" and row_get(t, "profit_locked_in") is not None and t["spx_at_expiry"]:
                        spx_ex = float(t["spx_at_expiry"])
                        contracts = int(t["contracts"])
                        locked = float(t["profit_locked_in"])
                        locked_ct = locked * 100 * contracts
                        try:
                            adj_sh = ic_expiry_pnl_per_share(
                                spx_ex,
                                float(t["ic_long_put"]), float(t["ic_short_put"]),
                                float(t["ic_short_call"]), float(t["ic_long_call"]),
                            )
                        except (TypeError, ValueError):
                            adj_sh = None
                        if adj_sh is not None:
                            adj_ct = adj_sh * 100 * contracts
                            st.markdown(
                                "<div style='background:#1a2035;border:1px solid #2a3f56;"
                                "border-radius:8px;padding:12px 16px;margin-top:8px'>"
                                "<div style='color:#64748b;font-size:11px;text-transform:uppercase;"
                                "letter-spacing:.08em;margin-bottom:8px'>P&L Lifecycle Breakdown</div>"
                                "<table style='width:100%;font-family:monospace;font-size:13px;"
                                "border-collapse:collapse'>"
                                f"<tr><td style='color:#94a3b8;padding:3px 0'>1. Transformation gain</td>"
                                f"<td style='text-align:right;color:{'#4ade80' if locked_ct >= 0 else '#f87171'}'>"
                                f"{fmt_pl(locked_ct)}</td></tr>"
                                f"<tr><td style='color:#94a3b8;padding:3px 0'>2. IC expiry adjustment"
                                f"&nbsp;<span style='color:#64748b;font-size:11px'>"
                                f"(SPX {spx_ex:.0f} vs shorts {t['ic_short_put']:.0f}–{t['ic_short_call']:.0f})"
                                f"</span></td>"
                                f"<td style='text-align:right;color:{'#4ade80' if adj_ct >= 0 else '#f87171'}'>"
                                f"{fmt_pl(adj_ct)}</td></tr>"
                                "<tr><td colspan='2'><hr style='border:none;border-top:1px solid #2a3f56;"
                                "margin:6px 0'></td></tr>"
                                f"<tr><td style='color:#e2e8f0;font-weight:600;padding:3px 0'>Total Realized P&L</td>"
                                f"<td style='text-align:right;font-weight:600;"
                                f"color:{'#4ade80' if (t['final_pl'] or 0) >= 0 else '#f87171'}'>"
                                f"{fmt_pl(t['final_pl'])}</td></tr>"
                                "</table></div>",
                                unsafe_allow_html=True,
                            )

            # ── Tab 4: Notes ──────────────────────────────────────────────
            with tabs[4]:
                st.markdown(t["notes"] or "*No notes yet. Use ✏️ Edit Notes.*")
                st.caption(f"Last updated: {t['updated_at']} UTC")

# ─────────────────────────────────────────────────────────────────────────────
# ── LOG A TRADE  (Wizard Step 1)
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "➕ Log a Trade":

    edit_id    = st.session_state.get("edit_trade_id")
    is_edit    = edit_id is not None
    edit_trade = db.get_trade(config.DB_PATH, edit_id) if is_edit else None
    in_wizard  = st.session_state.get("_wizard_mode", False)

    # ── Unsaved-changes leave dialog ─────────────────────────────────────────
    if st.session_state["_show_leave_warning"]:
        dest = st.session_state.get("_interrupted_nav_dest", "📊 Overview")
        st.warning(
            f"⚠️ You have unsaved changes on this page. "
            f"If you leave, your edits will be discarded."
        )
        lv1, lv2, _ = st.columns([1,1,6])
        if lv1.button("Leave (discard changes)", key="btn_leave"):
            st.session_state["edit_trade_id"]       = None
            st.session_state["_wizard_mode"]        = False
            st.session_state["_wizard_trade_id"]    = None
            st.session_state["_show_leave_warning"] = False
            st.session_state["_pending_nav"]        = dest
            st.rerun()
        if lv2.button("Stay on page", key="btn_stay"):
            st.session_state["_show_leave_warning"]  = False
            st.session_state["_interrupted_nav_dest"] = None
            st.rerun()
        st.stop()

    # ── Header ───────────────────────────────────────────────────────────────
    if in_wizard and is_edit:
        st.info(f"**Step 1 of 2 — Initial Trade Entry** for {edit_id}. "
                f"Review and save, or move to Step 2 without changes.")
        # Top action buttons (outside the form)
        hdr_c1, hdr_c2, hdr_c3 = st.columns([1, 1.3, 5])
        if hdr_c1.button("← Cancel Edit", key="cancel_edit", use_container_width=True):
            # Discard unsaved edits, return to Overview — zero DB writes.
            st.session_state["edit_trade_id"]    = None
            st.session_state["_wizard_mode"]     = False
            st.session_state["_wizard_trade_id"] = None
            st.session_state["_show_leave_warning"] = False
            st.session_state["_pending_nav"]     = "📊 Overview"
            st.rerun()
        if hdr_c2.button("Move to Step 2 →", key="move_to_step2", use_container_width=True):
            # Navigate to Close/Transform WITHOUT saving the initial trade.
            wizard_id = st.session_state.get("_wizard_trade_id", edit_id)
            _existing = db.get_trade(config.DB_PATH, wizard_id)
            _ct_existing = get_close_type(_existing) if _existing else None
            st.session_state["edit_trade_id"]       = None
            st.session_state["edit_transform_id"]   = wizard_id
            st.session_state["_pending_close_mode"] = (
                "Close Position Directly" if _ct_existing == "direct" else "Transform to Iron Condor"
            )
            st.session_state["_pending_nav"]  = "🔄 Close / Transform"
            st.session_state["_success_msg"]  = "Log Entry unchanged. Review Close / Transform record below."
            st.rerun()
        st.subheader(f"Log a Trade — Editing {edit_id}")
        st.caption("Modifying the initial entry. Transformation and IC data are unchanged.")
    elif is_edit and edit_trade:
        tc, cc = st.columns([5,1])
        tc.subheader(f"Log a Trade — Editing {edit_id}")
        tc.caption("Modifying the initial entry. Transformation and IC data are unchanged.")
        if cc.button("← Cancel", key="cancel_edit"):
            st.session_state["edit_trade_id"]    = None
            st.session_state["_pending_nav"]     = "📊 Overview"
            st.rerun()
    else:
        st.subheader("Log a Trade")
        next_id = db.get_next_trade_id(config.DB_PATH)
        st.caption(f"Next trade ID: **{next_id}**")

    # ── Pre-populate legs ─────────────────────────────────────────────────────
    existing_legs: list[dict] = []
    if is_edit and edit_trade and edit_trade["initial_legs"]:
        try:
            existing_legs = json.loads(edit_trade["initial_legs"])
        except Exception:
            existing_legs = []
    while len(existing_legs) < 4:
        existing_legs.append({"expiry": date.today().isoformat(), "type":"Call",
                               "action":"Sell to Open","strike":0.0,"fill":0.0})

    form_key = f"trade_form_{edit_id or 'new'}"
    _et = edit_trade if is_edit and edit_trade else None

    with st.form(form_key):
        st.markdown("**Trade Summary**")
        c1,c2,c3,c4 = st.columns(4)
        entry_date  = c1.date_input("Entry Date",     value=date.fromisoformat(_et["entry_date"]) if _et else date.today())
        entry_time  = c2.text_input("Entry Time (ET)", value=(_et["entry_time"] or "") if _et else "", placeholder="09:34")
        spx_entry   = c3.number_input("SPX at Entry",  min_value=0.0, step=0.01, value=float(_et["spx_at_entry"] or 0.0) if _et else 0.0)
        contracts   = c4.number_input("Contracts",     min_value=1, step=1, value=int(_et["contracts"]) if _et else 1)
        c1,c2 = st.columns(2)
        total_debit  = c1.number_input("Total Debit / share ($)", min_value=0.0, step=0.01, value=float(_et["total_debit"]) if _et else 0.0)
        commissions  = c2.number_input("Commissions / fees ($, optional)", min_value=0.0, step=0.01, value=float(_et["commissions"] or 0.0) if _et else 0.0)
        st.markdown("**Initial Legs** — 4 required")
        ACTS = ["Sell to Open","Buy to Open","Sell to Close","Buy to Close"]
        leg_data = []
        for i in range(4):
            leg = existing_legs[i]
            st.markdown(f"*Leg {i+1}*")
            c1,c2,c3,c4,c5 = st.columns([2,1,2,1,1])
            try:    ld = date.fromisoformat(leg["expiry"])
            except: ld = date.today()
            expiry = c1.date_input("Expiry", value=ld,  key=f"{form_key}_l{i}_exp")
            ltype  = c2.selectbox("Type", ["Call","Put"], index=["Call","Put"].index(leg.get("type","Call")), key=f"{form_key}_l{i}_type")
            av     = leg.get("action","Sell to Open")
            action = c3.selectbox("Action", ACTS, index=ACTS.index(av) if av in ACTS else 0, key=f"{form_key}_l{i}_act")
            strike = c4.number_input("Strike", min_value=0.0, step=1.0, value=float(leg.get("strike",0.0)), key=f"{form_key}_l{i}_str")
            fill   = c5.number_input("Fill",   min_value=0.0, step=0.01,value=float(leg.get("fill",0.0)),   key=f"{form_key}_l{i}_fill")
            leg_data.append({"expiry":expiry.isoformat(),"type":ltype,"action":action,"strike":strike,"fill":fill})
        st.markdown("**Notes**")
        notes = st.text_area("Trade notes", value=(_et["notes"] or "") if _et else "", height=100)

        if st.form_submit_button("💾 Save Changes" if is_edit else "💾 Save Trade", use_container_width=True):
            if total_debit <= 0:
                st.error("Total debit must be > 0.")
            elif not entry_time:
                st.error("Entry time is required.")
            else:
                fields = {
                    "entry_date":   entry_date.isoformat(),
                    "entry_time":   entry_time,
                    "day_of_week":  DAYS[(entry_date.weekday()+1) % 7],
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
                    if in_wizard:
                        # Proceed to Step 2 — same destination as Move to Step 2
                        wizard_id = st.session_state.get("_wizard_trade_id", edit_id)
                        _existing = db.get_trade(config.DB_PATH, wizard_id)
                        _ct_existing = get_close_type(_existing) if _existing else None
                        st.session_state["edit_transform_id"]   = wizard_id
                        st.session_state["_pending_close_mode"] = (
                            "Close Position Directly" if _ct_existing == "direct" else "Transform to Iron Condor"
                        )
                        st.session_state["_pending_nav"]  = "🔄 Close / Transform"
                        st.session_state["_success_msg"]  = "Initial Trade saved. Review Close / Transform record below."
                    else:
                        st.session_state["_pending_nav"]  = "📊 Overview"
                        st.session_state["_success_msg"]  = "Changes saved successfully."
                else:
                    fields["trade_id"] = next_id
                    fields["status"]   = "Open"
                    db.insert_trade(config.DB_PATH, fields)
                    st.session_state["_success_msg"] = "Trade logged successfully."
                st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# ── CLOSE / TRANSFORM  (Wizard Step 2)
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "🔄 Close / Transform":

    edit_tf_id    = st.session_state.get("edit_transform_id")
    is_tf_edit    = edit_tf_id is not None
    edit_tf_trade = db.get_trade(config.DB_PATH, edit_tf_id) if is_tf_edit else None
    in_wizard     = st.session_state.get("_wizard_mode", False)

    # ── Unsaved-changes leave dialog ─────────────────────────────────────────
    if st.session_state["_show_leave_warning"]:
        dest = st.session_state.get("_interrupted_nav_dest", "📊 Overview")
        st.warning("⚠️ You have unsaved changes on this page. If you leave, edits will be discarded.")
        lv1, lv2, _ = st.columns([1,1,6])
        if lv1.button("Leave (discard changes)", key="btn_leave"):
            st.session_state["edit_transform_id"]   = None
            st.session_state["_wizard_mode"]        = False
            st.session_state["_wizard_trade_id"]    = None
            st.session_state["_show_leave_warning"] = False
            st.session_state["_pending_nav"]        = dest
            st.rerun()
        if lv2.button("Stay on page", key="btn_stay"):
            st.session_state["_show_leave_warning"]  = False
            st.session_state["_interrupted_nav_dest"] = None
            st.rerun()
        st.stop()

    # ── "Nothing entered" warning (set by form submit when wizard + no data) ──
    if st.session_state.get("_show_no_data_warning"):
        st.warning("Position hasn't been transformed or closed.")
        if st.button("← Return to Overview", key="no_data_overview"):
            st.session_state["_show_no_data_warning"] = False
            st.session_state["edit_transform_id"]     = None
            st.session_state["_wizard_mode"]          = False
            st.session_state["_wizard_trade_id"]      = None
            st.session_state["_pending_nav"]          = "📊 Overview"
            st.rerun()
        st.stop()

    # ── Header ───────────────────────────────────────────────────────────────
    if is_tf_edit and edit_tf_trade:
        if in_wizard:
            hdr_cols = st.columns([4, 1, 1])
            hdr_cols[0].subheader(f"Close / Transform — Step 2 of 2: Editing {edit_tf_id}")
            hdr_cols[0].caption("Modifying close or transformation record.")
            if hdr_cols[1].button("← Go Back", key="wizard_go_back", use_container_width=True,
                                   help="Return to Step 1 — Log a Trade"):
                wizard_id = st.session_state.get("_wizard_trade_id", edit_tf_id)
                st.session_state["edit_transform_id"] = None
                st.session_state["edit_trade_id"]     = wizard_id
                st.session_state["_pending_nav"]      = "➕ Log a Trade"
                st.rerun()
            if hdr_cols[2].button("Cancel", key="cancel_tf_edit", use_container_width=True,
                                   help="Exit wizard and return to Overview"):
                st.session_state["edit_transform_id"] = None
                st.session_state["_wizard_mode"]      = False
                st.session_state["_wizard_trade_id"]  = None
                st.session_state["_pending_nav"]      = "📊 Overview"
                st.rerun()
        else:
            hdr_cols = st.columns([5, 1])
            hdr_cols[0].subheader(f"Close / Transform — Editing {edit_tf_id}")
            hdr_cols[0].caption("Modifying close or transformation record.")
            if hdr_cols[1].button("← Cancel", key="cancel_tf_edit"):
                st.session_state["edit_transform_id"] = None
                st.session_state["_pending_nav"]      = "📊 Overview"
                st.rerun()
        base      = edit_tf_trade
        chosen_id = edit_tf_id
    else:
        st.subheader("Close / Transform")
        if not open_trades:
            st.info("No Open trades to close or transform. Add a trade first.")
            st.stop()
        trade_map = {t["trade_id"]: t for t in open_trades}
        chosen_id = st.selectbox("Select Open Trade", list(trade_map.keys()))
        base      = trade_map[chosen_id]

    st.caption(f"Entry: {base['entry_date']} {base['entry_time']} ET · Debit: ${base['total_debit']:.2f}/sh")

    # ── Close mode toggle ─────────────────────────────────────────────────────
    close_mode = st.radio(
        "How are you closing this trade?",
        ["Transform to Iron Condor","Close Position Directly"],
        horizontal=True, key="close_mode_radio")
    st.markdown("")

    # ════════════════════════════════════════════════════════════════════════
    # BRANCH A: Close Position Directly
    # ════════════════════════════════════════════════════════════════════════

    if close_mode == "Close Position Directly":
        _etf = edit_tf_trade if is_tf_edit and edit_tf_trade else None
        _is_direct_edit = is_tf_edit and _etf and get_close_type(_etf)=="direct"

        default_dc_date = (date.fromisoformat(_etf["transform_date"]) if _is_direct_edit and _etf["transform_date"] else date.today())
        default_dc_time = (_etf["transform_time"] or "") if _is_direct_edit else ""
        default_dc_spx  = float(_etf["spx_at_transform"] or 0.0) if _is_direct_edit else 0.0
        default_dc_net  = float(_etf["credit_received"] or 0.0) if _is_direct_edit else 0.0
        default_dc_comm = float(_etf["transform_commissions"] or 0.0) if (
            _is_direct_edit and _etf and "transform_commissions" in _etf.keys() and _etf["transform_commissions"]) else 0.0

        dc_form_key = f"dc_form_{edit_tf_id or chosen_id}"
        with st.form(dc_form_key):
            st.markdown("**Close Details**")
            c1,c2,c3 = st.columns(3)
            close_date     = c1.date_input("Close Date", value=default_dc_date)
            close_time     = c2.text_input("Close Time (ET)", value=default_dc_time, placeholder="09:47")
            spx_close      = c3.number_input("SPX at Close", min_value=0.0, step=0.01, value=default_dc_spx)
            c1,c2 = st.columns(2)
            net_credit     = c1.number_input("Net Proceeds / share ($)", step=0.01, value=default_dc_net,
                                             help="Positive = net credit. Negative = net debit paid to close.")
            dc_commissions = c2.number_input("Commissions / fees ($, optional)", min_value=0.0, step=0.01, value=default_dc_comm)
            if net_credit != 0.0:
                locked = net_credit - base["total_debit"]; pnl_ct = locked*100*int(base["contracts"])
                col = "#4ade80" if locked>=0 else "#f87171"
                st.markdown(f"<span style='color:{col};font-family:monospace'>"
                            f"Realized P&L / sh: {'+'if locked>=0 else ''}${locked:.2f} · /ct: {'+'if pnl_ct>=0 else ''}${pnl_ct:.0f}</span>",
                            unsafe_allow_html=True)
            if st.form_submit_button("💾 Save Changes" if is_tf_edit else "💾 Record Close", use_container_width=True):
                if not close_time:
                    if in_wizard:
                        st.session_state["_show_no_data_warning"] = True
                        st.rerun()
                    else:
                        st.error("Close time is required.")
                else:
                    locked = net_credit - base["total_debit"]
                    final_pl_ct = locked*100*int(base["contracts"])
                    outcome = "Closed at Profit" if locked>0 else ("Break Even" if locked==0 else "Closed at Loss")
                    db.update_trade(config.DB_PATH, chosen_id,
                        status="Closed", close_type="direct",
                        transform_date=close_date.isoformat(), transform_time=close_time,
                        spx_at_transform=spx_close if spx_close>0 else None,
                        credit_received=net_credit, profit_locked_in=locked,
                        transform_commissions=dc_commissions if dc_commissions>0 else None,
                        result_date=close_date.isoformat(), final_pl=final_pl_ct, outcome=outcome)
                    st.session_state["edit_transform_id"] = None
                    st.session_state["_wizard_mode"]      = False
                    st.session_state["_wizard_trade_id"]  = None
                    # Direct close: position fully recorded → return to Overview
                    st.session_state["_pending_nav"]      = "📊 Overview"
                    st.session_state["_success_msg"]      = (
                        f"Close record {'updated' if is_tf_edit else 'saved'} for {chosen_id}. "
                        f"Realized P&L: {'+'if locked>=0 else ''}${locked:.2f}/sh ({outcome}).")
                    st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # BRANCH B: Transform to Iron Condor
    # ════════════════════════════════════════════════════════════════════════

    else:
        existing_tf_legs: list[dict] = []
        if is_tf_edit and edit_tf_trade and edit_tf_trade["transform_legs"]:
            try:    existing_tf_legs = json.loads(edit_tf_trade["transform_legs"])
            except: existing_tf_legs = []
        while len(existing_tf_legs) < 4:
            existing_tf_legs.append({"expiry":date.today().isoformat(),"type":"Call",
                                      "action":"Sell to Close","strike":0.0,"fill":0.0})

        tf_form_key = f"tf_form_{edit_tf_id or 'new'}"
        _etf = edit_tf_trade if is_tf_edit and edit_tf_trade else None

        with st.form(tf_form_key):
            st.markdown("**Transformation Details**")
            c1,c2,c3,c4 = st.columns(4)
            default_tf_date = (date.fromisoformat(_etf["transform_date"]) if _etf and _etf["transform_date"] else date.today())
            default_tf_time = (_etf["transform_time"] or "") if _etf else ""
            default_spx_tf  = float(_etf["spx_at_transform"] or 0.0) if _etf else 0.0
            default_credit  = float(_etf["credit_received"] or 0.0) if _etf else 0.0
            default_tf_comm = float(_etf["transform_commissions"] or 0.0) if (
                _etf and "transform_commissions" in _etf.keys() and _etf["transform_commissions"]) else 0.0

            tf_date = c1.date_input("Transform Date", value=default_tf_date)
            tf_time = c2.text_input("Transform Time (ET)", value=default_tf_time, placeholder="09:47")
            spx_tf  = c3.number_input("SPX at Transform", min_value=0.0, step=0.01, value=default_spx_tf)
            credit  = c4.number_input("Credit Received / share ($)", min_value=0.0, step=0.01, value=default_credit)
            c1,_ = st.columns(2)
            tf_commissions = c1.number_input("Commissions / fees ($, optional)", min_value=0.0, step=0.01, value=default_tf_comm)
            if credit > 0:
                locked = credit - base["total_debit"]
                col = "#4ade80" if locked>=0 else "#f87171"
                st.markdown(f"<span style='color:{col};font-family:monospace'>"
                            f"Realized P&L / sh: {'+'if locked>=0 else ''}${locked:.2f} · "
                            f"/ct: {'+'if locked>=0 else ''}${locked*100*base['contracts']:.0f}</span>",
                            unsafe_allow_html=True)
            st.markdown("**Transformation Legs** (close back longs + open protective wings)")
            TF_ACTS = ["Sell to Close","Buy to Open","Buy to Close","Sell to Open"]
            tf_legs = []
            for i in range(4):
                leg = existing_tf_legs[i]
                st.markdown(f"*Leg {i+1}*")
                c1,c2,c3,c4,c5 = st.columns([2,1,2,1,1])
                try:    ld = date.fromisoformat(leg["expiry"])
                except: ld = date.today()
                expiry = c1.date_input("Expiry", value=ld, key=f"{tf_form_key}_l{i}_exp")
                ltype  = c2.selectbox("Type",["Call","Put"], index=["Call","Put"].index(leg.get("type","Call")), key=f"{tf_form_key}_l{i}_type")
                av     = leg.get("action","Sell to Close")
                action = c3.selectbox("Action", TF_ACTS, index=TF_ACTS.index(av) if av in TF_ACTS else 0, key=f"{tf_form_key}_l{i}_act")
                strike = c4.number_input("Strike", min_value=0.0, step=1.0, value=float(leg.get("strike",0.0)), key=f"{tf_form_key}_l{i}_str")
                fill   = c5.number_input("Fill",   min_value=0.0, step=0.01,value=float(leg.get("fill",0.0)),   key=f"{tf_form_key}_l{i}_fill")
                tf_legs.append({"expiry":expiry.isoformat(),"type":ltype,"action":action,"strike":strike,"fill":fill})

            if st.form_submit_button("💾 Save Changes" if is_tf_edit else "💾 Save Transformation", use_container_width=True):
                if credit <= 0:
                    if in_wizard:
                        st.session_state["_show_no_data_warning"] = True
                        st.rerun()
                    else:
                        st.error("Credit received must be > 0.")
                elif not tf_time:
                    st.error("Transform time is required.")
                else:
                    try:
                        entry_dt = datetime.strptime(f"{base['entry_date']} {base['entry_time']}", "%Y-%m-%d %H:%M")
                        tf_dt    = datetime.strptime(f"{tf_date.isoformat()} {tf_time}", "%Y-%m-%d %H:%M")
                        mins = max(0, int((tf_dt-entry_dt).total_seconds()/60))
                    except: mins = 0
                    locked = credit - base["total_debit"]
                    ic = derive_ic(base["initial_legs"], tf_legs, credit, base["total_debit"], base["contracts"])
                    updates = {
                        "status":"Transformed", "close_type":"transform",
                        "transform_date":tf_date.isoformat(), "transform_time":tf_time,
                        "transform_minutes":mins, "spx_at_transform":spx_tf if spx_tf>0 else None,
                        "transform_legs":json.dumps(tf_legs), "credit_received":credit,
                        "profit_locked_in":locked,
                        "transform_commissions":tf_commissions if tf_commissions>0 else None,
                    }
                    if ic: updates.update(ic)
                    db.update_trade(config.DB_PATH, chosen_id, **updates)
                    st.session_state["edit_transform_id"] = None
                    st.session_state["_wizard_mode"]      = False
                    st.session_state["_wizard_trade_id"]  = None
                    ic_note = (f" IC: Max Profit ${ic['ic_max_profit']:.0f} · {'⚡ Risk-Free' if ic['ic_risk_free'] else 'Not risk-free'}." if ic else "")
                    if in_wizard:
                        # IC still needs expiry tracking — send to Mark Expired
                        st.session_state["_pending_nav"]  = "⏰ Mark Expired"
                        st.session_state["_success_msg"]  = (
                            f"Transformation recorded for {chosen_id}.{ic_note} "
                            f"Mark the position as expired when it reaches expiry.")
                    else:
                        st.session_state["_pending_nav"]  = "📊 Overview"
                        st.session_state["_success_msg"]  = (
                            f"Transformation {'updated' if is_tf_edit else 'recorded'} for {chosen_id}.{ic_note}")
                    st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# ── MARK EXPIRED
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
        is_transformed = base["status"] == "Transformed" and base["ic_short_call"] is not None

        spx_s = db.get_eod_spx(config.DB_PATH, ic_exp) if ic_exp else None
        if spx_s:
            st.info(f"📈 Last recorded SPX on IC expiry date ({ic_exp}): **{spx_s:.2f}**")

        with st.form("expire_form"):
            c1, c2 = st.columns(2)
            result_date = c1.date_input(
                "Expiration Date",
                value=date.fromisoformat(ic_exp) if ic_exp else date.today(),
            )
            spx_expiry = c2.number_input(
                "SPX at Expiry",
                min_value=0.0, step=0.01,
                value=float(spx_s) if spx_s else 0.0,
            )

            # ── Outcome detection ─────────────────────────────────────────
            outcome = "—"; exp_inside = exp_shorts = None
            adj_sh = None
            if spx_expiry > 0 and is_transformed:
                sc = float(base["ic_short_call"]); lc = float(base["ic_long_call"])
                sp = float(base["ic_short_put"]);  lp = float(base["ic_long_put"])
                exp_inside = 1 if (spx_expiry > lp and spx_expiry < lc) else 0
                exp_shorts = 1 if (spx_expiry >= sp and spx_expiry <= sc) else 0
                outcome = (
                    "Maximum Profit" if exp_shorts else
                    "Minimum Profit (Risk-Free)" if (not exp_inside and base["ic_risk_free"]) else
                    "Maximum Loss" if not exp_inside else
                    "Partial Profit"
                )
                adj_sh = ic_expiry_pnl_per_share(spx_expiry, lp, sp, sc, lc)

            st.markdown(
                f"**Auto-detected:** `{outcome}` · "
                f"Wings: {bool_icon(exp_inside)} · Shorts: {bool_icon(exp_shorts)}"
            )

            # ── Auto-calculated P&L breakdown (Transformed trades only) ──
            _auto_pl = None
            if is_transformed and spx_expiry > 0 and adj_sh is not None:
                locked     = float(base["profit_locked_in"] or 0.0)
                contracts  = int(base["contracts"])
                locked_ct  = locked * 100 * contracts
                adj_ct     = adj_sh * 100 * contracts
                _auto_pl   = locked_ct + adj_ct

                def _clr(v):
                    return "#4ade80" if v >= 0 else "#f87171"

                st.markdown(
                    "<div style='background:#0f1d2e;border:1px solid #1e3a5f;"
                    "border-radius:8px;padding:12px 16px;margin:10px 0'>"
                    "<div style='color:#64748b;font-size:11px;text-transform:uppercase;"
                    "letter-spacing:.08em;margin-bottom:8px'>Auto-Calculated P&L</div>"
                    "<table style='width:100%;font-family:monospace;font-size:13px;"
                    "border-collapse:collapse'>"
                    f"<tr><td style='color:#94a3b8;padding:3px 0'>① Transformation gain&nbsp;"
                    f"<span style='color:#64748b;font-size:11px'>"
                    f"({locked:+.2f}/sh × 100 × {contracts} ct)</span></td>"
                    f"<td style='text-align:right;color:{_clr(locked_ct)};font-weight:600'>"
                    f"{fmt_pl(locked_ct)}</td></tr>"
                    f"<tr><td style='color:#94a3b8;padding:3px 0'>② IC expiry adjustment&nbsp;"
                    f"<span style='color:#64748b;font-size:11px'>"
                    f"(SPX {spx_expiry:.0f} → {adj_sh:+.2f}/sh)</span></td>"
                    f"<td style='text-align:right;color:{_clr(adj_ct)}'>"
                    f"{fmt_pl(adj_ct)}</td></tr>"
                    "<tr><td colspan='2'><hr style='border:none;border-top:1px solid #1e3a5f;"
                    "margin:5px 0'></td></tr>"
                    f"<tr><td style='color:#e2e8f0;font-weight:600;padding:3px 0'>"
                    f"Total Realized P&L / contract</td>"
                    f"<td style='text-align:right;font-weight:700;font-size:15px;"
                    f"color:{_clr(_auto_pl)}'>{fmt_pl(_auto_pl)}</td></tr>"
                    "</table></div>",
                    unsafe_allow_html=True,
                )

            # ── P&L input — pre-filled but always editable ───────────────
            final_pl = st.number_input(
                "Final Realized P&L / contract ($)"
                + (" — auto-calculated above, override if needed" if _auto_pl is not None else ""),
                value=float(round(_auto_pl)) if _auto_pl is not None else 0.0,
                step=1.0,
            )

            if not is_transformed:
                st.caption(
                    "Open trade (no IC data) — enter the total realized P&L manually: "
                    "net proceeds minus original entry debit, times 100, times contracts."
                )

            if st.form_submit_button("⏰ Confirm Expiry", use_container_width=True):
                db.update_trade(
                    config.DB_PATH, chosen_id,
                    status="Expired",
                    result_date=result_date.isoformat(),
                    spx_at_expiry=spx_expiry if spx_expiry > 0 else None,
                    final_pl=final_pl,
                    expired_inside_wings=exp_inside,
                    expired_between_shorts=exp_shorts,
                    outcome=outcome,
                )
                st.session_state["_success_msg"] = f"{chosen_id} marked Expired. Outcome: {outcome}"
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# ── EDIT NOTES
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
            new_notes = st.text_area("Notes", value=base["notes"] or "", height=250,
                                     placeholder="Rationale, market conditions, lessons learned...")
            if st.form_submit_button("💾 Save Notes", use_container_width=True):
                db.update_trade(config.DB_PATH, chosen_id, notes=new_notes)
                st.session_state["_success_msg"] = f"Notes saved for {chosen_id}."
                st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# ── REGIME ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

elif page_mode == "📈 Regime Analysis":
    render_regime_analysis(all_trades)
