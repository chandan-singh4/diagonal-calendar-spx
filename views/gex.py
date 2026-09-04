"""Gamma Exposure — where dealer hedging sits, strike by strike.

THREE PANELS ON ONE STRIKE AXIS, deliberately. Gamma exposure, open interest
and volume answer three different questions about the same strike, and the
answer is usually in how they disagree: a strike heavy with open interest but
carrying no volume is old positioning, while one heavy with volume and light
with open interest is today's argument. Stacked on a shared axis they are read
in one glance; on three separate charts they are three lookups and a memory
test.

NO NEW QUERY. Everything drawn here comes from `ctx.chain_df`, the chain
app.py already loaded through its memo for every other tab. A view that
reached for its own read would re-query SQLite on each rerun with no visible
symptom (ADR-032) — and there is genuinely nothing extra to fetch, because
gamma and open interest have been collected on every row since June.

WHAT THIS TAB IS HONEST ABOUT, in the caption and not just here:

  * The dealer assumption (long calls, short puts) is a CONVENTION. See
    core/gex.py. Every number on this page inherits it.
  * The figures are computed to OUR definitions. Vendors publish "GEX Ratio"
    and "GEX Sentiment" without publishing the arithmetic, so these will not
    match theirs, and a matching-looking number that was computed differently
    is worse than an obviously different one.
  * The chain is truncated at +/-300 points. Measured on the live record,
    gamma x OI at that edge is 0.2% of its at-the-money value, so the cost to
    GEX is negligible — but it is a real edge and the caption says so.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from core import gex
from views.context import ViewContext

# Panel background and grid, matching the other tabs' chart cards.
_BG = "#0c1421"
_GRID = "#0c1928"
_INK = "#6d8fa8"
_BRIGHT = "#dde6f1"

# Call side and put side. Green/red are the project's existing pair
# (core/market.GREEN / RED) so a green bar means the same thing here as it
# does everywhere else on the dashboard.
_CALL = "#10d4a3"
_PUT = "#f05252"
_NET_POS = "#10d4a3"
_NET_NEG = "#f05252"

# The same two colours at 10% opacity, for the profile areas behind the bars.
# Written out rather than derived from the hex above: a string-munged colour is
# a colour nobody can grep for.
_CALL_FILL = "rgba(16,212,163,0.10)"
_PUT_FILL = "rgba(240,82,82,0.10)"

# How many strikes the window offers. SPX lists strikes 5 points apart near
# the money, so 40 is roughly +/-100 points — tight enough to read individual
# bars, which is the whole point of narrowing.
_STRIKE_COUNTS = [20, 40, 60, 100, 0]      # 0 = every strike collected

_VIEWS = ["Net Gamma", "Abs Gamma", "Call Gamma", "Put Gamma"]

# Which column each view draws, and how its bars are coloured. Net is the only
# one that can go negative, and the only one coloured per bar.
_VIEW_COLUMN = {
    "Net Gamma": "net_gex",
    "Abs Gamma": "abs_gex",
    "Call Gamma": "call_gex",
    "Put Gamma": "put_gex",
}


def _fmt_gex(value: float | None) -> str:
    """A dollar-gamma figure at a readable magnitude, or an em dash.

    An em dash rather than 0 or "N/A": absent and zero are different states
    and the project's rule is that a missing number shows blank.
    """
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    mag = abs(value)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if mag >= cutoff:
            return f"{sign}${mag / cutoff:,.1f}{suffix}"
    return f"{sign}${mag:,.0f}"


def _metric(label: str, value: str, colour: str = _BRIGHT) -> str:
    return (
        f'<div style="flex:1;min-width:110px;">'
        f'<div style="color:#2f4459;font-size:0.72em;text-transform:uppercase;'
        f'letter-spacing:0.06em;">{label}</div>'
        f'<div style="color:{colour};font-size:1.15em;font-weight:600;'
        f'line-height:1.5;">{value}</div></div>'
    )


def _bar(x, y, name, colour, row, fig, hover):
    fig.add_trace(
        go.Bar(x=x, y=y, name=name, marker=dict(color=colour, line=dict(width=0)),
               hovertemplate=hover, showlegend=False),
        row=row, col=1,
    )


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
            "That is a normal gap for some snapshots rather than a fault — "
            "pick a different one from the header."
        )
        return

    # ── Controls ─────────────────────────────────────────────────────────────
    expiries = sorted(chain["expiry"].dropna().unique())
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        choice = st.selectbox(
            "Expiry", ["All expiries", *expiries], key="gex_expiry",
            help="All expiries is the whole-chain figure most GEX commentary "
                 "refers to. A single expiry shows what that one contract "
                 "contributes — the third Friday appears twice because SPX "
                 "lists two of them.",
        )
    with c2:
        count = st.selectbox(
            "Strikes", _STRIKE_COUNTS, index=1, key="gex_strikes",
            format_func=lambda n: "All" if n == 0 else f"{n} strikes",
        )
    with c3:
        view = st.selectbox("View", _VIEWS, key="gex_view")

    expiry = None if choice == "All expiries" else choice
    per_strike = gex.by_strike(chain, ctx.spx_price, expiry=expiry)
    if per_strike.empty:
        st.info("No strike in this selection carries both gamma and a strike price.")
        return

    shown = gex.window(per_strike, ctx.spx_price, count)

    # The headline figures describe the SELECTION, not the window. Narrowing
    # the axis is a display choice, and letting it silently change "Net GEX"
    # would make the number mean something different at each zoom level.
    totals = gex.summary(per_strike)

    # ── Headline figures ─────────────────────────────────────────────────────
    net = totals["net_gex"]
    net_colour = _BRIGHT if net is None else (_NET_POS if net >= 0 else _NET_NEG)
    ratio = totals["ratio"]
    sentiment = totals["sentiment"]
    flip = totals["flip_strike"]
    peak = totals["peak_strike"]

    st.markdown(
        '<div style="display:flex;gap:18px;flex-wrap:wrap;padding:10px 14px;'
        'background:#0c1421;border:1px solid #16283d;border-radius:8px;'
        'margin-bottom:10px;">'
        + _metric("Net GEX", _fmt_gex(net), net_colour)
        + _metric("Call GEX", _fmt_gex(totals["call_gex"]), _CALL)
        + _metric("Put GEX", _fmt_gex(totals["put_gex"]), _PUT)
        + _metric("Call / Put", "—" if ratio is None else f"{ratio:,.1f}x")
        + _metric("Sentiment",
                  "—" if sentiment is None else f"{sentiment:,.0f}% call")
        + _metric("Peak strike",
                  "—" if peak is None else f"{peak:,.0f} ({totals['peak_side']})")
        + _metric("Gamma flip", "—" if flip is None else f"{flip:,.0f}")
        + "</div>",
        unsafe_allow_html=True,
    )

    # ── The three panels ─────────────────────────────────────────────────────
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.045,
        row_heights=[0.46, 0.27, 0.27],
        subplot_titles=(view, "Open Interest", "Volume"),
    )

    column = _VIEW_COLUMN[view]
    values = shown[column]

    if view == "Net Gamma":
        colours = [_NET_POS if v >= 0 else _NET_NEG for v in values]
    elif view == "Put Gamma":
        colours = _PUT
    elif view == "Call Gamma":
        colours = _CALL
    else:
        colours = "#4c8fd1"

    _bar(shown["strike"], values, view, colours, 1, fig,
         "Strike %{x:,.0f}<br>" + view + " %{y:$,.0f}<extra></extra>")

    # The call and put profiles behind the bars. Same axis, NOT a second one:
    # they are the same quantity in the same unit, and putting them on their
    # own scale would let a small side look as tall as a large one.
    for col, colour, fill, name in (("call_gex", _CALL, _CALL_FILL, "Call"),
                                    ("put_gex", _PUT, _PUT_FILL, "Put")):
        fig.add_trace(
            go.Scatter(
                x=shown["strike"], y=shown[col], name=name, mode="lines",
                line=dict(color=colour, width=1), fill="tozeroy",
                fillcolor=fill, hoverinfo="skip", showlegend=False,
            ),
            row=1, col=1,
        )

    _bar(shown["strike"], shown["call_oi"], "Calls", _CALL, 2, fig,
         "Strike %{x:,.0f}<br>Call OI %{y:,.0f}<extra></extra>")
    _bar(shown["strike"], -shown["put_oi"], "Puts", _PUT, 2, fig,
         "Strike %{x:,.0f}<br>Put OI %{customdata:,.0f}<extra></extra>")
    fig.data[-1].customdata = shown["put_oi"]

    _bar(shown["strike"], shown["call_volume"], "Calls", _CALL, 3, fig,
         "Strike %{x:,.0f}<br>Call volume %{y:,.0f}<extra></extra>")
    _bar(shown["strike"], -shown["put_volume"], "Puts", _PUT, 3, fig,
         "Strike %{x:,.0f}<br>Put volume %{customdata:,.0f}<extra></extra>")
    fig.data[-1].customdata = shown["put_volume"]

    # Spot, on all three panels. Without it the strikes are just numbers.
    for row in (1, 2, 3):
        fig.add_vline(
            x=ctx.spx_price, line=dict(color="#8fa9c4", width=1, dash="dot"),
            row=row, col=1,
        )
    fig.add_annotation(
        x=ctx.spx_price, y=1.0, yref="y domain", row=1, col=1,
        text=f"{ctx.spx_price:,.2f}", showarrow=False, yanchor="bottom",
        font=dict(color=_BRIGHT, size=10),
        bgcolor="#16283d", borderpad=3,
    )

    # The gamma flip, where there is one. Dashed and labelled rather than
    # solid: it is the one line here with a directional claim attached, and it
    # should not look more certain than the bars it sits over.
    if flip is not None and shown["strike"].min() <= flip <= shown["strike"].max():
        fig.add_vline(
            x=flip, line=dict(color="#e8b64c", width=1, dash="dash"),
            row=1, col=1,
        )

    fig.update_xaxes(gridcolor=_GRID, title_text="", row=1, col=1)
    fig.update_xaxes(gridcolor=_GRID, title_text="", row=2, col=1)
    fig.update_xaxes(gridcolor=_GRID, title_text="Strike", row=3, col=1)
    fig.update_yaxes(gridcolor=_GRID, title_text="$ per 1% move",
                     row=1, col=1, automargin=False)
    fig.update_yaxes(gridcolor=_GRID, title_text="Open interest",
                     row=2, col=1, automargin=False)
    fig.update_yaxes(gridcolor=_GRID, title_text="Volume",
                     row=3, col=1, automargin=False)

    fig.update_layout(
        height=760,
        margin=dict(l=70, r=30, t=44, b=30),
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        font=dict(family="Inter", color=_INK, size=11),
        bargap=0.15,
        barmode="relative",
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#111c2e", bordercolor="#1a2d45",
                        font=dict(color=_BRIGHT, size=12)),
        showlegend=False,
    )
    for note in fig.layout.annotations[:3]:
        note.font.update(color=_INK, size=12)

    with st.container(key="chartcard_gex"):
        st.plotly_chart(fig, use_container_width=True)

    _draw_caption(totals, expiry, per_strike)


def _draw_caption(totals: dict, expiry: str | None,
                  per_strike: pd.DataFrame) -> None:
    """What the reader has to know to not over-trust the picture.

    Kept as prose under the chart rather than in a tooltip because every one
    of these is a reason a number here could be wrong, and a caveat behind a
    hover is a caveat nobody reads.
    """
    flip = totals["flip_strike"]
    lo, hi = per_strike["strike"].min(), per_strike["strike"].max()

    lines = [
        "**Puts are drawn downward** in the lower two panels so the two sides "
        "can be compared at a glance; the hover shows the true positive count.",
        "**Gamma exposure assumes dealers are long calls and short puts.** "
        "That is the standard convention and it is an assumption, not data — "
        "nobody publishes dealer inventory. Every figure above inherits it.",
        f"**Computed to our own definitions** — sentiment is the call share of "
        f"total exposure and the ratio is call ÷ put gamma, both in dollars "
        f"per 1% move. Vendors publish figures with these names without "
        f"publishing the arithmetic, so these will not match theirs.",
        f"**The chain is recorded between {lo:,.0f} and {hi:,.0f}** — the "
        f"collector keeps ±300 points around spot. Gamma at that edge measured "
        f"0.2% of its at-the-money value, so the cost here is small, but "
        f"strikes beyond it are absent rather than empty.",
    ]
    if flip is not None:
        lines.insert(1, (
            f"**The dashed gold line at {flip:,.0f} is the gamma flip** — where "
            f"cumulative net exposure crosses zero. Below it dealers are said "
            f"to amplify moves and above it to damp them. Treat that as the "
            f"hypothesis it is; the record now holds enough history to test it."
        ))
    if expiry is None:
        lines.append(
            "**All expiries are summed.** Gamma concentrates in the nearest "
            "ones, so the near-dated contracts dominate this view even though "
            "every expiry collected is included."
        )

    st.caption("  \n".join(f"· {line}" for line in lines))
