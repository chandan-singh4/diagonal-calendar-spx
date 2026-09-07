/**
 * Chart 1 and its SPX panel — `views/edge.py`, "Diagonal vs. Transform Order
 * Mark" and the strike-channel panel beneath it.
 *
 * NOTHING HERE COMPUTES A MARK. `diagonal_mark`, `transform_mark` and `gap`
 * are columns the server sent, from `core.scanner.add_mark_columns` — the one
 * definition of what a diagonal is worth. The strike crossings are the
 * server's too (`core.series.strike_crossings`): a crossing is a directed
 * event with a boundary rule, and getting that rule wrong marks the chart
 * with events that never happened, which reads as a volatile session rather
 * than as a bug.
 *
 * The rangebreaks are served as well. Which breaks are safe was established
 * by bisection — a per-date break corrupts Plotly's point positioning for
 * everything after it the moment a holiday falls inside the window — and the
 * evidence lives in core/series.py beside the values.
 *
 * THE SHADED STRETCHES ARE WHERE THE GAP REACHED THE THRESHOLD, which is the
 * whole reason this chart exists: those are the moments the position could
 * have been transformed. The threshold is read from the response, never
 * written here.
 */
import Plotly from 'plotly.js-dist-min'
import { useEffect, useRef } from 'react'

import type { Crossings, MarkRow, RangeBreak } from '../api/types'
import { marketOpenShapes } from './marketOpens'

const DIAGONAL = '#548ce8'
const TRANSFORM = '#10d4a3'
const SPX_LINE = '#d7deea'
const BG = '#0c1421'
const GRID = '#0c1928'
const INK = '#6d8fa8'
const BRIGHT = '#dde6f1'
const STRIKE_LINE = '#4a5d80'

export interface GapChartProps {
  rows: MarkRow[]
  rangebreaks: RangeBreak[]
  crossings: Crossings | null
  putStrike: number
  callStrike: number
  /** The 5-point line, from the response. Duplicated in four places in Python
   *  already (DEBT-031); a fifth copy here would be the one nobody updates. */
  threshold: number
  /** 09:30 for each trading day, from the response. */
  marketOpens: string[]
  /** The window to draw the time axis on, from the response, or null to let
   *  Plotly fit the data (which is right across several days).
   *
   *  WHY THIS IS NOT OPTIONAL. Autoranging a single session ends the axis at
   *  the last row, so a day whose marks stopped at 15:05 drew an axis stopping
   *  at 15:05 and the missing hour was invisible -- it read as a short trading
   *  day. Pinning the session makes the absence show as empty space. The
   *  window itself is Python's (core.series.session_axis_range); the old
   *  screen draws on the same one. */
  sessionAxisRange: [string, string] | null
}

function column(rows: MarkRow[], name: keyof MarkRow): (number | null)[] {
  return rows.map((row) => {
    const v = row[name]
    return typeof v === 'number' ? v : null
  })
}

/** The contiguous stretches where the gap reached the threshold, as x-ranges.
 *
 *  THIS IS NOT A MEASUREMENT. It compares an already-computed `gap` against
 *  an already-served `threshold` and groups the neighbours that pass — the
 *  shading is a drawing of the column, not a second opinion about it. A gap
 *  row (null, inserted by break_sessions) ends a stretch, so a band never
 *  spans a weekend.
 *
 *  A BAND ENDS AT THE FIRST ROW THAT FAILS, NOT THE LAST ONE THAT PASSED,
 *  and that one-row difference is the whole bug this replaced (Chandan,
 *  2026-09-06: "when the difference is over five, I don't see the shaded
 *  green"). Crossings here are frequently a SINGLE reading — on the live
 *  record one pair reached the threshold at four isolated moments across
 *  five sessions — and ending the band on the last passing row makes
 *  `x0 === x1` for every one of them: a zero-width rectangle, which Plotly
 *  draws faithfully as nothing at all. The chart was not failing to find
 *  the crossings, it was drawing them with no width. `views/edge.py` closes
 *  its `add_vrect` on `_ts_list[i]` at the row where the flag goes false,
 *  for exactly this reason; this now matches it. */
function eligibleBands(rows: MarkRow[], threshold: number): [string, string][] {
  const bands: [string, string][] = []
  let start: string | null = null
  for (let i = 0; i < rows.length; i += 1) {
    const gap = rows[i].gap
    const on = gap !== null && gap >= threshold
    if (on && start === null) start = rows[i].timestamp
    if (!on && start !== null) {
      bands.push([start, rows[i].timestamp])
      start = null
    }
  }
  if (start !== null) bands.push([start, rows[rows.length - 1].timestamp])
  return bands
}

export function GapChart({
  rows, rangebreaks, crossings, putStrike, callStrike, threshold, marketOpens,
  sessionAxisRange,
}: GapChartProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    const x = rows.map((row) => row.timestamp)
    const hasSpx = rows.some((row) => row.spx !== null)

    // ONE MASTER TOOLTIP IN A FIXED LINE ORDER: SPX, Diagonal, Transform,
    // Gap — carried by the Transform trace, with every other trace's hover
    // suppressed. `views/edge.py` builds it the same way and says why: left
    // to itself, Plotly orders the unified tooltip by trace and axis, which
    // silently reversed the lines. The reader learns one order and it stays.
    //
    // THE GAP LINE IS UNSIGNED and the shading is not, which is the page's
    // behaviour rather than an oversight introduced here. The tooltip reports
    // the DISTANCE between the two marks (`views/edge.py` takes `.abs()`),
    // while the green shading tests the signed gap against the threshold. So
    // a pair where the diagonal is dearer than the transform reads "Gap:
    // $8.00" with no shading, correctly: that is eight points the wrong way.
    const master = rows.map((row) => [
      row.spx,
      row.diagonal_mark,
      row.transform_mark,
      row.gap === null ? null : Math.abs(row.gap),
    ])

    const traces: Partial<Plotly.PlotData>[] = [
      {
        type: 'scatter', mode: 'lines', name: 'Diagonal Mark',
        x, y: column(rows, 'diagonal_mark'),
        line: { color: DIAGONAL, width: 1.8 },
        hoverinfo: 'skip',
        xaxis: 'x', yaxis: 'y',
      },
      {
        type: 'scatter', mode: 'lines', name: 'Transform Order Mark',
        x, y: column(rows, 'transform_mark'),
        line: { color: TRANSFORM, width: 1.8 },
        // The band between the two lines IS the gap, drawn directly so it
        // reads at a glance rather than by comparing two heights.
        fill: 'tonexty', fillcolor: 'rgba(124,148,199,0.11)',
        customdata: master as unknown as Plotly.Datum[],
        hovertemplate:
          'SPX: %{customdata[0]:,.2f}' +
          '<br>Diagonal Mark: $%{customdata[1]:.2f}' +
          '<br>Transform Order Mark: $%{customdata[2]:.2f}' +
          '<br>Gap: $%{customdata[3]:.2f}<extra></extra>',
        xaxis: 'x', yaxis: 'y',
      },
    ]

    if (hasSpx) {
      traces.push({
        type: 'scatter', mode: 'lines', name: 'SPX',
        x, y: column(rows, 'spx'),
        line: { color: SPX_LINE, width: 2 },
        // Reported by the master tooltip above, not twice.
        hoverinfo: 'skip',
        xaxis: 'x2', yaxis: 'y2',
      })
      for (const [key, symbol, label] of [
        ['up', 'triangle-up', 'crossed up through strike'],
        ['down', 'triangle-down', 'crossed down through strike'],
      ] as const) {
        const points = crossings?.[key] ?? []
        if (points.length === 0) continue
        traces.push({
          type: 'scatter', mode: 'markers',
          x: points.map((p) => p.x), y: points.map((p) => p.y),
          marker: { symbol, size: 11, color: TRANSFORM,
                    line: { width: 1, color: BG } },
          showlegend: false,
          hovertemplate: `SPX ${label}<extra></extra>`,
          xaxis: 'x2', yaxis: 'y2',
        })
      }
    }

    const shapes: Partial<Plotly.Shape>[] = eligibleBands(rows, threshold).map(
      ([x0, x1]) => ({
        type: 'rect', xref: 'x', yref: 'y domain',
        x0, x1, y0: 0, y1: 1,
        fillcolor: 'rgba(16,212,163,0.10)', line: { width: 0 }, layer: 'below',
      }),
    )

    // WHERE EACH TRADING DAY STARTS. Without these the session boundary is
    // invisible: break_sessions leaves a gap, but a gap looks the same as a
    // quiet stretch, and Thursday's close cannot be told from Friday's open.
    // Drawn across BOTH panels (`yref: 'paper'`) so the marks and the SPX
    // path below them turn over on the same line.
    shapes.push(...marketOpenShapes(marketOpens))

    if (hasSpx) {
      // The channel between the short strikes. Neutral, NOT green: SPX
      // sitting inside it is a fact, not a validated good signal.
      shapes.push({
        type: 'rect', xref: 'paper', yref: 'y2',
        x0: 0, x1: 1,
        y0: Math.min(putStrike, callStrike), y1: Math.max(putStrike, callStrike),
        fillcolor: 'rgba(124,148,199,0.09)', line: { width: 0 }, layer: 'below',
      })
      for (const strike of [putStrike, callStrike]) {
        shapes.push({
          type: 'line', xref: 'paper', yref: 'y2',
          x0: 0, x1: 1, y0: strike, y1: strike,
          line: { color: STRIKE_LINE, width: 1.2, dash: 'dash' },
        })
      }
    }

    const axisBase = { gridcolor: GRID, tickfont: { color: INK, size: 10 }, linecolor: GRID }
    // The two panels share one time axis, so a zoom on either moves both.
    // `matches` takes Plotly's own axis-name union, hence the cast.
    // `range` only when the server sent one: a fixed window is right for one
    // session and wrong across several, and that call is made in Python.
    const xRange = sessionAxisRange ?? undefined
    const xBase = {
      ...axisBase, rangebreaks, range: xRange,
      matches: (hasSpx ? 'x2' : undefined) as Plotly.LayoutAxis['matches'],
    }

    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: BG, plot_bgcolor: BG,
      font: { color: INK, size: 11 },
      margin: { l: 58, r: 20, t: 30, b: 34 },
      hovermode: 'x unified',
      hoverlabel: { bgcolor: '#111c2e', bordercolor: '#1a2d45',
                    font: { color: BRIGHT, size: 12 } },
      legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'left', x: 0,
                font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
      // THE GAP BETWEEN THE TWO PANELS IS 0.14 OF THE FIGURE, widened from
      // 0.08: "a little spacing between Diagonal vs Transform chart and SPX
      // line chart. They both look very close to each other" (Chandan,
      // 2026-09-07). They are two panels of ONE Plotly figure — that is what
      // lets a zoom on either move both — so the space between them is a
      // domain gap here, not a CSS margin between two components.
      //
      // THE SPX PANEL GAVE UP MOST OF IT. It is a single line read for shape
      // and for where it sits against two dashed strikes, and it loses less
      // to being 2% shorter than the mark panel above it, which carries two
      // series, the shaded transform stretches and the crossing markers.
      xaxis: { ...xBase, domain: [0, 1], anchor: 'y', showticklabels: !hasSpx },
      yaxis: { ...axisBase, domain: hasSpx ? [0.40, 1] : [0, 1], title: { text: 'Mark' } },
      ...(hasSpx
        ? {
            xaxis2: { ...axisBase, rangebreaks, range: xRange, domain: [0, 1], anchor: 'y2' },
            yaxis2: { ...axisBase, domain: [0, 0.26], title: { text: 'SPX' } },
          }
        : {}),
      shapes,
    }

    void Plotly.react(node, traces, layout, { displayModeBar: false, responsive: true })
  }, [rows, rangebreaks, crossings, putStrike, callStrike, threshold, marketOpens,
      sessionAxisRange])

  useEffect(() => {
    const node = host.current
    return () => {
      if (node) Plotly.purge(node)
    }
  }, [])

  return <div ref={host} style={{ width: '100%', height: '30rem' }} />
}
