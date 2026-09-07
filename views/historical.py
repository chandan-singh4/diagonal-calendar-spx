"""Historical Statistics — ATM IV ratio range over four lookback windows."""
from __future__ import annotations

import streamlit as st

import config
import iv_engine
from core.series import HISTORICAL_WINDOWS, merge_iv_pair
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
    # The four windows are core.series', so this panel and the React one
    # cannot end up offering different ones -- the drift the Scanner's
    # lookback picker caused, avoided by naming the set once.
    for col, (label, days) in zip(stat_cols, HISTORICAL_WINDOWS):
        # The join is `merge_iv_pair` with the session breaks off: it converts
        # both frames to display time before merging ON that column (DEBT-030,
        # and both sides must agree), and this panel has no time axis for a
        # gap row to gap.
        pm = merge_iv_pair(
            ctx.load_atm_hist_fb(ctx.front_expiry, days),
            ctx.load_atm_hist_fb(ctx.back_expiry,  days),
            config.DISPLAY_TIMEZONE,
            names=("f", "b", "ratio"),
            insert_breaks=False,
        )
        with col:
            st.caption(label)
            if not pm.empty:
                rs       = iv_engine.range_stats(pm["ratio"], ctx.ts_now.ratio)
                pct_rank = iv_engine.percentile_rank(pm["ratio"], ctx.ts_now.ratio)
                # Where a percentile stops being unremarkable is iv_engine's,
                # for the same reason: it is a claim about when a reading is
                # worth noticing, and two screens must not disagree about it.
                _ctx_label, _ctx_color = iv_engine.percentile_band(pct_rank)
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
