# M5.0 — Where a tab click actually goes

**Measured 2026-09-05, against the live 3.42 GB record, after M2 and M4.**
Instrument: `streamlit.testing.v1.AppTest` running the real `app.py`, with
every prelude function and every tab body wrapped in a timer. Harness in the
session scratchpad (`measure_tabs.py`, `pass2.py`); raw numbers in
`m5_timings.json` and `m5_pass2.json`.

ENH-011 says: *do not start by tuning TTLs, measure first.* This is the
measurement.

## What it can and cannot see

`AppTest` executes the same script the Streamlit server executes, so
everything below is **server-side script time and nothing else**. It excludes
the websocket delta and the browser's paint. A real click therefore feels
slower than these numbers by an amount this method cannot report — playwright
and selenium are not installed, so measuring that increment means a manual
pass with browser devtools. **The conclusion below is stated only about the
part that was measured.**

The first run also applied schema migration v4 (pending since M4). That is the
`_init_db_once` line and it happens once, ever.

## The numbers

Cold start, Scanner tab: **3.24 s wall.**

| component | self |
|---|---|
| `mission_control.run` | 2.312 s |
| `view.scanner` | 0.133 s |
| `theme.apply` | 0.103 s |
| `compute_transform_scanner` | 0.085 s |
| everything else instrumented | 0.12 s |
| **unattributed (Streamlit itself)** | **0.49 s** |

Every subsequent click, by tab:

| click | wall | dominant cost |
|---|---|---|
| scanner | 0.188 s | mission control 0.097 |
| entry | 0.162 s | mission control 0.094 |
| edge | 0.846 s | `view.edge` 0.666 |
| strike | 2.662 s | `_load_contract_hist` 2.338 (4 calls) |
| gex | 1.033 s | `_load_intraday_strike_metrics` 0.655 (3 calls) |
| research | 1.585 s | `_load_diagonal_hist` 1.368 |
| scanner (revisit) | 0.175 s | mission control 0.089 |

**Streamlit's own overhead is the "unattributed" row: ~0.04 s per rerun** once
started. That is the framework's entire share of a click. Everything else is
our own SQL and our own Python.

## Three findings

**1. The framework is not the bottleneck.** 40 ms per rerun. Migrating off
Streamlit would buy back 40 ms of a click that costs 200–2,700 ms. On the
measured portion, the July impression no longer holds — M2 appears to have
been the fix.

**2. There is a fixed prelude tax on every click: ~0.15 s, of which Mission
Control is ~0.09 s.** It runs on every script execution regardless of tab,
because the header's Attention Strip needs it. It is paid to redraw a tab that
does not use it.

**3. TTL expiry re-pays for byte-identical answers — this is ENH-011,
confirmed with a number.** Visiting Gamma Exposure cost 1.09 s, an immediate
return cost 0.245 s, and a return after 65 s idle cost 0.881 s again. The
snapshot had not changed; the clock had. `_load_intraday_strike_metrics`
recomputed 0.65 s of work to produce the same result. `api/cache.py` already
demonstrates the fix — key on the snapshot, drop the TTL — and it is a change
inside `services/loaders.py`, not a migration.

## What this says about M5.1–5.6

The premise of M5 was "does Streamlit still hurt". On server-side script time,
measured: **no, not materially.** The cost is our queries, and the two
cheapest wins are both inside the current stack:

- port `api/cache.py`'s snapshot-keying into `services/loaders.py` (ENH-011)
- stop paying full Mission Control on tabs that do not display it

Neither requires leaving Streamlit. The open question this cannot answer is
browser-side render time, and that should be measured before any migration
decision — not assumed in either direction.

---

# After the two fixes (same day, same instrument)

Both fixes were made and re-measured with the harness that found the problem.

**ENH-011** — every TTL removed from `services/loaders.py`; the memo is now
dropped by `invalidate_on_new_snapshot()`, called once per script run from
`app.py` before the first read. Two reads are deliberately excluded: a prior
session's close and its open interest are finished facts, already keyed by
session date, and clearing them per snapshot would re-query immutable history
*more* often than the 300s TTL they replaced.

**ENH-012** — `_build_non_atm_panel` was walking the **whole** eligibility
registry on every click to display twenty cards. The registry holds 1,286
entries. That was 0.46s per click, on every tab, including tabs that never
show the panel. It is now memoised on (snapshot, lookback).

Note the first measurement under-reported this at 0.09s: the registry grew
between the two runs, and the cost scales with it. The panel build is now paid
once per snapshot, so registry size no longer touches click latency — but it
does still scale the once-per-snapshot compute.

| click | before | after |
|---|---|---|
| scanner | 0.188 s | **0.102 s** |
| entry | 0.162 s | **0.066 s** |
| edge | 0.846 s | **0.245 s** |
| strike | 2.662 s | **0.302 s** |
| gex | 1.033 s | **0.939 s** |
| research | 1.585 s | **0.207 s** |

Gamma Exposure barely moved because its cost is a genuine first-visit query,
not repeated work — and a first visit has to happen once.

The idle test, which is what proved the TTL was the problem, now shows the
opposite result:

| | before | after |
|---|---|---|
| gex, first visit | 1.09 s | 0.94 s |
| gex, immediate return | 0.245 s | 0.184 s |
| **gex, return after 65s idle** | **0.881 s** | **0.133 s** |

The cache now holds across the idle instead of expiring into a rebuild of the
same answer.

**Caveat on the "after" column:** the OS file cache was warm from the earlier
runs, so first-visit figures are flattered. The idle comparison is not
affected by this and is the one to trust.

The browser-side caveat at the top of this document still stands: none of this
measures paint time.

---

# The flicker (reported by Chandan, 2026-09-05, after the fixes above)

> *"when I navigate from one tab to another, I see the flicker or the refresh
> on the page"*

**Cause: every tab click ran the whole script twice.** Confirmed by counting
script executions per click — two, every time. The page was built, discarded,
and built again, and the discard is what showed as a flash.

The nav is a button row rather than `st.tabs`, so a Mission Control card can
jump straight to a pre-scoped tab. The buttons sit *below* the controls bar,
so by the time a click was noticed the bar had already been drawn for the tab
being left. The workaround was `st.rerun()` immediately after the click, which
bought correctness at the price of a second full render.

**Fix:** the click is now read at the top of the run from the button's own
state — a button's press is visible in `session_state` before the widget is
recreated, verified in isolation rather than assumed — so the active tab is
settled before anything draws. The buttons only draw now; they decide nothing.
One run per click, the highlight correct on the same run, and the controls bar
still correctly hidden on Gamma Exposure.

The three remaining `st.rerun()` calls in `views/` and `ui/locks.py` are
untouched and genuine: they set `pending_` values the controls bar must
promote before its widgets exist, which can only happen on a fresh run.

Guarded by tests/test_tab_navigation.py, proved by restoring the old shape.

## A caveat on every absolute number in this document

Re-measuring at 14:50 the same day gave figures roughly three times those
taken at 14:00 — `_load_intraday_strike_metrics` went from 0.65s to 1.97s —
with the code unchanged and the same snapshot. The intraday tables grow
through the trading session, so query cost grows with it.

**So absolute timings here are only comparable within a single run.** The
before/after pairs above were each taken minutes apart and are sound; a figure
from one section should not be compared with one from another. The idle test
was re-run under the later, slower conditions and still holds: Gamma Exposure
3.30s on first visit, 0.437s returning after 65 seconds idle.

That intraday growth is itself worth knowing, and is filed as ENH-013.
