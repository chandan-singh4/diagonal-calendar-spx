/**
 * Historical Statistics — `views/historical.py`, drawn beneath Strike Detail.
 *
 * Four windows of one question: is today's front/back ATM IV ratio high or
 * low compared with the last day, week, fortnight and month of its own
 * readings. Each column shows the range those readings covered, a marker
 * where today falls inside it, and the percentile.
 *
 * THIS PANEL IS WHY THE TAB HAS TWO HALVES. Strike Detail above says what
 * the ratio IS; this says whether that is unusual. It was a separate tab
 * until the two were joined — a reader comparing them was clicking back and
 * forth holding both in their head.
 *
 * NOTHING HERE IS MEASURED, INCLUDING THE MARKER'S POSITION.
 * `position_pct` is served, because "where does today sit between the low
 * and the high" is a different question from the percentile beside it and
 * would quietly disagree if this file worked it out too — a ratio can sit
 * halfway up its RANGE while being above 90% of its READINGS, and both
 * numbers are true.
 *
 * HIGH IS GREEN AND THAT IS NOT A RECOMMENDATION. Green means the front leg
 * is expensive relative to its own history — the condition this strategy
 * looks for — not that the trade is good. Favorability is unvalidated; the
 * colours come from `iv_engine.percentile_band` and say so there too.
 */
import type { HistoricalWindow } from '../api/types'

const INK = '#2f4459'

function num(value: number | null, digits: number): string {
  return value === null ? 'N/A' : value.toFixed(digits)
}

function Window({ window: w, current }: { window: HistoricalWindow; current: number | null }) {
  const empty = w.observations === 0
  return (
    <div style={{ flex: '1 1 11rem', minWidth: '10rem' }}>
      <p className="mb-1 text-[11px]" style={{ color: 'var(--text-2)' }}>{w.label}</p>
      {empty ? (
        // A real state, said as such. "The ratio has never been lower" and
        // "we have nothing to compare against" must not look the same.
        <p className="text-[11px]" style={{ color: INK }}>No data</p>
      ) : (
        <div className="text-[12px] leading-relaxed">
          <div>
            <span style={{ color: INK }}>Min </span>
            <span className="mono" style={{ color: 'var(--text-2)' }}>{num(w.low, 4)}</span>
          </div>
          <div
            className="relative my-[5px] rounded-[3px]"
            style={{ height: '5px', background: 'linear-gradient(90deg,#0f1e30,#1a2d45)' }}
          >
            <div
              className="absolute rounded-full"
              style={{
                left: `${w.position_pct}%`, top: '-4px',
                width: '13px', height: '13px',
                background: '#f05252', border: '2px solid #060b12',
                transform: 'translateX(-50%)',
              }}
            />
          </div>
          <div>
            <span style={{ color: INK }}>Max </span>
            <span className="mono" style={{ color: 'var(--text-2)' }}>{num(w.high, 4)}</span>
          </div>
          <div className="mt-[2px]">
            <span style={{ color: INK }}>Now </span>
            <span className="mono font-semibold" style={{ color: 'var(--text)' }}>
              {num(current, 4)}
            </span>
            <span className="ml-2 text-[11px]" style={{ color: w.colour }}>
              {w.percentile === null ? '—' : `${w.percentile.toFixed(0)}th`} · {w.band}
            </span>
          </div>
          {/* How much record the percentile rests on. A 90th percentile out
              of eleven readings and one out of two thousand are different
              claims wearing the same number. */}
          <p className="mt-[2px] text-[10px]" style={{ color: INK }}>
            {w.observations.toLocaleString()} readings
          </p>
        </div>
      )}
    </div>
  )
}

export interface HistoricalStatsProps {
  windows: HistoricalWindow[]
  current: number | null
  front: string
  back: string
}

export function HistoricalStats({ windows, current, front, back }: HistoricalStatsProps) {
  return (
    <section className="mt-6 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
      <div className="mb-3 flex items-center gap-2">
        <span>📉</span>
        <span className="text-[13px] font-semibold" style={{ color: 'var(--text)' }}>
          Historical Statistics — ATM IV Ratio
        </span>
        <span
          className="mono rounded-[4px] px-2 py-[1px] text-[10px]"
          style={{ background: 'var(--bg-raised)', color: 'var(--text-2)' }}
        >
          {front} / {back}
        </span>
      </div>
      <div className="flex flex-wrap gap-6">
        {windows.map((w) => (
          <Window key={w.label} window={w} current={current} />
        ))}
      </div>
    </section>
  )
}
