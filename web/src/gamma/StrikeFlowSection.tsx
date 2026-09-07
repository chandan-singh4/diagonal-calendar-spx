/**
 * The strike-flow section: its heading, its strike picker, and the panel.
 *
 * WHY IT IS NOT PART OF `ExposureGrid`. It was, and that made it grid-only —
 * "when I go from grid to detailed view, I only see gamma exposure, volume,
 * and open interest chart, I don't see the strike flow chart" (Chandan,
 * 2026-09-06). The bug was structural rather than a missing line: the section
 * lived inside the component that draws the six-panel grid, so switching to
 * the detail view unmounted it along with the grid.
 *
 * Lifting it here makes the relationship explicit. Grid and Detail are two
 * ways of reading ONE SNAPSHOT across strikes; strike flow is one strike read
 * across TIME, and it answers the same question whichever of the two is above
 * it. So it sits below both, owned by the tab rather than by either view.
 *
 * IT KEEPS ITS OWN `Cell` CHROME — the maximise and reset buttons — because
 * those belong to a panel, not to a grid.
 */
import { useState } from 'react'

import { useStrikeFlow } from '../api/client'
import type { GammaResponse } from '../api/types'
import { PanelShell } from './PanelShell'
import { StrikeFlow } from './StrikeFlow'

const FLOW_ID = 'strike-flow'
const FLOW_HEIGHT = '20rem'
const FULL_HEIGHT = 'calc(100vh - 5.5rem)'

export interface StrikeFlowSectionProps {
  gamma: GammaResponse
  expiries: string[]
}

export function StrikeFlowSection({ gamma, expiries }: StrikeFlowSectionProps) {
  // WHICH STRIKE THE PANEL FOLLOWS. Null until the first response, and then
  // `summary.peak_strike` — the largest absolute exposure on the board, which
  // is the server's own answer to "the interesting strike" and the figure the
  // header already shows as MAX |GEX|. So the panel opens on something worth
  // looking at without this file deciding what "interesting" means.
  //
  // NOT `flip_strike`, which was the obvious choice and is the wrong one: the
  // flip is an INTERPOLATED PRICE LEVEL (7829.647...), not a listed strike.
  // Asked for it, the endpoint answers 200 with no rows — there are no
  // snapshots at a strike that does not exist — and the panel sits on
  // "Loading" while the <select> beneath it, finding no matching <option>,
  // quietly displays the first strike on the board instead. Nothing errors
  // anywhere, and the control disagrees with the request it is making.
  const [strike, setStrike] = useState<number | null>(null)
  const [big, setBig] = useState(false)
  const chosen = strike ?? gamma.summary?.peak_strike ?? null
  const query = useStrikeFlow(chosen, expiries)

  const height = big ? FULL_HEIGHT : FLOW_HEIGHT

  return (
    <div className="mt-5 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
      <div className="mb-2 flex items-center gap-4">
        <p className="text-[11px] uppercase tracking-wide"
           style={{ color: 'var(--text-2)' }}>
          Strike flow — one strike, through the day
        </p>
        <label className="flex items-center gap-2 text-[12px]"
               style={{ color: 'var(--text-2)' }}>
          Strike
          <select
            value={chosen ?? ''}
            onChange={(e) => setStrike(Number(e.target.value))}
            className="rounded-[6px] border px-2 py-[3px] text-[12px]"
            style={{ background: 'var(--bg-card)', borderColor: 'var(--border)',
                     color: 'var(--text)' }}
          >
            {gamma.rows.map((r) => (
              <option key={r.strike} value={r.strike}>
                {r.strike.toLocaleString()}
              </option>
            ))}
          </select>
        </label>
      </div>
      <PanelShell big={big} onBig={() => setBig((v) => !v)}
                  hold={FLOW_HEIGHT} id={FLOW_ID}>
        {query.isError ? (
          <p className="px-3 py-8 text-[12px]" style={{ color: 'var(--text-2)' }}>
            Could not load the flow for {chosen}:{' '}
            {(query.error as Error).message}
          </p>
        ) : !query.data ? (
          <p className="px-3 py-8 text-[12px]" style={{ color: 'var(--text-2)' }}>
            Loading strike flow…
          </p>
        ) : query.data.rows.length === 0 ? (
          // NOT AN ERROR. A strike with no snapshots is a real state — a
          // contract that has not traded, or a session that has not started.
          <p className="px-3 py-8 text-[12px]" style={{ color: 'var(--text-2)' }}>
            No snapshots at {chosen?.toLocaleString()} for this session.
          </p>
        ) : (
          <StrikeFlow
            rows={query.data.rows}
            strike={query.data.strike}
            basis={query.data.basis}
            height={height}
          />
        )}
      </PanelShell>
    </div>
  )
}
