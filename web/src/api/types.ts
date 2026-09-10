/**
 * The shapes `api/reads.py` actually returns, written down once.
 *
 * These are HAND-WRITTEN and that is a real cost: nothing checks them against
 * the FastAPI schema, so a renamed field on the server becomes `undefined`
 * here and renders as a blank rather than an error. Two things keep that
 * honest for now — the endpoints have contract tests on the Python side, and
 * every optional field below is drawn with an explicit em-dash fallback, so a
 * field that disappears looks missing rather than looking like zero. That is
 * the project's blank-not-zero rule carried into the browser.
 *
 * Generating them from /openapi.json is the obvious improvement and is not
 * done yet; see docs/m6_migration_plan.md.
 */

/** One row of the transform sweep. The keys have spaces because the sweep is
 *  a pandas frame whose column names are display labels — `serialize.py`
 *  passes them through unchanged rather than inventing a second vocabulary
 *  that would then have to be kept in step with the page's. */
export interface SweepRow {
  'Front Expiry': string
  'Back Expiry': string
  'Put Strike': number
  'Call Strike': number
  'Diagonal Mark': number | null
  'Transform Mark': number | null
  'Transform Diff': number
  'IV Ratio': number | null
  /** The display KEYS behind the two labels — what every endpoint taking an
   *  `expiry` wants. Served rather than recovered from the label, so no
   *  client has to know that "2026-09-23 (19d)" is not an address. See
   *  api.computed.add_raw_expiries, including what it cannot do for the
   *  third Friday's a.m. contract. */
  'Front Raw': string
  'Back Raw': string
}

export interface ScanBands {
  eligible: number
  approaching: number
  total: number
  /** The 5-point line. Read from the response, never written here: it is
   *  duplicated in four places in Python already (DEBT-031) and a fifth copy
   *  in TypeScript would be the one nobody remembers to change. */
  threshold: number
  approaching_from: number
}

export interface ScanResponse {
  snapshot_id: number
  spot: number
  count: number
  returned: number
  bands: ScanBands
  rows: SweepRow[]
}

/** A Mission Control opportunity card.
 *
 *  `*_label` fields are pre-formatted by core/format.py. Prefer them over the
 *  raw values everywhere they exist — that is the whole reason they are sent.
 *  The raw numbers stay for sorting and comparison only. */
export interface Card {
  /** Present on non-ATM cards only. The join key `/mission/new` answers in;
   *  built by api/computed.pair_key so it is never rebuilt here. */
  key?: string
  front_raw: string
  back_raw: string
  front_label: string
  back_label: string
  put_strike: number
  call_strike: number
  gap: number
  iv_ratio: number | null
  duration: string | null
  duration_label: string
  eta_minutes: number | null
  eta_label: string
  spark: string
  trend_up: boolean
  /** Non-ATM cards only. */
  is_live?: boolean
  current_gap?: number | null
  max_gap?: number
  hit_count?: number
  last_seen?: string
  last_seen_ago?: string
  outside_lookback?: boolean
}

export interface CardsResponse {
  snapshot_id: number
  spot: number
  approaching_cards: Card[]
  likely_next: Card[]
  n_approaching: number
}

export interface NonAtmResponse {
  snapshot_id: number
  spot: number
  lookback: number
  registry_entries: number
  cards: Card[]
  in_window_total: number
  fallback_used: number
}

export interface NewResponse {
  snapshot_id: number
  /** null means this is the first recording and nothing can be new yet — a
   *  different state from "nothing was new", and drawn differently. */
  compared_against_snapshot: number | null
  eligible_count: number
  new_count: number
  new_keys: string[]
  recorded: boolean
}


// ─────────────────────────────────────────────────────────────────────────────
// The Gamma Exposure tab
// ─────────────────────────────────────────────────────────────────────────────

/** The four strike panels /mission/gamma can draw — one per view on the tab,
 *  minus the three that are all read off `gamma`. */
export type Measure = 'gamma' | 'vgex' | 'delta' | 'vanna' | 'charm'

/** One row of a by-strike frame. The columns DIFFER BY MEASURE — `call_gex`
 *  for gamma, `call_dex` for delta, `call_vex`/`call_cex` for the second-order
 *  pair — so this is indexed rather than fully named. `strike` is the only
 *  column every measure has, and the volume/OI columns ride only on gamma. */
export interface StrikeRow {
  strike: number
  [column: string]: number | null
}

/** An option the `expiry` parameter accepts, with the wording the page uses.
 *  The third Friday appears twice — SPX lists two contracts for it, one
 *  settling on the open and one on the close. */
export interface ExpiryOption {
  key: string
  label: string
  dte: number
  /** "2026-09-18" — the calendar date behind the key, with the (AM) suffix
   *  stripped. Present so nothing here has to parse a display key. */
  date: string
  /** "Wed, Sep 9" and "3 DTE" — the picker's two captions, formatted by
   *  api/computed so they read the same as everywhere else. */
  day_label: string
  dte_label: string
  is_third_friday: boolean
  is_am: boolean
  /** Which of the filter windows this expiry falls inside, by key —
   *  core.contract.EXPIRY_FILTERS. MEMBERSHIP, NOT A BOUND: the picker
   *  filters with a set lookup and never compares two dates, because a date
   *  comparison here would run in the viewer's timezone. */
  filters: string[]
  /** Totals across every strike of this expiry, on by_strike's scale, so a
   *  board figure equals the sum of that expiry's bars. */
  call_gex: number
  put_gex: number
  /** The same two through core.format.fmt_money. `put_label` carries the
   *  minus sign: the picker draws puts as the negative side, as the
   *  "Call vs Put" panel does. */
  call_label: string
  put_label: string
}

/** One strike's session high and low exposure — the "wicks".
 *
 *  NOT the change since the open. gexstream, whose chart this follows,
 *  defines these as the per-strike session high/low tracked since the 4:00 PM
 *  ET reset; core.gex.session_range_by_strike says why the distinction
 *  matters. `net_low`/`net_high` are the signed figure's range and are
 *  read by the NET panels, which draw one signed bar instead of two.
 *
 *  THE COLUMN NAMES ARE THE SAME FOR EVERY MEASURE and say "low" and "high"
 *  rather than naming a Greek. A wick is two numbers behind a bar; which
 *  exposure produced them is the request's business (`measure`), not the
 *  row's, and naming them per measure would buy a branch in the drawing code
 *  and nothing else. */
export interface SessionRangeRow {
  strike: number
  call_low: number
  call_high: number
  put_low: number
  put_high: number
  net_low: number
  net_high: number
}

export interface SessionRangeResponse {
  session_date: string
  /** Echoed so a panel can tell which measure's range it is holding — the
   *  same reason `/mission/gamma` echoes it. */
  measure: Measure
  rows: SessionRangeRow[]
}

/**
 * One bucket of the strike-flow panel.
 *
 * CONTRACTS TRADED, NOT ORDER FLOW. The reference panel this was modelled on
 * splits every bar into calls/puts bought and sold. That needs each individual
 * print measured against the quote standing at that instant, and this
 * dashboard snapshots the chain rather than recording trades — so the split
 * cannot be derived and is not attempted. `basis` on the response says so in
 * words; nothing here may be labelled "bought" or "sold".
 *
 * `call_volume`, `put_volume` and `total` are NULLABLE, and the null is
 * load-bearing: the session's first bucket has no predecessor to difference
 * against, and a running total that fell came from a partial poll. Both mean
 * "not known". Drawing either as 0 would claim a quiet market.
 */
export interface StrikeFlowRow {
  /** ISO, ZONED, in market time — already the string the label was made from. */
  bucket: string
  /** The finished hover caption ("Tue 8/11 12:20 PM"), formatted server-side
   *  because a browser formats a timestamp in the VIEWER's timezone. */
  label: string
  call_volume: number | null
  put_volume: number | null
  total: number | null
  spot: number
  /** 60 in the first and last half hour, 300 midday — the collector's own
   *  cadence. A bar is comparable only to another of the same width. */
  bucket_secs: number
}

export interface StrikeFlowResponse {
  session_date: string
  strike: number
  expiry: string | null
  /** What these numbers are, and what they are not, in words. Shown on the
   *  panel rather than kept in a comment. */
  basis: string
  rows: StrikeFlowRow[]
}

/**
 * One point on one line of a "through the session" panel.
 *
 * TIDY, NOT PIVOTED. The server returns one row per strike per snapshot,
 * sorted by strike then time, so the browser groups into traces without
 * sorting or deciding anything. A pivoted shape would need the front end to
 * know which strikes exist before it can name the columns.
 */
export interface SessionLineRow {
  /** ISO, ZONED UTC. Plotly's date axis handles the conversion for display;
   *  nothing here reformats it (see StrikeFlowRow.label for why that matters
   *  when the string is TEXT rather than an axis position). */
  timestamp: string
  strike: number
}

export interface NetVolumeRow extends SessionLineRow {
  /** Calls traded minus puts traded, CUMULATIVE for the session — these lines
   *  climb all day. Negative means the puts led. */
  net_volume: number
  call_volume: number
  put_volume: number
}

export interface NetVolumeResponse {
  session_date: string
  expiry: string | null
  dte_max: number | null
  /** How many strikes were asked for. Echoed so a stored response can be read
   *  later without the request beside it. */
  count_requested: number
  basis: string
  count: number
  rows: NetVolumeRow[]
}

export interface GexTimelineRow extends SessionLineRow {
  /** Dollars per 1% move, each snapshot scaled by its OWN spot price. */
  net_gex: number
}

/**
 * The headline strip above the gamma panel.
 *
 * COMPUTED SERVER-SIDE FROM THE LINES RETURNED, not summed here. Added up in
 * the browser these would be a second definition of the same figure, and the
 * strip and the chart would eventually disagree by a number nobody can
 * account for. Null is "no reading", which is not zero.
 */
export interface GexTotals {
  now: number | null
  at_open: number | null
  change: number | null
  levels: number[]
}

export interface GexTimelineResponse {
  session_date: string
  expiry: string | null
  dte_max: number | null
  count_requested: number
  basis: string
  totals: GexTotals
  count: number
  rows: GexTimelineRow[]
}

/**
 * One point on the dealer-structure chart: an (expiry, strike) that traded.
 *
 * `radius` ARRIVES COMPUTED and is relative to the busiest point on the whole
 * band — not on what survived the trim. Rescaling in the browser would make a
 * quiet expiry look busy simply because its neighbours were dropped, and the
 * sizing rule (sqrt, floors and ceilings) is core/dealer.py's to own.
 */
export interface BubbleRow {
  /** The display key — the third Friday appears twice, a.m. and p.m. */
  expiry: string
  /** How the column is labelled, built server-side from the expiry and dte. */
  expiry_label: string
  /** Days to expiry, and the column order. */
  expiry_order: number
  strike: number
  call_volume: number
  put_volume: number
  total_volume: number
  /** Put/call ratio. `null` where neither side traded; the server sends
   *  Infinity as null where puts traded and calls did not — an undefined
   *  ratio, which is NOT the same as balanced. Read `flow` instead. */
  pcr: number | null
  /** Premium that changed hands: mark x volume x 100. Null where the chain
   *  carries no mark — a dash, not an invented zero. */
  notional: number | null
  /** The verdict for this point, bucketed server-side.
   *
   *  THE VOCABULARY DEPENDS ON THE MEASURE, and the two sets do not overlap
   *  so they cannot be confused: 'call' | 'put' | 'balanced' under `volume`,
   *  and 'long' | 'short' | 'flat' under `gex` / `vgex`. A strike can be
   *  call-dominated and short gamma at the same time, so one set of words
   *  for both would assert a link that is not there. */
  flow: string
  radius: number

  // ── gamma views only (`gex` / `vgex`) ────────────────────────────────
  // Absent on the volume rows. The server sends one shape or the other and
  // names which in `measure`; these are optional rather than nullable so
  // TypeScript makes a caller check the measure before reading them.
  /** Dollars per 1% move, dealer-signed: calls positive, puts negative. */
  net_gex?: number
  /** |net_gex| — what the radius is drawn from and what the trim ranks on.
   *  Sent rather than derived so the browser holds no second copy of the
   *  sizing rule. */
  abs_net_gex?: number
  /** Call + put gamma REGARDLESS of sign. Differs from abs_net_gex where the
   *  two sides cancel: a strike with 10B of each has abs_gex 20B and net_gex
   *  0 — a large installed position that pushes nowhere. */
  abs_gex?: number
  call_gex?: number
  put_gex?: number
  call_oi?: number
  put_oi?: number
}

/** The three views of the bubble grid. `volume` is where trading went today;
 *  `gex` is where dealer gamma sits (open interest); `vgex` is the gamma this
 *  session added (today's volume). */
export type BubbleMeasure = 'volume' | 'gex' | 'vgex'

export interface BubbleResponse {
  snapshot_id: number
  /** THIS snapshot's spot, not the newest one's. Everything here is a
   *  distance from it. */
  spot: number
  trimmed: boolean
  band_percent: number
  basis: string
  count: number
  /** Which of the three views these rows are. Echoed by the server so a
   *  client never has to infer the row shape from which fields happen to be
   *  present. */
  measure: BubbleMeasure
  rows: BubbleRow[]
}

/**
 * One strike's volume against the overnight change in open interest.
 *
 * THE CHANGE IS YESTERDAY'S. Open interest is republished once, overnight, so
 * today's figure minus yesterday's is what was opened or closed during
 * YESTERDAY's session. `total_volume` is today's and is context beside the
 * verdict, not the evidence behind it.
 */
export interface PositioningRow {
  strike: number
  call_volume: number
  put_volume: number
  /** Today's, for context. */
  total_volume: number
  settled_call_volume: number | null
  settled_put_volume: number | null
  /** The prior session's volume — what every verdict is measured against. */
  settled_volume: number | null
  /** Null with no prior session to difference against. Blank, not zero:
   *  "unknown" reported as no change would call the whole board churn. */
  delta_oi: number | null
  /** The word for what happened, or an em dash where none of the named
   *  states fit — which is most strikes, and saying so is the honest
   *  default. */
  verdict: string | null
  /** How to colour it. Server-chosen so the word and its tone cannot
   *  disagree. */
  tone: string | null
}

export interface PositioningResponse {
  snapshot_id: number
  session_date: string
  expiry: string | null
  spot: number
  /** Whether the bell is ringing RIGHT NOW, decided server-side. It is a
   *  clock comparison against market holidays in market time, which in a
   *  browser would run in the viewer's timezone. */
  market_open: boolean
  /** What to call each side of the panel. "Yesterday" never needs
   *  qualifying; today's volume is "Live" while it is still being written
   *  and "Today" once it is final. */
  day_labels: { prior: string; current: string }
  /** The strike nearest spot, so the row can be marked without the browser
   *  deciding what "nearest" means. Null on an empty board. */
  atm_strike: number | null
  /** What each verdict means, in plain words, in reading order. Served
   *  rather than written here: the wording IS the definition of the band. */
  glossary: { verdict: string; tone: string; meaning: string }[]
  basis: string
  count: number
  rows: PositioningRow[]
}

/** The filter windows the picker offers, keyed as the server keys them. */
export interface ExpiryFilter {
  key: string
  label: string
}

/** Tick positions and text from core.format.money_ticks, so a billion reads
 *  "12.2B" here exactly as it does on the Streamlit tab, rather than Plotly's
 *  SI "12.2G". Empty arrays mean "decide for yourself". */
export interface AxisTicks {
  tickvals: number[]
  ticktext: string[]
}

export interface GammaResponse {
  snapshot_id: number
  measure: Measure
  expiry: string | null
  spot: number
  count: number
  rows: StrikeRow[]
  expiries: ExpiryOption[]
  /** The filter windows the picker offers — core.contract.EXPIRY_FILTERS. */
  expiry_filters?: ExpiryFilter[]
  /** Which expiry the tab should OPEN on, decided by the server because it is
   *  a clock rule — 0 DTE during the session, the next expiry once the day is
   *  over. See core/expiry.py:default_scope. Null means "no opinion", and the
   *  tab then opens on the whole board. */
  default_expiry?: string | null
  ticks: AxisTicks
  /** Absent on `delta`, which has no summary — see api/computed.delta_exposure. */
  summary?: Record<string, number | null>
  /** Pre-formatted `summary`, key for key. Absent on `delta` for the same
   *  reason. Prefer these over the raw values for anything shown. */
  labels?: Record<string, string>
  /** gamma and vgex. */
  flip_strike?: number | null
  /** vgex only — sum of positive net exposure over sum of absolute, so it is
   *  bounded in [0, 1]. NOT `summary.ratio`, which is the larger side over
   *  the smaller and unbounded; core.gex.flow_ratio says why both exist. */
  flow_ratio?: number | null
  /** vanna and charm only. Their ABSENCE on gamma and delta is the signal:
   *  those two weight a column the broker sent and rest on no assumption. */
  assumptions?: {
    risk_free_rate: number
    dividend_yield: number
    day_remainder: number
    snapshot_timestamp: string
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// The Calendar Edge tab
// ─────────────────────────────────────────────────────────────────────────────

/** Where a Plotly x-axis should skip empty time. Served, not written here:
 *  which breaks are safe was established empirically and a holiday break
 *  corrupts point positioning — see core/series.py for the finding. */
export interface RangeBreak {
  bounds: (string | number)[]
  /** Narrowed to what Plotly actually accepts rather than left as `string`.
   *  The server sends only `hour`; typing it loosely would have made a
   *  mismatch between the two a runtime surprise instead of a compile error. */
  pattern?: '' | 'day of week' | 'hour'
}

/** One row of the transform-mark history. The three derived marks are the
 *  server's — see core.scanner.add_mark_columns. `null` appears on the gap
 *  rows `break_sessions` inserts, which is how the line breaks across a
 *  weekend instead of drawing a connector over it. */
export interface MarkRow {
  timestamp: string
  spx: number | null
  front_call_mark: number | null
  back_call_mark: number | null
  front_put_mark: number | null
  back_put_mark: number | null
  front_wing_call_mark: number | null
  front_wing_put_mark: number | null
  diagonal_mark: number | null
  transform_mark: number | null
  gap: number | null
}

/** A directed crossing of a short strike. The rule that decides one is
 *  `core.series.strike_crossings`; this is only the answer. */
export interface Crossings {
  up: { x: string; y: number }[]
  down: { x: string; y: number }[]
}

export interface MarksResponse {
  /** 09:30 for each trading day in the window, as naive wall-clock ISO
   *  strings. Empty on a single-session window, where one marker at the left
   *  edge adds no information — that rule is the server's, not this file's. */
  market_opens: string[]
  /** The [start, end] the x-axis is drawn on, for EVERY window, or null only
   *  when there is nothing on record to draw.
   *  Naive wall-clock, the server's decision (core.series.session_axis_range).
   *  A chart left to autorange ends where its DATA ends, so an afternoon of
   *  missing marks drew a short trading day instead of a visible gap. */
  session_axis_range: [string, string] | null
  count: number
  front: string
  back: string
  call_strike: number
  put_strike: number
  days: number
  rows: MarkRow[]
  /** SPX at every complete snapshot in the window, read SEPARATELY from the
   *  marks and drawn as the lower panel.
   *
   *  WHY IT IS NOT JUST `rows[].spx`. It was, and the lower panel stopped an
   *  hour before the market did. A marks row needs all six option legs
   *  priceable; on a 0DTE afternoon the front legs stop being quoted around
   *  15:00, the row is dropped, and the index went with it -- even though it
   *  is recorded on the snapshot and never stopped. `rows[].spx` is still
   *  there and still correct for the marks tooltip; this is the full line. */
  spx_rows: SpxRow[]
  /** The 5-point line. Read, never written here — DEBT-031 already has four
   *  copies of it in Python and a fifth in TypeScript would be the worst one. */
  threshold: number
  rangebreaks?: RangeBreak[]
  crossings?: Crossings
}

/** One IV-ratio regime. Colour and wording are the server's: the tab prints
 *  these labels as prose under the chart, and a second copy in the client
 *  would be a second opinion about what a regime is. */
export interface RatioBand {
  low: number | null
  high: number | null
  colour: string
  label: string
}

export interface AtmPairRow {
  timestamp: string
  front_iv: number | null
  back_iv: number | null
  iv_ratio: number | null
  /** Decimal hour of the reading — the scatter's colour axis. Optional
   *  because /mission/strike-iv reuses this row shape and has no scatter. */
  hod?: number | null
}

export interface AtmPairResponse {
  count: number
  front: string
  back: string
  days: number
  iv_units: string
  rows: AtmPairRow[]
  bands: RatioBand[]
  rangebreaks: RangeBreak[]
  /** The padded low/high the scatter's R=1 line spans, shared by both axes so
   *  the diagonal sits at 45°. Null on an empty frame. */
  scatter_domain: [number, number] | null
  /** As on MarksResponse — see there. */
  market_opens: string[]
  /** As on MarksResponse — see there. */
  session_axis_range: [string, string] | null
  /** Present exactly when there is too little history to trust a percentile.
   *  A string, not a count — the wording is iv_engine's. */
  sample_warning: string | null
}

/** The four figures above the Calendar Edge charts. Every one is null-able:
 *  an expiry with no IV at its ATM strike is a real state on a thin chain. */
export interface EdgeHeadline {
  snapshot_id: number
  spot: number
  front_iv: number | null
  back_iv: number | null
  ratio: number | null
  iv_index: number
}


/** What the four pair controls offer, and the narrowing they apply.
 *  Served rather than derived here: back expiries exclude anything not
 *  strictly later than the front, and the strike lists are the intersection
 *  of both legs — see api.computed.pair_controls. */
export interface ControlsResponse {
  snapshot_id: number
  spot: number
  expiries: ExpiryOption[]
  /** Already narrowed to dates after `front`. Empty when the furthest expiry
   *  collected is chosen as the front leg — then `back` is null too. */
  back_expiries: ExpiryOption[]
  front: string | null
  back: string | null
  put_strikes: number[]
  call_strikes: number[]
  put_strike: number | null
  call_strike: number | null
}


// ─────────────────────────────────────────────────────────────────────────────
// The Strike Detail tab
// ─────────────────────────────────────────────────────────────────────────────

/** One leg of the diagonal, priced in both expiries.
 *
 *  `null` means the chain had no figure, and is drawn as "N/A". It is NOT
 *  zero: a zero mark reads as a measured claim that the option is worthless,
 *  which is a different statement from "not recorded". That distinction is
 *  the project's blank-not-zero rule and it matters most here, where the
 *  numbers are prices.
 *
 *  `front_exact`/`back_exact` are false when the chain did not list the
 *  strike asked for and the nearest one was used instead — shown, because a
 *  neighbouring contract's price under this strike's heading would otherwise
 *  be indistinguishable from the real thing. */
export interface StrikeLeg {
  label: string
  strike: number
  front_iv: number | null
  back_iv: number | null
  front_mark: number | null
  back_mark: number | null
  iv_ratio: number | null
  front_exact: boolean
  back_exact: boolean
}

/** The latest ATM IV for one expiry, and its move since the previous record.
 *  `change` is null with only one record on file — unknown, not flat. */
export interface AtmHeadline {
  role: string
  expiry: string
  atm_iv: number | null
  change: number | null
}

export interface StrikeDetailResponse {
  snapshot_id: number
  legs: StrikeLeg[]
  expiries: AtmHeadline[]
}

export interface StrikeIvResponse {
  front: string
  back: string
  days: number
  iv_units: string
  call_strike: number
  put_strike: number
  /** Either side can be empty: a strike can have history in one expiry and
   *  none in the other, and half a ratio is not an answer. */
  calls: AtmPairRow[]
  puts: AtmPairRow[]
  rangebreaks: RangeBreak[]
}


/**
 * The strip above every tab. `amber_at` / `red_at` are seconds and are the
 * SERVER's — how late is late depends on which market session is happening
 * now, and the client is not the thing that knows that. `market_closed` says
 * the two are meaningless rather than merely large.
 */
export interface HeaderResponse {
  snapshot_id: number
  snapshot_timestamp: string
  session_date: string
  spx_price: number
  vix: number | null
  change: {
    points: number
    percent: number
    arrow: string
    colour: string
    reference_label: string
  }
  gex_label: string
  age_seconds: number
  dot: 'green' | 'amber' | 'red'
  session: string | null
  expected_interval: number | null
  amber_at: number
  red_at: number
  market_closed: boolean
}


/** One lookback window of the ATM IV ratio's own history. Every figure is
 *  the server's: `low`/`high` bound the window, `position_pct` places the
 *  marker between them, and `percentile` says how much of the record sits
 *  below today. Three different questions — deriving any from the others
 *  gives a plausible wrong answer. Nulls mean no overlapping history, which
 *  is not a zeroth percentile. */
export interface HistoricalWindow {
  label: string
  days: number
  low: number | null
  high: number | null
  position_pct: number
  percentile: number | null
  band: 'HIGH' | 'MID' | 'LOW'
  colour: string
  observations: number
}

export interface HistoricalStatsResponse {
  snapshot_id: number
  front: string
  back: string
  current: number | null
  windows: HistoricalWindow[]
}

/**
 * One entry lock — the price a diagonal was actually filled at.
 *
 * SHAPED BY PYTHON, NOT BY THIS FILE. Every field here is stored verbatim in
 * `entry_locks.json` by `state/entry_locks.py`, including `key`, which the
 * server sends rather than letting the client build: the key has a format
 * (whole-number strikes) and a client that spelled it itself would eventually
 * address a lock the file does not have under that name.
 *
 * `journal_trade_id` is always null today. The Journal is out of scope
 * (Chandan, 2026-09-07); `mode: 'monitor_and_log'` records the intention so
 * the eventual Journal row can be built from this record instead of a second
 * copy of the same fill.
 */
export interface EntryLock {
  key: string
  lock_id: string
  front_expiry: string
  back_expiry: string
  put_strike: number
  call_strike: number
  entry_diagonal_mark: number
  locked_at: string
  mode: 'monitor_only' | 'monitor_and_log'
  journal_trade_id: string | null
}

export interface LocksResponse {
  locks: EntryLock[]
  count: number
}

/** One reading of the index. `timestamp` is naive wall-clock New York, the
 *  same convention every chart series uses (DEBT-030). */
export interface SpxRow {
  timestamp: string
  spx: number | null
}

// ── Ask panel (POST /mission/ask) ────────────────────────────────────────
/** One exchange in the chat. The server trims to the last few, but the whole
 *  visible conversation is what the panel holds. */
export interface TutorTurn {
  role: 'user' | 'assistant'
  content: string
}

export type Effort = 'low' | 'medium' | 'high' | 'max'

export interface AskRequest {
  question: string
  history: TutorTurn[]
  /** Omitted means "the newest snapshot", which is what the live board shows. */
  snapshot_id?: number
  /** Promoted to the head of the fallback chain. Omitted means the chain's
   *  own default, which is whichever Gemini Flash is newest today. */
  model?: string
  effort?: Effort
}

/** One rung of the live fallback chain. `context` is the token window, which
 *  is the only figure that meaningfully separates these for a reader. */
export interface TutorModel {
  id: string
  provider: string
  name: string
  context: number
}

export interface TutorModelsResponse {
  models: TutorModel[]
  default: string | null
  efforts: Effort[]
}

/** The answer, plus what it was read FROM. The echoed spot, time and pin are
 *  not decoration: this chat's whole claim is that it is reading the same
 *  figures as the panels, and an answer with no visible snapshot behind it is
 *  one the reader cannot check. */
export interface AskResponse {
  answer: string
  provider: string
  model: string
  snapshot_id: number
  spot: string
  session_time: string
  /** The chain fell past the model that was picked. Shown, because a reader
   *  who chose one model and silently got another would draw conclusions
   *  about the wrong one. */
  fell_back: boolean
  effort: Effort
  pin: {
    pin_possible: boolean
    reason: string
    candidate: string
    support: string
    resistance: string
  }
}
