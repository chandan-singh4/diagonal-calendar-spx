/**
 * Positioning — whether the trading at each strike left real positions
 * behind, or was the same contracts changing hands over and over.
 *
 * A BAR TABLE, NOT A BARE ONE. The first version of this file rendered the
 * figures as plain right-aligned numbers, and that lost the panel's whole
 * point: "it just gives the value, there is nothing, no graphic, no bar
 * chart" (Chandan, 2026-09-06). Forty rows of five-digit numbers is a
 * spreadsheet — the reader has to compare them one pair at a time. The bar is
 * what turns it into a shape you can scan: one strike obviously twice as busy
 * as its neighbours, one obviously the only negative in a run of positives.
 *
 * EACH BAR IS SCALED WITHIN ITS OWN COLUMN, never across the table. Volume is
 * in contracts and the change in open interest is in contracts too, but they
 * are different quantities on different days; a shared scale would invite
 * exactly the comparison that makes this panel wrong.
 *
 * **ONE VOLUME COLUMN, CHOSEN BY THE READER.** Both days at once was two bars
 * that must never be compared with each other, and it read as clutter —
 * correctly, because that is what it was. The toggle names the day, and the
 * verdict column follows it: today's verdict does not exist yet.
 *
 * **THE CHANGE IN OPEN INTEREST IS YESTERDAY'S.** Open interest is
 * republished once, overnight, so today's published figure minus yesterday's
 * is what was opened or closed during YESTERDAY's session. Today's trading
 * has not been counted anywhere yet and will not be until tonight — which is
 * why the live view says "tonight" in that column rather than borrowing
 * yesterday's number to fill it.
 *
 * BLANK IS A REAL ANSWER. With no prior session there is nothing to subtract
 * from, so the change is null and every verdict is empty. Rendering that as
 * "0 change / churn" would be a verdict on the whole board from no evidence.
 *
 * Every verdict, every tone, both day labels and the ATM strike arrive from
 * the server. This file chooses only which colour a tone gets and how wide to
 * draw a bar.
 */
import { useState } from 'react'

import type { PositioningRow } from '../api/types'

export interface PositioningProps {
  rows: PositioningRow[]
  /** Only used to say which side of the money a strike is on. */
  spot: number
  /** The strike nearest spot, picked server-side. Null on an empty board. */
  atmStrike: number | null
  /** "Yesterday" and "Live"/"Today" — the word tracks the market, and the
   *  server owns that rule because it is a clock comparison in market time. */
  dayLabels: { prior: string; current: string }
  /** What each verdict means, in plain words and in reading order. Rendered
   *  verbatim beneath the table — see the header for why it is not written
   *  here. */
  glossary: { verdict: string; tone: string; meaning: string }[]
  basis: string
}

/**
 * Tone → the badge's fill, text and border.
 *
 * A LOOKUP, NOT A RULE: which strikes count as accumulation is decided by
 * core.dealer.classify against thresholds that live in Python. The five keys
 * match its five tones; an unrecognised one falls back to `quiet` rather than
 * being guessed at. The colours are the Streamlit tab's own, so a reader
 * moving between the two sees the same green mean the same thing.
 */
const TONE: Record<string, [string, string, string]> = {
  accumulation: ['rgba(16,185,129,.15)', '#34d399', 'rgba(16,185,129,.3)'],
  churn: ['rgba(148,163,184,.15)', '#cbd5e1', 'rgba(148,163,184,.3)'],
  liquidation: ['rgba(239,68,68,.15)', '#f87171', 'rgba(239,68,68,.3)'],
  wall: ['rgba(56,189,248,.15)', '#7dd3fc', 'rgba(56,189,248,.3)'],
  quiet: ['transparent', '#41586e', 'transparent'],
}

/** The volume bar. Deliberately neither green nor red: it counts contracts
 *  and takes no view on direction, unlike the change beside it. */
const VOL_BAR = '#8b5cf6'
const UP = '#10b981'
const DOWN = '#ef4444'
const FLAT = '#64748b'

/** Blank, not zero — the whole point of the null arriving from the server. */
function count(v: number | null): string {
  return v === null ? '—' : Math.round(v).toLocaleString()
}

function signed(v: number): string {
  const n = Math.round(v)
  return `${n > 0 ? '+' : ''}${n.toLocaleString()}`
}

/** A proportional fill inside a fixed track. The percentage is presentation —
 *  how wide to draw a box — not a claim about the data, which is why it is
 *  computed here and not on the server. */
function Bar({ pct, colour }: { pct: number; colour: string }) {
  return (
    <div className="h-[6px] w-full overflow-hidden rounded-[3px]"
         style={{ background: 'rgba(255,255,255,.05)' }}>
      <div className="h-full rounded-[3px]"
           style={{ width: `${pct}%`, background: colour }} />
    </div>
  )
}

export function Positioning({ rows, spot, atmStrike, dayLabels,
                              glossary, basis }: PositioningProps) {
  // Opens on the FINISHED session, because that is the side with verdicts on
  // it. The live side is real but can only ever show volume.
  const [live, setLive] = useState(false)
  const day = live ? dayLabels.current : dayLabels.prior

  const volumeOf = (r: PositioningRow) =>
    live ? r.total_volume : r.settled_volume

  // WIDEST IN EACH COLUMN, computed separately — see the header. `|| 1`
  // guards the all-zero board, where every bar is empty and dividing by the
  // maximum would be a division by zero.
  const widestVol = Math.max(1, ...rows.map((r) => volumeOf(r) ?? 0))
  const widestDelta = Math.max(
    1, ...rows.map((r) => Math.abs(r.delta_oi ?? 0)))

  const head = 'px-3 py-1 text-left text-[11px] font-normal'
  const cell = 'px-3 py-[6px] align-middle'

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 px-3 pt-2">
        {([['prior', dayLabels.prior], ['current', dayLabels.current]] as const)
          .map(([which, label]) => {
            const on = (which === 'current') === live
            return (
              <button
                key={which}
                type="button"
                onClick={() => setLive(which === 'current')}
                aria-pressed={on}
                className="rounded-[6px] border px-2 py-[2px] text-[11px]"
                style={{
                  background: on ? 'rgba(16,212,163,.12)' : 'var(--bg-card)',
                  borderColor: on ? '#10d4a3' : 'var(--border)',
                  color: on ? '#10d4a3' : 'var(--text-2)',
                }}
              >
                {label}
              </button>
            )
          })}
        <span className="text-[11px]" style={{ color: 'var(--text-3)' }}>
          {live
            ? 'this session’s volume — the contract count is republished '
              + 'after the close, so today’s verdict cannot exist yet'
            : 'the finished session: volume, what stuck, and a verdict'}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[12px]" style={{ color: 'var(--text)' }}>
          <thead>
            <tr style={{ color: 'var(--text-3)' }}>
              <th className={`${head} w-[110px]`}>Strike</th>
              <th className={head}>Traded {day.toLowerCase()}</th>
              {/* THE CHANGE IS NOT THE SAME DAY AS THE VOLUME BESIDE IT in
                  the live view — it does not exist yet — so the header says
                  when it will, rather than borrowing the word next to it. */}
              <th className={head}>
                Net ΔOI ({live ? 'arrives tonight' : dayLabels.prior.toLowerCase()})
              </th>
              <th className={`${head} w-[170px]`}>Position verdict</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const atm = atmStrike !== null && r.strike === atmStrike
              const vol = volumeOf(r)
              // Today's change has not been published yet. It is not zero,
              // not missing data, and not yesterday's — it is a number that
              // does not exist until after the close, and the cell says so.
              const delta = live ? null : r.delta_oi
              const deltaColour =
                delta === null ? FLAT
                : delta > 0 ? UP : delta < 0 ? DOWN : FLAT
              const [bg, fg, edge] =
                TONE[(live ? 'quiet' : r.tone) ?? 'quiet'] ?? TONE.quiet
              const verdict = live ? 'tonight' : (r.verdict || '—')

              return (
                <tr key={r.strike}
                    style={{
                      borderTop: '1px solid var(--border)',
                      background: atm ? 'rgba(84,160,255,.06)' : undefined,
                    }}>
                  <td className={cell}>
                    {r.strike.toLocaleString()}
                    <span className="ml-1 text-[10px]"
                          style={{ color: atm ? '#54a0ff' : 'var(--text-3)' }}>
                      {atm ? 'ATM'
                        : r.strike > spot ? 'above'
                        : r.strike < spot ? 'below' : 'at'}
                    </span>
                  </td>
                  <td className={cell}>
                    <div className="flex items-center gap-2">
                      <Bar pct={100 * ((vol ?? 0) / widestVol)}
                           colour={VOL_BAR} />
                      <span className="w-[72px] shrink-0 text-right tabular-nums">
                        {count(vol)}
                      </span>
                    </div>
                  </td>
                  <td className={cell}>
                    <div className="flex items-center gap-2">
                      <Bar
                        pct={delta === null
                          ? 0 : 100 * (Math.abs(delta) / widestDelta)}
                        colour={deltaColour}
                      />
                      <span className="w-[72px] shrink-0 text-right tabular-nums"
                            style={{ color: deltaColour }}>
                        {live ? 'tonight' : delta === null ? '—' : signed(delta)}
                      </span>
                    </div>
                  </td>
                  <td className={cell}>
                    <span className="inline-block rounded-[4px] px-2 py-[1px] text-[11px]"
                          style={{ background: bg, color: fg,
                                   border: `1px solid ${edge}` }}>
                      {verdict}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* THE LEGEND, under the table where a reader meets it after the word
          rather than before. Each badge is drawn in the SAME tone as the
          column above, so matching one to the other needs no reading at all
          — the colour does it. The wording is the server's; this file only
          lays it out. */}
      <div className="mt-3 border-t px-3 pt-3"
           style={{ borderColor: 'var(--border)' }}>
        <p className="mb-2 text-[10px] uppercase tracking-wide"
           style={{ color: 'var(--text-3)' }}>
          What the verdicts mean
        </p>
        <div className="grid gap-x-6 gap-y-[6px]"
             style={{ gridTemplateColumns:
                        'repeat(auto-fit, minmax(320px, 1fr))' }}>
          {glossary.map((g) => {
            const [bg, fg, edge] = TONE[g.tone] ?? TONE.quiet
            return (
              <div key={g.verdict} className="flex items-baseline gap-2">
                <span className="shrink-0 rounded-[4px] px-2 py-[1px] text-[11px]"
                      style={{ background: bg, color: fg,
                               border: `1px solid ${edge}` }}>
                  {g.verdict}
                </span>
                <span className="text-[11px] leading-[15px]"
                      style={{ color: 'var(--text-2)' }}>
                  {g.meaning}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <p className="px-3 pb-1 pt-3 text-[10px] leading-[14px]"
         style={{ color: 'var(--text-3)' }}>
        {basis}
      </p>
    </div>
  )
}
