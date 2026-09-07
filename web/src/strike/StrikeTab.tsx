/**
 * The Strike Detail tab — `views/strike.py`.
 *
 * A left column of figures and one chart on the right. The figures are the
 * four contracts of the diagonal priced in both expiries, plus each expiry's
 * ATM IV and how far it just moved; the chart is front-vs-back IV at the two
 * trade strikes with the ratio on a second axis.
 *
 * BLANK IS NOT ZERO, AND THIS IS THE TAB WHERE THAT MATTERS MOST. Every
 * figure here is a price or a volatility, and a missing one is drawn "N/A".
 * Rendering it as 0.00 would be a measured claim that an option is worthless
 * — a statement about the market rather than about the record. The front
 * leg's mark is genuinely absent on the live snapshot right now, so this is
 * not a hypothetical.
 *
 * NOTHING HERE DIVIDES. The IV ratio on each leg and the ratio line on the
 * chart are both the server's, from `core.series.merge_iv_pair` — the same
 * join the Calendar Edge tab uses, one level down from expiry to contract.
 */
import { useState } from 'react'

import { useControls, useHistoricalStats, useStrikeDetail, useStrikeIv } from '../api/client'
import type { StrikeLeg } from '../api/types'
import { PairControls } from '../edge/PairControls'
import type { EdgeSelection } from '../nav'
import { HistoricalStats } from './HistoricalStats'
import { StrikeIvChart } from './StrikeIvChart'

const CALL = '#10d4a3'
const BACK = '#5b9cff'
const RATIO = '#f05252'
const DIM = '#2f4459'

/** A number, or "N/A". `digits` is presentation; the value is the server's. */
function num(value: number | null, digits: number, suffix = '', prefix = ''): string {
  if (value === null) return 'N/A'
  return `${prefix}${value.toFixed(digits)}${suffix}`
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="px-1 py-3 text-[12px]" style={{ color: 'var(--text-2)' }}>{children}</p>
}

function Leg({ leg }: { leg: StrikeLeg }) {
  const inexact = !leg.front_exact || !leg.back_exact
  return (
    <div className="mb-3">
      <p className="text-[12px] font-semibold" style={{ color: 'var(--text)' }}>
        {leg.label} {leg.strike.toLocaleString()}
      </p>
      <p className="mono text-[11px]">
        <span style={{ color: 'var(--text-2)' }}>IV → F </span>
        <span style={{ color: CALL }}>{num(leg.front_iv, 2, '%')}</span>
        <span style={{ color: 'var(--text-2)' }}> / B </span>
        <span style={{ color: BACK }}>{num(leg.back_iv, 2, '%')}</span>
        <span style={{ color: 'var(--text-2)' }}> · Ratio </span>
        <span style={{ color: RATIO }}>{num(leg.iv_ratio, 4)}</span>
      </p>
      <p className="mono text-[11px]">
        <span style={{ color: 'var(--text-2)' }}>Mark → F </span>
        <span style={{ color: CALL }}>{num(leg.front_mark, 2, '', '$')}</span>
        <span style={{ color: 'var(--text-2)' }}> / B </span>
        <span style={{ color: BACK }}>{num(leg.back_mark, 2, '', '$')}</span>
      </p>
      {/* Said out loud, because a neighbouring contract's price under this
          strike's heading is otherwise indistinguishable from the real one. */}
      {inexact && (
        <p className="text-[10px]" style={{ color: '#e8b64c' }}>
          Nearest available strike — the chain does not list this one in{' '}
          {!leg.front_exact && !leg.back_exact ? 'either expiry'
            : leg.front_exact ? 'the back expiry' : 'the front expiry'}.
        </p>
      )}
    </div>
  )
}

export function StrikeTab({ initial = null }: { initial?: EdgeSelection | null }) {
  // Seeded from the address when the Scanner drilled through to here, else
  // null, which means "use the server's default for this pair". Held as
  // state rather than read from the URL on every render: once you are on
  // this tab the pickers are yours, and the link that brought you here does
  // not get to keep overriding them.
  const [front, setFront] = useState<string | null>(initial?.front ?? null)
  const [back, setBack] = useState<string | null>(initial?.back ?? null)
  const [putStrike, setPutStrike] = useState<number | null>(initial?.putStrike ?? null)
  const [callStrike, setCallStrike] = useState<number | null>(initial?.callStrike ?? null)
  const [days, setDays] = useState(5)

  const controls = useControls(front, back)
  const c = controls.data
  const eFront = front ?? c?.front ?? null
  const eBack = back ?? c?.back ?? null
  const ePut = putStrike ?? c?.put_strike ?? null
  const eCall = callStrike ?? c?.call_strike ?? null

  const detail = useStrikeDetail(eFront, eBack, ePut, eCall)
  const history = useStrikeIv(eFront, eBack, ePut, eCall, days)
  // NOT SCOPED BY `days`, and that is the point of the panel: it compares
  // four fixed windows at once, so the Range picker above must not narrow
  // them. The two controls answer different questions on the same screen.
  const stats = useHistoricalStats(eFront, eBack)

  if (controls.isPending) return <Note>Loading the chain…</Note>
  if (controls.isError) {
    return <Note>Could not read the chain: {(controls.error as Error).message}</Note>
  }
  if (!c) return <Note>Nothing to draw.</Note>

  return (
    <div className="px-5 py-4">
      <PairControls
        data={c} front={eFront} back={eBack}
        putStrike={ePut} callStrike={eCall} days={days}
        onChange={(next) => {
          if (next.front !== undefined) setFront(next.front)
          if (next.back !== undefined) setBack(next.back)
          if (next.putStrike !== undefined) setPutStrike(next.putStrike)
          if (next.callStrike !== undefined) setCallStrike(next.callStrike)
          if (next.days !== undefined) setDays(next.days)
        }}
      />

      <div className="flex flex-wrap gap-6">
        <div style={{ minWidth: '15rem', flex: '0 0 15rem' }}>
          <p className="mb-2 text-[13px] font-semibold" style={{ color: 'var(--text)' }}>
            Expiry Detail
          </p>
          {detail.isPending ? (
            <Note>Loading…</Note>
          ) : detail.isError ? (
            <Note>Unavailable — {(detail.error as Error).message}</Note>
          ) : (
            <>
              {detail.data.expiries.map((e) => (
                <div key={e.role} className="mb-3">
                  <p className="text-[10px]" style={{ color: DIM }}>
                    {e.role} · {e.expiry}
                  </p>
                  <p className="mono text-[19px] font-semibold" style={{ color: 'var(--text)' }}>
                    {num(e.atm_iv, 2, '%')}
                  </p>
                  {/* No arrow when the change is unknown. One record on file
                      is not a flat reading, and a green ↑ 0.00% would say it
                      was. */}
                  <p className="mono text-[11px]"
                     style={{ color: e.change === null ? DIM
                       : e.change >= 0 ? CALL : RATIO }}>
                    {e.change === null ? 'no prior record'
                      : `${e.change >= 0 ? '↑' : '↓'} ${e.change >= 0 ? '+' : ''}${e.change.toFixed(2)}%`}
                  </p>
                </div>
              ))}

              <hr className="my-3" style={{ borderColor: 'var(--border)', opacity: 0.4 }} />
              <p className="mb-2 text-[13px] font-semibold" style={{ color: 'var(--text)' }}>
                Strike Detail
              </p>
              {detail.data.legs.map((leg) => <Leg key={leg.label} leg={leg} />)}
            </>
          )}
        </div>

        <div style={{ flex: '1 1 32rem', minWidth: '24rem' }}>
          <p className="text-[13px] font-semibold" style={{ color: 'var(--text)' }}>
            Selected-Strike IV
          </p>
          <p className="mb-2 text-[11px]" style={{ color: 'var(--text-2)' }}>
            Front vs back IV at your trade strikes — ratio on the right axis.
          </p>
          {history.isPending ? (
            <Note>Loading the IV history…</Note>
          ) : history.isError ? (
            <Note>Unavailable — {(history.error as Error).message}</Note>
          ) : history.data.calls.length === 0 && history.data.puts.length === 0 ? (
            <Note>
              No per-strike history for {eCall?.toLocaleString()}C /{' '}
              {ePut?.toLocaleString()}P in the selected range. Try Today.
            </Note>
          ) : (
            <StrikeIvChart
              calls={history.data.calls}
              puts={history.data.puts}
              callStrike={history.data.call_strike}
              putStrike={history.data.put_strike}
              rangebreaks={history.data.rangebreaks}
            />
          )}
        </div>
      </div>

      {stats.isError ? (
        <Note>Historical statistics unavailable — {(stats.error as Error).message}</Note>
      ) : stats.data ? (
        <HistoricalStats
          windows={stats.data.windows}
          current={stats.data.current}
          front={stats.data.front}
          back={stats.data.back}
        />
      ) : null}
    </div>
  )
}
