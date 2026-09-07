/**
 * The full transform sweep — the React counterpart of the `st.dataframe` at
 * the bottom of `views/scanner.py`.
 *
 * SORTING IS DONE HERE AND NOTHING ELSE IS. Reordering rows the server sent
 * is not a formula: no number changes, and the same set is shown either way.
 * Filtering or recomputing `Transform Diff` would be, and is not done — the
 * threshold that decides which rows go green arrives in `bands.threshold`
 * rather than being written down again (DEBT-031 is the record of what
 * happens when it is written down again).
 */
import { useMemo, useState } from 'react'

import type { SweepRow } from '../api/types'

/** Columns in the order the page shows them, with how each is drawn.
 *  A table of nulls is the normal case for `Diagonal Mark` early in a
 *  session, so every formatter has to survive one. */
type ColumnKey = keyof SweepRow

interface Column {
  key: ColumnKey
  label: string
  /** Absent on the two text columns. A number column without it would round
   *  to whole units silently, so the type makes it a decision per column
   *  rather than a default that is right for none of them. */
  digits?: number
  /** Transform Diff only: it is the one column where the sign is the point,
   *  so a positive value is drawn with an explicit +. */
  signed?: boolean
  text?: boolean
}

const COLUMNS: Column[] = [
  { key: 'Front Expiry', label: 'Front Expiry', text: true },
  { key: 'Back Expiry', label: 'Back Expiry', text: true },
  { key: 'Put Strike', label: 'Put', digits: 0 },
  { key: 'Call Strike', label: 'Call', digits: 0 },
  { key: 'Diagonal Mark', label: 'Diag Mark', digits: 2 },
  { key: 'Transform Mark', label: 'Transform Mark', digits: 2 },
  { key: 'Transform Diff', label: 'Transform Diff', digits: 2, signed: true },
  { key: 'IV Ratio', label: 'IV Ratio', digits: 4 },
]

function cellText(row: SweepRow, col: Column): string {
  const value = row[col.key]
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  const text = value.toFixed(col.digits ?? 0)
  return col.signed && value >= 0 ? `+${text}` : text
}

export interface SweepTableProps {
  rows: SweepRow[]
  /** From `bands.threshold`. Rows at or above it are ready to transform. */
  threshold: number
  /** Open Calendar Edge on this row's pair. ONE CLICK, NO CONFIRMATION —
   *  see the note in src/App.tsx on why the page needs a second step and
   *  this does not. There is no selected-row state to go with it: selection
   *  existed to be confirmed, and nothing is confirmed any more. */
  onOpen: (row: SweepRow) => void
}

export function SweepTable({ rows, threshold, onOpen }: SweepTableProps) {
  const [sortKey, setSortKey] = useState<ColumnKey>('Transform Diff')
  const [ascending, setAscending] = useState(false)

  const sorted = useMemo(() => {
    // A copy: `rows` belongs to the query cache, and sorting in place would
    // mutate what every other reader of that cache sees — the same class of
    // fault as the `pop` on a cached dict that broke /mission/gamma.
    return [...rows].sort((a, b) => {
      const x = a[sortKey]
      const y = b[sortKey]
      // Nulls sort last in both directions. They are absences, not small
      // numbers, so letting them drift to the top on an ascending sort would
      // put "we don't know" where "cheapest" belongs.
      if (x === null || x === undefined) return 1
      if (y === null || y === undefined) return -1
      const order = typeof x === 'string' ? x.localeCompare(y as string) : x - (y as number)
      return ascending ? order : -order
    })
  }, [rows, sortKey, ascending])

  function toggle(key: ColumnKey) {
    if (key === sortKey) {
      setAscending((prev) => !prev)
    } else {
      setSortKey(key)
      setAscending(false)
    }
  }

  return (
    <div
      className="overflow-auto rounded-[10px] border"
      style={{ borderColor: 'var(--border)', maxHeight: '30rem' }}
    >
      <table className="w-full border-collapse text-[12px]">
        <thead className="sticky top-0 z-10">
          <tr style={{ background: 'var(--bg-raised)' }}>
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                scope="col"
                className="cursor-pointer px-3 py-2 text-left font-semibold select-none"
                style={{
                  color: col.key === sortKey ? 'var(--text)' : 'var(--text-2)',
                  borderBottom: '1px solid var(--border)',
                  textAlign: col.text ? 'left' : 'right',
                  whiteSpace: 'nowrap',
                }}
                onClick={() => toggle(col.key)}
              >
                {col.label}
                {col.key === sortKey && (ascending ? ' ↑' : ' ↓')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const ready = row['Transform Diff'] >= threshold
            const negative = row['Transform Diff'] < 0
            return (
              <tr
                key={`${row['Front Expiry']}|${row['Back Expiry']}|${row['Put Strike']}|${row['Call Strike']}`}
                onClick={() => onOpen(row)}
                title="Open this pair in Calendar Edge"
                className="cursor-pointer"
                style={{
                  background: ready ? 'var(--row-ready)' : 'transparent',
                  color: ready ? 'var(--green)' : negative ? 'var(--red)' : 'var(--text)',
                }}
              >
                {COLUMNS.map((col) => (
                  <td
                    key={col.key}
                    className={col.text ? 'px-3 py-[5px]' : 'mono px-3 py-[5px] text-right'}
                    style={{ whiteSpace: 'nowrap' }}
                  >
                    {cellText(row, col)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
