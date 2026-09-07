/**
 * The strip above every tab — `ui/header.py`, `render` and
 * `_render_liveness_strip`.
 *
 * SPX and its change on the left, VIX / Max |GEX| chips and a staleness dot
 * on the right, and beneath them a wall clock and the age of the newest
 * price counting upward.
 *
 * WHY THE AGE COUNTS UP AND NOT DOWN. This replaced a "next update in: 42s"
 * countdown (M3.4, Chandan's call). A countdown runs toward a moment rather
 * than away from one, so its WORST case — the collector dead, no price for an
 * hour — displays as `0s` and sits there, which reads like everything is
 * fine. Counting upward has no such resting state: the longer it is broken,
 * the louder the number gets.
 *
 * TWO SEPARATE CLAIMS, AND THEY ARE NOT THE SAME CLAIM. The wall clock says
 * THIS PAGE IS ALIVE — it answers "has the dashboard frozen?". The age says
 * THE PRICES ARE ALIVE. The clock's limit is worth being plain about: it
 * ticks in the browser, so it would keep ticking even if the API behind it
 * died. It proves the tab is not frozen. It does not prove the data is
 * fresh, which is why the number beside it exists, and why neither replaces
 * the watchdog that runs whether or not this page is open.
 *
 * WHAT COUNTS AS LATE IS NOT DECIDED HERE. `amber_at` and `red_at` arrive on
 * the response, from `core.session` — the same module the collector and the
 * watchdog read. They depend on which market session is happening right now
 * (60 seconds in the first and last half hour, 300 midday, and no expectation
 * at all when the market is shut, because a collector idle by design is not
 * late). A browser deciding that for itself would eventually disagree with
 * the process it is reporting on.
 */
import { useEffect, useState } from 'react'

import { useHeader } from '../api/client'

const GREEN = '#10d4a3'
const AMBER = '#f0a752'
const RED = '#f05252'
const DIM = '#2f4459'

const DOT_COLOUR = { green: GREEN, amber: AMBER, red: RED } as const

/** Mirrors `_fmt_age` in ui/header.py so the number does not change shape
 *  between the first paint and the first tick. */
function fmtAge(secs: number): string {
  const m = Math.floor(secs / 60)
  const r = secs % 60
  if (m >= 60) return `${Math.floor(m / 60)}h ${m % 60}m`
  return m > 0 ? `${m}m ${r}s` : `${r}s`
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="flex items-baseline gap-[6px] rounded-[5px] px-2 py-[3px]"
      style={{ background: 'var(--bg-raised)' }}
    >
      <span className="text-[10px] uppercase tracking-wide" style={{ color: 'var(--text-3)' }}>
        {label}
      </span>
      <span className="mono text-[12px] font-semibold" style={{ color: 'var(--text)' }}>
        {value}
      </span>
    </div>
  )
}

export function HeaderBar() {
  const { data, dataUpdatedAt } = useHeader()

  // ONE TICKING VALUE, AND THE AGE IS DERIVED FROM IT rather than counted up
  // separately. `dataUpdatedAt` is when this response arrived, so the age is
  // always the server's measurement plus the time since — which means a
  // refetch CORRECTS any drift instead of the drift compounding. A separate
  // counter reset by an effect would have the same reading most of the time
  // and quietly disagree after a sleeping laptop wakes up.
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  // Eastern, to match every other time on this page and the market sessions
  // the thresholds come from.
  const clock = new Date(now).toLocaleTimeString('en-GB', {
    timeZone: 'America/New_York',
    hour12: false,
  })

  const age = data
    ? data.age_seconds + Math.max(0, Math.floor((now - dataUpdatedAt) / 1000))
    : 0
  let ageColour = GREEN
  let note = ''
  if (!data) {
    ageColour = DIM
  } else if (data.market_closed) {
    ageColour = DIM
    note = ' — market closed, collector idle'
  } else if (age >= data.red_at) {
    ageColour = RED
    note = ' — LATE, check the collector'
  } else if (age >= data.amber_at) {
    ageColour = AMBER
    note = ' — later than expected'
  }

  return (
    <header className="px-5 pt-3" style={{ borderBottom: '1px solid var(--border)' }}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <span className="text-[13px] font-semibold tracking-wide" style={{ color: 'var(--text-2)' }}>
            SPX
          </span>
          <span
            className="mono text-[26px] font-semibold"
            style={{ color: data ? data.change.colour : 'var(--text-3)' }}
          >
            {data ? data.spx_price.toLocaleString(undefined, {
              minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
          </span>
          {data && (
            <span className="mono text-[13px]" style={{ color: data.change.colour }}>
              {data.change.arrow} {data.change.points >= 0 ? '+' : ''}
              {data.change.points.toFixed(1)} ({data.change.points >= 0 ? '+' : ''}
              {data.change.percent.toFixed(2)}%)
            </span>
          )}
          {/* The "vs Prev Close 7,748" caption was here and was removed at
              Chandan's request (2026-09-06). `reference_label` is still on
              the response and still says what the change is measured from —
              on the first day of collection that is the session open, not a
              prior close — so anything that needs to name the baseline can,
              without this strip spending a line on it. */}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Chip label="VIX" value={data?.vix != null ? data.vix.toFixed(2) : 'N/A'} />
          <Chip label="Max |GEX|" value={data?.gex_label ?? '—'} />
          <div className="flex items-center gap-[6px]">
            <span
              className="inline-block h-[7px] w-[7px] rounded-full"
              style={{ background: data ? DOT_COLOUR[data.dot] : DIM }}
            />
            <span className="mono text-[11px]" style={{ color: 'var(--text-3)' }}>
              {data ? `${data.snapshot_timestamp.slice(0, 16)} UTC` : 'no snapshot'}
            </span>
          </div>
        </div>
      </div>

      <div className="pt-[2px] pb-1 text-[11px]" style={{ color: DIM }}>
        <span>🕐 {clock} ET</span>
        <span style={{ opacity: 0.45, margin: '0 6px' }}>·</span>
        <span>Time since last data: </span>
        <span className="font-semibold" style={{ color: ageColour }}>
          {data ? fmtAge(age) : '—'}
        </span>
        <span style={{ color: ageColour, opacity: 0.85 }}>{note}</span>
      </div>
    </header>
  )
}
