/**
 * The palette and the wick geometry, shared by the two chart components.
 *
 * ITS OWN FILE BECAUSE BOTH DRAW THE SAME MEASURES. `StrikeChart` is the
 * detail view and `MiniPanel` is one cell of the grid, and a call bar has to
 * be the same green in both — the kind of difference nobody reports and
 * everybody notices. These lived in StrikeChart.tsx until the grid was built,
 * and moving them out is also what the fast-refresh rule wants: a module that
 * exports components should export only components.
 */
import type { SessionRangeRow } from '../api/types'

export const CALL = '#10d4a3'
export const PUT = '#f05252'
export const FLIP = '#e8b64c'
export const BG = '#0c1421'
export const GRID = '#0c1928'
export const INK = '#6d8fa8'
export const BRIGHT = '#dde6f1'

// The wick colours are the call/put pair at low opacity ON PURPOSE, unlike the
// volume fills: a wick belongs to the bar it sits behind and says where that
// same series has been, so sharing its hue is the message. The volume fills
// are a different quantity entirely and get their own pair.
export const CALL_WICK = 'rgba(16,212,163,0.5)'
export const PUT_WICK = 'rgba(240,82,82,0.5)'
// The NET panels draw one signed bar rather than a call/put pair, so their
// wick belongs to neither side. Amber is the tab's third colour — already the
// flip line — and it reads as "this is the net figure" against bars that are
// green above the axis and red below.
export const NET_WICK = 'rgba(232,182,76,0.55)'
// Deliberately not the call/put pair: those identify SIDES, and reusing them
// for the volume backdrop would read as one.
//
// LIGHTENED ON PURPOSE (Chandan, 2026-09-06): "replace that with something
// lighter that contrasts well with the background". The old pair was a mid
// blue and a mid purple — #548ce8 and #a374e0 — at 20% opacity over a near
// black navy ground (BG, #0c1421). Both are DARKER than several of the chart's
// own inks at that opacity, so the shade they drew sat almost exactly on the
// panel background and disappeared on any screen not viewed straight on.
//
// The hues are unchanged; the lightness is what moved. Composited over BG the
// fills go from 1.32:1 and 1.30:1 against the ground to 2.19:1 and 1.62:1, and
// the edges from ~2.5:1 to ~7:1.
//
// THE TWO FILLS DIFFER IN LIGHTNESS AS WELL AS HUE, and the first attempt at
// this did not: lightening both by the same amount left them at 1.00:1 against
// EACH OTHER — identical brightness, separated by hue alone. That is precisely
// the pair a red-green-blind reader cannot split, and it is invisible in a
// greyscale print or a screenshot run through one. The call side is now the
// lighter of the two by a clear step, so the distinction survives losing
// colour altogether.
//
// The fill carries the shape, so it takes most of the lift; the edge only has
// to stay visible where two shades abut, and pushing it further would make a
// backdrop compete with the bars it sits behind.
export const CALL_VOL_FILL = 'rgba(150,190,255,0.34)'
export const PUT_VOL_FILL = 'rgba(176,140,235,0.28)'
export const CALL_VOL_EDGE = 'rgba(178,209,255,0.75)'
export const PUT_VOL_EDGE = 'rgba(205,180,248,0.72)'

/** How panel 1 is drawn for each view. `mirror` puts the put series below the
 *  axis; the second-order pair does NOT mirror, because calls and puts at one
 *  strike carry the same sign there and flipping one would draw two bars
 *  cancelling out where the honest picture is a single tall one. */
/**
 * THE HOVER BOX, once, for every panel on the tab.
 *
 * It was three near-copies that had drifted apart, and the drift showed:
 * Plotly takes a hover label's TEXT colour from the trace colour unless told
 * otherwise, so on a dark box the body rendered in the same dim grey as the
 * axis ticks — legible on the green call bar, barely readable on everything
 * else. Stating the colour is the fix; stating it in one place is what stops
 * the next panel getting it wrong again.
 *
 * `align: 'left'` matters more than it looks: these boxes are label/value
 * pairs, and centred rows put every number at a different indent.
 */
/** Every chart button on this tab wears this, so they cannot drift apart in
 *  size the way they once drifted apart in position. A fixed width because the
 *  glyphs have different natural advances and padding alone left one 2px
 *  narrower than the other. */
export const BUTTON =
  'w-[26px] rounded-[6px] border py-[1px] text-center text-[12px] leading-[16px]'

export const HOVER = {
  font: { size: 13, color: BRIGHT, family: 'inherit' },
  bgcolor: '#0d1a29',
  bordercolor: '#2a3f57',
  align: 'left' as const,
}

export interface PanelSpec {
  callColumn: string
  putColumn: string
  /** Set for Net Gamma, which draws one signed series instead of two. */
  netColumn?: string
  /** The server's own net column, for the HOVER — always present on every
   *  measure (`net_gex`, `net_dex`, `net_vex`, `net_cex`).
   *
   *  SEPARATE FROM `netColumn`, which decides how the panel is DRAWN (one
   *  signed bar instead of two). This one only decides what the box says, and
   *  every panel has an answer for it.
   *
   *  IT IS READ, NEVER COMPUTED. The hover first showed `call - put`, which
   *  looks obviously right and is wrong: on the delta board the put figures
   *  are already signed, so subtracting them ADDED the put side and a strike
   *  with +36.3M calls and -10.4M puts reported a net of +46.7M instead of
   *  +25.9M. A sign convention is exactly the kind of rule that may only live
   *  in Python (docs/m6_migration_plan.md), and this is what breaking that
   *  rule looks like: a plausible number, on a panel nobody would re-check. */
  netValue?: string
  mirror: boolean
  /** Draw the session-range wicks behind these bars.
   *
   *  ON EVERYWHERE NOW, and this stays a flag rather than becoming an
   *  assumption. `/strikes/session-range` once answered for gamma alone, so
   *  the other five panels had to set this false — an open-interest range
   *  behind a volume-weighted bar is two different quantities on one line,
   *  and behind delta, vanna or charm it is a different Greek entirely. That
   *  constraint is gone: the endpoint takes a `measure` and each panel asks
   *  for its own (core/ranges.py). What has NOT changed is that the caller
   *  must hand the matching range — the flag says "draw them", never "fetch
   *  the right ones".
   *
   *  IT REMAINS EXPLICIT RATHER THAN INFERRED. It started life as
   *  `!spec.netColumn`, which gave the right answer for the wrong reason and
   *  would have gone silently wrong the moment a net view wanted wicks —
   *  which is exactly what happened. */
  wicks: boolean
  /** Draw the traded-volume shade behind these bars. Off for the volume and
   *  open-interest panels themselves: a volume shade behind a volume bar is
   *  the same series drawn twice, which reads as agreement between two
   *  measurements when it is one measurement repeated. */
  shade?: boolean
  /** Start this panel's y axis AT ZERO instead of centring it.
   *
   *  Only for a panel whose bars are all one sign — the stacked volume and
   *  open-interest panels. Every other panel here is a signed exposure read
   *  against a zero line in the middle of the cell, and that centring is what
   *  makes the six grid cells comparable; but a stacked count has nothing
   *  below zero, so centring it throws away half the cell and draws every bar
   *  at half the height it could be. */
  floor?: boolean
  tag: string
  title: string
}

/**
 * One trace holding every strike's wick, as line segments separated by nulls.
 *
 * ONE TRACE, NOT ONE PER STRIKE. A hundred and ten strikes would be a hundred
 * and ten traces, and Plotly redraws all of them on every hover; the null
 * between each pair of points is what breaks the line so the segments do not
 * join up into a zigzag across the chart.
 *
 * `sign` mirrors the wick with the bar it belongs to. A put wick drawn above
 * the axis while its bar hangs below would be the single most misleading thing
 * on the chart — it would read as call-side exposure.
 */
export function wickTrace(
  ranges: SessionRangeRow[],
  lowKey: 'call_low' | 'put_low' | 'net_low',
  highKey: 'call_high' | 'put_high' | 'net_high',
  colour: string,
  sign: 1 | -1,
): Partial<Plotly.PlotData> {
  const x: (number | null)[] = []
  const y: (number | null)[] = []
  for (const row of ranges) {
    x.push(row.strike, row.strike, null)
    y.push(sign * row[lowKey], sign * row[highKey], null)
  }
  return {
    type: 'scatter',
    mode: 'lines',
    x,
    y,
    line: { color: colour, width: 1 },
    hoverinfo: 'skip',
    showlegend: false,
    xaxis: 'x',
    yaxis: 'y',
  }
}

/**
 * The hover's number, for both panels.
 *
 * NO B/M/K SUFFIXES. Abbreviating them here would be a second copy of
 * `core/format.py`'s rounding rule — which already decides that a billion
 * reads "12.2B" rather than Plotly's SI "12.2G", and which the axis ticks on
 * this tab already come from. Two copies of a rounding rule is how a hover
 * comes to disagree with the axis it sits over. The full figure, grouped, is
 * also what MiniPanel's hover shows, so the two boxes read alike.
 */
export function reading(v: number): string {
  return Math.round(v).toLocaleString()
}

/**
 * The fixed 09:30–16:00 band for a session chart's date axis.
 *
 * WHY PIN IT AT ALL. Autorange fits the axis to the data that has arrived, so
 * at 09:41 the whole width of the panel is eleven minutes and every line looks
 * like a decisive move. The same chart at 15:55 draws those eleven minutes as
 * a sliver. Nothing about the market changed between the two readings — only
 * how much of the day had been collected — and a shape that depends on the
 * clock rather than the data is a shape that cannot be compared with itself an
 * hour later, or with a screenshot of yesterday. Fixing the band makes the
 * morning's slope mean the same thing all day, and leaves the empty right-hand
 * side saying the honest thing: the session is not finished.
 *
 * DERIVED FROM THE DATA, NOT FROM THE BROWSER'S CLOCK — the same rule, and the
 * same reason, as `core.expiry.default_scope` and the `market_open` label on
 * /dealer/positioning. The session date AND the UTC offset are both read back
 * out of a timestamp the server already sent, so a reader in London gets the
 * New York session rather than their own afternoon, and the change from EDT to
 * EST in November needs no edit here. Constructing `Date` objects and asking
 * the browser for an offset is precisely the bug this avoids.
 *
 * Returns undefined when there is no row to read, and the caller then leaves
 * the axis autoranged — an empty panel has no session to pin to, and guessing
 * one would draw a full day's axis over no data at all.
 */
export function sessionRange(stamp: string | undefined):
    [string, string] | undefined {
  if (!stamp) return undefined
  // The offset is whatever trails the seconds, taken verbatim: '-04:00' in
  // summer, '-05:00' in winter, and '' or 'Z' if the server's format ever
  // changes. Passing the SAME suffix the data carries means the range and the
  // points go through one parser and cannot land in different frames.
  const m = /^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?(.*)$/.exec(stamp)
  if (!m) return undefined
  return [`${m[1]}T09:30:00${m[2]}`, `${m[1]}T16:00:00${m[2]}`]
}
