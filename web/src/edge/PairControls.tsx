/**
 * The four selections every pair tab is drawn from — `ui/controls.py`.
 *
 * EVERY LIST HERE IS THE SERVER'S, INCLUDING THE NARROWING. Back expiries
 * arrive already excluding anything not strictly later than the front, and
 * the strike lists arrive already intersected across both legs. Neither
 * filter is applied below, and that is deliberate: both are rules with
 * reasons behind them (a diagonal whose back leg expires first is not a
 * diagonal; a strike in only one leg cannot be traded as a pair), and a
 * client that re-derived them would be a second opinion about what a
 * tradeable pair is.
 *
 * The empty back list is a real state, not a loading one — it happens when
 * the furthest expiry collected is chosen as the front leg, and it is said
 * in words rather than shown as four empty dropdowns.
 */
import type { ControlsResponse } from '../api/types'

export interface PairControlsProps {
  data: ControlsResponse
  front: string | null
  back: string | null
  putStrike: number | null
  callStrike: number | null
  days: number
  onChange: (next: {
    front?: string | null
    back?: string | null
    putStrike?: number | null
    callStrike?: number | null
    days?: number
  }) => void
}

/** The chart windows this tab offers. It is the Streamlit tab's own control,
 *  and it lives here rather than on the Scanner because that is where it was
 *  moved to — see the note in web/src/scanner/ScannerTab.tsx. */
const RANGES = [
  { value: 1, label: 'Today' },
  { value: 5, label: '5D' },
  { value: 10, label: '10D' },
  { value: 20, label: '20D' },
]

const FIELD =
  'rounded-[6px] border px-2 py-[5px] text-[12px]'

function fieldStyle() {
  return {
    background: 'var(--bg-card)',
    borderColor: 'var(--border)',
    color: 'var(--text)',
  }
}

function Field({ label, children, className = '' }: {
  label: string; children: React.ReactNode; className?: string
}) {
  return (
    <label className={`flex flex-col gap-1 ${className}`}>
      <span className="text-[10px] tracking-wider uppercase" style={{ color: 'var(--text-2)' }}>
        {label}
      </span>
      {children}
    </label>
  )
}

export function PairControls({
  data, front, back, putStrike, callStrike, days, onChange,
}: PairControlsProps) {
  const noBack = data.back_expiries.length === 0

  return (
    <div className="mb-3 flex flex-wrap items-end gap-4">
      <Field label="Front expiry">
        <select
          className={FIELD} style={fieldStyle()}
          value={front ?? data.front ?? ''}
          onChange={(e) =>
            // The back and both strikes are cleared, not kept. They were
            // chosen against the OLD front; carrying one over would either
            // name a pair that no longer exists or silently keep a strike
            // the new pair does not list. Cleared, the server's defaults
            // for the new front decide.
            onChange({ front: e.target.value, back: null, putStrike: null, callStrike: null })
          }
        >
          {data.expiries.map((option) => (
            <option key={option.key} value={option.key}>{option.label}</option>
          ))}
        </select>
      </Field>

      <Field label="Back expiry">
        <select
          className={FIELD} style={fieldStyle()}
          value={back ?? data.back ?? ''}
          disabled={noBack}
          onChange={(e) => onChange({ back: e.target.value, putStrike: null, callStrike: null })}
        >
          {data.back_expiries.map((option) => (
            <option key={option.key} value={option.key}>{option.label}</option>
          ))}
        </select>
      </Field>

      <Field label="Put strike">
        <select
          className={`${FIELD} mono`} style={fieldStyle()}
          value={putStrike ?? data.put_strike ?? ''}
          disabled={data.put_strikes.length === 0}
          onChange={(e) => onChange({ putStrike: Number(e.target.value) })}
        >
          {data.put_strikes.map((strike) => (
            <option key={strike} value={strike}>{strike.toLocaleString()}</option>
          ))}
        </select>
      </Field>

      <Field label="Call strike">
        <select
          className={`${FIELD} mono`} style={fieldStyle()}
          value={callStrike ?? data.call_strike ?? ''}
          disabled={data.call_strikes.length === 0}
          onChange={(e) => onChange({ callStrike: Number(e.target.value) })}
        >
          {data.call_strikes.map((strike) => (
            <option key={strike} value={strike}>{strike.toLocaleString()}</option>
          ))}
        </select>
      </Field>

      {/* PUSHED TO THE FAR RIGHT (Chandan, 2026-09-07). `ml-auto` on a flex
          child eats every spare pixel on its line, so Range sits against the
          right edge while the four selects stay grouped on the left. It is
          the odd one out on this row — the other four choose WHICH pair to
          look at, and this one chooses HOW MUCH HISTORY to draw — so the gap
          is doing work, not just decoration.

          It degrades correctly when the row wraps: `ml-auto` applies within
          whichever line the field lands on, so on a narrow window Range is
          still right-aligned rather than stranded mid-row. */}
      <Field label="Range" className="ml-auto">
        <div className="flex gap-1">
          {RANGES.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange({ days: option.value })}
              className="rounded-[6px] px-3 py-[5px] text-[11px]"
              style={{
                background: option.value === days ? 'var(--bg-hover)' : 'var(--bg-card)',
                border: `1px solid ${option.value === days ? 'var(--border-hi)' : 'var(--border)'}`,
                color: option.value === days ? 'var(--text)' : 'var(--text-2)',
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </Field>

      {noBack && (
        <p className="pb-[6px] text-[11px]" style={{ color: 'var(--amber, #e8b64c)' }}>
          No expiry later than this one is being recorded, so there is no back leg to
          pair it with. Choose an earlier front expiry.
        </p>
      )}
    </div>
  )
}
