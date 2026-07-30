"""Historical Statistics — ATM IV ratio range over four lookback windows."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import iv_engine
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

    """
    st.markdown(
        f'<div class="sh"><span class="sh-ico">📉</span>'
        f'<span class="sh-ttl">Historical Statistics — ATM IV Ratio</span>'
        f'<span class="sh-bdg">{ctx.front_expiry} ({ctx.front_dte}d) / {ctx.back_expiry} ({ctx.back_dte}d)</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    stat_cols = st.columns(4)
    for col, (label, days) in zip(
        stat_cols,
        [("Today", 1), ("5 Days", 5), ("10 Days", 10), ("20 Days", 20)],
    ):
        pf = ctx.load_atm_hist_fb(ctx.front_expiry, days)
        pb = ctx.load_atm_hist_fb(ctx.back_expiry,  days)
        with col:
            st.caption(label)
            if not pf.empty and not pb.empty:
                pm = pd.merge(
                    pf[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "f"}),
                    pb[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "b"}),
                    on="timestamp",
                )
                pm["ratio"] = pm["f"] / pm["b"]
                rs       = iv_engine.range_stats(pm["ratio"], ctx.ts_now.ratio)
                pct_rank = iv_engine.percentile_rank(pm["ratio"], ctx.ts_now.ratio)
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
  <span style="color:#2f4459;">Now</span> <b style="color:#dde6f1;">{ctx.ts_now.ratio:.4f}</b>
  &nbsp;<span style="color:{_ctx_color};font-size:0.88em;">{pct_rank:.0f}th · {_ctx_label}</span>
</div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No data")
