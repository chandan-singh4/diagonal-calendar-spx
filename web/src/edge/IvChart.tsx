/**
 * Front vs Back ATM IV, and the ratio beneath it — `views/edge.py` charts 2–4.
 *
 * ONE FRAME, NOT TWO SERIES. The rows arrive already inner-joined on
 * timestamp with the ratio computed (`core.series.merge_atm_pair`). Asking
 * for the two expiries separately and dividing here would be the tempting
 * shortcut and the wrong one: the two are polled independently, either can
 * miss a snapshot, and a division across two different minutes produces a
 * ratio that reads perfectly and never existed.
 *
 * THE REGIME BANDS ARE THE SERVER'S TOO. Where backwardation starts, and that
 * below 0.70 is nearly always same-day options decaying rather than a signal,
 * is a claim about the market that the tab prints as prose. A second copy of
 * those boundaries here would be a second opinion about what a regime is.
 */
import Plotly from 'plotly.js-dist-min'
import { useEffect, useRef } from 'react'

import type { AtmPairRow, RangeBreak, RatioBand } from '../api/types'
import { marketOpenShapes } from './marketOpens'

const FRONT = '#548ce8'
const BACK = '#a374e0'
const RATIO = '#f05252'
const BG = '#0c1421'
const GRID = '#0c1928'
const INK = '#6d8fa8'
const BRIGHT = '#dde6f1'

export interface IvChartProps {
  rows: AtmPairRow[]
  bands: RatioBand[]
  rangebreaks: RangeBreak[]
  /** 09:30 for each trading day, from the response. */
  marketOpens: string[]
  /** The window to draw the time axis on, or null to fit the data. See
   *  GapChart for why a single session is pinned (BUG-041). */
  sessionAxisRange: [string, string] | null
}

export function IvChart({ rows, bands, rangebreaks, marketOpens,
                         sessionAxisRange }: IvChartProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    const x = rows.map((row) => row.timestamp)
    const traces: Partial<Plotly.PlotData>[] = [
      {
        type: 'scatter', mode: 'lines', name: 'Front ATM IV',
        x, y: rows.map((r) => r.front_iv),
        line: { color: FRONT, width: 1.8 },
        hovertemplate: 'Front %{y:.2f}%<extra></extra>',
        xaxis: 'x', yaxis: 'y',
      },
      {
        type: 'scatter', mode: 'lines', name: 'Back ATM IV',
        x, y: rows.map((r) => r.back_iv),
        line: { color: BACK, width: 1.8 },
        hovertemplate: 'Back %{y:.2f}%<extra></extra>',
        xaxis: 'x', yaxis: 'y',
      },
      {
        type: 'scatter', mode: 'lines', name: 'IV Ratio (F/B)',
        x, y: rows.map((r) => r.iv_ratio),
        line: { color: RATIO, width: 1.8 },
        hovertemplate: 'Ratio %{y:.3f}<extra></extra>',
        xaxis: 'x2', yaxis: 'y2',
      },
    ]

    // The regime bands, drawn behind the ratio. Open-ended bands arrive with
    // a null bound — JSON has no infinity — and are clipped to what the data
    // actually reaches rather than drawn to an invented edge.
    const ratios = rows.map((r) => r.iv_ratio).filter((v): v is number => v !== null)
    const lo = ratios.length ? Math.min(...ratios) : 0
    const hi = ratios.length ? Math.max(...ratios) : 1
    const shapes: Partial<Plotly.Shape>[] = bands.map((band) => ({
      type: 'rect', xref: 'paper', yref: 'y2',
      x0: 0, x1: 1,
      y0: band.low ?? Math.min(lo, hi),
      y1: band.high ?? Math.max(lo, hi),
      fillcolor: `${band.colour}1f`,
      line: { width: 0 },
      layer: 'below',
    }))
    // Across both panels, so the IV lines and the ratio beneath them turn
    // over on the same vertical.
    shapes.push(...marketOpenShapes(marketOpens))

    const axisBase = { gridcolor: GRID, tickfont: { color: INK, size: 10 }, linecolor: GRID }

    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: BG, plot_bgcolor: BG,
      font: { color: INK, size: 11 },
      margin: { l: 58, r: 20, t: 30, b: 34 },
      hovermode: 'x unified',
      hoverlabel: { bgcolor: '#111c2e', bordercolor: '#1a2d45',
                    font: { color: BRIGHT, size: 12 } },
      legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'left', x: 0,
                font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
      xaxis: { ...axisBase, rangebreaks, range: sessionAxisRange ?? undefined,
               domain: [0, 1], anchor: 'y',
               matches: 'x2', showticklabels: false },
      yaxis: { ...axisBase, domain: [0.42, 1], title: { text: 'IV %' } },
      xaxis2: { ...axisBase, rangebreaks, range: sessionAxisRange ?? undefined,
                domain: [0, 1], anchor: 'y2' },
      yaxis2: { ...axisBase, domain: [0, 0.34], title: { text: 'Ratio' } },
      shapes,
    }

    void Plotly.react(node, traces, layout, { displayModeBar: false, responsive: true })
  }, [rows, bands, rangebreaks, marketOpens, sessionAxisRange])

  useEffect(() => {
    const node = host.current
    return () => {
      if (node) Plotly.purge(node)
    }
  }, [])

  return <div ref={host} style={{ width: '100%', height: '26rem' }} />
}
