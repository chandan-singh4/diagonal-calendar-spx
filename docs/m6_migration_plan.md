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
| 1 | Scanner | sweep table `/mission/scan` — **covered**. Approaching + Likely Next cards `/mission/cards` — **covered** as of 2026-09-05. `/mission/new` — **covered**, write split off by BUG-040. Non-ATM card grid — **gap**, DEBT-041. |
| 2 | Gamma Exposure | `/mission/gamma`, `/strikes/intraday-metrics`, `/strikes/prior-session-oi` — **covered** (`expiry` scoping added 2026-09-05; the endpoint had been half-served since ENH-014) |
| 3 | Calendar Edge | `/pairs/transform-marks`, `/atm-history`, `/spx/intraday` — **covered** |
| 4 | Strike Detail | `/contract-history`, `/atm-iv/latest` — **covered** |
| 5 | Research | `/pairs/diagonal-history` — **covered** |
| 6 | Entry Analysis | **gap** — term structure, theta differential, straddle, IC mark, IV percentile and liquidity are derived in `app.py`'s prelude and exposed nowhere |

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
2. **Mission Control's non-ATM panel** — `/mission/scan` returns the sweep,
   not the curated registry-backed panel that `services/mission_control.py`
   builds. DEBT-041 records this.
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
