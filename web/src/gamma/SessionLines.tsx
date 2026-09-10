/**
 * "Through the session" — a handful of strikes, one line each, across the day.
 *
 * ONE COMPONENT FOR BOTH PANELS. Net volume and net gamma are the same
 * picture: pick a few strikes out of a board of a hundred, draw one line per
 * strike through the session, and put a zero line through the middle because
 * the sign is the signal. Only the units and the palette differ. Written twice
 * they would drift — a different hover, a different zero line, a legend in a
 * different corner — and the two sit one above the other, where any difference
 * reads as a difference in the data.
 *
 * NOTHING IS DERIVED HERE. Which strikes are drawn, what "net" means for each
 * measure, and the dollar scaling all arrive finished from core/timelines.py.
 * The two panels rank their strikes by DIFFERENT rules on purpose (loudest
 * moment for volume, position right now for gamma) and neither rule is
 * expressible here — this file could not tell you which one it is showing.
 *
 * A COLOUR PER STRIKE, NOT PER SIDE. Everywhere else on this tab green is
 * calls and red is puts. Here a line IS one strike and its sign already says
 * which side is winning, so reusing the call/put pair would claim a meaning
 * the lines do not have. The palette is deliberately unrelated to it.
 */
import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-dist-min'

import { BG, GRID, HOVER, INK, sessionRange } from './chart'

/** Eight hues that stay apart on a dark ground. Ordered so neighbours in the
 *  legend are not neighbours on the colour wheel — adjacent strikes are the
 *  ones most often confused for each other. */
const LINES = ['#54a0ff', '#10d4a3', '#e8b64c', '#a374e0',
               '#f78fb3', '#4dd0c7', '#f0885a', '#8fa6bd']

export interface SessionLinesProps {
  rows: { timestamp: string; strike: number }[]
  /** The column each line is drawn from — `net_volume` or `net_gex`. Named
   *  rather than pre-extracted so the caller passes the server's rows through
   *  untouched. */
  valueKey: string
  /** Left-axis label, and the word the hover uses. */
  units: string
  /** Formats one reading for the hover. Passed in because a count and a
   *  dollar figure are not written the same way, and neither is this file's
   *  decision to make. */
  format: (v: number) => string
  /** The server's sentence about what these numbers are. Rendered verbatim. */
  basis: string
  height: string
}

export function SessionLines({ rows, valueKey, units, format, basis,
                              height }: SessionLinesProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    // GROUPED BY WALKING, NOT BY SORTING. The server returns the rows already
    // ordered by strike then time, so a Map keyed on strike preserves both the
    // grouping and the order within each group in one pass. Sorting here would
    // be a second opinion about an order that already arrived correct.
    const byStrike = new Map<number, { x: string[]; y: number[] }>()
    for (const row of rows) {
      let line = byStrike.get(row.strike)
      if (!line) {
        line = { x: [], y: [] }
        byStrike.set(row.strike, line)
      }
      line.x.push(row.timestamp)
      line.y.push((row as Record<string, unknown>)[valueKey] as number)
    }

    // LEGEND ORDER IS PRICE ORDER, not the ranking the server used. The
    // ranking is about which strikes deserve a line; once they have one, a
    // reader looking for 7,750 wants it where 7,750 belongs.
    const strikes = [...byStrike.keys()].sort((a, b) => a - b)

    const traces = strikes.map((strike, i) => {
      const line = byStrike.get(strike)!
      return {
        type: 'scatter' as const,
        mode: 'lines' as const,
        x: line.x,
        y: line.y,
        name: strike.toLocaleString(),
        line: { color: LINES[i % LINES.length], width: 1.5 },
        // ONE BOX PER LINE, at the point hovered. Not `x unified`: with eight
        // traces that collects eight rows into a box taller than the chart,
        // and the panel is read one strike at a time.
        hovertemplate:
          `<b>${strike.toLocaleString()}</b><br>` +
          `<span style="color:${INK}">${units}</span>  ` +
          '%{customdata}<extra></extra>',
        customdata: line.y.map(format),
      }
    })

    // ANY ROW WILL DO. Only the calendar date and the UTC offset are read out
    // of it, and every row in one response is the same session — so this does
    // not depend on the rows being sorted, which they are by strike first.
    const band = sessionRange(rows[0]?.timestamp)

    void Plotly.react(node, traces, {
      paper_bgcolor: BG,
      plot_bgcolor: BG,
      margin: { l: 62, r: 12, t: 26, b: 34 },
      hovermode: 'closest' as const,
      hoverlabel: HOVER,
      showlegend: true,
      legend: {
        orientation: 'h' as const,
        y: 1, yanchor: 'bottom' as const,
        x: 1, xanchor: 'right' as const,
        font: { color: INK, size: 9 },
        bgcolor: 'rgba(0,0,0,0)',
      },
      xaxis: {
        type: 'date' as const,
        // THE WHOLE SESSION, ALWAYS — 09:30 to 16:00, whether or not the day
        // has got there yet. See chart.ts sessionRange: an axis that grows
        // with the data redraws the same eleven minutes as the full width of
        // the panel at 09:41 and as a sliver at 15:55, and neither shape can
        // be compared with the other. Spread rather than assigned so an empty
        // board falls back to autorange instead of being handed `undefined`.
        ...(band ? { range: band, autorange: false as const } : {}),
        gridcolor: GRID, linecolor: GRID,
        tickfont: { color: INK, size: 9 },
      },
      yaxis: {
        // AUTORANGED, unlike the strike-flow panel. There the bars and a price
        // line shared a cell and had to be given separate bands; here every
        // trace is the same quantity in the same units, so letting Plotly fit
        // them is right — and pinning the range would clip whichever strike
        // ran away with the day, which is the one worth seeing.
        gridcolor: GRID, linecolor: GRID,
        // THE ZERO LINE IS THE CHART. Above it the calls led, below it the
        // puts did, and where a line crosses is the moment the day turned.
        zeroline: true, zerolinecolor: '#2a3f57', zerolinewidth: 1,
        tickfont: { color: INK, size: 9 },
        title: { text: units, font: { color: INK, size: 9 } },
      },
    }, { displayModeBar: false, responsive: true, scrollZoom: false })
  }, [rows, valueKey, units, format, height])

  return (
    <div>
      <div ref={host} style={{ width: '100%', height }} />
      {/* The server's own sentence, rendered rather than paraphrased, so the
          claim and the data cannot drift apart. */}
      <p className="px-2 pb-1 text-[10px] leading-[14px]"
         style={{ color: 'var(--text-3)' }}>
        {basis}
      </p>
    </div>
  )
}
