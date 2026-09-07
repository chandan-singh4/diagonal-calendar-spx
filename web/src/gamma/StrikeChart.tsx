/**
 * The three stacked strike panels — exposure, volume, open interest.
 *
 * THE COUNTERPART OF `views/gex.py:_strike_figure`, and the same layout for
 * the same reason: volume and open interest share the exposure panel's x-axis
 * so a bar in one can be read straight down against the same strike in the
 * others, with no scrolling and no remembering where 7,750 sat. Volume sits
 * above open interest because it is the one that moves while you watch.
 *
 * BRIEFLY A PRICE LADDER, AND BACK AGAIN (2026-09-06). These panels were
 * transposed — strikes down the y-axis, bars running left and right — to match
 * the gexstream.com chart Chandan had asked for. Seeing it, he asked for the
 * original shape back: rotated, the chart became a tall narrow column to be
 * scrolled rather than a wide block to be read at a glance, and the width of
 * the screen is the thing worth spending on a hundred strikes. The REASON he
 * wanted that chart — the wicks — was kept, and works just as well this way
 * up. Recorded because the obvious future edit is to rotate it again, and the
 * reason not to is not visible from the code.
 *
 * THE WICKS. The thin pale line behind each bar is that strike's HIGHEST and
 * LOWEST exposure so far this session. gexstream's own documentation defines
 * them as the "per-strike session high / low for GEX, DEX and vGEX, tracked
 * since the 4:00 PM ET reset", and it is worth being exact about that because
 * the obvious reading is the wrong one: a wick is NOT the change since the
 * open. A bar is one instant, and on its own it cannot say whether that
 * exposure has stood there all day or arrived in the last ten minutes. The
 * wick answers that on the same bar, which is why it replaces a second chart.
 *
 * NOTHING HERE COMPUTES AN EXPOSURE. Every value is a column the server sent;
 * the tick positions and their text come from `core.format.money_ticks` in the
 * response, so this axis reads "12.2B" exactly as the Streamlit one does
 * rather than Plotly's SI "12.2G". The only arithmetic below is negating the
 * put series to mirror it under the axis, which is a layout choice and is
 * exactly what the page does with its `put_sign`.
 *
 * PLOTLY IS LOADED FROM `plotly.js-dist-min`, which is 4 MB minified against a
 * Scanner bundle of 235 KB. That is a real cost and it is stated in
 * web/README.md rather than absorbed quietly; `plotly.js-basic-dist-min` has
 * bar and scatter and would be about a quarter of the size, which is the
 * obvious next move if the bundle starts to matter.
 */
import Plotly from 'plotly.js-dist-min'
import { useEffect, useRef } from 'react'

import type { AxisTicks, SessionRangeRow, StrikeRow } from '../api/types'
import {
  BG, BRIGHT, CALL, CALL_VOL_EDGE, CALL_VOL_FILL, CALL_WICK, GRID, HOVER, INK,
  NET_WICK,
  type PanelSpec, PUT, PUT_VOL_EDGE, PUT_VOL_FILL, PUT_WICK, wickTrace,
} from './chart'

export type { PanelSpec }

function column(rows: StrikeRow[], name: string): (number | null)[] {
  return rows.map((row) => (name in row ? row[name] : null))
}

function negated(values: (number | null)[]): (number | null)[] {
  return values.map((v) => (v === null ? null : -v))
}

export interface StrikeChartProps {
  /** The gamma frame. Always present whatever view is selected — it carries
   *  the volume and open-interest columns the lower two panels need. */
  gammaRows: StrikeRow[]
  /** The selected measure's frame, when it is not gamma. */
  panelRows: StrikeRow[]
  /** Each strike's session high and low, for the wicks. EMPTY IS FINE and is
   *  the normal state for a moment after load: the bars are one request and
   *  the session range is another, roughly a hundred times the rows, so the
   *  panels draw first and the wicks appear when they arrive. */
  ranges: SessionRangeRow[]
  spec: PanelSpec
  ticks: AxisTicks
  spot: number
  /** Mirrored puts the volume and OI panels below, independently of panel 1. */
  stack: boolean
  /** The tab's volume-shade toggle. The dedicated volume PANEL below is not
   *  affected — this is only the translucent backdrop behind the bars. */
  showVolume: boolean
}

export function StrikeChart({
  gammaRows,
  panelRows,
  ranges,
  spec,
  ticks,
  spot,
  stack,
  showVolume,
}: StrikeChartProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    const strikes = gammaRows.map((row) => row.strike)
    // NARROWED TO THE STRIKES THE PANELS BELOW DRAW. The second-order frames
    // drop a contract with no IV while the gamma frame drops one with no
    // gamma, so the two lists can differ by a strike or two — and a top panel
    // one bar wider than the volume panel under it silently misaligns every
    // reading made by looking straight down.
    const shown = new Set(strikes)
    const panel = panelRows.filter((row) => shown.has(row.strike))
    const panelStrikes = panel.map((row) => row.strike)
    // The wicks come from a different request and a different scope of the
    // record — the whole session rather than one snapshot — so a strike can
    // appear in one and not the other. Narrowed for the same reason.
    const wicks = ranges.filter((row) => shown.has(row.strike))

    const bar = (
      x: number[],
      y: (number | null)[],
      colour: string | string[],
      axis: string,
      hover: string,
      custom?: (number | null)[],
    ): Partial<Plotly.PlotData> => ({
      type: 'bar',
      x,
      y,
      marker: { color: colour },
      xaxis: axis === 'y' ? 'x' : axis === 'y3' ? 'x2' : 'x3',
      yaxis: axis,
      customdata: custom ?? undefined,
      hovertemplate: hover,
      showlegend: false,
    })

    const traces: Partial<Plotly.PlotData>[] = []

    // ── Panel 1 background: the day's volume as translucent fills ───────────
    // Drawn FIRST so the bars sit on top, and on a secondary axis because
    // contracts traded and exposure are not the same unit.
    // The tab's toggle reaches here too. The detail view already draws volume
    // as its own panel below, so the shade behind the bars is the redundant
    // copy -- if anywhere earns the option to switch it off, it is here.
    for (const [col, fill, edge] of showVolume ? [
      ['put_volume', PUT_VOL_FILL, PUT_VOL_EDGE],
      ['call_volume', CALL_VOL_FILL, CALL_VOL_EDGE],
    ] as const : []) {
      traces.push({
        type: 'scatter',
        mode: 'lines',
        x: strikes,
        y: column(gammaRows, col),
        line: { width: 1.2, color: edge },
        fill: 'tozeroy',
        fillcolor: fill,
        hoverinfo: 'skip',
        showlegend: false,
        xaxis: 'x',
        yaxis: 'y2',
      })
    }

    // ── Panel 1: the wicks, UNDER the bars ─────────────────────────────────
    // Under, because the bar is the reading and the wick is its context; drawn
    // over the top the pale line would cut every bar in half lengthways.
    if (wicks.length > 0 && spec.wicks) {
      // A NET PANEL DRAWS ONE SIGNED BAR, so it gets ONE wick, from the
      // server's own net range. Drawing the call and put wicks behind a net
      // bar would put two lines behind one bar at heights neither of them
      // reaches — the call side alone can tower over a net that has nearly
      // cancelled out.
      traces.push(...(spec.netColumn
        ? [wickTrace(wicks, 'net_low', 'net_high', NET_WICK, 1)]
        : [
            wickTrace(wicks, 'call_low', 'call_high', CALL_WICK, 1),
            wickTrace(wicks, 'put_low', 'put_high', PUT_WICK,
                      spec.mirror ? -1 : 1),
          ]))
    }

    // ── Panel 1 foreground: whichever view is selected ──────────────────────
    if (spec.netColumn) {
      const net = column(panel, spec.netColumn)
      traces.push(
        bar(
          panelStrikes,
          net,
          net.map((v) => ((v ?? 0) >= 0 ? CALL : PUT)),
          'y',
          `Strike %{x:,.0f}<br>${spec.tag} %{y:,.0f}<extra></extra>`,
        ),
      )
    } else {
      const put = column(panel, spec.putColumn)
      traces.push(
        bar(
          panelStrikes,
          column(panel, spec.callColumn),
          CALL,
          'y',
          `Strike %{x:,.0f}<br>Call ${spec.tag} %{y:,.0f}<extra></extra>`,
        ),
        bar(
          panelStrikes,
          spec.mirror ? negated(put) : put,
          PUT,
          'y',
          `Strike %{x:,.0f}<br>Put ${spec.tag} %{customdata:,.0f}<extra></extra>`,
          put,
        ),
      )
    }

    // ── Panels 2 and 3 ──────────────────────────────────────────────────────
    // barmode is "relative", so two positive bars stack and a positive and a
    // negative one straddle the axis. The put side flipping sign is therefore
    // the WHOLE difference between the two layouts — the hover reports the
    // put's own figure, unsigned, either way, which is what `customdata` is.
    const sign = stack ? 1 : -1
    const flip = (values: (number | null)[]) =>
      values.map((v) => (v === null ? null : sign * v))

    for (const [axis, callCol, putCol, word] of [
      ['y3', 'call_volume', 'put_volume', 'volume'],
      ['y4', 'call_oi', 'put_oi', 'OI'],
    ] as const) {
      const put = column(gammaRows, putCol)
      traces.push(
        bar(
          strikes,
          column(gammaRows, callCol),
          CALL,
          axis,
          `Strike %{x:,.0f}<br>Call ${word} %{y:,.0f}<extra></extra>`,
        ),
        bar(
          strikes,
          flip(put),
          PUT,
          axis,
          `Strike %{x:,.0f}<br>Put ${word} %{customdata:,.0f}<extra></extra>`,
          put,
        ),
      )
    }

    // The spot line through all three panels. NO GAMMA-FLIP LINE: it was
    // removed at Chandan's request on 2026-09-07, the day after it was made
    // whole-chain. The level is the WHOLE BOARD's while these bars are one
    // expiry's, so a vertical rule drawn across them invites exactly the
    // reading it cannot support — that the green turns red HERE, in this
    // panel. The number is still on the headline strip, where it is labelled
    // "(chain)" and has no bars beside it to be misread against.
    const shapes: Partial<Plotly.Shape>[] = ['y', 'y3', 'y4'].map((axis) => ({
      type: 'line',
      x0: spot,
      x1: spot,
      yref: `${axis} domain` as Plotly.Shape['yref'],
      y0: 0,
      y1: 1,
      xref: axis === 'y' ? 'x' : axis === 'y3' ? 'x2' : 'x3',
      line: { color: '#8fa9c4', width: 1, dash: 'dot' },
    }))

    // Ticks arrive placed and labelled. Empty arrays mean the server had
    // nothing to place, and Plotly is left to decide — see money_ticks.
    const moneyAxis =
      ticks.tickvals.length > 0
        ? { tickmode: 'array' as const, tickvals: ticks.tickvals, ticktext: ticks.ticktext }
        : {}

    const axisBase = {
      gridcolor: GRID,
      zerolinecolor: '#3c5570',
      zerolinewidth: 1,
      tickfont: { color: INK, size: 10 },
      linecolor: GRID,
    }

    const layout: Partial<Plotly.Layout> = {
      barmode: 'relative',
      bargap: 0.15,
      paper_bgcolor: BG,
      plot_bgcolor: BG,
      font: { color: INK, size: 11 },
      margin: { l: 62, r: 46, t: 34, b: 34 },
      hovermode: 'closest',
      // The same box as every other panel on the tab. This one had no
      // hoverlabel at all, so it took Plotly's default — which colours the
      // text from the TRACE, and rendered the body in the dim grey of the
      // axis ticks on every panel but the green one.
      hoverlabel: HOVER,
      showlegend: false,
      // Three rows sharing one strike axis, in the page's proportions.
      xaxis: { ...axisBase, domain: [0, 1], anchor: 'y', matches: 'x3', showticklabels: false },
      yaxis: { ...axisBase, ...moneyAxis, domain: [0.54, 1] },
      yaxis2: {
        overlaying: 'y',
        side: 'right',
        showgrid: false,
        zeroline: false,
        tickfont: { color: 'rgba(109,143,168,.6)', size: 9 },
      },
      xaxis2: { ...axisBase, domain: [0, 1], anchor: 'y3', matches: 'x3', showticklabels: false },
      yaxis3: { ...axisBase, domain: [0.28, 0.48] },
      xaxis3: { ...axisBase, domain: [0, 1], anchor: 'y4', title: { text: 'Strike' } },
      yaxis4: { ...axisBase, domain: [0, 0.22] },
      annotations: [
        { text: spec.title, x: 0, y: 1.0, xref: 'paper', yref: 'paper',
          xanchor: 'left', showarrow: false, font: { color: BRIGHT, size: 12 } },
        { text: 'Volume', x: 0, y: 0.505, xref: 'paper', yref: 'paper',
          xanchor: 'left', showarrow: false, font: { color: BRIGHT, size: 12 } },
        { text: 'Open Interest', x: 0, y: 0.245, xref: 'paper', yref: 'paper',
          xanchor: 'left', showarrow: false, font: { color: BRIGHT, size: 12 } },
        // Anchored just INSIDE the panel, not above it: at the top edge this
        // label lands on the panel title and the two overprint.
        { text: spot.toLocaleString(undefined, { minimumFractionDigits: 2,
                                                 maximumFractionDigits: 2 }),
          x: spot, y: 0.98, xref: 'x', yref: 'y domain', yanchor: 'top',
          showarrow: false, font: { color: BRIGHT, size: 10 },
          bgcolor: '#16283d', borderpad: 3 },
      ],
      shapes,
    }

    void Plotly.react(node, traces, layout, {
      displayModeBar: false,
      responsive: true,
    })
  }, [gammaRows, panelRows, ranges, spec, ticks, spot, stack, showVolume])

  // Purge on unmount. Plotly attaches listeners and a WebGL-free but still
  // stateful graph div to the node; React removing the element does not tell
  // Plotly to let go of it.
  useEffect(() => {
    const node = host.current
    return () => {
      if (node) Plotly.purge(node)
    }
  }, [])

  return <div ref={host} style={{ width: '100%', height: '46rem' }} />
}
