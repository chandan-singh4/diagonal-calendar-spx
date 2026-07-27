# progress_log.md — Chronological Development Log

Newest first. Every session appends an entry: what was completed, what was discovered,
what broke, and what remains.

---

## 2026-07-26 (session 5) — the collection cycle gets its first checks

### Completed

**`_run_cycle()` is now covered — 23 tests (`tests/test_collector_cycle.py`).** This is the
function that produced every price in the database, and it had no checks at all, while the gap
classifier guarding it had 38. The alarm system was tested; the thing it guards was not.

**Driven end-to-end, not stubbed.** The Schwab calls are patched at the `schwab_client` module
boundary and the writes go to a throwaway database, so the cycle runs for real from quote to
sealed snapshot. The chain fixture is built in Schwab's **raw nested JSON shape**, so
`chain_to_dataframe()` parsing executes rather than being skipped — a pre-parsed fixture would
have left the exact layer a Schwab format change would break completely unprotected.

**Assertions read the database, not the mocks.** Checking that a function was called only
confirms the code does what it currently does. Reading back the stored rows confirms the contract
the dashboard actually depends on. Covered: snapshot sealing on every exit path, IV
percentage→decimal conversion, mark as bid/ask midpoint, side-correct intrinsic value, ATM strike
selection, front-expiry NULLs, missing-IV skipping, VIX being non-fatal, the `last` price
fallback, error truncation, and expiry trimming.

**Mutation-verified: 7 injected faults, 7 caught.** IV left as a percentage, transposed call/put
intrinsic, a failed cycle no longer sealing its snapshot, expiries trimmed from the wrong end,
missing IV stored as zero, VIX made fatal, and a snapshot left PARTIAL on success. `collector.py`
was restored from git and confirmed byte-identical afterwards.

### Found and fixed

**BUG-017 — a snapshot could overstate its own coverage.** `db.insert_option_rows()` returns the
count *actually stored*; `INSERT OR IGNORE` silently discards any row failing a constraint, not
just duplicates (ADR-022). The collector discarded that return value and recorded
`strikes_fetched = len(option_rows)` — the count *offered*. A snapshot that lost rows still
reported full coverage.

This was **already known and written down** at `db.py:419-422` when DEBT-008 was analysed; it was
documented as still-optimistic and left. It was not a new discovery — it was a known hole that
survived because nothing failed when it was wrong. Recorded as a failing test first (reported 40
rows against 20 stored), then fixed. The `rows=` figure in the success log now reports the stored
count too.

Why it matters more than a wrong number: the history cannot be re-fetched. A snapshot claiming
3,096 rows while holding 2,000 is a hole in the record that *reads as intact*, and no later check
could tell it from a real one.

### M1.6 closed — the loop decisions are covered

**Extracted four judgements out of `main()`, then tested them — 47 tests
(`tests/test_collector_main.py`).** These were the last of the collector with no checks, and the
reason was structural, not editorial: they were written inline inside `while True`, which no test
can enter — it never returns, it sleeps in real time, and it calls Schwab. Testing *around* the
loop was never going to work, so the loop gave up the decisions instead.

Now pure functions, in the same style as `token_days_remaining()`: `is_auth_error()`,
`failure_is_critical()`, `sleep_after_cycle()`, `should_recheck_token()`. **The extraction changed
no behaviour** — the suite was green before it, after it, and unchanged in count.

**Mutation-verified: 11 injected faults, 11 caught, zero survivors.** The market opening a minute
late, collection running past the 16:00 close, holidays and weekends treated as trading days, OPEN
polled at the slow cadence, auth detection stuck on and stuck off, drift correction removed, a
negative sleep left unclamped, escalation off by one, and the token rechecked every cycle.

The session boundaries are tested at the exact edges rather than at comfortable midpoints. A
boundary written `<` where it should be `<=` is invisible at 11:00 and obvious at 09:30, and that
one minute is the entire failure mode.

**One known imprecision pinned rather than fixed.** `is_auth_error` is substring-based, so a
message containing "expired" — including one about an expired *option* — counts as an auth
failure. Left alone on purpose: a false positive costs one needless re-login and self-corrects,
while a missed auth failure costs every remaining cycle of the session. The test names the
trade-off so that tightening the rule later is a deliberate decision, not an accident.

### Discovered, not done
- **The PARTIAL `error_message` is still optimistic.** Status is decided at step 8, before the
  step-9 write, so its `"{n} option rows written"` text is the offered count. Cosmetic — the
  `strikes_fetched` column, which analysis actually reads, is now correct. Left alone rather than
  reordering the cycle for a log string.
- `ruff` reports 23 findings across the repo under the current config (part of the 85 in
  DEBT-025). The two new test files are clean.

---

## 2026-07-26 (session 4) — the codebase is linted for the first time

### Completed

**`ruff` installed and run.** It was configured in M0 and declared in the dev dependencies, but
never actually installed — so in a 9,628-line codebase nothing had ever been linted. First run:
**435 findings**.

**Tuned the config before acting on it.** ~70% of those were three families that are house style
or false positives, not defects: 147 `dict()`-instead-of-`{}` (nearly all Plotly chart specs,
where `dict(...)` is the library's own idiom), 110 "ambiguous unicode" (the em-dashes and multiply
signs this codebase uses deliberately), 45 multiple-statements-per-line. Silenced in
`pyproject.toml` **with the reasoning written next to each**, which took the report to 133 — a
list someone will actually read. A report nobody reads is worth nothing.

**Auto-fixed 55 mechanical items** — unused import, missing trailing newlines, import ordering,
quoted type annotations, stale `# noqa` directives, `timezone.utc` to `UTC`. **85 remain**, all
judgement calls, logged as DEBT-025.

**No defects found.** Worth stating plainly: nothing ruff surfaced will crash, corrupt data or
produce a wrong number. The value was elsewhere.

### Discovered

**DEBT-007 is now measured rather than estimated.** The backlog said "11x `except: pass`, 4x
bare `except:`". The real figures: **4 bare `except:`** and **~30 blind `except Exception:`** —
18 in `journal.py`, 3 each in `app.py` and `collector.py`, 2 in `db.py` — with exact line
numbers, reproducible via `ruff check --select BLE001,E722`. A vague known-issue became a
worklist.

**A possible unfinished job in the stats panel.** `compute_stats()` computes
`scratch = [p for p in pls if p == 0]` and never uses it. Left over from last session's ADR-021
break-even work. The behaviour is correct — scratches already reach the win-rate denominator and
stay out of Average Loser through other filters — so this is not a bug, but it may be a display
that was intended and never finished. **Left in place; Chandan's call.**

**The auto-fix was not as inert as "mechanical" implies, and the tests caught it.** Ruff's
`timezone.utc` to `UTC` modernisation also dropped `timezone` from `collector.py`'s imports — and
`tests/test_collector_gaps.py` was reaching through the module for it (`collector.timezone.utc`),
so **34 tests broke instantly**. The right fix was the tests, not a revert: reaching into another
module for its imports is fragile coupling, and the test file now imports `UTC` itself. Two
lessons — run the suite after every "safe" auto-fix, and the M1.9 hook earned its keep on the
very first lint pass.

**Ruff deleted two comments that carried reasoning.** `RUF100` strips redundant `# noqa`
directives — correctly, since those rules were not enabled — but it takes the trailing
explanation with them. Lost: *"a broken check must never stop collection"* in `collector.py` and
*"import must follow the sys.path shim above"* in `scripts/check_db.py`. Both restored as plain
comments. Worth knowing before anyone runs `--fix` again: **check the diff for prose, not just
for code.**

### Notes

- 329 tests still passing; `db.py` still 100%.
- **`ruff` is NOT wired into the pre-commit hook**, deliberately. 85 findings remain, and a gate
  that fails on every commit teaches everyone to bypass it. Wire it when DEBT-025 hits zero.
- The formatter (`black` / `ruff format`) was **not** run. ADR-015 still holds: `app.py` and
  `pages/journal.py` are the files it would touch most and they remain largely untested.
- `ruff 0.16.0` installed into the shared venv, which serves other projects too (DEBT-020). It is
  a standalone linter — nothing imports it, so it cannot affect runtime behaviour anywhere.

---

## 2026-07-26 (session 3) — BUG-005 and BUG-002 closed; M1.6 begun (274 → 329 tests)

### Completed

**BUG-005 closed (ADR-024) — gaps are now classified by market minutes missed.** One
measurement, `market_minutes_between()`, replaces three heuristics: sum the overlap with the
09:30–16:00 ET session of every trading day the gap touches. A gap is routine if and only if
nothing was collectable during it. 38 tests, pinned before the fix (ADR-019), mutation-verified
on an isolated copy — 23 of 24 caught, the survivor a proven equivalent mutant.

**BUG-002 closed — the Selected-Strike IV chart no longer draws across holidays.** The backlog
called it a one-line fix; it is two — the call (`cm`) and put (`pm`) frames both needed
`_break_sessions()`, applied *after* the ratio column so the breaker rows carry NaN there too.
17 tests, including a guard that the wiring cannot be silently removed again, because the helper
itself was correct all along and a unit test of it would have stayed green through the bug's
entire life.

**Historical gap rows reclassified.** `migrations/reclassify_collection_gaps.py`, run after a
dry run: 19 of 47 rows corrected to MARKET_CLOSED. Idempotent, refuses to write without a
verified backup, and appends previous values to `notes` rather than overwriting. Integrity
`ok` afterwards; snapshots and option rows untouched.

### Discovered — the write-up was wrong in three ways

Writing the pinning tests first, and reading the caller rather than trusting the backlog, turned
up more than the recorded defect.

**1. The recorded number was wrong.** BUG-005 said a Friday-to-Monday weekend is misfiled at
2,611 minutes. It is 3,931, which trips the crude `> 3,600 minutes` rule and comes out *right by
accident*. The genuinely misfiled weekend is a restart **during** the weekend — which is exactly
what `check_db.py` printed at the start of this session.

**2. The same rules hid real data loss — the direction nobody had recorded.** A collector dead
from Monday lunchtime to Thursday lunchtime loses three trading days and was labelled
MARKET_CLOSED, then **suppressed**: `_check_startup_gap()` returns early on routine verdicts and
writes nothing. The worst data-loss event this system can suffer left no trace. The holiday scan
did the same to any long outage containing a holiday. M3.4 plans liveness alerting on this
classifier — it was blind to its own primary case. That inverts the severity: BUG-005 was filed
as "the alarm is noisy"; it was also deaf.

**3. `_classify_gap()` was not even the main culprit.** It is only reached from
`_check_startup_gap()`, which runs once per collector *start* — and the collector has run
continuously since 2026-07-16. The detector that fires constantly is the mid-session one in
`main()`, which **hardcoded `reason="COLLECTOR_OFFLINE"` and never called the classifier**, and
`prev_snapshot_ts` is not cleared when the market closes. Every trading morning it compared
against 15:59 the previous day and wrote a false row. **A fix aimed only at `_classify_gap()` —
which is what the backlog prescribed — would have left the largest source of bad rows untouched
and looked successful.**

**4. A fourth defect, found only by running the fix against real data.** With the classifier
fixed, 22 of 47 rows were still faults, all ~5 minutes at 15:25–15:31 ET. At 15:30 the cadence
changes from 300s to 60s, so an ordinary MIDDAY interval was judged against the new 60s
threshold (5.0 > 2.5) and recorded as a stall — every trading day, more rows than the overnight
bug produced. The threshold now uses the slower of the two cadences. General lesson: compare a
threshold against the cadence that *produced* the data, not the one in force when the comparison
runs.

**The stored damage figures were nonsense.** `expected_snapshots_lost` totalled **19,759 across
47 rows — more than the database has ever held (2,605)**, because it was computed from wall
clock rather than market time. The truthful total is 145.

**Streamlit binds 0.0.0.0 (OPS-006).** Noticed while smoke-testing the dashboard after the
BUG-002 edit: startup advertises a Network URL and an External URL. It is reachable from the LAN
today, before any Tailscale work. OPS-006 updated from "confirm" to "confirmed, and it does not".

### Notes

- Full suite: **329 passing** (274 before, +55). Suite runs on every commit via the M1.9 hook.
- BUG-002 verified against real data, not only by unit test: the 2026-07-07 7355C contract
  jumps IV 0.1109 → 0.2036 across the 3 July holiday, which was being drawn as a smooth line.
  The dashboard was also started headless to confirm the edited page still loads.
- Open bugs: 11 → 8. BUG-008 was deleted alongside BUG-005 — it was explicitly an instance of
  it ("startup gap logged on every restart") and is fixed by the same change.
- **Not pushed.** `origin/main` is still at `eac2461`.

---

## 2026-07-26 (session 2) — M1.5 + M1.9: `db.py` tested, checks now automatic, 3 bugs fixed (151 → 274 tests)

### Completed

**M1.5 — `db.py` now has 111 tests at 100% statement coverage.** The module every other
component reads through went from zero tests to fully covered. `tests/test_db.py`, plus
`temp_db` / `trades_db` fixtures in `conftest.py` that build a throwaway database under
pytest's `tmp_path` and **assert they did not resolve to the production path** — several
db.py functions default to `config.DB_PATH` when `db_path` is None, so one missing argument
in a future test would have pointed the suite at 1.42 GB of irreplaceable history.

Covered: the read-only guarantee (`PRAGMA query_only`), the foreign-key cascade, transaction
rollback, the one-time dedup migration, the schema-version gate, and every read query's status
filter, ordering, date boundary and N-day window — plus the trades table end to end.

**Mutation-verified, and this time on an ISOLATED COPY of the source.** 26 deliberate faults,
**24 caught**. Previous sessions mutated files in the working tree and restored them; this run
copied `db.py`, `config.py` and the tests into a scratch directory with no `data/` in it, so
the production database was not merely untouched but unreachable, and a crash mid-run could not
have left a mutated `db.py` on disk for the collector to load on its next restart.

**The 2 survivors are equivalent mutants, not holes.** Both were verified against sqlite3
directly rather than assumed:
- *ORDER BY drops the `right` tiebreak* — unobservable, because
  `idx_option_rows_contract_snap(expiry_date, strike, right, snapshot_id)` satisfies the query
  as a covering-index scan that already emits rows in `right` order (EXPLAIN QUERY PLAN).
  The clause still matters if that index is ever dropped — and DEBT-016 shows indexes here
  *do* get dropped for size.
- *`<` → `<=` on the prior-session-close boundary* — no `'YYYY-MM-DD HH:MM:SS'` string can
  ever compare equal to a bare `'YYYY-MM-DD'`, so the two operators are indistinguishable.
  The boundary is correct by accident of the timestamp *format*, not the operator, so a test
  was added pinning that format invariant.

Both are documented in the tests themselves, so a future audit does not mistake them for
untested branches.

**One test was found to be fake and fixed.** `test_update_trade_with_no_fields_is_a_no_op`
passed even with the early return deleted, because `_utcnow()` has one-second resolution and
the test ran inside a single second. It now backdates `updated_at` to a value `now()` cannot
produce. This is exactly the failure the mutation run exists to catch — a test that looked
real and proved nothing.

### Discovered

**DEBT-008 is much worse than recorded, and is now P0 — ADR-022.** ADR-004 described
`INSERT OR IGNORE` as a *logging* shortcoming. It is a **silent data-loss** risk. `OR IGNORE`
is not scoped to uniqueness: SQLite applies it to every constraint on the statement, so a
`CHECK` or `NOT NULL` violation skips the row rather than raising. `insert_option_rows()` then
returns `len(rows)`, computed *before* the statement runs. So if Schwab ever changed its `right`
convention from `'C'` to `'CALL'` — the kind of change an unofficial community wrapper is
prone to, which is why `schwab-py` is pinned tightest — every row would be discarded,
`insert_option_rows()` would return 3,096, `collector.log` would record a healthy cycle, and
`check_db.py` would show COMPLETE snapshots accumulating. The only symptom would be charts
that quietly stop moving, and the missed prices are unrecoverable.

**Three new defects, all pinned rather than fixed** (ADR-019 — record first, fix separately):
- **BUG-016 (P1)** — `get_next_trade_id()` uses `COUNT(*) + 1`, which is not a sequence.
  Delete any non-newest trade and the next ID collides with a surviving PRIMARY KEY, so
  `insert_trade()` raises and the trade being recorded is lost. **Reachable and scheduled:**
  STATUS.md commits to discarding the 6 practice trades before serious trading resumes.
- **BUG-015 (P2)** — `get_spx_intraday_today()` has no upper date bound; asking for an older
  session silently returns that session *and every session after it* as one "intraday" series.
  `app.py` is safe only by accident of always passing the latest snapshot's date.
- **BUG-014 (P3)** — the BUG-007 truthiness-on-floats trap, three more sites in `db.py`.

### Also completed (same session, after review)

**DEBT-008 step 1 fixed — the silent loss is now a log line (ADR-022).**
`insert_option_rows()` returns `cursor.rowcount` (verified to report the true stored count
under `executemany` + `OR IGNORE`) instead of `len(rows)`, and logs a WARNING naming the
shortfall and pointing at DEBT-008/ADR-022. Per ADR-019 the fix landed as a **visible change
to the pinned tests**: the two tests that asserted the dishonest count were rewritten to
assert the honest one and relabelled from PINNED to FIXED, and two new tests cover the
warning firing and staying quiet on a clean write.

Verified load-bearing by reverting both halves on the isolated copy: removing the rowcount
change fails 4 tests, removing the warning fails 1. Full mutation set re-run against the new
`db.py` — **26 of 28 caught**, same two equivalent mutants, no new holes.

**What was deliberately NOT changed.** `OR IGNORE` still discards constraint-violating rows
rather than raising (that is the M3.6 per-constraint decision, ADR-022 step 2), and
`collector.py:565` still records `strikes_fetched = len(option_rows)` — the offered count —
while discarding this function's return value entirely. So the WARNING is currently the only
signal. Correcting the collector's own reporting changes what is written to the `snapshots`
table, which was outside the approved scope. DEBT-008 is therefore **half fixed** and back to
P1, not closed.

**M1.9 done — the checks now run themselves.** `.githooks/pre-commit` (which already existed
from M0.3 as a secrets guard, with `core.hooksPath` already configured) now runs the full
suite whenever a `.py` file is staged. Docs-only commits skip it, so editing the backlog stays
instant. Interpreter resolution falls back through `$VIRTUAL_ENV` → the known venv path →
`PATH`, because the venv lives outside the project and is shared (DEBT-020) and a commit from
a fresh terminal or IDE will not have `VIRTUAL_ENV` set.

**Exercised in all four directions rather than assumed:** skips on a docs-only commit; passes
with `VIRTUAL_ENV` unset; **blocks a deliberately-failing test** (probe file created, hook
confirmed to exit 1, probe deleted); and honours the `SKIP_TESTS=1` bypass. This closes the
"checks only run when someone remembers" gap that M1 exists to remove.

### Also completed (same session, third pass)

**Work committed.** Four commits on `main`: the db.py suite + DEBT-008 fix, the M1.9 hook, the
docs, and then the three bug fixes. The M1.9 hook ran the suite for real on every commit that
touched Python — its first live use was the commit that introduced it.

**All three bugs found this session are fixed — BUG-016, BUG-015, BUG-014 (ADR-023).** Each
was pinned first and fixed as a deliberate rewrite of its pinned test (ADR-019), and each fix
was verified load-bearing by reverting it on the isolated copy: **10 reverts, 10 caught.**

- **BUG-016** — `get_next_trade_id()` now derives from `MAX()` of the numeric part rather than
  `COUNT(*) + 1`. IDs are never reused; a deleted T-003 leaves a permanent gap. The tidier
  alternative (refill the gap) was rejected because the journal is *evidence* — an ID that
  once meant one trade must never later mean another (ADR-023 §1). **This unblocks discarding
  the 6 practice trades**, which previously would have raised on the first real trade after.
- **BUG-015** — `get_spx_intraday_today()` bounded at both ends, with a test proving the upper
  bound does not clip the 19:59 UTC close.
- **BUG-014** — three truthiness-on-float sites. The one that mattered was `get_ic_marks()`,
  where a missing quote became a real-looking `0.00` that flowed into `cost_to_close` and out
  to the unrealized-P&L figure. It now falls back to the bid/ask midpoint as the rest of the
  module always did, and withholds the whole valuation if a leg has no computable mark at all
  (ADR-023 §2). The Journal will occasionally show nothing where it previously showed a
  confident wrong number — the correct trade.

**A test of mine was caught being fake, again.** `test_next_trade_id_ignores_ids_that_are_not_
trade_ids` used `SEED` and `x` as junk IDs; SQLite casts both tails to 0, so they lose the
`MAX()` with or without the `LIKE 'T-_%'` filter — the test appeared to protect the filter and
did not. Mutation-testing the test caught it. Adding `TX999` (whose tail casts to 999) made it
real. Second instance this session of a plausible-looking test proving nothing.

**Backlog cleaned per ADR-017.** BUG-014, BUG-015 and BUG-016 rows deleted, not marked done —
git is the archive, and the reasoning worth keeping went into ADR-023 first. Also corrected two
entries this session's work had made stale: DEBT-012's mark-fallback duplication count (×4
→ ×5, since `get_ic_marks()` now uses it too) and DEBT-001's test count. BUG-007 is now
annotated as the last surviving site of the truthiness-on-floats pattern.

### Notes

- Full suite: **274 passing** (151 before this session, +123). `db.py` at 100%.
- `ruff` is not installed in the shared venv (`No module named ruff`), so the new files were
  not linted. It is declared in the `dev` optional-dependency group but never installed —
  worth folding into M1.9 alongside the automated test run.
- Committed to `main` in four commits (`84c7cb2`, `c57f91a`, `df1f80c`, `725a75b`), plus
  this documentation pass. **Not pushed** — `origin/main` is still at `eac2461`.
- Production code touched: `db.py` only (DEBT-008 reporting, plus the three bug fixes).

---

## 2026-07-26 — M0 merged; M1 Test Foundation begun (0 → 140 tests)

### Completed

**M0 merged to `main` and pushed.** Fast-forward from `bfc78c0` to `f76d2c2`; `main` now at
`c0180e3`. Working tree clean.

**M1.1–1.4 — the test foundation exists.** 140 tests, all passing:
- `iv_engine.py` — 73 tests, 100% statement coverage
- Journal P&L maths — 53 tests
- Scanner golden net — 14 tests over 2 real snapshots

Every suite was **mutation-verified** rather than trusted on its pass count: deliberate faults
were injected and the suites required to catch them. Source files were restored byte-identical
afterwards (confirmed via `git diff --stat`).

**Single-instance lock added to `collector.py`** — an OS file lock, so it cannot go stale.

**BUG-011 and BUG-012 fixed** (ADR-021), pinned first and fixed second per ADR-019. Both
verified load-bearing by reverting them and watching the tests fail.

**Documentation policy changed (ADR-017):** `backlog.md` now holds open items only. Closing an
item deletes its row; git is the archive. `/wrap` enforces it. `progress_log.md` deliberately
unchanged — append-only is correct here.

### Discovered

**The "two collectors" is one collector** (ADR-018). The `python.exe` inside `.venv\Scripts\`
is a `uv` trampoline (241 KB) that re-execs the real interpreter (91 KB, under
`AppData\Roaming\uv\`) as a child process. Confirmed via `Win32_Process` `ParentProcessId` +
`ExecutablePath`; only one launcher exists. **This was a suspected cause of BUG-001 and is not
— BUG-001 stays open with no leads.** It had already cost Chandan and an earlier session real
time; now written down so it costs nobody again.

**Seven defects found by the new tests:** BUG-006, BUG-007 (from `iv_engine`), BUG-009…013
(from the journal maths). Five remain open; the two material ones are fixed.

**A mutation that did NOT fail** — DEBT-014. Altering the bid/ask midpoint formula in the
scanner changed nothing, because although 77 of 3,096 rows use that branch, none reach the
top-50 output. The golden net therefore does not protect it. Recorded rather than glossed over.

### What broke

**Data loss, self-inflicted.** `git checkout main` silently overwrote the live untracked
`eligible_history.json`, `entry_locks.json` and `chart_colors.json` with their last-committed
versions (gitignored files are overwritten without warning), and the subsequent merge deleted
them. Restored from `bfc78c0` — dated **10 July** — so roughly two weeks of accumulated scanner
history and any entry locks placed since are gone. The M0 backup is database-only, so no better
copy existed. `data/dashboard.db` was never at risk. `collector.log` was backed up beforehand
and is intact.

**Lesson:** before any branch switch in this repo, copy the untracked runtime JSON aside. Those
files are gitignored *and* tracked on older commits, which is the exact combination git handles
destructively without prompting.

**Collector stopped and restarted** (with permission) to release a file lock on `collector.log`
that blocked the merge. Markets were closed, so nothing was missed. One restart-gap row was
logged — an instance of BUG-005, recorded as BUG-008.

### Remains

M1.5 `db.py` tests (next — everything reads through it, no coverage), M1.6 `collector.py`,
M1.7 `schwab_client.py`, M1.8 the ~70% target, M1.9 checks that run without being remembered.
DEBT-014. BUG-001 still blocked on the user.

---

## 2026-07-25 — Phase 1 Audit + M0 Stabilization (in progress)

### Completed

**Phase 1 — Engineering Audit** (delivered as `AUDIT_2026-07-25.md`)
Full repository discovery: both large documents, all 9 Python modules (9,628 lines),
config/ops scripts, 30 commits of git history, and direct measurement of the live
1.81 GB production database. Ten-section report delivered and approved.

**M0.1 — Database backup + verified restore** ✅
- Backed up `data/dashboard.db` (1.81 GB) via SQLite's **online backup API**
  (`Connection.backup()`), not a file copy — the collector was running and the DB is in
  WAL mode, where a plain copy can capture a torn state. 15.8 s, no downtime.
- Target: `C:\Users\chand\Python\spx-dashboard-backups\dashboard-20260725-232118.db`,
  deliberately **outside** the repo so a `.gitignore` regression can never commit it.
- Restore verified: `integrity_check` = ok, `foreign_key_check` clean, all 9 tables
  row-for-row identical, and `option_rows` checksums matching to the cent
  (`sum(mark)` = 712,596,858.75) and to 4 dp on IV.

**M0.2 — `.gitignore` fixed; runtime state untracked** ✅
- Rewrote `.gitignore` as clean UTF-8/LF (3,439 bytes, 0 null bytes) with an explicit
  warning comment about PowerShell's encoding default.
- Untracked (kept on disk, verified byte-identical afterwards): `collector.log` (486 KB),
  `eligible_history.json` (599 KB), `entry_locks.json`, `chart_colors.json`,
  `data/demo_dashboard.db`, `Project Reboot & Engineering Audit.docx`.

**M0.4 — Orphans and superseded documents removed** ✅
- Deleted: `dashboard.db` (0-byte root orphan), `pinned_pairs.json` (feature removed in
  v3.3), `AUDIT_REPORT_2026-06-25.md`, `spx_dashboard_implementation_plan.md`.
- Cleared the dirty index (`AFTER_AUDIT.md`, `TO_DO.md` — staged-added, absent from disk
  and from HEAD; content was never committed).

**M0.5 / 0.6 / 0.7 (partial)** 🔄
- `pyproject.toml` created: all runtime deps pinned **with upper bounds**, dev deps added
  (pytest, pytest-cov, ruff, black, mypy, pip-audit), plus ruff/black/mypy/pytest config.
- `.env.example` created, documenting the two removed settings so they don't get re-added.

**M0.8 / 0.9 — Context files established** ✅
- `plan.md`, `progress_log.md`, `decisions.md`, `backlog.md` created.
- `decisions.md` backfilled with **16 ADRs** — 12 reconstructed from `DEV_JOURNAL.md`
  (including ADR-001, the IV-ratio retraction) and 4 new from this session.

### Discoveries

1. **The database is 1.81 GB after 31 days** — 7,816,508 `option_rows`, 2,608 snapshots,
   ~82 MB per trading day, on track for ~20 GB/year with no retention policy.

2. **Indexes are 44% of the database** (790 MB on `option_rows` alone), and two are
   provably redundant — each is a strict left-prefix of another index. ~318 MB recoverable,
   plus reduced write cost on every one of ~3,000 inserts per cycle.

3. **`.gitignore` was corrupted with UTF-16LE bytes** appended to a UTF-8 file — a
   PowerShell `Add-Content`/`Out-File` accident. The intended `pinned_pairs.json` entry
   was stored null-separated and matched nothing, so five runtime-state files had been
   tracked for weeks. This is the mechanism behind the "drift between local file and
   discussed state" that the 2026-07-07 journal names as a root cause of that session's churn.

4. **`transform_credit()` and `calendar_edge()` are never called** — yet
   `DOCUMENTATION.md` §6.5 documents the former as "the correct profitability metric."
   The app computes an equivalent inline instead. The declared source of truth describes
   functions the application does not use.

5. **The `$5.00` transform threshold — the single most important business rule — is
   duplicated four times as bare literals** and is absent from `config.py`, while §9.1
   plans to recalibrate it to $6.50–7.00. That recalibration would currently half-apply.

6. **Retention pruning would silently break Regime Analysis** (→ ADR-016). This was the
   most valuable finding of the session and nearly got built wrong:
   `get_entry_iv_context()` reconstructs entry-time term structure from historical
   `option_rows` *retroactively*, so pruning by expiry would destroy the validation
   mechanism for exactly the completed trades it exists to study. Must be resolved before
   pruning runs once — it is irreversible.

7. **Dependency drift is worse than the manifest suggests** — running pandas 3.0.3 against
   a declared `>=2.2.0`, and plotly 6.8.0 against `>=5.20.0`. Two full major versions of
   silent drift.

8. **The "duplicate collector" is not duplicated** — verified via `ExecutablePath` per the
   journal's own Windows lesson: PID 16268 is the `.venv` shim, PID 17580 is its child
   (uv-managed CPython), spawned 2 s apart. One collector.

9. **Collector health confirmed** — up since 2026-07-16, last snapshot Friday 15:59 ET,
   and the three prior sessions each captured a full 126–127 snapshots (09:30–15:59 ET).
   Correctly asleep on a Saturday, not dead.

10. **Commit `bfc78c0` "V4.2 added" contains no source changes at all** — only journal
    text, log noise, and 1,832 lines of generated JSON. Version labels are decoupled from
    reality; there are no tags and no VERSION file.

### Bugs found
None introduced. One pre-existing issue surfaced: `git rm --cached` aborted wholesale on
the first attempt because `collector.log` had staged content differing from both the
working file and HEAD (a partial stage left over from an earlier session). Resolved with
`-f`, which with `--cached` touches only the index and never the disk file — verified after.

### Completed (continued — second half of session)

**M0.10 — Database cleanup** ✅
- **Rehearsed the entire destructive change on a clone of the backup first**, which is
  what made it safe to approve: `EXPLAIN QUERY PLAN` confirmed every hot query still
  resolves via an index after the drops (snapshot lookups correctly fall through to
  `uq_option_rows_contract`, whose leading column is `snapshot_id`), and measured
  timings were unchanged (7/24/13 ms before → 7/24/12 ms after).
- Executed on the live DB: dropped `idx_option_rows_contract`,
  `idx_option_rows_snapshot_id`, and tables `expiry_snapshots`, `strike_snapshots`,
  `positions`; then `VACUUM`. **1.810 GB → 1.423 GB (387 MB, 21.4%)**, ~48 s total.
- Verified: all five preserved tables row-identical, `integrity_check` = ok.
- **Critical follow-up caught:** `db.py`'s `_DDL` would have recreated both indexes on
  the collector's very next `init_db()` call, silently undoing the work. Removed them
  from the DDL with a comment explaining the prefix-redundancy analysis, and verified
  by running `init_db()` and confirming they stayed dropped.

**M0.11 — Dead code removal** ✅
- Removed from `iv_engine.py`: `iv_regime`, `mean_reversion_estimate` +
  `ReversionEstimate`, `trade_quality_score`, `expected_move_log_check` +
  `ExpectedMoveCheck`; plus the now-unused `numpy` and `config` imports.
- Removed from `db.py`: `get_term_structure`, `get_iv_spread_history`, `get_snapshots`,
  `get_all_expiry_atm_iv_today`, `update_snapshot_notes`, and the code-generator
  artifact.
- Deleted `demo_data.py`, `data/demo_dashboard.db`, `config.DEMO_MODE`, `DEMO_DB_PATH`.
- **Retained deliberately:** `transform_credit()`, `calendar_edge()` (M2.2 wiring
  targets — `app.py` duplicates their logic inline, so deleting them would mean
  rebuilding them in three weeks) and `get_gaps()` (OPS-005).

**M0.3 / 0.12 / 0.13 / 0.14** ✅ / 🔄
- `.githooks/pre-commit` installed and tested both directions.
- `collector.py` logging → `RotatingFileHandler` (1 MB × 5), module-relative path.
- `start_collector.bat` rewritten `%~dp0`-relative with venv auto-discovery.
- `CHANGELOG.md` created, history reconstructed back to v0.1.

### Bugs

**Introduced and fixed within the session:** removing the code-generator artifact from
`db.py` also removed the adjacent `import json as _json`, while `seed_t001` still used
`_json` — and `seed_t001` *is* called from `pages/journal.py:51`. It compiled cleanly and
would have failed at runtime. Caught by an AST-based cross-module reference check, then
fixed by hoisting `import json` to the module top. **This is exactly the defect class M1
exists to catch automatically, and it took a purpose-built check to find manually.**

**Pre-existing, discovered (BUG-005, P1):** `_classify_gap()` misclassifies every routine
overnight and weekend gap as `COLLECTOR_OFFLINE`. **All 46 rows in `collection_gaps` carry
that reason; the classifier has never once produced `MARKET_CLOSED` or `HOLIDAY`.** At
least 19 are plainly routine (15:59 → 09:30 next morning). Two off-by-ones against the
collector's own window at `collector.py:182-186`: `after_close` tests `>= 16:00` but the
last write of the day lands at 15:59, and `before_open` tests `< 09:30` but the collector
restarts at 09:30–09:31. Both False ⇒ falls through to `COLLECTOR_OFFLINE`. Blocks M3.4
liveness alerting and would make OPS-005 render 46 false alarms. **Not fixed — outside
M0's no-behaviour-change scope; awaiting a decision.**

**Disclosure:** running `python collector.py --once` to verify the collector still starts
wrote one spurious `collection_gaps` row (the 46th). Harmless, and it is what surfaced
BUG-005, but it was an unintended write.

### Remaining work (this milestone)
- Commit the work and tag `v4.2` (nothing is committed yet).
- Register the collector scheduled task — needs approval (system change).
- Decide whether to fix BUG-005 now or at M3.
- **Deferred by decision:** repo-wide formatter run until the M1 suite exists (ADR-015);
  dashboard logging until M2.13.

### Session close (2026-07-26)

- **M0 complete.** 9 commits on `m0-stabilize-and-clean`, tagged `v4.2`. Dashboard opened
  and verified by the user after all changes — all six tabs render, no issues.
- **Collector auto-start finding corrected.** The audit's OPS-001 ("depends on manual
  start") was wrong: a Startup-folder shortcut has run it since 2026-06-22. Task Scheduler
  registration was attempted and failed with Access Denied (ONLOGON triggers require
  elevation) — which is exactly what `DEV_JOURNAL.md` 2026-06-22 documented. OPS-001 closed,
  OPS-001b opened for the real residual gap (no crash recovery).
- **Repo organised** into `docs/`, `scripts/`, `migrations/` via `git mv`. Source modules
  deliberately left flat until M2, gated behind M1.
- **Session commands added.** `STATUS.md` (repo root, max 100 lines, plain language,
  self-contained) plus `/STARTUP` and `/wrap` in `.claude/commands/`. Purpose is token
  efficiency: a new session reads `STATUS.md` alone and can begin work without opening any
  other file.

### Notes
- All changes are **staged/unstaged, not committed** — awaiting instruction on commit
  granularity.
- Source changes were confined to dead-code removal, the DDL index-policy fix, logging
  configuration, and the `json` import repair. **No trading math, collector polling
  logic, scanner ranking, or P&L rule was touched.**
- Verification performed: all 8 modules compile; AST cross-module reference check passes;
  end-to-end smoke test against real data exercises `atm_iv`, `term_structure`,
  `interpret_curve`, `strike_contract`, `atm_straddle_price`, `normalized_debit`,
  `theta_differential`, `liquidity_score`, and four `db` readers; collector starts clean.
