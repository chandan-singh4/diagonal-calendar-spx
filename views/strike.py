"""Strike Detail — the two chosen strikes, their Greeks, and their IV history.

The left column is a static read of the current snapshot; the right is a
per-strike time series over a chosen window. Both are gated on strikes
actually being chosen, and say so differently when they are not.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import iv_engine
from core.charts import SESSION_RANGEBREAKS, break_sessions
from views.context import ViewContext


def render(ctx: ViewContext) -> None:
    """Draw the tab.

    Moved out of app.py in M2 step 2.4, then de-scaffolded in DEBT-028. The
    move itself was verbatim — same statements, same order, same indentation
    — and each body was proved byte-identical to app.py's before anything
    here was renamed. That evidence is now spent: this file reads `ctx.` in
    place of the rebind preamble the move needed, so the comparison that
    justified it no longer applies and the before/after RENDER comparison is
    what stands behind this file instead (ADR-038).

    Carried across unchanged: `use_container_width` on the chart (DEBT-029),
    and the naive wall-clock x-axis the rangebreaks below require (DEBT-030,
    two of its ten chart sites are in this file).
    """
    st.markdown(
        '<div class="sh"><span class="sh-ico">🎯</span>'
        '<span class="sh-ttl">Strike Detail</span></div>',
        unsafe_allow_html=True,
    )

    sd_period_label = st.radio(
        "Period",
        ["Today", "5D", "10D", "20D"],
        horizontal=True,
        key="sd_period_radio",
    )
    sd_period_days = {"Today": 1, "5D": 5, "10D": 10, "20D": 20}[sd_period_label]

    sd_left, sd_right = st.columns([1, 3])

    with sd_left:
        st.markdown('<div class="sh"><span class="sh-ttl">Expiry Detail</span></div>', unsafe_allow_html=True)
        for exp_label_s, exp_date, dte_val in [
            ("Front", ctx.front_expiry, ctx.front_dte),
            ("Back",  ctx.back_expiry,  ctx.back_dte),
        ]:
            exp_rows = ctx.load_latest_atm_iv(exp_date, ctx.snapshot_id, n=2)
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
        st.markdown('<div class="sh"><span class="sh-ttl">Strike Detail</span></div>', unsafe_allow_html=True)

        if ctx.strikes_set:
            fc_call = iv_engine.strike_contract(ctx.chain_df, ctx.front_expiry, ctx.call_strike, "CALL")
            bc_call = iv_engine.strike_contract(ctx.chain_df, ctx.back_expiry,  ctx.call_strike, "CALL")
            fc_put  = iv_engine.strike_contract(ctx.chain_df, ctx.front_expiry, ctx.put_strike,  "PUT")
            bc_put  = iv_engine.strike_contract(ctx.chain_df, ctx.back_expiry,  ctx.put_strike,  "PUT")

            for leg_label, fc, bc in [
                (f"Put  {ctx.put_strike:.0f}",  fc_put,  bc_put),
                (f"Call {ctx.call_strike:.0f}", fc_call, bc_call),
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

    with sd_right:
        st.markdown('<div class="sh"><span class="sh-ttl">Selected-Strike IV</span></div>', unsafe_allow_html=True)
        st.caption("Front vs back IV at your trade strikes — ratio on right axis.")

        if ctx.strikes_set:
            fch = ctx.load_contract_hist(ctx.front_expiry, ctx.call_strike, "CALL", sd_period_days)
            bch = ctx.load_contract_hist(ctx.back_expiry,  ctx.call_strike, "CALL", sd_period_days)
            fph = ctx.load_contract_hist(ctx.front_expiry, ctx.put_strike,  "PUT",  sd_period_days)
            bph = ctx.load_contract_hist(ctx.back_expiry,  ctx.put_strike,  "PUT",  sd_period_days)

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
                    # BUG-002: without this the three call traces draw a
                    # straight connector across holidays, weekends and collector
                    # outages, inventing IV movement that never happened. Must
                    # come AFTER call_ratio is computed so the inserted breaker
                    # rows carry NaN in the ratio column too. (ADR-006)
                    cm = break_sessions(cm)
                    fig_str.add_trace(go.Scatter(
                        x=cm["timestamp"], y=cm["f_call"],
                        name=f"Front {ctx.call_strike:.0f}C",
                        line=dict(color=ctx.chart_colors["front_iv"], width=1.5), yaxis="y1"))
                    fig_str.add_trace(go.Scatter(
                        x=cm["timestamp"], y=cm["b_call"],
                        name=f"Back  {ctx.call_strike:.0f}C",
                        line=dict(color=ctx.chart_colors["back_iv"], width=1.5), yaxis="y1"))
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
                    pm = break_sessions(pm)      # BUG-002, as above
                    fig_str.add_trace(go.Scatter(
                        x=pm["timestamp"], y=pm["f_put"],
                        name=f"Front {ctx.put_strike:.0f}P",
                        line=dict(color=ctx.chart_colors["front_iv"], width=1.5, dash="dot"), yaxis="y1"))
                    fig_str.add_trace(go.Scatter(
                        x=pm["timestamp"], y=pm["b_put"],
                        name=f"Back  {ctx.put_strike:.0f}P",
                        line=dict(color=ctx.chart_colors["back_iv"], width=1.5, dash="dot"), yaxis="y1"))
                    fig_str.add_trace(go.Scatter(
                        x=pm["timestamp"], y=pm["put_ratio"],
                        name="Put Ratio (F/B)",
                        line=dict(color="#f05252", width=1.5, dash="dot"), yaxis="y2"))
                _sd_xaxis = (
                    dict(range=[f"{ctx.session_date} 09:30", f"{ctx.session_date} 16:15"],
                         rangebreaks=SESSION_RANGEBREAKS, gridcolor="#0c1928")
                    if sd_period_label == "Today"
                    else dict(rangebreaks=SESSION_RANGEBREAKS, gridcolor="#0c1928")
                )
                fig_str.update_layout(
                    height=420,
                    margin=dict(l=20, r=20, t=10, b=20),
                    paper_bgcolor="#060b12",
                    plot_bgcolor="#060b12",
                    font=dict(family="Inter", color="#6d8fa8", size=11),
                    hovermode="x unified",
                    hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                                    font=dict(color="#dde6f1", size=12)),
                    xaxis=_sd_xaxis,
                    yaxis=dict(title="IV %", side="left",  gridcolor="#0c1928"),
                    yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="left", x=0, bgcolor="rgba(0,0,0,0)"),
                )
                st.plotly_chart(fig_str, use_container_width=True)
            else:
                st.info(
                    f"No per-strike history for {ctx.call_strike:.0f}C / {ctx.put_strike:.0f}P "
                    f"in the selected range. Try 'Today'."
                )
        else:
            st.caption("Enter call and put strikes in the Controls row above.")
