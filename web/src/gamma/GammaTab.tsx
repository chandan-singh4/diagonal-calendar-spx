/**
 * The Gamma Exposure tab — `views/gex.py`, top half.
 *
 * WHAT THIS IS AND WHAT IT IS NOT. The Streamlit tab is much larger than what
 * is here: below the strike panels it carries time panels, the 0DTE flow
 * board, dealer structure, net flow and a replay. This file draws the part
 * every one of those sits under — the headline strip and the six strike
 * views — and the rest is still the Streamlit tab's. That is stated in
 * web/README.md too rather than left to be discovered by scrolling.
 *
 * TWO REQUESTS, NOT ONE. `useGamma('gamma')` always runs, because the volume
 * and open-interest panels are gamma columns whichever view is selected, and
 * because the headline strip reads the gamma figures even while showing
 * vanna's. A second request runs only when the selected view is not gamma.
 * The Streamlit page does exactly this: `shown` is always the gamma frame,
 * `second` is the selected one.
 *
 * NO FORMULA LIVES HERE. Every figure on screen is a string the server
 * formatted, every tick position is one the server placed, and the expiry
 * list is the server's. The only arithmetic in this directory is negating a
 * put series to mirror it, which is a layout choice, not a measurement.
 */
import { useEffect, useMemo, useRef, useState } from 'react'

import { useGamma, useSessionRange } from '../api/client'
import type { Measure } from '../api/types'
import { ExpiryDropdown } from './ExpiryPicker'
import { ExposureGrid } from './ExposureGrid'
import { DealerSection } from './DealerSection'
import { SessionSection } from './SessionSection'
import { StrikeFlowSection } from './StrikeFlowSection'
import { HeadlineStrip } from './HeadlineStrip'
import { StrikeChart, type PanelSpec } from './StrikeChart'

/** The six views, in the page's order, each naming the measure it needs and
 *  how panel 1 is drawn for it — `views/gex.py:_VIEWS` and the branch under
 *  "Panel 1, foreground".
 *
 *  MIRRORING IS NOT A STYLE CHOICE. Only "Call vs Put" flips the put series
 *  below the axis; there the sign IS the message. Abs Gamma stacks the two
 *  halves of one positive total, and the second-order pair carries the same
 *  sign on both sides at a strike, so mirroring either would draw two bars
 *  cancelling out where the honest picture is one tall one. */
const VIEWS: { label: string; measure: Measure; spec: PanelSpec }[] = [
  {
    label: 'Call vs Put',
    measure: 'gamma',
    spec: { callColumn: 'call_gex', putColumn: 'put_gex', mirror: true,
            wicks: true, tag: 'GEX', title: 'Gamma Exposure' },
  },
  {
    label: 'Abs Gamma',
    measure: 'gamma',
    spec: { callColumn: 'call_gex', putColumn: 'put_gex', mirror: false,
            wicks: true, tag: 'GEX', title: 'Gamma Exposure' },
  },
  {
    label: 'Net Gamma',
    measure: 'gamma',
    spec: { callColumn: 'call_gex', putColumn: 'put_gex', netColumn: 'net_gex',
            mirror: false, wicks: true, tag: 'Net GEX', title: 'Gamma Exposure' },
  },
  {
    // vGEX carries gamma's COLUMN NAMES on purpose — it is the same measure
    // over today's traded volume instead of the installed open interest — so
    // the panel needs no new columns, only a different measure and a title
    // that says which weighting is on screen.
    label: 'vGEX by Volume',
    measure: 'vgex',
    // CALL VS PUT, MIRRORED — the same shape as the default GEX view, and
    // deliberately so. The whole use of vGEX is to be flipped against GEX and
    // read for divergence, and two panels drawn differently cannot be
    // compared by looking. ITS WICKS ARE VOLUME-WEIGHTED TOO — the range is
    // requested at this panel's own measure, so the line behind the bar is
    // today's flow rather than the installed open interest.
    spec: { callColumn: 'call_gex', putColumn: 'put_gex', mirror: true,
            wicks: true, tag: 'vGEX',
            title: 'Volume-Weighted Gamma — today’s flow, not installed OI' },
  },
  {
    label: 'Delta Exposure',
    measure: 'delta',
    spec: { callColumn: 'call_dex', putColumn: 'put_dex', mirror: false,
            wicks: true, tag: 'DEX', title: 'Delta Exposure' },
  },
  {
    label: 'Vanna Exposure',
    measure: 'vanna',
    spec: { callColumn: 'call_vex', putColumn: 'put_vex', mirror: false,
            wicks: true, tag: 'VEX', title: 'Vanna Exposure — $ delta per IV point' },
  },
  {
    label: 'Charm Exposure',
    measure: 'charm',
    spec: { callColumn: 'call_cex', putColumn: 'put_cex', mirror: false,
            wicks: true, tag: 'CEX', title: 'Charm Exposure — $ delta per day' },
  },
]

function Note({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-5 py-8 text-[13px]" style={{ color: 'var(--text-2)' }}>
      {children}
    </p>
  )
}

/**
 * How long an update may take before the page admits to being mid-update.
 *
 * WHY THERE IS A DELAY AT ALL (Chandan, 2026-09-07: "charts go dim and say
 * Updating", reported as the page still refreshing). Ticking an expiry is
 * answered in about 0.2s, so the dim and the label appeared and vanished
 * inside a fifth of a second on every single click. Correct, honest, and read
 * as a flicker -- which is what the whole change was meant to stop. A signal
 * that fires on every ordinary action is not a signal.
 *
 * WHY IT IS NOT SIMPLY REMOVED. The reason for saying it has not gone away:
 * what is on screen during that moment IS the previous scope's answer, and a
 * chart that quietly draws the old expiry under the new selection is worse
 * than a blank one because the reader believes it. The delay does not hide
 * that -- it waits to see whether the moment is long enough to be noticed. A
 * slow answer still dims; a fast one never had a stale chart on screen long
 * enough for anyone to read it.
 *
 * 400ms, from the measurement rather than from taste: cold requests to
 * /mission/gamma run ~0.2s, so an ordinary tick is finished before this
 * fires, and anything that trips it is genuinely slower than the tab's normal
 * behaviour.
 */
const SETTLE_MS = 400

/**
 * `active`, but only once it has been true for `delay` milliseconds.
 *
 * Goes false the INSTANT `active` does, with no trailing delay: the point is
 * to suppress a flicker on the way in, and holding a "stale" mark over data
 * that has already arrived would be the page lying in the other direction.
 */
function useSettled(active: boolean, delay: number): boolean {
  const [elapsed, setElapsed] = useState(false)
  useEffect(() => {
    if (!active) return
    const timer = window.setTimeout(() => setElapsed(true), delay)
    // Cleared on every change of `active` and on unmount, so a fast update
    // cannot leave a timer behind that dims a page already current -- and the
    // flag is reset here rather than in the effect body, which would set
    // state during a render pass for no gain (oxlint set-state-in-effect).
    return () => {
      window.clearTimeout(timer)
      setElapsed(false)
    }
  }, [active, delay])
  // ANDED WITH `active`, so a stale `elapsed` from the previous update can
  // never dim a page whose answer has already arrived.
  return active && elapsed
}


export function GammaTab() {
  const [viewIndex, setViewIndex] = useState(0)
  // GRID BY DEFAULT (Chandan, 2026-09-06): the reason he asked for it was to
  // stop switching between measures, and a layout you have to switch TO would
  // have kept most of that cost. The detail view is one click away and is
  // where volume and open interest live.
  const [grid, setGrid] = useState(true)
  // A LIST, and the empty list means the whole board — see ExpiryPicker.
  const [expiries, setExpiries] = useState<string[]>([])
  // HAS HE CHOSEN YET? The server's default is applied ONCE, on first load.
  // Without this flag, clearing the picker back to the whole board would be
  // undone on the next render by the default being re-applied — the control
  // would refuse to do the thing its own Reset button says it does.
  const touched = useRef(false)
  const [stack, setStack] = useState(false)
  // ON BY DEFAULT, because it is what the tab has always shown and a control
  // that changes the page the moment it loads is not a preference, it is a
  // surprise. Not persisted: nothing on this screen writes browser storage
  // yet, and one toggle is not the reason to start.
  const [showVolume, setShowVolume] = useState(true)

  const view = VIEWS[viewIndex]
  const gamma = useGamma('gamma', expiries)
  // Kept mounted at 'gamma' when the view needs nothing else, so the hook
  // count never changes between renders. React requires that; and the query
  // key matches the call above, so TanStack serves it from cache rather than
  // asking the server the same question twice.
  const second = useGamma(view.measure, expiries)
  // Scoped exactly as the bars are — same expiries AND same measure — so a
  // wick can never belong to a contract, or to a Greek, that the bar in front
  // of it does not. See useSessionRange.
  const ranges = useSessionRange(expiries, view.measure)
  const usesSecond = view.measure !== 'gamma'

  const panel = usesSecond ? second : gamma

  const spec = useMemo(() => view.spec, [view])

  // WHAT IS ON SCREEN IS THE PREVIOUS SCOPE'S ANSWER while this is true, and
  // the page has to say so. Charts that quietly keep drawing an old expiry
  // under a new selection are worse than a blank page: the reader believes
  // them. `isPlaceholderData` is exactly this question -- not `isFetching`,
  // which is also true for the background refresh that leaves the answer
  // correct.
  const restating = gamma.isPlaceholderData || panel.isPlaceholderData
  // WHAT THE PAGE ACTUALLY SHOWS. `restating` is the truth; `stale` is the
  // truth that has lasted long enough to be worth interrupting the reader
  // with. See SETTLE_MS.
  const stale = useSettled(restating, SETTLE_MS)

  // 0 DTE during the session, the next expiry once the day is over. THE RULE
  // IS THE SERVER'S (core/expiry.py:default_scope) because it compares a
  // wall-clock time against the market's timezone, and this browser would
  // compare it against the viewer's — DEBT-030's failure, one screen along.
  const serverDefault = gamma.data?.default_expiry ?? null
  useEffect(() => {
    if (touched.current || serverDefault === null) return
    touched.current = true
    setExpiries([serverDefault])
  }, [serverDefault])

  // TRUE ONLY ON THE FIRST EVER LOAD now. Every later change of expiry or
  // measure keeps the previous answer on screen (see STEERED in api/client),
  // so this branch is the empty-page case it was always meant to be rather
  // than something the reader hits on every tick of the picker.
  if (gamma.isPending) return <Note>Loading the chain…</Note>
  if (gamma.isError) {
    return <Note>Could not read the chain: {(gamma.error as Error).message}</Note>
  }

  const body = gamma.data
  if (body.rows.length === 0) {
    // Blank, not zero. An empty frame is a snapshot with no gamma in it —
    // a real state on a thin chain — and drawing empty axes would look like
    // a chain of zeroes rather than an absent one.
    return <Note>Snapshot {body.snapshot_id} carries no gamma by strike.</Note>
  }

  return (
    // THE TOOLBAR NEVER DIMS, only the panels. The picker is what the reader
    // is using while this is true, and greying the control they are clicking
    // to say the charts are stale would be saying it about the wrong thing.
    <div className="px-5 py-4">
      <div className="mb-3 flex flex-wrap items-end gap-4">
        <ExpiryDropdown
          options={body.expiries}
          filters={body.expiry_filters ?? []}
          selected={expiries}
          onChange={(keys) => { touched.current = true; setExpiries(keys) }}
        />

        {/* SAID, NOT SHOWN BY BLANKING. The charts below are the previous
            scope's answer for a moment; a reader who is not told that would
            read them as the selection they just made. It sits beside the
            picker because that is where the reader is looking when it
            appears, and it reserves no space when idle -- a permanently
            present, usually empty label would shift the toolbar every time
            it filled. */}
        {stale && (
          <span className="pb-[6px] text-[11px]" style={{ color: 'var(--text-3)' }}>
            Updating…
          </span>
        )}

        <label className="flex flex-col gap-1" hidden={grid}>
          <span className="text-[10px] tracking-wider uppercase" style={{ color: 'var(--text-2)' }}>
            View
          </span>
          <select
            className="rounded-[6px] border px-2 py-[5px] text-[12px]"
            style={{ background: 'var(--bg-card)', borderColor: 'var(--border)',
                     color: 'var(--text)' }}
            value={viewIndex}
            onChange={(e) => setViewIndex(Number(e.target.value))}
          >
            {VIEWS.map((v, i) => (
              <option key={v.label} value={i}>{v.label}</option>
            ))}
          </select>
        </label>

        {/* IN THE DETAIL VIEW IT STAYS IN THE TOOLBAR, because there the
            volume and OI panels are two of three stacked charts with no
            heading of their own to hang a control from. In the GRID it moved
            down to sit beside "The book" (Chandan, 2026-09-06: "why don't you
            place it next to the book"): a control far from the only two panels
            it affects reads as though it governs the whole tab. */}
        <label className="flex items-center gap-2 pb-[6px] text-[12px]" hidden={grid}
               style={{ color: 'var(--text-2)' }}>
          <input type="checkbox" checked={stack} onChange={(e) => setStack(e.target.checked)} />
          Stack volume &amp; OI
        </label>

        {/* THE VOLUME SHADE, ON BOTH VIEWS (Chandan, 2026-09-07: "I want that
            to be like, I can either choose to have it or not"). It is the one
            thing on these panels that is CONTEXT rather than the measurement:
            every cell's bars are the selected Greek, and the violet and blue
            behind them are contracts traded. Reading six exposures against
            each other is harder with a seventh series behind all of them, and
            it is the only series a reader might want gone.

            NOT `hidden={grid}` like the checkbox above it: the shade is drawn
            in both layouts, so a control that vanished in one would leave it
            switched off with no way to bring it back. */}
        <label className="flex items-center gap-2 pb-[6px] text-[12px]"
               style={{ color: 'var(--text-2)' }}>
          <input type="checkbox" checked={showVolume}
                 onChange={(e) => setShowVolume(e.target.checked)} />
          Volume shade
        </label>

        {/* LAYOUT SWITCH, FAR RIGHT (Chandan, 2026-09-06). It is the one
            control on this row that changes the SHAPE of the page rather than
            what the page is about, and sitting first it read as the primary
            question. `ml-auto` puts it in the corner; the icons are a 2x2 of
            panels and a single panel, and the tooltips carry the words. */}
        <div className="ml-auto flex rounded-[8px] border p-[2px]"
             style={{ borderColor: 'var(--border)' }}>
          {[{ on: true, icon: '▦', label: 'Grid — all six measures at once' },
            { on: false, icon: '▣', label: 'Detail — one measure, with volume and open interest' },
          ].map((b) => (
            <button
              key={b.label}
              type="button"
              onClick={() => setGrid(b.on)}
              title={b.label}
              aria-label={b.label}
              aria-pressed={grid === b.on}
              className="rounded-[6px] px-[10px] py-[3px] text-[15px] leading-[20px]"
              style={grid === b.on
                ? { background: 'var(--bg-2)', color: 'var(--text)' }
                : { color: 'var(--text-2)' }}
            >
              {b.icon}
            </button>
          ))}
        </div>
      </div>

      {/* FULL WIDTH (Chandan, 2026-09-06: "so that we get the whole screen from
          left to right for the grid view... at the moment it looks congested").
          The picker used to sit in a 300px column here; it is now the dropdown
          in the toolbar above, and the panels have the row to themselves. */}
      <div>
        {/* DIMMED, NOT REPLACED, while the answer on screen belongs to the
            previous scope. Enough to say "this is not settled yet" from the
            corner of the eye, not enough to stop the reader comparing it with
            what they just picked -- which is the whole reason for keeping it
            drawn. The transition is short: a blink at 200ms reads as a
            glitch, and anything long enough to watch is worse than blanking. */}
        <div style={{ opacity: stale ? 0.55 : 1,
                      transition: 'opacity 120ms linear' }}>
          <HeadlineStrip
            summary={body.summary ?? {}}
            labels={body.labels ?? {}}
            second={
              !grid && usesSecond && panel.data?.summary && panel.data.labels
                ? { summary: panel.data.summary, labels: panel.data.labels }
                : undefined
            }
            measure={grid ? 'gamma' : view.measure}
          />

          {grid ? (
            <ExposureGrid gamma={body} expiries={expiries}
                          showVolume={showVolume}
                          stack={stack} onStack={setStack} />
          ) : usesSecond && panel.isPending ? (
            <Note>Loading {view.label}…</Note>
          ) : usesSecond && panel.isError ? (
            <Note>Could not compute {view.label}: {(panel.error as Error).message}</Note>
          ) : (
            <StrikeChart
              gammaRows={body.rows}
              panelRows={panel.data?.rows ?? []}
              ranges={ranges.data?.rows ?? []}
              spec={spec}
              // The SELECTED panel's ticks, because the axis has to fit what is
              // drawn — a delta axis labelled in gamma's billions would be wrong
              // by orders of magnitude rather than merely ugly.
              ticks={panel.data?.ticks ?? { tickvals: [], ticktext: [] }}
              spot={body.spot}
              stack={stack}
              showVolume={showVolume}
            />
          )}

          {/* BELOW BOTH VIEWS, not inside either. Grid and Detail are two ways
              of reading ONE SNAPSHOT across strikes; this is one strike read
              across TIME, and it answers the same question whichever is above
              it. It used to live inside ExposureGrid, which made it vanish on
              the switch to Detail — "I don't see the strike flow chart". */}
          <StrikeFlowSection gamma={body} expiries={expiries} />
      <SessionSection expiries={expiries} />
      <DealerSection expiries={expiries} />
        </div>
      </div>

      {/* vGEX's own ratio, which is not the summary's. Shown only where it
          means something, and captioned with what it is a share OF — a bare
          "0.72" beside a ratio of 2.55 invites the two to be read as the
          same kind of number. */}
      {!grid && view.measure === 'vgex' && typeof panel.data?.flow_ratio === 'number' && (
        <p className="mono mt-1 text-[10px]" style={{ color: 'var(--text-3)' }}>
          vGEX ratio {(panel.data.flow_ratio * 100).toFixed(1)}% of today’s
          {' '}absolute gamma flow is on the positive side
        </p>
      )}

      {/* The assumptions behind vanna and charm, stated where they are used.
          Their ABSENCE on gamma and delta is the honest signal: those two
          weight a column the broker sent and rest on no assumption at all. */}
      {panel.data?.assumptions && (
        <p className="mono mt-1 text-[10px]" style={{ color: 'var(--text-3)' }}>
          r={panel.data.assumptions.risk_free_rate} · q={panel.data.assumptions.dividend_yield} ·{' '}
          {panel.data.assumptions.day_remainder.toFixed(3)} of the day left at{' '}
          {panel.data.assumptions.snapshot_timestamp}
        </p>
      )}
    </div>
  )
}
