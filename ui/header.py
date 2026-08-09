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
           snap_ts_str: str, change: DailyChange,
           expected_interval: int | None = None) -> None:
    """Price block on the left, status chips and staleness dot on the right.

    expected_interval — how many seconds SHOULD pass between prices right now
    (60 in the first and last half hour, 300 midday), or None when the market
    is shut. It is the collector's own polling interval, passed in from app.py
    via core.session rather than restated here; see core/session.py for why
    that matters. Defaulting to None keeps every existing caller working and
    degrades to "no expectation", which is the safe direction: the strip says
    how old the data is and declines to call it late.
    """
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

    _render_liveness_strip(snap_age_secs, expected_interval)


# ─────────────────────────────────────────────────────────────────────────────
# The liveness strip
# ─────────────────────────────────────────────────────────────────────────────

# How late is late. Chandan's threshold is "over 5 minutes midday, over a
# minute in the first and last half hour" — which is exactly the collector's
# polling interval, so it arrives as `expected_interval` rather than as a
# second copy of those numbers (core/session.py).
#
# WHY TWO STAGES AND NOT ONE. At a 300-second cadence the age reaches 300
# seconds immediately before every new price lands — that is the cadence
# working, not failing. Turning red exactly on the threshold would flash red
# once per cycle, all day, and a warning that fires when nothing is wrong is
# one you stop reading by Wednesday. So the threshold Chandan named turns the
# number AMBER (it is now later than it should be), and half as long again
# turns it RED (it is now late enough that something is wrong). Both are
# computed in the browser as the number ticks, so the colour changes while
# you are watching rather than only on the next redraw.
_RED_MULTIPLE = 1.5


def _render_liveness_strip(snap_age_secs: float, expected_interval: int | None) -> None:
    """A wall clock that ticks, and the age of the newest price, ticking upward.

    REPLACED "Next update in: 42s" (M3.4, Chandan's call). The countdown ran
    toward a moment rather than away from one, so its worst case — the
    collector dead, no price for an hour — displayed as `0s` and sat there,
    which reads like everything is fine. Counting upward has no such resting
    state: the longer it is broken, the louder the number gets.

    TWO SEPARATE CLAIMS, AND THEY ARE NOT THE SAME CLAIM. The wall clock says
    *this page is alive* — it is the answer to "has the dashboard frozen?".
    The age says *the prices are alive*. Be clear about the wall clock's limit:
    it ticks in the browser, so it would keep ticking even if the dashboard's
    Python engine died behind it. It proves the tab is not frozen. It does not
    prove the data is fresh, which is why the number beside it exists, and why
    neither of them is a substitute for the watchdog (M3.4) that runs whether
    or not this page is open.

    Both tick in the BROWSER rather than by rerunning the page — a per-second
    Streamlit rerun to animate a number would be the same mistake the
    live-refresh fragment exists to undo (see ui/refresh.py, BUG-020).
    """
    age0 = max(0, int(snap_age_secs))

    if expected_interval is None:
        # Market shut: the collector is idle BY DESIGN, so there is no such
        # thing as late. Saying so beats a red number every evening — see the
        # note in core/session.py about alarms that cry wolf nightly.
        amber_at, red_at, closed = -1, -1, True
    else:
        amber_at, red_at, closed = expected_interval, int(expected_interval * _RED_MULTIPLE), False

    components.html(
        f"""
    <div style="font-family:'Inter',sans-serif;font-size:0.72em;
                color:#2f4459;padding:0;margin:-10px 0 4px 0;">
        <span id="spx-clock">--:--:--</span>
        <span style="opacity:0.45;margin:0 6px;">·</span>
        <span>Time since last data:</span>
        <span id="spx-age" style="font-weight:600;">{_fmt_age(age0)}</span>
        <span id="spx-note" style="opacity:0.7;"></span>
    </div>
    <script>
    (function(){{
        var age     = {age0};
        var amberAt = {amber_at};
        var redAt   = {red_at};
        var closed  = {"true" if closed else "false"};

        var elClock = document.getElementById('spx-clock');
        var elAge   = document.getElementById('spx-age');
        var elNote  = document.getElementById('spx-note');

        function fmt(s) {{
            var m = Math.floor(s / 60), r = s % 60;
            if (m >= 60) {{
                var h = Math.floor(m / 60);
                return h + 'h ' + (m % 60) + 'm';
            }}
            return m > 0 ? (m + 'm ' + r + 's') : (r + 's');
        }}

        function paint() {{
            if (elAge) elAge.textContent = fmt(age);
            if (closed) {{
                if (elAge)  elAge.style.color = '#2f4459';
                if (elNote) elNote.textContent = ' — market closed, collector idle';
                return;
            }}
            var col = '#10d4a3', note = '';
            if (age >= redAt)        {{ col = '#f05252'; note = ' — LATE, check the collector'; }}
            else if (age >= amberAt) {{ col = '#f0a752'; note = ' — later than expected'; }}
            if (elAge)  elAge.style.color = col;
            if (elNote) {{ elNote.textContent = note; elNote.style.color = col; }}
        }}

        function tick() {{
            age += 1;
            if (elClock) {{
                // Eastern, to match every other time on this page and the
                // market sessions the thresholds come from.
                elClock.textContent = '🕐 ' + new Date().toLocaleTimeString('en-GB', {{
                    timeZone: 'America/New_York', hour12: false
                }}) + ' ET';
            }}
            paint();
        }}

        // One clock only. Without this, every rerun would leave its interval
        // running and the number would accelerate — one tick per second per
        // rerun the tab has ever done.
        if (window.__spxLive) clearInterval(window.__spxLive);
        paint();
        tick();
        window.__spxLive = setInterval(tick, 1000);
    }})();
    </script>
    """,
        height=22,
    )


def _fmt_age(secs: int) -> str:
    """Server-side first paint. Mirrors the `fmt` above so the number does not
    visibly change shape in the instant before the first browser tick."""
    m, r = divmod(int(secs), 60)
    if m >= 60:
        return f"{m // 60}h {m % 60}m"
    return f"{m}m {r}s" if m else f"{r}s"


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
