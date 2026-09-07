/**
 * The four figures above the Calendar Edge charts — `views/edge.py`, the
 * `st.columns(4)` metric row.
 *
 * NOT ONE OF THESE IS COMPUTED HERE, and three of them could not be. An ATM
 * IV is the mean of the call and the put at the strike nearest spot, chosen
 * so a stale quote on one side gets halved instead of believed; the IV Index
 * is a mean of PER-EXPIRY means, so a heavily-quoted weekly cannot outvote a
 * thin monthly. Both rules live in `iv_engine`, and this file reads their
 * answers. The ratio is served for the same reason: dividing the two IVs
 * printed here would be right by luck rather than by rule.
 *
 * N/A IS A REAL ANSWER. An expiry with no IV at its ATM strike happens on a
 * thin chain near the close, and the box says so rather than showing 0.00%.
 */
import type { EdgeHeadline } from '../api/types'

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ minWidth: '9rem', flex: '1 1 9rem' }}>
      <p className="text-[11px]" style={{ color: 'var(--text-2)' }}>{label}</p>
      <p className="mono text-[22px] font-semibold" style={{ color: 'var(--text)' }}>
        {value}
      </p>
    </div>
  )
}

function fmt(value: number | null | undefined, digits: number, suffix = ''): string {
  if (value === null || value === undefined) return 'N/A'
  return `${value.toFixed(digits)}${suffix}`
}

export function EdgeMetrics({ data }: { data: EdgeHeadline | undefined }) {
  return (
    <div className="mb-4 flex flex-wrap gap-4">
      <Metric label="ATM IV Ratio (F/B)" value={fmt(data?.ratio, 4)} />
      <Metric label="Front ATM IV" value={fmt(data?.front_iv, 2, '%')} />
      <Metric label="Back ATM IV" value={fmt(data?.back_iv, 2, '%')} />
      <Metric label="IV Index (avg)" value={fmt(data?.iv_index, 2, '%')} />
    </div>
  )
}
