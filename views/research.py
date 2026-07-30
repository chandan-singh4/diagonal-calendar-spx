"""Research — IV ratio against normalized debit, one point per snapshot.

Descriptive only. The OLS line is drawn because a cloud of points is hard to
read, not because a relationship is claimed; the label on it says so, and
that wording is deliberate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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

    Carried across unchanged, and NOT fixed here: `use_container_width` on
    the chart below is DEBT-029, a Streamlit argument whose removal date has
    already passed. It is one of 34 call sites and they want doing in a
    single pass, not smuggled into a move that is supposed to change nothing.
    """
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

    if not ctx.strikes_set:
        st.info("Set call and put strikes in Controls to populate the scatter.")
    else:
        _hist = ctx.load_diagonal_hist(
            ctx.front_expiry, ctx.back_expiry, ctx.call_strike, ctx.put_strike,
            90, ctx.snapshot_id,
        )
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

        if ctx.norm_deb is not None and ctx.ts_now.ratio is not None:
            fig_sc.add_trace(go.Scatter(
                x=[ctx.ts_now.ratio], y=[ctx.norm_deb], mode="markers",
                marker=dict(symbol="diamond", color="#f0a429", size=14,
                            line=dict(color="#78350f", width=1.5)),
                showlegend=True, name="Current",
                hovertemplate=(
                    "<b>Current observation</b><br>"
                    f"SPX: {ctx.spx_price:.0f}<br>"
                    "IV Ratio: %{x:.4f}<br>Norm. Debit: %{y:.4f}<br>"
                    + (f"Diagonal Mark: ${ctx.diag_mark:.2f}" if ctx.diag_mark else "")
                    + "<extra></extra>"
                ),
            ))

        fig_sc.add_vline(
            x=1.0, line=dict(color="#2a3f56", width=1, dash="dot"),
            annotation_text="ratio = 1.0",
            annotation_font=dict(color="#2f4459", size=10),
            annotation_position="top right",
        )
        if not _has_data and ctx.norm_deb is None:
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
