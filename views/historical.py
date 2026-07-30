"""Historical Statistics — ATM IV ratio range over four lookback windows."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import iv_engine
from views.context import ViewContext


def render(ctx: ViewContext) -> None:
    """Draw the tab.

    The body below is the code that stood at app.py lines 3744-3787, moved
    unchanged — same statements, same order, same indentation, since a tab
    body sat one level in under `if st.session_state[...]:` and sits one
    level in under `def render(...)` too. The only new lines are the rebinds
    directly beneath this docstring, which reattach the names the body was
    written with to the context that now supplies them.

    That is deliberate. This layer has no tests and cannot really get them —
    a panel whose rows come back reordered still looks like a panel. What
    can be checked is that nothing was rewritten in transit, and that is
    only checkable if the diff is a pure move. Tidying the names is DEBT-028,
    after every tab is out.
    """
    front_expiry = ctx.front_expiry
    front_dte = ctx.front_dte
    back_expiry = ctx.back_expiry
    back_dte = ctx.back_dte
    ts_now = ctx.ts_now
    _load_atm_hist_fb = ctx.load_atm_hist_fb

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
