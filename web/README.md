# web — the M6 rebuild

The React screen that will replace the Streamlit dashboard, one tab at a time.
**Streamlit is still the live dashboard.** Nothing here is in use yet, and
`docs/m6_migration_plan.md` is the plan this follows.

## Running it

Two processes, in this order. The screen has no other way to reach the record.

```bash
# 1. the read-only API, from the project root
python -m uvicorn api.app:app --host 127.0.0.1 --port 8899

# 2. the dev server, from web/, with the token in the environment
SPX_API_TOKEN=... npm run dev
```

The token goes in the **server's** environment, never in the code. `vite.config.ts`
proxies `/api` to port 8899 and attaches `X-API-Token` there, so the browser sees
one origin and the token never enters the bundle. A token shipped to the browser
is a published token — it would sit in `dist/` in plain text.

If the API is not running, every panel says so in its own words and the page
still draws. That is deliberate: a scanner showing nothing looks like a quiet
market, which is the most expensive thing a trading screen can get wrong.

## The rule this code follows

**No formula may exist in the new language.** Anything on screen was computed
once, in Python, under test. TypeScript chooses words, colours and layout, and
sorts rows — nothing else.

The line falls in a specific place, and it is worth knowing where:

* `value.toFixed(2)` is presentation of a number already decided. Allowed.
* Turning `132.4` minutes into `"~2.2 hr"` is a **rounding rule**. Not allowed —
  that is `core/format.py:fmt_eta`, and the server sends `eta_label` beside the
  raw value so this code never has to.
* Building `"2026-09-23|2026-09-29|7710|7725"` to match a pair against
  `/mission/new` would be a **format** duplicated in two languages, and one that
  fails silently: a mismatched key does not error, it reports nothing as new,
  forever. So `api/computed.pair_key` builds it and the card carries it, and the
  client does a set-membership test.

Where a label is needed and does not exist, the fix is a field on the response,
not a function here.

## What is built

| Tab | State |
|-----|-------|
| Scanner | **built** — non-ATM cards, Likely Next cards, full sweep table. A card or a row is a link: one click opens that pair in Calendar Edge |
| Gamma Exposure | **part built** — headline strip, the expiry picker, and all seven strike views as a price ladder with session wicks. The rest of the Streamlit tab is not here; see below |
| Calendar Edge | **part built** — read-only. The four headline figures and all four charts. No entry locks; see below |
| Strike Detail | **built** — the four legs priced in both expiries, each expiry's ATM IV and move, the per-strike IV chart, and the Historical Statistics panel beneath it |
| Research, Entry Analysis | not started; the tab strip shows them disabled rather than hiding them |

### Known gaps in the Calendar Edge tab

* **It cannot lock an entry, and the button is absent rather than inert.** This
  is the only tab that WRITES — entry locks, and an opportunistic backfill of
  the eligibility registry as a side effect of drawing. The read-only API does
  neither. A "Lock Entry Here" button that looked like it froze your entry
  price and did not would be worse than no button, so there is none, and the
  screen says so. The position-management framing a lock switches the chart
  into is missing for the same reason.
* **No momentum readout** (the "closing $0.42/hr, ~18 min to threshold" line).
  It is computed in the view from the locked entry, so it follows the locks.
* Everything drawn *is* the page's: every frame comes from the definitions the
  Streamlit tab calls, compared row by row against it on the live record —
  128 rows, every derived column identical to the last decimal.

The tab shipped on 2026-09-06 missing three of its parts, and the miss is worth
recording because tests could not have caught it. The four headline figures and
two of the four charts were simply absent — not broken, not wrong, absent. What
was built matched the page exactly; it was a smaller page. **Checking a rebuilt
tab means walking the original top to bottom and counting, not confirming that
what you drew is right.** Chandan spotted all three by looking at the screen.

### The header strip

SPX and its change, VIX, Max |GEX|, a staleness dot, a wall clock and the age
of the newest price — on every tab, as `ui/header.py` has it. The age counts
UP rather than down, because a countdown's worst case (collector dead, no
price for an hour) displays as `0s` and reads like everything is fine.

What counts as *late* is not decided in the browser. `amber_at` and `red_at`
arrive on `/mission/header` from `core.session` — the module the collector and
the watchdog also read — and depend on which market session is happening now:
60 seconds in the first and last half hour, 300 midday, and no expectation at
all when the market is shut.

### Navigation

Tabs are switched by the URL hash — `#gex` opens Gamma Exposure. A drill-down
carries its pair in the same place:
`#edge?front=2026-09-23&back=2026-09-29&put=7710&call=7725`. So a scoped chart
can be linked to and a reload comes back to it — which the Streamlit page
cannot do, since it stages the same four values in `st.session_state`.

**One click, no confirmation.** The page needs a second step (select a row,
then press "View Chart") because Streamlit reruns top to bottom and the
Controls Bar has already drawn by the time the click is seen, so the values
are staged for the *next* run. That is a constraint of the framework, not a
decision about what a click should mean. Here the click does the only thing it
was ever for (Chandan, 2026-09-06).

### The Gamma Exposure tab (rebuilt 2026-09-06)

Three changes Chandan asked for on the same day, all after gexstream.com:

* **The strike panels are a price ladder.** Strikes run down the y-axis in
  price order and the bars run left and right from zero. The three panels are
  now three *columns* sharing that one strike axis rather than three rows
  sharing a strike x-axis, so a bar is still read straight across against the
  same strike in the others.
* **The wicks.** The thin pale line behind each bar is that strike's highest
  and lowest exposure so far this session, from `/strikes/session-range`.
  gexstream's own docs define these as the "per-strike session high / low for
  GEX, DEX and vGEX, tracked since the 4:00 PM ET reset", and the distinction
  is worth stating because the obvious reading is the wrong one: **a wick is
  not the change since the open.** That is `net_flow_by_strike`, a different
  number — a strike that travelled a long way and came back has zero flow and
  a wide wick. **Every one of the six views now carries them** (Chandan,
  2026-09-07): `/strikes/session-range` takes a `measure` and each panel asks
  for its own, so a delta wick is a delta range and a charm wick is a charm
  one. `PanelSpec.wicks` stays an explicit flag rather than becoming an
  assumption — the flag says "draw them", never "fetch the right ones", and
  the caller must still hand the panel its matching range.
* **The expiry picker** replaced the `<select>`. Each row shows that expiry's
  call and put gamma, so the choice is made by looking rather than by
  selecting one, reading the chart, and selecting the next. It multi-selects
  (`?expiry=a&expiry=b`, and the exposures add), and filters by This
  Week / Next 2 Weeks / This OpEx Cycle / Next 2 OpEx Cycles — all four
  cumulative, all four defined in `core/contract.py`, and each expiry carries
  the list of windows it belongs to so nothing here compares two dates.
  An empty selection is the whole board.

  **A filter button SELECTS its window, it does not merely narrow the list**
  (Chandan, 2026-09-07). Clicking "This Week" picks every expiry in that
  window and the chart redraws as their sum; it used to shorten the list and
  leave the selection alone, which meant the button looked like it had done
  something and had not.

  **The countdowns follow the clock, not the snapshot.** Friday's board said
  "8 Sep — 4 DTE" on Sunday, because 4 is the number the collector stored on
  Friday. The rule is `core.expiry.countdown_anchor` and it re-bases only
  when the snapshot being shown is the **newest one there is**: a replayed
  session keeps its own countdowns, or every filter window would come back
  empty. A settled expiry therefore reads a negative number rather than being
  clamped to 0 — clamping it would leave it sitting in the one bucket a
  trader acts on. The same anchor feeds Calendar Edge's front and back
  dropdowns, through one helper, so the two tabs cannot disagree.

**vGEX by Volume** was added the same day: gamma weighted by today's traded
volume instead of installed open interest. Same formula, same scale, one
column swapped (`core.gex.by_strike(weight=...)`), and drawn call-vs-put like
the default GEX view precisely so the two can be flipped between and read for
divergence. `flow_ratio` on the response is gexstream's own vGEX ratio —
positive over absolute, bounded in [0, 1] — and is *not* `summary.ratio`.

### Known gaps in the Gamma Exposure tab

* **vGEX over the whole board is dominated by one strike.** Volume-weighted
  gamma on SPX concentrates almost entirely at the 0DTE money, so the axis
  runs to a trillion and everything else is squashed. That is the measure
  behaving correctly; scope it with the expiry picker to read it.
* **The Streamlit tab has no vGEX view and no ladder.** Neither was
  back-ported; `views/gex.py` still draws the vertical panels.
* **Only the top half is here.** The Streamlit tab carries, below the strike
  panels, the time panels, the 0DTE flow board, dealer structure, net flow and
  a replay. None of those are built. What *is* built is the part all of them
  sit under: the headline strip, the expiry picker and the seven strike views
  (Call vs Put, Abs Gamma, Net Gamma, vGEX, Delta, Vanna, Charm), each with
  volume and open interest beside it, sharing one strike axis.
* **Plotly is 4 MB.** `plotly.js-dist-min` takes the production bundle from
  235 KB to 4.3 MB (1.3 MB gzipped). That is the whole cost of this tab and it
  is stated rather than absorbed: `plotly.js-basic-dist-min` has bar and
  scatter, which is all this tab draws, and is roughly a quarter of the size.
  It is the obvious next move if the bundle starts to matter.
* **`@types/plotly.js-dist-min` is not installed.** `src/plotly-dist-min.d.ts`
  declares the module locally against `@types/plotly.js` instead. Those types
  are v3 against a v4 runtime, so they are a guide rather than a guarantee.
* Delta Exposure has **no summary of its own** — the strip keeps showing the
  gamma figures, which is what the Streamlit tab does. Their absence is the
  honest signal, not an omission.

### Known gaps in the Scanner tab

* **The custom put/call offset selectors are missing.** They drive
  `compute_transform_scanner`, which `/mission/scan` does not expose — it serves
  the standard sweep only. The tab says so under the table rather than offering
  a control that does nothing.
* **"NEW" means something different here than on the Streamlit page**, on
  purpose. Streamlit compares against what the current *browser session* last
  saw (`st.session_state`), which a client keeping no state cannot reproduce.
  This tab uses `/mission/new`, which compares against the last snapshot
  **recorded in the database** — the portable question, and the one BUG-040
  built the endpoint to answer. When nothing has been recorded yet the endpoint
  returns `compared_against_snapshot: null`, and no badge is drawn: nothing can
  be new when there is no "before".
* **The lookback window is fixed to Today, on purpose.** The tab first shipped
  with a Today / 5 / 10 / 20-session picker; the Streamlit page used to have
  one and no longer does — `views/scanner.py` removed it when the time-range
  control moved to the Calendar Edge tab and pinned Mission Control to
  "Today". Two screens offering different windows over one snapshot can show
  different cards with no way to tell which is right, so the picker came out.
  `/mission/non-atm` still accepts wider windows; if the control is wanted it
  belongs on both screens, added deliberately.
* **The sightings registry only advances while Streamlit is running** —
  DEBT-042. The card counts are correct and will silently stop moving if this
  screen is ever used on its own, so the panel prints the registry size.

## Types

`src/api/types.ts` is hand-written and nothing checks it against FastAPI's
schema. A renamed field becomes `undefined` and renders blank rather than
raising. Generating it from `/openapi.json` is the obvious improvement and is
not done yet. Until then every optional field is drawn with an explicit `—`
fallback, so a missing value looks missing rather than looking like zero.

## Checks

```bash
npm run lint    # oxlint
npx tsc -b      # types
npm run build   # both, then the production bundle
```

There are no browser tests yet. Both tabs were verified by rendering them in
headless Chrome against the live record and reading the result — which is how
three wrong numbers were found on its first draft, all of the same kind: a
figure that looked right. A capped list counted as a total, a post-limit row
count printed as the full sweep, and a NEW badge on every card because the
client read `new_keys` without reading `compared_against_snapshot` beside it.

The Gamma tab was checked the same way: every one of the six views rendered
against snapshot 6387, with the headline strings compared field by field
against what `/mission/gamma` answered. Net Gamma draws 101 fewer bars than
the others, which is the check that it really is drawing ONE signed series on
panel 1 rather than two.
