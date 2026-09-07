# M6 — React + FastAPI, built beside Streamlit

Chandan's decision, 2026-09-05, after the measurement below. One tab at a
time, both apps running, and a proper redesign rather than a like-for-like
port.

## What this migration is and is not for

**It is not for speed.** That question was measured on 2026-09-05 and the
answer is on the record: Streamlit's own overhead is **~0.04 s per click**. A
warm tab click costs 0.07–0.19 s. The 4.5 s that made the Gamma Exposure tab
feel broken was one SQL query (BUG-039), and fixing the query took it to
1.07 s cold and 0.14 s warm — without touching the framework.

Anyone reading this later and wondering whether the rewrite was justified on
performance grounds: it was not, and it was not sold that way.

**It is for what Streamlit cannot do at all.** Every interaction re-runs the
whole script, so there is no such thing as changing one number without
redrawing the page; widget state is discarded when a widget is not drawn
(BUG-032); the element tree is patched by position, so switching tabs
repaints the old one (BUG-037); and the layout is whatever Streamlit's
column model permits. A dense trading view — panels that update
independently, a table that stays responsive over the whole sweep, state that
survives navigation because it lives in the client — is a different shape of
program, not a faster one.

## The rule that governs every decision here

**No formula may exist in TypeScript.** `core/` is the single source of truth
and stays that way. The API serves COMPUTED results — a gamma ladder, a
scanner sweep, a flow series — never raw option rows for the frontend to
aggregate. The moment the browser computes net GEX, there are two definitions
of it and they will diverge; `core/gex.py` was extracted to end exactly that
split and this migration must not reintroduce it in another language.

The practical test: if a number appears on screen, `grep` for its formula
should find it once, in Python, under test.

`tests/test_layering.py` already enforces that `api/` may not import
`streamlit`, `services`, `views`, `ui` or `app`. That rule is what makes two
front ends possible at all, and it stays.

## Architecture

```
SQLite (unchanged, one file, WAL)
  └── core/ dataaccess/          pure, shared, already tested
        ├── services/ + views/  → Streamlit app      (stays live)
        └── api/                → FastAPI            (already exists)
                                     └── React + TypeScript (new)
```

**Backend: FastAPI, already built.** M4 delivered 15 working endpoints with
auth, snapshot-keyed caching and a live-update channel. Verified against the
live record on 2026-09-05. This migration extends it; it does not start it.

**Frontend: React + TypeScript + Vite.** TypeScript rather than JavaScript
because the API's shapes are the contract between two languages, and a typo
in a field name should fail at build time rather than render as `undefined`
on a trading screen.

**Charts: Plotly.js.** The same library the Python side already uses, so the
domain-specific work carries over rather than being re-derived: market-hours
gaps on intraday axes, the mirrored GEX ladder, the 128-frame replay, the
third-Friday AM/PM contract split. Those took real effort and are covered by
tests. A redesign restyles them; it does not need to re-invent them.

**Shell: Tailwind + shadcn/ui, TanStack Query, TanStack Table.** The shell is
what actually looks unpolished today — density, navigation, typography,
panels — and it is the part Streamlit constrains most. TanStack Query gives
the caching and background-refresh behaviour that `@st.cache_data` provides
today; TanStack Table handles the Scanner's 4,221 rows without shipping them
all to the DOM.

## Sequence — one tab at a time

Each tab is DONE when it renders from the API against the live record, has
been compared side by side with the Streamlit original, and its endpoints
have contract tests. Streamlit keeps serving every tab not yet moved.

| # | Tab | API status |
|---|-----|-----------|
| 1 | Scanner | **TAB BUILT 2026-09-06** (`web/src/scanner/`). API status below. |
| 1a | Scanner endpoints | sweep table `/mission/scan` — **covered**. Approaching + Likely Next cards `/mission/cards` — **covered** as of 2026-09-05. `/mission/new` — **covered**, write split off by BUG-040. Non-ATM card grid `/mission/non-atm` — **covered** as of 2026-09-06, DEBT-041 closed. All three grids are now served from the definitions the page draws. Note the panel READS the sightings registry and never advances it — DEBT-042. |
| 2 | Gamma Exposure | **TAB PART BUILT 2026-09-06** (`web/src/gamma/`) — headline strip, expiry picker and all seven strike views, drawn as a price ladder with session wicks; the time panels, 0DTE board, dealer structure, net flow and replay are not built, and `web/README.md` lists them. API: `/mission/gamma`, `/strikes/intraday-metrics`, `/strikes/prior-session-oi` — **covered for the four GEX/DEX views** (`expiry` scoping added 2026-09-05; the endpoint had been half-served since ENH-014). Vanna and Charm Exposure, added to the tab the same day, are **covered as of 2026-09-06** via `/mission/gamma?measure=vanna|charm` — `measure=gamma` is the default and returns exactly what the endpoint returned before, so nothing already reading it had to change. That needed one extraction: `_day_remainder` was private to `views/gex.py`, and charm cannot be computed without it on the last day of an expiry, so it moved to `core.gex.day_remainder` with the timezone passed in. **The whole tab is now served.** Rebuilt the same day at Chandan's request after gexstream.com: the panels were transposed into a price ladder, an expiry picker replaced the `<select>`, and a **vGEX by volume** view was added (`measure=vgex` — `core.gex.by_strike(weight=...)`, one column swapped, same scale as GEX on purpose so the two can be read against each other). Three new rules moved into Python rather than the browser: `core.contract.opex_of`/`next_opex`/`week_end`/`filters_for` (the picker's four filter windows, served as MEMBERSHIP per expiry so nothing in TypeScript compares two dates), `core.gex.by_expiry` (the picker's per-expiry totals, which equal the sum of that expiry's bars), and `core.gex.session_range_by_strike` behind `/strikes/session-range` (the wicks — the session high/low, **not** the change since the open). **Extended 2026-09-07 to all six views**: `/strikes/session-range` now takes a `measure`, and the ranges are computed by `core/ranges.py`, which calls the measure's OWN `by_strike` function once per snapshot rather than re-deriving it — so a wick cannot drift from the bar it sits behind, and the sign conventions (vanna and charm impose the dealer sign, delta deliberately does not) are not restated anywhere. That needed a session-wide chain read, `dataaccess.load_session_chain_df`; written the obvious way it took **45s**, and it is 2.0s as shipped — the BUG-039 subquery form plus thirteen named columns instead of `SELECT o.*`, which alone was worth 15.6s → 2.0s on 410,069 rows. Also 2026-09-07: **the countdowns follow the live clock** (`core.expiry.countdown_anchor`), re-basing only when the snapshot shown is the newest one, and the picker's filter buttons now SELECT their window rather than narrowing the list. |
| 3 | Calendar Edge | **TAB PART BUILT 2026-09-06** (`web/src/edge/`), read-only — no entry locks. API: `/pairs/transform-marks`, `/atm-history`, `/spx/intraday` — the addresses were covered; the **columns were not**. "Covered" in this table had meant an endpoint exists, not that it serves what the tab draws. Chart 1 needs `diagonal_mark`, `transform_mark` and `gap`, all three derived in the view from the four raw marks — **served as of 2026-09-06** via `core.scanner.add_mark_columns`, along with the wall-clock timestamp and the session breaks. Charts 2–4 checked the same way: they ride on two frames, and the second — front and back ATM IV inner-joined with the ratio — was derived in the view too. **Served as of 2026-09-06** at `/pairs/atm-pair`, with the regime bands and the sample-size warning. Charts 3 and 4 and the four headline figures were MISSED ENTIRELY on the first pass and added later the same day after Chandan looked at the screen — a reminder that this column tracks endpoints, and only counting the original tab's pieces tracks the tab. They needed `/mission/edge-headline` (`iv_engine.iv_index` plus the two ATM IVs and their ratio) and two more columns on `/pairs/atm-pair` (`hod`, `scatter_domain`). Still page-only: the SPX strike-crossing markers and the shared x-range rule. The tab also **writes** (entry locks, registry backfill), which the read-only API does not do and this table never said. **2026-09-07:** the front/back expiry dropdowns take the same live-clock countdown as the Gamma picker — `/mission/controls` and `/mission/gamma` share one `_countdown_anchor` helper, so the two tabs cannot show one contract at two different DTEs. The back-expiry narrowing still compares DATES, not countdowns, and there is a test pinning that: a display change that silently altered which pairs are offered is the failure worth guarding. |
| 4 | Strike Detail | **TAB BUILT 2026-09-06** (`web/src/strike/`), including the **Historical Statistics** panel that `app.py` composes onto the same tab from `views/historical.py` — missed on the first pass because reading `views/strike.py` end to end cannot show it. Served at `/mission/historical-stats`; every window verified identical to the page's own path on snapshot 6387, row counts included (128 / 633 / 1,262 / 2,391). `/contract-history`, `/atm-iv/latest` were covered as addresses; the tab needed the four legs priced together and the per-contract IV join, now `/mission/strike-detail` and `/mission/strike-iv` — the latter reusing `core.series.merge_iv_pair`, the expiry-level join generalised one level down to a single contract. Verified leg by leg against the page on snapshot 6387, including a `front_mark` that is genuinely absent and draws N/A on both. |
| 5 | Research | `/pairs/diagonal-history` — **covered** |
| 6 | Entry Analysis | **gap** — term structure, theta differential, straddle, IC mark, IV percentile and liquidity are derived in `app.py`'s prelude and exposed nowhere |

| — | Header strip | **BUILT 2026-09-06** (`web/src/shell/HeaderBar.tsx`). Not a tab and not in this table until now, which is exactly why it was absent from the rebuild for three tabs running. `/mission/header` serves SPX and its change, VIX, Max |GEX|, the snapshot stamp and what "late" means for the session happening now. |

Scanner first because it is the tab most in need of a real table, and the
sweep behind that table is already served. Two corrections to what this
paragraph first claimed, both found on 2026-09-05 by reading the code rather
than the plan:

**Its endpoints do NOT already exist in full.** `/mission/scan` returns the
sweep and band COUNTS. The Mission Control opportunity cards above the table —
which `views/scanner.py` calls "the strategy's whole point" — are built by
`services/mission_control.py` and are served by nothing. The first increment
is therefore the sweep table only, and the cards need an endpoint before the
tab can replace the Streamlit one.

**The 4,221 figure was mine, not the app's.** That is the size of the sweep.
The Scanner has never drawn more than 200 rows of it: `ui/sidebar.py` offers
10-200, default 50. `/mission/scan` caps `limit` at 2,000, so the endpoint is
already an order of magnitude more generous than the tab has ever been, and
row volume is not a problem to solve here.

### Known gaps, to be closed as their tab comes up

1. **Entry Analysis derivations** — computed in `app.py` between the loaders
   and `ViewContext`. They belong in `core/` or a new pure module and then in
   an endpoint; leaving them in `app.py` means the React tab cannot have them
   without importing Streamlit.
2. ~~**Mission Control's non-ATM panel**~~ — **closed 2026-09-06.** The panel
   moved to `api/computed.py:non_atm_panel` and is served at `/mission/non-atm`;
   `services/mission_control.py` now calls the same function the server does, and
   `tests/test_api_non_atm.py` compares the two answers field by field. The server
   learned where the sidecar files live via `create_app(state_dir=...)`, mirroring
   the existing `db_path` seam so `api/` still imports nothing from `services/`.
   What remains is DEBT-042: the served panel reads the registry, never writes it.
3. **Writes.** Entry locks, the eligible-history backfill and the chart-colour
   settings all write through `state/`. `api/` is read-only by design and the
   one documented exception is the "New" registry. Adding writes is a
   decision, not a detail — see `api/__init__.py`.
4. **`ViewContext` is the de facto contract.** It lists exactly what a tab
   needs. It is the right checklist for each tab's endpoints.

## What stays true throughout

- The collector is untouched. Both apps read the same database read-only.
- No check touches the real database; trade numbers are never reused.
- Streamlit is not removed until the last tab is moved and verified. If the
  migration stalls, the dashboard still works — that is the entire point of
  doing it this way rather than in a branch.

## Risks worth naming now

- **Two front ends to keep working.** Every backend change must satisfy both
  until the swap. This is the cost of not going dark for two months, and it is
  the right trade, but it is a real cost.
- **Redesign and migration at once.** Changing the architecture and the layout
  together means a difference on screen has two possible causes. Mitigated by
  comparing each tab against the Streamlit original before calling it done,
  even though the two will not look alike.
- **The test asymmetry.** 11,636 lines of tests cover `core/` and `db/` and
  carry over untouched. 5,069 lines are Streamlit-coupled and do not. New
  frontend behaviour needs new tests; it does not inherit them.
