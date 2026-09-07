/**
 * The Calendar Edge tab — `views/edge.py`, read-only.
 *
 * WHAT IS MISSING, AND WHY IT IS MISSING ON PURPOSE. The Streamlit tab is the
 * only one that WRITES: it creates and clears entry locks, and it backfills
 * the eligibility registry as a side effect of drawing. The read-only API
 * does neither, and adding a write path is not a decision to take while
 * building a screen. So there is no "Lock Entry Here" button here — absent,
 * rather than present and inert, because a button that looks like it locks a
 * trade and does not is worse than no button at all. The position-management
 * framing that a lock switches this chart into is therefore absent too.
 *
 * Everything drawn IS the same as the page's: both frames come from the
 * definitions the Streamlit tab calls, and both were compared row by row
 * against it on the live record before this file existed.
 */
import { useState } from 'react'

import { useAtmPair, useControls, useEdgeHeadline, useTransformMarks } from '../api/client'
import type { EdgeSelection } from '../nav'
import { EdgeMetrics } from './EdgeMetrics'
import { GapChart } from './GapChart'
import { IvChart } from './IvChart'
import { IvDualAxis } from './IvDualAxis'
import { IvScatter } from './IvScatter'
import { PairControls } from './PairControls'

function Note({ children, tone = 'plain' }: { children: React.ReactNode; tone?: 'plain' | 'warn' }) {
  return (
    <p
      className="px-1 py-3 text-[12px]"
      style={{ color: tone === 'warn' ? '#e8b64c' : 'var(--text-2)' }}
    >
      {children}
    </p>
  )
}

function Heading({ icon, title, badge }: { icon: string; title: string; badge?: string }) {
  return (
    <div className="mt-4 mb-2 flex items-center gap-2">
      <span>{icon}</span>
      <span className="text-[13px] font-semibold" style={{ color: 'var(--text)' }}>{title}</span>
      {badge && (
        <span
          className="rounded-[4px] px-2 py-[1px] text-[10px]"
          style={{ background: 'rgba(16,212,163,.14)', color: '#10d4a3' }}
        >
          {badge}
        </span>
      )}
    </div>
  )
}

export function EdgeTab({ initial = null }: { initial?: EdgeSelection | null }) {
  // Seeded from the address when the Scanner drilled through to here, else
  // null, which means "use the server's default for this pair". Held as
  // state rather than read from the URL on every render: once you are on
  // this tab the pickers are yours, and the link that brought you here does
  // not get to keep overriding them.
  const [front, setFront] = useState<string | null>(initial?.front ?? null)
  const [back, setBack] = useState<string | null>(initial?.back ?? null)
  const [putStrike, setPutStrike] = useState<number | null>(initial?.putStrike ?? null)
  const [callStrike, setCallStrike] = useState<number | null>(initial?.callStrike ?? null)
  const [days, setDays] = useState(5)

  const controls = useControls(front, back)

  // The EFFECTIVE selection: whatever has been chosen, else the server's
  // default for the current pair. Held this way rather than copied into state
  // on load, because copying would need an effect that fires on every change
  // of pair and would be one render behind the list it is choosing from.
  const c = controls.data
  const eFront = front ?? c?.front ?? null
  const eBack = back ?? c?.back ?? null
  const ePut = putStrike ?? c?.put_strike ?? null
  const eCall = callStrike ?? c?.call_strike ?? null

  const marks = useTransformMarks(eFront, eBack, eCall, ePut, days)
  const atm = useAtmPair(eFront, eBack, days)
  const headline = useEdgeHeadline(eFront, eBack)

  if (controls.isPending) return <Note>Loading the chain…</Note>
  if (controls.isError) {
    return <Note>Could not read the chain: {(controls.error as Error).message}</Note>
  }
  if (!c) return <Note>Nothing to draw.</Note>

  return (
    <div className="px-5 py-4">
      <PairControls
        data={c}
        front={eFront}
        back={eBack}
        putStrike={ePut}
        callStrike={eCall}
        days={days}
        onChange={(next) => {
          if (next.front !== undefined) setFront(next.front)
          if (next.back !== undefined) setBack(next.back)
          if (next.putStrike !== undefined) setPutStrike(next.putStrike)
          if (next.callStrike !== undefined) setCallStrike(next.callStrike)
          if (next.days !== undefined) setDays(next.days)
        }}
      />

      <EdgeMetrics data={headline.data} />

      <Heading
        icon="🟢"
        title="Diagonal vs. Transform Order Mark"
        badge={marks.data ? `Shaded = Transform Gap ≥ ${marks.data.threshold}` : undefined}
      />

      {marks.isPending ? (
        <Note>Loading the mark history…</Note>
      ) : marks.isError ? (
        <Note>Could not read the marks: {(marks.error as Error).message}</Note>
      ) : marks.data.rows.length === 0 ? (
        // A real state, not an error: this pair has simply never had all six
        // legs recorded together in the window. Said as such.
        <Note>
          No transform-mark history yet for Put {ePut?.toLocaleString()} / Call{' '}
          {eCall?.toLocaleString()} in the selected range.
        </Note>
      ) : (
        <>
          <GapChart
            rows={marks.data.rows}
            rangebreaks={marks.data.rangebreaks ?? []}
            crossings={marks.data.crossings ?? null}
            putStrike={ePut ?? 0}
            callStrike={eCall ?? 0}
            threshold={marks.data.threshold}
            marketOpens={marks.data.market_opens ?? []}
            sessionAxisRange={marks.data.session_axis_range ?? null}
          />
          <p className="mt-1 text-[11px]" style={{ color: 'var(--text-3)' }}>
            Green shading marks every stretch when the position could have been
            transformed — when the iron condor was worth at least{' '}
            {marks.data.threshold} points more than the diagonal being held. Lower
            panel: SPX against the short strikes; ▲▼ mark a strike being crossed.
          </p>
        </>
      )}

      <Heading icon="📐" title="Front vs. Back ATM IV — same axis · IV Ratio by regime" />

      {atm.isPending ? (
        <Note>Loading the IV history…</Note>
      ) : atm.isError ? (
        <Note>Could not read the IV history: {(atm.error as Error).message}</Note>
      ) : atm.data.rows.length === 0 ? (
        <Note>
          No ATM IV recorded for both legs of this pair in the selected range.
        </Note>
      ) : (
        <>
          <IvChart
            rows={atm.data.rows}
            bands={atm.data.bands}
            rangebreaks={atm.data.rangebreaks}
            marketOpens={atm.data.market_opens ?? []}
            sessionAxisRange={atm.data.session_axis_range ?? null}
          />
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
            {atm.data.bands.map((band) => (
              <span key={band.label} style={{ color: band.colour }}>
                ■ {band.label}
              </span>
            ))}
          </div>
          <Heading icon="📈" title="Front ATM IV vs. Back ATM IV — with IV Ratio" />
          <IvDualAxis
            rows={atm.data.rows}
            rangebreaks={atm.data.rangebreaks}
            marketOpens={atm.data.market_opens ?? []}
            sessionAxisRange={atm.data.session_axis_range ?? null}
          />

          {/* Present exactly when there is too little history to trust a
              percentile. The wording is iv_engine's, not this file's. */}
          {atm.data.sample_warning && (
            <Note tone="warn">{atm.data.sample_warning}</Note>
          )}

          <Heading icon="🌀" title="Front vs. Back IV Scatter — intraday trajectory" />
          {/* No domain means an empty frame, which the branch above already
              ruled out — but the type says null, so it is checked rather
              than asserted away. */}
          {atm.data.scatter_domain && (
            <>
              <IvScatter rows={atm.data.rows} domain={atm.data.scatter_domain} />
              <p className="mt-1 text-[11px]" style={{ color: 'var(--text-3)' }}>
                Each dot is one reading, taken every few minutes. Dots above the
                dashed line are times when near-dated options were pricing in more
                movement than far-dated ones; below it is the normal state. Colour
                shows the time of day. If the dots hug one straight line, the
                relationship between the two held steady; if they fan out, it moved
                around independently of how volatile the market was overall.
              </p>
            </>
          )}
        </>
      )}

      <p className="mt-6 text-[11px]" style={{ color: 'var(--text-3)' }}>
        Entry locks are not available on this screen. The Streamlit tab writes them
        to the database; this one is read-only, and a lock button that did nothing
        would be worse than none.
      </p>
    </div>
  )
}
