/**
 * The row of figures above the chart — `views/gex.py:_draw_headline`.
 *
 * EVERY VALUE HERE IS A STRING THE SERVER FORMATTED. `labels` comes from
 * `api/computed.exposure_labels`, which calls the same `core.format.fmt_money`
 * the Streamlit strip calls, so "12.2B" is the same twelve-point-two-billion
 * on both screens. Nothing below divides, rounds or picks a suffix.
 *
 * THE COLOURS ARE DECIDED FROM THE RAW `summary`, not from the label text.
 * Reading a minus sign out of a formatted string to choose red would work
 * until the day a figure is formatted differently, and would fail silently
 * when it did.
 */
const CALL = '#10d4a3'
const PUT = '#f05252'
const FLIP = '#e8b64c'
const BRIGHT = '#dde6f1'

function Metric({
  label,
  value,
  colour = BRIGHT,
}: {
  label: string
  value: string
  colour?: string
}) {
  return (
    <div className="flex flex-col gap-[2px]">
      <span className="text-[10px] tracking-wider uppercase" style={{ color: 'var(--text-2)' }}>
        {label}
      </span>
      <span className="mono text-[15px] font-semibold" style={{ color: colour }}>
        {value}
      </span>
    </div>
  )
}

/** Green when the figure is positive, red when negative, plain when absent.
 *  `undefined` and `null` both mean "no number", which is not zero. */
function signColour(value: number | null | undefined): string {
  if (value === null || value === undefined) return BRIGHT
  return value >= 0 ? CALL : PUT
}

export interface HeadlineStripProps {
  /** Always the gamma summary and labels — the gamma figures stay on screen
   *  even when a derived view is selected. Vanna and charm are read AGAINST
   *  the gamma picture, not instead of it: "dealers are short gamma here and
   *  get shorter delta as vol rises" is one sentence needing both halves. */
  summary: Record<string, number | null>
  labels: Record<string, string>
  /** The selected measure's summary and labels, when it is vgex, vanna or
   *  charm. Appended to the strip, never swapped in. */
  second?: { summary: Record<string, number | null>; labels: Record<string, string> }
  measure: string
}

export function HeadlineStrip({ summary, labels, second, measure }: HeadlineStripProps) {
  const extra: { label: string; value: string; colour?: string }[] = []

  if (second && measure === 'vgex') {
    // BESIDE the GEX figures, not instead of them — and the reason is the
    // whole use of vGEX. The measure earns its place by DISAGREEING with
    // GEX: a flip level that has moved says today's flow is repricing a
    // level before open interest catches up, and one that has not says the
    // installed structure is holding. Swapping the numbers in would leave
    // nothing to compare and the strip would just look like the gamma one.
    extra.push(
      { label: 'Net vGEX', value: second.labels.net_gex ?? '—',
        colour: signColour(second.summary.net_gex) },
      // (chain), like the gamma one beside it: the level is the whole
      // board's even when one expiry is selected. See api/computed.py.
      { label: 'vGEX flip (chain)', value: second.labels.flip_strike ?? '—',
        colour: FLIP },
    )
  } else if (second && measure === 'vanna') {
    extra.push(
      { label: 'Net VEX', value: second.labels.net_vex ?? '—',
        colour: signColour(second.summary.net_vex) },
      { label: 'Peak vanna', value: second.labels.peak_vex_strike ?? '—' },
    )
  } else if (second && measure === 'charm') {
    extra.push(
      { label: 'Net CEX', value: second.labels.net_cex ?? '—',
        colour: signColour(second.summary.net_cex) },
      { label: 'Peak charm', value: second.labels.peak_cex_strike ?? '—' },
    )
  }

  return (
    /* SPREAD ACROSS THE STRIP, not bunched at the left (Chandan,
       2026-09-07). The row used to be `flex flex-wrap`, which packs every
       figure against the left edge and leaves the right two-thirds of a wide
       card empty — the numbers read as a huddle rather than as a dashboard.

       A GRID RATHER THAN `justify-between`, and the difference only shows
       when the strip wraps. `justify-between` spreads each LINE on its own,
       so a second line holding two figures flings them to opposite edges of
       the card with a chasm between; the columns also stop lining up between
       lines. `auto-fit` builds as many equal tracks as fit and collapses the
       empty ones, so the figures share the full width evenly however many
       there are — and there are seven, nine or eleven here depending on the
       selected measure.

       The text stays LEFT-ALIGNED inside each column. Centring the pairs
       would leave each label sitting at a different indent from the number
       beneath it, since a label and its value are rarely the same width. */
    <div
      className="mb-3 grid gap-x-6 gap-y-3 rounded-[10px] border px-4 py-3"
      style={{
        background: 'var(--bg-card)',
        borderColor: 'var(--border)',
        gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))',
      }}
    >
      <Metric label="Net GEX" value={labels.net_gex ?? '—'} colour={signColour(summary.net_gex)} />
      {/* Green when positive gamma dominates, red when negative — the
          vendor's own colouring, and the sign of the ratio already carries
          which regime it is. */}
      <Metric label="GEX Ratio" value={labels.ratio ?? '—'} colour={signColour(summary.ratio)} />
      <Metric label="Sentiment" value={labels.sentiment ?? '—'} />
      <Metric label="Call GEX" value={labels.call_gex ?? '—'} colour={CALL} />
      <Metric label="Put GEX" value={labels.put_gex ?? '—'} colour={PUT} />
      {/* The label already reads "7,720 (Put)" — the side is joined to the
          strike server-side, so this does not pair a number with a word. */}
      <Metric label="Peak strike" value={labels.peak_strike ?? '—'} />
      {/* THE SCOPE IS IN THE LABEL because it differs from every other
          figure in this strip. Net GEX, the ratio and the peak all describe
          the selected expiry; the flip describes the whole chain, which is
          what the published definition means by it (Chandan, 2026-09-07).
          A level quoted without its scope is how the number gets read as
          belonging to the bars beside it. */}
      <Metric label="Gamma flip (chain)" value={labels.flip_strike ?? '—'}
              colour={FLIP} />
      {extra.map((item) => (
        <Metric key={item.label} label={item.label} value={item.value} colour={item.colour} />
      ))}
    </div>
  )
}
