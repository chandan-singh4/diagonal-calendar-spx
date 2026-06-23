# Development Journal — SPX Diagonal Calendar Analyzer

Log every change here. Format:

```
## YYYY-MM-DD — Short title
**Changed:** what you did
**Why:** the reason / problem it solves
**Impact:** effect on strategy logic or dashboard behavior
**Open questions / follow-ups:** anything left unresolved
```

Newest entries at the top.

---

## HOW TO START A NEW CHAT SESSION IF REPO IS PRIVATE
*(Read this every time before opening a new Claude chat)*

1. **Push any unsaved local changes first:**
   ```bash
   git add .
   git commit -m "brief description of what changed"
   git push
   ```
2. **Open a new Claude chat under the same Project.**
3. **Paste the full contents of this DEV_JOURNAL.md** into the first message.
   - This is the single file that tells Claude everything: what's been built,
     what bugs were fixed, what decisions were made, and what's next.
   - The repo is private so Claude cannot fetch it directly — pasting the journal
     is the handshake that gets Claude up to speed instantly.
4. **If Claude needs to see a specific file** (e.g. to debug app.py), paste that
   file's contents into the chat on request. No need to paste all files upfront.
5. **At the end of each session**, push again before closing the chat so the
   journal and any changed files are always current on GitHub.

GitHub repo: https://github.com/chandan-singh4/diagonal-calendar-spx
Primary branch: main
Local path: wherever your spx-diagonal-dashboard folder lives (parent folder contains .venv)

## 6/23/2026 — app.py: Fixed broken reference to removed config constant

**What changed**
`app.py` referenced `config.MAX_EXPIRY_DTE` which was removed when the expiry
collection strategy changed from DTE-capped to count-based. Replaced all
occurrences with `config.MAX_EXPIRY_FETCH_DAYS` via VS Code Find and Replace
(Ctrl+H → Replace All).

**Why**
Dashboard failed on launch with: "Couldn't reach Schwab API: module 'config'
has no attribute 'MAX_EXPIRY_DTE'"

**Impact**
Dashboard loads normally again. No logic change — `MAX_EXPIRY_FETCH_DAYS = 90`
is the direct drop-in replacement for the old `MAX_EXPIRY_DTE = 20` in any
context where a date range in days is needed.

---

## 6/23/2026 — config.py + collector.py: Collect next 20 expirations by count

**What changed**
`config.py`: replaced `MAX_EXPIRY_DTE = 20` with two new constants:
  - `MAX_EXPIRY_COUNT = 20` — collect exactly this many expirations per snapshot
  - `MAX_EXPIRY_FETCH_DAYS = 90` — how far out to cast the net before trimming

`collector.py` (`_run_cycle`): two changes:
  1. `max_date` now uses `MAX_EXPIRY_FETCH_DAYS` instead of `MAX_EXPIRY_DTE`
  2. After `chain_to_dataframe`, chain is trimmed to the nearest
     `MAX_EXPIRY_COUNT` expirations before any further processing:

```python
all_expiries = sorted(chain_df["expiry"].unique())
keep_expiries = set(all_expiries[:config.MAX_EXPIRY_COUNT])
chain_df = chain_df[chain_df["expiry"].isin(keep_expiries)]
```

**Why**
Original design capped collection at expirations within 20 calendar days.
First live run showed only 14 expirations in that window. Collecting by count
instead guarantees exactly 20 expirations per snapshot regardless of how SPX
weekly calendars are spaced — typically reaching ~35–50 DTE for the 20th
expiry.

**Impact**
- `exp=14` → `exp=20` per snapshot
- `rows=2240` → `rows=3200` per snapshot (~43% more option rows)
- Storage: ~330,000 option rows per trading day vs ~230,000 previously
- Term structure view now spans ~6–7 weeks out rather than 3 weeks
- `iv_spread_to_front` column covers a fuller curve

---

## 6/23/2026 — check_db.py: New database health check script

**What changed**
New file `check_db.py` added to project root.

**Why**
Multiline `python -c "..."` commands don't work in PowerShell. Rather than
finding PowerShell-compatible syntax, a dedicated script is cleaner, reusable,
and readable. Replaces all ad-hoc database query snippets with one command:
`python check_db.py`

**What it shows**
- Total snapshots today and all-time (by status: COMPLETE / PARTIAL / FAILED)
- Total option rows stored
- Last 5 snapshots with SPX, VIX, row count, latency, status
- Full IV term structure from the most recent COMPLETE snapshot
- Recent collection gaps (last 5)

**Usage**
Run from any second terminal while the collector is running in Terminal 1.
Never writes to the database — read-only.

---

## 6/23/2026 — config.py: Fixed VIX symbol

**What changed**
`config.py`: `VIX_SYMBOL = "$VIX.X"` → `VIX_SYMBOL = "$VIX"`

**Why**
First live collection run showed:
`HTTP/1.1 400 Bad Request` for `$VIX.X`
Schwab's correct symbol for the VIX index is `$VIX` not `$VIX.X`.
VIX was logging as N/A in every snapshot.

**Impact**
VIX value now populates `snapshots.vix_value` correctly. Required collector
restart to take effect.

---

## 6/23/2026 — Collector: First live run confirmed working

**What happened**
First successful live collection cycle:

✓ snap=1 | MIDDAY | SPX=7397.57 | VIX=N/A | rows=2240 | exp=14 | 6467ms | COMPLETE

**Observations**
- 14 expirations within 20 DTE (prompted the count-based change above)
- 2240 rows = 14 × 80 strikes × 2 sides — math confirms design is correct
- 2SD warning fired: IV=21.3%, DTE=20 → expected move ±736 pts exceeds ±300pt
  window. Decision: leave `STRIKE_FETCH_WIDTH_POINTS = 300`. For diagonal
  calendar trading, strikes beyond ±150 pts are never traded. Warning is
  informational only and does not affect data quality.
- VIX returned 400 → fixed separately (see entry above)
- Collection latency 6467ms — normal for first authenticated cycle

**Startup confirmed**
Collector started via `python collector.py` in VS Code Terminal 1.
Startup shortcut added to Windows Startup folder (shell:startup) pointing
directly to `python.exe` to bypass Smart App Control block on .bat files.
Sleep mode set to Never (plugged in) so collection is uninterrupted during
market hours while laptop is locked.

---

## 6/22/2026 — Windows auto-start: Startup folder shortcut

**What changed**
Added shortcut to Windows Startup folder (`shell:startup`) pointing to:
`C:\Users\chand\Python\.venv\Scripts\python.exe`
with argument: `"C:\Users\chand\Python\spx-diagonal-dashboard\collector.py"`
and Start In: `C:\Users\chand\Python\spx-diagonal-dashboard`

`start_collector.bat` and `register_collector_task.ps1` were created but
could not be used:
- `.bat` file blocked by Windows Smart App Control
- PowerShell script failed silently (no Admin rights in VS Code terminal)

**Why shortcut instead of Task Scheduler**
Direct Python shortcut bypasses Smart App Control entirely (python.exe is a
trusted executable). No Admin rights needed. Equivalent reliability for a
personal machine.

**Behavior**
Collector starts automatically at every Windows logon. Sleeps outside market
hours (9:30 AM – 4:00 PM ET). No manual intervention required on trading days
except weekly Schwab OAuth re-authorization (~every 7 days).

---

## 6/22/2026 — collector.py: Initial implementation + supporting changes

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

## 2026-06-22 — db.py: Full schema overhaul (snapshot-anchored design)

### What changed
`db.py` completely rewritten. The file grew from 164 lines to ~390 lines.
All existing functions are preserved with identical signatures — `app.py`
requires zero changes and continues to work as before.

### New tables added (new schema)

| Table | Purpose |
|---|---|
| `schema_version` | Version tracking; enables future migrations |
| `snapshots` | One row per collection cycle; anchor for all child data |
| `option_rows` | One row per contract per snapshot; irreplaceable intraday record |
| `atm_iv_by_expiry` | Pre-aggregated ATM IV per expiry; primary analytics query target |
| `collection_gaps` | Audit log of missed collection windows |

### Legacy tables kept (for existing app.py)

| Table | Status |
|---|---|
| `expiry_snapshots` | Kept — app.py still reads from this |
| `strike_snapshots` | Kept — app.py still reads from this |
| `positions` | Kept — unchanged |

Legacy tables will be removed in a future commit when `app.py` is
refactored to read from the new schema.

### New functions added

**Write (collector.py only):**
- `create_snapshot()` — opens a PARTIAL snapshot; returns snapshot_id
- `finalize_snapshot()` — seals to COMPLETE/PARTIAL/FAILED after child rows commit
- `insert_option_rows()` — bulk insert in single transaction
- `insert_atm_iv_records()` — bulk insert pre-aggregated ATM IV
- `record_gap()` — writes gap record on restart or missed cycle

**Read (future refactored app.py):**
- `get_last_snapshot_timestamp()` — for gap detection on startup
- `get_snapshots()` — time-range query, default status=COMPLETE
- `get_option_chain()` — chain reconstruction at any historical snapshot
- `get_contract_iv_history()` — IV time-series for a specific strike
- `get_atm_iv_history()` — ATM IV time-series for a specific expiry
- `get_term_structure()` — all expiries for a snapshot (for curve chart)
- `get_iv_spread_history()` — front/back spread for IV percentile engine
- `get_gaps()` — gap query with optional reason exclusion
- `update_snapshot_notes()` — narrow write exception permitted from app.py

### Connection management improvements
- `get_conn()` (legacy) now rolls back on exception — this was missing before
- `managed_conn()` (new) requires explicit db_path; no silent default writes
- Both context managers set WAL mode and `PRAGMA foreign_keys = ON` per connection
- `_make_conn()` shared internal factory with 15-second timeout

### Indexes added (9 total)
```
idx_snapshots_timestamp
idx_snapshots_status
idx_snapshots_timestamp_status      ← primary dashboard query index
idx_option_rows_snapshot_id
idx_option_rows_contract
idx_option_rows_contract_snap       ← most critical: covers per-contract IV history
idx_atm_iv_snapshot_id
idx_atm_iv_expiry_snap              ← primary percentile engine index
idx_gaps_start
```

### Why this design
Schwab provides no historical intraday option chain endpoint. Every row in
`option_rows` represents a moment that cannot be reconstructed from any external
source once missed. The snapshot-anchored design ensures:
- Every collection cycle is auditable (COMPLETE / PARTIAL / FAILED)
- Partial fetches don't silently pollute IV percentile statistics
- Chain reconstruction at any historical timestamp is a first-class query
- The `atm_iv_by_expiry` pre-aggregation keeps dashboard queries fast at scale
  (scans ~3,150 rows vs ~4.8M rows for a 30-day ATM IV history query)

### Impact on existing system
- `app.py` requires no changes
- `demo_data.py` requires no changes
- `iv_engine.py` requires no changes
- `schwab_client.py` requires no changes
- `collector.py` (not yet written) will use the new write functions

### Open questions
- When to refactor `app.py` to read from new schema (after collector.py is
  built and accumulating real data)
- `SCHEMA_VERSION = 1`: increment to 2 when the first schema change is needed
  post-deployment; add migration function to `init_db()` at that time


---
**Implementation Date:** 2026-06-22
**Implementation Status:** Complete — all changes verified live

### Files Modified

**`config.py`**
- Replaced `POLL_INTERVAL_SECONDS = 10` with `POLL_INTERVAL_NORMAL = 300` and `POLL_INTERVAL_EVENT = 60`
- Added `STRIKE_COUNT = 80`, `STRIKE_FETCH_WIDTH_POINTS = 300`, `MAX_EXPIRY_DTE = 20`
- Added `DISPLAY_TIMEZONE = "America/New_York"`

**`schwab_client.py`**
- `get_option_chain()` default `strike_count` changed from `20` → `config.STRIKE_COUNT` (80)
- Added `filter_chain_by_strike_window()` — Python-side safety filter enforcing ±300 pt hard boundary; includes optional 2 SD log warning if window becomes inadequate

**`iv_engine.py`**
- Added `ExpectedMoveCheck` dataclass and `expected_move_log_check()` — computes 1 SD and 2 SD expected move, checks adequacy of configured window; informational only, never gates the fetch

**`app.py`**
- `to_date` changed from hardcoded `+45 days` → `config.MAX_EXPIRY_DTE` (20 days)
- `filter_chain_by_strike_window()` called immediately after `chain_to_dataframe()`
- Event Mode toggle added to sidebar; `poll_interval` variable drives `st_autorefresh` and header caption
- Expected move log check wired into snapshot cycle (live mode only)
- All chart X-axis timestamps converted from UTC → `America/New_York`

**`db.py`**
- Verified only — no changes required; schema compatible with wider data

### Verified Working
- Strikes 7550C and 7350P resolved without "showing nearest" fallback
- Expiry selector now shows full 20 DTE window (~10 expirations)
- Event Mode toggle visible in sidebar; header reflects active poll interval
- Chart timestamps display in Eastern time

---

## Session: Architecture Review & Data Collection Redesign
**Date:** 2026-06-22
**Status:** Decisions finalized — implementation pending approval

### What Was Decided

This session was a full architectural review of the data collection layer,
triggered by a UX bug: strikes such as 7550C and 7350P were not found in
the dashboard when SPX was near 7478. The root cause was confirmed as an
API-level fetch limitation, not a storage or UI bug.

---

### Root Cause Confirmed

`schwab_client.py` was calling `get_option_chain()` with `strike_count=20`,
returning only ~40 strikes centered on ATM (±100 pts at 5-pt spacing).
No `range` parameter was passed, so Schwab defaulted to NTM behavior.
The `iv_engine.strike_contract()` fallback to "nearest" was working correctly —
it was simply receiving sparse input from upstream.

Failure point: API fetch layer only. All downstream logic (parsing, storage,
UI lookup) was functioning as designed.

---

### Strike Collection: Decision

**Chosen approach:** `strike_count=80` at API level + Python-side safety filter.

- `strike_count=80` requests 80 strikes above and 80 below ATM from Schwab
- Covers approximately ±300–400 points at 5-pt spacing near spot
- A Python-side filter (`filter_chain_by_strike_window()`) enforces a hard
  ±300-point boundary as a safety net against any overshoot
- `STRIKE_FETCH_WIDTH_POINTS = 300` stored in `config.py` for easy adjustment

**Why not `range='ALL'`:**
`range='ALL'` returns 300–600 unique strikes per expiry across the full SPX
chain (roughly 8,000 contracts per response, ~12 MB payload). At 2-minute
polling that is unnecessary bandwidth — we would download and discard ~70%
of every response. `strike_count=80` achieves the same practical coverage
at ~2.5 MB per call with zero waste.

**Why not dynamic 2 SD filtering:**
Calculating expected move before determining what to fetch creates a
chicken-and-egg dependency (IV is needed to compute SD, but IV comes from
the fetch). A fixed configurable window avoids this entirely. The 2 SD
calculation is preserved as a log-only informational check
(`expected_move_log_check()` in `iv_engine.py`) that flags when the
configured window may be too narrow — but never gates the fetch.

**Why ±300 points:**
Covers all practical diagonal calendar candidates at any expiry within the
20 DTE analysis window. At 5-pt spacing this is ~120 unique strikes.
Far-OTM tail strikes (beyond ±300 pts) are not relevant to short-dated
diagonal structures and are not worth storing.

---

### Expiration Collection: Decision

**Chosen approach:** All expirations from today through `today + 20 calendar days`.

- `MAX_EXPIRY_DTE = 20` stored in `config.py`
- Enforced via `from_date` / `to_date` parameters already supported by
  Schwab's `get_option_chain()` — no additional filtering needed
- Captures approximately 10–11 SPX expirations per fetch window
  (Mon/Wed/Fri weeklies + end-of-month within 20 days)

**Rationale:** Dashboard purpose is not to track one specific diagonal but
to compare IV across many strike/expiry combinations to identify where
front-dated IV is elevated relative to back-dated IV. 20 DTE covers all
realistic diagonal pairings without storing irrelevant longer-dated data.

---

### Polling Strategy: Decision

**Chosen approach:** Manual event mode toggle in the Streamlit sidebar.

- **Normal mode:** 5-minute polling (`POLL_INTERVAL_NORMAL = 300`)
- **Event mode:** 60-second polling (`POLL_INTERVAL_EVENT = 60`)
- Toggle control: `st.toggle("⚡ Event Mode (60s polling)")` in sidebar
- User activates event mode manually before known high-impact events
  (FOMC, CPI, NFP, PPI, Powell speeches, etc.)

**Why not fixed 2-minute:** Storage cost is 2.5x higher than 5-minute for
minimal analytical gain on normal days. IV term structure shifts over
minutes and hours, not seconds — 5-minute captures it faithfully.

**Why not automatic adaptive polling:** Automatic IV-threshold detection
has inherent lag (only switches after the spike has started). A calendar-
based approach requires external dependencies and ongoing maintenance.
The user already knows when major events occur and can activate event mode
10–15 minutes in advance — earlier than any automatic system would trigger.

**Why manual toggle wins:** Zero external dependencies, zero maintenance
burden, anticipatory rather than reactive, and trivial to implement.
The FOMC example (spread briefly spiking from $8 to $20 before reverting)
is exactly the scenario a manual pre-event toggle handles better than any
automatic system.

---

### Timezone Fix

All timestamp display and chart X-axis labels to be converted from UTC to
`America/New_York` (EST/EDT). A `DISPLAY_TIMEZONE` constant added to
`config.py` so timezone is defined in one place across the application.

---

### Files Affected By Implementation

| File | Nature of Change |
|---|---|
| `config.py` | Add 6 new constants |
| `schwab_client.py` | Update fetch params, add safety filter function |
| `iv_engine.py` | Add expected move log check function |
| `app.py` | Date range, filter call, sidebar toggle, timezone fix |
| `db.py` | Verification only — no changes expected |

### Schema Impact
None. Existing `strike_snapshots` table is compatible with wider data.
DB growth will increase proportionally to wider strike/expiry coverage —
accepted and expected per design.

---

## 2026-06-21 — Fix: db.py duplicated causing SCHEMA overwrite
**Changed:** Replaced db.py with a clean single-copy version.
**Why:** The file had been fully duplicated (two complete copies concatenated).
The second copy redefined SCHEMA without strike_snapshots, so Python always
used the old schema. init_db() never created strike_snapshots even on a
fresh database file.
**Impact:** strike_snapshots table now created correctly on startup.
**Open questions:** Check git history to find when the duplication was introduced
— likely a session where file content was appended instead of replaced.

## 2026-06-21 — Fix: strike_snapshots table missing on existing DB
**Changed:** Deleted data/demo_dashboard.db and data/dashboard.db so init_db()
recreates all tables from scratch including the new strike_snapshots table.
**Why:** DB files were created in a prior session before strike_snapshots was
added to db.py. Existing files don't auto-migrate — CREATE TABLE IF NOT EXISTS
only adds tables missing from a blank DB, not from an already-existing file
that predates the schema change.
**Impact:** Lost ~1 session of synthetic demo history (no real data existed).
All tables now match current db.py schema. Won't recur unless schema changes
again without a migration step.
**Open questions / follow-ups:** For future schema changes, consider adding
a simple migration check in init_db() that runs ALTER TABLE or CREATE TABLE
for any new tables/columns, so existing DB files don't need to be deleted.

## 2026-06-21 — GitHub repo created + sync workflow established
**Changed:** Created private GitHub repo `chandan-singh4/spx-diagonal-dashboard`.
All project files as of end of this session pushed to `main` branch. `.gitignore`
already excludes `.env`, `data/token.json`, `data/dashboard.db` — credentials and
local data never go to GitHub.
**Why:** Needed a persistent source of truth that survives between Claude chat
sessions. Claude's sandbox is ephemeral; GitHub is not.
**Impact:** From this point forward, GitHub `main` is the canonical version of
the code. Any changes made in a Claude session must be applied locally and pushed
before closing — otherwise the next session starts from stale code.
**Workflow confirmed:** Repo is private, so Claude cannot fetch files directly.
Instead: paste DEV_JOURNAL.md at the start of each new chat (see instructions
above), paste specific files on request. Claude makes changes in sandbox, gives
edited files back, you apply locally and push.
**Open questions / follow-ups:** None for this entry — workflow is established.

## 2026-06-21 — Feature: strike-specific IV chart + independent expiry selectors
**Changed:**
- `db.py`: Added `strike_snapshots` table (expiry, strike, side, iv, bid, ask, volume, OI)
  with `save_strike_snapshot()` and `get_strike_history()` functions. ATM snapshots
  (existing) and strike snapshots are written independently each poll.
- `iv_engine.py`: Added `StrikeContract` dataclass and `strike_contract()` function
  — looks up a specific strike/side/expiry in the live chain_df, falls back to
  nearest available strike with a `found_exact=False` flag if the typed strike
  doesn't exist in the chain (e.g. coarser spacing far from spot).
- `app.py`: Full layout restructure:
  - Left panel: expiry selectors (now with stable `key=` — truly independent),
    + new Strike Selection section with two `st.number_input` fields (call strike,
    put strike), free-typed, defaulting to ATM and ATM-100 as starting suggestions.
    Live contract data (Front IV / Back IV / Ratio) shown per strike beneath inputs.
  - Right panel: TWO stacked charts:
    - TOP — "Selected-Strike IV": front vs back for the typed call strike (solid lines)
      and put strike (dotted lines), plus their respective IV ratios. Only renders
      once strike-specific history exists (first poll after strikes are entered).
    - BOTTOM — "ATM IV": existing floating-ATM chart, now explicitly labeled as
      "macro context."
  - All four legs recorded each poll when strikes are set: front_call, front_put,
    back_call, back_put (same strike both expiries, per confirmed strategy).

**Why:** ATM-only IV was confirmed insufficient for actual entry decisions — the
IV at your specific strike is what determines the real edge of a given trade, not
the floating ATM proxy. Also resolved the front/back expiry coupling bug (missing
`key=`) and the "only-selected-expiries-recorded" history gap from the prior session.

**Design decision logged:** User confirmed diagonal structure uses same strike on
both front and back for a given side (not four independent strikes). Transformation
to IC/double-diagonal handled separately via limit orders, not modeled in the IV
dashboard — IV analysis is entry-only.

**Impact:** Strike-specific chart will be blank until a few data points accumulate
(per the ~10s poll interval). ATM chart behavior unchanged.

**Open questions / follow-ups:**
- Demo mode's synthetic chain data uses a basic smile model (IV rises away from ATM)
  so the strike-specific chart will work in demo mode too, but IV values won't
  reflect realistic skew for a specific SPX strike — that's fine for UI testing.
- Theta Advantage score component still placeholder at 50. Next phase item.

## 2026-06-21 — Fix: front/back expiry coupling, and only-2-expiries-recorded history gap
**Changed:**
- `app.py` expiry selectors: removed the `back_options = [e for e in available_expiries if e > front_expiry]` filtering and the lack of `key=` on both selectboxes. Both now use the full expiry list with stable `key="front_expiry_select"` / `key="back_expiry_select"`, plus a non-blocking warning if Back ends up earlier than or equal to Front.
- `app.py` snapshot saving: now loops over every expiry visible in `chain_df` each poll and records its ATM IV, instead of only recording the two currently-selected (front/back) expiries.

**Why:** (1) Without explicit `key=`s, Streamlit treated the Back selectbox as a brand-new widget every time its option list changed (which happened every time Front changed, since the list was filtered relative to Front) — so it always reset to `index=0` of the new filtered list, which looked like "Back auto-snaps to a fixed offset after Front." (2) Only ever recording IV for the actively-selected pair meant switching the Front/Back dropdowns reset visible history to zero for the newly-selected pair — compounding the (separate, expected) issue of having started live data collection only today.

**Impact:** Front and Back are now fully independent selections that persist across reruns. History now accumulates for every visible expiry regardless of which pair is on screen, so switching expiries won't lose data going forward. This does NOT retroactively create history for today before this fix — only data captured after this change is recorded per-expiry-broadly.

**Open questions / follow-ups:** Strike-specific IV (not just ATM) is the next real gap, per feedback from another chat in this project — ATM-only IV doesn't reflect the actual contracts once specific strikes are selected for a trade. Scoping that as the next feature: needs a strike selector UI, a schema extension to store per-strike IV history, and `iv_engine` functions for non-ATM strike lookup.

## 2026-06-21 — Fix: OAuth "Redirect server exited" — switch to manual flow
**Changed:** `schwab_client.py`'s `get_client()` — replaced `schwab.auth.easy_client()`
with a manual check: load the cached token via `client_from_token_file()` if one
exists, otherwise authenticate via `schwab.auth.client_from_manual_flow()`.
**Why:** Your registered Schwab callback URL is `https://127.0.0.1` (no port).
`easy_client`'s automatic flow (`client_from_login_flow` under the hood) requires
a port number so it can spin up a local listener to auto-capture the OAuth
redirect — without one it fails with "Redirect server exited." The fix would be
adding a port to your registered callback URL, but per `schwab-py`'s own docs,
changing a registered callback URL likely triggers Schwab re-approval (can take
days). `client_from_manual_flow()` avoids the local listener entirely — it has
you copy-paste the post-login redirect URL by hand — so it works with your
exact already-approved callback URL, no portal changes, no re-approval wait.
**Impact:** First-ever login now requires one manual copy-paste step in the
terminal instead of being fully automatic. Every login after that (i.e., once
`data/token.json` exists) is unaffected — same automatic token-file loading and
refresh as before.
**Open questions / follow-ups:** This is still untested against the real Schwab
OAuth endpoint from my side (sandbox network can't reach it) — next checkpoint
is whether the manual copy-paste flow completes successfully and `token.json`
gets created.

## 2026-06-21 — Fix: Demo Mode silently reverting after process restart
**Changed:** `config.py` — `DEMO_MODE` now defaults to OFF automatically once real
`SCHWAB_APP_KEY`/`SCHWAB_APP_SECRET` are present in `.env`, instead of always
defaulting to ON. An explicit `DEMO_MODE=` value in `.env` still overrides this
either way.
**Why:** Confirmed root cause of "nothing happens when I toggle Demo Mode off and
restart" — every Streamlit process restart resets widget state to its coded
default, and that default was hardcoded to `True`. So toggling Demo Mode off in
the browser, then restarting the process (e.g. to pick up new `.env` values, per
the normal setup flow), silently flipped it back to Demo Mode without any error —
confirmed by `demo_dashboard.db`'s modified timestamp continuing to update every
poll cycle while `dashboard.db`/`token.json` never got created.
**Impact:** First-time setup still defaults to Demo Mode with zero config (no
credentials = no reason to default to live). Once real credentials exist, a
process restart no longer silently reverts to synthetic data — closes the exact
gap that caused the confusion.
**Open questions / follow-ups:** Live OAuth flow itself still untested from my
side (sandbox can't reach Schwab) — next real checkpoint is whether the login
popup/error appears correctly on the next restart.

## 2026-06-21 — Fix: mixed-precision timestamp parsing crash
**Changed:** `app.py` line ~138 — `pd.to_datetime(merged["timestamp"])` now passes
`format="ISO8601"`.
**Why:** Found while running locally for the first time. Seeded demo timestamps and
live-saved timestamps had inconsistent precision — Python's `datetime.isoformat()`
omits the microseconds segment when it's exactly zero, so some stored rows looked
like `...T14:30:00+00:00` and others like `...T19:26:22.672015+00:00`. Pandas
inferred one rigid format from the data and crashed (`ValueError: time data ...
doesn't match format`) the first time a mixed batch got parsed.
**Impact:** Chart now renders correctly. No data was lost — this was a parsing
bug, not a storage bug.
**Open questions / follow-ups:** None — confirmed working after the fix.

## 2026-06-21 — Flux-style chart, demo mode, schema redesign
**Changed:**
- Rebuilt the dashboard layout to match the NavigationTrading FLUX reference (top metric strip: IV Ratio/Front IV%/Back IV%/IV Index; expirations panel with day-change; dual-axis Front/Back IV + Ratio chart; historical range bars).
- Added a time-range selector: Today / 5D / 10D / 15D / 1M (per your request, beyond Flux's Today/5D/20D).
- Redesigned the DB schema from "snapshot per front/back pair" to "snapshot per expiry," so changing the front/back dropdown doesn't require fresh history — any two expiries' stored history can be joined on timestamp after the fact.
- Added `demo_data.py`: a synthetic, mean-reverting IV generator and a fake option chain, writing to a separate `demo_dashboard.db` so it never touches real collected data. Added a "Demo Mode" toggle in the sidebar (`app.py`), on by default, so the dashboard is runnable today with zero Schwab credentials.
- Added `iv_engine.range_stats()` for the historical-statistics range bars.

**Why:** You wanted the FLUX-style visualization and to "build something today" — but this sandbox can't reach `api.schwabapi.com` (network allowlist), so live data has to come from your machine. Demo Mode closes that gap: same UI, same chart code path, synthetic data instead of live, so you can verify the dashboard works *today* and just flip a switch once your local Schwab auth is wired up.

**Impact:** Breaking schema change — `iv_snapshots` table replaced by `expiry_snapshots`. No real data existed yet, so no migration needed. `app.py`'s data-fetch branch is now demo/live conditional; downstream chart and stats code is identical either way, which is intentional — verifies the same code path you'll actually trade against.

**Open questions / follow-ups:**
- IV Index metric is a simple average-of-all-expiries approximation, not FLUX's proprietary calc — revisit if you want it to mean something more specific.
- Theta Advantage in the Trade Quality Score is still a placeholder (50 flat) — Phase 3 item.
- Smoke-tested the full demo data → chart pipeline in the sandbox (seed → fetch → merge → range stats) — all passed. Have NOT been able to test live Schwab auth/data calls from this environment; that first live run is on you, locally.

## 2026-06-21 — Initial scaffold
**Changed:** Created project structure: README, config, Schwab auth client, SQLite
storage layer, IV term-structure engine, Streamlit MVP dashboard.
**Why:** Establish the baseline architecture before connecting real credentials —
get the plumbing right (auth, storage schema, IV math) before layering on UI polish.
**Impact:** No live strategy logic yet — this is infrastructure only. Trade Quality
Score, transformation calculator, and payoff diagrams are stubbed for Phase 3-5.
**Open questions / follow-ups:**
- Need real Schwab app approval before any live data flows.
- IV percentile calculations will be unreliable until several weeks of history accumulate.
- Decide on polling interval (currently defaulted to 10s) once you've observed actual
  Schwab API rate limit behavior in practice.
