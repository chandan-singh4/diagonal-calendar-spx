/**
 * The 09:30 markers, as Plotly shapes.
 *
 * WHICH DAYS GET A LINE IS NOT DECIDED HERE — the list arrives on the
 * response from `core.series.market_open_lines`, including the rule that a
 * single-session window gets none. This file only turns timestamps into
 * dotted verticals, and exists so the three charts that draw them cannot
 * drift apart on colour, dash or opacity.
 */
import type Plotly from 'plotly.js-dist-min'

export function marketOpenShapes(opens: string[]): Partial<Plotly.Shape>[] {
  return opens.map((open) => ({
    type: 'line', xref: 'x', yref: 'paper',
    x0: open, x1: open, y0: 0, y1: 1,
    line: { color: '#3a5170', width: 1, dash: 'dot' },
    opacity: 0.6, layer: 'below',
  }))
}
