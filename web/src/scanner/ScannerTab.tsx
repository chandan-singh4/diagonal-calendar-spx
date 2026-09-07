/**
 * The Scanner tab, rebuilt — the first tab of M6.
 *
 * It draws the same four things `views/scanner.py` does, in the same order:
 * the live count, the non-ATM opportunity cards, the Likely Next cards, and
 * the full sweep table. Every number comes from `api/` and none is derived
 * here; see docs/m6_migration_plan.md for why that rule exists.
 *
 * ONE THING THE STREAMLIT TAB HAS THAT THIS DOES NOT: the custom put/call
 * offset selectors above the table. Those drive `compute_transform_scanner`,
 * which `/mission/scan` does not expose — it serves the standard sweep only.
 * The gap is stated here rather than papered over with a control that
 * silently does nothing, and recorded in the migration plan.
 *
 * THE "NEW" BADGE MEANS SOMETHING DIFFERENT HERE, and deliberately.
 * Streamlit's version compares against whatever the current browser session
 * last saw, held in `st.session_state` — it resets when the tab is closed and
 * cannot be reproduced by a client that keeps no state. `/mission/new`
 * compares against the last snapshot RECORDED IN THE DATABASE, which is the
 * portable question and the one BUG-040 built the endpoint to answer. So a
 * pair badged NEW here appeared since the record was last advanced, not since
 * this tab was opened.
 */
import { useMemo } from 'react'

import { useCards, useNewPairs, useNonAtm, useScan } from '../api/client'
import type { Card } from '../api/types'
import type { EdgeSelection } from '../nav'
import { OpportunityCard } from './OpportunityCard'
import { SweepTable } from './SweepTable'

/** THE WINDOW IS FIXED TO TODAY, AND THAT IS A DELIBERATE MATCH.
 *
 *  This tab first shipped with a Today / 5 / 10 / 20-session picker, because
 *  `/mission/non-atm` takes a `lookback` and the Streamlit page once had the
 *  same control. It no longer does: `views/scanner.py` removed it when the
 *  time-range control moved to the Calendar Edge tab, and pinned the Mission
 *  Control window to "Today". Offering it here meant this screen could show a
 *  different set of cards than the page from the same snapshot — the exact
 *  disagreement between two screens that the whole rebuild is written to
 *  avoid, and the reader would have had no way to tell which was right.
 *
 *  The endpoint still takes wider windows; nothing was removed from the
 *  server. If the picker is wanted back it belongs on BOTH screens, added
 *  deliberately, not left behind on one of them.
 *
 *  In sessions on record, not calendar days — BUG-035 was exactly that
 *  distinction going wrong, "10D" drawing eight sessions.
 */
const LOOKBACK = 1
const LOOKBACK_LABEL = 'Today'

function SectionHeading({
  icon,
  title,
  badge,
  badgeTone,
}: {
  icon: string
  title: string
  badge?: string
  badgeTone?: 'green' | 'plain'
}) {
  return (
    <div className="mt-5 mb-3 flex items-center gap-2">
      <span aria-hidden="true">{icon}</span>
      <h2 className="text-[13px] font-semibold tracking-wide" style={{ color: 'var(--text)' }}>
        {title}
      </h2>
      {badge && (
        <span
          className="mono rounded-[4px] px-2 py-[1px] text-[10px] font-semibold"
          style={
            badgeTone === 'green'
              ? { background: 'rgba(16,212,163,.12)', color: 'var(--green)' }
              : { background: 'var(--bg-raised)', color: 'var(--text-2)' }
          }
        >
          {badge}
        </span>
      )}
    </div>
  )
}

function Notice({ children, tone = 'plain' }: { children: React.ReactNode; tone?: 'plain' | 'error' }) {
  return (
    <p
      className="rounded-[8px] px-3 py-2 text-[12px] leading-relaxed"
      style={{
        background: 'var(--bg-card)',
        border: `1px solid ${tone === 'error' ? 'var(--red)' : 'var(--border)'}`,
        color: tone === 'error' ? 'var(--red)' : 'var(--text-2)',
      }}
    >
      {children}
    </p>
  )
}

function CardGrid({
  cards,
  showLiveBadge,
  showDuration,
  newKeys,
  onOpenEdge,
}: {
  cards: Card[]
  showLiveBadge?: boolean
  showDuration?: boolean
  newKeys: Set<string>
  onOpenEdge: (selection: EdgeSelection) => void
}) {
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(15rem,1fr))' }}>
      {cards.map((card, index) => (
        <OpportunityCard
          key={card.key ?? `${card.front_raw}|${card.back_raw}|${card.put_strike}|${card.call_strike}`}
          card={card}
          rank={index + 1}
          // `front_raw`, not `front_label`: the label carries the DTE in
          // brackets and every endpoint taking an expiry rejects it.
          onOpen={() => onOpenEdge({
            front: card.front_raw,
            back: card.back_raw,
            putStrike: card.put_strike,
            callStrike: card.call_strike,
          })}
          showLiveBadge={showLiveBadge}
          showDuration={showDuration}
          // Set membership on a key BUILT BY THE SERVER. Nothing here knows
          // the key's format, which is the point — see api/computed.pair_key.
          isNew={card.key !== undefined && newKeys.has(card.key)}
        />
      ))}
    </div>
  )
}

export interface ScannerTabProps {
  /** Open Calendar Edge on a pair. ONE CLICK — a card or a row IS the
   *  control, with no confirmation step; see the note in src/App.tsx. */
  onOpenEdge: (selection: EdgeSelection) => void
}

export function ScannerTab({ onOpenEdge }: ScannerTabProps) {

  const scan = useScan(250)
  const cards = useCards()
  const nonAtm = useNonAtm(LOOKBACK)
  const newPairs = useNewPairs()

  // EMPTY WHEN NOTHING CAN BE NEW, and that guard is the whole point.
  //
  // `/mission/new` returns `new_keys` alongside `compared_against_snapshot`,
  // and its docstring says plainly that a null comparison means this is the
  // first recording and nothing can be new yet. Ignoring that field badges
  // EVERY pair as new — which is exactly what the first render of this tab
  // did against the live record: 53 keys, no comparison point, six cards all
  // wearing a NEW badge that meant nothing.
  //
  // The endpoint was right and the client was reading half its answer. A flag
  // that is always on is worse than no flag: it looks like information.
  const newKeys = useMemo(() => {
    const body = newPairs.data
    if (!body || body.compared_against_snapshot === null) return new Set<string>()
    return new Set(body.new_keys)
  }, [newPairs.data])


  // The page shows six non-ATM and three Likely Next. Kept identical so the
  // two screens can be compared card for card while both are running.
  const nonAtmShown = nonAtm.data?.cards.slice(0, 6) ?? []
  const likelyShown = cards.data?.likely_next.slice(0, 3) ?? []

  // NOT a count of live pairs. `cards` is already capped by the server, so
  // counting the live ones inside it reports the cap and calls it a total —
  // it read "20 Live" against a window holding 269. `in_window_total` is the
  // figure the server actually computed, so the badge says what that is.
  const inWindow = nonAtm.data?.in_window_total

  return (
    <main className="mx-auto max-w-[80rem] px-5 pb-16">
      {/* NO TITLE ROW HERE. It said "Scanner" beside the SPX price and the
          snapshot id — and all three now sit above every tab: the tab strip
          names the tab, and the header strip carries the price. Removed at
          Chandan's request (2026-09-06); a second SPX reading on the page is
          a second thing that can be stale. */}

      {/* Each panel reports its own failure. One endpoint being down is not a
          reason to blank the two that answered — a scanner showing nothing
          looks like a quiet market, which is the most expensive thing a
          trading screen can get wrong. */}
      {scan.isError && (
        <div className="mt-4">
          <Notice tone="error">Sweep unavailable — {(scan.error as Error).message}</Notice>
        </div>
      )}

      <SectionHeading
        icon="🔥"
        title="Transform Opportunities"
        badge={inWindow === undefined ? undefined : `${inWindow} in window`}
        badgeTone="green"
      />

      {nonAtm.isError ? (
        <Notice tone="error">
          Non-ATM panel unavailable — {(nonAtm.error as Error).message}
        </Notice>
      ) : nonAtm.isPending ? (
        <Notice>Loading the non-ATM panel…</Notice>
      ) : nonAtmShown.length === 0 ? (
        <Notice>
          No non-ATM transform opportunities have ever crossed the threshold yet — this fills in
          automatically as the registry accumulates history. Scanning continues every refresh.
        </Notice>
      ) : (
        <>
          <SectionHeading
            icon="🟢"
            title={`Non-ATM Opportunities — live or active within ${LOOKBACK_LABEL}`}
          />
          <CardGrid cards={nonAtmShown} showLiveBadge newKeys={newKeys} onOpenEdge={onOpenEdge} />
          <p className="mt-2 text-[11px]" style={{ color: 'var(--text-2)' }}>
            {nonAtm.data.in_window_total > nonAtmShown.length &&
              `Showing top ${nonAtmShown.length} of ${nonAtm.data.in_window_total} in the ${LOOKBACK_LABEL} window. `}
            {nonAtm.data.fallback_used > 0 &&
              `${nonAtm.data.fallback_used} shown above fell outside the ${LOOKBACK_LABEL} window — included so this panel is never empty, marked PAST. `}
            {/* The registry only advances while the Streamlit page is open
                (DEBT-042). Saying so is better than letting a stale count
                read as a live one. */}
            Sightings registry: {nonAtm.data.registry_entries} entries.
          </p>
        </>
      )}

      {likelyShown.length > 0 && (
        <>
          <SectionHeading icon="⏱" title="Likely Next — gap rising, sorted by soonest ETA" />
          <CardGrid cards={likelyShown} showDuration={false} newKeys={newKeys} onOpenEdge={onOpenEdge} />
        </>
      )}

      <div className="mt-8 border-t pt-1" style={{ borderColor: 'var(--border)' }} />

      <SectionHeading
        icon="📋"
        title="Full Scanner — complete sweep"
        badge={
          scan.data
            ? `${scan.data.bands.eligible} ready · Diff ≥ ${scan.data.bands.threshold.toFixed(0)}`
            : undefined
        }
      />

      {scan.isPending ? (
        <Notice>Scanning combinations…</Notice>
      ) : scan.data && scan.data.rows.length > 0 ? (
        <>
          <SweepTable
            rows={scan.data.rows}
            threshold={scan.data.bands.threshold}
            onOpen={(row) => onOpenEdge({
              front: row['Front Raw'],
              back: row['Back Raw'],
              putStrike: row['Put Strike'],
              callStrike: row['Call Strike'],
            })}
          />
          <p className="mt-2 text-[11px]" style={{ color: 'var(--text-2)' }}>
            Showing {scan.data.returned} of {scan.data.bands.total} combinations · green = ready to
            transform (≥ {scan.data.bands.threshold.toFixed(0)}) · click any header to re-sort ·
            click a row to open that pair in Calendar Edge.
            {' '}The custom put/call offset selectors from the Streamlit tab are not here yet —{' '}
            <code>/mission/scan</code> serves the standard sweep only.
          </p>
        </>
      ) : (
        !scan.isError && (
          <Notice>
            No valid combinations found — the current chain has no strike/expiry pairs with marks
            available for all four diagonal legs plus the two wing strikes. The collector may not
            have run yet.
          </Notice>
        )
      )}
    </main>
  )
}
