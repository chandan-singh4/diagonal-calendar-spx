/**
 * Dealer structure — where today's trading actually happened, across BOTH
 * when the option expires and what price it is betting on.
 *
 * TWO DIMENSIONS AND A SIZE, which is the whole reason this chart exists and
 * the reason it is not filtered by the expiry picker: expiry runs along the
 * bottom, strike up the side, and the area of each point is how much traded
 * there. Scoped to one expiry it would be a single column — a chart whose
 * subject is the term structure, with the term structure removed.
 *
 * NOTHING IS SIZED OR CLASSIFIED HERE. `radius` and `flow` arrive computed
 * from core/dealer.py. The radius is relative to the busiest point on the
 * WHOLE band, so a quiet expiry cannot be made to look busy by the trim
 * dropping its neighbours; the flow bucket is a put/call ratio read against
 * thresholds that are Python's to own. Recomputing either here would be a
 * second definition of the chart's meaning.
 *
 * A CATEGORICAL X AXIS. Expiries are irregularly spaced in time — a run of
 * dailies then a monthly gap — and on a date axis the near columns crush
 * together while the far ones sit alone. The columns are evenly spaced and
 * `expiry_order` (days to expiry) puts them in the right order.
 */
import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-dist-min'

import type { BubbleMeasure, BubbleRow } from '../api/types'
import { BG, GRID, HOVER, INK, reading } from './chart'

export interface DealerBubblesProps {
  rows: BubbleRow[]
  /** THIS snapshot's spot. The dashed line every point is read against. */
  spot: number
  /** Which view these rows are. Decides the palette's MEANING and the hover's
   *  rows — not merely a caption, which is why it is required rather than
   *  defaulted. */
  measure: BubbleMeasure
  basis: string
  height: string
}

/**
 * The three flow buckets, and the words for them.
 *
 * KEYED ON THE SERVER'S OWN STRING. `flow` is core/dealer.py's verdict, so
 * this is a lookup rather than a rule — the boundary between "mostly puts"
 * and "balanced" is never decided here. An unrecognised bucket falls through
 * to grey rather than being guessed at.
 */
const FLOW: Record<string, { colour: string; label: string }> = {
  call: { colour: '#10d4a3', label: 'mostly calls' },
  put: { colour: '#f05252', label: 'mostly puts' },
  balanced: { colour: '#e8b64c', label: 'both sides' },

  // THE GAMMA BUCKETS SHARE THE PALETTE AND NOT THE MEANING, and the labels
  // are what stop that being a trap. Green is "calls" on one view and
  // "dealers damp the move" on the other; the two are unrelated, and a strike
  // is routinely call-heavy AND short gamma. The legend is always on screen
  // and always says which reading is in force, so the colour is never the
  // only thing carrying it.
  //
  // The hues are reused rather than invented because they already mean
  // steady/green and violent/red everywhere else on this tab, and a fourth
  // and fifth colour would compete with the flip line and the spot rule for
  // no gain.
  long: { colour: '#10d4a3', label: 'dampens moves' },
  short: { colour: '#f05252', label: 'amplifies moves' },
  flat: { colour: '#8fa6bd', label: 'no net exposure' },
}
const UNKNOWN = { colour: '#8fa6bd', label: 'unclassified' }

/** Dollars, at the scale a gamma figure is actually read at. `reading` is the
 *  full grouped number, which is right for a contract count and unreadable
 *  for 1.4e10 — this is core/format.py's own rounding, kept to the same
 *  breakpoints so the hover and the tab's headline labels agree. */
function dollars(v: number | undefined): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—'
  const sign = v < 0 ? '-' : ''
  const n = Math.abs(v)
  if (n >= 1e9) return `${sign}$${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${sign}$${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${sign}$${(n / 1e3).toFixed(1)}K`
  return `${sign}$${n.toFixed(0)}`
}

export function DealerBubbles({ rows, spot, measure, basis,
                               height }: DealerBubblesProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return

    // COLUMN ORDER IS PROXIMITY, from the server's `expiry_order` (days to
    // expiry). Sorting the labels as strings would order "Fri, Oct 2" before
    // "Mon, Sep 8" — alphabetical, and meaningless as a term structure.
    const order = new Map<string, number>()
    for (const r of rows) order.set(r.expiry_label, r.expiry_order)
    const columns = [...order.keys()].sort(
      (a, b) => order.get(a)! - order.get(b)!)

    // ONE TRACE PER FLOW BUCKET, not per point. Three traces give three
    // legend entries that mean something ("mostly calls"), where one trace
    // per point would give hundreds that mean nothing — and Plotly redraws
    // every trace on hover.
    // Which vocabulary the rows are speaking. Taken from the measure the
    // server echoed, NOT sniffed from whether `net_gex` happens to be
    // present: an empty board has no rows to sniff.
    const isGamma = measure !== 'volume'

    const buckets = [...new Set(rows.map((r) => r.flow))]
    const traces = buckets.map((bucket) => {
      const kind = FLOW[bucket] ?? UNKNOWN
      const points = rows.filter((r) => r.flow === bucket)
      return {
        type: 'scatter' as const,
        mode: 'markers' as const,
        x: points.map((p) => p.expiry_label),
        y: points.map((p) => p.strike),
        name: kind.label,
        marker: {
          // RADIUS x 2, because Plotly's `size` is a DIAMETER and
          // core/dealer.py's is a radius. Passed straight through, every
          // bubble is drawn at half the area the sizing rule chose — which
          // looks like a plausible chart, just a quieter session than the one
          // that happened. `sizemode` is stated for the same reason: its
          // default is 'diameter' but leaving it implicit is what let the
          // mismatch hide in the first place.
          size: points.map((p) => p.radius * 2),
          sizemode: 'diameter' as const,
          color: kind.colour,
          opacity: 0.62,
          line: { color: kind.colour, width: 1 },
        },
        // ONE customdata SHAPE PER MEASURE, chosen once outside the loop.
        // Both end with the bucket's label so the last template line is
        // shared; everything above it is the measure's own.
        customdata: points.map((p) => (isGamma
          ? [
              dollars(p.net_gex),
              dollars(p.call_gex),
              // Put gamma is stored unsigned (it is a magnitude, dealer sign
              // applied separately) so it is negated for display: the reader
              // is looking at what pushes which way, and an unsigned put
              // figure beside a signed net reads as an arithmetic error.
              dollars(p.put_gex === undefined ? undefined : -p.put_gex),
              // The total REGARDLESS of side. On a near-cancelling strike
              // this is large while the bubble is small, and that gap is the
              // one thing the chart's geometry cannot show.
              dollars(p.abs_gex),
              kind.label,
            ]
          : [
              reading(p.total_volume),
              reading(p.call_volume),
              reading(p.put_volume),
              // A DASH, NOT A ZERO, where the chain carried no mark. The
              // server sends null for exactly this reason and turning it into
              // 0 here would claim a strike where nothing was paid.
              p.notional === null ? '—' : reading(p.notional),
              kind.label,
            ])),
        hovertemplate: isGamma
          ? '<b>%{y:,.0f}</b>  ·  %{x}<br>' +
            `<span style="color:${INK}">Net</span>  %{customdata[0]}<br>` +
            `<span style="color:${FLOW.call.colour}">Calls</span>  ` +
            '%{customdata[1]}<br>' +
            `<span style="color:${FLOW.put.colour}">Puts</span>  ` +
            '%{customdata[2]}<br>' +
            `<span style="color:${INK}">Total both sides</span>  ` +
            '%{customdata[3]}<br>' +
            '%{customdata[4]}<extra></extra>'
          : '<b>%{y:,.0f}</b>  ·  %{x}<br>' +
            `<span style="color:${INK}">Traded</span>  %{customdata[0]}<br>` +
            `<span style="color:${FLOW.call.colour}">Calls</span>  ` +
            '%{customdata[1]}<br>' +
            `<span style="color:${FLOW.put.colour}">Puts</span>  ` +
            '%{customdata[2]}<br>' +
            `<span style="color:${INK}">Premium</span>  $%{customdata[3]}<br>` +
            '%{customdata[4]}<extra></extra>',
      }
    })

    void Plotly.react(node, traces, {
      paper_bgcolor: BG,
      plot_bgcolor: BG,
      margin: { l: 62, r: 12, t: 26, b: 44 },
      hovermode: 'closest' as const,
      hoverlabel: HOVER,
      showlegend: true,
      legend: {
        orientation: 'h' as const,
        y: 1, yanchor: 'bottom' as const,
        x: 1, xanchor: 'right' as const,
        font: { color: INK, size: 9 },
        bgcolor: 'rgba(0,0,0,0)',
        // The legend swatch would otherwise inherit the largest bubble's
        // size and swamp its own label.
        itemsizing: 'constant' as const,
      },
      xaxis: {
        type: 'category' as const,
        categoryorder: 'array' as const,
        categoryarray: columns,
        gridcolor: GRID, linecolor: GRID,
        tickfont: { color: INK, size: 9 },
      },
      yaxis: {
        gridcolor: GRID, linecolor: GRID, zeroline: false,
        tickfont: { color: INK, size: 9 },
        title: { text: 'strike', font: { color: INK, size: 9 } },
      },
      shapes: [{
        // Spot, across the whole width. Every point on this chart is read as
        // "above or below the money", and without the line that reading has
        // to be done from the axis labels.
        type: 'line' as const,
        xref: 'paper' as const, yref: 'y' as const,
        x0: 0, x1: 1, y0: spot, y1: spot,
        line: { color: '#54a0ff', width: 1, dash: 'dash' as const },
      }],
      annotations: [{
        text: `SPX ${spot.toLocaleString(undefined, {
          minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
        x: 1, y: spot, xref: 'paper' as const, yref: 'y' as const,
        xanchor: 'right' as const, yanchor: 'bottom' as const, yshift: 2,
        showarrow: false, font: { color: '#54a0ff', size: 9 },
      }],
    }, { displayModeBar: false, responsive: true, scrollZoom: false })
  }, [rows, spot, measure, height])

  return (
    <div>
      <div ref={host} style={{ width: '100%', height }} />
      <p className="px-2 pb-1 text-[10px] leading-[14px]"
         style={{ color: 'var(--text-3)' }}>
        {basis}
      </p>
    </div>
  )
}
