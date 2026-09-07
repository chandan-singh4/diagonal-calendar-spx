/**
 * Lock the entry — the one control on this screen that changes anything.
 *
 * WHAT LOCKING IS FOR. Once a diagonal is actually filled, "what would a new
 * diagonal cost today" stops being the useful number. The trader wants their
 * FIXED entry against the live Transform Order Mark, so the chart above
 * switches from discovery to position management for this exact combo.
 * `views/edge.py` has done this since long before the rebuild; this is the
 * new screen catching up, against the same file, so the two screens cannot
 * disagree about which positions are open.
 *
 * IT CONFIRMS BEFORE IT WRITES, and not out of politeness. The collector
 * reads `entry_locks.json` to choose which strikes it fetches next cycle
 * (collector.py:649), so a stray lock changes what tomorrow's record
 * contains — and the record is the product. A one-click write on a phone is
 * the wrong shape for that.
 *
 * THE PRICE IS THE ONE ON SCREEN, taken from the last row the chart drew and
 * shown in the confirmation before anything is sent. It is passed to the
 * server rather than recomputed there: the trader is asserting the mark they
 * saw, and by the time the request lands the newest snapshot may be a
 * different minute.
 *
 * "ALL LOCKS" IS PAGE CHROME, NOT PART OF A CHART, and it is here because a
 * lock was unreachable without it: locking a combo and then changing the
 * strike or the expiry left the position with nowhere to be seen (Chandan,
 * 2026-09-07 — "how do I see what are my locked entries?"). The old screen
 * has the same list in its page header, and `ui/locks.py` says why it belongs
 * at page level: it manages positions ACROSS combos, so it must not read as
 * belonging to whichever chart happens to be showing.
 *
 * NO NUMBER IS FORMATTED INTO A DECISION HERE. The mark is displayed to two
 * places, which is presentation; the mode strings, the key and the purge rule
 * are the server's, because "no formula may exist in the new language".
 */
import { useState } from 'react'

import { useClearLock, useCreateLock, useLocks } from '../api/client'
import type { ApiError } from '../api/client'

/** What the "view" button on a listed lock asks the tab to do. */
export interface LockSelection {
  frontExpiry: string
  backExpiry: string
  putStrike: number
  callStrike: number
}

interface Props {
  frontExpiry: string | null
  backExpiry: string | null
  putStrike: number | null
  callStrike: number | null
  /** The newest diagonal mark on the chart, or null when it drew nothing. */
  currentMark: number | null
  /** Point the tab at another locked combo. Without this the list would show
   *  positions with no way to reach them. */
  onSelect: (selection: LockSelection) => void
}

/** `locked_at` is an ISO stamp already in New York time (the server writes it
 *  in the display timezone). Rendered with the timezone STRIPPED rather than
 *  parsed as local: `new Date(...)` on a machine set to another zone would
 *  shift the hour, which is exactly the fault that put London time on two of
 *  this tab's charts (progress log 16). */
function whenLocked(stamp: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(stamp)
  if (!match) return stamp
  const [, , month, day, hour, minute] = match
  const h = Number(hour)
  const suffix = h < 12 ? 'AM' : 'PM'
  const twelve = h % 12 === 0 ? 12 : h % 12
  return `${month}/${day} ${twelve}:${minute} ${suffix}`
}

export function EntryLockBar({
  frontExpiry, backExpiry, putStrike, callStrike, currentMark, onSelect,
}: Props) {
  const [confirming, setConfirming] = useState(false)
  const [listing, setListing] = useState(false)
  const [mode, setMode] = useState<'monitor_only' | 'monitor_and_log'>('monitor_only')

  const locks = useLocks()
  const create = useCreateLock()
  const clear = useClearLock()

  const ready =
    frontExpiry !== null && backExpiry !== null &&
    putStrike !== null && callStrike !== null
  if (!ready) return null

  const combo = {
    front_expiry: frontExpiry,
    back_expiry: backExpiry,
    put_strike: putStrike,
    call_strike: callStrike,
  }

  // MATCHED ON THE SERVER'S OWN FIELDS, not on a key rebuilt here. The key
  // format is Python's; comparing the four values it is built from asks the
  // same question without owning a second copy of the answer.
  const lock = locks.data?.locks.find(
    (candidate) =>
      candidate.front_expiry === frontExpiry &&
      candidate.back_expiry === backExpiry &&
      candidate.put_strike === putStrike &&
      candidate.call_strike === callStrike,
  )

  const failure = (create.error ?? clear.error) as ApiError | null
  const busy = create.isPending || clear.isPending

  return (
    <div className="mb-3">
      <div className="flex flex-wrap items-center gap-3">
        {lock ? (
          <>
            <span
              className="mono rounded px-2 py-1 text-[12px] font-semibold"
              style={{ background: 'rgba(16,212,163,.12)', color: '#10d4a3' }}
            >
              ● Entry locked: ${lock.entry_diagonal_mark.toFixed(2)} @{' '}
              {whenLocked(lock.locked_at)}
            </span>
            <button
              type="button"
              className="rounded px-2 py-1 text-[12px]"
              style={{ border: '1px solid var(--line)', color: 'var(--text-2)' }}
              disabled={busy}
              onClick={() => clear.mutate(combo)}
            >
              {clear.isPending ? 'Clearing…' : 'Clear'}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="rounded px-2 py-1 text-[12px]"
            style={{ border: '1px solid var(--line)', color: 'var(--text)' }}
            // Nothing to freeze when the chart drew nothing. Locking a null
            // would store the absence of a price as a price.
            disabled={currentMark === null || busy}
            onClick={() => setConfirming(true)}
          >
            🔒 Lock Entry Here
          </button>
        )}
        {/* Always present, whether or not THIS combo is locked -- that is the
            whole point of it. The count is on the button so a held position
            is visible without opening anything. */}
        <button
          type="button"
          className="rounded px-2 py-1 text-[12px]"
          style={{ border: '1px solid var(--line)', color: 'var(--text-2)' }}
          onClick={() => setListing((open) => !open)}
        >
          🔒 All Locks ({locks.data?.count ?? 0})
        </button>
        {locks.isError && (
          <span className="text-[11px]" style={{ color: 'var(--text-3)' }}>
            Could not read the locks: {(locks.error as Error).message}
          </span>
        )}
      </div>

      {listing && (
        <div className="mt-2 rounded p-3" style={{ border: '1px solid var(--line)' }}>
          {(locks.data?.locks.length ?? 0) === 0 ? (
            <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>
              Nothing locked yet. Use “Lock Entry Here” once you are in a position.
            </p>
          ) : (
            locks.data?.locks.map((entry) => {
              const viewing =
                entry.front_expiry === frontExpiry &&
                entry.back_expiry === backExpiry &&
                entry.put_strike === putStrike &&
                entry.call_strike === callStrike
              return (
                <div key={entry.lock_id} className="mb-2 flex flex-wrap items-center gap-3">
                  <span className="mono text-[12px]" style={{
                    color: 'var(--text)',
                    fontWeight: viewing ? 700 : 400,
                  }}>
                    Put {entry.put_strike.toLocaleString()} / Call{' '}
                    {entry.call_strike.toLocaleString()}
                    {viewing && (
                      <span style={{ color: '#10d4a3', fontSize: '.68rem', fontWeight: 600 }}>
                        {' '}● viewing
                      </span>
                    )}
                  </span>
                  <span className="mono text-[11px]" style={{ color: 'var(--text-2)' }}>
                    {entry.front_expiry} → {entry.back_expiry} · Entry $
                    {entry.entry_diagonal_mark.toFixed(2)} · {whenLocked(entry.locked_at)}
                  </span>
                  <button
                    type="button"
                    className="rounded px-2 py-[2px] text-[11px]"
                    style={{ border: '1px solid var(--line)', color: 'var(--text-2)' }}
                    // Disabled on the combo already shown: a button that
                    // silently does nothing is worse than one that says it
                    // has nothing to do.
                    disabled={viewing}
                    onClick={() => onSelect({
                      frontExpiry: entry.front_expiry,
                      backExpiry: entry.back_expiry,
                      putStrike: entry.put_strike,
                      callStrike: entry.call_strike,
                    })}
                  >
                    View
                  </button>
                  <button
                    type="button"
                    className="rounded px-2 py-[2px] text-[11px]"
                    style={{ border: '1px solid var(--line)', color: 'var(--text-2)' }}
                    disabled={busy}
                    onClick={() => clear.mutate({
                      front_expiry: entry.front_expiry,
                      back_expiry: entry.back_expiry,
                      put_strike: entry.put_strike,
                      call_strike: entry.call_strike,
                    })}
                  >
                    Remove
                  </button>
                </div>
              )
            })
          )}
        </div>
      )}

      {failure && (
        // The server's own sentence. A 409 says what it is already locked at
        // and when — far more use than "request failed".
        <p className="mt-2 text-[12px]" style={{ color: '#ff8a7a' }}>
          {failure.message}
        </p>
      )}

      {confirming && !lock && currentMark !== null && (
        <div
          className="mt-2 rounded p-3"
          style={{ border: '1px solid var(--line)' }}
        >
          <p className="text-[13px]" style={{ color: 'var(--text)' }}>
            <strong>
              Lock entry at current Diagonal Mark: ${currentMark.toFixed(2)}
            </strong>
            <br />
            <span style={{ color: 'var(--text-2)' }}>
              Put {putStrike.toLocaleString()} / Call {callStrike.toLocaleString()} ·{' '}
              {frontExpiry} → {backExpiry}
            </span>
          </p>

          <div className="mt-2 flex gap-3 text-[12px]" style={{ color: 'var(--text-2)' }}>
            {(['monitor_only', 'monitor_and_log'] as const).map((option) => (
              <label key={option} className="flex items-center gap-1">
                <input
                  type="radio"
                  name="lock-mode"
                  checked={mode === option}
                  onChange={() => setMode(option)}
                />
                {option === 'monitor_only' ? 'Monitor Only' : 'Monitor + Log Trade'}
              </label>
            ))}
          </div>

          {mode === 'monitor_and_log' && (
            // Said plainly rather than hidden: the option exists so the
            // eventual Journal row can be built from this same lock instead
            // of a second record of the same fill, but it creates no trade
            // today (Chandan, 2026-09-07 — "hold on to journal for now").
            <p className="mt-2 text-[11px]" style={{ color: 'var(--text-3)' }}>
              The Journal is not wired up yet. This locks as Monitor Only for
              now; your choice is saved on the lock so the eventual Journal
              entry can be created from it rather than a second record.
            </p>
          )}

          <div className="mt-3 flex gap-2">
            <button
              type="button"
              className="rounded px-3 py-1 text-[12px] font-semibold"
              style={{ background: '#10d4a3', color: '#04121b' }}
              disabled={create.isPending}
              onClick={() => {
                create.mutate(
                  { ...combo, diagonal_mark: currentMark, mode },
                  { onSuccess: () => setConfirming(false) },
                )
              }}
            >
              {create.isPending ? 'Locking…' : 'Confirm Lock'}
            </button>
            <button
              type="button"
              className="rounded px-3 py-1 text-[12px]"
              style={{ border: '1px solid var(--line)', color: 'var(--text-2)' }}
              onClick={() => { create.reset(); setConfirming(false) }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
