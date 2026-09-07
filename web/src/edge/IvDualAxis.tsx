/**
 * Chart 3 — "Front ATM IV vs. Back ATM IV, with IV Ratio" (`views/edge.py`,
 * `fig_atm`). All three series on one panel, the ratio on a right-hand axis.
 *
 * WHY THIS EXISTS ALONGSIDE THE STACKED CHART ABOVE IT, which draws the same
 * three numbers. The stacked one puts the two IVs on a shared scale so the
 * VERTICAL GAP is the spread, and pushes the ratio into its own panel where
 * the regime bands can be read. This one overlays the ratio on the IVs, so a
 * move in the ratio can be traced to whichever leg caused it. Neither answers
 * the other's question, which is why the page carries both.
 *
 * The ratio has its own axis because a value near 1.0 and an IV near 20%
 * share no scale — plotted together on one axis the ratio would be a flat
 * line along the floor.
 */
import Plotly from 'plotly.js-dist-min'
import { useEffect, useRef } from 'react'

import type { AtmPairRow, RangeBreak } from '../api/types'
import { marketOpenShapes } from './marketOpens'

const FRONT = '#548ce8'
const BACK = '#a374e0'
const RATIO = '#f05252'
const BG = '#0c1421'
const GRID = '#0c1928'
const INK = '#6d8fa8'
const BRIGHT = '#dde6f1'

export interface IvDualAxisProps {
  rows: AtmPairRow[]
  rangebreaks: RangeBreak[]
  /** 09:30 for each trading day, from the response. */
  marketOpens: string[]
  /** The window to draw the time axis on, or null to fit the data. See
   *  GapChart for why a single session is pinned (BUG-041). */
  sessionAxisRange: [string, string] | null
}

export function IvDualAxis({ rows, rangebreaks, marketOpens,
                            sessionAxisRange }: IvDualAxisProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    const x = rows.map((r) => r.timestamp)
    const traces: Partial<Plotly.PlotData>[] = [
      {
        type: 'scatter', mode: 'lines', name: 'Front ATM IV',
        x, y: rows.map((r) => r.front_iv),
        line: { color: FRONT, width: 1.8 }, yaxis: 'y',
        hovertemplate: 'Front %{y:.2f}%<extra></extra>',
      },
      {
        type: 'scatter', mode: 'lines', name: 'Back ATM IV',
        x, y: rows.map((r) => r.back_iv),
        line: { color: BACK, width: 1.8 }, yaxis: 'y',
        hovertemplate: 'Back %{y:.2f}%<extra></extra>',
      },
      {
        type: 'scatter', mode: 'lines', name: 'IV Ratio (F/B)',
        x, y: rows.map((r) => r.iv_ratio),
        line: { color: RATIO, width: 1.8 }, yaxis: 'y2',
        hovertemplate: 'Ratio %{y:.4f}<extra></extra>',
      },
    ]

    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: BG, plot_bgcolor: BG,
      font: { color: INK, size: 11 },
      margin: { l: 58, r: 58, t: 12, b: 40 },
      hovermode: 'x unified',
      hoverlabel: { bgcolor: '#111c2e', bordercolor: '#1a2d45',
                    font: { color: BRIGHT, size: 12 } },
      xaxis: { rangebreaks, range: sessionAxisRange ?? undefined,
               gridcolor: GRID, tickfont: { color: INK, size: 10 } },
      yaxis: { title: { text: 'IV %' }, side: 'left', gridcolor: GRID,
               tickfont: { color: INK, size: 10 } },
      yaxis2: { title: { text: 'Ratio' }, side: 'right', overlaying: 'y',
                showgrid: false, tickfont: { color: INK, size: 10 } },
      legend: { orientation: 'h', yanchor: 'top', y: -0.14, xanchor: 'center', x: 0.5,
                font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
      shapes: marketOpenShapes(marketOpens),
    }

    void Plotly.react(node, traces, layout, { displayModeBar: false, responsive: true })
  }, [rows, rangebreaks, marketOpens, sessionAxisRange])

  useEffect(() => {
    const node = host.current
    return () => { if (node) Plotly.purge(node) }
  }, [])

  return <div ref={host} style={{ width: '100%', height: '21rem' }} />
}
