/**
 * The one place this app talks to the server.
 *
 * NO TOKEN APPEARS HERE. Requests go to a same-origin `/api/...` path and the
 * Vite proxy (see vite.config.ts) attaches `X-API-Token` from the server's own
 * environment. A token bundled into JavaScript is a published token — it would
 * sit in `dist/` in plain text and be readable by anyone the page is ever
 * shown to. This is also why every URL below is relative.
 */
import { useQuery } from '@tanstack/react-query'

import type {
  AtmPairResponse,
  CardsResponse,
  ControlsResponse,
  EdgeHeadline,
  BubbleResponse,
  GammaResponse,
  GexTimelineResponse,
  NetVolumeResponse,
  PositioningResponse,
  SessionRangeResponse,
  HeaderResponse,
  HistoricalStatsResponse,
  MarksResponse,
  Measure,
  NewResponse,
  NonAtmResponse,
  ScanResponse,
  StrikeDetailResponse,
  StrikeIvResponse,
  StrikeFlowResponse,
} from './types'

/** Thrown with the server's own words when it says something useful.
 *
 *  api/reads.py answers a database on an old schema with a 503 and a sentence
 *  explaining which program applies the migration. Collapsing that into
 *  "request failed" would throw away the only part worth reading. */
export class ApiError extends Error {
  // A plain field rather than a `readonly` constructor parameter: this project
  // builds with `erasableSyntaxOnly`, which rejects parameter properties
  // because they emit code rather than erasing to nothing.
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    // FastAPI puts the explanation in `detail`. Read it when it is there and
    // fall back to the status line when it is not, rather than assuming a
    // shape the error path cannot guarantee.
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // A non-JSON error body is not itself an error worth reporting over the
      // status we already have.
    }
    throw new ApiError(response.status, detail)
  }

  return (await response.json()) as T
}

/**
 * Shared query defaults.
 *
 * `refetchOnWindowFocus` is left ON deliberately: every endpoint below is a
 * GET that BUG-040 made genuinely read-only, so tabbing back to the browser
 * refreshes the view and changes nothing on the server. That was not true
 * before — `/mission/new` used to record by default, and a focus refetch
 * would have advanced the comparison registry with no user action behind it.
 * The split is what makes this setting safe, so the two are noted together.
 *
 * The record only moves when the collector writes a snapshot, roughly once a
 * minute, so a 30-second staleTime refetches at about the rate new data
 * actually arrives instead of faster than it can change.
 */
const SHARED = { staleTime: 30_000, retry: 1 } as const

export function useScan(limit = 250) {
  return useQuery({
    queryKey: ['scan', limit],
    queryFn: () => get<ScanResponse>(`/mission/scan?limit=${limit}`),
    ...SHARED,
  })
}

export function useCards() {
  return useQuery({
    queryKey: ['cards'],
    queryFn: () => get<CardsResponse>('/mission/cards'),
    ...SHARED,
  })
}

export function useNonAtm(lookback: number) {
  return useQuery({
    queryKey: ['non-atm', lookback],
    queryFn: () =>
      get<NonAtmResponse>(`/mission/non-atm?lookback=${lookback}`),
    ...SHARED,
  })
}

export function useNewPairs() {
  return useQuery({
    queryKey: ['new'],
    queryFn: () => get<NewResponse>('/mission/new'),
    ...SHARED,
  })
}

/**
 * One strike panel.
 *
 * TWO CALLS ARE NEEDED TO DRAW THIS TAB, not one, and that is not an
 * inefficiency to tidy away. The volume and open-interest panels under the
 * chart are drawn from `call_volume`/`call_oi`, which ride only on the gamma
 * measure — they are the same two panels whatever view is selected above
 * them. So the tab always holds `useGamma('gamma')` and, when the selected
 * view is something else, a second call for that measure. The Streamlit page
 * does exactly this: `shown` is always the gamma frame, `second` is the
 * selected one.
 */
/**
 * Each strike's session high and low, for the ladder's wicks.
 *
 * A SEPARATE REQUEST FROM `useGamma`, deliberately. The bars are one
 * snapshot; this walks every snapshot of the session — roughly a hundred
 * times the rows — and the panel must not wait on it. The ladder draws on the
 * gamma response and the wicks appear underneath when this resolves, which is
 * why `StrikeChart` treats an empty range list as normal rather than as an
 * error.
 *
 * SCOPED THE SAME WAY THE BARS ARE. A whole-board range behind a
 * single-expiry bar would put the bar inside a wick belonging to twenty other
 * contracts — a bar sitting a tenth of the way up its own day's range, which
 * reads as a strike that has collapsed and is nothing of the kind.
 */
/**
 * ...AND SCOPED TO THE SAME MEASURE. The wicks were gamma-only, so the other
 * four panels drew none (Chandan, 2026-09-06: "I want that same wick to be
 * present in all the six chart"). `measure` must travel for the same reason
 * `expiry` does, and more sharply: a gamma range behind a charm bar is a
 * different Greek at a scale that still looks entirely plausible.
 *
 * IT IS ALSO IN THE QUERY KEY, which is what stops the five panels on screen
 * from serving each other's ranges out of the cache.
 */
export function useSessionRange(expiries: string[], measure: Measure = 'gamma',
                                dteMax?: number) {
  const query = new URLSearchParams()
  for (const key of expiries) query.append('expiry', key)
  query.set('measure', measure)
  if (dteMax !== undefined) query.set('dte_max', String(dteMax))
  return useQuery({
    queryKey: ['session-range', expiries, measure, dteMax],
    queryFn: () => get<SessionRangeResponse>(`/strikes/session-range?${query}`),
    ...SHARED,
  })
}


/**
 * Contracts traded at ONE strike, bucketed through the session.
 *
 * A SEPARATE REQUEST, and a per-strike one: it walks every snapshot of the
 * day, so it is the same cost as `useSessionRange` and must not be folded
 * into the request the bars are drawn from.
 *
 * `enabled` on a null strike rather than a guard at the call site. The strike
 * arrives from the gamma response, so this hook is mounted before there is a
 * strike to ask about, and firing `?strike=null` would cache a 422 under a key
 * the real question will later want.
 */
export function useStrikeFlow(strike: number | null, expiries: string[]) {
  const query = new URLSearchParams({ strike: String(strike ?? 0) })
  // ONE EXPIRY OR THE WHOLE BOARD. The endpoint scopes to a single display
  // key; a multi-expiry selection is answered with the board rather than with
  // the first key alone, which would be one contract wearing five contracts'
  // label.
  if (expiries.length === 1) query.set('expiry', expiries[0])
  return useQuery({
    queryKey: ['strike-flow', strike, expiries],
    queryFn: () => get<StrikeFlowResponse>(`/strikes/flow?${query}`),
    enabled: strike !== null,
    ...SHARED,
  })
}


/**
 * The two "through the session" panels: a few strikes, traced across the day.
 *
 * ONE HOOK FOR BOTH, because they are the same request with a different
 * endpoint and the pair must stay in step — they sit one above the other and
 * are read together, so a difference in how they scope or cache would show up
 * as two charts disagreeing about which session they are describing.
 *
 * `dteMax` is what makes the gamma panel the 0DTE panel. It is passed only
 * when set, so the parameter is absent rather than `dte_max=null` — which the
 * endpoint would reject with a 422 and cache under the key the real question
 * later wants.
 */
function sessionLines(expiries: string[], count?: number, dteMax?: number) {
  const query = new URLSearchParams()
  // ONE EXPIRY OR THE WHOLE BOARD — the same rule as useStrikeFlow. The
  // endpoint scopes to a single display key, so a multi-expiry selection is
  // answered with the board rather than with the first key wearing five
  // contracts' label.
  if (expiries.length === 1) query.set('expiry', expiries[0])
  if (count !== undefined) query.set('count', String(count))
  if (dteMax !== undefined) query.set('dte_max', String(dteMax))
  return query
}

export function useNetVolume(expiries: string[], count?: number) {
  const query = sessionLines(expiries, count)
  return useQuery({
    queryKey: ['net-volume', expiries, count],
    queryFn: () => get<NetVolumeResponse>(`/strikes/net-volume?${query}`),
    ...SHARED,
  })
}

export function useGexTimeline(expiries: string[], count?: number,
                               dteMax?: number) {
  const query = sessionLines(expiries, count, dteMax)
  return useQuery({
    queryKey: ['gex-timeline', expiries, count, dteMax],
    queryFn: () => get<GexTimelineResponse>(`/strikes/gex-timeline?${query}`),
    ...SHARED,
  })
}

/**
 * Dealer structure — volume across expiry and strike at once.
 *
 * NO EXPIRY ARGUMENT, deliberately: this chart exists to COMPARE expiries and
 * scoped to one it is a single column. It is keyed on the snapshot alone,
 * which is also why it needs no filter state threaded through the tab.
 */
export function useDealerBubbles(trim = true) {
  return useQuery({
    queryKey: ['dealer-bubbles', trim],
    queryFn: () => get<BubbleResponse>(`/dealer/bubbles?trim=${trim}`),
    ...SHARED,
  })
}

/**
 * Positioning — today's volume against the overnight change in open interest.
 *
 * The expiry scopes BOTH sides of that comparison server-side; passing it is
 * all this hook does about it. One expiry's open interest subtracted from the
 * whole board's would report the rest of the board as an overnight
 * liquidation.
 */
export function useDealerPositioning(expiries: string[]) {
  const query = new URLSearchParams()
  if (expiries.length === 1) query.set('expiry', expiries[0])
  return useQuery({
    queryKey: ['dealer-positioning', expiries],
    queryFn: () => get<PositioningResponse>(`/dealer/positioning?${query}`),
    ...SHARED,
  })
}

export function useGamma(measure: Measure, expiries: string[]) {
  const query = new URLSearchParams({ measure })
  // REPEATED, NOT COMMA-JOINED. The endpoint takes `?expiry=a&expiry=b`;
  // a joined string would arrive as one display key nothing matches, and the
  // reply would be an empty chain rather than an error.
  for (const key of expiries) query.append('expiry', key)
  return useQuery({
    // The array is stringified into the key so two different selections are
    // two cache entries. TanStack hashes it structurally, so the order is
    // part of the identity — the same as the server's tuple cache key.
    queryKey: ['gamma', measure, expiries],
    queryFn: () => get<GammaResponse>(`/mission/gamma?${query}`),
    ...SHARED,
  })
}


/**
 * The Calendar Edge tab's two frames.
 *
 * BOTH ARE SERVED WHOLE, derived columns included. The marks response carries
 * `diagonal_mark`, `transform_mark` and `gap` — three subtractions that
 * define what a diagonal is worth, and that existed in four hand-written
 * copies before `core.scanner.add_mark_columns` collected them. The ATM
 * response carries the front/back inner join and the ratio, because a client
 * joining two series by position would divide readings from different minutes
 * and produce a number that reads perfectly and never existed.
 *
 * `days` is the window. The Streamlit tab's Today / 5D / 10D / 20D control
 * lives on THIS tab (it was moved here from the Scanner), so unlike the
 * Scanner's removed picker, this one belongs.
 */
export function useTransformMarks(
  front: string | null, back: string | null,
  callStrike: number | null, putStrike: number | null, days: number,
) {
  const ready = front !== null && back !== null && callStrike !== null && putStrike !== null
  const query = new URLSearchParams({
    front: front ?? '', back: back ?? '',
    call_strike: String(callStrike ?? ''), put_strike: String(putStrike ?? ''),
    days: String(days),
  })
  return useQuery({
    queryKey: ['marks', front, back, callStrike, putStrike, days],
    queryFn: () => get<MarksResponse>(`/pairs/transform-marks?${query}`),
    enabled: ready,
    ...SHARED,
  })
}

export function useAtmPair(front: string | null, back: string | null, days: number) {
  const ready = front !== null && back !== null
  const query = new URLSearchParams({ front: front ?? '', back: back ?? '', days: String(days) })
  return useQuery({
    queryKey: ['atmPair', front, back, days],
    queryFn: () => get<AtmPairResponse>(`/pairs/atm-pair?${query}`),
    enabled: ready,
    ...SHARED,
  })
}


/**
 * The strip above every tab.
 *
 * REFETCHED ON A CLOCK, unlike every other query here. The rest of this app
 * answers "what does the record say"; this one answers "is the record still
 * being written to", and a cached answer to that question is the failure it
 * exists to report. Sixty seconds is well inside the collector's own busiest
 * cadence, so the strip cannot claim data is late while a newer snapshot sits
 * unread.
 */
export function useHeader() {
  return useQuery({
    queryKey: ['header'],
    queryFn: () => get<HeaderResponse>('/mission/header'),
    refetchInterval: 60_000,
    staleTime: 0,
  })
}


/**
 * Where today's IV ratio sits in four windows of its own history.
 */
export function useHistoricalStats(front: string | null, back: string | null) {
  const ready = front !== null && back !== null
  const query = new URLSearchParams({ front: front ?? '', back: back ?? '' })
  return useQuery({
    queryKey: ['historicalStats', front, back],
    queryFn: () => get<HistoricalStatsResponse>(`/mission/historical-stats?${query}`),
    enabled: ready,
    ...SHARED,
  })
}


/**
 * The four figures above the Calendar Edge charts.
 *
 * A SEPARATE REQUEST FROM THE CHARTS ON PURPOSE. These come from the current
 * chain and the charts come from the history sweep; keyed together, changing
 * the day window would re-fetch a headline that cannot have moved.
 */
export function useEdgeHeadline(front: string | null, back: string | null) {
  const ready = front !== null && back !== null
  const query = new URLSearchParams({ front: front ?? '', back: back ?? '' })
  return useQuery({
    queryKey: ['edgeHeadline', front, back],
    queryFn: () => get<EdgeHeadline>(`/mission/edge-headline?${query}`),
    enabled: ready,
    ...SHARED,
  })
}


/**
 * The pair selection every pair-based tab is drawn from.
 *
 * Re-asked whenever the front or back changes, because the STRIKE LISTS
 * depend on the pair: only strikes present in both legs can be a diagonal.
 * Asking once and filtering here would mean re-implementing that
 * intersection, and the narrowing is a rule, not a convenience.
 */
export function useControls(front: string | null, back: string | null) {
  const query = new URLSearchParams()
  if (front) query.set('front', front)
  if (back) query.set('back', back)
  const qs = query.toString()
  return useQuery({
    queryKey: ['controls', front, back],
    queryFn: () => get<ControlsResponse>(`/mission/controls${qs ? `?${qs}` : ''}`),
    ...SHARED,
  })
}


/** The Strike Detail tab's two reads. Split because they change on different
 *  things: the priced legs move with the snapshot, the history with the
 *  window, and asking for 637 rows again to refresh four prices would be
 *  work done for nothing. */
export function useStrikeDetail(
  front: string | null, back: string | null,
  putStrike: number | null, callStrike: number | null,
) {
  const ready = front !== null && back !== null && putStrike !== null && callStrike !== null
  const query = new URLSearchParams({
    front: front ?? '', back: back ?? '',
    put_strike: String(putStrike ?? ''), call_strike: String(callStrike ?? ''),
  })
  return useQuery({
    queryKey: ['strikeDetail', front, back, putStrike, callStrike],
    queryFn: () => get<StrikeDetailResponse>(`/mission/strike-detail?${query}`),
    enabled: ready,
    ...SHARED,
  })
}

export function useStrikeIv(
  front: string | null, back: string | null,
  putStrike: number | null, callStrike: number | null, days: number,
) {
  const ready = front !== null && back !== null && putStrike !== null && callStrike !== null
  const query = new URLSearchParams({
    front: front ?? '', back: back ?? '',
    put_strike: String(putStrike ?? ''), call_strike: String(callStrike ?? ''),
    days: String(days),
  })
  return useQuery({
    queryKey: ['strikeIv', front, back, putStrike, callStrike, days],
    queryFn: () => get<StrikeIvResponse>(`/mission/strike-iv?${query}`),
    enabled: ready,
    ...SHARED,
  })
}
