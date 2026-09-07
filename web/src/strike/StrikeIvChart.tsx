/**
 * Front vs back IV at the two trade strikes — `views/strike.py`, `fig_str`.
 *
 * Six traces at most: front IV, back IV and their ratio, for the call strike
 * (solid) and the put strike (dotted). The ratio pair sits on a second axis
 * because a ratio around 1.0 and an IV around 20% share no scale.
 *
 * THE SIDES ARE DRAWN INDEPENDENTLY, and that is not tidiness. A strike can
 * have history in one expiry and none in the other, so either side can be
 * empty on its own; drawing them as one frame would mean dropping both when
 * one is missing.
 *
 * The gaps in each line are `break_sessions` rows, and the ratio breaks with
 * them because the server computes the ratio BEFORE inserting them (BUG-002).
 * Reversed, the ratio line draws a straight connector across a weekend and
 * invents IV movement that never happened.
 */
import Plotly from 'plotly.js-dist-min'
import { useEffect, useRef } from 'react'

import type { AtmPairRow, RangeBreak } from '../api/types'

const FRONT = '#548ce8'
const BACK = '#a374e0'
const RATIO = '#f05252'
const BG = '#060b12'
const GRID = '#0c1928'
const INK = '#6d8fa8'
const BRIGHT = '#dde6f1'

export interface StrikeIvChartProps {
  calls: AtmPairRow[]
  puts: AtmPairRow[]
  callStrike: number
  putStrike: number
  rangebreaks: RangeBreak[]
}

export function StrikeIvChart({
  calls, puts, callStrike, putStrike, rangebreaks,
}: StrikeIvChartProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    const traces: Partial<Plotly.PlotData>[] = []

    for (const [rows, strike, letter, dash] of [
      [calls, callStrike, 'C', undefined],
      [puts, putStrike, 'P', 'dot'],
    ] as const) {
      if (rows.length === 0) continue
      const x = rows.map((r) => r.timestamp)
      traces.push(
        {
          type: 'scatter', mode: 'lines',
          name: `Front ${strike.toFixed(0)}${letter}`,
          x, y: rows.map((r) => r.front_iv),
          line: { color: FRONT, width: 1.5, dash },
          hovertemplate: '%{y:.2f}%<extra></extra>',
          yaxis: 'y',
        },
        {
          type: 'scatter', mode: 'lines',
          name: `Back ${strike.toFixed(0)}${letter}`,
          x, y: rows.map((r) => r.back_iv),
          line: { color: BACK, width: 1.5, dash },
          hovertemplate: '%{y:.2f}%<extra></extra>',
          yaxis: 'y',
        },
        {
          type: 'scatter', mode: 'lines',
          name: `${letter === 'C' ? 'Call' : 'Put'} Ratio (F/B)`,
          x, y: rows.map((r) => r.iv_ratio),
          line: { color: RATIO, width: 1.5, dash },
          hovertemplate: '%{y:.4f}<extra></extra>',
          yaxis: 'y2',
        },
      )
    }

    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: BG, plot_bgcolor: BG,
      font: { color: INK, size: 11 },
      margin: { l: 52, r: 52, t: 28, b: 26 },
      hovermode: 'x unified',
      hoverlabel: { bgcolor: '#111c2e', bordercolor: '#1a2d45',
                    font: { color: BRIGHT, size: 12 } },
      xaxis: { rangebreaks, gridcolor: GRID, tickfont: { color: INK, size: 10 } },
      yaxis: { title: { text: 'IV %' }, side: 'left', gridcolor: GRID,
               tickfont: { color: INK, size: 10 } },
      yaxis2: { title: { text: 'Ratio' }, side: 'right', overlaying: 'y',
                showgrid: false, tickfont: { color: INK, size: 10 } },
      legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'left', x: 0,
                font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
    }

    void Plotly.react(node, traces, layout, { displayModeBar: false, responsive: true })
  }, [calls, puts, callStrike, putStrike, rangebreaks])

  useEffect(() => {
    const node = host.current
    return () => {
      if (node) Plotly.purge(node)
    }
  }, [])

  return <div ref={host} style={{ width: '100%', height: '26rem' }} />
}
