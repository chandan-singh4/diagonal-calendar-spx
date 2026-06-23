# SPX Diagonal Dashboard — Implementation Plan
## Design Review Document (Pre-Code Approval)

**Version:** 1.0  
**Status:** Pending Approval  
**Scope:** collector.py, schema DDL, db.py refactor  
**Not in scope:** app.py refactor, positions table, backtesting engine

---

## Finalized Architecture Decisions (Reference)

| Decision | Value |
|---|---|
| STRIKE_COUNT | 80 (total, centered on ATM) |
| STRIKE_FETCH_WIDTH_POINTS | ±300 points hard filter |
| MAX_EXPIRY_DTE | 20 days |
| Expected expirations per snapshot | ~10–11 |
| Poll interval (normal) | 300 seconds |
| Poll interval (open/close event) | 60 seconds |
| Database | SQLite |
| Writer | collector.py only |
| Reader | app.py only |

---

## Phase 1 — Database Design

### Overview of Tables

The schema consists of four tables. Three are data tables; one is a diagnostic log. Every design decision flows from a single principle: **a snapshot is the atomic unit of collection.** Every option row, every computed ATM IV, every gap record is either anchored to a snapshot or describes the absence of one.

---

### Table 1: `snapshots`

**Purpose:** One row per collection cycle. The anchor for all other data. Records both successful collections and failures, so the history of what was attempted — not just what succeeded — is preserved.

**Why it exists:** Without an anchor, there is no way to know whether a set of option rows represents a complete, healthy collection or a partial failure. The `status` field transforms the row set from a pile of numbers into a verified dataset with known quality.

| Column | Type | Required | Purpose |
|---|---|---|---|
| `snapshot_id` | INTEGER PK AUTOINCREMENT | Yes | Surrogate key. Referenced by all child tables. |
| `snapshot_timestamp` | TEXT NOT NULL | Yes | UTC ISO8601. The moment collection began. |
| `status` | TEXT NOT NULL | Yes | `COMPLETE`, `PARTIAL`, or `FAILED`. Controls which snapshots the analytics layer trusts. |
| `underlying_price` | REAL | Yes | SPX mid-price at collection time. Required for moneyness calculations, ATM detection, and diagonal spread valuation. |
| `underlying_bid` | REAL | Recommended | SPX bid. Widens during stress. Provides spread context for backtesting entry conditions. |
| `underlying_ask` | REAL | Recommended | SPX ask. Partner to above. |
| `vix_value` | REAL | Recommended | VIX spot at collection time. Distinguishes SPX-specific IV distortion from broad volatility regime. Essential for interpreting IV percentile readings in historical context. |
| `market_session` | TEXT | Yes | `OPEN`, `MIDDAY`, or `CLOSE`. Required to interpret sampling density. Without it, uneven poll intervals look like IV volatility in time-series charts. |
| `poll_interval_used` | INTEGER | Yes | Seconds. Actual interval used for this cycle. Audit trail for collection behavior. |
| `strikes_fetched` | INTEGER | Yes | Actual option row count written. Detects partial fetches even when status appears COMPLETE. |
| `expiries_fetched` | INTEGER | Yes | Number of distinct expirations collected. Confirms all expected expirations were returned. |
| `collection_latency_ms` | INTEGER | Yes | Wall-clock milliseconds for the full API cycle. Snapshots that took 8,000ms have non-simultaneous IV values. Allows filtering high-latency snapshots from time-sensitive analysis. |
| `error_message` | TEXT | Conditional | Populated for PARTIAL or FAILED. Free text. Not queried programmatically. |
| `notes` | TEXT | Optional | Manual annotations. Fed day, VIX spike, circuit breaker, etc. Supports retrospective analysis. |

**Relationships:** Parent to `option_rows` and `atm_iv_by_expiry`. No parent of its own.

**Expected row volume:** ~100 rows per trading day. ~25,200 rows per year. Negligible storage impact — this table stays small forever.

---

### Table 2: `option_rows`

**Purpose:** One row per option contract per snapshot. The primary data store. Contains the raw market data that all IV analysis, term structure charts, and diagonal spread analytics are built on.

**Why it exists:** This is the irreplaceable record. Schwab has no historical intraday option chain endpoint. Every row in this table represents a moment that cannot be reconstructed from any external source once missed.

| Column | Type | Required | Purpose |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | Yes | Row identity. |
| `snapshot_id` | INTEGER NOT NULL FK | Yes | Links to `snapshots`. Enables chain reconstruction at any historical timestamp. |
| `expiry_date` | TEXT NOT NULL | Yes | `YYYY-MM-DD`. The expiration this contract belongs to. |
| `dte` | INTEGER NOT NULL | Yes | Days to expiration as of snapshot_timestamp. Stored explicitly — not computed. DTE at collection time is a historical fact, not a derivable value. Required for theta decay modeling. |
| `strike` | REAL NOT NULL | Yes | Strike price. |
| `right` | TEXT NOT NULL | Yes | `C` or `P`. |
| `bid` | REAL | Yes | Bid price. Required for spread calculation and entry modeling. |
| `ask` | REAL | Yes | Ask price. Partner to bid. |
| `mark` | REAL | Yes | `(bid + ask) / 2`. The standard valuation price for spread positions. Stored explicitly to avoid recomputation in every query. |
| `last` | REAL | Optional | Last traded price. Useful when bid/ask spread is wide and last trade is more informative. Low storage cost. |
| `iv` | REAL | Yes | Implied volatility as a decimal (0.18 = 18%). The core analytical input. Everything is built on this. |
| `delta` | REAL | Yes | Directional exposure. Required for strike selection in diagonal setup and transformation timing. |
| `gamma` | REAL | Yes | Rate of delta change. Elevated near expiry. Required for understanding risk acceleration as front leg approaches expiration. |
| `theta` | REAL | Yes | Time decay per day. The economic engine of the diagonal strategy — selling front theta, owning back theta. |
| `vega` | REAL | Yes | IV sensitivity. Required for transformation timing analysis. When vega is highest, IV contraction has the most impact on position value. |
| `volume` | INTEGER | Yes | Contracts traded today. First-order liquidity signal. Zero-volume strikes have unreliable IVs. Required for Trade Quality Score. |
| `open_interest` | INTEGER | Yes | Total open contracts. Second-order liquidity signal. Low OI means wide markets and high slippage risk. Required for Trade Quality Score. |
| `intrinsic_value` | REAL | Recommended | `max(0, underlying - strike)` for calls; `max(0, strike - underlying)` for puts. Stored at collection time to avoid requiring a join back to `snapshots.underlying_price` in every historical query. |
| `time_value` | REAL | Recommended | `mark - intrinsic_value`. The pure optionality premium. Diagonal strategy extracts time value from front leg. Stored for the same join-avoidance reason as intrinsic_value. |

**Relationships:** Child of `snapshots` via `snapshot_id`.

**Expected row volume:**
- 80 strikes × 2 rights = 160 rows per expiry
- 10–11 expiries per snapshot = ~1,600 rows per snapshot
- ~100 snapshots per trading day = ~160,000 rows per day
- ~252 trading days per year = ~40.3 million rows per year

**Storage impact:** See Phase 5 for detailed estimates.

---

### Table 3: `atm_iv_by_expiry`

**Purpose:** Pre-aggregated ATM implied volatility per expiry per snapshot. Computed and stored at collection time. This table is what your IV percentile engine, term structure charts, and diagonal opportunity scanner primarily query.

**Why it exists:** Consider the query: *"Give me the front-vs-back ATM IV spread for every complete snapshot in the last 180 days."* Without this table, that query must scan 40+ million rows in `option_rows`, join to `snapshots` to get the underlying price, identify the ATM strike per expiry per snapshot, aggregate, and compute spreads — every time the dashboard loads. With this table, the same query scans roughly 1.8 million rows of pre-aggregated data. The performance difference is one to two orders of magnitude, and it compounds as history grows.

This table is the architectural feature that makes the dashboard remain fast after one year of data collection.

| Column | Type | Required | Purpose |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | Yes | Row identity. |
| `snapshot_id` | INTEGER NOT NULL FK | Yes | Links to `snapshots`. |
| `expiry_date` | TEXT NOT NULL | Yes | The expiration this ATM record describes. |
| `dte` | INTEGER NOT NULL | Yes | DTE as of snapshot_timestamp. Supports term structure queries without joins. |
| `atm_strike` | REAL NOT NULL | Yes | The strike closest to `underlying_price` at collection time. Records which strike was ATM historically — ATM shifts as SPX moves, so this cannot be derived retrospectively. |
| `atm_call_iv` | REAL | Yes | IV of the ATM call for this expiry at this snapshot. |
| `atm_put_iv` | REAL | Yes | IV of the ATM put for this expiry at this snapshot. |
| `atm_avg_iv` | REAL | Yes | `(atm_call_iv + atm_put_iv) / 2`. The standard single-number IV representation for a given expiry. This is what goes into term structure charts and IV spread calculations. |
| `iv_spread_to_front` | REAL | Yes | `this_expiry_atm_avg_iv - front_expiry_atm_avg_iv`. Null for the front expiry itself. Pre-computed term structure spread. The most-queried derived value in the entire schema. |
| `iv_ratio_to_front` | REAL | Recommended | `this_expiry_atm_avg_iv / front_expiry_atm_avg_iv`. Null for front. The ratio form of term structure. Useful for detecting inverted curves where the absolute spread understates the distortion. |

**Relationships:** Child of `snapshots` via `snapshot_id`. Computed from `option_rows` at collection time.

**Expected row volume:**
- 10–11 rows per snapshot (one per expiry)
- ~1,050 rows per trading day
- ~264,600 rows per year

**Storage impact:** Trivial. This table is the most-queried in the system and the smallest by far.

---

### Table 4: `collection_gaps`

**Purpose:** Records intervals where no collection occurred during market hours. Written by `collector.py` on startup when it detects that time has passed since the last snapshot.

**Why it exists:** Your IV percentile engine will eventually state something like "current IV spread is at the 88th percentile based on 180 days of data." Without a gap log, that claim is silent about data completeness. If 15 trading days are missing — particularly volatile days where IV moved significantly — the percentile calculation is not only inaccurate but biased. The gap log lets the analytics layer display a data quality warning and lets you assess whether your percentile figures are trustworthy.

| Column | Type | Required | Purpose |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | Yes | Row identity. |
| `gap_start` | TEXT NOT NULL | Yes | UTC timestamp of last successful snapshot before the gap. |
| `gap_end` | TEXT NOT NULL | Yes | UTC timestamp of first successful snapshot after the gap. |
| `gap_minutes` | REAL NOT NULL | Yes | Duration in minutes. Directly queryable for coverage calculations. |
| `expected_snapshots_lost` | INTEGER | Yes | Estimate: `gap_minutes / poll_interval_used`. Tells the analytics layer how much data is missing, not just that a gap exists. |
| `reason` | TEXT | Yes | `COLLECTOR_OFFLINE`, `API_ERROR`, `MARKET_CLOSED`, `HOLIDAY`, `UNKNOWN`. Categorizes the cause. `MARKET_CLOSED` and `HOLIDAY` gaps are expected and should be excluded from data quality warnings. |
| `detected_at` | TEXT NOT NULL | Yes | UTC timestamp when collector noticed the gap on restart. Audit trail. |
| `notes` | TEXT | Optional | Manual context. "Power outage," "Windows update reboot," etc. |

**Relationships:** None. Gap records describe the absence of snapshots, so they cannot reference `snapshot_ids` that don't exist.

**Expected row volume:** Sparse. Even with frequent restarts, this table might accumulate a few hundred rows per year. Storage is negligible.

---

## Phase 2 — Database Initialization

### Files Affected

**`db.py`** is the only file responsible for schema creation. It will be refactored to:
1. Create all four tables if they do not exist
2. Create all indexes
3. Manage schema versioning
4. Provide read and write functions used by `collector.py` and `app.py` respectively

No schema logic lives in `collector.py` or `app.py`. They call `db.py` functions; they do not issue DDL directly.

### Schema Creation Strategy

Schema creation uses `CREATE TABLE IF NOT EXISTS` for all tables. This means running `db.py` initialization against an existing database is safe — it will create missing tables and skip existing ones. It will not modify columns on existing tables.

On first run against a fresh database, the full schema is created in a single transaction so either everything succeeds or nothing does. A half-initialized database is not a valid state.

### Schema Versioning

A lightweight `schema_version` table will be added:

```
schema_version
--------------
version        INTEGER NOT NULL
applied_at     TEXT NOT NULL
description    TEXT
```

The current schema is version 1. On startup, `db.py` reads the current version and compares it to the expected version in code. If they match, proceed. If the database has no version table (fresh install), create everything and write version 1.

**Why not a full migration framework?** Tools like Alembic add complexity that is not justified at this stage. The version table gives you the minimum viable mechanism to detect schema drift and know whether a migration is needed. When schema version 2 is required in the future, the process will be:
1. Write a migration function in `db.py`
2. Increment the expected version constant
3. On startup, `db.py` detects version mismatch and runs the migration

### Migration Support Now vs. Later

Migration support is **not needed now**. The database is young. The right time to add migration logic is version 2 — when you actually have data you cannot afford to lose and a schema change you need to make. Building migration infrastructure before you have either problem is premature.

What IS needed now: the `schema_version` table, so that when migration support is added, the existing database has a known version to migrate from.

---

## Phase 3 — Collector Lifecycle

### Step 1: Startup

Collector initializes in this order:
1. Load environment variables from `.env` (API credentials)
2. Initialize database connection via `db.py` (creates schema if needed)
3. Check schema version — halt with clear error if mismatch detected
4. Authenticate with Schwab (`client_from_token_file` or `client_from_manual_flow` if token expired)
5. Log startup event with timestamp
6. Run gap detection (see Step 7)
7. Enter the main polling loop

### Step 2: Market Hours Check

Before every collection attempt, the collector checks whether the US equity market is open. The check uses Eastern Time regardless of the machine's local timezone (this is why `DISPLAY_TIMEZONE = "America/New_York"` is in config).

Market is considered open if:
- Current ET time is between 09:30 and 16:15 (SPX options trade until 4:15 PM)
- Current day is Monday through Friday
- Current date is not a recognized US market holiday

If market is closed, the collector sleeps for a configurable check interval (e.g., 60 seconds) and re-checks. It does not exit — it waits. This means you can start the collector at any time and it will self-activate at market open.

A `holidays` list will be maintained in `config.py` as a simple set of date strings. This is the lowest-complexity approach and sufficient for a personal tool. No third-party holiday calendar library is needed.

### Step 3: Polling Schedule Selection

Once market is confirmed open, the collector selects the poll interval based on current session:

- **OPEN session** (09:30–10:00 ET): `POLL_INTERVAL_EVENT = 60` seconds
- **MIDDAY session** (10:00–15:30 ET): `POLL_INTERVAL_NORMAL = 300` seconds
- **CLOSE session** (15:30–16:15 ET): `POLL_INTERVAL_EVENT = 60` seconds

The selected interval is stored in `poll_interval_used` on the snapshot record, so the collection density is auditable historically.

### Step 4: Schwab API Request

Each collection cycle makes exactly two API calls in sequence:

**Call 1: SPX quote** — Fetches the current SPX underlying price (bid, ask, last). This becomes `underlying_price`, `underlying_bid`, `underlying_ask` on the snapshot.

**Call 2: SPX option chain** — Fetches all expirations with DTE ≤ 20, with STRIKE_COUNT = 80 centered on ATM. The ±300-point hard filter is applied in Python after the response is received, not as an API parameter. This ensures the filter is consistent regardless of how Schwab handles the `strikeCount` parameter.

**VIX quote** — A third call fetches the VIX spot price. This call is non-critical: if it fails, collection proceeds without VIX rather than failing the entire snapshot.

The total wall-clock time for these calls is measured and stored as `collection_latency_ms`.

### Step 5: Snapshot Creation

Before writing any option rows, a snapshot record is created with `status = 'PARTIAL'`. This is the correct failure-safe ordering: if the process crashes during option row insertion, the snapshot record exists with PARTIAL status rather than orphaned rows with no parent. The snapshot is updated to `COMPLETE` only after all rows are successfully written and committed.

The snapshot record captures:
- `snapshot_timestamp` (current UTC time, set when collection began — not when it completed)
- `underlying_price`, `underlying_bid`, `underlying_ask`
- `vix_value` (null if VIX call failed)
- `market_session`
- `poll_interval_used`

### Step 6: Option Row Insertion

After the snapshot is created, option rows are inserted in a single database transaction. The full processing pipeline per option contract:

1. Apply ±300-point strike filter — discard anything outside `underlying_price ± 300`
2. Compute `mark = (bid + ask) / 2`
3. Compute `intrinsic_value` using underlying price from the snapshot
4. Compute `time_value = mark - intrinsic_value`
5. Compute `dte` from snapshot timestamp and expiry date
6. Insert row with `snapshot_id` foreign key

After all option rows are inserted, `atm_iv_by_expiry` is populated: for each distinct expiry, find the strike nearest to `underlying_price`, extract its call and put IVs, compute `atm_avg_iv`, and compute `iv_spread_to_front` and `iv_ratio_to_front` by referencing the front expiry row just computed.

All of this — option rows + atm_iv_by_expiry — commits in a single transaction. Either all rows for a snapshot are written or none are.

### Step 7: Snapshot Completion

After the transaction commits:
1. Update `snapshots.status` to `COMPLETE`
2. Update `snapshots.strikes_fetched` with actual row count
3. Update `snapshots.expiries_fetched` with distinct expiry count
4. Log completion with latency

### Step 8: Gap Detection

Gap detection runs in two places:
- **On startup:** Compares `now()` to the last `snapshot_timestamp` in the database. If the gap exceeds one poll interval and falls within market hours, a gap record is written.
- **After each successful snapshot:** Compares the current snapshot timestamp to the previous one. If the gap is larger than expected (more than 1.5× the poll interval), a gap record is written. This catches cases where a single collection cycle was unusually slow or failed silently.

The gap detection logic deliberately ignores overnight and weekend periods — a 16-hour gap between Friday close and Monday open is not a data gap, it is expected behavior. The market hours check (same logic as Step 2) is applied when evaluating whether a gap represents missing data.

### Step 9: Error Handling

See Phase 4 for detailed failure scenarios.

### Step 10: Sleep Until Next Cycle

After a successful collection, the collector sleeps for `poll_interval_used` seconds minus `collection_latency_ms`. This drift-corrected sleep keeps the collection schedule close to wall-clock intervals. Without drift correction, a 5-minute poll that takes 8 seconds will drift to 5:08, then 5:16, then 5:24, accumulating meaningful timing error across a full trading day.

If the collection failed, the sleep is a shorter backoff interval (e.g., 30 seconds) before retrying — not the full poll interval.

---

## Phase 4 — Failure Scenarios

### Scenario 1: Schwab API Timeout

**What gets written:** The snapshot record is created with `status = 'FAILED'` and `error_message` populated with the timeout description. No option rows are written because there is no data to write.

**Is data lost?** Yes. One collection cycle is missed. The snapshot record serves as a permanent record that collection was attempted and failed.

**Recovery:** The collector logs the failure, sleeps the backoff interval (30 seconds), and attempts the next cycle. No human intervention required.

### Scenario 2: Partial Option Chain Returned

This is the most operationally dangerous scenario because it is silent without explicit detection.

**What gets written:** The snapshot is created with `status = 'PARTIAL'`. Option rows for whatever was returned are written. `strikes_fetched` and `expiries_fetched` are populated with actual counts. The gap between actual and expected counts is visible in these fields.

**Is data lost?** Partially. The rows that were returned are preserved. The missing strikes are gone.

**Recovery:** The analytics layer filters `WHERE status = 'COMPLETE'`. PARTIAL snapshots are stored but excluded from IV percentile calculations and term structure analysis. This prevents partial data from silently polluting statistics.

**Detection:** After the option chain response, the collector compares `expiries_fetched` against the expected count based on MAX_EXPIRY_DTE. If fewer expirations are returned than expected, the snapshot is immediately marked PARTIAL before any rows are written. Similarly, if total strikes are more than 20% below the expected count, PARTIAL status is applied.

### Scenario 3: SQLite Database Locked

SQLite allows one writer at a time. If `app.py` happens to be writing (it should not be — it is read-only by design), or if a previous collector transaction is still open, a write attempt will return `database is locked`.

**What gets written:** Nothing. The transaction is not committed.

**Is data lost?** One cycle is missed.

**Recovery:** The collector catches the `OperationalError`, logs it, and retries after the backoff interval. The lock is almost always transient (milliseconds) and resolves on retry.

**Prevention:** The read-only enforcement on `app.py` is the primary prevention. SQLite WAL (Write-Ahead Logging) mode will be enabled on the database at initialization — this allows readers and the single writer to coexist without locks under normal conditions.

### Scenario 4: Collector Process Crashes Mid-Insertion

**What gets written:** The snapshot record exists with `status = 'PARTIAL'` (it was created before insertion began). Because insertion is wrapped in a single transaction, if the crash happens during insertion, the transaction is rolled back by SQLite on next open. No partial option rows survive.

**Is data lost?** The snapshot record remains with PARTIAL status. No corrupted partial data exists in `option_rows`.

**Recovery:** On the next startup, gap detection identifies the interruption. The PARTIAL snapshot record remains as an audit trail. Collection resumes normally.

### Scenario 5: Computer Reboots or Power Outage

**What gets written:** Same as Scenario 4. SQLite's transaction guarantees protect against partial writes. The database will be in a consistent state when the machine restarts.

**Is data lost?** All collection between the last committed snapshot and the restart is lost. The gap is logged on next startup.

**Recovery:** Start the collector manually after reboot. Gap detection runs on startup and logs the interruption. Collection resumes from that point. There is no automatic start-on-boot mechanism in this design — that would require a Windows scheduled task or service, which is out of scope for the current phase.

**Note:** The 7-day Schwab refresh token means that if the machine is off for more than 7 days, manual OAuth re-authorization is required before collection can resume.

### Scenario 6: Network Disconnection

**What gets written:** If disconnection occurs during the Schwab API call, the request times out. Behavior is identical to Scenario 1 (API timeout).

**Is data lost?** One or more cycles are missed, logged as FAILED snapshots.

**Recovery:** The collector retries with backoff. Once the network is restored, normal collection resumes automatically.

### Scenario 7: Market Holiday

**What gets written:** Nothing. The market hours check in Step 2 prevents any collection attempt. Holidays are recognized in `config.py`.

**Is data lost?** No. There is no market data to collect on a holiday.

**Gap handling:** The gap detection logic must recognize holiday gaps as expected absences, not data losses. When writing a gap record, the `reason` field will be set to `HOLIDAY` for gaps that align with known holidays. The analytics layer excludes HOLIDAY and MARKET_CLOSED gaps from data quality warnings.

---

## Phase 5 — Data Growth Analysis

### Row Count Projections

**Rows per snapshot:**
- `option_rows`: 80 strikes × 2 rights × 10 expiries = 1,600 rows
- `atm_iv_by_expiry`: 10 rows (one per expiry)
- `snapshots`: 1 row
- **Total per snapshot: ~1,611 rows**

**Snapshots per trading day:**
- OPEN session (09:30–10:00, 30 min at 60-second intervals): 30 snapshots
- MIDDAY session (10:00–15:30, 330 min at 300-second intervals): 66 snapshots
- CLOSE session (15:30–16:15, 45 min at 60-second intervals): 45 snapshots
- **Total: ~141 snapshots per day**

*(Note: This is higher than earlier estimates because the OPEN and CLOSE windows at 60-second intervals are quite active. The exact count will vary — this is a ceiling, not a guarantee.)*

**Rows per trading day:**
- `option_rows`: 141 × 1,600 = 225,600 rows
- `atm_iv_by_expiry`: 141 × 10 = 1,410 rows
- `snapshots`: 141 rows
- **Total: ~227,151 rows per day**

**Rows per month (21 trading days):** ~4.77 million rows  
**Rows per year (252 trading days):** ~57.2 million rows

---

### Storage Estimates

**Per-row storage assumptions (data + index overhead):**
- `option_rows`: ~280 bytes per row (19 columns, mix of REAL and INTEGER, plus 4 index entries averaging ~30 bytes each)
- `atm_iv_by_expiry`: ~160 bytes per row (10 columns, plus 2 index entries)
- `snapshots`: ~350 bytes per row (15 columns, mostly text and integer, plus 2 index entries)
- `collection_gaps`: negligible

| Period | option_rows | atm_iv_by_expiry | snapshots | **Total** |
|---|---|---|---|---|
| 6 months | ~18.9 GB | ~223 MB | ~15 MB | **~19.1 GB** |
| 1 year | ~37.9 GB | ~447 MB | ~30 MB | **~38.3 GB** |
| 3 years | ~113.6 GB | ~1.34 GB | ~90 MB | **~115 GB** |

**Important caveat on these numbers:** SQLite page size, fill factor, and WAL overhead add 10–25% to raw byte calculations. These estimates include that buffer. However, they assume collection runs every trading day at the full rate. Actual storage will be somewhat lower — market open/close phases, API failures, and partial trading days reduce effective throughput.

### Storage Mitigation Strategy

The 3-year projection (115 GB) is large for a personal machine's SQLite database. A tiered strategy is recommended:

**Tier 1 (0–18 months): Collect everything as designed.** No pruning. Full fidelity.

**Tier 2 (18 months+): Consider pruning `option_rows` for old, far-OTM strikes.** The high-frequency data you need for IV percentile calculations and diagonal analytics is concentrated near ATM. Strikes ±200 points OTM from a year ago have limited analytical value. A pruning job that removes `option_rows` where `abs(strike - snapshot.underlying_price) > 150` and `snapshot_timestamp < 12 months ago` could reduce the database to roughly 60% of projected size with minimal loss of useful data.

**Critical design rule:** Even if `option_rows` is pruned, `atm_iv_by_expiry` and `snapshots` are NEVER pruned. These tables are small and contain the derived data that all analytics depend on. The historical ATM IV record is irreplaceable.

**Tier 3 (3+ years): Annual database archival.** Prior-year data moves to a separate `archive_YYYY.db` file. The active `dashboard.db` contains only the most recent year. The analytics layer reads from the active database; archived databases can be queried ad-hoc when needed.

---

## Phase 6 — Query Performance

### Indexes

**`snapshots` table:**
```
idx_snapshots_timestamp         ON snapshots(snapshot_timestamp)
idx_snapshots_status            ON snapshots(status)
idx_snapshots_timestamp_status  ON snapshots(snapshot_timestamp, status)
```
The composite index `(timestamp, status)` is the primary workhorse. Nearly every dashboard query begins with "give me all COMPLETE snapshots between date A and date B." This index covers both filter conditions and avoids a table scan.

**`option_rows` table:**
```
idx_option_rows_snapshot_id     ON option_rows(snapshot_id)
idx_option_rows_contract        ON option_rows(expiry_date, strike, right)
idx_option_rows_contract_snap   ON option_rows(expiry_date, strike, right, snapshot_id)
```
`idx_option_rows_snapshot_id` enables chain reconstruction: "give me all rows for snapshot 4,892." Without it, full table scan.

`idx_option_rows_contract` enables contract-based lookups: "give me all rows for the 5700C expiring 2026-06-26." 

`idx_option_rows_contract_snap` is the most important index in the schema. It makes the query "give me the IV history of the 5700C over the last 30 days" a covering index scan rather than a full table scan. At 40+ million rows per year, the difference between these is seconds versus milliseconds.

**`atm_iv_by_expiry` table:**
```
idx_atm_iv_snapshot_id          ON atm_iv_by_expiry(snapshot_id)
idx_atm_iv_expiry_snap          ON atm_iv_by_expiry(expiry_date, snapshot_id)
```
`idx_atm_iv_expiry_snap` enables "give me the ATM IV history of the 2026-07-18 expiration over the last 60 days" — the query that drives term structure charts and IV percentile calculations.

**`collection_gaps` table:**
```
idx_gaps_start                  ON collection_gaps(gap_start)
```
Allows time-range queries on gaps. Used by the data quality dashboard component.

---

### Query Performance Projections at 1 Year of Data

| Query | Table Scanned | Expected Rows Touched | Expected Performance |
|---|---|---|---|
| Load term structure chart (last 30 days) | `atm_iv_by_expiry` + `snapshots` | ~63,000 rows | < 100ms |
| IV history for specific strike (last 30 days) | `option_rows` via contract+snap index | ~4,230 rows | < 50ms |
| IV percentile calculation (180-day lookback) | `atm_iv_by_expiry` | ~1.58 million rows | 200–800ms |
| Full chain reconstruction for single timestamp | `option_rows` via snapshot_id index | ~1,600 rows | < 20ms |
| Dashboard home page load (all charts) | Multiple tables | Aggregate | 1–3 seconds |

**The IV percentile query is the most expensive** because it scans 6 months of `atm_iv_by_expiry` rows. This is why that table exists as a pre-aggregation layer — the equivalent scan of `option_rows` would touch 100× more rows and be unusable in a dashboard context.

### SQLite Limitations to Know

**Concurrent writers:** SQLite allows exactly one writer at a time. The architecture enforces this — `collector.py` is the only writer. No limitation in practice.

**Database file size:** SQLite performs well up to ~100GB with proper indexing. The 3-year projection approaches this ceiling. Annual archival (described in Phase 5) keeps the active database well within comfortable bounds.

**No column type enforcement:** SQLite does not enforce column types at the storage level. A Python bug that inserts a string into an IV column will succeed silently. This is mitigated by validation logic in `db.py` before insertion — type checking happens in Python, not SQLite.

**No built-in percentile functions:** SQLite has no `PERCENTILE_CONT()` or `PERCENTILE_DISC()` function. IV percentile calculations must load the relevant column into Python/pandas and compute there. This is the correct approach anyway — the analytics belong in Python, not SQL.

**WAL mode:** Write-Ahead Logging mode will be enabled at initialization. This prevents the collector write cycle from blocking dashboard read queries, which is critical when `app.py` is open during active collection.

---

## Phase 7 — Future Expansion

This section explains how each potential future addition integrates with the current schema without requiring structural changes to existing tables.

### Position Tracking

A `positions` table stores your actual diagonal spread entries:

```
positions
---------
position_id         PK
entry_timestamp     TEXT
front_expiry        TEXT
back_expiry         TEXT
call_strike         REAL
put_strike          REAL
entry_debit         REAL      -- net premium paid
target_profit       REAL      -- e.g. +$5
status              TEXT      -- OPEN, TRANSFORMED, CLOSED
notes               TEXT
```

This table references no foreign keys into the existing schema. It is a standalone record of your trades. The analytics layer joins it to `option_rows` and `atm_iv_by_expiry` using `expiry_date` and `strike` as natural keys — not FK relationships. This keeps the collector schema clean and position tracking as an additive layer.

### Realized P&L

A `position_snapshots` table links positions to market data at each collection cycle:

```
position_snapshots
------------------
id                  PK
position_id         FK → positions
snapshot_id         FK → snapshots
front_call_mark     REAL
front_put_mark      REAL
back_call_mark      REAL
back_put_mark       REAL
spread_value        REAL      -- current position value
unrealized_pnl      REAL      -- spread_value - entry_debit
```

This table enables P&L tracking over time and transformation threshold detection without modifying any existing table.

### Diagonal Trade Journal

Built entirely on top of `positions` and `position_snapshots`. No schema changes. The journal is a report generated from existing data — a Streamlit page in `app.py` that queries position history and formats it as a trade log.

### Backtesting Engine

Uses `option_rows` and `atm_iv_by_expiry` directly. A backtest selects snapshots within a date range, reconstructs the chain at each timestamp, and simulates diagonal spread entries and exits based on configurable rules. The snapshot-anchored schema was designed explicitly to support this use case — chain reconstruction at any historical timestamp is a first-class query.

The `status = 'COMPLETE'` filter on snapshots is the backtesting engine's data quality gate.

### GEX (Gamma Exposure) Metrics

A `gex_by_expiry` table can be added as a sibling to `atm_iv_by_expiry`:

```
gex_by_expiry
-------------
id            PK
snapshot_id   FK → snapshots
expiry_date   TEXT
gex           REAL    -- sum(gamma × open_interest × underlying²× 100)
net_gex       REAL    -- calls minus puts
```

GEX is computed from `gamma` and `open_interest` already stored in `option_rows`. The computation happens at collection time in `collector.py` and is stored here as a derived aggregate. No changes to existing tables.

### IV Percentile Engine

The full IV percentile system is already supported by the current schema. The required query is:

```
Given today's atm_avg_iv for the front expiry,
what percentage of historical atm_avg_iv observations
for the front expiry over the last 180 COMPLETE trading days
were lower than today's value?
```

This query uses `atm_iv_by_expiry` joined to `snapshots` (for status filtering and date range). The `collection_gaps` table provides coverage metadata so the engine can report "88th percentile based on 163 of 180 trading days" when gaps exist, rather than silently overclaiming data completeness.

---

## Summary of Decisions

| Decision | Recommendation | Rationale |
|---|---|---|
| Snapshot-anchored schema | Confirmed | Chain reconstruction requires atomic collection unit |
| `status` field with PARTIAL | Confirmed | Silent partial fetches are the most dangerous failure mode |
| `atm_iv_by_expiry` pre-aggregation | Strongly recommended | 100× query performance difference at 1-year scale |
| `collection_gaps` table | Confirmed | IV percentile accuracy requires data completeness awareness |
| WAL mode | Confirmed | Prevents collector writes from blocking dashboard reads |
| `schema_version` table | Confirmed | Minimum viable migration detection |
| Full migration framework | Defer | Not justified until schema version 2 is needed |
| `positions` table | Defer | Build after collector is working; no FK into collector schema |
| Annual database archival | Plan for 18 months | 3-year SQLite projection approaches practical ceiling |
| Strike pruning for old data | Plan for 18 months | Far-OTM historical data has low analytical value |

---

*End of Implementation Plan v1.0*
*Awaiting approval before DDL or Python code is written.*
