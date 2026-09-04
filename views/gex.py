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
from core import dealer
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

_VIEWS = ["Call vs Put", "Abs Gamma", "Net Gamma", "Delta Exposure"]

# How the Open Interest and Volume panels lay their two sides out. Mirrored
# (puts drawn downward) compares the two sides at a strike; stacked compares
# the TOTAL at one strike against the total at another, which is the reading
# for "where is the crowd" and the one a mirrored chart makes you do by eye.
_SIDE_MODES = ["Mirrored", "Stacked"]

# Distinct hues for the 0DTE flow lines. Deliberately not the call/put pair:
# these identify STRIKES, and reusing green/red would read as sides.
_FLOW_COLOURS = ["#4d8eff", "#e8b64c", "#10d4a3", "#f05252",
                 "#a78bfa", "#c9a227", "#ec4899", "#22d3ee"]


def _fmt_money(value: float | None, unit: str = "") -> str:
    """A large figure at a readable magnitude, or an em dash.

    NO CURRENCY MARK BY DEFAULT. Exposure is derived from a notional — gamma
    times open interest times a hundred times spot squared — so the units are
    real but the number is not money anybody holds or pays, and a "$" invites
    it to be read as one. The magnitude suffix is the part that matters.

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


def _money_ticks(values, unit: str = "") -> dict:
    """Axis ticks labelled the way the headline numbers are labelled.

    Plotly's SI format writes a billion as "G" — correct for engineers, wrong
    for anyone reading a magnitude, and different from the "29.3B" in the
    strip directly above the chart. Two notations for one number on one screen
    is a reader's problem, not a formatting preference, so the ticks are placed
    here and labelled with `_fmt_money`.

    The step is the largest of 1/2/2.5/5 x 10^k that still leaves about six
    ticks across the range, and the range always includes zero: on these charts
    the sign is the message, so an axis that cropped zero out would hide it.
    """
    series = pd.Series(list(values), dtype="float64").dropna()
    if series.empty:
        return {}
    lo, hi = min(0.0, float(series.min())), max(0.0, float(series.max()))
    if hi == lo:
        return {}

    import math
    rough = (hi - lo) / 6.0
    power = 10.0 ** math.floor(math.log10(rough))
    step = next((m * power for m in (1.0, 2.0, 2.5, 5.0) if m * power >= rough),
                10.0 * power)

    first = math.floor(lo / step)
    ticks = [(first + i) * step for i in range(int((hi - lo) / step) + 3)]
    ticks = [t for t in ticks if lo - step <= t <= hi + step]
    return dict(tickmode="array", tickvals=ticks,
                ticktext=[_fmt_money(t, unit) for t in ticks])


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


def _gap(height: int = 40) -> None:
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

    # Capped to the same width as the charts below, so the control row
    # and the panels it drives share one left and right edge.
    with st.container(key="gexcontrols"):
        c1, c3 = st.columns([2, 2], vertical_alignment="bottom")
        with c1:
            choice = st.selectbox(
                "Expiry", ["All expiries", *expiries], key="gex_expiry",
                format_func=lambda e: (e if e == "All expiries"
                                       else fmt.exp_label(e, dte_by_expiry)),
                help="All expiries is the whole-chain figure most GEX commentary "
                     "refers to. The third Friday appears twice because SPX lists "
                     "two contracts for it.",
            )
        # ONE widget each, not a hand-built row of buttons. The chevron
        # stepper these replace was three columns held together by CSS, and
        # it drifted out of line with the selectbox beside it every time the
        # column widths changed. A segmented control is laid out by Streamlit
        # and reaches any option in a single click rather than stepping.
        with c3:
            view = st.segmented_control(
                "View", _VIEWS, default=_VIEWS[0], key="gex_view",
                selection_mode="single") or _VIEWS[0]

    # The OI/Volume layout picker is NOT in that row. It changes nothing about
    # the gamma panel it used to sit above, and reading its value here rather
    # than where the widget is drawn lets it live beside the two panels it
    # does change. Session state already holds the choice from the last run;
    # on the first, no key exists yet and the default applies.
    side_mode = st.session_state.get("gex_side_mode") or _SIDE_MODES[0]

    expiry = None if choice == "All expiries" else choice
    per_strike = gex.by_strike(chain, ctx.spx_price, expiry=expiry)
    if per_strike.empty:
        st.info("No strike in this selection carries gamma.")
        return

    # EVERY strike collected, always. The window control that used to live
    # here traded a figure that stayed put for one that changed under the
    # reader every time they narrowed it — and Option Alpha's ratio and
    # sentiment are defined over the displayed bars, so the numbers moved with
    # it. Showing the whole chain makes them one answer instead of five.
    shown = per_strike
    totals = gex.summary(shown)          # displayed bars, per the documentation

    # Wrapped so the stylesheet can cap the width. Full-bleed on a wide
    # monitor stretches a ~450px panel across ~1700px: the bars turn into
    # ribbons and the shape of the curve — the thing being read — flattens.
    # The headline strip is INSIDE it: outside, it ran the full width of the
    # page while the chart under it stopped at the cap, and the mismatch read
    # as the strip being broken rather than as two different widths.
    with st.container(key="gexbody"):
        _draw_headline(totals)
        _draw_side_mode_control()
        _draw_strike_panels(ctx, shown, view, expiry, side_mode == "Stacked")
        _gap()
        # The cumulative gamma curve stood here. It restated the flip strike
        # the headline already gives as a number, and its shape was dominated
        # by the far strikes where the running total starts. Net flow answers
        # the question the bars cannot: not where the gamma is, but what
        # today put there.
        _draw_net_flow(ctx)
        _draw_caption(totals, expiry, per_strike, view)

        st.divider()
        _draw_time_panels(ctx, shown, expiry)

        st.divider()
        _draw_dealer_structure(ctx, per_strike, choice, expiry, dte_by_expiry)


def _draw_side_mode_control() -> None:
    """Drawn at the top right of the figure whose lower two panels it lays out.

    render() has already read the value out of session state, so this is the
    widget only. Streamlit is happy either way — the key holds the choice
    between runs — and it puts the control where its effect is visible."""
    with st.container(key="gexsidemode"):
        st.segmented_control(
            "OI / Volume", _SIDE_MODES, default=_SIDE_MODES[0],
            key="gex_side_mode", selection_mode="single", label_visibility="collapsed",
            help="Mirrored draws puts below the axis, so the two sides "
                 "compare at one strike. Stacked adds them, so totals "
                 "compare BETWEEN strikes.")


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
                        view: str, expiry: str | None, stack: bool) -> None:
    fig = _strike_figure(shown, ctx.chain_df, ctx.spx_price, view, expiry,
                         stack, ctx.snapshot_id)
    with st.container(key="chartcard_gex"):
        st.plotly_chart(fig, use_container_width=True)


# MEMOISED FIGURES. Building these is the bulk of what a click on this tab
# costs — a chevron press was rebuilding five Plotly figures from scratch,
# and four of them do not depend on what the chevron changed. The cache key
# is the SNAPSHOT plus the choices that actually alter the drawing; the
# frames are passed with a leading underscore so Streamlit skips hashing
# them, which on a 3,000-row chain costs more than the redraw it saves.
#
# Nothing may mutate a figure after it is returned. A cached object is shared
# with every later hit, so a caller that edited one would corrupt every
# subsequent render — and silently, since it would look right the first time.
@st.cache_data(show_spinner=False, max_entries=16)
def _strike_figure(_shown: pd.DataFrame, _chain: pd.DataFrame, spot: float,
                   view: str, expiry: str | None, stack: bool,
                   snapshot_id: int):
    shown, ctx_chain = _shown, _chain
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.14,
        row_heights=[0.48, 0.26, 0.26],
        specs=[[{"secondary_y": True}], [{}], [{}]],
        subplot_titles=("Gamma Exposure", "Open Interest", "Volume"),
    )

    # ── Panel 1, background: the day's volume as translucent fills ───────────
    # Drawn FIRST so the bars sit on top of it, and on a secondary axis
    # because contracts traded and gamma exposure are not the same unit.
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
                 "Strike %{x:,.0f}<br>Call GEX %{y:,.0f}<extra></extra>")
        _add_bar(fig, shown["strike"], -shown["put_gex"], _PUT, 1,
                 "Strike %{x:,.0f}<br>Put GEX %{y:,.0f}<extra></extra>")
    elif view == "Abs Gamma":
        # Stacked, so the one positive total still shows its two halves.
        _add_bar(fig, shown["strike"], shown["call_gex"], _CALL, 1,
                 "Strike %{x:,.0f}<br>Call %{y:,.0f}<extra></extra>")
        _add_bar(fig, shown["strike"], shown["put_gex"], _PUT, 1,
                 "Strike %{x:,.0f}<br>Put %{y:,.0f}<extra></extra>")
    elif view == "Net Gamma":
        colours = [_CALL if v >= 0 else _PUT for v in shown["net_gex"]]
        _add_bar(fig, shown["strike"], shown["net_gex"], colours, 1,
                 "Strike %{x:,.0f}<br>Net GEX %{y:,.0f}<extra></extra>")
    else:
        dex = gex.dex_by_strike(ctx_chain, spot, expiry=expiry)
        dex = dex[dex["strike"].isin(shown["strike"])]
        _add_bar(fig, dex["strike"], dex["call_dex"], _CALL, 1,
                 "Strike %{x:,.0f}<br>Call DEX %{y:,.0f}<extra></extra>")
        _add_bar(fig, dex["strike"], dex["put_dex"], _PUT, 1,
                 "Strike %{x:,.0f}<br>Put DEX %{y:,.0f}<extra></extra>")

    # ── Panels 2 and 3 ───────────────────────────────────────────────────────
    # barmode is "relative", so two positive bars stack and a positive and a
    # negative one straddle the axis. The put side flipping sign is therefore
    # the WHOLE difference between the two layouts — the hover still reports
    # the put's own figure, unsigned, either way.
    put_sign = 1.0 if stack else -1.0
    _add_bar(fig, shown["strike"], shown["call_oi"], _CALL, 2,
             "Strike %{x:,.0f}<br>Call OI %{customdata:,.0f}<extra></extra>",
             custom=shown["call_oi"])
    _add_bar(fig, shown["strike"], put_sign * shown["put_oi"], _PUT, 2,
             "Strike %{x:,.0f}<br>Put OI %{customdata:,.0f}<extra></extra>",
             custom=shown["put_oi"])
    _add_bar(fig, shown["strike"], shown["call_volume"], _CALL, 3,
             "Strike %{x:,.0f}<br>Call volume %{customdata:,.0f}<extra></extra>",
             custom=shown["call_volume"])
    _add_bar(fig, shown["strike"], put_sign * shown["put_volume"], _PUT, 3,
             "Strike %{x:,.0f}<br>Put volume %{customdata:,.0f}<extra></extra>",
             custom=shown["put_volume"])

    for row in (1, 2, 3):
        fig.add_vline(x=spot,
                      line=dict(color="#8fa9c4", width=1, dash="dot"),
                      row=row, col=1)
    # Anchored just INSIDE the panel, not above it: at the top edge this label
    # lands on the "Gamma Exposure" subplot title and the two overprint.
    fig.add_annotation(
        x=spot, y=0.98, yref="y domain", row=1, col=1,
        text=f"{spot:,.2f}", showarrow=False, yanchor="top",
        font=dict(color=_BRIGHT, size=10), bgcolor="#16283d", borderpad=3,
    )

    flip = gex.flip_strike(shown)
    if flip is not None and shown["strike"].min() <= flip <= shown["strike"].max():
        fig.add_vline(x=flip, line=dict(color=_FLIP, width=1, dash="dash"),
                      row=1, col=1)

    # ── Axes a reader can read without hovering ──────────────────────────────
    # Ticks are placed and labelled by _money_ticks rather than left to
    # Plotly, so a billion reads "$1.0B" here and in the headline strip above
    # instead of the SI "1G". The explicit zero line is what makes "above the
    # axis" and "below the axis" a reading rather than an inference: on the
    # Call vs Put and Net views the SIGN is the message.
    #
    # No y-axis titles: each panel already carries a subplot heading saying
    # what it is, and the unit is in the caption under the chart. A rotated
    # title on the left only eats width the bars could use.
    if view == "Delta Exposure":
        primary = pd.concat([dex["call_dex"], dex["put_dex"]])
    elif view == "Call vs Put":
        primary = pd.concat([shown["call_gex"], -shown["put_gex"]])
    elif view == "Abs Gamma":
        primary = pd.concat([shown["abs_gex"], shown["call_gex"]])
    else:
        primary = shown["net_gex"]

    oi_side = (shown["call_oi"] + shown["put_oi"] if stack
               else pd.concat([shown["call_oi"], -shown["put_oi"]]))
    vol_side = (shown["call_volume"] + shown["put_volume"] if stack
                else pd.concat([shown["call_volume"], -shown["put_volume"]]))

    fig.update_yaxes(title_text=None, row=1, col=1, secondary_y=False,
                     showticklabels=True, ticks="outside", ticklen=4,
                     tickcolor=_GRID, zeroline=True, zerolinecolor="#3c5570",
                     zerolinewidth=1, **_money_ticks(primary))
    fig.update_yaxes(title_text=None, row=1, col=1, secondary_y=True,
                     showgrid=False, showticklabels=True, ticks="outside",
                     ticklen=4, tickcolor=_GRID,
                     **_money_ticks(pd.concat([shown["call_volume"],
                                               shown["put_volume"]]), ""))
    fig.update_yaxes(title_text=None, row=2, col=1, zeroline=True,
                     zerolinecolor="#3c5570", **_money_ticks(oi_side, ""))
    fig.update_yaxes(title_text=None, row=3, col=1, zeroline=True,
                     zerolinecolor="#3c5570", **_money_ticks(vol_side, ""))

    # Strike labels on EVERY panel, not only the bottom one: shared_xaxes
    # hides them on the upper rows, which leaves the gamma panel — the one
    # people actually read — with no scale of its own. The step is chosen from
    # the displayed span (~18 labels) and rounded to a multiple of 5 so every
    # tick lands on a real listed strike; Plotly's default picks 50 or 100,
    # which is three labels across a twenty-strike window.
    span = float(shown["strike"].max() - shown["strike"].min())
    step = max(5.0, round(span / 18.0 / 5.0) * 5.0) if span > 0 else 5.0
    # Plain integers, level. A strike is an identifier, not a quantity: the
    # thousands comma in "7,620" is punctuation nobody uses saying the number
    # out loud, and the -45 degree tilt was only there to make room for the
    # extra width it took. Without it they fit horizontally.
    fig.update_xaxes(showticklabels=True, ticks="outside", ticklen=4,
                     tickcolor=_GRID, tickangle=0, dtick=step,
                     tickformat="d")
    fig.update_xaxes(title_text=None, row=3, col=1)
    fig.update_layout(barmode="relative", bargap=0.15)
    _dark(fig, 940)
    return fig


def _add_bar(fig, x, y, colour, row, hover, secondary: bool = False,
             custom=None) -> None:
    fig.add_trace(
        go.Bar(x=x, y=y, marker=dict(color=colour, line=dict(width=0)),
               customdata=custom, hovertemplate=hover, showlegend=False),
        row=row, col=1, **({"secondary_y": secondary} if row == 1 else {}),
    )


# ─────────────────────────────────────────────────────────────────────────────
# The time panels — the half a vendor's screen cannot show
# ─────────────────────────────────────────────────────────────────────────────

def _draw_time_panels(ctx: ViewContext, shown: pd.DataFrame,
                      expiry: str | None) -> None:
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
    _draw_cumulative_volume(intraday, shown, span, ctx.snapshot_id, expiry)
    _gap()
    _draw_zero_dte_flow(ctx, span)
    _gap()
    _draw_oi_change(ctx, shown, expiry)


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


def _draw_cumulative_volume(intraday: pd.DataFrame, shown: pd.DataFrame,
                            span: list, snapshot_id: int,
                            expiry: str | None) -> None:
    fig = _volume_figure(intraday, shown, span, snapshot_id, expiry)
    if fig is None:
        return
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "· **What this shows:** calls traded minus puts traded at each price "
        "level, adding up as the day goes on. A line **above zero** means "
        "more people are buying calls there (bets the market rises); "
        "**below zero**, more puts (bets it falls, or protection). A line "
        "that goes flat means trading at that price has stopped. Only the "
        "eight busiest price levels are drawn, or the chart is unreadable."
    )


@st.cache_data(show_spinner=False, max_entries=4)
def _volume_figure(_intraday: pd.DataFrame, _shown: pd.DataFrame,
                   span: list, snapshot_id: int, expiry: str | None):
    """Net volume building up through the day, per strike.

    The broker's `volume` field is already a running total for the session, so
    this needs no cumulative sum of its own — the accumulation is what the
    field IS. What the panel adds is the shape of it: which strikes were being
    worked early, which only came alive after lunch, and where the call side
    handed over to the put side.
    """
    intraday, shown = _intraday, _shown
    strikes = list(shown["strike"])
    if not strikes:
        return None
    work = intraday[intraday["strike"].isin(strikes)].copy()
    if work.empty:
        return None
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
                                 gridcolor=_GRID,
                                 **_money_ticks(work["net_volume"])))
    _dark(fig, 320, legend=True)
    return fig


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

    built = _flow_figure(zero, span, ctx.snapshot_id)
    if built is None:
        return
    fig, total_now, total_open, levels = built

    _strip([
        _metric("0DTE GEX now", _fmt_money(total_now),
                _CALL if total_now >= 0 else _PUT),
        _metric("0DTE flow today", _fmt_money(total_now - total_open),
                _CALL if total_now >= total_open else _PUT),
        _metric("Key levels",
                " · ".join(f"{s:,.0f}" for s in sorted(levels)[:3])),
    ])
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "· **What this shows:** the same gamma figure as the charts above, "
        "but only for options that **expire today**, and traced through the "
        "day instead of frozen at this moment. These are the fastest-moving "
        "options on the board — they are worthless or settled by this "
        "afternoon — so a price level can swing from one side to the other "
        "in minutes. **Key levels** are simply the price levels with the most "
        "gamma sitting on them right now."
    )


@st.cache_data(show_spinner=False, max_entries=4)
def _flow_figure(_zero: pd.DataFrame, span: list, snapshot_id: int):
    """The figure and the three headline numbers drawn above it.

    Returned together because they are computed together: the "key levels"
    ARE the lines on the chart, and deriving them twice is how a strip and a
    legend come to disagree.
    """
    zero = to_display_time(_zero, config.DISPLAY_TIMEZONE).copy()
    scale = zero["underlying_price"].map(gex.dollar_scale)
    zero["net_gex"] = (zero["call_gamma_oi"] - zero["put_gamma_oi"]) * scale

    latest = zero[zero["timestamp"] == zero["timestamp"].max()]
    levels = (latest.assign(mag=latest["net_gex"].abs())
              .nlargest(7, "mag")["strike"].tolist())
    if not levels:
        return None

    total_now = float(latest["net_gex"].sum())
    first_ts = zero["timestamp"].min()
    total_open = float(zero[zero["timestamp"] == first_ts]["net_gex"].sum())

    fig = go.Figure()
    for i, strike in enumerate(sorted(levels)):
        one = zero[zero["strike"] == strike].sort_values("timestamp")
        fig.add_trace(go.Scatter(
            x=one["timestamp"], y=one["net_gex"], name=f"{strike:,.0f}",
            mode="lines", line=dict(color=_FLOW_COLOURS[i % len(_FLOW_COLOURS)],
                                    width=1.5),
            hovertemplate=f"{strike:,.0f}<br>%{{x|%H:%M}}"
                          "<br>Net GEX %{y:,.0f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color="#8fa9c4", width=1))
    fig.update_layout(xaxis=_time_axis(span),
                      yaxis=dict(title="0DTE net GEX", gridcolor=_GRID,
                                 zeroline=True, zerolinecolor="#3c5570",
                                 **_money_ticks(zero["net_gex"])))
    _dark(fig, 340, legend=True)
    return fig, total_now, total_open, levels


def _draw_oi_change(ctx: ViewContext, shown: pd.DataFrame,
                    expiry: str | None) -> None:
    """Today's open interest against the previous session's.

    Scoped to the SAME expiry as `shown`. Yesterday summed across every expiry
    minus one expiry's today is the rest of the board reported as an overnight
    liquidation — six-figure negative bars at strikes that barely traded.
    """
    prior = ctx.load_prior_session_oi(ctx.session_date, expiry)
    if prior is None or prior.empty:
        st.info(
            "No previous session to compare open interest against — this is "
            "the first collected day, or the record has a gap before it."
        )
        return

    fig = _oi_change_figure(shown, prior, ctx.spx_price, ctx.snapshot_id,
                            expiry)
    if fig is None:
        st.info("Open interest is unchanged from the previous session.")
        return
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "· **What this shows:** how many option contracts were newly opened "
        "or closed out at each price level since yesterday. **Open interest** "
        "is the count of contracts that exist; it is published once a day, "
        "overnight. Today's count minus yesterday's is therefore real "
        "positions being put on (bars up) or taken off (bars down) — not the "
        "same contract being traded back and forth. Puts are drawn "
        "downward. **No data vendor sells this history; yours goes back to "
        "23 June because this dashboard has been recording it.**"
    )


@st.cache_data(show_spinner=False, max_entries=4)
def _oi_change_figure(_shown: pd.DataFrame, _prior: pd.DataFrame, spot: float,
                      snapshot_id: int, expiry: str | None):
    change = gex.oi_change(_shown, _prior)
    change = change[(change["call_oi_change"] != 0) | (change["put_oi_change"] != 0)]
    if change.empty:
        return None

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=change["strike"], y=change["call_oi_change"], name="Calls",
        marker=dict(color=_CALL, line=dict(width=0)),
        hovertemplate="Strike %{x:,.0f}<br>Call OI change %{y:+,.0f}<extra></extra>"))
    fig.add_trace(go.Bar(
        x=change["strike"], y=-change["put_oi_change"], name="Puts",
        marker=dict(color=_PUT, line=dict(width=0)),
        hovertemplate="Strike %{x:,.0f}<br>Put OI change %{y:+,.0f}<extra></extra>"))
    fig.add_vline(x=spot, line=dict(color="#8fa9c4", width=1, dash="dot"))
    fig.add_hline(y=0, line=dict(color="#2a3f56", width=1))
    fig.update_layout(barmode="relative", bargap=0.15,
                      xaxis=dict(title="Strike", gridcolor=_GRID),
                      yaxis=dict(title="Contracts opened / closed",
                                 gridcolor=_GRID))
    _dark(fig, 300, legend=True)
    return fig


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
        "**Reading the shading:** the faint violet is how many puts traded "
        "today at each price level, the faint blue is calls; grey is where "
        "they overlap. It is measured on the right-hand scale, not the left. "
        "In the two lower panels puts are drawn downward so the two sides can "
        "be compared at a glance — hovering shows the real, positive count.",
        "**Ratio and Sentiment describe only the bars you can see**, so they "
        "move if the strike range changes. Sentiment is the percentage of "
        "the price levels shown that have positive gamma. Ratio is the "
        "bigger side divided by the smaller, with the sign showing which one "
        "won. These are Option Alpha's published definitions, so the numbers "
        "should match theirs on the same data.",
        "**Gamma flip is left blank when the crossing point lands near "
        "either edge of the data.** The running total starts at the lowest "
        "price level recorded, so if the record stops before the far "
        "downside options do, the crossing gets pushed up against that edge "
        "and is an artefact of where collection stopped, not a real level. "
        "A blank is the honest answer. This happens often on a same-day "
        "expiry, where only about ±100 points are collected.",
        "**Every gamma number here rests on one assumption: that the market "
        "makers on the other side are holding calls and owe puts.** That is "
        "the standard convention, but it is an assumption, not measured "
        "data — nobody publishes what dealers actually hold. If it is wrong, "
        "every figure on this tab has the wrong sign.",
        f"**Only prices between {lo:,.0f} and {hi:,.0f} are recorded** — "
        f"about ±300 points either side of where SPX is now. Options further "
        f"out are missing from these totals rather than being zero. At that "
        f"edge they carried 0.2% of the gamma of an at-the-money option, so "
        f"the amount left out is small.",
    ]
    if view == "Delta Exposure":
        lines.insert(1, (
            "**Delta Exposure does not use the dealer assumption above.** A "
            "put's delta is already a negative number, so applying the "
            "convention again would count it twice. This panel is simply "
            "what the option chain itself reports."
        ))
    if expiry is None:
        lines.append(
            "**All expiry dates are added together here.** Options expiring "
            "soonest carry far more gamma than distant ones, so this view is "
            "dominated by the nearest few dates."
        )
    st.caption("  \n".join(f"· {line}" for line in lines))


# ─────────────────────────────────────────────────────────────────────────────
# Dealer structure — where the flow went, and whether it stayed
#
# These two panels answer what the per-strike ones cannot. Gamma exposure
# describes what is LISTED; these describe what was TRADED today. The bubble
# chart spreads that volume across expiry AND strike, so 0DTE gamma chasing
# separates from monthly OPEX hedging instead of both collapsing into one
# per-strike total; the positioning table reads the same volume against the
# overnight change in open interest — the only thing in this data that can
# tell a position opened from one contract changing hands forty times.
# ─────────────────────────────────────────────────────────────────────────────

# The reference design's palette, kept exactly. Flow colour is a reading, and
# a green here has to mean what green means on every other panel.
_FLOW_FILL = {"call": "#10b981", "put": "#ef4444", "balanced": "#f59e0b"}
_FLOW_EDGE = {"call": "#059669", "put": "#dc2626", "balanced": "#d97706"}
_FLOW_NAME = {"call": "Call-dominated", "put": "Put-dominated",
              "balanced": "Balanced / straddle"}
_SPOT_ACCENT = "#38bdf8"
_VOL_BAR = "#8b5cf6"
# Today's volume is context, not evidence: it is drawn, but muted, because
# nothing on this panel is computed from it.
_TODAY_BAR = "rgba(139,92,246,0.32)"

_TONE_BADGE = {
    "accumulation": ("rgba(16,185,129,.15)", "#34d399", "rgba(16,185,129,.3)"),
    "churn":        ("rgba(148,163,184,.15)", "#cbd5e1", "rgba(148,163,184,.3)"),
    "liquidation":  ("rgba(239,68,68,.15)", "#f87171", "rgba(239,68,68,.3)"),
    "wall":         ("rgba(56,189,248,.15)", "#7dd3fc", "rgba(56,189,248,.3)"),
    "quiet":        ("transparent", "#41586e", "transparent"),
}


def _draw_dealer_structure(ctx: ViewContext, per_strike: pd.DataFrame,
                           choice: str, expiry: str | None,
                           dte_by_expiry: dict) -> None:
    st.markdown(
        '<div class="sh"><span class="sh-ico">🏛️</span>'
        '<span class="sh-ttl">Dealer structure &amp; positioning</span>'
        '<span class="sh-bdg">term structure · what stuck yesterday</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    _draw_term_bubbles(ctx)
    _gap()
    _draw_scope_pill(per_strike, choice, expiry, dte_by_expiry)
    _draw_volume_vs_oi(ctx, per_strike, expiry)


_FLOW_SCOPES = {"0DTE": 0, "All expiries": None}


def _draw_net_flow(ctx: ViewContext) -> None:
    """Net gamma exposure gained or lost at each strike since the open.

    A price ladder, not a time series: strike up the side, the change along
    the bottom, zero down the middle. Green to the right is exposure ADDED
    today, red to the left exposure taken off — and the length is the size of
    the day's change at that strike, which the level charts above cannot show
    because they are dominated by positions that were already there.
    """
    scope = st.segmented_control(
        "Scope", list(_FLOW_SCOPES), default="0DTE", key="gex_flow_scope",
        selection_mode="single", label_visibility="collapsed",
        help="0DTE is where the day's flow actually moves the market — those "
             "positions settle in hours. All expiries is the whole board.",
    ) or "0DTE"

    intraday = ctx.load_intraday_strike_metrics(
        ctx.session_date, ctx.snapshot_id, _FLOW_SCOPES[scope])
    if intraday is None or intraday.empty:
        st.info("No snapshots for this scope yet today.")
        return

    rows = _net_flow(intraday, ctx.snapshot_id, scope)
    if rows.empty:
        st.info(
            "Only one snapshot so far this session, so there is no change to "
            "measure yet. This fills in from the second collection."
        )
        return

    added = float(rows.loc[rows["flow"] > 0, "flow"].sum())
    removed = float(rows.loc[rows["flow"] < 0, "flow"].sum())
    _strip([
        _metric("Net flow today", _fmt_money(added + removed),
                _CALL if added + removed >= 0 else _PUT),
        _metric("Gamma added", _fmt_money(added), _CALL),
        _metric("Gamma removed", _fmt_money(removed), _PUT),
    ])
    st.plotly_chart(_net_flow_figure(rows, ctx.spx_price, ctx.snapshot_id, scope),
                    use_container_width=True)
    st.caption(
        "· **What this shows:** how much the gamma at each price level has "
        "**changed since this morning**. Green to the right means gamma was "
        "added there today; red to the left means it was taken away. "
        "**Why it is separate from the charts above:** those show everything "
        "sitting on the board, and most of that was put on days or weeks "
        "ago. A price level can be piled high and have seen no trading at "
        "all today. This is the only chart here that shows what *today* did. "
        "The dotted line is the current SPX price."
    )


@st.cache_data(show_spinner=False, max_entries=4)
def _net_flow(_intraday: pd.DataFrame, snapshot_id: int, scope: str):
    return gex.net_flow_by_strike(_intraday, top=_NET_FLOW_STRIKES)


# Enough rungs to see the shape of the ladder, few enough that each bar keeps
# a readable height. The rest of the board moved by too little to draw.
_NET_FLOW_STRIKES = 28


@st.cache_data(show_spinner=False, max_entries=4)
def _net_flow_figure(_rows: pd.DataFrame, spot: float, snapshot_id: int,
                     scope: str):
    rows = _rows
    colours = [_CALL if v >= 0 else _PUT for v in rows["flow"]]
    fig = go.Figure(go.Bar(
        x=rows["flow"], y=rows["strike"], orientation="h",
        marker=dict(color=colours), width=3.2,
        customdata=rows[["open_gex", "now_gex"]].to_numpy(),
        hovertemplate="Strike %{y:,.0f}<br>Flow today %{x:,.0f}"
                      "<br>At open %{customdata[0]:,.0f}"
                      "<br>Now %{customdata[1]:,.0f}<extra></extra>",
    ))
    fig.add_vline(x=0, line=dict(color="#2a3f56", width=1))
    # Spot as a dotted rung across the ladder, so "above" and "below" the
    # money are read off the chart rather than remembered.
    fig.add_hline(y=spot, line=dict(color="#9575cd", width=1.4, dash="dot"))
    fig.add_annotation(
        x=1, xref="paper", y=spot, xanchor="right", yanchor="bottom",
        text=f"SPOT {spot:,.2f}", showarrow=False,
        font=dict(color="#9575cd", size=9))
    fig.update_layout(
        xaxis=dict(gridcolor=_GRID, zeroline=False,
                   **_money_ticks(rows["flow"])),
        yaxis=dict(gridcolor=_GRID, tickformat="d", dtick=_ladder_step(rows)),
        bargap=0.35,
    )
    _dark(fig, 520)
    return fig


def _ladder_step(rows: pd.DataFrame) -> float:
    """Strike labels every step, in multiples of five."""
    span = float(rows["strike"].max() - rows["strike"].min())
    return max(5.0, round(span / 14.0 / 5.0) * 5.0)


def _draw_scope_pill(per_strike: pd.DataFrame, choice: str,
                     expiry: str | None, dte_by_expiry: dict) -> None:
    """What the table below is showing, in one line.

    The table follows the Expiry control, and a reader who has scrolled this
    far has left that control off the top of the screen. Volume is the two sides added across every strike in scope, which is the
    same figure the table's bars are drawn from.
    """
    scope = (choice if expiry is None
             else fmt.exp_label(expiry, dte_by_expiry))
    volume = float(per_strike[["call_volume", "put_volume"]].sum().sum())
    st.markdown(
        '<div class="scope-pill">'
        f'<span class="k">Viewing</span><span class="v">{scope}</span>'
        '<span class="sep">|</span>'
        f'<span class="k">Total volume</span>'
        f'<span class="v">{_compact(volume)} contracts</span>'
        '</div>',
        unsafe_allow_html=True,
    )


def _compact(value: float) -> str:
    """48,200 as "48.2K". The pill is a glance, not a figure to reconcile."""
    for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= cut:
            return f"{value / cut:,.1f}{suffix}"
    return f"{value:,.0f}"


def _draw_term_bubbles(ctx: ViewContext) -> None:
    """Volume across expiry and strike at once.

    Deliberately ignores the Expiry control above. This panel exists to
    COMPARE expiries; filtering it to one would leave a single column.
    """
    # Square root always. The log toggle that used to sit here compressed the
    # range so hard that almost every bubble came out near the maximum, which
    # on a board this dense fused the columns into solid bars — it made the
    # one problem this panel has worse, so it is gone rather than defaulted off.
    scale = "sqrt"

    points = _bubble_points(ctx.chain_df, ctx.spx_price, scale, ctx.snapshot_id)
    if points.empty:
        st.info(
            "No volume within 4% of spot in this snapshot yet. This panel "
            "fills in as the session trades."
        )
        return

    drawn = dealer.most_traded(points)
    st.plotly_chart(
        _bubble_figure(drawn, ctx.spx_price, scale, ctx.snapshot_id),
        use_container_width=True)

    expiries_all = points["expiry_label"].nunique()
    expiries_drawn = drawn["expiry_label"].nunique()
    # Never claim "every expiry" when the guard has trimmed some off the end.
    columns = (f"every expiry date on record ({expiries_drawn}) is drawn"
               if expiries_drawn == expiries_all
               else f"the {expiries_drawn} nearest expiry dates are drawn, "
                    f"out of {expiries_all} on record")
    st.caption(
        "· **What this shows:** where today's trading actually happened — "
        "across both **when the option expires** (bottom) and **what price "
        "it is betting on** (side). **Bigger bubble = more contracts "
        "traded.** **Green** means mostly calls were traded there (upside "
        "bets), **red** mostly puts (downside bets or protection), and "
        "**amber** means roughly equal amounts of both, which usually means "
        "people are betting on a big move without picking a direction. The "
        "blue dashed line is the current SPX price.  " "\n"
        f"· **What is left out:** {columns}, and within each only the "
        f"**{dealer.TOP_STRIKES_PER_EXPIRY} busiest price levels**. There are "
        "about 120 price levels in range; drawing them all turns each column "
        "into one solid blob. Bubble size grows more slowly than the volume "
        "it represents, or a quiet expiry would shrink to a dot next to "
        "today's. **The Expiry box at the top does not change this chart** — "
        "comparing the different expiry dates is the whole point of it."
    )


@st.cache_data(show_spinner=False, max_entries=8)
def _bubble_points(_chain: pd.DataFrame, spot: float, scale: str,
                   snapshot_id: int) -> pd.DataFrame:
    return dealer.bubble_points(_chain, spot, scale=scale)


@st.cache_data(show_spinner=False, max_entries=8)
def _bubble_figure(_points: pd.DataFrame, spot: float, scale: str,
                   snapshot_id: int):
    points = _points
    # Discrete columns ordered by days to expiry, plotted against the LABEL
    # rather than the date: the gap between tomorrow and the monthly is then
    # one column, not five weeks of empty axis.
    order = (points[["expiry_label", "expiry_order"]]
             .drop_duplicates().sort_values("expiry_order")["expiry_label"]
             .tolist())

    fig = go.Figure()
    for flow in ("call", "put", "balanced"):
        side = points[points["flow"] == flow]
        if side.empty:
            continue
        fig.add_trace(go.Scatter(
            x=side["expiry_label"], y=side["strike"], mode="markers",
            name=_FLOW_NAME[flow],
            marker=dict(
                size=side["radius"] * 2.0,        # Plotly sizes by DIAMETER
                sizemode="diameter",
                color=_FLOW_FILL[flow], opacity=0.62,
                line=dict(color=_FLOW_EDGE[flow], width=1.2),
            ),
            customdata=side[["call_volume", "put_volume", "total_volume",
                             "pcr", "notional"]].to_numpy(),
            hovertemplate=(
                "%{x} · %{y:,.0f}<br>"
                "Total volume %{customdata[2]:,.0f}<br>"
                "Calls %{customdata[0]:,.0f} · Puts %{customdata[1]:,.0f}<br>"
                "PCR %{customdata[3]:.2f}<br>"
                "Premium %{customdata[4]:$,.0f}<extra></extra>"),
        ))

    fig.add_hline(y=spot, line=dict(color=_SPOT_ACCENT, width=1.5, dash="dash"))
    fig.add_annotation(
        x=0, xref="paper", y=spot, xanchor="left", yanchor="middle",
        text=f"SPX SPOT {spot:,.2f}", showarrow=False,
        font=dict(color="#ffffff", size=9), bgcolor="#0369a1", borderpad=3)

    fig.update_layout(
        xaxis=dict(type="category", categoryorder="array", categoryarray=order,
                   gridcolor=_GRID, showgrid=True, tickangle=-45,
                   automargin=True),
        yaxis=dict(gridcolor=_GRID, tickformat="d"),
        hovermode="closest",
    )
    # Taller than the panels above it. Height here is not decoration: it is
    # how many points of strike fit between two bubbles, and at 460px the
    # neighbours touched.
    _dark(fig, 620, legend=True)
    return fig

def _draw_volume_vs_oi(ctx: ViewContext, per_strike: pd.DataFrame,
                       expiry: str | None) -> None:
    """Today's volume beside the overnight change in open interest."""
    # Scoped to the SAME expiry as the volume beside it. All-expiry open
    # interest minus one expiry's is the rest of the board reported as an
    # overnight liquidation.
    prior = ctx.load_prior_session_oi(ctx.session_date, expiry)
    # dealer.VERDICT_COLUMNS is in the cache key so that CHANGING the shape of
    # that frame invalidates the cache. Streamlit hashes the body of the
    # decorated function, not the module it calls, so a running server that
    # was started before core/dealer.py changed keeps serving the old columns
    # to the new renderer — which is a KeyError on the first redraw, in the
    # user's face, from code that is correct on disk.
    rows = _positioning(per_strike, prior, ctx.spx_price, ctx.snapshot_id,
                        expiry, dealer.VERDICT_COLUMNS)
    if rows.empty:
        st.info("No strike within 2.5% of spot has traded yet today.")
        return

    st.markdown(_positioning_table(rows, ctx.spx_price), unsafe_allow_html=True)
    _draw_worked_example(rows, per_strike, prior, ctx.session_date)

    if rows["delta_oi"].isna().all():
        st.caption(
            "· **There is no previous day to compare against**, so the "
            "change column and every verdict are blank. The count of "
            "existing contracts is published only once a day, overnight — "
            "without yesterday's number there is nothing to subtract from "
            "today's. Showing a zero instead would be a guess dressed up as "
            "a reading."
        )
        return
    st.caption(
        "· **What this shows:** whether the trading at each price level left "
        "real positions behind, or was the same contracts changing hands over "
        "and over. Volume alone cannot tell those apart. The count of "
        "contracts that exist settles it, because it only counts what was "
        "still open at the close.  " "\n"
        "· **This table is about YESTERDAY.** The exchange publishes that "
        "count once a day, after the close, so the newest one available "
        "covers yesterday's session. Today's trading has not been counted "
        "anywhere yet — it arrives tonight. Both the change and the verdict "
        "are therefore yesterday's, measured against yesterday's volume. "
        "**Today's volume is shown in its own column for comparison and is "
        "never divided by**; mixing the two days produced changes larger than "
        "the whole day's trading on 20% of contracts, which cannot happen.  " "\n"
        "· A verdict is offered only where that day's volume was in the "
        "busiest quarter of the price levels shown. The rest is ordinary "
        "two-way trade and is left blank rather than labelled."
    )


def _draw_worked_example(rows: pd.DataFrame, today: pd.DataFrame,
                        prior: pd.DataFrame, session_date: str) -> None:
    """The arithmetic behind the biggest row, in the numbers on screen.

    Regenerated on every render from the same frames the table was drawn
    from, so it follows the expiry, the session and the data. A worked
    example written once into a caption would be a claim about one afternoon
    that quietly goes stale; this one cannot disagree with the picture above
    it, because it is read from the same place.
    """
    ex = dealer.worked_example(rows, today, prior)
    if ex is None:
        return

    verdict = (f"it is labelled <b>{ex['verdict']}</b>"
               if ex["verdict"] != "—"
               else "no verdict is offered")
    reason = (
        f"{_compact(ex['total_volume'])} traded is above the "
        f"{_compact(ex['cut'])} mark — the busiest quarter of the strikes "
        f"shown — so this strike is loud enough to read"
        if ex["high_volume"] else
        f"{_compact(ex['total_volume'])} traded is below the "
        f"{_compact(ex['cut'])} mark — the busiest quarter of the strikes "
        f"shown — so this strike is too quiet to call either way"
    )
    direction = ("<b>more</b> contracts existed at the end of that day than "
                 "the day before, so positions were opened"
                 if ex["delta_oi"] >= 0 else
                 "<b>fewer</b> contracts existed at the end of that day than "
                 "the day before, so positions were closed")

    with st.expander(f"Show me how the {ex['strike']:,.0f} row was calculated"):
        st.markdown(
            f'''<div class="worked">
<p><b>First, the important bit: this row is about YESTERDAY, not today.</b>
The exchange counts up how many contracts exist only once, after the close.
So the newest count available right now was published last night, and it
covers yesterday&rsquo;s trading. Today&rsquo;s trading has not been counted
anywhere yet and will not be until tonight.</p>
<p>Take the <b>{ex["strike"]:,.0f}</b> row &mdash; the biggest change on this
screen.</p>
<table>
  <tr><th></th><th>Calls</th><th>Puts</th><th>Total</th></tr>
  <tr><td>Contracts open, count before last</td>
      <td>{ex["was_call"]:,.0f}</td><td>{ex["was_put"]:,.0f}</td>
      <td>{ex["was_total"]:,.0f}</td></tr>
  <tr><td>Contracts open, latest count</td>
      <td>{ex["now_call"]:,.0f}</td><td>{ex["now_put"]:,.0f}</td>
      <td>{ex["now_total"]:,.0f}</td></tr>
  <tr class="sum"><td>Change</td>
      <td>{ex["now_call"] - ex["was_call"]:+,.0f}</td>
      <td>{ex["now_put"] - ex["was_put"]:+,.0f}</td>
      <td>{ex["delta_oi"]:+,.0f}</td></tr>
</table>
<p>That <b>{ex["delta_oi"]:+,.0f}</b> is the &Delta;OI column.
{direction[0].upper() + direction[1:]}.</p>
<p><b>Now the volume it is measured against.</b> Yesterday
{_compact(ex["total_volume"])} contracts changed hands here
({ex["call_volume"]:,.0f} calls, {ex["put_volume"]:,.0f} puts). Only
{abs(ex["delta_oi"]):,.0f} of those left a lasting position behind &mdash;
the rest was the same contracts being passed around by people who closed out
before the bell.</p>
<p><b>{abs(ex["delta_oi"]):,.0f} &divide; {ex["total_volume"]:,.0f} =
{abs(ex["ratio"]):.0%}</b> of that day&rsquo;s trading stuck. And {reason}, so
{verdict}.</p>
<p class="note"><b>Why today&rsquo;s volume is shown but never divided by.</b>
{_compact(ex["today_volume"])} contracts have traded here today, and none of
it has reached the contract count yet. Dividing yesterday&rsquo;s change by
today&rsquo;s volume would be mixing two different days &mdash; across this
record it produced a change bigger than the entire day&rsquo;s volume on
<b>20% of contracts</b>, which cannot happen, since no more positions can be
opened than were traded. Today&rsquo;s number is there for comparison only.
<b>A live churn reading for today is not possible from this data</b>; it
arrives tonight.</p>
</div>''',
            unsafe_allow_html=True)


@st.cache_data(show_spinner=False, max_entries=8)
def _positioning(_today: pd.DataFrame, _prior: pd.DataFrame, spot: float,
                 snapshot_id: int, expiry: str | None,
                 columns: tuple) -> pd.DataFrame:
    """`expiry` is in the signature to be in the CACHE KEY, not to be used.

    Both frames are underscore-prefixed so Streamlit skips hashing them —
    which left the key as (spot, snapshot_id), so every expiry returned the
    first table computed and the panel never changed when the control did.

    `columns` is in the signature for the same reason — to be in the key, so
    a change to the frame's shape retires the entries built before it.
    """
    return dealer.positioning(_today, _prior, spot)


def _positioning_table(rows: pd.DataFrame, spot: float) -> str:
    """The strike rows as ONE HTML block.

    One block rather than a Streamlit element per row: forty rows would be
    forty layout containers rebuilt on every rerun, and this is a table, not
    forty widgets.
    """
    widest_vol = float(rows["settled_volume"].max()) or 1.0
    widest_today = float(rows["total_volume"].max()) or 1.0
    deltas = rows["delta_oi"].abs()
    widest_delta = (float(deltas.max()) if deltas.notna().any() else 1.0) or 1.0
    nearest = (rows["strike"] - spot).abs().idxmin()

    out = ['<div class="dealer-table">',
           '<div class="dealer-row dealer-head">'
           '<div>Strike</div><div>Traded yesterday</div>'
           '<div>Net &Delta;OI (yesterday)</div>'
           '<div>Traded today</div>'
           '<div class="c">Position verdict</div></div>']

    for i, r in rows.iterrows():
        atm = i == nearest
        strike = f"{r['strike']:,.0f}" + (" ATM" if atm else "")
        settled = float(r["settled_volume"])
        vol_pct = 100.0 * settled / widest_vol
        today_pct = 100.0 * float(r["total_volume"]) / widest_today

        delta = r["delta_oi"]
        if pd.isna(delta):
            delta_bar, delta_text, delta_colour = 0.0, "—", "#41586e"
        else:
            delta_bar = 100.0 * abs(float(delta)) / widest_delta
            delta_text = f"{delta:+,.0f}"
            delta_colour = ("#10b981" if delta > 0
                            else "#ef4444" if delta < 0 else "#64748b")

        bg, fg, edge = _TONE_BADGE.get(r["tone"], _TONE_BADGE["quiet"])
        badge = (f'<span class="dealer-badge" style="background:{bg};'
                 f'color:{fg};border:1px solid {edge};">{r["verdict"]}</span>')

        out.append(
            f'<div class="dealer-row{" atm" if atm else ""}">'
            f'<div class="dealer-strike{" atm" if atm else ""}">{strike}</div>'
            f'<div class="dealer-bar"><div class="dealer-track">'
            f'<div class="dealer-fill" style="width:{vol_pct:.1f}%;'
            f'background:{_VOL_BAR};"></div></div>'
            f'<div class="dealer-val">{_fmt_money(settled)}</div>'
            f'</div>'
            f'<div class="dealer-bar"><div class="dealer-track">'
            f'<div class="dealer-fill" style="width:{delta_bar:.1f}%;'
            f'background:{delta_colour};"></div></div>'
            f'<div class="dealer-val" style="color:{delta_colour};">'
            f'{delta_text}</div></div>'
            f'<div class="dealer-bar today"><div class="dealer-track">'
            f'<div class="dealer-fill" style="width:{today_pct:.1f}%;'
            f'background:{_TODAY_BAR};"></div></div>'
            f'<div class="dealer-val">{_fmt_money(r["total_volume"])}</div>'
            f'</div>'
            f'<div class="c">{badge}</div></div>'
        )
    out.append("</div>")
    return "".join(out)
