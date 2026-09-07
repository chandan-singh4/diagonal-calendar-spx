/**
 * One exposure panel on its own — the top third of `StrikeChart`, no volume
 * and no open interest beneath it.
 *
 * WHY IT EXISTS SEPARATELY. The grid shows six measures at once, and the
 * open-interest PANEL is the same series in all six cells: given a row of its
 * own under every panel it would take a third of the grid to say one thing six
 * times. The stacked panels stay in the detail view, which is where a single
 * measure is read closely.
 *
 * VOLUME IS HERE, THOUGH, AS A BACKDROP (Chandan, 2026-09-06: "please add the
 * call and put volume shade"). It costs no height — it is a translucent fill
 * behind the bars on its own axis — and it answers the question every one of
 * these panels raises: is this exposure where today actually traded? The fill
 * colours are deliberately NOT the call/put green and red, which identify
 * SIDES; a third pair in those hues would read as a third side.
 *
 * NOT A GENERALISED `StrikeChart`. It would have been possible to give that
 * component a "panels" option and have one file draw both. That file already
 * carries three linked axes, a secondary overlay axis and two mirroring
 * rules, and threading a mode through all of it would have made the layout
 * harder to read than having the small version state its own. The pieces that
 * MUST agree between them — the columns, the mirroring rule, the wick rule —
 * are the shared `PanelSpec` and the shared `wickTrace`, so the parts that
 * could silently disagree are the parts that are shared.
 *
 * NOTHING HERE COMPUTES AN EXPOSURE, for the reasons StrikeChart states at
 * length. The only arithmetic is negating a put series to mirror it.
 */
import Plotly from 'plotly.js-dist-min'
import { useCallback, useEffect, useRef } from 'react'

import type { AxisTicks, SessionRangeRow, StrikeRow } from '../api/types'
import {
  BG, BRIGHT, CALL, CALL_VOL_EDGE, CALL_VOL_FILL, CALL_WICK, FLIP, GRID, INK,
  NET_WICK,
  BUTTON, HOVER, type PanelSpec, PUT, PUT_VOL_EDGE, PUT_VOL_FILL, PUT_WICK,
  wickTrace,
} from './chart'

export interface MiniPanelProps {
  rows: StrikeRow[]
  ranges: SessionRangeRow[]
  /** The gamma rows, ONLY for the volume backdrop. Passed separately because
   *  the second-order responses carry their own strike list and this panel may
   *  be drawing delta while the shade behind it is the chain's traded volume —
   *  one chain, one volume, whichever Greek is in front of it. */
  volumeRows: StrikeRow[]
  /** The strike span EVERY cell in the grid is drawn across, decided once by
   *  the grid and handed to all six.
   *
   *  NOT EACH PANEL'S OWN MIN AND MAX. Each measure's response carries its own
   *  strike list — a strike with delta but no gamma is in one and not the
   *  other — so panels left to autorange land on different spans, and 7700 is
   *  a different distance across each cell. Chandan, 2026-09-06: "if I'm
   *  comparing delta exposure chart with call versus put, then the 7700 strike
   *  doesn't align." A column of six charts that do not share an x axis is six
   *  charts; sharing it is what makes it one picture. */
  xRange: [number, number] | undefined
  spec: PanelSpec
  ticks: AxisTicks
  spot: number
  flipStrike: number | null
  /** Maximise handler. Omitted (Detail view) hides the button. */
  onBig?: () => void
  /** Is this panel currently maximised? */
  big?: boolean
  height: string
}

export function MiniPanel({
  rows, ranges, volumeRows, xRange, spec, ticks, spot, flipStrike, height,
  onBig, big = false,
}: MiniPanelProps) {
  const host = useRef<HTMLDivElement>(null)
  // RESET IS A RELAYOUT, AND IT HAS TO BE. `Plotly.react` compares the layout
  // it is given against the layout it was LAST GIVEN, not against what the
  // user has since done to the chart — so redrawing with the same home ranges
  // is a no-op on a zoomed panel and the reset button did nothing. relayout
  // addresses the axis directly and always lands.
  const home = useRef<{ x?: [number, number]; y?: [number, number]
                        y2?: [number, number] }>({})

  const reset = useCallback(() => {
    const node = host.current
    if (!node) return
    const at = home.current
    if (!at.x) return
    // ONLY THE KEYS THAT HAVE A VALUE. relayout THROWS on an undefined value
    // rather than skipping it, and the throw takes the whole call with it —
    // so a panel with no volume behind it (`yaxis2.range` absent) had a reset
    // button that silently did nothing at all, while the identical button on
    // a panel that did have volume worked. Found by calling relayout with an
    // undefined value by hand in the page: "Cannot read properties of
    // undefined (reading 'node')".
    //
    // Dotted paths are how relayout addresses one nested attribute; the
    // typings only describe whole Layout objects, hence the cast. Nothing here
    // says `autorange` — that would refit to the data and undo the centred
    // zero this cell shares with the other five.
    // COPIES AGAIN, for the reason the effect below spells out and this line
    // learned the hard way: handing Plotly the stored arrays makes them the
    // live layout arrays, so the NEXT zoom overwrites the home they hold. The
    // first reset then worked and every one after it silently restored the
    // previous zoom — the shape of the bug Chandan found by pressing the
    // button twice.
    const back: Record<string, [number, number]> = { 'xaxis.range': [...at.x] }
    if (at.y) back['yaxis.range'] = [...at.y]
    if (at.y2) back['yaxis2.range'] = [...at.y2]
    void Plotly.relayout(node, back as unknown as Partial<Plotly.Layout>)
  }, [])

  useEffect(() => {
    const node = host.current
    if (!node) return

    const strikes = rows.map((row) => row.strike)
    const shown = new Set(strikes)
    const wicks = ranges.filter((row) => shown.has(row.strike))
    const col = (name: string) => rows.map((r) => (name in r ? r[name] : null))

    const traces: Partial<Plotly.PlotData>[] = []

    // ── The day's volume, behind everything, on its own axis ───────────────
    // First in the list so the bars and wicks draw on top of it, and on `y2`
    // because contracts traded and dollar exposure are not the same unit —
    // sharing an axis would scale one of them into invisibility.
    //
    // THE PUT SHADE ALWAYS HANGS BELOW THE LINE, in all six cells, whether or
    // not that cell's put BARS are mirrored. It began as "mirror with the
    // bars", which left the put shade stacked on top of the call shade in the
    // four non-mirrored cells — two translucent fills over one another, which
    // is what Chandan meant by "not proper for the rest of the five chart".
    // Volume is one series with a fixed meaning; call up, put down, every
    // panel, so the shade reads the same way wherever the eye lands.
    const volStrikes = volumeRows.map((row) => row.strike)
    let volPeak = 0
    for (const [name, fill, edge, side] of spec.shade === false ? [] : [
      ['put_volume', PUT_VOL_FILL, PUT_VOL_EDGE, -1],
      ['call_volume', CALL_VOL_FILL, CALL_VOL_EDGE, 1],
    ] as const) {
      const values = volumeRows.map((r) => {
        const v = name in r ? r[name] : null
        if (v === null) return null
        volPeak = Math.max(volPeak, Math.abs(v))
        return side * v
      })
      traces.push({
        type: 'scatter',
        mode: 'lines',
        x: volStrikes,
        y: values,
        line: { width: 1, color: edge },
        fill: 'tozeroy',
        fillcolor: fill,
        hoverinfo: 'skip',
        showlegend: false,
        xaxis: 'x',
        yaxis: 'y2',
      })
    }

    if (wicks.length > 0 && spec.wicks) {
      // A NET PANEL DRAWS ONE SIGNED BAR, so it gets ONE wick, from the
      // server's own net range. Drawing the call and put wicks behind a net
      // bar would put two lines behind one bar at heights neither of them
      // reaches — the call side alone can tower over a net that has nearly
      // cancelled out.
      traces.push(...(spec.netColumn
        ? [wickTrace(wicks, 'net_low', 'net_high', NET_WICK, 1)]
        : [
            wickTrace(wicks, 'call_low', 'call_high', CALL_WICK, 1),
            wickTrace(wicks, 'put_low', 'put_high', PUT_WICK,
                      spec.mirror ? -1 : 1),
          ]))
    }

    // THE EXPOSURE PEAK, for the centred axis below. Every value that will be
    // DRAWN counts toward it — bars and wicks alike — because an axis sized to
    // the bars alone would clip the wicks that stick out past them.
    let peak = 0
    const note = (values: (number | null)[]): (number | null)[] => {
      for (const v of values) if (v !== null) peak = Math.max(peak, Math.abs(v))
      return values
    }
    if (wicks.length > 0 && spec.wicks) {
      for (const w of wicks) {
        peak = spec.netColumn
          ? Math.max(peak, Math.abs(w.net_low), Math.abs(w.net_high))
          : Math.max(peak, Math.abs(w.call_low), Math.abs(w.call_high),
                           Math.abs(w.put_low), Math.abs(w.put_high))
      }
    }

    // ── The hover ──────────────────────────────────────────────────────────
    // ONE BOX PER STRIKE, CARRYING THE WHOLE ROW. It used to be one box per
    // TRACE saying a single number — hovering a put bar said "Put GEX
    // 767,062,992" and nothing else, so comparing the two sides of a strike
    // meant hovering twice and remembering the first answer. Reading a strike
    // is the thing this panel is for; the box now answers it in one look.
    //
    // Carried on the FIRST bar trace only, with `hovermode: 'x'` doing the
    // matching. Repeating the template on both traces is what produced the
    // duplicated box on the strike-flow panel — see StrikeFlow.tsx.
    const money = (v: number | null) =>
      v === null ? '—' : Math.round(v).toLocaleString()
    const callSide = col(spec.callColumn)
    const putSide = col(spec.putColumn)
    // THE SERVER'S NET, not this file's subtraction — see PanelSpec.netValue
    // for the sign bug that taught the difference.
    const netSide = spec.netValue ? col(spec.netValue) : null
    // FROM `volumeRows`, KEYED BY STRIKE, because that is the series actually
    // DRAWN behind these bars. Read from `rows` it came back blank on delta,
    // vanna and charm — their responses carry only their own Greek, so the box
    // said "— C · — P" underneath a shade that was plainly there. The two must
    // agree: the number in the box is the fill behind it, or it is nothing.
    const volByStrike = new Map(volumeRows.map((r) => [r.strike, r]))
    const detail = strikes.map((strike, i) => {
      const vol = volByStrike.get(strike)
      return [
        money(callSide[i]),
        money(putSide[i]),
        netSide ? money(netSide[i]) : '—',
        money((vol?.call_volume as number | null | undefined) ?? null),
        money((vol?.put_volume as number | null | undefined) ?? null),
      ]
    })
    // The day's volume is DRAWN on these panels as the shade behind the bars,
    // so it belongs in the box that explains them. It is omitted from the two
    // book panels, where the bars ARE the volume and a second copy would read
    // as a second measurement agreeing with the first.
    const volLine = spec.shade === false ? ''
      : `<span style="color:${INK}">Volume</span>  %{customdata[3]} C · %{customdata[4]} P`
    // NO NET LINE WHERE THE SERVER HAS NO NET. The two book panels are
    // contract counts and the response carries no `net_*` for them; a "Net —"
    // row would be a field the panel does not have rather than a figure it is
    // missing, and this file may not fill it in itself.
    const netLine = spec.netValue ? 'Net  %{customdata[2]}' : ''
    const template =
      '<b>Strike %{x:,.0f}</b><br>' +
      `<span style="color:${CALL}">Calls</span>  %{customdata[0]}<br>` +
      `<span style="color:${PUT}">Puts</span>  %{customdata[1]}` +
      (netLine ? '<br>' + netLine : '') +
      (volLine ? '<br>' + volLine : '') +
      `<br><span style="color:${INK}">${spec.tag}</span>` +
      '<extra></extra>'

    const base = { type: 'bar' as const, showlegend: false }
    if (spec.netColumn) {
      const net = note(col(spec.netColumn))
      traces.push({
        ...base, x: strikes, y: net,
        marker: { color: net.map((v) => ((v ?? 0) >= 0 ? CALL : PUT)) },
        customdata: detail, hovertemplate: template,
      })
    } else {
      const put = note(col(spec.putColumn))
      // A STACKED BAR IS AS TALL AS THE SUM, and `note` only ever saw the two
      // series separately — so the axis was scaled to the tallest single side
      // and every stacked bar above that was clipped at the top of the cell
      // ("the bars don't really fit the chart size"). barmode is 'relative',
      // so this only applies where the puts are NOT mirrored: mirrored, the
      // two grow in opposite directions and each side's own peak is the reach.
      if (!spec.mirror) {
        const call = col(spec.callColumn)
        for (let i = 0; i < put.length; i += 1) {
          peak = Math.max(peak, Math.abs((call[i] ?? 0) + (put[i] ?? 0)))
        }
      }
      traces.push(
        { ...base, x: strikes, y: note(col(spec.callColumn)), marker: { color: CALL },
          customdata: detail, hovertemplate: template },
        { ...base, x: strikes,
          y: spec.mirror ? put.map((v) => (v === null ? null : -v)) : put,
          marker: { color: PUT },
          // SKIPPED, not templated. Everything it would say is in the call
          // trace's box already, and a second box is how the strike-flow
          // panel came to print the same figures twice.
          hoverinfo: 'skip' },
      )
    }

    const axisBase = {
      gridcolor: GRID, zerolinecolor: '#3c5570', zerolinewidth: 1,
      tickfont: { color: INK, size: 9 }, linecolor: GRID,
    }
    // The money axis carries the server's ticks here too. A grid cell is small
    // enough that Plotly's own SI labels ("12.2G") would be the only thing
    // readable on it, and they would disagree with every other figure on the
    // tab — see core.format.money_ticks.
    const moneyAxis = ticks.tickvals.length > 0
      ? { tickmode: 'array' as const, tickvals: ticks.tickvals, ticktext: ticks.ticktext }
      : {}

    const shapes: Partial<Plotly.Shape>[] = [{
      type: 'line', x0: spot, x1: spot, xref: 'x', yref: 'y domain', y0: 0, y1: 1,
      line: { color: '#8fa9c4', width: 1, dash: 'dot' },
    }]
    if (flipStrike !== null && strikes.length > 0 &&
        flipStrike >= Math.min(...strikes) && flipStrike <= Math.max(...strikes)) {
      shapes.push({
        type: 'line', x0: flipStrike, x1: flipStrike, xref: 'x', yref: 'y domain',
        y0: 0, y1: 1, line: { color: FLIP, width: 1, dash: 'dash' },
      })
    }

    // A STACKED COUNT SITS ON THE FLOOR; everything else is centred on zero.
    // See PanelSpec.floor — centring a series with nothing below zero would
    // spend half the cell on empty space and halve every bar.
    const yHome: [number, number] | undefined =
      peak <= 0 ? undefined
      : spec.floor ? [0, peak * 1.06]
      : [-peak * 1.06, peak * 1.06]
    const y2Home: [number, number] | undefined =
      volPeak > 0 ? [-volPeak * 2.2, volPeak * 2.2] : undefined
    // COPIES, NOT THE ARRAYS PLOTLY IS ABOUT TO BE GIVEN. Plotly writes a new
    // range INTO the array it was handed when the user zooms, so holding the
    // same reference here meant a box zoom quietly rewrote the "home" this was
    // storing — reset then restored the zoom it was supposed to undo, and the
    // button looked dead. The layout below gets its own copies for the same
    // reason: `xRange` is the grid's shared array, and letting Plotly mutate
    // it would have moved all six panels.
    home.current = {
      x: xRange ? [...xRange] : undefined,
      y: yHome ? [...yHome] : undefined,
      y2: y2Home ? [...y2Home] : undefined,
    }

    void Plotly.react(node, traces, {
      barmode: 'relative',
      bargap: 0.15,
      paper_bgcolor: BG,
      plot_bgcolor: BG,
      font: { color: INK, size: 10 },
      margin: { l: 52, r: 10, t: 26, b: 26 },
      // 'x', NOT 'closest'. The box describes the whole STRIKE, so it must
      // appear wherever the cursor is over that strike — including the empty
      // space above a short bar, which 'closest' would hand to a neighbour.
      hovermode: 'x',
      // Drag a box to zoom in, scroll to zoom, double-click or the button in
      // the corner to come back (Chandan, 2026-09-06: "zooming in ability
      // WITHIN the grid rather than enlarging the grid to the whole screen").
      // The panels stay where they are and the comparison across the row
      // survives the zoom, which enlarging into the full row did not.
      dragmode: 'zoom',
      // Readable at grid size: the hover box is where the actual numbers are
      // read now that the cells are small, so it does not shrink with them.
      hoverlabel: HOVER,
      showlegend: false,
      xaxis: { ...axisBase, range: xRange ? [...xRange] : undefined },
      // ── ZERO SITS HALFWAY UP EVERY CELL ────────────────────────────────
      // Chandan, 2026-09-06: "the zero line for all the chart should be in
      // sync". Left to autoscale, each panel fits its own data, so zero lands
      // at a different height in all six and the eye cannot carry a reading
      // from one cell to the next — which is the entire purpose of the grid.
      //
      // A SHARED RANGE WAS NOT AN OPTION: these are six different quantities
      // (dollars of gamma, dollars of delta, dollars per IV point), and one
      // range across them would flatten five of the six to a line. So each
      // panel keeps its own SCALE and they share a centred ORIGIN — symmetric
      // about zero, which puts the zero line at mid-height whatever the data
      // does.
      //
      // The cost is honest and worth naming: a panel whose data is all one
      // sign (Abs Gamma) now uses half its height. That is the price of being
      // able to read across the row without re-reading six axes.
      yaxis: { ...axisBase, ...moneyAxis, range: yHome ? [...yHome] : undefined },
      // Unlabelled: a grid cell has no room for a second set of tick numbers,
      // and the shade is read for its SHAPE — where today traded — not for a
      // contract count anyone would take off the axis.
      // Held to the lower half of the cell ON PURPOSE. The shade is CONTEXT
      // for the bars, and autoscaled it fills the panel and reads as the
      // subject. Giving it twice the headroom its peak needs keeps the tallest
      // volume around half height, so the exposure stays the foreground.
      // Symmetric where the puts mirror, so both shades share one scale.
      // SYMMETRIC FOR THE SAME REASON, and this one is not cosmetic: an
      // overlaying axis has its OWN zero, so a shade axis running [0, max] put
      // the fill's baseline at the BOTTOM of the cell while the bars sat on a
      // zero line halfway up, and the shade appeared to float free of the
      // chart it belongs to. Both axes centred means both zeros are one line.
      yaxis2: {
        overlaying: 'y', side: 'right', showgrid: false, zeroline: false,
        showticklabels: false,
        range: y2Home ? [...y2Home] : undefined,
      },
      // ANCHORED TO THE PLOT EDGE, NOT A FRACTION ABOVE IT. `y: 1.06` is 6%
      // of the plot's HEIGHT, so on a maximised panel the title was pushed
      // some 60px above a 26px top margin and clipped away entirely — the
      // chart filled the screen with nothing saying what it was. `yshift` is
      // in pixels and so means the same thing at every size.
      annotations: [{
        text: spec.title, x: 0, y: 1, xref: 'paper', yref: 'paper',
        xanchor: 'left', yanchor: 'bottom', yshift: 6,
        showarrow: false, font: { color: BRIGHT, size: 11 },
      }],
      shapes,
      // scrollZoom IS DELIBERATELY OFF. It was on for one revision and was
      // wrong: six plots stacked down a scrolling page turn the wheel into a
      // trap, and passing over a cell on the way down the page zooms it
      // (Chandan: "if I'm just scrolling... the chart moves even though I
      // don't intend to do that"). Zoom must be something you ASK for, and
      // dragging a box is the asking. The wheel belongs to the page.
    }, { displayModeBar: false, responsive: true, scrollZoom: false,
         doubleClick: false })
    // `height` IS IN THE DEPS even though the body never reads it: it changes
    // when a cell is maximised, and Plotly lays a chart out against the size
    // its container had AT DRAW TIME. Without a redraw the panel would fill
    // the screen with a chart still drawn for a grid cell — tick labels and
    // margins sized for 19rem, stretched.
  }, [rows, ranges, volumeRows, xRange, spec, ticks, spot, flipStrike, height])

  // Double-click is Plotly's own reset and would AUTORANGE, undoing the
  // centred zero the grid depends on. It is turned off above and routed here
  // so it restores the same home the corner button does.
  useEffect(() => {
    const node = host.current
    if (!node) return
    const on = () => reset()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(node as any).on?.('plotly_doubleclick', on)
    return () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ;(node as any).removeAllListeners?.('plotly_doubleclick')
    }
  }, [reset])

  useEffect(() => {
    const node = host.current
    return () => { if (node) Plotly.purge(node) }
  }, [])

  return (
    <div className="relative">
      {/* ONE FLEX ROW, ONE POSITIONING PARENT. These two were absolutely
          placed in DIFFERENT boxes — ⤢ against the Cell's card, ⟲ against this
          wrapper, which sits inside that card's padding — so they could never
          agree on where "top right" is and sat 4px apart. Two `absolute`
          elements line up only if they share an ancestor; the pair now does,
          and `items-start` plus one class list makes them the same size. */}
      <div className="absolute right-2 top-2 z-20 flex items-start gap-1">
        {onBig && (
          <button
            type="button"
            onClick={onBig}
            title={big ? 'Back to the grid (Esc)' : 'Maximise this chart'}
            aria-label={big ? 'Back to the grid' : 'Maximise this chart'}
            aria-pressed={big}
            className={BUTTON}
            style={{ background: 'var(--bg-card)', borderColor: 'var(--border)',
                     color: big ? '#10d4a3' : 'var(--text-2)' }}
          >
            {big ? '⤡' : '⤢'}
          </button>
        )}
        <button
          type="button"
          onClick={reset}
          title="Reset this chart's zoom"
          aria-label="Reset this chart's zoom"
          className={BUTTON}
          style={{ background: 'var(--bg-card)', borderColor: 'var(--border)',
                   color: 'var(--text-2)' }}
        >
          {'⟲'}
        </button>
      </div>
      <div ref={host} style={{ width: '100%', height }} />
    </div>
  )
}
