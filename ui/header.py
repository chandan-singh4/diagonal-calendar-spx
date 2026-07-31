"""The top bar: price, chips, countdown, Attention Strip, token banner.

FOUR THINGS THAT ALWAYS RENDER, whichever tab is open. That is why they are
here and not in `views/` — the Attention Strip in particular is the reason
Mission Control runs on every script execution rather than only when the
Scanner is showing.

Extracted from app.py in M2 step 2.5. Every HTML string is carried across
character for character; the before/after render comparison compares those
strings exactly, so a single changed space would have shown up as a
difference on every page.
"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from core.format import fmt_duration
from core.market import DailyChange
from ui.sidebar import reauth_command


def render(*, spx_price: float, vix_value: float | None, gex_label: str,
           poll_label: str, poll_interval: int, snap_age_secs: float,
           snap_ts_str: str, change: DailyChange) -> None:
    """Price block on the left, status chips and staleness dot on the right."""
    sign         = "+" if change.points >= 0 else ""
    chg_display  = f"{sign}{change.points:.1f} ({sign}{change.percent:.2f}%)"
    vix_str      = f"{vix_value:.2f}" if vix_value else "N/A"

    # The dot is about STALENESS, not market hours: green under ten minutes,
    # amber under an hour, red beyond. After hours the collector is idle by
    # design, so a red dot overnight is expected rather than a fault.
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
    <span class="spx-price" style="color:{change.color}">{spx_price:,.2f}</span>
    <span class="spx-chg" style="color:{change.color}">{change.arrow} {chg_display}</span>
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

    # Collector-anchored countdown. It ticks in the BROWSER rather than by
    # rerunning the page — a per-second Streamlit rerun to animate a number
    # would be the same mistake the live-refresh fragment exists to undo.
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
                if (el) el.textContent = '⏱ Next update in: ' + n + 's';
            }}, 1000);
        }}
    }})();
    </script>
    """,
        height=22,
    )


def render_attention_strip(mc: dict) -> None:
    """Eligible / Approaching / New counts, and the single best opportunity.

    Persistent on every tab: the whole point is that a crossing is visible
    without being on the Scanner when it happens.
    """
    if mc["best"] is not None:
        _b = mc["best"]
        if _b["is_live"]:
            _best_status = f'Active {fmt_duration(_b["duration"])}'
        else:
            _best_status = f'Peak · seen {_b["last_seen_ago"]}'
        _attn_html = (
            '<div class="attn-strip">'
            '<div class="attn-counts">'
            '<span class="attn-count-item">'
            f'<span class="attn-count-n green">{mc["n_eligible"]}</span>'
            '<span class="attn-count-l">Eligible</span></span>'
            '<span class="attn-count-item">'
            f'<span class="attn-count-n amber">{mc["n_approaching"]}</span>'
            '<span class="attn-count-l">Approaching</span></span>'
            '<span class="attn-count-item">'
            f'<span class="attn-count-n blue">{mc["n_new"]}</span>'
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
            f'<span class="attn-count-n">{mc["n_eligible"]}</span>'
            '<span class="attn-count-l">Eligible</span></span>'
            '<span class="attn-count-item">'
            f'<span class="attn-count-n">{mc["n_approaching"]}</span>'
            '<span class="attn-count-l">Approaching</span></span>'
            '</div>'
            '<div class="attn-divider"></div>'
            '<span class="attn-empty">No non-ATM transform opportunities right now — '
            'scanning every refresh.</span>'
            '</div>'
        )
    st.markdown(_attn_html, unsafe_allow_html=True)


def render_token_banner(token_age_days: float | None, executable: str) -> None:
    """From day 6, warn that the Schwab token is about to expire.

    An expired token means the collector is blind and NO new prices are being
    recorded — the one failure that costs history rather than convenience,
    since the broker will not sell you last Tuesday's prices. Hence a banner
    at the top of the page rather than a line in the sidebar.
    """
    if token_age_days is not None and token_age_days >= 6:
        if token_age_days >= 7:
            st.markdown(
                f"""<div class="spx-token-emergency">
🚨 SCHWAB TOKEN EXPIRED {token_age_days - 7:.1f} days ago — no new prices are being
collected. Paste this into a terminal to re-authenticate:
</div>""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""<div class="spx-token-warning">
⚠️ Schwab API token expires in <strong>{7 - token_age_days:.1f} days</strong>.
Re-authenticate before then to avoid losing a session. Paste this into a terminal:
</div>""",
                unsafe_allow_html=True,
            )
        st.code(reauth_command(executable), language="powershell")
        st.caption(
            "Opens a Schwab login in your browser, then asks you to paste the redirected "
            "URL back. The page will look like an error — that is expected. Your current "
            "token is restored automatically if you cancel partway through."
        )
