"""Calendar Edge — the IV-ratio history that the whole strategy is timed on.

The largest tab by a distance, and the least covered by any check: ten of the
repo's chart sites are here, none of which a test can look inside. It is
extracted last for that reason.

It also WRITES, which no other view does — entry locks are created and
cleared, and the eligibility registry is backfilled. Those are user actions
behind buttons, not a side effect of drawing, and they go through injected
callables so this module never learns where any file lives.
"""
from __future__ import annotations

from time import perf_counter as _perf_counter

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config
import iv_engine
from core.charts import (
    SESSION_RANGEBREAKS,
    banded_ratio_traces,
    break_sessions,
    to_display_time,
)
from views.context import ViewContext


# Moved here with the tab rather than injected: unlike every other helper the
# edge tab uses, this one touches nothing but `st` — no file, no directory, no
# configuration — and this is its only caller.
def _render_note(text: str, *, kind: str = "info", icon: str | None = None) -> None:
    """Render a small, unobtrusive explanation callout below a chart.

    Keeps the dashboard self-explanatory without adding bulk. Use sparingly —
    at most one per visualization — per the information-density-first principle.

    kind: "info" (blue accent) | "good" (green accent).
    """
    _ic = icon if icon is not None else ("✓" if kind == "good" else "ℹ")
    _cls = "note good" if kind == "good" else "note"
    st.markdown(
        f'<div class="{_cls}"><span class="note-ic">{_ic}</span><span>{text}</span></div>',
        unsafe_allow_html=True,
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

    `config` IS imported here, unlike in the other views, for one line:
    `config.DISPLAY_TIMEZONE`. It survived DEBT-028 because there is nothing
    on the context to inline it to — it is a setting, not per-render state.
    A layer test stops that import becoming a door to `config.DB_PATH`.

    Carried across unchanged: `use_container_width` (DEBT-029), the hardcoded
    threshold DEBT-031 also covers, and the naive wall-clock timestamps the
    rangebreaks require — this file holds most of DEBT-030's ten chart sites,
    and is the reason that fix was deferred until after 2.4.
    """
    _ce_ttl_col, _ce_locks_col = st.columns([4, 1.3])
    with _ce_ttl_col:
        st.markdown(
            '<div class="sh"><span class="sh-ico">📈</span>'
            f'<span class="sh-ttl">Calendar Edge</span>'
            f'<span class="sh-bdg">{iv_engine.interpret_curve(ctx.ts_now)[:30]}</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    with _ce_locks_col:
        st.markdown('<div style="text-align:right;margin-top:.15rem;">', unsafe_allow_html=True)
        _current_combo_key = (
            ctx.entry_lock_key(ctx.front_expiry, ctx.back_expiry, ctx.put_strike, ctx.call_strike)
            if ctx.strikes_set else None
        )
        ctx.render_all_locks_popover(_current_combo_key)
        st.markdown('</div>', unsafe_allow_html=True)

    # Metrics row
    iv_index = float(ctx.chain_df.groupby("expiry")["iv"].mean().mean())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("ATM IV Ratio (F/B)", f"{ctx.ts_now.ratio:.4f}")
    m2.metric("Front ATM IV",       f"{ctx.ts_now.front_iv:.2f}%")
    m3.metric("Back ATM IV",        f"{ctx.ts_now.back_iv:.2f}%")
    m4.metric("IV Index (avg)",     f"{iv_index:.2f}%")

    period_label = st.session_state.get("period_radio", "Today")

    period_days = {"Today": 1, "5D": 5, "10D": 10, "20D": 20}[period_label]
    # DEBT-030: the reads hand back zoned UTC now, so the wall-clock the
    # rangebreaks need is applied HERE, at the point of drawing.
    _fhp = to_display_time(ctx.load_atm_hist_fb(ctx.front_expiry, period_days),
                           config.DISPLAY_TIMEZONE)
    _bhp = to_display_time(ctx.load_atm_hist_fb(ctx.back_expiry,  period_days),
                           config.DISPLAY_TIMEZONE)
    atm_merged = pd.DataFrame()
    if not _fhp.empty and not _bhp.empty:
        atm_merged = pd.merge(
            _fhp[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "front_iv"}),
            _bhp[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "back_iv"}),
            on="timestamp", how="inner",
        )
        atm_merged["iv_ratio"] = atm_merged["front_iv"] / atm_merged["back_iv"]
        atm_merged = break_sessions(atm_merged)

    # Both synced charts autorange independently unless given an explicit
    # range, and their underlying queries don't cover the same history:
    # get_transform_mark_history() drops any snapshot missing ANY of the six
    # option legs it needs (including the ±5 wing strikes), while the ATM IV
    # series is expiry-level and has no such strike dependency. For a given
    # strike/expiry combo, Chart 1 can therefore have materially less history
    # than Chart 2/3 even over the "same" 5D/10D/20D window — which is
    # exactly what was making Chart 1 appear to start days later. Anchoring
    # every chart's x-axis to the *fuller* series (ATM IV) makes the missing
    # portion show as an honest gap in Chart 1 rather than a silently
    # shifted, seemingly-synced axis.
    if period_label == "Today":
        _shared_range = [f"{ctx.session_date} 09:30", f"{ctx.session_date} 16:15"]
    elif not atm_merged.empty:
        _shared_range = [atm_merged["timestamp"].min(), atm_merged["timestamp"].max()]
    else:
        _shared_range = None

    _gap_xaxis = dict(rangebreaks=SESSION_RANGEBREAKS, gridcolor="#0c1928")
    if _shared_range is not None:
        _gap_xaxis["range"] = _shared_range

    # Shared left/right margins so 9:30 AM lands at the identical pixel
    # position on every synced chart below, regardless of each chart's own
    # y-axis label width or legend layout.
    _SYNC_MARGIN_L, _SYNC_MARGIN_R = 58, 20

    def _add_market_open_lines(fig, ts_series: pd.Series, **vline_kwargs) -> None:
        """Subtle vertical dotted line at 9:30 AM for each trading day present
        in ts_series. Skipped entirely for the Today view, where a single
        9:30 marker at the left edge of the chart adds no information."""
        if period_label == "Today" or ts_series is None or ts_series.empty:
            return
        for _day in sorted(pd.to_datetime(ts_series).dt.date.unique()):
            _open_ts = pd.Timestamp(f"{_day} 09:30")  # naive, matches naive plotted timestamps
            fig.add_vline(
                x=_open_ts, line_width=1, line_dash="dot",
                line_color="#3a5170", opacity=0.6, **vline_kwargs,
            )

    # ── Chart 1 (primary): Diagonal Mark vs Transform Order Mark ─────────────
    _c1_ttl_col, _c1_radio_col = st.columns([3, 1.6])
    with _c1_ttl_col:
        st.markdown(
            '<div class="sh" style="margin-top:.4rem">'
            '<span class="sh-ico">🟢</span>'
            '<span class="sh-ttl">Diagonal vs. Transform Order Mark</span>'
            '<span class="sh-bdg g">Shaded = Transform Gap ≥ 5</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    with _c1_radio_col:
        st.markdown('<div style="text-align:right">', unsafe_allow_html=True)
        period_label = st.radio(
            "Chart Range",
            ["Today", "5D", "10D", "20D"],
            horizontal=True,
            key="period_radio",
            label_visibility="collapsed",
        )
        st.markdown('</div>', unsafe_allow_html=True)

    if not ctx.strikes_set:
        st.caption("Set call and put strikes in Controls above to see the Transform Gap chart.")
    else:
        _perf_marks0 = _perf_counter()
        _gap_df = ctx.load_transform_marks(
            ctx.front_expiry, ctx.back_expiry, ctx.call_strike, ctx.put_strike,
            period_days, ctx.snapshot_id,
        )
        _perf_marks_ms = (_perf_counter() - _perf_marks0) * 1000.0

        if not _gap_df.empty:
            _gap_df["timestamp"] = (
                pd.to_datetime(_gap_df["snapshot_timestamp"], format="ISO8601", utc=True)
                .dt.tz_convert(config.DISPLAY_TIMEZONE)
                .dt.tz_localize(None)  # naive wall-clock: required by Plotly rangebreaks
            )
            if period_label == "Today":
                _last_date = _gap_df["timestamp"].dt.date.max()
                _gap_df = _gap_df[_gap_df["timestamp"].dt.date == _last_date]

        if not _gap_df.empty:
            _gap_df["diagonal_mark"] = (
                _gap_df["back_call_mark"] + _gap_df["back_put_mark"]
                - _gap_df["front_call_mark"] - _gap_df["front_put_mark"]
            )
            _gap_df["transform_mark"] = (
                _gap_df["back_call_mark"] + _gap_df["back_put_mark"]
                - _gap_df["front_wing_call_mark"] - _gap_df["front_wing_put_mark"]
            )
            _gap_df["transform_gap"] = _gap_df["transform_mark"] - _gap_df["diagonal_mark"]

            # Opportunistic registry backfill — see _backfill_eligible_history
            # docstring. Only meaningful for non-ATM combos, matching the
            # registry's scope everywhere else.
            if ctx.put_strike != ctx.call_strike:
                ctx.backfill_eligible_history(
                    ctx.front_expiry, ctx.back_expiry, ctx.put_strike, ctx.call_strike,
                    _gap_df.rename(columns={"transform_gap": "gap"})[["timestamp", "gap"]],
                )

            _gap_df = break_sessions(_gap_df)

            # ── Entry Lock — position management mode ────────────────────────
            # Once a diagonal is actually filled, the trader stops caring where
            # a *new* hypothetical diagonal prices today and instead wants to
            # track their *fixed* entry against the live Transform Order Mark.
            # Locking freezes diagonal_mark at the moment of the click; the
            # chart and the metrics below switch from "discovery" framing to
            # "position management" framing for this exact strike/expiry combo.
            _lock = ctx.get_entry_lock(ctx.front_expiry, ctx.back_expiry, ctx.put_strike, ctx.call_strike)
            _lock_toggle_key = f"_show_lock_panel_{ctx.entry_lock_key(ctx.front_expiry, ctx.back_expiry, ctx.put_strike, ctx.call_strike)}"

            _lk_l, _lk_m = st.columns([3, 2])
            with _lk_l:
                if _lock is None:
                    if st.button("🔒 Lock Entry Here", key="lock_entry_btn",
                                 help="Freeze the current Diagonal Mark as your entry price "
                                      "and switch this chart to position-management mode."):
                        st.session_state[_lock_toggle_key] = True
                else:
                    _locked_dt = pd.Timestamp(_lock["locked_at"])
                    st.markdown(
                        f'<div class="ready-badge" style="margin-bottom:0;">'
                        f'<span class="rdot"></span>Entry locked: '
                        f'${_lock["entry_diagonal_mark"]:.2f} @ {_locked_dt.strftime("%m/%d %I:%M %p")}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            with _lk_m:
                if _lock is not None:
                    _mom_col, _clear_col = st.columns([2, 1])
                    with _mom_col:
                        _momentum_window_min = st.selectbox(
                            "Momentum window", [15, 30, 60, 120],
                            index=1, key="momentum_window_min",
                            format_func=lambda m: f"Momentum: last {m} min",
                            label_visibility="collapsed",
                        )
                    with _clear_col:
                        if st.button("Clear", key="clear_entry_lock_btn",
                                     help="Remove this entry lock and return to discovery mode."):
                            ctx.clear_entry_lock(ctx.front_expiry, ctx.back_expiry, ctx.put_strike, ctx.call_strike)
                            st.rerun()

            if _lock is None and st.session_state.get(_lock_toggle_key):
                with st.container(border=True):
                    _current_diag = float(_gap_df.iloc[-1]["diagonal_mark"])
                    st.markdown(
                        f"**Lock entry at current Diagonal Mark: ${_current_diag:.2f}**  \n"
                        f"Put {ctx.put_strike:.0f} / Call {ctx.call_strike:.0f} · "
                        f"{ctx.front_expiry} → {ctx.back_expiry}"
                    )
                    _lock_mode_choice = st.radio(
                        "How should this lock behave?",
                        ["Monitor Only", "Monitor + Log Trade"],
                        key="lock_mode_choice",
                        label_visibility="collapsed",
                        horizontal=True,
                    )
                    if _lock_mode_choice == "Monitor + Log Trade":
                        st.caption(
                            "Journal integration isn't wired up yet (Journal changes are scoped for later). "
                            "This will lock as Monitor Only for now; your choice is saved so the eventual "
                            "Journal entry can be created from this same lock record instead of a second one."
                        )
                    _cf1, _cf2 = st.columns(2)
                    with _cf1:
                        if st.button("Confirm Lock", key="confirm_lock_btn", type="primary",
                                     use_container_width=True):
                            _mode = "monitor_and_log" if _lock_mode_choice == "Monitor + Log Trade" else "monitor_only"
                            ctx.create_entry_lock(ctx.front_expiry, ctx.back_expiry, ctx.put_strike, ctx.call_strike,
                                                _current_diag, _mode)
                            st.session_state[_lock_toggle_key] = False
                            st.rerun()
                    with _cf2:
                        if st.button("Cancel", key="cancel_lock_btn", use_container_width=True):
                            st.session_state[_lock_toggle_key] = False
                            st.rerun()

            _perf_build0 = _perf_counter()
            fig_gap = go.Figure()

            # Shade every contiguous region where Transform Gap >= 5
            _flag = (_gap_df["transform_gap"] >= 5.0).reset_index(drop=True)
            _ts_list = _gap_df["timestamp"].reset_index(drop=True).tolist()
            _region_start = None
            for i in range(len(_flag)):
                if _flag.iloc[i] and _region_start is None:
                    _region_start = _ts_list[i]
                if _region_start is not None and (not _flag.iloc[i] or i == len(_flag) - 1):
                    fig_gap.add_vrect(
                        x0=_region_start, x1=_ts_list[i],
                        fillcolor="rgba(16,212,163,0.14)",
                        line_width=0, layer="below",
                    )
                    _region_start = None

            # SPX underlying: invisible line on an overlaid, hidden axis. It draws
            # nothing, but places "SPX" at the top of the unified hover tooltip so
            # every readout leads with WHERE PRICE WAS when the marks moved.
            if "spx" in _gap_df.columns and _gap_df["spx"].notna().any():
                fig_gap.add_trace(go.Scatter(
                    x=_gap_df["timestamp"], y=_gap_df["spx"],
                    name="SPX", yaxis="y2", mode="lines",
                    line=dict(width=0, color="rgba(0,0,0,0)"),
                    showlegend=False,
                    hovertemplate="SPX: %{y:,.2f}<extra></extra>",
                ))

            if _lock is not None:
                # Position-management mode: the live diagonal_mark line becomes
                # dimmed reference context, and a fixed dashed line marks entry.
                fig_gap.add_trace(go.Scatter(
                    x=_gap_df["timestamp"], y=_gap_df["diagonal_mark"],
                    name="Live Diagonal Mark (hypothetical)",
                    line=dict(color=ctx.chart_colors["diagonal_mark"], width=1.2, dash="dot"),
                    opacity=0.45,
                    hovertemplate="Live Diagonal Mark: $%{y:.2f}<extra></extra>",
                ))
                fig_gap.add_hline(
                    y=_lock["entry_diagonal_mark"], line_width=1.6, line_dash="dash",
                    line_color=ctx.chart_colors["diagonal_mark"],
                    annotation_text=f"Entry ${_lock['entry_diagonal_mark']:.2f}",
                    annotation_position="right",
                    annotation_font=dict(size=10, color=ctx.chart_colors["diagonal_mark"]),
                )
            else:
                fig_gap.add_trace(go.Scatter(
                    x=_gap_df["timestamp"], y=_gap_df["diagonal_mark"],
                    name="Diagonal Mark",
                    line=dict(color=ctx.chart_colors["diagonal_mark"], width=1.8),
                    hovertemplate="Diagonal Mark: $%{y:.2f}<extra></extra>",
                ))
            # Gap / live-difference series (computed first so it can feed the
            # unified master tooltip below).
            if _lock is not None:
                # Position management: signed, against the fixed entry —
                # Current Transform Order Mark − Fixed Entry Diagonal Mark.
                _diff_series = _gap_df["transform_mark"] - _lock["entry_diagonal_mark"]
                _diff_hover_label = "Live Difference (vs. entry)"
            else:
                # Discovery mode: unsigned distance between two live values.
                _diff_series = (_gap_df["transform_mark"] - _gap_df["diagonal_mark"]).abs()
                _diff_hover_label = "Gap"

            # A SINGLE master tooltip, carried by the Transform trace, in a FIXED
            # line order: SPX, Diagonal, Transform, Gap. Every other trace's hover
            # is suppressed (loop below), so the unified tooltip shows exactly
            # these four lines in this order — independent of Plotly's trace/axis
            # ordering, which is what silently reversed it before.
            _master_cd = np.column_stack([
                _gap_df["spx"].to_numpy(dtype=float),
                _gap_df["diagonal_mark"].to_numpy(dtype=float),
                _gap_df["transform_mark"].to_numpy(dtype=float),
                _diff_series.to_numpy(dtype=float),
            ])
            _master_ht = (
                "SPX: %{customdata[0]:,.2f}"
                "<br>Diagonal Mark: $%{customdata[1]:.2f}"
                "<br>Transform Order Mark: $%{customdata[2]:.2f}"
                f"<br>{_diff_hover_label}: $%{{customdata[3]:.2f}}<extra></extra>"
            )

            # Always-on Gap fill: the band between Diagonal and Transform IS the
            # Gap, drawn directly so it reads at a glance rather than by comparing
            # two lines. Discovery mode only — position-management mode tells a
            # different story (distance vs. a fixed entry), so no band there.
            _gap_fill = (dict(fill="tonexty", fillcolor="rgba(124,148,199,0.11)")
                         if _lock is None else {})
            fig_gap.add_trace(go.Scatter(
                x=_gap_df["timestamp"], y=_gap_df["transform_mark"],
                name="Transform Order Mark",
                line=dict(color=ctx.chart_colors["transform_mark"], width=1.8),
                customdata=_master_cd,
                hovertemplate=_master_ht,
                **_gap_fill,
            ))
            # Invisible trace kept for figure structure; hover suppressed (the
            # master tooltip already reports the Gap line).
            fig_gap.add_trace(go.Scatter(
                x=_gap_df["timestamp"], y=_diff_series,
                name=_diff_hover_label,
                line=dict(width=0, color="rgba(0,0,0,0)"),
                showlegend=False,
                hoverinfo="skip",
            ))

            # Directional crossing markers: a caret wherever the Diagonal line
            # crosses the Transform line. ▲ = Diagonal crossed UP through
            # Transform; ▼ = crossed DOWN. Shape carries direction (no green/
            # red — favorability isn't validated), so it reads without inspecting.
            _dm = _gap_df["diagonal_mark"].to_numpy()
            _tm = _gap_df["transform_mark"].to_numpy()
            _tx = _gap_df["timestamp"].to_numpy()
            _sgn = _dm - _tm
            _up_x, _up_y, _dn_x, _dn_y = [], [], [], []
            for _i in range(1, len(_sgn)):
                if _sgn[_i - 1] < 0 <= _sgn[_i]:
                    _up_x.append(_tx[_i]); _up_y.append(_tm[_i])
                elif _sgn[_i - 1] > 0 >= _sgn[_i]:
                    _dn_x.append(_tx[_i]); _dn_y.append(_tm[_i])
            if _up_x:
                fig_gap.add_trace(go.Scatter(
                    x=_up_x, y=_up_y, mode="markers", name="cross-up",
                    marker=dict(symbol="triangle-up", size=11,
                                color=ctx.chart_colors["transform_mark"],
                                line=dict(width=1, color="#0c1421")),
                    showlegend=False,
                    hovertemplate="▲ Diagonal crossed up through Transform<extra></extra>",
                ))
            if _dn_x:
                fig_gap.add_trace(go.Scatter(
                    x=_dn_x, y=_dn_y, mode="markers", name="cross-down",
                    marker=dict(symbol="triangle-down", size=11,
                                color=ctx.chart_colors["transform_mark"],
                                line=dict(width=1, color="#0c1421")),
                    showlegend=False,
                    hovertemplate="▼ Diagonal crossed down through Transform<extra></extra>",
                ))
            _add_market_open_lines(fig_gap, _gap_df["timestamp"])

            # Only the Transform trace drives the unified tooltip; silence the
            # rest so the four master lines appear alone, in fixed order.
            for _tr in fig_gap.data:
                if _tr.name != "Transform Order Mark":
                    _tr.hoverinfo = "skip"
                    _tr.hovertemplate = None

            fig_gap.update_layout(
                height=320,
                margin=dict(l=_SYNC_MARGIN_L, r=_SYNC_MARGIN_R, t=10, b=20),
                paper_bgcolor="#0c1421",
                plot_bgcolor="#0c1421",
                font=dict(family="Inter", color="#6d8fa8", size=11),
                hovermode="x unified",
                hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                                font=dict(color="#dde6f1", size=12)),
                xaxis=_gap_xaxis,
                yaxis=dict(title="Mark ($)", gridcolor="#0c1928", automargin=False),
                yaxis2=dict(overlaying="y", side="right", visible=False,
                            showgrid=False, zeroline=False),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                            bgcolor="rgba(0,0,0,0)"),
            )
            _perf_build_ms = (_perf_counter() - _perf_build0) * 1000.0
            with st.container(key="chartcard_gap"):
                st.plotly_chart(fig_gap, use_container_width=True)
                # ── #3 profiling: real numbers on the current (esp. OTM) load ──
                # A cache MISS (new/OTM strike pair) shows the true query cost;
                # a cache HIT reads ~0 ms. This isolates data-retrieval vs.
                # chart-generation so we optimise the actual bottleneck.
                try:
                    _spx_now = float(_gap_df["spx"].iloc[-1])
                    _dist_txt = (f" · call {ctx.call_strike - _spx_now:+.0f} / "
                                 f"put {ctx.put_strike - _spx_now:+.0f} pts from spot")
                except Exception:
                    _dist_txt = ""
                st.caption(
                    f"⏱ marks query {_perf_marks_ms:.0f} ms · "
                    f"chart build {_perf_build_ms:.0f} ms · "
                    f"{len(_gap_df)} pts{_dist_txt}"
                )
                st.markdown(
                    '<div class="chart-cap"><span class="cap-legend">'
                    'Filled band = live Gap (Transform − Diagonal) · '
                    'brighter green = Gap ≥ threshold · '
                    '▲▼ = Diagonal crosses Transform</span></div>',
                    unsafe_allow_html=True,
                )
            if _lock is None:
                _render_note(
                    "This chart shows <b>why</b> the marks move. The shaded band is the Gap "
                    "drawn directly — watch it thicken or thin instead of comparing two "
                    "lines by eye. When it crosses your threshold the fill brightens green: "
                    "a transformation window is open. Hover for SPX at that moment.",
                    kind="info",
                )
            else:
                _render_note(
                    "Position-management mode: the dashed line is your locked entry; the dotted "
                    "line is where the diagonal would be trading now. Hover any point for SPX, "
                    "both marks, and your live difference vs. entry.",
                    kind="good",
                )

            # ── Strike Channel: SPX relative to the selected strikes ──────────
            # Stacked directly under the gap chart, sharing its x-axis config and
            # margins so the two align vertically. Answers "where was SPX vs. my
            # position when the marks moved" — the band is the zone between the
            # short strikes; the SPX line poking out = a short strike being tested.
            if "spx" in _gap_df.columns and _gap_df["spx"].notna().any():
                _lo_k = float(min(ctx.put_strike, ctx.call_strike))
                _hi_k = float(max(ctx.put_strike, ctx.call_strike))
                _spx_series = _gap_df["spx"].astype(float)

                fig_spx = go.Figure()

                # Neutral channel band between the short strikes (NOT green — SPX
                # being inside the channel is a fact, not a validated good signal).
                fig_spx.add_hrect(
                    y0=_lo_k, y1=_hi_k,
                    fillcolor="rgba(124,148,199,0.09)", line_width=0, layer="below",
                )
                for _k, _lbl in [(ctx.call_strike, f"{ctx.call_strike:.0f} C"),
                                 (ctx.put_strike, f"{ctx.put_strike:.0f} P")]:
                    fig_spx.add_hline(
                        y=float(_k), line_width=1.2, line_dash="dash",
                        line_color="#4a5d80",
                        annotation_text=_lbl, annotation_position="right",
                        annotation_font=dict(size=10, color="#8b9ab3"),
                    )

                # SPX line — light gray so it doesn't compete with blue/amber/green.
                _cd = np.column_stack([
                    _spx_series.to_numpy() - ctx.put_strike,
                    _spx_series.to_numpy() - ctx.call_strike,
                ])
                fig_spx.add_trace(go.Scatter(
                    x=_gap_df["timestamp"], y=_spx_series,
                    name="SPX", mode="lines",
                    line=dict(color="#d7deea", width=2),
                    customdata=_cd,
                    hovertemplate=("SPX: %{y:,.2f}"
                                   "<br>vs Put: %{customdata[0]:+.0f}"
                                   "<br>vs Call: %{customdata[1]:+.0f}<extra></extra>"),
                ))

                # Directional crossing markers where SPX crosses a short strike.
                _sx = _spx_series.to_numpy()
                _tx = _gap_df["timestamp"].to_numpy()
                _cu_x, _cu_y, _cd_x, _cd_y = [], [], [], []
                for _k in (ctx.put_strike, ctx.call_strike):
                    for _i in range(1, len(_sx)):
                        if _sx[_i - 1] < _k <= _sx[_i]:
                            _cu_x.append(_tx[_i]); _cu_y.append(float(_k))
                        elif _sx[_i - 1] > _k >= _sx[_i]:
                            _cd_x.append(_tx[_i]); _cd_y.append(float(_k))
                if _cu_x:
                    fig_spx.add_trace(go.Scatter(
                        x=_cu_x, y=_cu_y, mode="markers", name="cross-up",
                        marker=dict(symbol="triangle-up", size=11,
                                    color=ctx.chart_colors["transform_mark"],
                                    line=dict(width=1, color="#0c1421")),
                        showlegend=False,
                        hovertemplate="▲ SPX crossed up through strike<extra></extra>",
                    ))
                if _cd_x:
                    fig_spx.add_trace(go.Scatter(
                        x=_cd_x, y=_cd_y, mode="markers", name="cross-down",
                        marker=dict(symbol="triangle-down", size=11,
                                    color=ctx.chart_colors["transform_mark"],
                                    line=dict(width=1, color="#0c1421")),
                        showlegend=False,
                        hovertemplate="▼ SPX crossed down through strike<extra></extra>",
                    ))
                _add_market_open_lines(fig_spx, _gap_df["timestamp"])

                # Y-range padded around both the band and the SPX path.
                _y_lo = min(_lo_k, float(_spx_series.min()))
                _y_hi = max(_hi_k, float(_spx_series.max()))
                _y_pad = max((_y_hi - _y_lo) * 0.08, 2.0)
                fig_spx.update_layout(
                    height=230,
                    margin=dict(l=_SYNC_MARGIN_L, r=_SYNC_MARGIN_R, t=10, b=20),
                    paper_bgcolor="#0c1421", plot_bgcolor="#0c1421",
                    font=dict(family="Inter", color="#6d8fa8", size=11),
                    hovermode="x unified",
                    hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                                    font=dict(color="#dde6f1", size=12)),
                    xaxis=_gap_xaxis,
                    yaxis=dict(title="SPX", gridcolor="#0c1928", automargin=False,
                               range=[_y_lo - _y_pad, _y_hi + _y_pad]),
                    showlegend=False,
                )
                with st.container(key="chartcard_spx"):
                    st.plotly_chart(fig_spx, use_container_width=True)
                    st.markdown(
                        '<div class="chart-cap"><span class="cap-legend">'
                        'Shaded band = between your short strikes · line = SPX · '
                        '▲▼ = SPX crosses a strike</span></div>',
                        unsafe_allow_html=True,
                    )
                _render_note(
                    "Where SPX sat relative to your position. Line inside the band = both "
                    "shorts OTM; the line poking above the call or below the put = that short "
                    "is being tested — which is usually what moved the marks above.",
                    kind="info",
                )

            if _lock is not None:
                # ── Position-management readout: progress + momentum ──────────
                # locked_at is stored tz-aware (America/New_York offset embedded,
                # see _lock_entry). _gap_df["timestamp"] is naive ET (stripped for
                # Plotly rangebreaks). Drop the tz before comparing so both sides
                # are naive ET wall-clock.
                _locked_at_naive = pd.Timestamp(_lock["locked_at"]).tz_localize(None)
                _since_entry = _gap_df[_gap_df["timestamp"] >= _locked_at_naive]
                if not _since_entry.empty:
                    _live_diff_now = float(_since_entry.iloc[-1]["transform_mark"]) - float(_lock["entry_diagonal_mark"])
                    _pct_to_threshold = max(0.0, min(1.0, _live_diff_now / 5.0))

                    _win_min = st.session_state.get("momentum_window_min", 30)
                    _win_start = _since_entry["timestamp"].iloc[-1] - pd.Timedelta(minutes=_win_min)
                    _win_df = _since_entry[_since_entry["timestamp"] >= _win_start]
                    _slope_per_hr = None
                    if len(_win_df) >= 2:
                        _t0, _t1 = _win_df["timestamp"].iloc[0], _win_df["timestamp"].iloc[-1]
                        _hrs = (_t1 - _t0).total_seconds() / 3600.0
                        if _hrs > 0:
                            _d0 = float(_win_df["transform_mark"].iloc[0]) - float(_lock["entry_diagonal_mark"])
                            _d1 = float(_win_df["transform_mark"].iloc[-1]) - float(_lock["entry_diagonal_mark"])
                            _slope_per_hr = (_d1 - _d0) / _hrs

                    _pcol1, _pcol2 = st.columns([2, 2])
                    with _pcol1:
                        st.progress(_pct_to_threshold,
                                    text=f"Live Difference: ${_live_diff_now:+.2f}  ·  "
                                         f"{_pct_to_threshold*100:.0f}% of the way to threshold ($5.00)")
                    with _pcol2:
                        if _slope_per_hr is None:
                            st.caption(f"Not enough data yet in the last {_win_min} min to read momentum.")
                        else:
                            _closing = _slope_per_hr > 0
                            _arrow = "↗" if _closing else ("↘" if _slope_per_hr < 0 else "→")
                            _mcolor = "#10d4a3" if _closing else "#f05252"
                            _label = "closing" if _closing else "widening" if _slope_per_hr < 0 else "flat"
                            _eta_txt = ""
                            if _closing and _live_diff_now < 5.0:
                                _hrs_to_go = (5.0 - _live_diff_now) / _slope_per_hr
                                if 0 < _hrs_to_go < 24:
                                    _mins_to_go = _hrs_to_go * 60
                                    _eta_txt = (f"  ·  ~{_mins_to_go:.0f} min to threshold "
                                                f"at this pace (naive straight-line projection, not a forecast)")
                            st.markdown(
                                f'<span style="color:{_mcolor};font-weight:600;">{_arrow} {_label} '
                                f'${abs(_slope_per_hr):.2f}/hr</span> over last {_win_min} min{_eta_txt}',
                                unsafe_allow_html=True,
                            )

            st.caption(
                "Green shading marks every window where Transform Gap "
                "(Transform Order Mark − Diagonal Mark) was ≥ 5 — the position "
                "was eligible for transformation during that span. "
                + ("Hover the chart for Live Diagonal Mark, Transform Order Mark, and Live Difference vs. entry."
                   if _lock is not None else
                   "Hover the chart for Diagonal Mark, Transform Order Mark, and their Difference at any point in time.")
            )
        else:
            st.caption(
                f"No transform-mark history yet for Put {ctx.put_strike:.0f} / "
                f"Call {ctx.call_strike:.0f} in the selected range."
            )

    if not atm_merged.empty:

        # ── Chart 2: Front vs Back ATM IV — same axis · IV Ratio by regime ────
        st.markdown(
            '<div class="sh" style="margin-top:.4rem">'
            '<span class="sh-ico">📐</span>'
            '<span class="sh-ttl">Front vs. Back ATM IV — same axis · IV Ratio by regime</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        fig_stack = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.62, 0.38], vertical_spacing=0.06,
            subplot_titles=(
                "Front vs Back ATM IV — same axis (the gap IS the spread)",
                "IV Ratio (F/B) — colored by regime",
            ),
        )
        fig_stack.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["front_iv"],
            name="Front ATM IV", line=dict(color=ctx.chart_colors["front_iv"], width=1.8)), row=1, col=1)
        fig_stack.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["back_iv"],
            name="Back ATM IV",  line=dict(color=ctx.chart_colors["back_iv"], width=1.8)), row=1, col=1)
        for tr in banded_ratio_traces(atm_merged["timestamp"], atm_merged["iv_ratio"]):
            fig_stack.add_trace(tr, row=2, col=1)
        for thr, dash in [(1.00, "solid"), (0.70, "dot"), (1.30, "dot")]:
            fig_stack.add_hline(
                y=thr, line=dict(color="#2a3f56", width=1, dash=dash), row=2, col=1)
        if _shared_range is not None:
            fig_stack.update_xaxes(
                range=_shared_range,
                rangebreaks=SESSION_RANGEBREAKS,
                gridcolor="#0c1928",
            )
        else:
            fig_stack.update_xaxes(rangebreaks=SESSION_RANGEBREAKS, gridcolor="#0c1928")
        fig_stack.update_yaxes(title_text="IV %",    row=1, col=1, gridcolor="#0c1928", automargin=False)
        fig_stack.update_yaxes(title_text="Ratio",   row=2, col=1, gridcolor="#0c1928", automargin=False)
        _add_market_open_lines(fig_stack, atm_merged["timestamp"], row="all", col="all")
        fig_stack.update_layout(
            height=520,
            margin=dict(l=_SYNC_MARGIN_L, r=_SYNC_MARGIN_R, t=40, b=20),
            paper_bgcolor="#0c1421",
            plot_bgcolor="#0c1421",
            font=dict(family="Inter", color="#6d8fa8", size=11),
            hovermode="x unified",
            hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                            font=dict(color="#dde6f1", size=12)),
            legend=dict(orientation="h", yanchor="bottom", y=-0.18,
                        xanchor="left", x=0, font=dict(size=10),
                        bgcolor="rgba(0,0,0,0)"),
        )
        with st.container(key="chartcard_stack"):
            st.plotly_chart(fig_stack, use_container_width=True)
        st.caption(
            "Top: front and back ATM IV share one axis — the vertical gap IS the spread. "
            "Bottom: ratio colored by regime at 0.70 / 1.00 / 1.30. "
            "Green (≥1) = backwardation (front rich). Amber (<0.70) = usually 0DTE decay artifact."
        )

        # ── Chart 3: Primary dual-axis chart (moved from top) ─────────────────
        st.markdown(
            '<div class="sh" style="margin-top:.4rem">'
            '<span class="sh-ico">📈</span>'
            '<span class="sh-ttl">Front ATM IV vs. Back ATM IV — with IV Ratio</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        fig_atm = go.Figure()
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["front_iv"],
            name="Front ATM IV", line=dict(color=ctx.chart_colors["front_iv"], width=1.8), yaxis="y1"))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["back_iv"],
            name="Back ATM IV",  line=dict(color=ctx.chart_colors["back_iv"], width=1.8), yaxis="y1"))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["iv_ratio"],
            name="IV Ratio (F/B)", line=dict(color="#f05252", width=1.8), yaxis="y2"))
        _add_market_open_lines(fig_atm, atm_merged["timestamp"])
        fig_atm.update_layout(
            height=340,
            margin=dict(l=_SYNC_MARGIN_L, r=_SYNC_MARGIN_R, t=10, b=70),
            paper_bgcolor="#0c1421",
            plot_bgcolor="#0c1421",
            font=dict(family="Inter", color="#6d8fa8", size=11),
            hovermode="x unified",
            hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                            font=dict(color="#dde6f1", size=12)),
            xaxis=_gap_xaxis,
            yaxis=dict(title="IV %", side="left",  gridcolor="#0c1928", automargin=False),
            yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False, automargin=False),
            legend=dict(orientation="h", yanchor="top", y=-0.22, xanchor="center", x=0.5,
                        font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
        )
        with st.container(key="chartcard_atm"):
            st.plotly_chart(fig_atm, use_container_width=True)

        samp_warn = iv_engine.sample_size_warning(atm_merged["iv_ratio"])
        if samp_warn:
            st.warning(samp_warn)

        # ── Chart 4: Front vs Back IV scatter — intraday trajectory ───────────
        st.markdown(
            '<div class="sh" style="margin-top:.4rem">'
            '<span class="sh-ico">🌀</span>'
            '<span class="sh-ttl">Front vs. Back IV Scatter — intraday trajectory</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        _sc = atm_merged.copy()
        _sc["hod"] = _sc["timestamp"].dt.hour + _sc["timestamp"].dt.minute / 60.0
        _lo = float(min(_sc["back_iv"].min(), _sc["front_iv"].min()))
        _hi = float(max(_sc["back_iv"].max(), _sc["front_iv"].max()))
        _pad = (_hi - _lo) * 0.05 or 1.0
        fig_intra = go.Figure()
        fig_intra.add_trace(go.Scatter(
            x=[_lo - _pad, _hi + _pad], y=[_lo - _pad, _hi + _pad], mode="lines",
            name="R = 1  (Front = Back)", line=dict(color="#2a3f56", dash="dash")))
        fig_intra.add_trace(go.Scatter(
            x=_sc["back_iv"], y=_sc["front_iv"], mode="markers", name="snapshots",
            marker=dict(size=6, color=_sc["hod"], colorscale="Viridis",
                        showscale=True, colorbar=dict(title="Hour ET"),
                        line=dict(width=0)),
            customdata=_sc["iv_ratio"],
            hovertemplate="Back %{x:.2f}%<br>Front %{y:.2f}%<br>R=%{customdata:.4f}<extra></extra>"))
        fig_intra.update_layout(
            height=420,
            margin=dict(l=20, r=20, t=10, b=20),
            paper_bgcolor="#0c1421",
            plot_bgcolor="#0c1421",
            font=dict(family="Inter", color="#6d8fa8", size=11),
            hovermode="x unified",
            hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                            font=dict(color="#dde6f1", size=12)),
            xaxis=dict(title="Back ATM IV %", gridcolor="#0c1928"),
            yaxis=dict(title="Front ATM IV %", gridcolor="#0c1928"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                        bgcolor="rgba(0,0,0,0)"),
        )
        with st.container(key="chartcard_intra"):
            st.plotly_chart(fig_intra, use_container_width=True)
        st.caption(
            "Each dot is one snapshot. Above the dashed line = backwardation (R>1); below = contango. "
            "Color = time of day. A cloud hugging one ray → ratio ≈ constant; "
            "fanning across angles → ratio varies independently of vol level."
        )

    else:
        st.caption(f"No ATM IV history for {ctx.front_expiry} / {ctx.back_expiry} in the selected range.")
