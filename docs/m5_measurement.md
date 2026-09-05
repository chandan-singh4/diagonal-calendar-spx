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
