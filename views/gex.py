"""Gamma Exposure — where dealer hedging sits, and how it moved today.

TWO KINDS OF PANEL, and the difference is the whole point.

The STRIKE panels (gamma, open interest, volume) are one moment: the newest
snapshot, drawn across strikes. That is what a vendor's screen shows, and it
is what `ctx.chain_df` already holds — so they cost no read.

The TIME panels (net GEX through the day, the 0DTE flow lines, the cumulative
net-volume build-up) are the same quantities drawn across the session. **Those
are the ones nobody can buy.** Brokers discard intraday history, which is the
founding observation of this project; a vendor can tell you today's GEX and
cannot tell you what it was at 10:15. They cost one memoised read each.

THE DEFINITIONS ARE OPTION ALPHA'S, from their published documentation rather
than reverse-engineered, so these figures should agree with their screen given
the same chain. The three views, in their words:

  Call vs Put   — call GEX above zero, put GEX below. The raw two sides.
  Abs Gamma     — the two summed as magnitudes into one positive bar, drawn
                  stacked so the split stays visible. 9.6b call and -1b put
                  is 10.6b absolute.
  Net Gamma     — the two summed signed: 8.6b.

Ratio and sentiment are computed over the DISPLAYED bars, not the whole chain,
because that is what their documentation specifies and it is the more useful
answer: a ratio dominated by strikes 300 points away is not about today.
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config
from core import contract
from core import session as core_session
from core import format as fmt
from core import gex
from core.charts import to_display_time
from views.context import ViewContext

_BG = "#0c1421"
_GRID = "#0c1928"
_INK = "#6d8fa8"
_BRIGHT = "#dde6f1"

# Call side and put side, matching core/market.GREEN / RED so a green bar
# means the same thing here as everywhere else on the dashboard.
_CALL = "#10d4a3"
_PUT = "#f05252"

# The volume shading behind the gamma bars. Option Alpha uses blue for calls
# and ORANGE for puts, which works on their chart because their bars are one
# colour. Here the bars are already green and red, and an orange fill behind a
# red bar mixes into a muddy brown that reads as a third thing — so the puts
# get violet instead. Blue and violet are both cool, neither collides with the
# green/red pair, and grey still emerges where the two fills overlap, which is
# the part of the vendor's design worth keeping.
#
# The faint outline is what makes each fill legible where they cross: the
# shapes stay traceable even where the interiors have blended.
_CALL_VOL_FILL = "rgba(84,140,232,0.20)"
_PUT_VOL_FILL = "rgba(163,116,224,0.20)"
_CALL_VOL_EDGE = "rgba(84,140,232,0.55)"
_PUT_VOL_EDGE = "rgba(163,116,224,0.55)"

_FLIP = "#e8b64c"

_STRIKE_COUNTS = [20, 40, 60, 100, 0]      # 0 = every strike collected
_VIEWS = ["Call vs Put", "Abs Gamma", "Net Gamma", "Delta Exposure"]

# Distinct hues for the 0DTE flow lines. Deliberately not the call/put pair:
# these identify STRIKES, and reusing green/red would read as sides.
_FLOW_COLOURS = ["#4d8eff", "#e8b64c", "#10d4a3", "#f05252",
                 "#a78bfa", "#c9a227", "#ec4899", "#22d3ee"]


def _fmt_money(value: float | None, unit: str = "$") -> str:
    """A large figure at a readable magnitude, or an em dash.

    An em dash rather than 0: absent and zero are different states, and the
    project's rule is that a missing number shows blank.
    """
    if value is None or pd.isna(value):
        return "—"
    sign = "-" if value < 0 else ""
    mag = abs(value)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if mag >= cutoff:
            return f"{sign}{unit}{mag / cutoff:,.1f}{suffix}"
    return f"{sign}{unit}{mag:,.0f}"


def _metric(label: str, value: str, colour: str = _BRIGHT) -> str:
    return (
        f'<div style="flex:1;min-width:104px;">'
        f'<div style="color:#2f4459;font-size:0.72em;text-transform:uppercase;'
        f'letter-spacing:0.06em;">{label}</div>'
        f'<div style="color:{colour};font-size:1.15em;font-weight:600;'
        f'line-height:1.5;">{value}</div></div>'
    )


def _strip(items: list[str]) -> None:
    st.markdown(
        '<div style="display:flex;gap:16px;flex-wrap:wrap;padding:10px 14px;'
        'background:#0c1421;border:1px solid #16283d;border-radius:8px;'
        'margin-bottom:10px;">' + "".join(items) + "</div>",
        unsafe_allow_html=True,
    )


def _gap(height: int = 22) -> None:
    """Breathing room between two stacked charts.

    Streamlit puts consecutive plotly_charts flush against each other, so a
    column of five panels reads as one wall. The space is what tells a reader
    where one chart ends and the next question begins.
    """
    st.markdown(f'<div style="height:{height}px"></div>',
                unsafe_allow_html=True)


def _dark(fig, height: int, *, legend: bool = False) -> None:
    """The chart-card styling every panel on this tab shares."""
    fig.update_xaxes(gridcolor=_GRID)
    fig.update_yaxes(gridcolor=_GRID, automargin=False)
    # The legend sits ABOVE the plot, not under it. Below, an eight-strike
    # legend lands exactly on the x tick labels — and on a time axis Plotly
    # adds a second row for the DATE beneath the hours, so the collision is
    # with a label that was never asked for. Above the plot there is nothing
    # to collide with, and the extra top margin is the room it needs.
    fig.update_layout(
        height=height + (26 if legend else 0),
        margin=dict(l=64, r=56, t=66 if legend else 44, b=54),
        paper_bgcolor=_BG, plot_bgcolor=_BG,
        font=dict(family="Inter", color=_INK, size=11),
        hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                        font=dict(color=_BRIGHT, size=12)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left",
                    x=0, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
    )
    for note in fig.layout.annotations:
        if note.text in ("Gamma Exposure", "Open Interest", "Volume"):
            note.font.update(color=_INK, size=12)


def render(ctx: ViewContext) -> None:
    """Draw the tab."""
    st.markdown(
        '<div class="sh"><span class="sh-ico">🧲</span>'
        '<span class="sh-ttl">Gamma Exposure</span>'
        '<span class="sh-bdg">dealer positioning by strike</span></div>',
        unsafe_allow_html=True,
    )

    chain = ctx.chain_df
    if chain is None or chain.empty:
        st.info("No option chain in this snapshot.")
        return
    if "gamma" not in chain.columns or not chain["gamma"].notna().any():
        st.info(
            "This snapshot carries no gamma, so exposure cannot be computed. "
            "That is a normal gap for some snapshots rather than a fault."
        )
        return

    # ── Controls ─────────────────────────────────────────────────────────────
    expiries = sorted(chain["expiry"].dropna().unique(), key=contract.sort_key)
    # Weekday, date and days-to-expiry, not a bare ISO string. `exp_label` is
    # the dashboard's existing one -- it already renders "Friday, Sep 18, 2026
    # (14 DTE)" and already writes the third Friday's morning contract as
    # "· AM settled" rather than a second bracket. Writing a second formatter
    # here would be a second thing to keep in step with core/contract.py.
    dte_by_expiry = chain.groupby("expiry")["dte"].first().astype(int).to_dict()

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        choice = st.selectbox(
            "Expiry", ["All expiries", *expiries], key="gex_expiry",
            format_func=lambda e: (e if e == "All expiries"
                                   else fmt.exp_label(e, dte_by_expiry)),
            help="All expiries is the whole-chain figure most GEX commentary "
                 "refers to. The third Friday appears twice because SPX lists "
                 "two contracts for it.",
        )
    with c2:
        count = st.selectbox(
            "Strikes", _STRIKE_COUNTS, index=1, key="gex_strikes",
            format_func=lambda n: "All" if n == 0 else f"{n} strikes",
            help="Ratio and sentiment are computed over the strikes shown, so "
                 "narrowing this changes them. That is Option Alpha's "
                 "definition, not an accident.",
        )
    with c3:
        view = st.selectbox("View", _VIEWS, key="gex_view")

    expiry = None if choice == "All expiries" else choice
    per_strike = gex.by_strike(chain, ctx.spx_price, expiry=expiry)
    if per_strike.empty:
        st.info("No strike in this selection carries gamma.")
        return

    shown = gex.window(per_strike, ctx.spx_price, count)
    totals = gex.summary(shown)          # displayed bars, per the documentation

    _draw_headline(totals)
    _draw_strike_panels(ctx, shown, view, expiry)
    _gap()
    _draw_cumulative_curve(ctx, shown)
    _draw_caption(totals, expiry, per_strike, view)

    st.divider()
    _draw_time_panels(ctx, shown)


# ─────────────────────────────────────────────────────────────────────────────
# The headline strip
# ─────────────────────────────────────────────────────────────────────────────

def _draw_headline(totals: dict) -> None:
    net = totals["net_gex"]
    ratio = totals["ratio"]
    sentiment = totals["sentiment"]

    net_colour = _BRIGHT if net is None else (_CALL if net >= 0 else _PUT)
    # Green when positive gamma dominates, red when negative — the vendor's
    # own colouring, and the sign of the ratio already carries which it is.
    ratio_colour = _BRIGHT if ratio is None else (_CALL if ratio > 0 else _PUT)

    bars = ("" if totals["total_bars"] is None
            else f" ({totals['positive_bars']}/{totals['total_bars']})")

    _strip([
        _metric("Net GEX", _fmt_money(net), net_colour),
        _metric("GEX Ratio", "—" if ratio is None else f"{ratio:+,.1f}x",
                ratio_colour),
        _metric("Sentiment",
                "—" if sentiment is None else f"{sentiment:,.0f}%{bars}"),
        _metric("Call GEX", _fmt_money(totals["call_gex"]), _CALL),
        _metric("Put GEX", _fmt_money(totals["put_gex"]), _PUT),
        _metric("Peak strike",
                "—" if totals["peak_strike"] is None
                else f"{totals['peak_strike']:,.0f} ({totals['peak_side']})"),
        _metric("Gamma flip",
                "—" if totals["flip_strike"] is None
                else f"{totals['flip_strike']:,.0f}", _FLIP),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# The three strike panels
# ─────────────────────────────────────────────────────────────────────────────

def _draw_strike_panels(ctx: ViewContext, shown: pd.DataFrame,
                        view: str, expiry: str | None) -> None:
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.12,
        row_heights=[0.48, 0.26, 0.26],
        specs=[[{"secondary_y": True}], [{}], [{}]],
        subplot_titles=("Gamma Exposure", "Open Interest", "Volume"),
    )

    # ── Panel 1, background: the day's volume as translucent fills ───────────
    # Drawn FIRST so the bars sit on top of it, and on a secondary axis
    # because contracts traded and dollars of gamma are not the same unit.
    for col, fill, edge, name in (
            ("put_volume", _PUT_VOL_FILL, _PUT_VOL_EDGE, "Put volume"),
            ("call_volume", _CALL_VOL_FILL, _CALL_VOL_EDGE, "Call volume")):
        fig.add_trace(
            go.Scatter(x=shown["strike"], y=shown[col], name=name, mode="lines",
                       line=dict(width=1.2, color=edge), fill="tozeroy",
                       fillcolor=fill, hoverinfo="skip", showlegend=False),
            row=1, col=1, secondary_y=True,
        )

    # ── Panel 1, foreground: whichever gamma view is selected ────────────────
    if view == "Call vs Put":
        _add_bar(fig, shown["strike"], shown["call_gex"], _CALL, 1,
                 "Strike %{x:,.0f}<br>Call GEX %{y:$,.0f}<extra></extra>")
        _add_bar(fig, shown["strike"], -shown["put_gex"], _PUT, 1,
                 "Strike %{x:,.0f}<br>Put GEX %{y:$,.0f}<extra></extra>")
    elif view == "Abs Gamma":
        # Stacked, so the one positive total still shows its two halves.
        _add_bar(fig, shown["strike"], shown["call_gex"], _CALL, 1,
                 "Strike %{x:,.0f}<br>Call %{y:$,.0f}<extra></extra>")
        _add_bar(fig, shown["strike"], shown["put_gex"], _PUT, 1,
                 "Strike %{x:,.0f}<br>Put %{y:$,.0f}<extra></extra>")
    elif view == "Net Gamma":
        colours = [_CALL if v >= 0 else _PUT for v in shown["net_gex"]]
        _add_bar(fig, shown["strike"], shown["net_gex"], colours, 1,
                 "Strike %{x:,.0f}<br>Net GEX %{y:$,.0f}<extra></extra>")
    else:
        dex = gex.dex_by_strike(ctx.chain_df, ctx.spx_price, expiry=expiry)
        dex = dex[dex["strike"].isin(shown["strike"])]
        _add_bar(fig, dex["strike"], dex["call_dex"], _CALL, 1,
                 "Strike %{x:,.0f}<br>Call DEX %{y:$,.0f}<extra></extra>")
        _add_bar(fig, dex["strike"], dex["put_dex"], _PUT, 1,
                 "Strike %{x:,.0f}<br>Put DEX %{y:$,.0f}<extra></extra>")

    # ── Panels 2 and 3 ───────────────────────────────────────────────────────
    _add_bar(fig, shown["strike"], shown["call_oi"], _CALL, 2,
             "Strike %{x:,.0f}<br>Call OI %{y:,.0f}<extra></extra>")
    _add_bar(fig, shown["strike"], -shown["put_oi"], _PUT, 2,
             "Strike %{x:,.0f}<br>Put OI %{y:,.0f}<extra></extra>")
    _add_bar(fig, shown["strike"], shown["call_volume"], _CALL, 3,
             "Strike %{x:,.0f}<br>Call volume %{y:,.0f}<extra></extra>")
    _add_bar(fig, shown["strike"], -shown["put_volume"], _PUT, 3,
             "Strike %{x:,.0f}<br>Put volume %{y:,.0f}<extra></extra>")

    for row in (1, 2, 3):
        fig.add_vline(x=ctx.spx_price,
                      line=dict(color="#8fa9c4", width=1, dash="dot"),
                      row=row, col=1)
    # Anchored just INSIDE the panel, not above it: at the top edge this label
    # lands on the "Gamma Exposure" subplot title and the two overprint.
    fig.add_annotation(
        x=ctx.spx_price, y=0.98, yref="y domain", row=1, col=1,
        text=f"{ctx.spx_price:,.2f}", showarrow=False, yanchor="top",
        font=dict(color=_BRIGHT, size=10), bgcolor="#16283d", borderpad=3,
    )

    flip = gex.flip_strike(shown)
    if flip is not None and shown["strike"].min() <= flip <= shown["strike"].max():
        fig.add_vline(x=flip, line=dict(color=_FLIP, width=1, dash="dash"),
                      row=1, col=1)

    # ── Axes a reader can read without hovering ──────────────────────────────
    # Every number on this chart is a magnitude in a unit nobody carries in
    # their head — dollars per 1% move run to eleven digits. SI suffixes ("$3B")
    # keep the tick labels short enough to render, and the explicit zero line
    # is what makes "above the axis" and "below the axis" a reading rather than
    # an inference: on the Call vs Put and Net views the SIGN is the message.
    # No y-axis titles on the three panels: each already carries a subplot
    # title saying what it is, and the unit is repeated in the caption under
    # the chart. A rotated title on the left only eats width the bars can use.
    fig.update_yaxes(title_text=None, row=1, col=1, secondary_y=False,
                     showticklabels=True, tickformat="$~s", ticks="outside",
                     ticklen=4, tickcolor=_GRID,
                     zeroline=True, zerolinecolor="#3c5570", zerolinewidth=1)
    fig.update_yaxes(title_text=None, row=1, col=1,
                     secondary_y=True, showgrid=False, showticklabels=True,
                     tickformat="~s", ticks="outside", ticklen=4,
                     tickcolor=_GRID)
    fig.update_yaxes(title_text=None, row=2, col=1,
                     tickformat="~s", zeroline=True, zerolinecolor="#3c5570")
    fig.update_yaxes(title_text=None, row=3, col=1,
                     tickformat="~s", zeroline=True, zerolinecolor="#3c5570")

    # Strike labels on EVERY panel, not only the bottom one. shared_xaxes
    # hides them on the upper rows by default, which means the gamma panel —
    # the one people actually read — has no strike scale of its own and has to
    # be traced down two charts to a row below it.
    # Strike labels dense enough to read a bar off directly. Plotly's default
    # picks a round interval that lands on 50 or 100 points — three labels
    # across a twenty-strike window — so the step is chosen here from the span
    # actually displayed, aiming at ~18 labels and rounded to a multiple of 5
    # so every tick falls on a real listed strike rather than between two.
    span = float(shown["strike"].max() - shown["strike"].min())
    step = max(5.0, round(span / 18.0 / 5.0) * 5.0) if span > 0 else 5.0
    fig.update_xaxes(showticklabels=True, ticks="outside", ticklen=4,
                     tickcolor=_GRID, tickangle=-45, dtick=step,
                     tickformat=",.0f")
    fig.update_xaxes(title_text=None, row=3, col=1)
    fig.update_layout(barmode="relative", bargap=0.15)
    _dark(fig, 900)

    with st.container(key="chartcard_gex"):
        st.plotly_chart(fig, use_container_width=True)


def _draw_cumulative_curve(ctx: ViewContext, shown: pd.DataFrame) -> None:
    """Net exposure accumulated from the lowest displayed strike upward.

    The bars answer "where is the gamma"; this answers "which side of it are
    we on". Where the curve is above zero, dealers hedging into a rally are
    selling and the move is damped; below zero the same hedge is buying, which
    is why a short-gamma tape trends. The crossing IS the flip strike drawn on
    the panel above — the same number, shown as a level rather than a line, so
    a reader can see HOW FAR from it the market is and how steeply it turns.

    Computed over the DISPLAYED window, like every other figure on this tab:
    a curve accumulated from strikes off the screen would cross somewhere the
    reader cannot see and read as an error.
    """
    if shown.empty:
        return
    curve = gex.cumulative_net(shown)
    flip = gex.flip_strike(shown)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=shown["strike"], y=curve, name="Cumulative net GEX", mode="lines",
        line=dict(color="#4d8eff", width=2), fill="tozeroy",
        fillcolor="rgba(77,142,255,0.12)",
        hovertemplate="Strike %{x:,.0f}<br>Cumulative %{y:$,.0f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color="#2a3f56", width=1))
    fig.add_vline(x=ctx.spx_price,
                  line=dict(color="#8fa9c4", width=1, dash="dot"))
    if flip is not None and shown["strike"].min() <= flip <= shown["strike"].max():
        fig.add_vline(x=flip, line=dict(color=_FLIP, width=1, dash="dash"))
        fig.add_annotation(
            x=flip, y=1.0, yref="y domain", yanchor="bottom", showarrow=False,
            text=f"flip {flip:,.0f}", font=dict(color=_FLIP, size=10),
            bgcolor="#16283d", borderpad=3)
    fig.update_layout(
        xaxis=dict(title="Strike", gridcolor=_GRID),
        yaxis=dict(title="Cumulative net GEX ($ per 1% move)",
                   gridcolor=_GRID, tickformat="$~s", zeroline=True,
                   zerolinecolor="#3c5570"),
    )
    _dark(fig, 300)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "· **Cumulative gamma curve.** Above zero is the damped, mean-reverting "
        "regime; below zero, dealer hedging amplifies the move instead. The "
        "dashed line is where it crosses — the same flip strike marked above, "
        "and the steeper the crossing, the more decisively the regime changes "
        "as SPX passes through it."
    )


def _add_bar(fig, x, y, colour, row, hover, secondary: bool = False) -> None:
    fig.add_trace(
        go.Bar(x=x, y=y, marker=dict(color=colour, line=dict(width=0)),
               hovertemplate=hover, showlegend=False),
        row=row, col=1, **({"secondary_y": secondary} if row == 1 else {}),
    )


# ─────────────────────────────────────────────────────────────────────────────
# The time panels — the half a vendor's screen cannot show
# ─────────────────────────────────────────────────────────────────────────────

def _draw_time_panels(ctx: ViewContext, shown: pd.DataFrame) -> None:
    st.markdown(
        '<div class="sh"><span class="sh-ico">⏱️</span>'
        '<span class="sh-ttl">Through the session</span>'
        '<span class="sh-bdg">intraday history — not available from a broker</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    intraday = ctx.load_intraday_strike_metrics(
        ctx.session_date, ctx.snapshot_id, None)
    if intraday is None or intraday.empty:
        st.info(
            "No completed snapshots for this session yet. These panels fill in "
            "as the day is collected."
        )
        return

    intraday = to_display_time(intraday, config.DISPLAY_TIMEZONE)

    span = _session_x_range(ctx.session_date)
    _draw_net_gex_series(intraday, span)
    _gap()
    _draw_cumulative_volume(intraday, shown, span)
    _gap()
    _draw_zero_dte_flow(ctx, span)
    _gap()
    _draw_oi_change(ctx, shown)


def _session_x_range(session_date: str) -> list:
    """The x range every time panel shares: the whole trading day, always.

    Left to itself Plotly fits the axis to the data, so at 09:45 fifteen
    minutes of collection are stretched across the full width and the chart
    reads like a finished session. Pinning the axis to 09:30-16:00 makes the
    empty part of the day visible as empty, and — more usefully — makes two
    panels stacked above each other line up at the same minute.

    Built from core.session's own OPEN_START rather than a literal, and
    converted into whatever DISPLAY_TIMEZONE is, because to_display_time has
    already moved the data there; hardcoding 09:30 would silently mean 09:30
    LOCAL for anyone who changes that setting.
    """
    et = ZoneInfo("America/New_York")
    local = ZoneInfo(config.DISPLAY_TIMEZONE)
    day = datetime.fromisoformat(session_date[:10]).date()
    bounds = (core_session.OPEN_START, dtime(16, 0))
    return [datetime.combine(day, t, tzinfo=et).astimezone(local)
            .replace(tzinfo=None) for t in bounds]


def _time_axis(span: list) -> dict:
    """Hourly ticks over the fixed session span, with no date row.

    Plotly's default on a datetime axis prints the date under the hours. Every
    panel here covers ONE session, named in the header, so that row is a
    duplicate that only ever gets in the legend's way.
    """
    return dict(range=span, gridcolor=_GRID, tickformat="%H:%M",
                dtick=3600000, ticks="outside", ticklen=4, tickcolor=_GRID)


def _session_totals(intraday: pd.DataFrame) -> pd.DataFrame:
    """Net and absolute GEX for each snapshot of the session, in dollars.

    The scaling uses each snapshot's OWN spot price. Using the latest one for
    the whole day would be a subtle, plausible-looking error — spot^2 moves
    about 1% for every 0.5% SPX move, so a trending day would show a drift in
    GEX that was really just the scale factor changing.
    """
    grouped = intraday.groupby("timestamp", as_index=False).agg(
        call_gamma_oi=("call_gamma_oi", "sum"),
        put_gamma_oi=("put_gamma_oi", "sum"),
        spot=("underlying_price", "first"),
    )
    scale = grouped["spot"].map(gex.dollar_scale)
    grouped["net_gex"] = (grouped["call_gamma_oi"] - grouped["put_gamma_oi"]) * scale
    grouped["abs_gex"] = (grouped["call_gamma_oi"] + grouped["put_gamma_oi"]) * scale
    return grouped


def _draw_net_gex_series(intraday: pd.DataFrame, span: list) -> None:
    totals = _session_totals(intraday)
    if totals.empty:
        return

    first, last = totals["net_gex"].iloc[0], totals["net_gex"].iloc[-1]
    _strip([
        _metric("Net GEX now", _fmt_money(last), _CALL if last >= 0 else _PUT),
        _metric("At the open", _fmt_money(first)),
        _metric("Change today", _fmt_money(last - first),
                _CALL if last >= first else _PUT),
        _metric("Snapshots", f"{len(totals):,}"),
    ])

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=totals["timestamp"], y=totals["net_gex"], name="Net GEX",
        mode="lines", line=dict(color="#4d8eff", width=1.8),
        hovertemplate="%{x|%H:%M}<br>Net GEX %{y:$,.0f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color="#2a3f56", width=1))
    fig.add_trace(go.Scatter(
        x=totals["timestamp"], y=totals["spot"], name="SPX", mode="lines",
        line=dict(color="#8fa9c4", width=1, dash="dot"), yaxis="y2",
        hovertemplate="%{x|%H:%M}<br>SPX %{y:,.2f}<extra></extra>"))
    fig.update_layout(
        xaxis=_time_axis(span),
        yaxis=dict(title="Net GEX ($ per 1% move)", gridcolor=_GRID,
                   tickformat="$~s", zeroline=True, zerolinecolor="#3c5570"),
        yaxis2=dict(title="SPX", overlaying="y", side="right", showgrid=False),
        hovermode="x unified",
    )
    _dark(fig, 300, legend=True)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "· Net GEX minute by minute against SPX. **A vendor can show you the "
        "left-hand number; only a stored record can show you the line.** "
        "Each point is scaled by that snapshot's own spot price, so the "
        "movement is exposure changing rather than the scale factor drifting."
    )


def _draw_cumulative_volume(intraday: pd.DataFrame, shown: pd.DataFrame,
                            span: list) -> None:
    """Net volume building up through the day, per strike.

    The broker's `volume` field is already a running total for the session, so
    this needs no cumulative sum of its own — the accumulation is what the
    field IS. What the panel adds is the shape of it: which strikes were being
    worked early, which only came alive after lunch, and where the call side
    handed over to the put side.
    """
    strikes = list(shown["strike"])
    if not strikes:
        return
    work = intraday[intraday["strike"].isin(strikes)].copy()
    if work.empty:
        return
    work["net_volume"] = work["call_volume"] - work["put_volume"]

    peak = (work.groupby("strike")["net_volume"].apply(lambda s: s.abs().max())
            .nlargest(8).index.tolist())
    work = work[work["strike"].isin(peak)]

    fig = go.Figure()
    for i, strike in enumerate(sorted(peak)):
        one = work[work["strike"] == strike].sort_values("timestamp")
        fig.add_trace(go.Scatter(
            x=one["timestamp"], y=one["net_volume"], name=f"{strike:,.0f}",
            mode="lines", line=dict(color=_FLOW_COLOURS[i % len(_FLOW_COLOURS)],
                                    width=1.5),
            hovertemplate=f"{strike:,.0f}<br>%{{x|%H:%M}}"
                          "<br>Net volume %{y:,.0f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color="#2a3f56", width=1))
    fig.update_layout(xaxis=_time_axis(span),
                      yaxis=dict(title="Call volume − put volume",
                                 gridcolor=_GRID, tickformat="~s"))
    _dark(fig, 320, legend=True)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "· **Cumulative net volume** — calls traded minus puts traded at each "
        "strike, as the session accumulates. Above zero the strike is being "
        "bought on the call side, below it on the put side; a line that "
        "flattens has stopped attracting flow. The eight busiest strikes of "
        "the displayed window."
    )


def _draw_zero_dte_flow(ctx: ViewContext, span: list) -> None:
    """Per-strike GEX for the contracts expiring TODAY, through the session."""
    zero = ctx.load_intraday_strike_metrics(ctx.session_date, ctx.snapshot_id, 0)
    if zero is None or zero.empty:
        st.info(
            "No contracts expire today, so there is no 0DTE flow to draw. "
            "SPX lists them Monday to Friday, so this fills in on any "
            "ordinary session."
        )
        return

    zero = to_display_time(zero, config.DISPLAY_TIMEZONE).copy()
    scale = zero["underlying_price"].map(gex.dollar_scale)
    zero["net_gex"] = (zero["call_gamma_oi"] - zero["put_gamma_oi"]) * scale

    latest = zero[zero["timestamp"] == zero["timestamp"].max()]
    levels = (latest.assign(mag=latest["net_gex"].abs())
              .nlargest(7, "mag")["strike"].tolist())
    if not levels:
        return

    total_now = float(latest["net_gex"].sum())
    first_ts = zero["timestamp"].min()
    total_open = float(zero[zero["timestamp"] == first_ts]["net_gex"].sum())

    _strip([
        _metric("0DTE GEX now", _fmt_money(total_now),
                _CALL if total_now >= 0 else _PUT),
        _metric("0DTE flow today", _fmt_money(total_now - total_open),
                _CALL if total_now >= total_open else _PUT),
        _metric("Key levels",
                " · ".join(f"{s:,.0f}" for s in sorted(levels)[:3])),
    ])

    fig = go.Figure()
    for i, strike in enumerate(sorted(levels)):
        one = zero[zero["strike"] == strike].sort_values("timestamp")
        fig.add_trace(go.Scatter(
            x=one["timestamp"], y=one["net_gex"], name=f"{strike:,.0f}",
            mode="lines", line=dict(color=_FLOW_COLOURS[i % len(_FLOW_COLOURS)],
                                    width=1.5),
            hovertemplate=f"{strike:,.0f}<br>%{{x|%H:%M}}"
                          "<br>Net GEX %{y:$,.0f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color="#8fa9c4", width=1))
    fig.update_layout(xaxis=_time_axis(span),
                      yaxis=dict(title="0DTE net GEX", gridcolor=_GRID,
                                 tickformat="$~s", zeroline=True,
                                 zerolinecolor="#3c5570"))
    _dark(fig, 340, legend=True)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "· **0DTE GEX flow** — one line per key strike, for contracts expiring "
        "today. Gamma is at its most violent here: these positions are hours "
        "from settlement, so a strike's exposure can invert in minutes. Key "
        "levels are the strikes carrying the largest exposure right now."
    )


def _draw_oi_change(ctx: ViewContext, shown: pd.DataFrame) -> None:
    """Today's open interest against the previous session's."""
    prior = ctx.load_prior_session_oi(ctx.session_date)
    if prior is None or prior.empty:
        st.info(
            "No previous session to compare open interest against — this is "
            "the first collected day, or the record has a gap before it."
        )
        return

    change = gex.oi_change(shown, prior)
    change = change[(change["call_oi_change"] != 0) | (change["put_oi_change"] != 0)]
    if change.empty:
        st.info("Open interest is unchanged from the previous session.")
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=change["strike"], y=change["call_oi_change"], name="Calls",
        marker=dict(color=_CALL, line=dict(width=0)),
        hovertemplate="Strike %{x:,.0f}<br>Call OI change %{y:+,.0f}<extra></extra>"))
    fig.add_trace(go.Bar(
        x=change["strike"], y=-change["put_oi_change"], name="Puts",
        marker=dict(color=_PUT, line=dict(width=0)),
        hovertemplate="Strike %{x:,.0f}<br>Put OI change %{y:+,.0f}<extra></extra>"))
    fig.add_vline(x=ctx.spx_price,
                  line=dict(color="#8fa9c4", width=1, dash="dot"))
    fig.add_hline(y=0, line=dict(color="#2a3f56", width=1))
    fig.update_layout(barmode="relative", bargap=0.15,
                      xaxis=dict(title="Strike", gridcolor=_GRID),
                      yaxis=dict(title="Contracts opened / closed",
                                 gridcolor=_GRID))
    _dark(fig, 300, legend=True)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "· **New positioning.** Open interest is republished once a day, "
        "overnight, so today's figure minus yesterday's is contracts actually "
        "opened (up) or closed (down) — the closest this data comes to a "
        "direct reading of what was put on. Puts are drawn downward. "
        "**No vendor sells this history; yours goes back to 23 June.**"
    )


# ─────────────────────────────────────────────────────────────────────────────

def _draw_caption(totals: dict, expiry: str | None,
                  per_strike: pd.DataFrame, view: str) -> None:
    """What the reader has to know to not over-trust the picture.

    Prose under the chart rather than a tooltip, because each of these is a
    reason a number above could be wrong and a caveat behind a hover is a
    caveat nobody reads.
    """
    lo, hi = per_strike["strike"].min(), per_strike["strike"].max()
    lines = [
        "**Violet shading is the day's put volume, blue is call volume**, on "
        "the right-hand axis; grey is where they overlap. Puts are drawn "
        "downward in the lower panels, and the hover shows the true count.",
        "**Ratio and sentiment cover the displayed bars only**, so they change "
        "with the strike window — sentiment is the *percentage of shown "
        "strikes with positive net gamma*, and the ratio is the larger side "
        "divided by the smaller, signed by which one wins. These are Option "
        "Alpha's published definitions, so the figures should agree with "
        "theirs on the same chain.",
        "**Gamma exposure assumes dealers are long calls and short puts.** "
        "That is the standard convention and it is an assumption, not data — "
        "nobody publishes dealer inventory. Every figure here inherits it, "
        "and the record now holds enough history to test it.",
        f"**The chain is recorded between {lo:,.0f} and {hi:,.0f}** — the "
        f"collector keeps ±300 points around spot. Gamma at that edge measured "
        f"0.2% of its at-the-money value, so the cost is small, but strikes "
        f"beyond it are absent rather than empty.",
    ]
    if view == "Delta Exposure":
        lines.insert(1, (
            "**Delta exposure carries no dealer sign, unlike gamma.** A put's "
            "delta is already negative, so imposing the convention again would "
            "double-count it. This panel is the chain's own delta — a "
            "description of what is listed, needing no assumption to be true."
        ))
    if expiry is None:
        lines.append(
            "**All expiries are summed.** Gamma concentrates in the nearest "
            "ones, so near-dated contracts dominate this view."
        )
    st.caption("  \n".join(f"· {line}" for line in lines))
