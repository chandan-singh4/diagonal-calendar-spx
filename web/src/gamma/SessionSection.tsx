/**
 * The "Through the session" section — the two time panels, stacked.
 *
 * WHY THEY SHARE A SECTION. They are the same eight-ish strikes asked two
 * questions: how much traded there, and how much gamma sits there. Read one
 * above the other you can see a level being worked before the gamma follows,
 * which is the whole reason the Streamlit tab drew them together and the
 * reason they are not two independent panels here.
 *
 * IT SITS BELOW BOTH VIEWS, like the strike-flow section and for the same
 * reason: Grid and Detail are two ways of reading ONE SNAPSHOT across strikes,
 * and these are strikes read across TIME. The question they answer does not
 * change with the view above them.
 *
 * THE GAMMA PANEL IS 0DTE. `dte_max=0` is what makes it the flow panel rather
 * than a second view of the board the bars above already show — the point of
 * it is the options that expire today, whose gamma moves fastest and matters
 * most into the close.
 */
import { useState } from 'react'

import { useGexTimeline, useNetVolume } from '../api/client'
import type { GexTotals } from '../api/types'
import { CALL, PUT, reading } from './chart'
import { PanelShell } from './PanelShell'
import { SessionLines } from './SessionLines'

const PANEL_HEIGHT = '20rem'
const FULL_HEIGHT = 'calc(100vh - 5.5rem)'

/** 0DTE, and nothing else. Not a magic number so much as the definition of
 *  the panel — see the header. */
const ZERO_DTE = 0

export interface SessionSectionProps {
  expiries: string[]
}

function Caption({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-3 py-8 text-[12px]" style={{ color: 'var(--text-2)' }}>
      {children}
    </p>
  )
}

/** One titled panel with its own maximise button, wrapping whatever state the
 *  query is in. Extracted because the two below differ only in their query and
 *  their words, and a copy-pasted loading branch is how two panels come to
 *  disagree about what "no data" looks like. */
function Panel({ title, note, strip, children, empty, loading, failed }: {
  title: string
  note: string
  /** The headline figures, when the panel has any. */
  strip?: React.ReactNode
  /** Given the height the chart should fill. A RENDER PROP rather than a
   *  wrapper div, because the chart is a fixed-height Plotly node with a
   *  caption beneath it — nested inside a div of the panel's height the pair
   *  overflows the card by exactly the caption. */
  children: (height: string) => React.ReactNode
  empty: boolean
  loading: boolean
  failed: Error | null
}) {
  const [big, setBig] = useState(false)
  return (
    <div>
      <div className="mb-2 flex items-baseline gap-3">
        <p className="text-[11px] uppercase tracking-wide"
           style={{ color: 'var(--text-2)' }}>
          {title}
        </p>
        <p className="text-[11px]" style={{ color: 'var(--text-3)' }}>{note}</p>
        {strip}
      </div>
      <PanelShell big={big} onBig={() => setBig((v) => !v)} hold={PANEL_HEIGHT}>
        {failed ? (
          <Caption>Could not load this panel: {failed.message}</Caption>
        ) : loading ? (
          <Caption>Loading…</Caption>
        ) : empty ? (
          // NOT AN ERROR. Before the first snapshot of the day there is
          // nothing to draw, and that is a real state rather than a fault.
          <Caption>No snapshots yet for this session.</Caption>
        ) : (
          children(big ? FULL_HEIGHT : PANEL_HEIGHT)
        )}
      </PanelShell>
    </div>
  )
}

/**
 * The headline strip: where 0DTE gamma sits now, and how far it has moved.
 *
 * READ, NOT SUMMED. The server computes these from the very lines it returns
 * (`totals` on the response), so the strip and the chart cannot disagree.
 * Adding up `rows` here would be a second definition of the same figure —
 * and null would quietly become 0, which claims the board is flat when the
 * truth is that nobody has looked.
 */
function Totals({ totals }: { totals?: GexTotals }) {
  if (!totals || totals.now === null) return null
  const change = totals.change
  return (
    <p className="text-[11px]" style={{ color: 'var(--text-3)' }}>
      now <span style={{ color: 'var(--text)' }}>{reading(totals.now)}</span>
      {change !== null && (
        <>
          {'  ·  '}
          <span style={{ color: change >= 0 ? CALL : PUT }}>
            {change >= 0 ? '+' : ''}{reading(change)}
          </span>
          {' since the open'}
        </>
      )}
    </p>
  )
}

export function SessionSection({ expiries }: SessionSectionProps) {
  const volume = useNetVolume(expiries)
  // THE GAMMA PANEL IGNORES THE EXPIRY PICKER, deliberately: it is defined by
  // being 0DTE, and a picker set to next month would leave it drawing nothing
  // under a heading that promises today's flow. The heading says so.
  const gamma = useGexTimeline([], undefined, ZERO_DTE)

  return (
    <div className="mt-5 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
      <p className="mb-3 text-[11px] uppercase tracking-wide"
         style={{ color: 'var(--text-2)' }}>
        Through the session
      </p>
      <div className="flex flex-col gap-5">
        <Panel
          title="Net volume — busiest strikes"
          note="calls minus puts, running total"
          loading={!volume.data && !volume.isError}
          failed={volume.isError ? (volume.error as Error) : null}
          empty={volume.data?.rows.length === 0}
        >
          {(height) => volume.data && (
            <SessionLines
              rows={volume.data.rows}
              valueKey="net_volume"
              units="contracts"
              format={reading}
              basis={volume.data.basis}
              height={height}
            />
          )}
        </Panel>
        <Panel
          title="0DTE net gamma — top strikes"
          note="expiring today, whatever the expiry picker says"
          strip={<Totals totals={gamma.data?.totals} />}
          loading={!gamma.data && !gamma.isError}
          failed={gamma.isError ? (gamma.error as Error) : null}
          empty={gamma.data?.rows.length === 0}
        >
          {(height) => gamma.data && (
            <SessionLines
              rows={gamma.data.rows}
              valueKey="net_gex"
              units="dollars per 1% move"
              format={reading}
              basis={gamma.data.basis}
              height={height}
            />
          )}
        </Panel>
      </div>
    </div>
  )
}
