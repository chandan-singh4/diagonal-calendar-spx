---

## [DATE] — collector.py: Initial implementation + supporting changes

### What changed
Three files modified or created:
- `config.py` — two additions
- `schwab_client.py` — three new functions + vega in chain_to_dataframe
- `collector.py` — new file (735 lines)

---

### config.py additions

**`VIX_SYMBOL = "$VIX.X"`**
Schwab's symbol for the CBOE VIX index. Used by collector to fetch VIX spot
alongside each option chain snapshot. Stored as `vix_value` in snapshots table
to provide volatility regime context for historical IV percentile analysis.

**`MARKET_HOLIDAYS`**
Set of US market holiday date strings for 2026. The collector uses this to
classify collection gaps as `HOLIDAY` vs `COLLECTOR_OFFLINE`, so weekend and
holiday gaps are suppressed from data quality warnings in the dashboard.
**Action required each January: add the next year's holidays.**

---

### schwab_client.py additions

**`_safe_float(val)`**
Internal utility. Returns `float(val)` or `None` if val is null, NaN, zero,
or unconvertible. Keeps API response sanitization in one place.

**`get_spx_quote_full(client) → dict`**
Returns `{bid, ask, last, mark}` for the SPX index. Used by collector to
populate `snapshots.underlying_bid`, `underlying_ask`, `underlying_price`.
The original `get_spx_quote()` (returns a float) is preserved unchanged for
backward compatibility with any existing `app.py` callers.

**`get_vix_quote(client) → float | None`**
Returns the VIX spot price, or None if the fetch fails. Deliberately non-fatal
— a VIX failure does not abort the collection cycle; the snapshot is recorded
with `vix_value = NULL`.

**`chain_to_dataframe` — added `vega` column**
`c.get("vega")` is now extracted alongside delta/gamma/theta. Vega is required
for transformation timing analysis: when vega is highest, IV contraction has
maximum impact on position value. This is a backward-compatible column addition.

---

### collector.py — new file

**Purpose**: The only component that talks to the Schwab API and writes to the
new snapshot-anchored SQLite schema. Runs independently of `app.py` as a
separate terminal process.

**Market session logic**
- OPEN (09:30–10:00 ET): 60-second polling
- MIDDAY (10:00–15:30 ET): 300-second polling
- CLOSE (15:30–16:00 ET): 60-second polling
- Collection stops at 16:00 ET — not 16:15 — because SPX (cash-settled index)
  stops updating at equity close; IVs after 16:00 use a frozen underlying.
- Sleeps outside market hours; self-activates at next open without restart.
- US market holidays read from `config.MARKET_HOLIDAYS`.

**Cycle lifecycle (10 steps)**
1. Fetch SPX quote (bid/ask/last/mark)
2. Fetch VIX (non-fatal)
3. Create snapshot as `PARTIAL` — ensures auditable record even on crash
4. Fetch option chain (all expirations ≤ MAX_EXPIRY_DTE = 20)
5. Flatten chain; apply ±300pt strike filter
6. Build `option_rows` list (one dict per contract)
7. Compute `atm_iv_by_expiry` records (one dict per expiry)
8. Determine status: COMPLETE / PARTIAL / FAILED
9. Write rows to database (separate transactions for each table)
10. Finalize snapshot to COMPLETE/PARTIAL/FAILED with metadata

**Snapshot status logic**
- `COMPLETE`: all rows written, ATM IV computed for all raw expiries
- `PARTIAL`: ATM IV computed for fewer expiries than raw chain contained
- `FAILED`: no option rows after filtering, or fatal API error

**Gap detection**
- On startup: compares `now()` to last snapshot timestamp; records unexpected
  gaps in `collection_gaps` table (HOLIDAY and MARKET_CLOSED gaps suppressed)
- Mid-session: flags gaps between consecutive snapshots > 2.5× expected interval

**Error handling**
- Auth errors (HTTP 401 / token expired): resets `client = None` to force
  re-authentication on the next cycle; sleeps 30s
- API / processing errors: logs failure count; sleeps 30s and retries
- After 5 consecutive failures: logs CRITICAL alert (collection continues)
- KeyboardInterrupt: clean shutdown

**Drift-corrected sleep**
Sleep duration = `poll_interval - cycle_elapsed_time`. Keeps collection times
close to wall-clock boundaries regardless of API latency variation.

**CLI flags**
- `python collector.py` — runs indefinitely
- `python collector.py --once` — one cycle then exit (for testing)
- `python collector.py --db PATH` — override database path

**Windows note**
Requires `pip install tzdata` for IANA timezone support (`zoneinfo` module).

---

### Impact
- `app.py`: no changes required — all legacy db.py functions unchanged
- `db.py`: no changes — uses existing `create_snapshot`, `insert_option_rows`,
  `insert_atm_iv_records`, `finalize_snapshot`, `record_gap` functions
- New snapshots accumulate in the `snapshots`, `option_rows`, and
  `atm_iv_by_expiry` tables; app.py reads from legacy tables until refactored

### Open questions
- When to refactor `app.py` to read from new schema (after collector has
  accumulated enough real data to make the new charts useful)
- Whether to add Windows Task Scheduler integration to auto-start collector
  at system boot (currently requires manual `python collector.py` each session)
- `MARKET_HOLIDAYS` needs to be updated each January

---

---

## [DATE] — Windows Task Scheduler: auto-start collector at logon

### What changed
Two new files added:
- `start_collector.bat` — launcher script for the collector
- `register_collector_task.ps1` — one-time Task Scheduler registration script

### Why
The collector needs to run every trading day without manual intervention. Running
manually from VS Code each morning is error-prone — a missed startup means a gap
in IV history that cannot be backfilled (no historical option chain endpoint at
Schwab). Automating the launch via Windows Task Scheduler eliminates that risk.

### Design decisions

**At-logon trigger, not a fixed daily time.**
The collector handles market hours internally — it sleeps when the market is
closed and activates at 9:30 AM ET automatically. Triggering at logon is simpler
and more reliable than a daily fixed time: if you boot at 7 AM, it starts and
sleeps; if you boot at 10 AM, it starts and begins collecting immediately.

**`MultipleInstances = IgnoreNew`.**
If for any reason a second logon event fires while the collector is running, the
task scheduler ignores the duplicate start. Only one collector instance ever runs.

**`RestartCount = 3, RestartInterval = 5 min`.**
If the collector crashes (network drop, unexpected exception that escapes the
main loop), Task Scheduler automatically restarts it up to 3 times before giving
up. This covers transient failure modes without requiring manual intervention.

**Run as current user, not SYSTEM.**
The SYSTEM account cannot access user-specific paths like `data/token.json` and
`.env` without complex permission configuration. Running as the logged-in user
means all existing credential paths work with zero changes.

**Window minimized, not hidden.**
The batch file runs in a minimized console window (visible in taskbar). This
lets you click it to see live INFO logs when needed, while keeping it out of
the way during normal computer use. `collector.log` captures WARNING+ persistently.

### Setup (one time only)
1. Deploy `start_collector.bat` to project root
2. Right-click PowerShell → Run as Administrator
3. `cd "C:\Users\chand\Python\spx-diagonal-dashboard"`
4. `.\register_collector_task.ps1`
5. Verify: open Task Scheduler → look for "SPX Diagonal Collector"
6. Test: `Start-ScheduledTask -TaskName "SPX Diagonal Collector"` (starts immediately)

### Impact
- No code changes to collector.py, db.py, or any other file
- Collection now starts automatically at every Windows logon
- The minimized console window is visible in the taskbar; clicking it shows live logs
- collector.log accumulates all WARNING+ entries across sessions

### Open questions
- Whether to add email/SMS alert if `consecutive_failures >= 5` (currently just
  logs CRITICAL — requires a notification mechanism)

---
