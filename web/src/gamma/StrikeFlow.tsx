/**
 * Strike flow — how many contracts traded at one strike, through the day.
 *
 * WHAT THIS IS NOT, FIRST, because the panel it was modelled on looks almost
 * identical and means something stronger. gexstream's "Strike flow" splits
 * every bar four ways: calls bought, puts bought, calls sold, puts sold. That
 * split needs each individual print measured against the quote standing at
 * that instant — this dashboard snapshots the CHAIN once a minute and never
 * records a trade, so the split cannot be derived and is not attempted here.
 *
 * These bars are CONTRACTS TRADED, calls and puts, and the panel says so in
 * words under its own title. That caption is not decoration: the chart is
 * shaped like an order-flow chart, and anyone reading it as one would draw
 * conclusions about who was the aggressor that the data cannot support.
 *
 * TWO AXES, ON PURPOSE. The bars are a count and the line is a price, and they
 * share nothing but a clock. The count sits on the left starting at zero; the
 * index level and the strike's own dashed line share the right. The whole
 * point of drawing them together is to see whether activity arrived while spot
 * was above the strike or below it, which needs both on one time axis and
 * neither rescaled to the other.
 *
 * NOTHING IS DERIVED HERE. The bucketing, the differencing of the running
 * total, the blank-not-zero rule and even the hover's date caption arrive
 * finished from core/flow.py — a browser formatting a timestamp formats it in
 * the VIEWER's timezone, which is how a chart ends up an hour wrong for one
 * reader and right for everyone else (DEBT-030).
 */
import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-dist-min'

import type { StrikeFlowRow } from '../api/types'
import { BG, BRIGHT, CALL, GRID, HOVER, INK, PUT, sessionRange } from './chart'

export interface StrikeFlowProps {
  rows: StrikeFlowRow[]
  strike: number
  /** The server's sentence about what these numbers are. Rendered, not
   *  paraphrased — see the header of this file. */
  basis: string
  height: string
}

/** The index line. White rather than a third hue: it is not a third series
 *  competing with calls and puts, it is the context they happened in. */
const SPOT = '#e8eef6'

export function StrikeFlow({ rows, strike, basis, height }: StrikeFlowProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    const at = rows.map((r) => r.bucket)
    // Only the calendar date and the UTC offset are read out of it, so any
    // bucket in the response answers for the whole session.
    const band = sessionRange(rows[0]?.bucket)
    const calls = rows.map((r) => r.call_volume)
    const puts = rows.map((r) => r.put_volume)

    // ONE BOX, WRITTEN ONCE. This was `hovermode: 'x unified'` with the same
    // full template on both bar traces, and unified does exactly what it says:
    // it collects EVERY trace's hover at that x into one box. So the date, the
    // time, the counts, the spot and the bucket width all appeared twice, under
    // a third heading Plotly wrote itself from the x value — three copies of
    // one timestamp in a box six lines longer than it needed to be.
    //
    // The fix is not to trim the template but to stop asking two traces for
    // the same answer. `hovermode: 'x'` with the template on the CALLS trace
    // alone gives one box; the puts trace and the spot line skip their hover
    // entirely, because everything they would say is already in it. Hovering
    // over a put bar still works — 'x' matches on the x position, not on which
    // bar the cursor is over.
    //
    // `customdata` carries finished strings so the template does no arithmetic
    // and no formatting, and a null stays "—" rather than becoming 0.
    const blank = (v: number | null) => (v === null ? '—' : v.toLocaleString())
    const detail = rows.map((r) => [
      // The width joins the timestamp rather than taking a line of its own: it
      // matters (it CHANGES at 10:00 and 15:30, so two bars of different
      // heights are not always two different volumes) but it is a property of
      // the moment, not a fourth measurement.
      `${r.label}  ·  ${r.bucket_secs === 60 ? '1 min' : '5 min'}`,
      blank(r.call_volume),
      blank(r.put_volume),
      blank(r.total),
      r.spot.toLocaleString(undefined, { minimumFractionDigits: 2,
                                         maximumFractionDigits: 2 }),
    ])
    // The words dropped with the repetition: "traded" appeared on both count
    // lines and is what the whole panel is about, so it said nothing either
    // time. The caption under the chart carries that claim instead.
    const template =
      '<b>%{customdata[0]}</b><br>' +
      `<span style="color:${CALL}">Calls</span>  %{customdata[1]}<br>` +
      `<span style="color:${PUT}">Puts</span>  %{customdata[2]}<br>` +
      'Total  %{customdata[3]}<br>' +
      'Spot  %{customdata[4]}' +
      '<extra></extra>'

    // BOTH AXES ARE SET HERE RATHER THAN LEFT TO AUTORANGE, because
    // autorange gives each series the whole cell and the two then sit on top
    // of one another: the bars filled the full height, the price line ran
    // through the middle of them, and neither could be read.
    //
    // The counts get the LOWER HALF and the price line the upper, which is
    // how the reference panel reads — activity along the floor, the index
    // above it. Doubling the peak is what buys that band; it is presentation,
    // the same trick MiniPanel uses for its volume shade.
    let peak = 0
    for (const r of rows) peak = Math.max(peak, r.total ?? 0)
    const countRange: [number, number] = [0, peak > 0 ? peak * 2.15 : 1]

    // THE STRIKE HAS TO BE INSIDE THE PRICE RANGE, and autorange had no idea
    // it existed: a shape is not data, so Plotly fitted the axis to the spot
    // line alone and drew the strike line hard against the bottom edge — or
    // off the chart entirely on a day that never traded near it. The whole
    // point of the panel is seeing which side of the strike spot was on, so
    // the strike is part of what the axis has to contain.
    const prices = rows.map((r) => r.spot).concat(strike)
    const low = Math.min(...prices)
    const high = Math.max(...prices)
    const pad = (high - low) * 0.12 || 1
    const priceRange: [number, number] = [low - pad, high + pad]

    const bars = (y: (number | null)[], color: string, name: string,
                  hover: boolean) => ({
      type: 'bar' as const, x: at, y, name, showlegend: false,
      marker: { color },
      ...(hover
        ? { customdata: detail, hovertemplate: template }
        : { hoverinfo: 'skip' as const }),
    })

    void Plotly.react(node, [
      bars(calls, CALL, 'Calls', true),
      bars(puts, PUT, 'Puts', false),
      {
        type: 'scatter' as const, mode: 'lines' as const,
        x: at, y: rows.map((r) => r.spot), yaxis: 'y2',
        line: { color: SPOT, width: 1.5 }, showlegend: false,
        // The bars' hover already reports spot, so this one would be a second
        // box saying the same number in a different place.
        hoverinfo: 'skip' as const,
      },
    ], {
      // STACKED, NOT GROUPED. The pair is a total split two ways, and side by
      // side halves every bar's width to show a relationship the eye then has
      // to add up anyway.
      barmode: 'stack',
      bargap: 0.15,
      paper_bgcolor: BG, plot_bgcolor: BG,
      margin: { l: 52, r: 58, t: 26, b: 34 },
      hovermode: 'x' as const,
      // Shared with every other panel on the tab — see chart.ts HOVER for why
      // the text colour has to be stated at all.
      hoverlabel: HOVER,
      xaxis: {
        gridcolor: GRID, linecolor: GRID,
        tickfont: { color: INK, size: 9 },
        // The gaps between sessions are real time with no bars in it. Left as
        // a date axis they would open a wide empty band overnight; `category`
        // would lose the ordering. `rangebreaks` is not used because the
        // frame is one session — if this ever serves several, it will need
        // them, and it will need them in market time.
        type: 'date' as const,
        // THE WHOLE SESSION, ALWAYS — 09:30 to 16:00, matching the two panels
        // below. See chart.ts sessionRange. It matters more here than there:
        // these are BARS, and autorange widens them to fill the axis, so a
        // handful of morning buckets draw as fat blocks that thin out through
        // the day. The bar width stops being a quantity you can read.
        ...(band ? { range: band, autorange: false as const } : {}),
      },
      yaxis: {
        // FROM ZERO. A count has nothing below it, and an axis that floats
        // makes a busy afternoon look like a quiet one that started high.
        range: countRange,
        gridcolor: GRID, linecolor: GRID, zeroline: false,
        tickfont: { color: INK, size: 9 },
        title: { text: 'contracts', font: { color: INK, size: 9 } },
      },
      yaxis2: {
        overlaying: 'y' as const, side: 'right' as const,
        range: priceRange,
        showgrid: false, zeroline: false,
        tickfont: { color: INK, size: 9 },
      },
      shapes: [{
        // The strike itself, on the PRICE axis. Its whole job is to let the
        // reader see which side of it spot was on while the bars were filling.
        type: 'line' as const, xref: 'paper' as const, yref: 'y2' as const,
        x0: 0, x1: 1, y0: strike, y1: strike,
        line: { color: '#8fa6bd', width: 1, dash: 'dash' as const },
      }],
      annotations: [
        { text: `Strike flow — ${strike.toLocaleString()}`,
          x: 0, y: 1, xref: 'paper' as const, yref: 'paper' as const,
          xanchor: 'left' as const, yanchor: 'bottom' as const, yshift: 6,
          showarrow: false, font: { color: BRIGHT, size: 11 } },
        { text: strike.toLocaleString(),
          x: 1, y: strike, xref: 'paper' as const, yref: 'y2' as const,
          xanchor: 'right' as const, yanchor: 'bottom' as const, yshift: 2,
          showarrow: false, font: { color: '#8fa6bd', size: 9 } },
      ],
    }, { displayModeBar: false, responsive: true, scrollZoom: false })
  }, [rows, strike, height])

  return (
    <div>
      <div ref={host} style={{ width: '100%', height }} />
      {/* THE CAPTION IS PART OF THE CHART, not a footnote. It is the only
          thing standing between a panel shaped like an order-flow chart and a
          reader who takes it for one. It is the SERVER's sentence, rendered
          rather than paraphrased, so the claim and the data cannot drift. */}
      <p className="px-2 pb-1 text-[10px] leading-[14px]"
         style={{ color: 'var(--text-3)' }}>
        {basis}
      </p>
    </div>
  )
}
