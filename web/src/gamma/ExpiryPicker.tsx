/**
 * The Gamma tab's expiry selector — the panel Chandan drew on 2026-09-06.
 *
 * WHY IT REPLACED A <select>. The dropdown could say which expiries exist and
 * nothing else, so choosing between them meant selecting one, reading the
 * chart, selecting the next, and holding two numbers in your head. This shows
 * every expiry's call and put gamma on the row you would click, so the
 * comparison is made by looking. That is the whole reason for the change; the
 * multi-select and the filters follow from it.
 *
 * NOTHING HERE IS DERIVED. Every figure, caption, sign and filter membership
 * on this panel arrives on the response (`ExpiryOption`). This file decides
 * which rows to show and what colour to paint them, and does no arithmetic on
 * an exposure, no date comparison and no formatting of a number. "This OpEx
 * Cycle" means whatever core/contract.py says it means — and which expiry the
 * tab OPENS on is core/expiry.py's answer, for the same reason.
 *
 * A FILTER WINDOW IS A SCOPE, NOT A VIEW. Picking "This Week" selects every
 * expiry in that week rather than merely listing them (Chandan, 2026-09-06);
 * `pick` below says why, and `matching` is the one place that decides what a
 * window contains, so the rows shown and the keys selected are the same set.
 *
 * SELECTION IS A LIST, AND THE EMPTY LIST IS "ALL". `/mission/gamma` with no
 * `expiry` is the whole-board figure most GEX commentary refers to, so an
 * untouched picker asks the question the tab has always asked. Deselecting
 * the last expiry therefore returns to the whole board rather than to an
 * empty chart — the alternative is a blank screen that looks broken.
 *
 * A PANEL INSIDE A DROPDOWN (Chandan, 2026-09-06). It began as a column beside
 * the chart, which cost 300px of width every second of the day to answer a
 * question asked once. With the grid drawing six panels across, "it looks
 * congested" — so the same panel now hangs off a button that states the
 * current scope, and the charts get the whole screen. The panel itself did not
 * change: it is still the comparison table, still opened deliberately.
 */
import { useMemo, useState } from 'react'

import type { ExpiryFilter, ExpiryOption } from '../api/types'

export interface ExpiryPickerProps {
  options: ExpiryOption[]
  filters: ExpiryFilter[]
  /** The selected display keys. Empty means the whole board. */
  selected: string[]
  onChange: (keys: string[]) => void
}

/** 0DTE is the expiry this dashboard looks at most, so it gets its own
 *  button rather than living three clicks into the filter menu. */
const ZERO_DTE = '0dte'

/** What the closed button says. THE SUMMARY IS THE POINT of collapsing this:
 *  a dropdown that hid which expiries are selected would make the six panels
 *  ambiguous — the same picture means different things scoped differently. */
function summarise(options: ExpiryOption[], selected: string[]): string {
  if (selected.length === 0) return 'Whole board'
  if (selected.length === 1) {
    const hit = options.find((o) => o.key === selected[0])
    return hit ? hit.dte_label + ' · ' + hit.day_label : selected[0]
  }
  return selected.length + ' expiries combined'
}

/**
 * The picker behind a button, for layouts that need their width back.
 *
 * The trigger is a real toggle rather than a hover target: opening it is a
 * decision, and a panel this tall appearing under the cursor by accident would
 * cover the charts it exists to scope.
 */
export function ExpiryDropdown(props: ExpiryPickerProps) {
  const [open, setOpen] = useState(false)
  const scoped = props.selected.length > 0
  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-label="Choose expiries"
        onClick={() => setOpen((v) => !v)}
        className="flex min-w-[210px] items-center justify-between gap-3 rounded-[6px] border px-3 py-[6px] text-[12px]"
        style={{ background: 'var(--bg-card)', borderColor: 'var(--border)',
                 color: scoped ? '#10d4a3' : 'var(--text)' }}
      >
        {/* The calendar says what the control is. An "EXPIRY" caption above it
            said the same thing a second time and cost a line of height. */}
        <span className="flex items-center gap-2">
          <span aria-hidden style={{ color: 'var(--text-2)' }}>&#x1F4C5;</span>
          {summarise(props.options, props.selected)}
        </span>
        <span aria-hidden style={{ color: 'var(--text-2)' }}>
          {open ? '▴' : '▾'}
        </span>
      </button>
      {open && (
        <div className="absolute left-0 top-[38px] z-30 shadow-2xl">
          <ExpiryPicker {...props} />
        </div>
      )}
    </div>
  )
}

/**
 * The expiries a filter window covers.
 *
 * ONE FUNCTION FOR TWO JOBS, and that is the point of it existing. It decides
 * both which rows the panel LISTS and which keys picking that window SELECTS,
 * so the list and the selection cannot disagree — a panel showing four rows
 * while the chart is scoped to three is the kind of mismatch nobody would
 * think to check.
 *
 * NO DATE COMPARISON HAPPENS HERE. Membership arrives per expiry on
 * `o.filters`, decided by core/contract.py against the market's calendar; a
 * comparison written here would run on the viewer's clock in the viewer's
 * timezone (DEBT-030). `null` is every expiry and `0dte` is the one bound
 * that is a plain integer rather than a date.
 */
function matching(options: ExpiryOption[], filter: string | null): ExpiryOption[] {
  if (filter === null) return options
  if (filter === ZERO_DTE) return options.filter((o) => o.dte === 0)
  return options.filter((o) => o.filters.includes(filter))
}

export function ExpiryPicker({ options, filters, selected, onChange }: ExpiryPickerProps) {
  const [filter, setFilter] = useState<string | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [byExposure, setByExposure] = useState(false)

  const chosen = useMemo(() => new Set(selected), [selected])

  const rows = useMemo(() => {
    const kept = matching(options, filter)
    if (!byExposure) return kept
    // Sorting is presentation over figures the server computed, so it may
    // live here. `call_gex + put_gex` is the "how much gamma sits at this
    // expiry regardless of side" ordering — the same question the Abs Gamma
    // panel asks, and the reason it is a sum rather than a difference.
    return [...kept].sort((a, b) =>
      (b.call_gex + b.put_gex) - (a.call_gex + a.put_gex))
  }, [options, filter, byExposure])

  const activeLabel =
    filter === null ? 'All'
    : filter === ZERO_DTE ? '0DTE'
    : (filters.find((f) => f.key === filter)?.label ?? filter)

  /**
   * Pick a window: narrow the list AND scope the charts to everything in it.
   *
   * WHY IT SELECTS AND DOES NOT MERELY SHOW (Chandan, 2026-09-06). "When I
   * click on anything in the filter, say this week, it should select all of
   * the expiry that belongs to this week." Narrowing alone left the reader to
   * click + on each row in turn to ask the question the window already names,
   * and the charts meanwhile still showed whatever was scoped before — a
   * panel listing this week's four expiries above a whole-board chart.
   *
   * IT REPLACES THE SELECTION RATHER THAN ADDING TO IT. These windows nest —
   * every expiry in "This Week" is also in "Next 2 Weeks" — so adding would
   * make the two buttons produce the same scope once both had been pressed,
   * and there would be no way back to the narrower one.
   *
   * "All" IS THE EMPTY LIST, which is this tab's whole board — the same
   * meaning it carries in the summary line and in `/mission/gamma` with no
   * `expiry`, rather than a second way of saying the same thing.
   *
   * A WINDOW WITH NOTHING IN IT LEAVES THE SCOPE ALONE. Late on a Friday
   * "This Week" can be empty, and selecting [] there would silently widen the
   * charts to the whole board — the opposite of narrowing, under a label that
   * says narrow. The list's own "No expiry falls inside" line says what
   * happened.
   */
  function pick(key: string | null) {
    setFilter(key)
    setMenuOpen(false)
    if (key === null) { onChange([]); return }
    const hits = matching(options, key)
    if (hits.length > 0) onChange(hits.map((o) => o.key))
  }

  function toggle(key: string) {
    onChange(chosen.has(key)
      ? selected.filter((k) => k !== key)
      : [...selected, key])
  }

  return (
    <div className="flex w-[300px] flex-col gap-2 rounded-[10px] border p-2"
         style={{ background: 'var(--bg-card)', borderColor: 'var(--border)' }}>

      <div className="flex items-center justify-between gap-2">
        <div className="flex rounded-[8px] border p-[2px]"
             style={{ borderColor: 'var(--border)' }}>
          {[{ key: null, label: 'All' }, { key: ZERO_DTE, label: '0DTE' }].map((b) => (
            <button
              key={b.label}
              type="button"
              onClick={() => pick(b.key)}
              className="rounded-[6px] px-3 py-[5px] text-[12px]"
              style={filter === b.key
                ? { background: 'var(--bg-2)', color: 'var(--text)' }
                : { color: 'var(--text-2)' }}
            >
              {b.label}
            </button>
          ))}
        </div>

        {/* ICONS, NOT WORDS, with the wording moved to the tooltip and the
            accessible label. Both buttons are STATEFUL — they turn green while
            they are doing something — so the icon alone never has to answer
            "is a filter on?", which is the question a bare glyph is worst at. */}
        <div className="relative flex gap-1">
          <button
            type="button"
            title={'Scope — ' + activeLabel}
            aria-label={'Scope — ' + activeLabel}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
            className="rounded-[8px] border px-2 py-[4px] text-[13px] leading-[18px]"
            style={{ borderColor: 'var(--border)',
                     color: filter === null ? 'var(--text-2)' : '#10d4a3' }}
          >
            {'☲'}
          </button>
          <button
            type="button"
            title={byExposure ? 'Sorted by gamma — click for date order'
                              : 'Sorted by date — click for gamma order'}
            aria-label={byExposure ? 'Sorted by gamma' : 'Sorted by date'}
            onClick={() => setByExposure((v) => !v)}
            className="rounded-[8px] border px-2 py-[4px] text-[13px] leading-[18px]"
            style={{ borderColor: 'var(--border)',
                     color: byExposure ? '#10d4a3' : 'var(--text-2)' }}
          >
            {'⇅'}
          </button>

          {menuOpen && (
            <div className="absolute right-0 top-[34px] z-20 w-[190px] rounded-[8px] border py-1 shadow-lg"
                 style={{ background: 'var(--bg-card)', borderColor: 'var(--border)' }}>
              {filters.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => pick(f.key)}
                  className="block w-full px-3 py-[7px] text-left text-[12px]"
                  style={{ color: filter === f.key ? 'var(--text)' : 'var(--text-2)' }}
                >
                  {f.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex max-h-[330px] flex-col gap-[6px] overflow-y-auto pr-1">
        {rows.length === 0 && (
          <p className="px-1 py-4 text-[12px]" style={{ color: 'var(--text-2)' }}>
            No expiry falls inside {activeLabel}.
          </p>
        )}
        {rows.map((o) => {
          const on = chosen.has(o.key)
          return (
            <div
              key={o.key}
              className="rounded-[8px] border px-2 py-[6px]"
              style={{ borderColor: on ? '#10d4a3' : 'var(--border)' }}
            >
              <div className="flex items-baseline justify-between">
                <span className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>
                  {o.dte_label}
                  {/* The monthly is the date the market's calendar is kept by,
                      and the a.m. contract is the one people forget exists.
                      Both are marked; every other expiry is left unmarked,
                      which is the point of that naming direction. */}
                  {o.is_third_friday && (
                    <span className="ml-[6px] text-[10px]" style={{ color: '#10d4a3' }}>
                      OPEX{o.is_am ? ' AM' : ''}
                    </span>
                  )}
                </span>
                <span className="text-[11px]" style={{ color: 'var(--text-2)' }}>
                  {o.day_label}
                </span>
              </div>
              <div className="mt-[5px] flex items-center gap-[6px]">
                <span className="mono flex-1 rounded-[6px] border py-[4px] text-center text-[12px]"
                      style={{ borderColor: '#1d7a5f', color: '#10d4a3' }}>
                  {o.call_label}
                </span>
                <span className="mono flex-1 rounded-[6px] border py-[4px] text-center text-[12px]"
                      style={{ borderColor: '#7a2f2f', color: '#f05252' }}>
                  {o.put_label}
                </span>
                <button
                  type="button"
                  onClick={() => toggle(o.key)}
                  title={on ? 'Remove ' + o.label : 'Add ' + o.label}
                  aria-pressed={on}
                  className="h-[28px] w-[34px] rounded-[6px] text-[15px] leading-none text-white"
                  style={{ background: on ? '#e04848' : '#12b981' }}
                >
                  {on ? '−' : '+'}
                </button>
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex items-center justify-between border-t pt-2"
           style={{ borderColor: 'var(--border)' }}>
        <span className="text-[11px]" style={{ color: 'var(--text-2)' }}>
          {selected.length === 0
            ? 'Whole board'
            : selected.length + (selected.length === 1 ? ' expiry' : ' expiries') + ' combined'}
        </span>
        <button
          type="button"
          onClick={() => { pick(null); setByExposure(false) }}
          className="rounded-[6px] border px-3 py-[5px] text-[12px]"
          style={{ borderColor: 'var(--border)', color: 'var(--text-2)' }}
        >
          Reset
        </button>
      </div>
    </div>
  )
}
