/**
 * The frame every time-axis chart on Calendar Edge is drawn in.
 *
 * WHY THIS FILE EXISTS. Three charts on this tab share one x-axis — the
 * session clock — and are read by scanning DOWN the page: 16:01 on the marks
 * chart must sit directly above 16:01 on the IV charts, or the eye pairs a
 * mark with the wrong minute's IV.
 *
 * MATCHING THE RANGE IS NOT ENOUGH, AND THAT WAS THE BUG (Chandan, four
 * times). All three already drew the identical `session_axis_range`; they
 * still did not line up, because Plotly maps that range onto the plotting
 * area, and the plotting area is the container minus the MARGINS. GapChart
 * and IvChart used `r: 20`; IvDualAxis needs room on the right for its Ratio
 * axis and used `r: 58`. Same range, a plot area 38px narrower, so its last
 * minute landed 38px to the left of the same minute above it. A correct range
 * inside a different frame is still a misaligned chart.
 *
 * WHY THESE NUMBERS. `r` is the widest of the three requirements, not the
 * narrowest: the dual-axis chart genuinely needs 58px for its right-hand tick
 * labels, so the other two are widened to match rather than it being narrowed
 * to clip. `l` fits a four-digit SPX tick plus its axis title.
 *
 * `automargin` IS LEFT OFF DELIBERATELY on every axis in these charts. It
 * expands the margin to fit whatever labels a particular day's data produces
 * — which is the same class of fault: a frame that moves with the data. A
 * five-digit SPX would widen one chart's left margin and no other's.
 */
export const TIME_AXIS_MARGIN = { l: 58, r: 58, t: 30, b: 40 } as const
