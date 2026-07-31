"""Scanner — Mission Control cards, then the full transform opportunity table.

The cards are the strategy's whole point: pairs whose transform difference has
crossed, or is approaching, the threshold. The table below is the unfiltered
sweep they were picked from.
"""
from __future__ import annotations

import streamlit as st

from core.format import fmt_duration, fmt_eta
from core.scanner import TSCAN_THRESHOLD
from views.context import ViewContext


def _render_mc_section(cards: list[dict], section: str, title: str, icon: str,
                        *, spx_price: float, snapshot_ts: str,
                        show_duration: bool = True,
                        show_live_badge: bool = False) -> None:
    """Draw one titled grid of Mission Control opportunity cards.

    MOVED HERE IN M2 STEP 2.5, and it is the one extraction in this
    milestone that could not be a pure move. In app.py the last two lines
    of the Journal deep-link read `spx_price` and `snap_ts_str` straight
    out of the module namespace — the exact pattern step 2.4 existed to
    remove, and the reason ADR-037 injected this function through the
    context instead of moving it with the rest of the tab.

    They are keyword-only ON PURPOSE. ADR-034 is the record of what a
    signature change costs when a call site is missed: `_exp_label` gained
    a required argument, two of three call sites were updated, and the
    third was a `format_func` passed by reference — invisible to a grep
    for the function name. All 569 tests passed and every tab raised
    TypeError on load. Keyword-only means a positional call cannot
    silently bind `spx_price` to whatever used to sit in that slot; it
    fails loudly instead.
    """
    if not cards:
        return
    st.markdown(
        f'<div class="sh" style="margin-top:.3rem">'
        f'<span class="sh-ico">{icon}</span>'
        f'<span class="sh-ttl">{title}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    n_cols = 3
    for row_start in range(0, len(cards), n_cols):
        row_cards = cards[row_start:row_start + n_cols]
        cols = st.columns(n_cols)
        for c_idx, (card, col) in enumerate(zip(row_cards, cols)):
            gidx = row_start + c_idx
            with col:
                with st.container(key=f"mc_card_{section}_{gidx}"):
                    _new_badge = '<span class="mc-new-badge">NEW</span>' if card["is_new"] else ""
                    _is_live = card.get("is_live", True)
                    if show_live_badge:
                        if _is_live:
                            _live_badge = '<span class="mc-live-badge">LIVE</span>'
                        elif card.get("outside_lookback"):
                            _live_badge = '<span class="mc-stale-badge">OUTSIDE WINDOW</span>'
                        else:
                            _live_badge = '<span class="mc-stale-badge">PAST</span>'
                    else:
                        _live_badge = ""
                    _gap_label = "Gap" if _is_live else "Peak Gap"
                    _gap_class = "gap" if _is_live else "gap gap-stale"
                    # The trend arrow used to ride on the end of the sparkline.
                    # That was removed 2026-07-30 as clutter; the arrow moved
                    # here, onto the number it actually describes.
                    _trend_arrow = " ↑" if card["trend_up"] else ""
                    _metrics = (
                        f'<div><span class="mc-metric-l">{_gap_label}</span>'
                        f'<span class="mc-metric-v {_gap_class}">'
                        f'+{card["gap"]:.2f}{_trend_arrow}</span></div>'
                    )
                    if show_duration and _is_live:
                        _metrics += (
                            f'<div><span class="mc-metric-l">Active</span>'
                            f'<span class="mc-metric-v">{fmt_duration(card["duration"])}</span></div>'
                        )
                    elif show_live_badge and not _is_live:
                        _metrics += (
                            f'<div><span class="mc-metric-l">Last Crossed</span>'
                            f'<span class="mc-metric-v">{card.get("last_seen_ago", "—")}</span></div>'
                        )
                    if show_live_badge and card.get("hit_count") is not None:
                        _metrics += (
                            f'<div><span class="mc-metric-l">Seen</span>'
                            f'<span class="mc-metric-v">{card["hit_count"]}×</span></div>'
                        )
                    if card["iv_ratio"] is not None:
                        _metrics += (
                            f'<div><span class="mc-metric-l">IV Ratio</span>'
                            f'<span class="mc-metric-v">{card["iv_ratio"]:.4f}</span></div>'
                        )
                    _eta_html = (
                        f'<div class="mc-eta">ETA {fmt_eta(card["eta_minutes"])}</div>'
                        if card.get("eta_minutes") is not None else ""
                    )
                    # No sparkline row: the gap's shape over time is what "View
                    # Chart" opens, drawn properly and at a readable size, so a
                    # 10-glyph version on every card was clutter rather than
                    # information. Removed 2026-07-30 at Chandan's request.
                    st.markdown(
                        f'<div class="mc-rank">#{gidx + 1}{_live_badge}{_new_badge}</div>'
                        f'<div class="mc-combo">{int(card["put_strike"])}P / {int(card["call_strike"])}C</div>'
                        f'<div class="mc-expiry">{card["front_label"]} → {card["back_label"]}</div>'
                        f'<div class="mc-metrics">{_metrics}</div>'
                        f'{_eta_html}',
                        unsafe_allow_html=True,
                    )
                    st.markdown('<div class="mc-actions-spacer"></div>', unsafe_allow_html=True)
                    bcol1, bcol2 = st.columns(2)
                    with bcol1:
                        if st.button("View Chart", key=f"viewchart_{section}_{gidx}",
                                     use_container_width=True):
                            # Can't write directly to *_select keys here — those
                            # widgets already rendered earlier in this script run
                            # (Controls Bar comes before Scanner). Stage under
                            # pending_ keys instead; they get promoted to the real
                            # widget keys at the top of the NEXT run, before the
                            # Controls Bar widgets are instantiated.
                            st.session_state["pending_front_expiry"] = card["front_raw"]
                            st.session_state["pending_back_expiry"]  = card["back_raw"]
                            st.session_state["pending_put_strike"]   = card["put_strike"]
                            st.session_state["pending_call_strike"]  = card["call_strike"]
                            st.session_state["active_tab"] = "edge"
                            st.rerun()
                    with bcol2:
                        if st.button("📓 Journal", key=f"journal_{section}_{gidx}",
                                     use_container_width=True):
                            # Deep-link contract for pages/journal.py — that page
                            # should read st.session_state.get("journal_prefill")
                            # on load and pre-populate a new IC-transform entry.
                            st.session_state["journal_prefill"] = dict(
                                type="transform",
                                front_expiry=card["front_raw"], back_expiry=card["back_raw"],
                                put_strike=card["put_strike"], call_strike=card["call_strike"],
                                transform_gap=card["gap"], iv_ratio=card["iv_ratio"],
                                spx_price=spx_price, timestamp=snapshot_ts,
                            )
                            try:
                                st.switch_page("pages/journal.py")
                            except Exception:
                                st.info(
                                    "Trade details staged — open Journal from the "
                                    "sidebar to continue."
                                )


def render(ctx: ViewContext) -> None:
    """Draw the tab.

    Moved out of app.py in M2 step 2.4, then de-scaffolded in DEBT-028. The
    move itself was verbatim — same statements, same order, same indentation
    — and each body was proved byte-identical to app.py's before anything
    here was renamed. That evidence is now spent: this file reads `ctx.` in
    place of the rebind preamble the move needed, so the comparison that
    justified it no longer applies and the before/after RENDER comparison is
    what stands behind this file instead (ADR-038).

    `_render_mc_section` now lives at the top of this file. Step 2.4 injected
    it through the context because it read two of app.py's prelude globals and
    moving it meant a signature change; step 2.5 made that change, and both
    values arrive off the context here (ADR-040).

    Carried across unchanged: `use_container_width` (DEBT-029), and the
    hardcoded threshold comparisons the table shares with DEBT-031.
    """
    # ── Mission Control ─────────────────────────────────────────────────────
    st.markdown(
        '<div class="sh" style="margin-top:.2rem">'
        '<span class="sh-ico">🔥</span>'
        '<span class="sh-ttl">Transform Opportunities</span>'
        f'<span class="sh-bdg g">{ctx.mc["n_eligible"]} Live</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    # Time-range control moved to the Calendar Edge tab (it drives the charts
    # there). The Mission Control lookback window used for these cards is
    # fixed to "Today" now that the picker has been removed from this tab.
    if "mc_lookback_select" not in st.session_state:
        st.session_state["mc_lookback_select"] = "Today"

    if not ctx.mc["non_atm"] and ctx.mc["n_approaching"] == 0:
        st.caption(
            "No non-ATM transform opportunities have ever crossed the threshold yet — "
            "this fills in automatically as the registry accumulates history. "
            "Scanning continues every refresh."
        )
    else:
        if ctx.mc["non_atm"]:
            _lb_label = st.session_state["mc_lookback_select"]
            _render_mc_section(
                ctx.mc["non_atm"][:6], "natm",
                f"Non-ATM Opportunities — live or active within {_lb_label}", "🟢",
                spx_price=ctx.spx_price, snapshot_ts=ctx.snapshot_ts,
                show_live_badge=True,
            )
            _caption_bits = []
            if ctx.mc["n_non_atm_total"] > 6:
                _caption_bits.append(
                    f"Showing top 6 of {ctx.mc['n_non_atm_total']} in the {_lb_label} window."
                )
            if ctx.mc["n_fallback_used"] > 0:
                _caption_bits.append(
                    f"{ctx.mc['n_fallback_used']} shown above fell outside the {_lb_label} "
                    "window — included so this panel is never empty, marked PAST."
                )
            if _caption_bits:
                st.caption(" ".join(_caption_bits))

        _render_mc_section(
            ctx.mc["likely_next"][:3], "likely",
            "Likely Next — gap rising, sorted by soonest ETA", "⏱",
            spx_price=ctx.spx_price, snapshot_ts=ctx.snapshot_ts,
            show_duration=False,
        )

    st.markdown(
        "<div style='margin:.9rem 0 .8rem;border-top:1px solid var(--border)'></div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="sh"><span class="sh-ico">🔍</span>'
        '<span class="sh-ttl">Full Scanner — custom offset &amp; complete table</span></div>',
        unsafe_allow_html=True,
    )
    with st.container():

        # ── Filter panel ─────────────────────────────────────────────────────────
        _offset_options = [0] + list(range(5, 205, 5))

        st.markdown('<div class="filter-panel"><div class="fp-title">Strike Selection</div></div>',
                    unsafe_allow_html=True)

        _sc_c1, _sc_c2, _sc_c3 = st.columns([1, 1, 2])
        with _sc_c1:

            sc_put_offset = st.selectbox(
                "Put Offset from ATM",
                options=_offset_options,
                format_func=lambda v: "ATM" if v == 0 else f"ATM − {v}",
                index=0,
                key="sc_put_offset",
            )
        with _sc_c2:
            sc_call_offset = st.selectbox(
                "Call Offset from ATM",
                options=_offset_options,
                format_func=lambda v: "ATM" if v == 0 else f"ATM + {v}",
                index=0,
                key="sc_call_offset",
            )
        with _sc_c3:
            sc_gap_pts = sc_put_offset + sc_call_offset
            _sym = "symmetric" if sc_put_offset == sc_call_offset else "asymmetric"
            st.markdown(
                f"<p style='margin:.6rem 0 0;font-size:.78rem;color:#6d8fa8;'>"
                f"Strike gap: <span style='color:#dde6f1;font-family:var(--mono);font-weight:600;'>"
                f"{sc_gap_pts} pts</span> &nbsp;·&nbsp; {_sym}</p>",
                unsafe_allow_html=True,
            )

        # ── Scanner compute ───────────────────────────────────────────────────────
        # Cached on (snapshot_id, offsets, max_rows) — st.expander's body still
        # executes every script run even when visually collapsed, so without
        # caching this would redo the full scan on every interaction anywhere
        # in the app. With caching, repeat calls with the same offset/snapshot
        # are a near-instant cache hit.
        with st.spinner("Scanning combinations…"):
            _ts_df = ctx.compute_transform_scanner(
                _chain_df    = ctx.chain_df,
                spx_price    = ctx.spx_price,
                snapshot_id  = ctx.snapshot_id,
                put_offset   = int(sc_put_offset),
                call_offset  = int(sc_call_offset),
                max_rows     = int(ctx.sc_max_rows),
            )

        # ── KPI cards ─────────────────────────────────────────────────────────────
        if not _ts_df.empty:
            _ready_count  = int((_ts_df["Transform Diff"] >= TSCAN_THRESHOLD).sum())
            _best_diff    = float(_ts_df["Transform Diff"].max())
            _best_row     = _ts_df.iloc[0]
            _best_label   = f"Put {int(_best_row['Put Strike'])} / Call {int(_best_row['Call Strike'])}"
            _avg_iv_ratio = (
                _ts_df["IV Ratio"].dropna().mean()
                if "IV Ratio" in _ts_df.columns else None
            )

            # Diff distribution for badge
            _diff_vals      = _ts_df["Transform Diff"]
            _gt5_count      = int((_diff_vals >= 5).sum())
            _best_diff_str  = f"{_best_diff:+.2f}"
            _avg_ratio_str  = f"{_avg_iv_ratio:.4f}" if _avg_iv_ratio else "—"

            # KPI 1 highlight check
            _kpi1_hl = " kpi-hl" if _best_diff >= TSCAN_THRESHOLD else ""
            _kpi2_hl = " kpi-hl" if _ready_count > 0 else ""

            kpi_html = f"""
    <div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
      <div class="kpi-card{_kpi2_hl}">
        <span class="kpi-icon">📡</span>
        <span class="kpi-v c-blue">{len(_ts_df):,}</span>
        <span class="kpi-l">Diagonals Scanned</span>
      </div>
      <div class="kpi-card{_kpi2_hl}">
        <span class="kpi-icon">🎯</span>
        <span class="kpi-v{'  c-green' if _ready_count > 0 else ''}">{_ready_count}</span>
        <span class="kpi-l">Diff &gt; {TSCAN_THRESHOLD:.0f}</span>
      </div>
      <div class="kpi-card{_kpi1_hl}">
        <span class="kpi-icon">✦</span>
        <span class="kpi-v">{_best_diff_str}</span>
        <span class="kpi-l">Best Difference</span>
        <span class="kpi-sub">{_best_label}</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon">⚡</span>
        <span class="kpi-v c-amber">{_avg_ratio_str}</span>
        <span class="kpi-l">Avg IV Ratio</span>
      </div>
    </div>"""

        # ── Ready badge ───────────────────────────────────────────────────────────
        if not _ts_df.empty and _ready_count > 0:
            st.markdown(
                f'<div class="ready-badge"><span class="rdot"></span>'
                f'{_ready_count} combination{"s" if _ready_count > 1 else ""} ready to transform'
                f'&nbsp;·&nbsp;Transform Diff ≥ {TSCAN_THRESHOLD:.0f}</div>',
                unsafe_allow_html=True,
            )

        # ── Main content: table + side panel ─────────────────────────────────────
        if _ts_df.empty:
            st.caption(
                "No valid combinations found — the current chain has no strike/expiry pairs "
                "with marks available for all four diagonal legs plus the two wing strikes. "
                "The collector may not have run yet, or try adjusting the Strike Window "
                "or Liquidity threshold in the sidebar."
            )
        else:
            st.markdown(
                '<div class="sh">'
                '<span class="sh-ico">📋</span>'
                '<span class="sh-ttl">Transformation Opportunities</span>'
                '<span class="sh-bdg">Sorted by Diff ↓</span>'
                '</div>',
                unsafe_allow_html=True,
            )

            def _ts_row_style(row):
                if row["Transform Diff"] >= TSCAN_THRESHOLD:
                    return ["background-color: #0a1d14; color: #10d4a3"] * len(row)
                elif row["Transform Diff"] < 0:
                    return ["color: #f05252"] * len(row)
                return [""] * len(row)

            _ts_display = _ts_df.style.apply(_ts_row_style, axis=1).format({
                "Diagonal Mark":  "{:.2f}",
                "Transform Mark": "{:.2f}",
                "Transform Diff": "{:+.2f}",
                "IV Ratio":       lambda v: f"{v:.4f}" if v is not None else "—",
            })

            _ts_event = st.dataframe(
                _ts_display,
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="scanner_full_table",
                column_config={
                    "Put Strike":     st.column_config.NumberColumn("Put Strike",     format="%d"),
                    "Call Strike":    st.column_config.NumberColumn("Call Strike",    format="%d"),
                    "Diagonal Mark":  st.column_config.NumberColumn("Diag Mark",      format="%.2f"),
                    "Transform Mark": st.column_config.NumberColumn("Transform Mark", format="%.2f"),
                    "Transform Diff": st.column_config.NumberColumn("Transform Diff", format="%+.2f"),
                    "IV Ratio":       st.column_config.NumberColumn("IV Ratio",       format="%.4f"),
                },
            )
            st.caption(
                f"{len(_ts_df)} combinations  ·  "
                "Green = ready to transform (≥ 5)  ·  "
                "Click any header to re-sort  ·  "
                "Click a row, then View Chart, to jump straight to Calendar Edge"
            )

            # Click-to-drill from the full table — select a row, confirm with
            # the button, jump to a pre-scoped Calendar Edge chart. Same
            # pending_ staging pattern as the Mission Control cards, since
            # this also renders after the Controls Bar in script order.
            _selected_rows = []
            try:
                _selected_rows = _ts_event.selection.rows
            except AttributeError:
                _selected_rows = []

            if _selected_rows:
                _sel_row = _ts_df.iloc[_selected_rows[0]]
                _sel_front_raw = str(_sel_row["Front Expiry"]).split(" ")[0]
                _sel_back_raw  = str(_sel_row["Back Expiry"]).split(" ")[0]
                _sc1, _sc2 = st.columns([3, 1])
                with _sc1:
                    st.markdown(
                        f"<p style='margin:.4rem 0 0;font-size:.82rem;color:#6d8fa8;'>"
                        f"Selected: <span style='color:#dde6f1;font-family:var(--mono);font-weight:600;'>"
                        f"{int(_sel_row['Put Strike'])}P / {int(_sel_row['Call Strike'])}C</span>"
                        f"&nbsp;·&nbsp;{_sel_row['Front Expiry']} → {_sel_row['Back Expiry']}"
                        f"&nbsp;·&nbsp;Diff <span style='color:#10d4a3;font-family:var(--mono);'>"
                        f"{_sel_row['Transform Diff']:+.2f}</span></p>",
                        unsafe_allow_html=True,
                    )
                with _sc2:
                    if st.button("→ View Chart", key="scanner_view_selected", use_container_width=True):
                        st.session_state["pending_front_expiry"] = _sel_front_raw
                        st.session_state["pending_back_expiry"]  = _sel_back_raw
                        st.session_state["pending_put_strike"]   = float(_sel_row["Put Strike"])
                        st.session_state["pending_call_strike"]  = float(_sel_row["Call Strike"])
                        st.session_state["active_tab"] = "edge"
                        st.rerun()
