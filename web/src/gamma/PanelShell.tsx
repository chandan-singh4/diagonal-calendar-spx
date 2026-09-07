/**
 * The card a panel sits in, and the full-screen overlay it becomes.
 *
 * EXTRACTED FROM `ExposureGrid.Cell` so the strike-flow section could use the
 * same chrome without importing the grid. The two had already diverged in one
 * way that mattered: the grid's cells get their maximise button from
 * `MiniPanel`, which owns the pair (⤢ and ⟲) so the two cannot fall out of
 * alignment — but the flow panel does not render a `MiniPanel`, so it had no
 * maximise button at all. `onBig` here fills that gap, wearing the same
 * `BUTTON` class as the grid's so the three look like one control.
 *
 * A PLACEHOLDER STAYS IN THE FLOW while a panel is maximised, holding exactly
 * the height the panel had, so the page does not jump when the overlay closes.
 */
import { useEffect } from 'react'

import { BUTTON } from './chart'

export interface PanelShellProps {
  children?: React.ReactNode
  big: boolean
  /** Omit where the child renders its own maximise button — the grid's cells
   *  do, because MiniPanel keeps ⤢ and ⟲ in one flex row. */
  onBig?: () => void
  /** Height the placeholder holds open while this panel is maximised. */
  hold: string
  id?: string
}

export function PanelShell({ children, big, onBig, hold }: PanelShellProps) {
  // Escape closes it. A full-screen overlay with no keyboard way out is a
  // trap — and Escape is what anyone tries first.
  useEffect(() => {
    if (!big || !onBig) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onBig() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [big, onBig])

  const panel = (
    <div className="relative h-full rounded-[10px] border p-1"
         style={{ background: 'var(--bg-card)', borderColor: 'var(--border)' }}>
      {onBig && (
        <button
          type="button"
          onClick={onBig}
          title={big ? 'Back to the page (Esc)' : 'Maximise this chart'}
          aria-label={big ? 'Back to the page' : 'Maximise this chart'}
          aria-pressed={big}
          className={`absolute right-2 top-2 z-20 ${BUTTON}`}
          style={{ background: 'var(--bg-card)', borderColor: 'var(--border)',
                   color: big ? '#10d4a3' : 'var(--text-2)' }}
        >
          {big ? '⤡' : '⤢'}
        </button>
      )}
      {children}
    </div>
  )

  if (!big) return panel
  return (
    <>
      <div style={{ height: hold }} />
      <div className="fixed inset-0 z-40 p-3" style={{ background: 'var(--bg)' }}>
        {panel}
      </div>
    </>
  )
}
