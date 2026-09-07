/**
 * All six exposure measures at once, three across and two down.
 *
 * WHY (Chandan, 2026-09-06): "so I can see all of the chart on the screen at
 * the same time rather than going from one to another". The dropdown made
 * every comparison a memory exercise — select vanna, look, select charm, look,
 * and try to recall where vanna's peak was. These measures are read AGAINST
 * each other: gamma against vGEX says whether today's flow agrees with the
 * installed structure, and vanna against charm says whether the book decays
 * toward or away from the move. None of that survives being seen one at a time.
 *
 * THE FIRST CELL HOLDS TWO. Call vs Put and Abs Gamma are the SAME numbers
 * drawn two ways — one mirrored, one stacked — so they are the one pair worth
 * a toggle rather than two cells. The other four measures are genuinely
 * different quantities and each get their own.
 *
 * NO OPEN-INTEREST PANEL HERE. It is the same series in all six cells; given a
 * row under each panel it would spend a third of the grid saying one thing six
 * times. The stacked panels stay in the detail view, which is where a single
 * measure is read closely. That is the whole trade the two layouts make:
 * breadth here, depth there. Volume DOES appear, as a shade behind the bars,
 * because it costs no height — see MiniPanel.
 *
 * ONE VOLUME SERIES FOR ALL SIX, taken from the gamma response. The chain has
 * one traded volume; drawing each cell's own would be the same numbers fetched
 * six times, and any disagreement between them would be a bug, not a reading.
 *
 * ONE SCOPE FOR ALL SIX. The expiry picker and the session range are the tab's,
 * not the cell's — six panels scoped differently would look like one picture
 * and be six.
 *
 * AND ANY CELL CAN TAKE THE SCREEN. Chandan, 2026-09-06: "in case I need to
 * look at the bigger picture." This is NOT the earlier version, which widened
 * a cell to the full row and was dropped because it re-flowed the other five;
 * an overlay COVERS the grid instead of rearranging it, so what is underneath
 * is exactly where it was when the overlay closes. Escape or the button
 * returns. It is a separate control from ⟲ on purpose: one changes how much of
 * the chart you see, the other how much of the screen it gets.
 *
 * EACH CELL ZOOMS IN PLACE. Chandan asked first to enlarge a cell to the full
 * row and then, seeing it, for the opposite: "zooming in ability WITHIN the
 * grid rather than enlarging the grid to the whole screen". He is right, and
 * the reason is the grid's own: enlarging one panel re-flows the other five
 * and breaks the side-by-side reading the layout exists for, while a box-zoom
 * inside a cell leaves every other cell exactly where it was. Drag, scroll, or
 * double-click; the ⟲ in each corner restores that panel alone. See MiniPanel.
 */
import { useEffect, useMemo, useState } from 'react'

import { useGamma, useSessionRange } from '../api/client'
import type { GammaResponse, Measure, SessionRangeRow } from '../api/types'
import { MiniPanel } from './MiniPanel'
import { PanelShell } from './PanelShell'
import type { PanelSpec } from './chart'

/** The two ways the same gamma numbers are drawn, and the only pair on the
 *  tab that shares a cell.
 *
 *  BOTH TITLES ARE EMPTY: the toggle buttons above the panel already say which
 *  of the two is showing, and the annotation repeated it a second time three
 *  lines lower. The other five cells have no toggle and so keep theirs. */
const GAMMA_PAIR: { label: string; spec: PanelSpec }[] = [
  {
    label: 'Call vs Put',
    spec: { callColumn: 'call_gex', netValue: 'net_gex', putColumn: 'put_gex', mirror: true,
            wicks: true, tag: 'GEX', title: '' },
  },
  {
    label: 'Abs Gamma',
    spec: { callColumn: 'call_gex', netValue: 'net_gex', putColumn: 'put_gex', mirror: false,
            wicks: true, tag: 'GEX', title: '' },
  },
]

/** Cells 2-6, in the order Chandan laid them out: net gamma and vGEX finish
 *  the first row, and the second row is the derivative chain — delta, then
 *  vanna and charm, which are both derivatives OF delta. */
const REST: { measure: Measure; spec: PanelSpec }[] = [
  {
    measure: 'gamma',
    spec: { callColumn: 'call_gex', netValue: 'net_gex', putColumn: 'put_gex', netColumn: 'net_gex',
            mirror: false, wicks: true, tag: 'Net GEX', title: 'Net Gamma' },
  },
  {
    measure: 'vgex',
    spec: { callColumn: 'call_gex', netValue: 'net_gex', putColumn: 'put_gex', mirror: true,
            wicks: true, tag: 'vGEX', title: 'vGEX — today’s volume' },
  },
  {
    measure: 'delta',
    spec: { callColumn: 'call_dex', netValue: 'net_dex', putColumn: 'put_dex', mirror: false,
            wicks: true, tag: 'DEX', title: 'Delta Exposure' },
  },
  {
    measure: 'vanna',
    spec: { callColumn: 'call_vex', netValue: 'net_vex', putColumn: 'put_vex', mirror: false,
            wicks: true, tag: 'VEX', title: 'Vanna — $ delta per IV point' },
  },
  {
    measure: 'charm',
    spec: { callColumn: 'call_cex', netValue: 'net_cex', putColumn: 'put_cex', mirror: false,
            wicks: true, tag: 'CEX', title: 'Charm — $ delta per day' },
  },
]

/**
 * THE BOOK — volume and open interest, kept OUT of the exposure grid.
 *
 * Chandan, 2026-09-06: "I want volume and open interest chart to be
 * independent of the grid." They were briefly the seventh and eighth cells of
 * it, and that was the wrong home for a reason worth writing down: the six
 * grid panels are all one KIND of thing — a dollar exposure, signed, read
 * against a zero line, and comparable cell to cell because they share a
 * y-scale idea. These two are contract COUNTS. Nothing about them is money,
 * their magnitudes have no relation to a GEX figure, and putting them in the
 * same visual sentence invited a comparison that means nothing.
 *
 * So they get their own section, their own heading and their own height. They
 * still share the grid's STRIKE span, because "independent" is about what they
 * measure, not about where a strike sits — the whole tab is read by scanning
 * one strike down the screen, and breaking that would cost more than it buys.
 */
// MIRRORED OR STACKED IS THE TAB'S CHOICE, not this list's. "Stack volume &
// OI" was a detail-view-only control because the grid had no volume or OI
// panels to stack; now that it does, the checkbox governs both views and
// these specs are built per render from it (Chandan, 2026-09-06: "under the
// left view I have an option to stack volume and open interest, but then I go
// to grid view and I don't see that option anymore").
function book(stack: boolean): { spec: PanelSpec }[] {
  return [
    {
      spec: { callColumn: 'call_volume', putColumn: 'put_volume',
              mirror: !stack, floor: stack, wicks: false, shade: false,
              tag: 'contracts', title: 'Volume — contracts traded today' },
    },
    {
      spec: { callColumn: 'call_oi', putColumn: 'put_oi',
              mirror: !stack, floor: stack, wicks: false, shade: false,
              tag: 'contracts',
              title: 'Open Interest — contracts still on the book' },
    },
  ]
}

/** These are measured in CONTRACTS, so they take no tick labels: the server's
 *  ticks are money ("12.2B"), and a contract count wearing a dollar label
 *  would be wrong rather than merely ugly. */
const NO_TICKS = { tickvals: [], ticktext: [] }


/** Taller than a grid cell. There are two of them across the same width the
 *  grid gives three, and the extra height is what makes a tall OI spike at one
 *  strike readable rather than a wall. */
const BOOK_HEIGHT = '22rem'

const CELL_HEIGHT = '19rem'
// The overlay's chart height: the viewport less the padding and the cell's own
// chrome. A number rather than `h-full` because MiniPanel sizes the plot div
// itself and Plotly needs a length, not a stretch.
const FULL_HEIGHT = 'calc(100vh - 5.5rem)'

// THE CARD AND THE OVERLAY LIVE IN PanelShell, so the grid's cells and the
// strike-flow panel below it cannot drift apart. No `onBig` is passed: the
// grid's cells get their maximise button from MiniPanel, which keeps it in one
// flex row with the reset so the pair cannot fall out of alignment.
function Cell({ children, note, big, hold = CELL_HEIGHT }: {
  children?: React.ReactNode
  note?: string
  big: boolean
  hold?: string
}) {
  return (
    <PanelShell big={big} hold={hold}>
      {note ? (
        <p className="px-3 py-8 text-[12px]" style={{ color: 'var(--text-2)' }}>{note}</p>
      ) : children}
    </PanelShell>
  )
}

export interface ExposureGridProps {
  /** The gamma response the tab already holds. Passed in rather than fetched
   *  again so the grid and the headline strip above it are the same snapshot;
   *  TanStack would serve it from cache either way, but "the same object" is a
   *  stronger guarantee than "the same query key". */
  gamma: GammaResponse
  expiries: string[]
  /** "Stack volume & OI" — the tab's checkbox, which now governs this view's
   *  book section as well as the detail view's panels. Shared rather than
   *  duplicated so switching views does not silently change the answer. */
  stack: boolean
  onStack: (on: boolean) => void
}

/** The first cell has no fixed title — its toggle names it — so it needs an
 *  identity of its own to be maximised by. */
const PAIR_ID = 'gamma-pair'

export function ExposureGrid({ gamma, expiries, stack, onStack }: ExposureGridProps) {
  const [pairIndex, setPairIndex] = useState(0)
  // ONE AT A TIME. Two full-screen panels is not a state that means anything.
  const [big, setBig] = useState<string | null>(null)
  const toggleBig = (id: string) => setBig((v) => (v === id ? null : id))

  // Escape closes it. A full-screen overlay with no keyboard way out is a trap
  // — and Escape is what anyone tries first.
  useEffect(() => {
    if (big === null) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setBig(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [big])

  // FIVE HOOKS, ALWAYS, IN THIS ORDER. React requires the count to be stable
  // between renders, so these cannot be called from inside the map below —
  // hence the flat list rather than a loop over REST.
  const vgex = useGamma('vgex', expiries)
  const delta = useGamma('delta', expiries)
  const vanna = useGamma('vanna', expiries)
  const charm = useGamma('charm', expiries)
  // ONE RANGE PER MEASURE (Chandan, 2026-09-06: "I want that same wick to be
  // present in all the six chart"). The wicks were gamma-only, so five of the
  // six cells drew none. They are separate requests for the same reason the
  // bars above are: a wick has to be the same measure as the bar it sits
  // behind, and the server refuses to answer otherwise.
  //
  // FLAT, NOT A LOOP, for the reason the comment above gives about `useGamma`
  // — React requires the hook count to be identical between renders, so these
  // cannot be called from inside the map further down. The five requests
  // share one session read on the server, so this is five cheap questions
  // against one expensive one rather than five expensive ones.
  const gammaRange = useSessionRange(expiries, 'gamma')
  const vgexRange = useSessionRange(expiries, 'vgex')
  const deltaRange = useSessionRange(expiries, 'delta')
  const vannaRange = useSessionRange(expiries, 'vanna')
  const charmRange = useSessionRange(expiries, 'charm')

  const byMeasure: Record<string, typeof vgex | undefined> = {
    vgex, delta, vanna, charm,
  }
  const rangeFor: Record<Measure, SessionRangeRow[]> = {
    gamma: gammaRange.data?.rows ?? [],
    vgex: vgexRange.data?.rows ?? [],
    delta: deltaRange.data?.rows ?? [],
    vanna: vannaRange.data?.rows ?? [],
    charm: charmRange.data?.rows ?? [],
  }
  const wicks = rangeFor.gamma
  const pair = GAMMA_PAIR[pairIndex]

  // ONE STRIKE SPAN FOR ALL SIX, taken from the gamma response because that is
  // the one every cell is read against. Padded by half a strike step so the
  // end bars are not clipped. See MiniPanel's xRange for why this is not each
  // panel's own min and max.
  const xRange = useMemo<[number, number] | undefined>(() => {
    const strikes = gamma.rows.map((r) => r.strike)
    if (strikes.length === 0) return undefined
    return [Math.min(...strikes) - 5, Math.max(...strikes) + 5]
  }, [gamma.rows])

  return (
    <>
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
      <Cell big={big === PAIR_ID}>
        {/* OVERLAID, NOT STACKED ABOVE THE CHART. Sitting in the flow, this
            row pushed cell 1's plot down and squeezed it shorter than the
            other five, so its zero line sat lower than theirs however
            carefully each panel centred its own — "the zero line doesn't align
            with the net gamma or the vGEX chart". Floating it costs the cell
            no height, and all six plot areas are then identical. */}
        <div className="absolute left-3 top-2 z-10 flex gap-1">
          {GAMMA_PAIR.map((option, i) => (
            <button
              key={option.label}
              type="button"
              onClick={() => setPairIndex(i)}
              className="rounded-[6px] px-2 py-[3px] text-[11px]"
              style={i === pairIndex
                ? { background: 'var(--bg-2)', color: 'var(--text)' }
                : { color: 'var(--text-2)' }}
            >
              {option.label}
            </button>
          ))}
        </div>
        <MiniPanel
          onBig={() => toggleBig(PAIR_ID)}
          big={big === PAIR_ID}
          rows={gamma.rows}
          ranges={wicks}
          volumeRows={gamma.rows}
          xRange={xRange}
          spec={pair.spec}
          ticks={gamma.ticks}
          spot={gamma.spot}
          flipStrike={gamma.flip_strike ?? null}
          height={big === PAIR_ID ? FULL_HEIGHT : CELL_HEIGHT}
        />
      </Cell>

      {REST.map(({ measure, spec }) => {
        // Net Gamma reads the gamma response the tab already has; the other
        // four each have their own. THE WICKS FOLLOW THE SAME MEASURE: a
        // gamma range drawn behind a charm bar is a different Greek at a
        // scale that still looks plausible, which is the one kind of wrong
        // this panel cannot show you.
        const query = byMeasure[measure]
        const body = measure === 'gamma' ? gamma : query?.data
        const note =
          !body && query?.isError
            ? `Could not compute ${spec.title}: ${(query.error as Error).message}`
            : !body
              ? `Loading ${spec.title}…`
              : undefined
        return (
          <Cell key={spec.title} note={note}
                big={big === spec.title}>
            {body && (
              <MiniPanel
                onBig={() => toggleBig(spec.title)}
                big={big === spec.title}
                rows={body.rows}
                ranges={rangeFor[measure]}
                volumeRows={gamma.rows}
                xRange={xRange}
                spec={spec}
                ticks={body.ticks}
                spot={body.spot}
                flipStrike={body.flip_strike ?? null}
                height={big === spec.title ? FULL_HEIGHT : CELL_HEIGHT}
              />
            )}
          </Cell>
        )
      })}
    </div>

    {/* A RULE AND A HEADING, not just a gap. The separation is the point of
        this section existing; a third row of unlabelled cells three pixels
        below the second would read as more of the same grid. */}
    <div className="mt-5 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
      <div className="mb-2 flex items-center gap-4">
        <p className="text-[11px] uppercase tracking-wide"
           style={{ color: 'var(--text-2)' }}>
          The book — contracts, not dollars
        </p>
        <label className="flex items-center gap-2 text-[12px]"
               style={{ color: 'var(--text-2)' }}>
          <input type="checkbox" checked={stack}
                 onChange={(e) => onStack(e.target.checked)} />
          Stack calls &amp; puts
        </label>
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {book(stack).map(({ spec }) => (
          <Cell key={spec.title} big={big === spec.title} hold={BOOK_HEIGHT}>
            <MiniPanel
              onBig={() => toggleBig(spec.title)}
              big={big === spec.title}
              rows={gamma.rows}
              ranges={wicks}
              volumeRows={gamma.rows}
              xRange={xRange}
              spec={spec}
              ticks={NO_TICKS}
              spot={gamma.spot}
              flipStrike={gamma.flip_strike ?? null}
              height={big === spec.title ? FULL_HEIGHT : BOOK_HEIGHT}
            />
          </Cell>
        ))}
      </div>
    </div>

    </>
  )
}
