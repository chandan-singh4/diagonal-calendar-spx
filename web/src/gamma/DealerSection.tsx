/**
 * "Dealer structure and positioning" — the last section of the tab.
 *
 * TWO HALVES OF ONE QUESTION, which is why they share a section. The bubbles
 * say WHERE the trading went, across expiry and strike. The table says
 * WHETHER it left anything behind. Read together they separate a level that
 * was genuinely built from one that was merely busy — and busy-without-
 * building is the more common of the two.
 *
 * THEY DISAGREE ABOUT THE EXPIRY PICKER, ON PURPOSE. The bubble chart exists
 * to COMPARE expiries and ignores it; the table compares two sessions at one
 * scope and honours it. Each says which in its own subtitle, because a reader
 * who assumes the picker applies to both would misread whichever is wrong.
 */
import { useState } from 'react'

import { useDealerBubbles, useDealerPositioning } from '../api/client'
import type { BubbleMeasure } from '../api/types'
import { DealerBubbles } from './DealerBubbles'
import { PanelShell } from './PanelShell'
import { Positioning } from './Positioning'

const CHART_HEIGHT = '24rem'
const FULL_HEIGHT = 'calc(100vh - 5.5rem)'

export interface DealerSectionProps {
  expiries: string[]
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-3 py-8 text-[12px]" style={{ color: 'var(--text-2)' }}>
      {children}
    </p>
  )
}

/**
 * The three views of the bubble grid, in reading order.
 *
 * ORDERED BY WHAT THEY ANSWER, not alphabetically: what traded today, what is
 * installed, what today ADDED to what is installed. GEX sits in the middle
 * because it is the one the other two are each a variation on.
 *
 * The note under the buttons is deliberately about what the view MEANS rather
 * than how it is computed — `basis`, rendered under the chart, already
 * carries the arithmetic, and repeating it here would be two captions to keep
 * in step.
 */
const MEASURES: { key: BubbleMeasure; label: string; note: string }[] = [
  { key: 'volume', label: 'Volume',
    note: 'where the trading went today — bubble size is contracts traded' },
  { key: 'gex', label: 'Gamma (GEX)',
    note: 'dealer gamma already installed — green damps a move, red '
        + 'amplifies it; size is the net figure' },
  { key: 'vgex', label: 'Gamma from today (vGEX)',
    note: 'gamma this session added, not what was already there — sparse at '
        + 'the open and fills through the day' },
]

function Heading({ title, note }: { title: string; note: string }) {
  return (
    <div className="mb-2 flex items-baseline gap-3">
      <p className="text-[11px] uppercase tracking-wide"
         style={{ color: 'var(--text-2)' }}>{title}</p>
      <p className="text-[11px]" style={{ color: 'var(--text-3)' }}>{note}</p>
    </div>
  )
}

export function DealerSection({ expiries }: DealerSectionProps) {
  const [measure, setMeasure] = useState<BubbleMeasure>('volume')
  const bubbles = useDealerBubbles(measure)
  const table = useDealerPositioning(expiries)
  const [big, setBig] = useState(false)
  const chosen = MEASURES.find((m) => m.key === measure)!

  return (
    <div className="mt-5 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
      <p className="mb-3 text-[11px] uppercase tracking-wide"
         style={{ color: 'var(--text-2)' }}>
        Dealer structure and positioning
      </p>

      {/* THE TITLE FOLLOWS THE MEASURE. "Where the trading went" over a
          gamma chart would be a false caption on the one panel whose whole
          risk is being read as an order-flow chart. */}
      <Heading title={measure === 'volume' ? 'Where the trading went'
                                           : 'Where dealer gamma sits'}
               note="every expiry — this panel ignores the expiry picker" />

      <div className="mb-2 flex flex-wrap items-center gap-2">
        {MEASURES.map((m) => {
          const on = m.key === measure
          return (
            <button
              key={m.key}
              type="button"
              onClick={() => setMeasure(m.key)}
              aria-pressed={on}
              className="rounded-[6px] border px-2 py-[2px] text-[11px]"
              style={{
                background: on ? 'rgba(16,212,163,.12)' : 'var(--bg-card)',
                borderColor: on ? '#10d4a3' : 'var(--border)',
                color: on ? '#10d4a3' : 'var(--text-2)',
              }}
            >
              {m.label}
            </button>
          )
        })}
        <span className="text-[11px]" style={{ color: 'var(--text-3)' }}>
          {chosen.note}
        </span>
      </div>

      <PanelShell big={big} onBig={() => setBig((v) => !v)} hold={CHART_HEIGHT}>
        {bubbles.isError ? (
          <Note>Could not load this panel: {(bubbles.error as Error).message}</Note>
        ) : !bubbles.data ? (
          <Note>Loading…</Note>
        ) : bubbles.data.rows.length === 0 ? (
          // NOT AN ERROR. Early in a session nothing near spot has traded yet,
          // and this panel fills in as the day goes on.
          // vGEX is the one that is legitimately empty at 09:30 — it is
          // weighted by today's volume, so an empty panel then is the measure
          // behaving correctly rather than a fault. Saying so beats a reader
          // concluding the feature is broken on its first morning.
          <Note>
            {measure === 'volume'
              ? `No volume within ${bubbles.data.band_percent}% of spot in `
                + 'this snapshot yet. This panel fills in as the session '
                + 'trades.'
              : `No gamma within ${bubbles.data.band_percent}% of spot in `
                + 'this snapshot yet.'
                + (measure === 'vgex'
                   ? ' vGEX is weighted by today’s volume, so it is empty at'
                     + ' the open and fills as the session trades.'
                   : '')}
          </Note>
        ) : (
          <DealerBubbles
            rows={bubbles.data.rows}
            spot={bubbles.data.spot}
            measure={bubbles.data.measure}
            basis={bubbles.data.basis}
            height={big ? FULL_HEIGHT : CHART_HEIGHT}
          />
        )}
      </PanelShell>

      <div className="mt-5">
        <Heading title="What it left behind"
                 note="the overnight change in open interest — yesterday's" />
        <div className="rounded-[10px] border p-1"
             style={{ background: 'var(--bg-card)',
                      borderColor: 'var(--border)' }}>
          {table.isError ? (
            <Note>Could not load this panel: {(table.error as Error).message}</Note>
          ) : !table.data ? (
            <Note>Loading…</Note>
          ) : table.data.rows.length === 0 ? (
            <Note>No strike within 2.5% of spot has traded yet today.</Note>
          ) : (
            <Positioning
              rows={table.data.rows}
              spot={table.data.spot}
              atmStrike={table.data.atm_strike}
              dayLabels={table.data.day_labels}
              glossary={table.data.glossary}
              basis={table.data.basis}
            />
          )}
        </div>
      </div>
    </div>
  )
}
