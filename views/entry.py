"""Entry Analysis — what this position costs, and what it is worth transforming.

Eight metric tiles in two rows. Every tile has a fallback string for the case
where its input is missing, and those strings are not interchangeable: "set
strikes" and "Greeks N/A" mean different things to whoever is reading the
screen, and BUG-010's lesson is that a missing calculation must never look
like a computed one.
"""
from __future__ import annotations

import streamlit as st

from views.context import ViewContext


def render(ctx: ViewContext) -> None:
    """Draw the tab.

    A verbatim move of app.py's Entry Analysis body — see views/historical.py
    for why the body is untouched and only the rebinds below are new.

    Carried across unchanged, and NOT fixed here: the two `_ic_signal > 5`
    tests and the local `_THRESHOLD = 5.0` are three hardcoded copies of a
    threshold that `core/scanner.py` already defines as `_TSCAN_THRESHOLD`,
    and one of them uses `>` where everything else uses `>=`. That is a real
    defect and it is DEBT-031. It is not this commit's, because a move that
    silently changes a trading threshold is not a move.
    """
    strikes_set = ctx.strikes_set
    _diag_mark = ctx.diag_mark
    _norm_deb = ctx.norm_deb
    _straddle = ctx.straddle
    _theta_diff = ctx.theta_diff
    _ic_mark = ctx.ic_mark
    _iv_pct = ctx.iv_pct
    _liquidity = ctx.liquidity


    st.markdown(
        '<div class="sh"><span class="sh-ico">📊</span>'
        '<span class="sh-ttl">Entry Analysis</span></div>',
        unsafe_allow_html=True,
    )

    # ── Row 1: position cost + theta ─────────────────────────────────────────
    r1a, r1b, r1c, r1d = st.columns(4)

    with r1a:
        if _diag_mark is not None:
            _diag_dollar = int(round(_diag_mark * 100))
            st.metric(
                "Diagonal Mark",
                f"{_diag_mark:.2f} pts  ·  ${_diag_dollar:,}",
                help="Per-share mark price of the diagonal × 100 = dollar cost per contract.",
            )
        else:
            st.metric("Diagonal Mark", "— (set strikes)")
        st.caption("What you'd pay to open this position right now.")

    with r1b:
        st.metric(
            "ATM Straddle",
            f"${_straddle:.2f}" if _straddle else "—",
            help="S × σ × √(2·DTE/365·π). The market's expected ±1σ move by front expiry.",
        )
        st.caption("How big a move the market expects by front expiry.")

    with r1c:
        st.metric(
            "Normalized Debit",
            f"{_norm_deb:.4f}" if _norm_deb is not None else "— (set strikes)",
            help="Diagonal Mark ÷ ATM Straddle. Removes SPX price-level and vol-regime "
                 "effects so entry cost is comparable across different dates. HYPOTHESIS.",
        )
        st.caption("Is this cheap or expensive relative to expected market movement?")

    with r1d:
        if _theta_diff is not None and _theta_diff.available:
            _net_ct_s = (
                f"+${_theta_diff.net_daily_theta_ct:.2f}"
                if _theta_diff.net_daily_theta_ct >= 0
                else f"−${abs(_theta_diff.net_daily_theta_ct):.2f}"
            )
            st.metric(
                "Net Daily θ / contract",
                _net_ct_s,
                help="Position earns this much per day from time decay alone. "
                     "Front decays faster than back — the difference is your daily gain. "
                     "HYPOTHESIS — not yet validated as entry predictor.",
            )
            st.caption(
                f"Front θ {_theta_diff.front_sum:+.3f} · "
                f"Back θ {_theta_diff.back_sum:+.3f} · "
                f"Net {_theta_diff.net_daily_theta:+.3f} /sh/day"
            )
        else:
            st.metric(
                "Net Daily θ / contract",
                "— (set strikes)" if not strikes_set else "— (Greeks N/A)",
            )
            st.caption("How much time decay earns you each calendar day.")

    st.markdown("<div style='margin-bottom:4px'></div>", unsafe_allow_html=True)

    # ── Row 2: Transform-to-IC + market conditions ────────────────────────────
    r2a, r2b, r2c, r2d = st.columns(4)

    with r2a:
        if _ic_mark is not None and _diag_mark is not None:
            _ic_signal = _ic_mark - _diag_mark
            _ic_color  = "#10d4a3" if _ic_signal > 5 else "#dde6f1"
            _ic_dollar = int(round(_ic_mark * 100))
            st.metric(
                "Transform Order Mark",
                f"{_ic_mark:.2f} pts  ·  ${_ic_dollar:,}",
                help="Credit value of the resulting IC after transformation: "
                     "short back legs minus long wings at ±5. "
                     "Green when IC Mark − Diagonal Mark > $5 (favorable to transform). "
                     "HYPOTHESIS — signal not yet validated.",
            )
            st.markdown(
                f"<p style='margin:0;font-size:0.78em;color:{_ic_color};'>"
                f"vs Diagonal: {_ic_signal:+.2f} pts"
                f"{'  ✓ Transformation favorable' if _ic_signal > 5 else ''}"
                f"</p>",
                unsafe_allow_html=True,
            )
        else:
            st.metric("Transform Order Mark", "— (set strikes)" if not strikes_set
                      else "— (wing strikes not in chain)")
            st.caption("Value of IC after transforming diagonal at these strikes.")

    with r2b:
        st.metric(
            "IV Ratio Percentile",
            f"{_iv_pct:.0f}th" if _iv_pct is not None else "— (need history)",
            help="Where today's IV ratio ranks within the last 90 days. "
                 "100th = front has never been this expensive relative to back.",
        )
        st.caption("Is today's term structure unusually steep or flat?")

    with r2c:
        st.metric(
            "Liquidity (ATM)",
            f"{_liquidity:.0f} / 100",
            help="Composite of ATM front-strike volume and open interest. "
                 "Higher = tighter bid/ask and easier fills. Below 50 = expect wider slippage.",
        )
        st.caption("How easy will it be to get filled near the mark price?")

    with r2d:
        _THRESHOLD = 5.0
        if _ic_mark is not None and _diag_mark is not None:
            _diff = _ic_mark - _diag_mark
            if _diff >= _THRESHOLD:
                st.metric("Transform Difference", f"+{_diff:.2f}")
                st.markdown(
                    "<div style='margin-top:2px;padding:6px 10px;border-radius:8px;"
                    "background:rgba(16,212,163,.08);border:1px solid rgba(16,212,163,.25);'>"
                    "<span style='color:#10d4a3;font-size:0.84em;font-weight:600;'>"
                    "✓ Transformation threshold reached</span><br>"
                    f"<span style='color:#6d8fa8;font-size:0.76em;'>"
                    f"Ready to transform · +{_diff:.2f} pts above threshold</span>"
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                _remaining = _THRESHOLD - _diff
                _progress  = max(0.0, min(1.0, _diff / _THRESHOLD))
                _filled    = int(_progress * 10)
                _bar       = "█" * _filled + "░" * (10 - _filled)
                st.metric("Transform Difference", f"{_diff:.2f}",
                          help=f"Transform Order Mark − Diagonal Mark. Green when ≥ {_THRESHOLD}.")
                st.markdown(
                    f"<div style='margin-top:4px;font-size:0.78em;color:#6d8fa8;'>"
                    f"<span style='color:#f0a429;font-family:JetBrains Mono,monospace;'>{_bar}</span>"
                    f"&nbsp;{_progress*100:.0f}%<br>"
                    f"<span style='color:#2f4459;'>{_remaining:.2f} pts until threshold</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.metric("Transform Difference", "— (set strikes)")
            st.caption(f"Needs {_THRESHOLD} pts to trigger transformation signal.")
