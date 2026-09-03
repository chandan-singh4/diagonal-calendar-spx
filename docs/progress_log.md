# progress_log.md — Chronological Development Log

Newest first. Every session appends an entry: what was completed, what was discovered,
what broke, and what remains.

----

## 2026-09-03 (session 14) — three items closed by reading, the closing price was never recorded, and an audit that found a bug on its first run

**The session's task list was the four items session 13 left behind. Three of them turned out
not to need the work they were written for**, and finding that out took less time than the work
would have.

**1. The collector did not need restarting.** STATUS said the running copy predated the 19 August
fix, so the p.m. contract's daily volatility line had stopped growing. Before stopping a process
that was collecting live prices, the claim was checked against the database: `atm_iv_by_expiry`
grouped by capture day and settlement shows **both AM and PM rows every single day from 20
August** — 126 and 2,520 yesterday — and unlabelled `None` rows only on 19 August and earlier.
The process itself started 26 August. It had already been restarted, twice over. Restarting
mid-session would have cost a real ~2-minute hole in today's prices to achieve nothing, so it
was left running. **This is the second consecutive session in which the written record was wrong
and the database was right**, which is now recorded in STATUS's working rules.

**2. M3.8 done — but the half that was missing was not the half the task named.** 3.8 reads
"streamline Schwab token re-auth; document the runbook". The streamlining already existed:
`scripts/reauth.py` moves the old token aside, runs the flow, and **restores it on abort or
failure** — the safety net that makes the chore safe to start. What did not exist was any
mention of it: **`docs/` and `README.md` between them contained zero references to the script**,
so the only way to know it was there was to have written it. That is exactly the failure 3.8
exists to prevent, and it makes the point that a tool nobody can find has not shipped.
`docs/RUNBOOK_REAUTH.md` is the runbook: the three independent ways you learn it is due (banner
from day 6, watchdog pop-up and email, `--check`), the seven steps, the failure modes, and the
`get_client()` trap recorded as a thing never to do. **Nothing was streamlined further** — the
7-day clock is Schwab's and the browser login is a deliberate security boundary, so "streamline"
can only mean *safe and documented*, never *automatic*.

**3. The watchdog's alarm path had already been proven — by a real outage nobody wrote down.**
Chandan remembered getting the pop-up and the email on both a stop and a recovery, and the
record confirms it: `collector.log` has four consecutive cycle failures from 12:30 ET on 19
August (a pandas logical-ops error), `watchdog_state.json` has `last_alert_utc` 8 minutes later
— one watchdog cycle — and `alarming: false` for the recovery. **The M3.4 caveat had been
discharged on the day it was written and the fact never reached a file.**

Detection was staged anyway, because the caveat's *premise* was wrong. It said staging an outage
needs the collector stopped or the database altered. **Both `DB_PATH` and `STATE_DIR` are
environment-overridable**, so a throwaway database holding one genuine `snapshots` row
timestamped three hours back reproduced it in complete isolation — nothing real touched. The
real `check()` returned "🚨 No prices for 3h 0m — collection has stopped", named the MIDDAY
session and its 12m 30s limit, and `should_alert()` decided to send; a control run against the
live database in the same breath returned ✅ and would have sent nothing. **The thing that
"could not be staged" took ten minutes and found a bug.**

**BUG-029, found by that rehearsal.** `watchdog.py` prints its headline — which starts with an
emoji — *before* it reaches the alerting block. On Windows a redirected stdout defaults to
cp1252, so that print raises `UnicodeEncodeError` and the process dies at exit 1: **detection
succeeded, no alert sent, and the wreckage looks like the watchdog itself being broken.** The
live alarm is unaffected and always has been (`register_watchdog_task.ps1` redirects nothing,
and the state file shows healthy checks throughout), so this is logged rather than rushed. But
an alarm that dies on its own console output is the one failure mode a watchdog cannot have.

**4. BUG-027 closed — the one item that was real work (ADR-048).** The a.m. third-Friday
contract settles on the opening print but was being carried until 4:15 PM like everything else,
about a session too long. It was left open in session 13 on purpose: `is_expired` is the only
rule in the program whose answer **deletes** a record (ADR-039), and every accurate alternative
deletes *earlier*. **Chandan chose the opening print, 9:30 New York**, over the contract's true
last trade the evening before — the later of the two correct answers, because being late costs a
stale row in a popover and being early destroys the entry price a live position is measured
against.

`MARKET_OPEN` joins `MARKET_CLOSE` in `core/expiry.py` and `contract.is_am` picks between them.
**The pinning test was replaced, not made to pass**: it recorded BUG-027 as a deliberate
inaccuracy awaiting exactly this decision, so rule and pin moved together — the only circumstance
in which a pin may be rewritten. **Proved by sabotage twice:** reverting to a single 4:15 cutoff
fails 2 tests; applying 9:30 to *every* contract fails 6, which is the mistake that matters,
since p.m. is ~94% of expiries and that slip would delete nearly every marker at half past nine.
**Verified on the live system as well as in tests** — `entry_locks.json` holds one lock, on a
bare p.m. key already expired under both rules, so **nothing is deleted today**; the change first
bites on 18 September.

**5. The closing price was never being recorded, on any day, since 23 June (ADR-049).** Chandan
raised it: "we only collect until 3:59 pm, why not add one more minute and get the final close
price." He was exactly right, and the query said so before anything changed — the last snapshot
of each of the last ten trading days is 15:59:50, 15:59:52, 15:59:53, 15:59:14. The window ran
09:30–16:00 with the end excluded, so **every "close" in the record is a quote from up to a
minute earlier.**

**Two minutes, not the one he asked for.** SPX is a cash index struck from its components'
closing auction prints, and those arrive over the seconds *after* the bell. A poll at 16:00
would very likely still carry the 15:59:59 level and record a close that is not the close —
worse than recording nothing, because it looks right. Sabotaging the constant to 16:01 fails 15
tests, one of which exists to say precisely this.

**Still not 16:15**, where the options actually stop trading: SPX freezes at 16:00, so IVs
computed later use a stale underlying while option marks keep moving. That original reasoning
was untouched — only its boundary was wrong. Sabotage to 16:15 fails 24 tests; back to 16:00,
19.

**No schema change was needed and that was the point.** `snapshots.market_session` carries a
CHECK constraint over three values, so a fourth would have meant rebuilding a table inside the
live 3.55 GB database — to buy a fact the timestamp already carries. The two polls past the bell
are the only ones taken against a frozen underlying and "at or after 16:00" identifies them.

**The subtle part was the gap classifier.** A collectable day is now 392 minutes, not 390. The
3.0-minute routine-gap tolerance is unchanged and still correct, because it budgets ~1.0 minute
at each end and the last write simply moved from 15:59:xx to 16:01:xx — both numbers moved with
the window. **Widening the window without moving the expected last write with it would have made
every ordinary night look like a fault**, which is BUG-005's crying wolf reintroduced from the
opposite direction. That property now has its own test.

**Cost:** two extra polls a day, 126 snapshots becoming 128, ~1.3 MB against ~82 MB.

**Restarted 12:12 ET, at Chandan's word, and it cost nothing.** He asked for it to be slipped
into the five-minute MIDDAY gap. Timed off the cycle: a poll had landed 59 seconds earlier, so
the process was idle in `sleep` rather than mid-write, and there were ~240 seconds of headroom.
The new snapshot landed at 12:12:18, **83 seconds after the previous one** — the restart gained
a poll rather than losing one, `collection_gaps` correctly recorded nothing, and the watchdog
reported healthy on the next check. **ADR-049 is still unproven in the wild** until the 16:00
and 16:01 polls are seen landing today.

**6. BUG-029 fixed.** The watchdog now reconfigures both its streams to UTF-8 at the top of
`main()`, and every print goes through a `_say()` that falls back to ASCII-with-replacements if
even that fails. A silent watchdog beats a dead one: the alert matters, the emoji does not. Proved
by driving it through a cp1252 stream and a stream that raises on every write. The same UTF-8
shim now exists in two places, logged as DEBT-039.

**7. M3.7 done — `scripts/audit.py`, and it found a bug on its first run.** The audit asks a
question no unit test can: not "does the code work" but **"is the record actually complete?"**
Every test in the suite passed throughout ADR-046, ADR-048 and ADR-049 — three separate cases of
data never being captured — because tests check what the code is *believed* to do. **Read-only by
construction**: the connection is opened `?mode=ro`, and a test asserts that a `delete` against it
raises. The daily expectation is **derived** by walking `core.session` rather than written down,
which is why ADR-049's window change carried through with no edit; a test shrinks the window and
watches the expectation follow. Every check is proved in both directions, and the negative half is
the half that matters — a short day the collector already recorded a gap for is reported as a
*note*, not an alarm, because an audit that cries wolf gets skimmed within a week (ADR-045's
lesson, arriving from a new direction).

**8. BUG-030 — the broker's "no value" marker, stored as data for ten weeks.** The audit's IV
sanity check found it immediately: **5,127 rows in `option_rows` hold `iv = -9.99`**, and every
single non-positive IV in the entire 18.7M-row table is exactly that value. Not noise — a
sentinel. Schwab sends **-999.0** when it has nothing to give, and the collector's ÷100 turned it
into a volatility of -999%.

**The first backlog entry under-reported it, and reading the record for the fix is what corrected
that.** The audit only inspects IV, so IV is all the entry named. **Each of `delta`, `gamma`,
`theta` and `vega` also has 5,081 rows at raw -999.0** — the greeks are not divided, so they kept
the marker's original shape and no derived column made them conspicuous. They arrive almost
entirely at 09:30:xx on the longer-dated expiries: at the bell those contracts have not traded, so
the broker has quotes but nothing to compute from.

**The trap is the other direction, and it is why the comparison is exact equality.** `-9.99` is a
perfectly ordinary theta — an option losing $9.99 a day — and **38 rows in the real record
legitimately hold it**. A tolerance band, or a plausible-looking `< -100` rule, would have deleted
real prices while tidying up a sentinel. `SCHWAB_NO_VALUE` and `_value_or_none()` now blank the
marker on exactly the five fields Schwab sends it for; `bid`/`ask`/`last` are deliberately left
alone, being quotes the broker either answers or omits. **Sabotaged three ways** — neutered to a
passthrough (6 failures), widened to a `< -100` band (1, and it is the -998.9 boundary test that
catches it), and blanking everything (5).

**Two halves remain, both Chandan's call.** The collector is **still running the old parser**, so
this resumes at 09:30 tomorrow until it is restarted; and repairing the ~5,100 existing rows is a
write to the live 3.55 GB record. The rows are identifiable exactly and the value was never
information, but neither happens without his word.

**9. The 5,127 poisoned rows were repaired, at Chandan's word.** He asked for it directly, so the
only questions left were *how carefully* and *how much of the record it could disturb*.

**Backed up first** — `dashboard.db.2026-09-03-pre-bug030`, 3.50 GB via `VACUUM INTO`, and the
backup was opened and `quick_check`ed rather than merely being a file of about the right size.

**The repair was split in two, and that split is the interesting part.** The obvious form is one
`UPDATE ... WHERE iv = -9.99 OR ...`, and it would have been wrong: that statement holds the write
lock for its entire 18.7M-row scan, ~50 seconds, and **`db.py:227` gives the collector a 15-second
timeout**. A live poll landing in that window would have failed, and the repair would have punched
a hole in the very record it was tidying. Instead the scan ran on a **read-only** connection, which
under WAL takes no write lock at all, and only the ids came back; the write was then `where id in
(...)` against the primary key. **It committed in 0.1 seconds**, with 70 seconds still to spare
before the next poll.

`iv_spread_to_front` and `iv_ratio_to_front` went to NULL with the values they were derived from —
they read -10.18 and a ratio of **-52**, which is not a number anyone should ever see, and keeping
them would have left the corruption in its most misleading form.

**Verified against the live record afterwards, in both directions:** zero marked rows in either
table, **the 38 legitimate `theta = -9.99` rows untouched**, no non-positive IV anywhere, no gap
row, the next poll COMPLETE, and the audit that found the bug this morning now reports **3 findings,
0 needing attention** — the other three are the known ADR-046/049 history it correctly files as
history.

**BUG-030 is closed.** The collector was restarted at **16:35 ET, after the close**, so it cost
nothing at all — and there was no reason to do it earlier: every poisoned value in ten weeks arrived
at the 09:30 poll, so the fix could not have mattered before tomorrow's open, while restarting
during the session would have put today's first-ever closing price at risk for no gain. The new
process (PID 26320, 16:35:56) started well after `schwab_client.py` was last written (14:16), which
is what makes it the fixed parser. **The proof proper arrives at 09:30 tomorrow**, when the audit's
IV check should stay silent on a live morning for the first time.

**10. ADR-049 is proven in the wild — the close was captured.** Today's record ends
**16:00:18 and 16:01:18 ET**, both COMPLETE, where the previous five sessions ended 15:59:12,
15:59:14, 15:59:50, 15:59:52 and 15:59:53. And the two minutes were not free: SPX read **7745.20**
at the 15:59 poll and **7747.59** at 16:01. **The old "close" was wrong by 2.39 points**, every day,
for 51 trading days — small, consistent, and exactly the kind of error that never announces itself.
The 16:00 and 16:01 readings agree to a hundredth (7747.60, 7747.59), which is what a settled print
looks like.

Today recorded 127 snapshots rather than 128, and that is the 12:12 restart, not a fault: the
cadence shifted from :x0:55 to :x2:17, losing one five-minute slot across the afternoon. No gap row,
no incomplete snapshot, and 127/128 is nowhere near the audit's short-day threshold.

**11. BUG-031 was raised and withdrawn the same day, and the mistake is worth more than the bug.**
Needing a way to restart the collector, I checked Task Scheduler, found `SPX Collector Watchdog` and
no `SPX Diagonal Collector`, and concluded the collector does not start with Windows — contradicting
`STATUS.md`, which I then "corrected". **Chandan said he had watched it start after a reboot, and he
was right.** It starts from a **Startup-folder shortcut** created 22 June, the day before collection
began, enabled in Task Manager, targeting `.venv\Scripts\python.exe collector.py` — **the exact
command line of the process that was running this morning**, which I had already seen and did not
follow up.

**The error is the same shape as the one M3.7 exists to prevent: a check that looked in one place,
found nothing, and was read as proof of absence.** Task Scheduler is one of at least four ways
Windows starts a program. Worse, the written record was right and the reasoning that overruled it
was "the record has been wrong three times before" — a prior turned into a conclusion. The rule in
STATUS is *read the database when the two disagree*, not *assume the file is wrong*.

What survives is small and is logged as **DEBT-040**, not a bug: `scripts/register_collector_task.ps1`
registers a scheduled task that does not exist and is not used, so the repo documents a mechanism the
machine does not run — which is what made the wrong conclusion so easy to reach. Startup works today
and nothing needs doing urgently.

**876 → 925 checks pass.** Sessions 13-14 work is committed; the 3.7/BUG-029/BUG-030 half is not
yet pushed.

----

## 2026-08-19 (session 13) — the third-Friday p.m. option was being thrown away, every cycle, since day one

**Chandan noticed the dashboard only showed one of the two third-Friday expirations.** He had
it slightly the other way round — the screen shows the **a.m.** contract and the **p.m.** one
was never stored — but the substance was exactly right, and it turned out to be the explanation
for a puzzle that had been sitting in "What to do next" for two weeks.

**What was happening.** SPX lists two options for each third Friday: the traditional monthly,
settling at the OPENING price and closing for trading the evening before, and the weekly SPXW,
trading all day and settling at the CLOSE. Schwab returns both under a single expiry key.
`chain_to_dataframe` threw away the contract symbol — the only field that tells them apart —
and the uniqueness rule had no room for the difference, so `INSERT OR IGNORE` silently dropped
the second one.

**The "160 of 3,156 discarded" mystery is solved, and it was the same thing.** 2,181 warnings
in `collector.log`, and **every single one reads exactly 160** — never any other number.
160 = 80 calls + 80 puts = precisely one expiry. A number that steady was never chance.

**The worse half, which nobody was looking for.** On ordinary days the stored row was the a.m.
contract. On the expiry day *itself* the a.m. option had already settled and dropped out of the
broker's chain — so the p.m. contract quietly took the slot, under the same date, with no
marker. Measured on the 17 July monthly: total call open interest climbed 148,989 → 266,366
through the month, then read **99,194** on expiry day. Open interest cannot fall by two-thirds
overnight. That is not the same option. Logged as BUG-024; those rows are not back-fillable.

**What was built.** A `settlement` column (`AM` / `PM` / NULL), read from Schwab's own
`settlementType` with the `SPXW` root as fallback; uniqueness widened to span it; and — the
part that mattered most — **every existing read pinned to one contract per strike: the a.m.
one where an a.m. one exists, otherwise the p.m. one.** ADR-046 has the reasoning.

**Why pinning the readers was not optional.** `atm_iv_by_expiry` holds one row per expiry per
snapshot and has **no uniqueness constraint**. Storing the p.m. contract without pinning would
have written two conflicting rows for every third Friday and silently corrupted the term
structure the entire analytics layer sits on. Shipping the storage fix alone would have been a
worse bug than the one being fixed.

**Two bugs of my own reached the live system, and only checking after deployment caught them.**
The first: the migration drops the old uniqueness index, but the legacy clean-up block still
tested for that index by name to decide whether it had already run. On the collector restart it
therefore ran again and **rebuilt the superseded index**, which rejected every p.m. row — and
its DELETE groups rows without regard to settlement, so one more restart would have deleted p.m.
data already collected (BUG-025). The second: the reader guard was written as
`settlement IS NOT 'PM'`, on the assumption that p.m. was the extra contract. It is the reverse
— nearly every SPX expiry is p.m.-settled, and a.m. exists only on the monthly — so the guard
hid ~94% of the chain. The collector logged `ATM IV computed for 1/20 expiries` for three
cycles (BUG-026). Both are fixed, both now have checks that were **proved by breaking a copy of
the code and watching them fail**, and the total is 819 passing.

**What both had in common: the checks were written against what I believed, not against what the
data says.** Every check passed while the live system was wrong. The one that found the truth
was the boring one — read the database back after deploying and count the rows. That is now the
habit worth keeping, not a better test.

**NULL means "not recorded", not "a.m."** Stamping the old rows `AM` was considered and
rejected: it is wrong on precisely the day that matters most, for the reason above.

**Two real faults found while building, both from the checks rather than from reading.**
`_DDL` created the new UNIQUE index *before* the deduplication migration ran, which would have
crashed `init_db` on exactly the legacy databases the migration exists to repair — the index is
now created in `init_db()` afterwards. And indexing the bare column would have stopped
deduplicating the legacy rows, because SQLite treats every NULL in a UNIQUE index as distinct;
`COALESCE(settlement, '?')` is what prevents that.

**Rehearsed on a copy, per the project rule.** A consistent read-only copy of the real 2.7 GB
file via SQLite's backup API — not a file copy, since the collector is writing. Migration:
**36.2 s, 14,305,769 rows before and after, `PRAGMA integrity_check` ok.** 806 checks pass
(788 + 18 new). **The live database has not been touched.**

**Deployment order is load-bearing.** Only `collector.py` calls `init_db()`, so the collector
must restart — running the migration — before the dashboard serves the new code. Dashboard
first against an unmigrated file gives `OperationalError: no such column: settlement`.

**Left deliberately undone:** the p.m. prices are now recorded but still cannot be *seen*.
How to show them — toggle, second row, separate expiry entry — is a design decision for
Chandan, not one to make silently while fixing collection (BUG-023).

---

## 2026-08-09 (session 12) — M3 begun: retention policy decided, entry-IV gate built, pruner shipped, watchdog live

### Completed

**M3.1 — the retention policy is decided and written down (ADR-044).** Chandan chose **90 days
past expiry** and **manual invocation** from the alternatives, with the measured tradeoffs in
front of him. `option_rows` is the only prunable table; `atm_iv_by_expiry`, `snapshots` and
`collection_gaps` are kept forever. Expiries used by a trade are exempt at any age.

**The entry-IV gate is built — this was the precondition on all pruning (ADR-016).**
`get_entry_iv_context()` answered "what was the term structure when I opened this?" by reading
historical `option_rows`. Pruning those made the question permanently unanswerable, and
*silently*: Regime Analysis would simply plot fewer trades each month with nothing on screen
saying why. Eight `entry_*` columns now carry the answer on the trade row, written by
`insert_trade`/`update_trade` rather than by the call site, so it cannot be forgotten. The old
reconstruction survives as the fallback for pre-M3 rows.

**M3.2 — `scripts/prune.py`, with three gates in front of the delete.** Reporting is the default;
`--execute` is a flag. `--execute` still refuses without a backup newer than the database. Past
that it asks for the row count *in figures* — a y/n prompt gets answered by reflex, a number has
to be read off the report. Closed stdin cancels, which is exactly the unattended case.

**41 new checks (693 → 740), and `render_check.py` clean on all six tabs.**

### Discovered

**The 90-day policy reclaims nothing until roughly November, and this was worth measuring.**
Collection began 2026-06-23, so on the day the policy was written the oldest expiry was 47 days
past. **A 90-day rule deletes zero rows today.** The policy is still right — it has to exist
before data ages into it — but M3.2 merging does not mean growth is solved. It arms a mechanism
that fires later. Near-term relief is a separate decision (downsampling, audit §5.8c),
deliberately not bundled into ADR-044.

**Pruning is worth more than the audit estimated.** `dbstat` on the live database: `option_rows`
1,399.6 MB, its two indexes another 636.4 MB. A deleted row reclaims **~2.2× its own bytes**
because both indexes carry every row. `atm_iv_by_expiry` is 5.3 MB for 47 days — 0.26% of the
database — so keeping the summaries forever is genuinely free.

**Rehearsed against the real data with `--today 2026-12-01`:** 42 expiries / 9,901,390 rows
(85.8%) would go, and **8 expiries / 1,589,912 rows were held back for the 6 practice trades** —
the protection rule working on real inputs, not just in a fixture.

**A lint rule wanted a change that would have broken the edit path.** Ruff's SIM118 flagged
`k in current.keys()` and suggested `k in current`. On a `sqlite3.Row`, `in` tests the row's
**values**, not its column names — verified in a REPL, not assumed. Taking the suggestion makes
`'entry_date' in row` False on a row that has one, blanking the stored context on every edit,
silently. Kept with a `noqa` and the reasoning inline;
`test_editing_the_entry_time_recomputes_the_stored_context` fails if anyone takes it later.

**The collector was blind and STATUS did not know.** The Schwab token had expired ~10 hours before
the session started; `render_check.py` reported it on every tab. No data was lost — markets were
shut all weekend — but Monday's open would have recorded nothing. Chandan re-authed. STATUS was
also stale on its first instruction: last session's two commits were already on `origin/main`.

### What broke

**Two tests passed vacuously on first writing and were rewritten.** `test_execute_is_all_or_nothing`
exercised `managed_conn` and never called `execute_prune` — replaced with a `BEFORE DELETE` trigger
that aborts on the second expiry, so the rollback is genuinely tested. `test_a_comma_formatted_count`
typed `"5"`, which contains no comma — it now seeds 1,500 rows and types `"1,500"`.

**`git checkout -- db.py` destroyed an hour of uncommitted work.** Used to undo a deliberate
sabotage that was proving a test could fail; it reverted the real changes in the same file too.
The sabotage had already done its job. Later verification used a file copy in a scratch directory
instead, and that is the pattern to use: **the project rule says rehearse on a copy, and `git
checkout` is not a copy — it is the opposite.**

### Also completed — M3.4: the collector watchdog (ADR-045)

**Why it could not be a better banner.** The dashboard's red TOKEN EXPIRED banner
(`ui/header.py::render_token_banner`, M1) was working perfectly this morning. Nobody had the
dashboard open. **An alarm that can only reach you through a page you have to open is not an
alarm** — so the monitoring had to leave Streamlit entirely.

`scripts/watchdog.py`, run every 10 minutes all day every day by Task Scheduler
(`scripts/register_watchdog_task.ps1`), alerting by desktop toast **and** email. It observes only:
no restart, no re-auth, no database write. Chandan chose the pop-up-plus-email combination
knowing it means storing an email password locally and sending a message off the machine.

**Most of the work was in not crying wolf.** Four ways a naive version fires when nothing is
wrong: overnight/weekends/holidays; at 09:31 before the first cycle has landed; in the moment
before every scheduled poll, when the age legitimately *reaches* the interval; and six times an
hour during a single outage. Handled by `core.session` returning None for a shut market,
`WATCHDOG_OPEN_GRACE_MINUTES`, `WATCHDOG_LATE_MULTIPLE = 2.5` and `WATCHDOG_REALERT_MINUTES = 60`.
A muted alarm is worse than none, because you believe you are covered.

**`core/session.py` extracted.** Chandan's stated dashboard thresholds are not a second policy
that happens to agree with the collector's poll intervals — **they are those intervals.** Two
copies of a number that must agree eventually disagree. `is_trading_day`, `session_of` and
`expected_interval` now live in pure `core/`, handed a holiday set because `core/` may not import
`config`; `collector.py` delegates. Proved by sabotaging a boundary on a copy and watching **both**
`tests/test_session.py` and the pre-existing collector test fail — that is the evidence the
collector really delegates rather than keeping its own copy.

**Header: countdown → clock + age.** `⏱ Next update in: 42s` is gone. In its place a browser-side
ticking wall clock (proving the tab is not frozen — and explicitly *not* proving the data is
fresh, since it would tick on happily if the Python behind it died) plus **Time since last data**
counting upward. The countdown had a resting state that read as healthy: collector dead, no price
for an hour, display `0s`. Counting upward has none. One deliberate departure from what Chandan
asked, reported rather than done quietly: amber at his threshold, red at 1.5×, because at the
300-second cadence the age hits 300s immediately before every poll and would flash red once per
cycle all day.

### What broke — M3.4

**A false all-clear, and it is the worst kind.** Found not by a test but by answering Chandan's
question *"does every ten minutes mean an email every ten minutes?"*. At 16:00 the market shuts
and `check()` starts returning "ok — market closed". A collector dead all afternoon would
therefore have flipped alarming → ok and sent **"RECOVERED: prices are arriving again."** They
were not; the market had closed and the watchdog had gone blind. A false all-clear is precisely
the message that stops you looking. The two blind states now carry `informative=False` — *"no
news"*, not *"all is well"* — and both the alert decision and the saved state leave the alarm
untouched. **An all-clear now requires positively observing fresh data.** Reverting the fix on a
copy confirmed exactly two tests fail without it, so they are not passing by construction.

**A negative age reported as healthy.** Probing `check()` by hand — before any test existed for it
— produced *"Collecting normally — newest price -2001584s old"*. A price newer than now means the
clock is lying, and **every judgement this script makes is a comparison against a clock**, so a
wrong clock invalidates the reassuring verdicts as much as the alarming ones. Now an alarm in its
own right.

**`[TimeSpan]::MaxValue` is rejected by this Windows 11 build.** The documented idiom for a
"forever" repetition serialises to `P99999999DT23H59M59S` and the task XML validator calls it out
of range. Replaced with 3,650 days, with the resulting August-2036 horizon written into the script
so it is known rather than a surprise.

**A sloppy assertion of my own, caught and fixed:** `assert session_of(...) is expected or
session_of(...) == expected` — the `or` gave it two ways to pass. Reduced to one `==`.

**Latent bug noted, deliberately not fixed:** `scripts/register_collector_task.ps1` sets
`$ProjectDir = $PSScriptRoot` and then looks for `start_collector.bat` beside itself, but it lives
in `scripts/` and the batch file is at the project root. Harmless — that script is documented as
not the active mechanism — and fixing it inside an unrelated commit is how unrelated things break.
Recorded in a comment in the new script instead.

**Two untracked files were one careless `git add .` from being published:**
`data/token.json.bak-20260802-063131` (a live Schwab credential backup) and `collector.log.1`.
Neither was matched by `.gitignore` — `*token*.json` misses a `.bak-…` suffix and `*.log` misses
`.log.1`. Both patterns widened with comments naming the actual near-miss, and the backup deleted
with Chandan's authorisation after confirming the live `data/token.json` was intact.

**Verified live, and what is not.** Desktop pop-up seen twice, email delivered to Chandan's phone
after he configured `.env`, and the schedule confirmed firing (`LastTaskResult 0`, state file
written). **Unproven: a real outage travelling the whole path.** That cannot be manufactured
without stopping the collector or tampering with the database, both of which need his word. The
first genuine outage is the test.

788 tests pass; `render_check.py` clean on all six tabs; ruff clean on every file authored here.

### Remaining

M3.3 migrations, 3.5 surface `collection_gaps`, 3.6 log the `INSERT OR IGNORE` mismatch, 3.7
data-quality checks, 3.8 token re-auth runbook, 3.9 the three operations documents. **3.8 is next**
— the watchdog now announces that collection has stopped and says nothing about what to do, and
re-auth is a weekly chore performed under time pressure on a market morning. 3.6 has a standing
symptom to explain: the collector logs **"160 of 3,156 rows DISCARDED"** on nearly every cycle, and
a figure that constant is a pattern, not random duplicates.

----

## 2026-08-01 (session 11) — BUG-022 and BUG-019 closed; M2 merged to main

### Completed

**Stage 2 signed off.** Chandan opened the dashboard and checked the charts by eye — the one thing
last session's word-for-word comparison could not do. Calendar Edge and Strike Detail are correct.

**BUG-022 is fixed at both ends, and the better half was Chandan's idea.** The bug: clicking
"View Chart" on a saved lock could silently show a *different* diagonal. The cause is that the
collector narrows every snapshot around **today** — nearest 20 expiries, strikes within ±300
points of spot — while a lock is fixed at the fill and never moves. As the index drifts, a locked
strike walks out of the recorded window, the dashboard finds it missing, drops it (it must, or the
page crashes) and falls back to a default. A position being held, charted as something else, with
nothing saying so.

The first proposal was a warning message. Chandan asked whether the locked legs could simply keep
being collected instead — **fix the cause, not the symptom**. Measuring the stored data showed he
was right and that it was nearly free: every snapshot is clipped at almost exactly ±300 points,
which means **the broker is already sending the strikes we throw away**. Keeping the locked ones
costs no extra request and no extra waiting. Both were done: the locked legs now survive both
narrowings, and the screen still says so on the occasions it cannot honour a click.

### What this rests on

**19 deliberate breakages, 19 caught — but only after one survived.** The survivor was worth more
than the eighteen: a safety net in the collector, meant to stop a damaged lock file killing a
snapshot, turned out to be unreachable from the test that claimed to cover it — a lower layer was
quietly handling the damage first. The net was real; the evidence for it was not. **A test that
cannot fail is not evidence.** Rewritten to inject the failure at the right seam, and both layers
are now checked separately.

**One assumption was stated here and was wrong.** It was claimed the broker's strike limit would
bind at about ±200 points, which would have made this expensive — a second request every cycle.
Reading the stored data disproved it in about a minute. The assumption was wrong in the direction
that would have cost the most work.

**The measurement is now permanent, because filtering destroyed the evidence.** Once stored, every
snapshot is clipped at exactly ±300, so the data could never answer "how much room was left?" The
collector now logs how wide the broker actually went *before* it filters. That log is what will
decide whether a strike that drifts very far ever needs a second request.

### What this does not do

**Pinning only works forwards.** It protects a lock from the next snapshot onward; it cannot fill
in history from before the lock existed. That is why the on-screen message stays — it is what
covers the gap honestly. There are also **no locks saved right now**, so this was built and
checked against constructed cases, not a live position.

### Also completed — BUG-019 closed: the Scanner's summary cards are gone for good

Four figures — Diagonals Scanned, Diff > 5, Best Difference, Avg IV Ratio — sat above the Scanner
table until 29 June, when a large refactor deleted the single line that drew them and left the
forty lines feeding them running on every refresh since. **Chandan chose to delete rather than
restore**, with the asymmetry stated: restoring was reversible after seeing them on screen,
deleting is not. Taken deliberately, not by default, which is what the standing advice warned
against. **ADR-043 records the exact commands to paste the block back** if he changes his mind —
that is the answer to "cannot be undone by looking at the screen", rather than refusing the call.

**And for the third time, something that read as dead was holding something up.** Ten of the
block's eleven working values were genuinely unused. The eleventh, `_ready_count`, feeds the
"N combinations ready to transform" badge that is still on screen. Deleting the block in one
piece was rehearsed on a copy first and **took down all six tabs** — not just the Scanner —
because that code runs before any tab is chosen. All 693 checks passed with the dashboard in
that state. `scripts/render_check.py` caught it, as it did the two previous times.

### Discovered

`pinned_pairs.json` in the project root is dead — an orphaned feature from the v2 dashboard, no
code reads it, contents long expired. Left in place pending Chandan's word (deleting files needs
his say-so); recorded as DEBT-036.

The KPI cards' styling — about fifty lines of `theme.css` — now styles nothing. Kept on purpose:
it is the cheapest restore path for the decision above. DEBT-037.

**Item IDs are being reused.** `BUG-019` has now been two unrelated bugs; the first was closed on
28 July. Since closing means deleting the row and `git log -S` is the documented way to recover
the text, a reused ID makes that search return two mixed-up items. Second occurrence of the
pattern — DEBT-031 was issued twice on 30 July. DEBT-038.

----

## 2026-07-31 (session 10) — M2 finished: app.py 2,505 → 392 lines

### Completed

**The last step of the decomposition is done, and the milestone with it.** `app.py` began this
milestone at 4,283 lines and ends it at **392** — assembly and nothing else. It now holds no
calculation, no stylesheet, no database query and no tab body. Every one of those has a layer with
a written rule and a test that enforces it.

**The brief did not add up, and that was worth saying before starting.** STATUS.md named two
pieces: 757 lines of stylesheet and the 115-line Mission Control card renderer. Both were real,
both are done — and together they are 872 of 2,505, which lands at about 1,630 and misses the
step's own target of "under 400" by a factor of four. The plan file had the honest number all
along: roughly 870 lines were "identified and spoken for", meaning 1,616 were not. Chandan was
shown the arithmetic and chose the full evacuation.

**Two new layers, because the existing four each failed on a rule worth keeping.** The Mission
Control pipeline reads session state — the "New" badge on a card is a comparison against what the
*previous* snapshot showed, so that memory has to survive a page rerun and cannot sit inside a
cached function. That rules out the pure-calculation layer completely. So `services/` was added:
the memoised reads, the sidecar-file bindings and Mission Control, and **the only layer allowed to
know both where the database is and how the page caches things** — which is exactly the pair every
other layer is deliberately kept away from. Separately, the header, sidebar and controls bar are
not tabs: they run before any tab is chosen, and the controls bar is where the current expiry and
strike selection actually comes from. `ui/` holds those, under the two rules the tabs already
follow: it draws, and it never fetches its own data.

### What this rests on, and it is not the test suite

**Partway through, a move left every one of the six tabs raising on load — and all 639 tests
passed.** One constant had moved to a new file and one reference to it had not followed. The suite
never saw it because the suite exercises functions, and until this step the page could not be
exercised at all; `scripts/render_check.py` has said exactly this in its own docstring since July.

So each of the eight phases was checked by running the whole dashboard twice — once against a
copy of the last commit, once against the working tree, on the same database — and comparing
**every string either one drew**, per tab. **Eight phases, eight identical results across all six
tabs.** That is the evidence for this step. The tests are a floor, not the proof.

**The comparison drifted before it was trusted, which nearly wasted it.** The first run showed
every opportunity card reading "Seen 3×" where the baseline said "Seen 2×". Not the code:
Chandan's dashboard is running, Streamlit reloads it whenever a source file is saved, and each
reload recomputed Mission Control and incremented a counter in the real registry file the harness
was copying as its starting point. Fixed by freezing that state once and starting every run from
the frozen copy — **removing the drift rather than filtering the symptom out of the diff**, which
would have trained the eye to skip the exact region where a real difference would appear.

**Two things the comparison cannot see, both handled separately.** It redacts the checkout
directory as known noise, because a scratch copy legitimately differs there — so it was blind to
the re-authentication command, which is built from the file's own location and had to be adjusted
by one directory level when it moved. Get that wrong and the dashboard hands you an instruction to
change into the wrong folder, discovered only when the token has already expired and collection
has stopped. That one is asserted directly instead. Charts remain uncovered entirely, unchanged
from last session: the testing tool exposes no way to read inside one.

### Discovered

**13 faults injected deliberately, 13 caught — and the thirteenth is the finding.** A guard
written last session checks that every timestamped database read is converted to local time before
it can reach a chart; miss a site and a chart's x-axis moves four or five hours while still looking
completely plausible. It matched the name `load_atm_hist`. Every call site outside the tabs is
named `_load_atm_hist`. **One underscore had made half that check decorative from the day it was
written** — the tabs were covered only by the accident that a tab reaches its loader through a
name that happens to carry no underscore. Nothing but deliberately breaking the code would have
found this. That is the second session running in which this exercise found a blind check rather
than confirming a healthy one.

**Two things left alone on purpose, both recorded instead.** The intraday price frame is loaded
and timezone-converted to build a column nothing reads — the frame's only surviving use is a
single number (DEBT-034). And the Scanner's missing summary cards are still BUG-019, still
Chandan's decision. Fixing either one inside a move would have destroyed the single property that
makes the move checkable, which is the same reasoning that protected BUG-019 when it was found
last session.

### Numbers

- `app.py` **2,505 → 392** lines (4,283 → 392 across the whole milestone, −91%)
- **639 → 659 tests**, all passing (+20: the two new layers' import rules, applied per
  module, plus the re-auth path and the stylesheet's integrity)
- Lint **102 → 95** findings
- 13 injected faults, **13 caught**
- 8 render comparisons, **8 identical**
- Closed: **DEBT-002**. Opened: **DEBT-033**, **DEBT-034** (both P3, both deliberate)

----

## 2026-07-30 (session 9, continued) — expired locks now delete themselves

### Completed

**A locked entry disappears once its front leg is done.** Three dead locks were still listed on
screen, the newest expired ten days earlier. The reason was the plainest one available: the cleanup
Chandan designed was never built. The saved-locks file could create a lock, read one, correct its
price, and delete one *when the trash button was pressed* — that was the whole list. Nothing
anywhere compared a lock against today's date.

It does now. A lock is gone once its front expiry date is past, or once the clock passes **4:15 PM
New York on the expiry date itself** — the cash-index close, not the 4:00 PM equity bell. No prompt,
no message; it simply stops being there.

**Deleted, not hidden — Chandan's call:** *"I don't think I'd look once it expires so no point in
archiving it."* That decision is what set the standard of proof for the rest of the work. A rule
that fires one day early now destroys a lock on a position still open, and the entry price it holds
is the number every chart is measured against. So the rule is a small self-contained calculation
handed the current time rather than reading a clock itself, which makes it testable at any instant:
4:14 PM keeps the lock, 4:15 deletes it, the morning of expiry day keeps it.

**The cleanup runs where every reader passes.** Filtering the on-screen list was the cheap option
and would have been wrong — the list would look tidy while the chart and the current-position
lookup carried on using the dead record.

**Two things the cleanup refuses to do.** A lock whose date cannot be read is *kept*, not deleted;
deleting what you cannot read is how records go missing quietly. And when nothing has expired the
file is not rewritten at all — this runs on every page load, and a rewrite that changes nothing is
still a chance to lose the file.

### Found

**One of my own checks was blind, and only breaking the code showed it.** Seven deliberate faults
were introduced on a throwaway copy to see whether the new checks would notice. Six were caught.
The seventh — code that *relabels* an incoming time as New York instead of *converting* it — sailed
through, because the two example times I had picked happened to give the same answer either way.
Rewritten with times where the two approaches disagree; 7 of 7 caught. A check that has never
failed has never been tested.

**The scrambled-controls problem is not fixed, and is not the same bug.** Chandan asked whether the
expiry cleanup made it moot. It does not. Clicking "View Chart" stages the lock's expiries and
strikes, a safety guard drops any value missing from today's data, and each dropdown then quietly
falls back to its default — so the screen shows a *different* diagonal than the one clicked, with
nothing saying so. Expiry was one way to trigger that. Two remain on a **live** position: a strike
drifting outside the range the collector stores as the market moves, and a back expiry further out
than the furthest one collected. Recorded as BUG-022, P1, awaiting Chandan's call on scope.

**Tab clicks are slow, and the cause is not yet established.** Logged as ENH-011 at Chandan's
request. Distinct from the freeze fixed earlier today — that was a page that never finished; this
is one that finishes slowly. The shape is known (every click re-runs the whole page from the top,
and the caches expire on 55- and 120-second timers rather than on new data arriving) but nothing is
measured yet, and the note says explicitly not to start by tuning those timers.

### Remains

The saved-locks file was backed up before anything was written to it. **The cleanup has not yet run
against the real file** — that happens on the next page load, and the three dead locks should be
gone from the popover.

----

## 2026-07-30 (session 9, continued) — clearing the debt the safe moves left behind

### Completed

**Three leftover items cleared, all created deliberately by the way the tabs were moved.** Each one
existed because moving code and tidying it at the same time destroys the only evidence that the move
changed nothing. That evidence has now been collected, so the tidying is due.

**The labels match the building again (DEBT-028).** Fourteen names in the calculation folder carried
a mark meaning "internal, don't call from outside" while being called from outside constantly — 168
references corrected. The 63 translation lines added so each tab could be moved without altering a
character are gone; the tabs read their inputs directly. And a small four-line calculation that had
been stranded in the big file finally moved, **with the six tests it never had** — it decides which
strike each dropdown lands on, so it decides what position a fresh page shows.

**Times stopped losing their labels too early (DEBT-030).** The part that fetches data was handing
out a bare "14:30" with nothing saying which city, because that is what the charts wanted. It now
hands over the full unambiguous time, and each chart strips it at the last moment. Nothing on screen
moved — that was checked, tab by tab.

### Found

**A month-old accident, nearly made permanent (BUG-019).** One item was recorded as dead code to
delete. Reading its history first showed it is not dead: it is a **feature that was accidentally
switched off on 29 June**. The commit that did it removed two of six summary cards, carefully
re-fitted the layout to the four that remained, *and* deleted the line that puts them on screen. You
do not carefully re-fit a layout for four cards in the same breath as deleting all of them. So the
Scanner has been missing four figures for a month while the calculations behind them kept running.
**Deleting was the option nobody could spot by looking at the screen** — it is left for Chandan to
decide.

**A hidden dependency, found by breaking it.** Two different pieces of code happened to share a
name, and one of the test tools silently relied on that coincidence. Renaming one broke seventeen
checks at once. The names match again, on purpose this time, and the reason is written down.

### Mistakes in my own work

**A tab broke and 623 checks stayed green.** One file used a setting it had never imported. Every
check passed; the tab raised the moment it was opened. Caught by the script that actually opens each
tab — which exists because this exact thing happened once before, in almost the same words.

**A test too weak to fail, for the third time.** The check written to protect the trickiest part of
the timezone change could not detect the fault it was written for: its example data sat inside a
single day, where the right and wrong answers agree. Rewritten to straddle the boundary deliberately.
**Two rewrites of a bulk edit were also wrong** — one turned a function's argument name into a broken
assignment, the other measured character positions in the wrong units. Both failed loudly and
immediately rather than quietly; the first refused to parse, the second tripped a guard before
anything was written.

**A difference that was not one.** A count on one tab read 126 before and 125 after. The cause was
the clock, not the change — that view asks for "the last 24 hours", so rows drop out as time passes.
Running the comparison in the reverse order produced identical output, which is what separated the
two.

**623 checks, 10 new.** Every new one proved by deliberately breaking the code on a copy: **6 faults
injected, 6 caught** — five of them before the weak test above was strengthened, which is how the
sixth was found.

--

## 2026-07-30 (session 9, continued) — step 2.4 finished: all six tabs out

### Completed

**The screen is no longer one file.** All six tabs now live in their own files. `app.py` has gone
from **4,283 lines to 2,486** over the milestone — the first time it has ever shrunk by more than a
rounding error. Calendar Edge alone was 747 lines, more than the other two remaining tabs together.

**Nothing on the screen changed, and this time that is measured rather than hoped.** Every one of
the six moved blocks is **character-for-character identical** to the version it replaced, checked by
script against the commit where each was still in the old file. All six tabs were also run before
and after against the same live database, and every word each one displays is identical.

**Why nothing was tidied on the way past.** Three times the move went straight over something worth
fixing — a threshold written out four times, a misleading message, a dead line of code. None were
touched. The character-for-character comparison is the only real evidence this step has, and it
survives exactly as long as nobody edits a body while moving it. They are all written down instead.

### Found

**A copy left behind — my own mistake, caught and then made impossible.** One small helper was
*copied* into its new home rather than moved, leaving two definitions of the same function. That is
worse than either forgetting or duplicating alone: the page runs one copy, anything inspecting the
old file sees the other, and nothing breaks loudly enough to notice. Found by listing what the old
file still defined but no longer used. There is now a check that fails if it ever happens again.

**Splitting the file made a checker useful.** One dead line has been dead for weeks and no tool could
say so, because an unused item in a big script is not an error while an unused item *inside a
function* is. Nothing about the line changed — it simply now lives somewhere with a boundary. A small
result, and the clearest single argument for the whole milestone. Recorded as DEBT-032, along with a
second dead function found the same way.

### Remaining in the old file

A 115-line block that draws the opportunity cards is still there. It reads two values directly from
the surrounding file, so moving it means changing what it asks for — and the last time a change like
that was made, every tab broke while all 569 checks stayed green. It moves in the next step, with its
callers checked.

**613 checks, all passing.** 22 new, every one proved by deliberately breaking the code on a copy:
**10 faults injected, 10 caught.**

---

## 2026-07-30 (session 9) — step 2.4 begins: three tabs out, and two faults found by reading

### Completed

**Three of the dashboard's six tabs now live in their own files** — Historical Statistics, Research,
and Entry Analysis. The main file is down from 3,945 lines to **3,644**. Nothing on the screen
changed, and that is the entire point.

**Why this step is different from the three before it.** Until now, every piece of code moved was
code the automatic checks already watched, so if a move broke something a check went red. The tabs
are *drawing* — a panel whose rows come out in the wrong order still looks like a panel, and no
check can tell. So instead of proving the moved code still works, each tab was moved in a way that
lets us prove **nothing was rewritten on the way**: the code sits at the same indentation in its new
home, so it could be copied rather than retyped, and a script then compared each moved block
character-by-character against the old file. **All three came back identical.** Tidying up the names
afterwards is a separate job (DEBT-028), precisely because tidying and moving in one go would leave
nothing to compare.

**Seven new automatic checks** guard the new folder. The one that matters: a tab is forbidden from
fetching its own data. If it did, the page would look exactly the same and give exactly the same
numbers, while asking the database for them again every few seconds — a fault with no visible
symptom at all. All seven were proved by deliberately breaking the code on a copy: **7 faults
injected, 7 caught.** 600 checks in total, all passing.

### Bugs and debt found

**BUG-018 — on expiry day the screen tells you to set strikes you have already set.** The "Front
Expiry" box defaults to the nearest expiry. On any day something expires *that same day*, that
default has zero days left, so the "expected move" figure it needs cannot be calculated — correctly,
by design. But the tile that depends on it then displays **"— (set strikes)"**, sitting directly
beside another tile showing a real price calculated from those very strikes. The message is not
merely unhelpful, it is false, and it points at the wrong control. Today is such a day, so this is
live right now. The Research tab loses its "you are here" marker on the same days for the same
reason. **Not fixed — the replacement wording is Chandan's call.**

**DEBT-031 — the 5-point transform threshold is written out four times and two copies disagree.**
The Scanner reads it from one named place. The Entry tab hardcodes it three separate times, and one
of those uses "greater than" where everything else uses "greater than or equal to" — so at exactly
5.00 two panels *on the same tab* contradict each other. Worse in future than now: change the
threshold and the Scanner would flag opportunities the Entry tab calls unfavourable, with nothing
reporting the disagreement.

**Neither was fixed here, deliberately.** Both were found by reading the tab closely while moving
it. Changing a trading threshold inside a move means it is no longer a move, and the
character-by-character comparison — the only real evidence this step has — would have been
destroyed to fix something that has been true for weeks.

### Mistakes in my own work

**A comparison that passed while proving nothing.** The before/after check writes down everything a
tab displays, before and after, and compares. Its first version crashed while saving — the file it
wrote was **empty** — and two empty files compare as identical. It reported success. Caught by
checking the file sizes rather than trusting the verdict; it now refuses to report an empty result.
**This is the third time in three steps** that a check has been too weak to fail (ADR-029, the
2.0a assertions, and now this).

**Charts are not covered by that comparison at all,** which I would rather state than let the word
"identical" imply otherwise. The testing tool in this Streamlit version cannot reach inside a chart,
so for the Research tab the comparison sees the captions around it and nothing else. The chart's
only assurance is that its code was proved unchanged.

**A wrong assumption, corrected.** The frozen dashboard was assumed to be caused by the work in
progress. It was not — nothing had been edited at that point. The running program had been up since
07:35, through three separate rounds of changes, and had consumed 5,200 seconds of processor time in
three hours. It needs restarting before anything it shows is trusted.

### Remaining

Three tabs left: Strike Detail, Scanner, and Calendar Edge. **Calendar Edge is half the remaining
work on its own** (~750 lines) and holds the ten chart sites that DEBT-030 is waiting on.

---

## 2026-07-30 (session 8, continued) — step 2.3: the file that would have quietly erased itself

### Completed

**`state/` exists, and three of DEBT-011's five faults are fixed** — the three that could lose data.
The three small JSON files (your chart colours, entry locks, and the ~700 KB opportunity registry)
now have one piece of code that reads and writes them, and it is told which folder to use.

**The fix was already sitting in the same file.** `config.py` has always had a `PROJECT_ROOT`, and
the database path was always built from it. The database got this right from day one; the JSON files
just never followed. `STATE_DIR` uses the same convention, so **nothing moved** — the existing
registry is found exactly where it always was.

**The headline defect was not the dangerous one.** The known problem was the relative filename:
start the dashboard from the wrong folder and it finds no history, creates an empty file there, and
shows a panel that has forgotten everything. Real, but recoverable — your file was never touched.

What I found while reading the code is worse. **If the registry ever failed to parse, the loader
returned "empty" — and the next update wrote that empty registry back over the file.** One bad parse
became permanent loss of 700 KB, with no copy anywhere, because all four sidecar files are excluded
from version control. An unreadable file is now moved aside before anything else happens.

**Writes are also atomic now** — written to a temporary file and renamed into place. The registry is
rewritten in full roughly 126 times a trading day, so "interrupted mid-write" is not a theoretical
concern.

### What the checks caught

**A real bug in my own quarantine code.** Two corruptions within the same second produced the same
rescue filename, and the second copy silently overwrote the first — defeating the whole point of
moving it aside. The check I wrote for exactly that failed on my first version.

**And one of my own tests was weaker than I claimed.** I wrote a "failed write leaves the old file
intact" check and described it as proving atomicity. It does not: an unserialisable payload fails
*before* any bytes are written, so a plain non-atomic write passes it too. Replaced with one that
asserts the temp-file-and-rename mechanism directly, and said plainly in the file why asserting a
mechanism is justified here.

### Verified

569 pre-existing checks pass unchanged; 22 new; **591 total**. Mutation-verified on a copy: **6
faults injected, 6 caught** — including reintroducing DEBT-011 itself. All six tabs render with no
exception.

**Your data:** backed up to `spx-dashboard-backups/state-20260730-091914` and verified by SHA256
*before any code changed*. Afterwards, `entry_locks.json` and `chart_colors.json` are byte-identical
to that backup; the registry grew 2,150 → 2,174 entries, which is the app doing its normal job. No
stray or quarantined files anywhere.

**One honest note on the render check:** the per-tab element counts differ from this morning's runs
(the Scanner tab shows fewer cards). That is live market data and a registry that has moved on, not
a code change — the structural counts are unchanged and nothing raised.

### Also done: the sparkline is off the opportunity cards

Removed at Chandan's request. The reasoning was sound — the gap's shape over time is exactly what
"View Chart" opens, drawn properly and at a readable size, so a 10-character version squeezed onto
every card was clutter competing with the numbers above it.

**One judgement call, stated because it was mine:** a small trend arrow (↑) used to ride on the end
of that sparkline. It is a different signal from the bars — "the last three readings are rising" —
so rather than delete it along with the chart, it moved onto the Gap number, which is the thing it
actually describes. Say if you would rather it went entirely.

**Only the display was removed.** The value is still computed and still carried on every card, and
is pinned by tests in two files. Deleting the field means retiring those tests, which is a different
kind of change from removing a `<div>` and deserves its own deliberate commit. Logged as DEBT-031 — **renumbered to DEBT-035 on 2026-07-31**, since the threshold item had already taken that number.

---

## 2026-07-30 (session 8, continued) — 569 green checks, and a completely broken dashboard

### Completed

**DEBT-027 is closed and off the backlog.** The label helper that writes "Friday, Aug 21, 2026
(23 DTE)" now takes the expiry table as an argument instead of reaching for a shared copy. Its only
caller was checking one table and looking up another, four lines apart — identical today, so it
worked; the day they differed the label would have silently lost its "(23 DTE)" with no error.

**I reversed my own advice on this, and should have checked before giving it.** I said it was best
left until step 4. It had two call sites in one function. Twenty seconds of looking would have told
me that before I said otherwise.

**DEBT-030 now has a safety net, but is deliberately still open.** The read layer flattens
timestamps to a local wall-clock because the charting library demands it. Fixing that properly means
touching **ten** chart sites, all of which step 4 moves anyway. What was dangerous was never the
change — it was that **no check could see it**: the frozen references were captured against the
current behaviour, so shifting every chart by four hours would match its own reference and pass. Two
new checks now assert the conversion from a known stored value, working the expected answer out
independently of the code. **They are designed to fail when DEBT-030 is fixed** — that failure is
what will force the ten charts to be updated in the same change.

### The thing worth remembering from today

**Every one of the 569 checks passed while all six tabs of the dashboard crashed on load.**

The label helper gained a required argument. I searched for `_exp_label(` and found two call sites,
updated both. What that search could not find is the two places the function is handed to Streamlit
**by name**, as the formatter for the expiry dropdowns — where Streamlit calls it with one argument.
Result: `TypeError` on every tab, and a completely green suite.

Three things came out of it:

1. **Searching for `name(` is not searching for uses.** A function passed as a callback is invisible
   to it. Search the bare name when changing any signature.
2. **The suite structurally cannot catch this**, and won't until `views/` exists — it exercises
   functions, and this was the page.
3. **`scripts/render_check.py` is now part of the repo**, not a scratch file. It runs the app once
   per tab and fails loudly if any raises. It is the only thing that caught this. Run it after any
   change to `app.py`.

**And a third instance of the same pattern.** Testing the label helper directly proved it honours
its argument; it did not prove the caller passes the right one. Re-injecting the original defect at
the call site left everything green until a new check was written for it. Three steps in a row now
where the gap was a wrong or missing **call** rather than a wrong calculation.

### Verified

564 pre-existing checks pass unchanged; 5 new; **569 total**. Mutation-verified on a copy: 4 faults
injected, 3 caught, **1 survivor which produced a fifth check**, then re-injected and caught. All
six tabs render, with output identical to before the change. Lint unchanged at 94.

### Remains

DEBT-030 (after step 2.4), DEBT-028, DEBT-029, DEBT-011. Next: **step 2.3, `state/`** — the small
JSON settings files, and the relative-path defect.

---

## 2026-07-30 (session 8, continued) — the reads move out, and a test stops tampering with the code

### Completed

**Step 2.2: `dataaccess/` holds the nine database reads.** The screen no longer fetches its own
data. Each read now takes the database location **as an argument** instead of assuming it, and
`app.py` keeps a thin wrapper per query whose only job is the memory of recent answers.
**`app.py` is now 3,936 lines**, from 4,283 when M2 started.

**The package is called `dataaccess/`, not `data/` as the plan said.** `data/` already exists and
holds the 1.57 GB database and the broker token file. Source code does not belong beside those.
Substance unchanged; renaming later is a directory move and one import line.

**DEBT-027's first half is closed, and this was the point of the step.** `_candidate_signals` used
to read the database location from a global, so all 22 tests around it had to overwrite that global
to get near the code — a test modifying the thing it tests. It now takes the path, defaulting to
the global so nothing in production changes.

### What was harder than it looked

**Adding the parameter proved nothing on its own.** All 563 tests passed with the new argument
completely ignored, because every one of them sets the global instead. A fix that no test can
distinguish from *not* fixing it is a claim, not a fix. So one new test aims the global at a file
that does not exist and passes the real database as the argument: results can only come back if the
argument won. Injection 6 confirms it fails when the argument is ignored.

**The same memo trap appeared again, in a different disguise.** The at-the-money history has a
fallback: if today is empty it re-reads a wider window. That second read must reuse the remembered
result rather than query again. Same shape as step 2.1's scanner, second step running, so both
seams now have source-level guards. **Assume this trap appears again in `state/` and `views/`.**

### Discovered

**The plan said eleven database reads. There are nine.** Three other similarly-named functions read
small JSON settings files — chart colours, entry locks, the eligibility registry — not the database.
They belong to step 2.3. Corrected in `plan.md`.

**Four functions carried an argument none of them ever read.** `snapshot_id` existed only to key
the cache. The cache stayed in `app.py`, so the key did too, and the queries stopped pretending to
use it.

**The read layer is quietly deciding what the x-axis looks like** — `.dt.tz_localize(None)`, done
because Plotly's rangebreaks demand it. That is a display decision inside the data layer, the exact
thing this step was meant to separate. **Not fixed:** changing it shifts every chart's x-axis and no
existing test would catch a one-hour error, because the goldens were captured against the current
behaviour. Opened as DEBT-030, to be done after `views/` exists.

### Verified

560 pre-existing tests pass unchanged; 4 new; **564 total**. Mutation-verified on a throwaway copy:
**6 faults injected, 6 caught, no survivors.** The one that matters most: dropping the
decimal-to-percent conversion *in its new home* fails the pipeline tests, proving the moved code is
still genuinely pinned where it now lives. The dashboard was run again — **all six tabs execute with
no exception, and the per-tab output is identical to before the change.**

---

## 2026-07-29 (session 8) — M2 begins: `core/` is out, and `app.py` shrinks for the first time

### Completed

**Step 2.1 of the decomposition: `core/` exists.** Eight functions and six constants left `app.py`
for four new modules — `format.py` (the on-card formatters), `charts.py` (session breaks and the
IV-ratio colour bands), `ranking.py` (card order and identity), `scanner.py` (Phase A maths and its
thresholds). **`app.py`: 4,283 → 3,991 lines**, the first reduction since the audit; it had only
grown until now.

**The rule for what moved: only code the tests already pin.** All eight functions are covered by
the 88 characterization tests written last session, so every move is verifiable rather than
hopeful. `_nearest_idx` was left behind for exactly this reason — four obviously-pure lines, but
untested, and untested code is where a silent break hides. It goes with `views/` (DEBT-028).

**549 tests passed unchanged.** That is the evidence the move altered nothing: not one existing
test was edited to accommodate it. Total is now **560**.

### What was harder than it looked

**The memo could not come along, and moving it naively would have made the dashboard slower.**
`_compute_transform_scanner` carries a Streamlit cache, and two callers share those saved results —
the Scanner tab and the 21-offset sweep. `core/` cannot import Streamlit. Moving the function
without thinking would have left the sweep calling an uncached copy: **identical numbers, every
test green, and 21 recomputations on every single rerun.** The cache therefore stays in `app.py`
wrapping the imported function, and the sweep is handed it explicitly. See ADR-032 for the
alternatives and why each was rejected.

**That seam then survived the injection run** — dropping the one keyword broke nothing any test
could see. It now has its own guard asserted against `app.py`'s syntax tree, the same shape as the
BUG-002 wiring test. **Third time on this project that a missing *call*, not a wrong calculation,
was the defect worth guarding.**

**Last session's tripwire fired exactly as designed.** `test_app_still_defines_break_sessions_
where_the_loader_expects_it` was written against this move and failed the moment it happened. It
was repointed, not deleted — and strengthened to assert the function has exactly *one* home, which
also catches a copy left behind in `app.py`.

### Discovered

**A number in the docs was wrong, and it was mine.** I measured `app.py` with a PowerShell command
that silently ignores blank lines and reported 3,891 where the true count was 4,283 — the figure
the backlog already carried. Corrected in `plan.md`, `decisions.md` and `backlog.md`; the real
before/after is 4,283 → 3,991.

**The collector holds `.collector.lock` open**, which is why a naive `robocopy` of the repo hangs:
its default is a million retries at 30 seconds each. Worth knowing before the next rehearsal copy —
skip the lock and the 1.57 GB `data/` directory (which also holds `token.json`).

### Verified

549 pre-existing tests pass with no edits; 11 new; **560 total**. Mutation-verified on a throwaway
copy: **5 faults injected, 4 caught, 1 survivor which produced the 560th test, then re-injected and
caught.** The survivor is described above.

**The dashboard was then opened, and it renders.** Committed first (`e6ce849`, branch
`m2-core-extraction`, not pushed; the pre-commit hook ran all 560 tests itself), then verified two
ways. `AppTest` executes `app.py` the way Streamlit does — a plain HTTP fetch proves nothing, since
Streamlit only runs the script when a client connects. **All six tabs execute with no exception and
no error on the page.** Running every tab mattered: the tabs are custom buttons rather than
`st.tabs`, so a single run only renders the active one, and the extracted chart and formatting code
lives in the tab bodies. The real server was then started and serves (health 200).

**Found while doing that, unrelated to the extraction: two Streamlit APIs are past their removal
dates.** `use_container_width` (33 uses) was due for removal after 2025-12-31 and
`st.components.v1.html` (1 use) after 2026-06-01. Both dates have passed; the dashboard works only
because the compatibility shims have outlived their notice, which means **the Streamlit version is
now effectively pinned whether or not anyone decided that.** Opened as DEBT-029.

### Remains

Steps 2.2–2.5: `data/`, `state/` (fix the relative-path defect), `views/`, then `app.py` under 400
lines. DEBT-028 opened for the deferred rename. Still blocked on Chandan: BUG-001's symptom, the
6 practice trades, and the break-even question.

---

## 2026-07-29 (session 7) — a safety net under the screen, before M2 touches it

### Completed

**Committed session 6's work — two commits, `dc86a21` and `597095e`.** The `check_db.py` fixes
with their tests, then the documentation pass that settled the plan-vs-audit split and retired
`docs/TESTING.md`. The pre-commit hook ran the full suite on the code commit as designed.

**M2 pre-work 2.0a: the display layer is now pinned — 28 tests, 462 → 490.** M1 froze what the
scanner *computes*. Nothing froze what the screen *shows*, and those fail differently. A scanner
returning wrong numbers can at least be checked against the database; a panel whose rows come back
in a different order looks completely normal. That matters because the ordering is not decoration —
**the top card is the one that gets traded.** A refactor that reversed a sort key would have gone
unnoticed for weeks while influencing real decisions.

Covered: card ordering (`_rank_for_panel`), the geometry of the multicolour IV-ratio line
(`_banded_ratio_traces`), the formatters behind every cell and glyph (`_sparkline`,
`_fmt_duration`, `_fmt_eta`), and card identity across reruns (`_card_key`). Ordering is asserted
against the **same real production snapshots** the scanner goldens replay, so the whole chain
*snapshot → scanner → ranked panel* is pinned end to end rather than in pieces.

**`tests/app_loader.py` gained a second entry point instead of a wider one.** The loader pulls
functions out of `app.py` via AST so they can run without launching the dashboard. Adding plotly to
the existing namespace would have been one line — and would have quietly destroyed the scanner's
guarantee that it cannot reach for I/O or drawing, which is enforced precisely by *withholding*
those names. Each caller now supplies its own namespace. See ADR-029.

**The real `app.py` was never modified.** The nine fault injections ran against a copy in a
scratch directory, with the loader pointed at it. The dashboard is running and Streamlit
hot-reloads on save; mutating the live file to test it would have pushed nine deliberately broken
versions of the app onto the screen.

### Discovered

**Two of my nine injections were not caught, and both were my assertions being wrong rather than
tests being missing.** This is the finding of the session and it generalises, so it went into
ADR-029 as the sibling of ADR-025:

1. *"Ranking must not mutate its input."* The function copies its argument as its first act, so
   the scratch column could **never** reach the input. I had asserted something that cannot break.
   The leak actually shows up in the return value, as a stray column on the rendered panel.
2. *"A long series must be downsampled, not truncated."* I fed it `range(100)` — and downsampling
   and truncation give the **identical** glyph on a straight line, because a straight line looks
   the same however you sample it. The fault only shows on a series that changes direction. The
   test now rises then falls, where truncation would report a peak as a climb.

ADR-025 established that a characterization test protects only what reaches its *output*. This
adds: **an assertion protects only what its *input* can express.** Both of these were green tests
protecting nothing. Choosing the test data is part of writing the assertion, not a detail below it.

**`app.py` is 4,283 lines, not 3,891.** I had been repeating the 3,891 figure; the audit's own
number was 4,230. It has grown ~50 lines since the audit measured it. Corrected in `backlog.md`
DEBT-002, with the growth noted, because a monolith still accreting while we plan around it is the
argument for M2 rather than a footnote to it. The audit's figures were left alone — it is a frozen
snapshot and 4,230 is what was true when it was written.

**One arguably-wrong behaviour was pinned deliberately rather than fixed.** `_RATIO_BANDS` compares
inclusively at both ends, so a ratio of exactly 1.00 is drawn in two overlapping bands. Invisible
in practice, and "fixing" it means deciding which band owns the edge — a real decision, not a typo.
Frozen with the reasoning inline so that a future change is made on purpose.

**Two symptoms Chandan mentioned were briefly logged as BUG-020 and BUG-021, then withdrawn the
same session at his request** — dashboard slowness, and "sometimes the chart would look off". Each
had been noticed once, without specifics. His reasoning was right and worth keeping: a backlog row
whose content is three plausible theories is not a bug report, it is a standing invitation to
optimise something that was never measured. Both are expected to resurface with real evidence once
M2 testing starts, and can be logged properly then. `git log -S "BUG-020" -- docs/backlog.md`
recovers the theories if they turn out to be useful.

Worth restating from those notes, because it is a *fact* rather than a theory: `_compute_mc_core`
is cached with `max_entries=3`. If that ever does become the slowness, it will be measurable, and
measurement comes before any change.

### Verified

- 490 tests pass. The **scanner goldens still pass**, which is what proves the `app_loader`
  refactor changed nothing — that suite is the control group for the change I made to its loader.
- 9 faults injected into a copy of `app.py`; 9 caught, after the two weak assertions were fixed.
  Re-ran both individually to confirm the fixes actually closed the gap rather than looking like it.
- `ruff check` clean on both new/changed test files.
- `git diff app.py` empty — the production file was untouched throughout.

### Later the same day — 2.0b started: the fixture database exists

**`_candidate_signals` is pinned — 22 tests, 490 → 511.** This is the function behind four things
on every Mission Control card: **Duration Active**, the **ETA**, the **▁▂▃ sparkline**, and the
**rising-trend arrow**. Unlike the morning's work it needs a real database, so it got a temporary
one — built by calling `db.py`'s own writers, never touching the 1.4 GB production file.

**The fixture is the actual deliverable, not the tests.** `make_transform_history()` and the
`mc_db` fixture now live in `conftest.py`, so the remaining Mission Control functions cost tests
rather than infrastructure. Three decisions in it are worth knowing:

- **It goes through `db.create_snapshot` / `insert_option_rows` / `finalize_snapshot`** rather than
  raw `INSERT` statements. Raw SQL would be shorter *and would survive a schema change* — which is
  the objection, not the benefit. A fixture that survives a schema change keeps testing a shape
  production no longer has and reports green while the pipeline is broken.
- **The gap is engineered to equal one leg's price exactly.** With the back legs cancelling and
  both wings at $5, the transform gap *is* the front call mark. So `write([4.0, 5.5])` means what
  it says, and a test about the $5 threshold reads honestly instead of hiding the number behind
  six leg prices.
- **Timestamps are computed relative to now, never hardcoded.** The query filters on
  `datetime('now', '-N days', 'utc')`. Fixed dates would pass today and silently return zero rows
  forever after — a test that decays into a no-op without ever turning red.

### Discovered — a correction that changes what we think protects what

**The risk I named this morning as the headline M2 danger was not the danger.** I had written that
the thing to fear was `df.sort_values("timestamp")` being dropped when the query splits out into
`data/queries.py` — "the SQL already has an ORDER BY". Injecting exactly that changed nothing: all
19 tests passed.

Rather than bend a test until it went red, I checked. The pandas sort is genuinely **redundant**.
The SQL's `ORDER BY s.snapshot_timestamp` *is* load-bearing — strip it and rows come back in
insertion order, which I verified directly — and it is **already pinned**, in
`test_db.py::test_transform_mark_history_is_ordered_oldest_first`, written months' worth of
sessions ago and inserting snapshots 1, 3 and 2 days back precisely so that insertion order and
time order disagree. The protection existed all along, one file over from where I was looking.

So the M2 instruction is a different one from the one I first wrote down, and more useful:
**when that query moves, its ordering test must move with it.**

**A second injection also proved equivalent, with a small finding attached.** Removing the timezone
conversion changes none of the four outputs — confirmed by running the real function under New York,
Tokyo and UTC and getting identical answers. It *cannot* matter: every output is either a difference
between two timestamps or derived from gap values alone, and the function returns no timestamp at
all. The comment on that line says *"required by Plotly rangebreaks"*, which is true where it was
copied from and misleading here, because this function never draws anything.

**Two of the nine caught faults were only catchable after fixing the test data**, the same lesson as
the morning, now twice in one day: a 12-reading sparkline window cannot be tested with 8 readings,
and a 3-reading trend window cannot be tested on a series where 2 and 3 readings agree. Both tests
passed against deliberately broken code until the data could express the fault.

**DEBT-027 logged.** `_candidate_signals` takes its database path from `config.DB_PATH`, a module
global, rather than as an argument — so no caller can point it elsewhere and every test must patch
global state. Harmless in production, where there is only one database, but it is a hidden
dependency that will follow the code into `core/` unless M2 turns it into a parameter.

**One more arguably-wrong behaviour pinned rather than fixed.** The eligibility streak is contiguous
in *snapshots*, not in time, so a 90-minute collector outage inside a streak is counted as though
the gap had been observed holding throughout. It was not observed at all. Frozen with the reasoning
inline, because what "active" should mean across an outage is a strategy decision, not a refactor's
to make.

### Verified (2.0b)

- 511 tests pass. `git diff app.py db.py` empty — no production code was touched all day.
- 11 faults injected into a copy of `app.py`: **9 caught, 2 proven equivalent** (the redundant sort
  and the timezone conversion, both investigated rather than assumed).
- The fixture is checked against itself first: one test asserts the engineered gaps come back as the
  gaps requested, because if that arithmetic were wrong every other test in the file would be
  meaningless while still passing.
- `ruff check` clean on the new files. The remaining findings in `conftest.py` are the pre-existing
  deferred-import family already parked under DEBT-025.

### Later still — DEBT-026 closed, M2's pre-work is complete

**The rest of the pipeline is pinned — 38 tests, 511 → 549.** `_compute_mc_core`,
`_build_non_atm_panel`, `_run_mission_control`, the persisted eligibility registry, and all nine
`_load_*` query wrappers. With the two earlier files that is **88 characterization tests** standing
between a refactor and a silently changed screen, and DEBT-026's row is deleted.

**The single most valuable test in the whole set.** `_compute_mc_core` ranks *before* it caps the
list to 20 candidates, and until today the only thing enforcing that was a comment. The test gives
it 25 symmetric combos at $4.90 and 3 asymmetric ones at $4.10: rank first and all three asymmetric
combos survive at the top of the panel; cap first by raw gap and every one of them disappears.

That matters because a symmetric combo — same put and call strike — is a degenerate straddle, not
the structure this strategy trades. So the failure mode is: **the dashboard quietly stops showing
the trades you actually take.** It still renders. It still looks sorted. Nothing errors. Injection 1
confirmed the test catches exactly that reordering.

**Two module-level hazards found by writing the tests, both real.**

The first nearly bit immediately. `_ELIGIBLE_HISTORY_PATH` is `Path("eligible_history.json")` — a
**relative** path, resolved against whatever directory the process happens to be in. The first draft
of these tests would have overwritten the live 599 KB registry in the repo root. The loader now takes
the path as an argument, which is the same shape of guard as `conftest`'s production-database
assertion. It also means the real registry silently depends on where the dashboard was launched
from: start it from the wrong folder and the eligibility history is simply empty, with no error.
Added to DEBT-011 rather than opened as a new item.

The second: `_build_non_atm_panel` takes a `dte_by_expiry` **parameter** and passes it nowhere —
`_exp_label()` reads a module **global** of the same name. In production they are the same object, so
the parameter is decorative. Filed with `config.DB_PATH` under DEBT-027; same defect, same fix.

**A stand-in for `st.session_state`, deliberately a real dict rather than a mock.** The "New" badge
logic is entirely about values *persisting between calls* — a card must be new the first time a
snapshot is seen and not new the second. A recording mock would confirm writes happened while proving
nothing about the behaviour that matters. `FakeStreamlit` having exactly one attribute is itself a
check: if it ever needs a second, logic has started reaching for the page from inside the data layer.

### Discovered

**32 injections, 31 caught, 1 proven equivalent — but four survived the first pass, and three of
those were my assertions again.** The pattern is now clear enough to be worth stating as a rule,
because it is the third time in two days:

> Every one of the three fixable misses was an assertion about a **set, a boundary, or an
> ordering**, tested against data containing only one case. One card cannot demonstrate an order.
> A gap of $6.00 cannot probe a threshold at $5.00. A membership check cannot detect a permutation.

Concretely: `is_live` flipped from `>=` to `>` went unnoticed because every test used gaps like 6.0
or 7.0; the call/put mapping inverted went unnoticed because the assertion was
`set(df["side"]) <= {"CALL", "PUT"}`, which passes just as happily when C and P are swapped; and the
asymmetric-first tier being dropped from "Likely Next" went unnoticed because only one card had a
computable ETA, so there was no order to observe. All three now have data that can express the fault.

The fourth survivor was **genuinely equivalent** and left alone: `best` ignoring liveness cannot
differ from taking the first card, because the panel already sorts live cards first. Documented at
the test rather than forced red — and it becomes load-bearing the moment anyone changes that sort,
which is separately pinned.

**One stub, and it is defensible.** `_scan_all_offsets` is replaced when testing
`_compute_mc_core`'s ordering. The rank-before-cap tie cannot be coaxed out of a real option chain,
and the scanner is already pinned by its own golden tests, so controlling the collaborator is what
makes the decision observable. Testing it through a real chain would have tested the chain.

### Verified (DEBT-026 closure)

- **549 tests pass.** `git diff app.py db.py config.py` empty — no production code was touched at
  any point today.
- **43 injections across the three new files: 40 caught, 3 proven equivalent.** Each of the three
  survivors was investigated to a conclusion rather than assumed to be a gap.
- `ruff check` clean on the new files; the remainder in `conftest.py` is the pre-existing
  deferred-import family parked under DEBT-025.
- The registry tests confirm the file is actually written to disk, not merely returned — an
  in-memory-only registry would have passed every other test in that section and lost everything on
  restart.

### Still not done

- **No code has been decomposed.** M2 proper — the actual extraction into `core/`, `data/`,
  `state/`, `views/` — has not started. What is finished is the safety net that makes it survivable.
- The rendering layer itself is still untested and will stay that way; it is the part that should end
  up thinnest.

### Housekeeping

`.claude/skills/explain-simply/` added — Chandan wrote it after noticing he had asked four times for
the same kind of plain-language explanation. It arrived as a zipped `.skill` bundle, which the
loader cannot see; skills are discovered as `<name>/SKILL.md` directories, so it was unpacked in
place.

---

## 2026-07-28 (session 6) — the session-start health check stops lying to you, twice

### Completed

**BUG-018 fixed: `scripts/check_db.py` crashed instead of reporting, whenever its output was
redirected.** The report is drawn with box characters (═ ─ →). Sent straight to a terminal they
are fine; sent into a file, into Git Bash, or into anything that captures the output, Python falls
back to the machine's cp1252 regional encoding, which cannot write them — and the script stopped
with `UnicodeEncodeError` before printing one line. `main()` now widens the stream to UTF-8 first,
with a fallback that turns anything still unwritable into `?` rather than losing the report.

**First checks ever for this script — 5 tests (`tests/test_check_db.py`), 450 → 455.** It is the
first thing run in a session and had none.

**The tests launch a real subprocess, and have to.** Running the script inside pytest proves
nothing here: pytest substitutes its own output object, which handles these characters happily, so
the condition that breaks is never present. The tests therefore start the script as a separate
process with the output forced to cp1252 — the only arrangement in which the fault exists.
Generalised as **ADR-026: a test that owns only a function cannot see a fault that lives in the
process.**

**Proven by deliberate breakage — 3 faults injected, all 3 caught,** each by the test meant to
catch it: reconfiguring to cp1252 instead of UTF-8 (4 tests fail), leaving the fix in place but
never calling it from `main()` (the 2 subprocess tests fail, the unit tests stay green — so the
wiring is genuinely covered, not just the function), and removing the guard that tolerates a
stream it cannot reconfigure (1 test fails).

**BUG-019 fixed in the same sitting: "snapshots today" counted a UTC day, so it read `0` every
evening.** Found while verifying the first fix — the figure was 126 at 19:59 UTC and `0` an hour
later, same healthy database. UTC's day rolls over at 8pm New York time, and the count used
SQLite's `DATE('now')`, which is UTC. A `0` on the top line of the session-start check is exactly
what a dead collector looks like. It now counts the **Eastern trading day** and prints the date
next to the figure — `Snapshots today : 126  (2026-07-28 ET)` — so the boundary is never a guess.
Reasoning in **ADR-027**; the day window is computed in Python because SQLite has no timezone
support, and the half-open range still uses the existing timestamp index.

**7 more tests, 455 → 462.** Three drive the report end to end with a frozen clock (evening
snapshots counted, the previous evening excluded, non-COMPLETE snapshots still filtered out); four
pin the window itself, including both daylight-saving changeovers.

**Proven by deliberate breakage on both fixes — 7 injections, 6 caught, 1 proven equivalent.**
For BUG-018: reconfiguring to cp1252 instead of UTF-8, never calling the fix from `main()`, and
removing the guard for a stream that cannot be reconfigured — 3 of 3 caught. For BUG-019:
reverting to the old `DATE('now')` query, breaking the daylight-saving boundary, and dropping the
`status='COMPLETE'` filter — 3 of 4 caught, each by the test meant to catch it. The fourth is the
equivalent mutant described below, and it is counted here rather than quietly dropped.

### Discovered

**A belief written into a docstring turned out to be false, and a failed injection is what caught
it.** The BUG-019 reasoning claimed that computing the day's end as `start + timedelta(days=1)`
was a daylight-saving bug. Injecting exactly that changed nothing — 12 tests still passed. The
tests were not weak: arithmetic on a zoneinfo-aware datetime is **wall-clock** arithmetic, so it
lands on the next local midnight and is simply correct. The real trap is converting to UTC first
and *then* adding 24 hours; injecting that version did fail both DST tests. Docstring and ADR-027
corrected. **The generalisable part: an injection that fails to fail is evidence — it means a
belief about the code was never true of the code.**

**Checked and deliberately left alone:** a sweep found no other `DATE('now')` anywhere in the
project. `collector.py` uses `date.today()` for the option-chain fetch window, which is the
*machine's local* date — correct today only because this machine is set to Eastern, and harmless
at the edge of a fetch window. Not fixed, to avoid widening the task; recorded in ADR-027 §Scope.

**A correction to the session-start briefing.** It was first reported that the script "crashes in
a normal Windows console." That was wrong, and the distinction is the whole bug: in an interactive
console Python writes through the Windows console API and the characters are fine. The failure
needs the output to be redirected or piped. PowerShell here reports a UTF-8 stream and always
worked; Git Bash reports cp1252 and always failed.

**Two silently-dropped M1 tasks found and settled (ADR-028).** Reading `AUDIT_2026-07-25.md` §10
against `plan.md` showed the two files disagreeing about M1. Most of it is renumbering — execution
reordered the middle of the milestone and the commit messages follow `plan.md`, so those numbers
are the real ones. But two audit tasks had never happened and had simply stopped being mentioned:
**GitHub Actions CI** and **`docs/TESTING.md`**. Neither appeared in `decisions.md` or
`backlog.md`, and neither exists on disk. An omission nobody wrote down is indistinguishable from
an oversight, so both are now decisions rather than gaps:

- **CI:** the pre-commit hook satisfies the milestone. The criterion said "CI green" but the
  intent was *checks that run without being remembered*, and the hook delivers that. Its three
  real costs are written down rather than glossed — single machine, bypassable with `SKIP_TESTS=1`
  leaving no trace, and it checks what is *about to be* committed rather than what landed on
  `origin`. Revisit at a second machine, a second person, or M8.
- **`docs/TESTING.md`: retired, not deferred**, and the audit's MEDIUM rating withdrawn. It was
  specified for a repo with zero tests, where a written map was the only orientation available.
  There are now 462, and the conventions live in `conftest.py` and in each module's docstring,
  where they cannot drift from the code. Writing the document accurately would mean re-reading all
  462 tests, and keeping it accurate would mean doing so after every milestone — a large recurring
  cost for a second description of something already documented at the point of use. A summary
  that goes stale is worse than none, because it is believed.

**The audit is now marked as a frozen snapshot** at its head, and `plan.md` states that its own
numbering is the operative one. The audit has two commits in its entire history — created, then
moved — so it was never a living document; nothing said so, which is why the two read as
contradicting each other. Left frozen deliberately: its value is recording what was true and
intended on 2026-07-25.

**Stale lines in `plan.md` refreshed:** current milestone said M1 (complete since the 26th), test
count said 444, and "Close DEBT-014" was listed as a next action though the same file records it
closed sixty lines earlier.

### Verified

Full suite: **462 passed**, ruff clean on both changed files. The script was also confirmed
working from Git Bash with no environment override — the exact invocation that failed at the start
of the session — and now reports `Snapshots today : 126  (2026-07-28 ET)` against the real
database, where the same command returned `0` earlier in the same sitting.

**Still untested:** the daylight-saving behaviour is pinned by unit tests against a fixed clock,
not observed on a real changeover day — the next one is 2026-11-01. The `errors="replace"`
fallback in `_force_utf8_stdout` has no test that reaches it, because no stream on this machine
both accepts `reconfigure` and then fails to encode UTF-8.

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

### DEBT-014 closed — the scanner's price fallback is protected

**6 tests over a built chain** (`tests/test_scanner_golden.py`). The scanner falls back to the
midpoint of bid and ask when a contract has no stored price. Mutation-testing on 2026-07-26 found
the golden net did not protect that branch *at all* — the formula could be changed freely and
every test still passed.

**The lesson, which generalises: a characterization test can only protect what appears in its
output.** The captured snapshots did contain NULL-`mark` rows — 77 of 3,096 in snapshot 2608 — so
the branch genuinely ran. But none of those rows reached the top-50 result, so its output was
computed and discarded. Coverage would have shown the line as exercised. It was, and it was
protecting nothing.

**Built rather than captured.** The backlog offered either. A third capture would have needed the
production database opened and a snapshot hunted for the right shape, and would only have held for
as long as that snapshot kept producing those rows. A built chain states the contract directly and
in numbers checkable by hand: front legs quoted 9.00/11.00 and no stored price make the Diagonal
Mark exactly 30.00. Take the bid alone and it is 32; the ask alone, 28; a mean of three, 33.33.
There is no way to alter the midpoint and still land on 30.

**Proven closed rather than declared closed.** The same six mutations were re-run against the
golden fixtures alone and then against the full file: **5 of 6 that the fixtures MISSED are now
caught, zero survivors.**

### Corrected while doing it

- **My expectation about priceless legs was wrong.** I assumed a pair with no price would be
  dropped. It is listed with *empty* money columns instead. The tests pin the real behaviour,
  which is the safer of the two — a listed pair with no price is visibly incomplete, a silently
  missing one is not. What matters either way is that it is never valued at **zero**: a front leg
  at 0.00 makes the Diagonal Mark 50.00, the largest number on the screen, sorted straight to the
  top, describing legs nobody can trade.
- **Two paths, two outcomes, both safe.** An unparseable quote drops the pair entirely; a missing
  quote survives as NaN and yields a listed pair with blank values. Same condition, reported two
  ways. Neither fabricates a number, so neither was changed during M1 — recorded in the tests so
  the inconsistency reads as understood rather than accidental.
- **`DEBT-014` was assigned to two unrelated items** — the scanner gap (P2) and ~6 dangling
  documentation citations (P1). "Close DEBT-014" was therefore ambiguous. Closing the scanner row
  removes the collision; the surviving item keeps the ID that git history already references.

### M1 complete — `schwab_client.py` covered (M1.7, M1.8)

**45 tests, 100% statement coverage** (`tests/test_schwab_client.py`). This was the last non-UI
module at zero, and the most exposed to a change nobody here controls: Schwab can rename a field
without notice, and the failure would be silent — a renamed `volatility` yields None, rows are
skipped as "illiquid", and collection keeps reporting healthy while storing progressively less.
The tests write the expected response **shape** down as executable fixtures, so that change fails
loudly here instead.

**Mutation-verified: 14 injected faults, 14 caught, zero survivors.** Including the double
conversion (IV divided by 100 in both layers), the expiry key stored raw with its `:DTE` suffix,
strikes left as strings, a cached token ignored so a browser login fires on every cycle, and the
2 SD check quietly filtering instead of only warning.

**Three deliberate asymmetries are now pinned**, each of which reads like a bug until you know why:
IV stays a *percentage* at this layer because the ÷100 belongs to the collector and would
otherwise be applied twice; a **VIX** failure returns None while an **SPX quote** failure raises,
because VIX is context and the quote is the product; and the expected-move check only ever warns,
because a filter there would make the stored window depend on volatility.

**One quirk pinned rather than changed.** `_safe_float` treats 0.0 as "no value". For Schwab that
is usually right — an unquoted leg arrives as 0.0 and storing a real zero would drag every average
down. But a deep out-of-the-money option genuinely can be bid 0.00, and that real zero is
discarded too, reading downstream as "no quote" rather than "quoted worthless". Harmless for the
near-the-money strikes this strategy trades, consistent with the settled show-nothing-rather-than-
zero rule, and now written down where someone will find it.

**M1.8 met: 80% coverage of non-UI code** against a ~70% target. `iv_engine`, `db.py`,
`schwab_client.py` and `config.py` at 100%; `collector.py` at 56%, where the shortfall is
`main()`'s body, the logging setup and the single-instance lock — process wiring, not logic, and
unreachable from a test by construction.

**M1 is complete. 444 tests. M2 is unblocked.**

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
