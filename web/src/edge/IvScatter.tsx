/**
 * Chart 4 — "Front vs. Back IV Scatter, intraday trajectory" (`views/edge.py`,
 * `fig_intra`). One dot per snapshot, coloured by time of day.
 *
 * THIS IS THE SAME DATA AS THE LINE CHARTS WITH TIME TAKEN OFF THE AXIS, and
 * that is what it is for: it answers "how did the two legs move against each
 * other" rather than "what did each do". Dots above the dashed R=1 line are
 * readings where the near-dated options were pricing in more movement than
 * the far-dated ones.
 *
 * BOTH AXES SHARE ONE RANGE, and it is the server's
 * (`core.series.scatter_domain`). Letting Plotly scale them independently
 * would tilt the diagonal off 45°, and every dot's position relative to it
 * would then be a drawing artefact rather than a comparison.
 *
 * THE COLOUR IS THE SERVER'S HOUR, not one parsed here. The timestamps arrive
 * as naive local wall-clock (DEBT-030), so a browser parsing them applies the
 * VIEWER'S timezone: a 10:30 New York reading would colour as 15:30 in London
 * and the chart would still look entirely plausible.
 */
import Plotly from 'plotly.js-dist-min'
import { useEffect, useRef } from 'react'

import type { AtmPairRow } from '../api/types'

const BG = '#0c1421'
const GRID = '#0c1928'
const INK = '#6d8fa8'
const BRIGHT = '#dde6f1'
const REF = '#2a3f56'

export interface IvScatterProps {
  rows: AtmPairRow[]
  domain: [number, number]
}

export function IvScatter({ rows, domain }: IvScatterProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    const traces: Partial<Plotly.PlotData>[] = [
      {
        type: 'scatter', mode: 'lines', name: 'R = 1  (Front = Back)',
        x: domain, y: domain,
        line: { color: REF, dash: 'dash' },
        hoverinfo: 'skip',
      },
      {
        type: 'scatter', mode: 'markers', name: 'snapshots',
        x: rows.map((r) => r.back_iv),
        y: rows.map((r) => r.front_iv),
        marker: {
          size: 6,
          color: rows.map((r) => r.hod ?? null) as number[],
          colorscale: 'Viridis',
          showscale: true,
          colorbar: { title: { text: 'Hour ET' } },
          line: { width: 0 },
        },
        customdata: rows.map((r) => r.iv_ratio) as number[],
        hovertemplate:
          'Back %{x:.2f}%<br>Front %{y:.2f}%<br>R=%{customdata:.4f}<extra></extra>',
      },
    ]

    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: BG, plot_bgcolor: BG,
      font: { color: INK, size: 11 },
      margin: { l: 58, r: 20, t: 12, b: 40 },
      hoverlabel: { bgcolor: '#111c2e', bordercolor: '#1a2d45',
                    font: { color: BRIGHT, size: 12 } },
      xaxis: { title: { text: 'Back ATM IV %' }, gridcolor: GRID, range: domain,
               tickfont: { color: INK, size: 10 } },
      yaxis: { title: { text: 'Front ATM IV %' }, gridcolor: GRID, range: domain,
               tickfont: { color: INK, size: 10 } },
      legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, x: 0,
                font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
    }

    void Plotly.react(node, traces, layout, { displayModeBar: false, responsive: true })
  }, [rows, domain])

  useEffect(() => {
    const node = host.current
    return () => { if (node) Plotly.purge(node) }
  }, [])

  return <div ref={host} style={{ width: '100%', height: '26rem' }} />
}
