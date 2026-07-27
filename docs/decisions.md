# decisions.md — Engineering Decision Log (ADRs)

Every significant engineering decision, with reasoning, alternatives, tradeoffs, and date.
Newest first. Historical entries (ADR-001…012) were backfilled from `DEV_JOURNAL.md` and
`DOCUMENTATION.md` on 2026-07-25 — dates reflect when the decision was *made*, not when
it was recorded here.

---

## ADR-025 — A characterization test protects only what reaches its output
**Date:** 2026-07-26 · **Status:** ACCEPTED · **Closes:** DEBT-014 · **Constrains:** M2

**In plain terms:** before rearranging the code behind the opportunity screen, we captured what
that screen produces today so we can prove the rearranging changed nothing. That safety net is
narrower than it looks. It can only notice a change that shows up in the captured *result*. Code
that runs, produces a number, and has that number discarded before the screen is reached is not
protected at all — even though every coverage report will call it tested.

**How it was found.** Mutation-testing the scanner net: the bid/ask midpoint formula — used
whenever a contract has no stored price — could be changed to anything at all and every test
still passed. The captured snapshots *did* contain 77 such rows out of 3,096. The branch genuinely
ran. None of those rows survived into the top-50 the fixtures record.

**Decision:** where a branch matters but does not reach the captured output, add a **built**
fixture that asserts real arithmetic, rather than hunting for a real snapshot that happens to
expose it. Six such tests now pin the fallback. Front legs quoted 9.00/11.00 with no stored price
make the Diagonal Mark exactly 30.00; the bid alone gives 32, the ask alone 28, a mean of three
33.33. There is no way to alter the midpoint and still land on 30.

**Why built, not captured.** The backlog offered either. A third capture needs the production
database opened and a snapshot hunted for the right shape, and it protects the branch only for as
long as that snapshot keeps producing those rows — the protection would decay silently. A built
chain states the contract directly and cannot drift.

**Alternative rejected: trusting statement coverage.** It reported the line as exercised. It was.
Coverage answers "did this run", never "would anyone notice if it were wrong". Mutation testing
answers the second, which is the question a safety net is for.

**Consequence for M2.** The golden net makes a real but bounded promise: *the visible output does
not change*. It is not a guarantee that internal behaviour is preserved. Before moving a piece of
the scanner, check whether its effect actually reaches the captured result — and if it does not,
pin it with a built fixture first, or the move is unprotected however green the suite looks.

---

## ADR-024 — Measure the market minutes missed; stop guessing from the clock
**Date:** 2026-07-26 · **Status:** ACCEPTED · **Closes:** BUG-005

**In plain terms:** the collector only works when the market is open, so every night and
weekend there is a quiet period. Something has to decide whether a quiet period was normal or
whether the collector actually broke. It was deciding by looking at the clock and guessing. Now
it counts how many minutes of *open market* fall inside the gap. If the answer is zero, nothing
could have been collected and nothing was lost.

**Decision:** one function, `market_minutes_between()`, replaces three heuristics. It sums the
overlap of the gap with the 09:30–16:00 ET session of every trading day it touches; weekends
and holidays contribute nothing because they are not trading days. A gap is routine if and only
if it cost fewer than 3 market minutes.

**Why a 3-minute tolerance rather than zero.** It absorbs the collector's own cadence at the
session edges, and nothing else: the last write of the day lands at 15:59:xx (up to ~1.0 min
before the close) and the first of the morning at 09:30–09:31 (up to ~1.0 min after the open).
Worst case ~2.0 minutes that no configuration could have collected. 3.0 leaves margin without
masking anything meaningful — a genuine outage costing under 3 market minutes is less than one
MIDDAY poll cycle. Pinned by a boundary test so the constant cannot drift unnoticed.

### What was actually wrong — three defects, two of them unrecorded

The backlog described BUG-005 as "cries wolf": every routine gap reported as a fault. True, and
the least of it.

**1. False positives (recorded).** `start.time() >= 16:00 and end.time() < 09:30` never passed,
because the collector writes at 15:59 and restarts at 09:30–09:31. Every ordinary night was a
"fault".

**2. False negatives (NOT recorded — the dangerous direction).** `gap_minutes > 3600 →
MARKET_CLOSED` assumed any gap over 60 hours was a weekend. A collector dead from Monday
lunchtime to Thursday lunchtime loses three full trading days and was labelled routine — then
**suppressed**, because `_check_startup_gap()` returns early on routine verdicts and writes
nothing. The worst data-loss event this system can suffer left no trace at all. The holiday scan
did the same: any holiday anywhere inside a long gap labelled the whole outage HOLIDAY.

This inverts the severity. M3.4 plans liveness alerting on this classifier; the alarm was blind
to its own primary case. A noisy alarm is annoying, a blind one is worse than none.

**3. The classifier was not even the main culprit (NOT recorded).** `_classify_gap()` is only
reached from `_check_startup_gap()`, which runs once per collector *start* — and the collector
has run continuously since 2026-07-16. The detector that fires constantly is the mid-session one
in `main()`, which **hardcoded `reason="COLLECTOR_OFFLINE"` and never called the classifier at
all**. Since `prev_snapshot_ts` is not cleared when the market closes, the first cycle of every
trading morning compared against 15:59 the previous day and wrote a false row. Extracted to
`_midsession_gap_reason()` and routed through the classifier.

**Method note worth keeping:** defects 2 and 3 were found by writing the pinning tests *before*
the fix (ADR-019) and by reading the caller rather than trusting the write-up. Defect 3 in
particular means a fix aimed only at `_classify_gap()` — which is what the backlog prescribed —
would have left the largest source of bad rows untouched and looked successful.

### A fourth defect, found by running the fix against real data

Running the fixed classifier over the actual `collection_gaps` rows showed 22 of 47 still
labelled faults, all ~5 minutes at 15:25–15:31 ET. At 15:30 MIDDAY becomes CLOSE and the cadence
changes from 300s to 60s — so an ordinary 5-minute MIDDAY interval was judged against the new
60-second threshold (5.0 > 2.5) and recorded as a stall. Every trading day. That is more rows
than the overnight misclassification produced. The threshold now uses the **slower of the two
cadences**.

**Generalisation:** a threshold must be compared against the cadence that produced the data, not
the cadence in force when the comparison happens.

### Historical rows

`migrations/reclassify_collection_gaps.py` re-ran the fixed classifier over the existing rows:
19 of 47 reclassified to MARKET_CLOSED. The stored "snapshots lost" figures totalled **19,759 —
more than the database has ever held (2,605)**; the truthful total is 145. Previous values are
appended to `notes` rather than overwritten, so the migration is self-documenting and
reversible. It is idempotent and refuses to write without a verified backup.

**Not done, deliberately:** the ~22 session-change artefact rows are left in place. They record
non-events and arguably should be deleted, but removing rows from an audit log is a judgement
call, not a migration's business. Logged as OPS-008 for a deliberate decision.

---

## ADR-023 — Trade IDs are never reused, and a valuation is withheld rather than guessed
**Date:** 2026-07-26 · **Status:** ACCEPTED

Two choices made while fixing BUG-016 and BUG-014. Both are about the same thing: what the
software should do when it does not know something.

### 1. A deleted trade number is retired forever

**In plain terms:** if you delete trade T-003, the next trade is not T-003 again. The number is
gone for good and there is a permanent gap in the sequence.

**Why.** `get_next_trade_id()` used `COUNT(*) + 1`, which is a count, not a sequence. Delete any
trade that was not the newest and the next number collided with one still in use — and since
`trade_id` is the PRIMARY KEY, saving raised a raw sqlite error and lost the trade being
recorded. Reachable through a plan already committed to in STATUS.md: discard the six practice
trades, then record a real one.

**The alternative was to fill the gap** — find the lowest unused number and reuse it, keeping
the sequence tidy. Rejected. The journal is **evidence**: it exists to check real results
against predictions at M6, and screenshots, notes and `DEV_JOURNAL.md` entries all refer to
trades by ID. An ID that once meant one trade must never later mean a different one, or every
historical reference to it silently becomes a reference to something else. A gap in the
numbering costs nothing; an ambiguous identifier corrupts the record the whole project exists
to keep.

Compared as an INTEGER rather than as text, so `T-010` outranks `T-009` — which a string
comparison gets right only by luck of zero-padding, and not at all past `T-999`.

### 2. An incomputable valuation is withheld, not zero-filled

**In plain terms:** if a price is missing, the screen shows nothing rather than showing zero.

**Why.** `get_ic_marks()` did `r["mark"] or 0.0`, so a missing quote became a real-looking
`0.00`. That number went straight into `cost_to_close`, which is subtracted from
`profit_locked_in` to produce the unrealized P&L figure. The result was a **wrong money number
presented as a right one**, with nothing to distinguish it from a genuine zero mark — and a
far-OTM leg really can mark at 0.00, so the two cases are not theoretically separable after the
fact.

**Decision, in order of preference:** use the stored mark; failing that, fall back to the
bid/ask midpoint, exactly as every history query in `db.py` already did (DEBT-012 records this
as the one place that was missing it); failing *that*, return `None` for the whole valuation.
The function was already all-four-legs-or-nothing, so this is consistent rather than new.

**Tradeoff accepted:** the Journal will occasionally show no unrealized P&L where it previously
showed a confident wrong figure. That is the correct trade. This is the same principle already
recorded in BUG-010 — "a flawless record and a missing calculation look identical on screen"
— approached from the other side: there, a blank was ambiguous; here, a number was a lie.
Ambiguity is recoverable, a false figure is not.

**Generalisation, worth applying at M2:** truthiness tests on floats (`if x`, `x or default`)
are wrong wherever `0.0` is a legitimate value. Three sites were fixed here; BUG-007 records the
surviving one in `iv_engine.calendar_edge()`.

---

## ADR-022 — `INSERT OR IGNORE` is a data-loss risk, not just a logging gap
**Date:** 2026-07-26 · **Status:** ACCEPTED — **step 1 implemented same day**; step 2 remains M3.6 · **Revises:** ADR-004

**In plain terms:** the collector has a rule that stops it recording the same option contract
twice in one snapshot. The way that rule is written also tells the database *"if anything at all
is wrong with a row, throw it away quietly."* Not just duplicates — anything. And the collector
then reports every thrown-away row as successfully saved.

**What we believed (ADR-004):** `INSERT OR IGNORE` silences legitimate write failures, "with no
count and no log line." Logged as DEBT-008 at P1 — a **logging** shortcoming.

**What is actually true.** `OR IGNORE` is not scoped to uniqueness. SQLite applies the conflict
resolution to *every* constraint on the statement, so a `CHECK` or `NOT NULL` violation also
skips the row instead of raising. Verified directly against `sqlite3`: a plain `INSERT` of a row
with `right = 'CALL'` raises `IntegrityError`; the same row through `insert_option_rows()`
disappears without a trace. `insert_option_rows()` then compounds it by returning `len(rows)`,
which is computed *before* the statement runs.

**Why that combination is the dangerous one.** If Schwab ever changed its `right` convention from
`'C'` to `'CALL'` — exactly the kind of change an **unofficial community API wrapper** is prone
to, and `schwab-py` is pinned tightest in `pyproject.toml` for precisely this reason — then:

- every option row would be silently discarded;
- `insert_option_rows()` would return 3,096;
- `collector.log` would record a full, healthy cycle;
- `scripts/check_db.py` would show snapshots accumulating with `status = COMPLETE`;
- the only symptom would be charts that quietly stop moving.

The database is the product (STATUS.md). Prices missed are gone — the broker will not sell you
last Tuesday's. This is the one failure mode in the system that is both silent and unrecoverable,
which is why it is now **P0** rather than P1.

**Decision:** raise DEBT-008 to P0 and split the fix in two.

1. **DONE 2026-07-26, same session.** `insert_option_rows()` now returns
   `cursor.rowcount` — the rows actually stored — and logs a WARNING naming the shortfall
   and pointing at this ADR. This converts a silent, permanent data loss into a line in
   `collector.log`. It does not change what is stored, so it could not make anything worse.
   Verified load-bearing by reverting both halves on an isolated copy and confirming the
   suite fails (4 tests on the return value, 1 on the warning).
   **Residual, deliberately not changed:** `collector.py:565` still records
   `strikes_fetched = len(option_rows)` — the offered count — and discards this function's
   return value entirely. So the WARNING in the log is currently the ONLY signal. Correcting
   the collector's own reporting changes what is written to the `snapshots` table and was
   outside the approved scope.
2. **At M3.6, as planned:** decide per-constraint behaviour deliberately — keep `OR IGNORE`
   for the genuine duplicate case, let anything else raise.

**Not fixed in this session on purpose.** ADR-019 governs: the fault was recorded first and is
pinned by tests (`test_insert_option_rows_silently_discards_a_row_failing_the_check`,
`test_insert_option_rows_keeps_the_good_rows_when_one_is_bad`) so that the fix later lands as a
visible, deliberate change to an existing test rather than a test written to fit a fix.

**Alternative considered and rejected:** drop the `CHECK(right IN ('C','P'))` constraint, since
`OR IGNORE` makes it unenforceable through the write path anyway. Rejected — the constraint is
real and does fire on a plain `INSERT` (pinned by
`test_option_right_check_constraint_rejects_anything_but_c_or_p`); the defect is the conflict
clause, not the constraint. Removing it would delete the evidence rather than the fault.

---

## ADR-021 — A break-even trade is neither a win nor a loss
**Date:** 2026-07-26 · **Status:** ACCEPTED

**In plain terms:** a trade that comes out at exactly £0 — a "scratch" — used to be filed as a
loss. That made the *average loss* look smaller than it really is, because a zero was being
averaged in with the real losses. It happens for real: a transformed trade whose locked-in
credit exactly cancels its assignment cost.

**Decision, and where a scratch now counts:**

| Statistic | Scratch counts? |
|---|---|
| Win Rate | **In the denominator only** — it's a completed trade that wasn't won |
| Average Loser | **No** — averaging in a zero understates the typical loss |
| Profit Factor | No (it never actually affected this — adding zero changes no sum) |
| Expectancy | Yes, as a zero, via the plain mean |

**On Expectancy specifically.** It used to be computed as
`win_rate × avg_win + (1 − win_rate) × avg_loss`. That formula only works when every trade is
either a win or a loss — with scratches present, the `(1 − win_rate)` part sweeps them in *at
the average loss*, making the strategy look worse than it is. It's now just the plain average
outcome per trade, which is what expectancy means. **This changed no existing number:** the two
are algebraically identical when there are no scratches, and a test asserts exactly that.

**Why this is written down rather than left as a fixed bug:** M6 judges the strategy against
these numbers, so "what counts as a win" is a definition the results depend on, not an
implementation detail. If the win-rate convention is ever revisited, this is the decision being
revisited.

*(Correction to the original bug note: it claimed the scratch also inflated Profit Factor. It
did not — adding a zero leaves the sum of losses unchanged. Average Loser and Expectancy were
the only figures actually affected.)*

---

## ADR-020 — The scanner "golden" test proves the numbers didn't change, not that they're right
**Date:** 2026-07-26 · **Status:** ACCEPTED

**In plain terms:** M2 is about to move the scanner's code to a new home. The danger isn't
that it breaks loudly — you'd spot that. It's that it comes out *slightly* different, and
you trade off a screen that quietly shifted, with nothing to compare against.

So before touching it, we ran the current scanner over two real days of stored market data
and saved exactly what it produced. After the move, the test replays the same days and
demands the same answers.

**What it does NOT say:** that today's answers are *correct*. Nobody has validated that yet
— that's M6's job, checking the strategy against real logged results. Writing a test that
claimed correctness would mean inventing an expectation and freezing a guess into the suite,
which is worse than no test at all: it would *look* like validation while being none.

**The rule that matters:** if that test ever goes red, it is doing its job. Either you meant
to change the scanner — then re-run `scripts/capture_scanner_golden.py` and review the diff
as a deliberate change — or you didn't, and it just caught the exact bug it exists to catch.
**Re-capturing to make a red test go green throws the whole protection away.**

**Known gap:** the saved days don't exercise the "estimate the price from the bid and ask"
branch, so that part isn't protected yet (DEBT-014).

---

## ADR-019 — When putting tests around money code, pin the bugs; don't fix them in the same pass
**Date:** 2026-07-26 · **Status:** ACCEPTED

**In plain terms:** while writing tests for the profit-and-loss maths we found five real
defects. We wrote tests that lock in the *current, wrong* behaviour and cross-referenced each
to a backlog ID, rather than fixing them there and then.

**Why, when fixing looks obviously better:** if you change the code and write its test in the
same breath, a passing suite tells you nothing. You can't tell whether the test proved the
code right or the code was quietly bent until the test went green. Doing it in two steps
means the fix arrives as a visible, reviewable change to a test that already existed — and
you can see the exact behaviour that changed.

This matters most precisely where it's most tempting to skip: code that decides what a trade
made. Applies to any future test-then-fix work on P&L, fills, or fees.

---

## ADR-018 — The collector "running twice" is one collector; stop investigating it
**Date:** 2026-07-26 · **Status:** ACCEPTED — ruled out, do not re-open

**In plain terms:** Task Manager shows two `python.exe` processes for the collector. It looks
like it started twice. **It didn't — that's one collector.**

The `python.exe` inside `.venv\Scripts\` isn't really Python. It's a small launcher (241 KB)
created by the `uv` tool that turns around and starts the *real* Python (91 KB, kept under
`AppData\Roaming\uv`) as a second process underneath it. Both show the same command line, and
they appear a second or two apart, which is exactly what a genuine double-start would look
like.

**How it was confirmed:** the second process's parent is the first, and the two run different
executables — checked with `Win32_Process` on `ParentProcessId` and `ExecutablePath`. There is
also only one thing that ever launches it (a Startup-folder shortcut): no scheduled task, no
registry Run entry.

**Why this is written down:** this cost time twice — Chandan and an earlier session both tried
to work out how to "stop one of them", and it was a suspected cause of the unexplained July
issue (BUG-001). It is not. **BUG-001 remains open with no leads, and the duplicate is not a
lead.** Seeing two entries in Task Manager is normal and healthy.

Separately, a single-instance lock was added to `collector.py` — not to fix this non-problem,
but because a *real* double-start (double-clicking the launcher while it's already running)
was genuinely unguarded, and two collectors would poll twice and write overlapping data.

---

## ADR-017 — `backlog.md` holds only open items; git is the archive
**Date:** 2026-07-26 · **Status:** ACCEPTED

**In plain terms:** the backlog was growing forever. Fixed bugs stayed on the page, and a
"Recently Completed" list grew at the bottom. Every session then paid attention — and tokens —
re-reading things that no longer needed any.

**Decision:** when an item is done, **delete its row.** The file's length now tracks work
*outstanding*, not work *ever done*.

**Why deleting is safe:** git already stores every past version of the file, so a closed
item's full text is always recoverable:

```
git log -S "BUG-011" -- docs/backlog.md
git show <sha>:docs/backlog.md
```

A separate archive file was considered and rejected — it would be a hand-maintained copy of
something git already does perfectly, and it would grow forever too, just somewhere else.

**The one real exception.** Git preserves *what* the row said, but not the thinking that made
the fix make sense. If closing an item leaves a lesson, a constraint, or a ruled-out theory
that would otherwise be painfully re-derived months later, write a short plain-English ADR
here **before** deleting the row. ADR-018 is exactly that case: the bug row is gone, but
without the note someone would spend another afternoon hunting a second collector that was
never there.

**Not changed:** `progress_log.md` is meant to be an append-only record of what happened, and
`decisions.md` entries are immutable by design. Both are working as intended.

---

## ADR-016 — Entry IV context must be decoupled from `option_rows` before any pruning
**Date:** 2026-07-25 · **Status:** OPEN — blocks M3.2
**Updated 2026-07-26 — severity downgraded, requirement unchanged.**

> **Update:** the user intends to discard the 6 existing trades and start the journal
> clean. That removes the *urgency* — there is no historical entry-context to preserve,
> so pruning can proceed without a backfill and without racing a closing window.
>
> **The design requirement stands, though, and the trap is now a future one:** any trade
> logged from here on has the same dependency. Log a trade today, let its expiries pass,
> run the pruner, and its entry-time IV context is gone — silently, and exactly for the
> completed trades M6.2 needs. So the fix must land **before the journal starts
> accumulating trades that matter**, not before the first prune.
>
> Revised sequencing: implement option (b) — snapshot entry IV context into `trades` at
> logging time — as part of M3, alongside the pruner rather than ahead of it. No backfill
> required.

**Decision:** Do not implement retention pruning (ADR-014) until entry-time IV context is
either (a) carved out of the pruner, or (b) snapshotted into the `trades` table at logging
time. Recommendation: **(b)**.

**Reasoning:** `db.get_entry_iv_context()` reconstructs entry-time term structure for
logged trades by reading **at-strike `option_rows` from historical snapshots**, and
`DOCUMENTATION.md` §11.4 advertises this as working retroactively. Pruning `option_rows`
by expiry date would destroy this for every trade whose expiries have passed — i.e. every
*completed* trade, which is precisely the population the Regime Analysis 2×2 (M6.2) exists
to study. That analysis is the designated mechanism for resolving the §3.1 IV-ratio
hypothesis. `atm_iv_by_expiry` does not substitute: it stores ATM only, while the analysis
explicitly requires IV at the strikes actually traded.

**Alternatives considered:**
- *Carve-out:* never prune rows whose `(expiry_date, strike)` appears in `trades`. Works,
  but leaves the pruner coupled to the journal schema and grows more complex over time.
- *Snapshot at logging time:* copy front/back IV at the traded strikes into `trades` when
  the trade is recorded. Makes the trade record self-contained and lets the pruner stay
  simple and unconditional.
- *Don't prune at all:* rejected — accepts the ~20 GB/yr trajectory (ADR-014).

**Tradeoff:** Option (b) requires a `trades` schema migration and a backfill for the 6
existing trades (which is still possible *today*, because nothing has been pruned yet —
this window closes the moment pruning runs).

---

## ADR-015 — Defer the repo-wide formatter run until after M1
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Add `ruff`/`black`/`mypy` configuration in M0, but do **not** run the
formatter across the codebase until the M1 test suite exists.

**Reasoning:** `black` is semantically safe in principle, but reformatting 9,628 lines with
zero automated tests means any accidental change is undetectable. The cost of waiting is
low; the cost of a silent formatting-induced defect in P&L code is not.

**Alternatives:** Format now in one isolated commit (as originally scoped in the audit);
never adopt a formatter.

**Tradeoff:** Style stays inconsistent for one more milestone. Accepted — the ordering
follows the same "tests before change" principle as the whole M0→M1→M2 path.

---

## ADR-014 — Retention policy: prune `option_rows` past expiry, keep `atm_iv_by_expiry` forever
**Date:** 2026-07-25 · **Status:** Accepted (implementation blocked by ADR-016)

**Decision:** Delete `option_rows` for expiries more than N days past expiration. Retain
`atm_iv_by_expiry` indefinitely.

**Reasoning:** Measured growth is ~82 MB per trading day (~20 GB/yr) with no policy at all.
`atm_iv_by_expiry` is only 3.7 MB for a full month — ~0.2% of the database — and powers most
historical charts. Pruning the per-strike detail for expiries that have already settled
should cut steady-state size by roughly an order of magnitude.

**Alternatives:** Archive to a separate `history.db`; downsample old snapshots to hourly;
keep everything and buy disk.

**Tradeoff:** Per-strike granularity for past expiries is lost permanently — which is
exactly what ADR-016 constrains.

---

## ADR-013 — Dashboard: phased, decision deferred to M5.0
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Extract and test a framework-agnostic `core/` (M2), wrap it in FastAPI (M4),
then re-evaluate Streamlit against evidence at M5.0. Do not pre-commit to a rewrite.

**Reasoning:** The evidence that Streamlit has been outgrown is strong and comes from the
project's own docs — every §15.4 performance fix was a workaround for the rerun model, and
all four items in §15.9 are deferred limitations rather than solved problems. But a rewrite
now fixes none of the HIGH-severity findings, and with zero tests there would be no way to
verify the new UI computes the same numbers as the old one, in a tool that drives real money.
`core/` extraction is valuable in every possible outcome and cannot be wasted work.

**Alternatives:** Commit to React immediately; stay on Streamlit permanently; move to
Dash/Panel (rejected as a lateral move — still Python-renders-HTML, full rewrite cost for a
partial win).

**Tradeoff:** Chart-reset and interactivity pain persist through M2 and are not fixed until
M5, if ever.

---

## ADR-012 — Access model: local machine, readable from phone/tablet
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Single machine, but reachable from mobile over LAN/Tailscale. Not multi-user.

**Reasoning:** Enables remote monitoring while staying consistent with `DOCUMENTATION.md`
§8.4, which lists multi-user/SaaS under "Do Not Build."

**Consequences:** Promotes M4 (FastAPI) from optional to expected; moves Tailscale from M8
into the M3–M4 window; adds a hard requirement that Streamlit binds localhost only and is
never exposed directly to the LAN.

---

## ADR-011 — Delete superseded documents rather than archiving them
**Date:** 2026-07-25 · **Status:** Accepted (user decision, over a recommendation to archive)

**Decision:** Delete `AUDIT_REPORT_2026-06-25.md` and `spx_dashboard_implementation_plan.md`
outright rather than moving them to `docs/archive/`.

**Reasoning (user):** Keep the repository root clean; both are historical and recoverable
from git.

**Recommendation that was overridden:** Archiving was advised because the June audit is
cited as the evidentiary basis for the IV-ratio retraction in `DOCUMENTATION.md` §3.1, §9.4,
the v1.1 changelog, and three `iv_engine.py` docstrings (lines 81-84, 110-111, 360-361).

**Mitigation:** Recovery SHAs recorded — June audit `6329fa28`, implementation plan
`15d1e919` (both reachable from HEAD `bfc78c06`). `backlog.md` DEBT-014 tracks rewriting the
dangling citations during the M2 documentation reconciliation.

**Tradeoff:** ~6 citations now point at files absent from the working tree until DEBT-014
is done.

---

## ADR-010 — Backups use SQLite's online backup API, not file copy
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Back up via `sqlite3.Connection.backup()` to a directory **outside** the repo.

**Reasoning:** The collector runs continuously and the database is in WAL mode. A plain file
copy of a live WAL database can capture a torn state. The backup API is transactionally
consistent against an active writer, so backups need no downtime. Storing them outside the
repo means a `.gitignore` regression can never cause a 1.8 GB commit.

**Alternatives:** `VACUUM INTO` (also consistent, but rewrites/compacts and is slower);
file copy with the collector stopped (needs downtime, risks a missed session).

---

## ADR-009 — Fix `.gitignore` corruption at the encoding level
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Rewrite `.gitignore` as UTF-8/LF with an explicit warning comment about
PowerShell encoding.

**Reasoning:** The file was UTF-8 with UTF-16LE bytes appended by PowerShell's
`Add-Content`/`Out-File`, which default to UTF-16LE. The intended `pinned_pairs.json` entry
was stored null-separated and matched nothing, so five runtime-state files were tracked for
weeks without anyone noticing. Fixing only the entry would leave the trap for the next
append. The comment documents `Add-Content -Encoding utf8` as the safe form.

---

## ADR-008 — Priority order M0 → M1 → M2, strictly sequential
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Cleanup, then tests, then decomposition. No feature work until complete.

**Reasoning:** Refactoring 4,230 lines without tests is how working systems break; building
tests on top of an untracked, unpinned, unbacked repo is building on sand. The ordering is
forced by dependency, not preference.

**Tradeoff:** Feature work and the open v4.1.1 report are deferred.

---

## ADR-007 — Remove the holiday `values` rangebreak from Plotly charts
**Date:** 2026-07-07 · **Status:** Accepted · *(backfilled)*

**Decision:** `_SESSION_RANGEBREAKS` keeps only `bounds=["sat","mon"]` and
`bounds=[16, 9.5], pattern="hour"`. No per-date breaks of any kind.

**Reasoning:** ANY per-date rangebreak corrupts Plotly's point positioning for all data
rendered after it — ghost lines, out-of-order hover, dead tooltips. Both variants
(`values=[dates]` and per-day `bounds=[...]`) were tested and both fail.

**Alternatives:** Keep holiday collapsing and accept corruption (rejected); pre-filter data
server-side (not attempted — the NaN line-breaker in ADR-006 covers the visual need).

**Tradeoff:** Market holidays now render as one session-width of blank space instead of
being collapsed. Accepted: correct rendering beats collapsed holidays. Affects a few days
per year.

---

## ADR-006 — Reinstate `_break_sessions()` NaN line-breaker
**Date:** 2026-07-07 · **Status:** Accepted · *(backfilled)*

**Decision:** Insert a NaN row wherever consecutive points gap by more than 60 minutes.

**Reasoning:** Rangebreaks collapse empty axis *space*; the NaN breaker breaks the *line*
across whatever space remains. Complementary, not redundant — without it, Plotly draws a
straight connector across holidays and outages.

**Known gap:** Not yet wired into the selected-strike IV chart (tracked as DEBT-009).

---

## ADR-005 — Change-triggered fragment poller replaces blind autorefresh
**Date:** 2026-07-03 · **Status:** Accepted · *(backfilled)*

**Decision:** Replace `st_autorefresh` with an `st.fragment(run_every=…)` poller that reruns
only when `snapshot_id` changes.

**Reasoning:** Blind autorefresh forced a full-page rerun every 60–300 s regardless of new
data, resetting Plotly zoom/pan mid-analysis.

**Tradeoff:** When a new snapshot *does* land the rerun is still full-page, so charts still
reset at that moment. True in-place updates need charts inside fragments — deferred.
**This limitation is a primary input to the M5.0 Streamlit re-evaluation.**

**Follow-on defect:** The first implementation caused an infinite rerun loop on fresh
sessions (the compared key was set further down the script); fixed by adopting the latest
snapshot silently on first check.

---

## ADR-004 — `UNIQUE(snapshot_id, expiry_date, strike, right)` + `INSERT OR IGNORE`
**Date:** 2026-07-03 · **Status:** Accepted · *(backfilled)*

**Decision:** Add a unique index on `option_rows`, dedupe once, and switch inserts to
`INSERT OR IGNORE`.

**Reasoning:** `option_rows` had no uniqueness guarantee, so a re-fetch could store the same
contract twice per snapshot. Six-leg `LEFT JOIN` history queries fanned those duplicates
out, rendering as a regular sawtooth and slowing far-OTM pairs.

**Tradeoff (now a known issue):** `INSERT OR IGNORE` also silences *legitimate* write
failures — a genuine constraint violation is discarded identically to a benign duplicate,
with no count and no log line. Tracked as DEBT-008.

---

## ADR-003 — Read-only, non-committing dashboard connections
**Date:** 2026-07-03 · **Status:** Accepted · *(backfilled)*

**Decision:** `_make_conn(read_only=True)` sets `PRAGMA query_only = ON`; `get_conn` does not
commit; the 20 pure-read functions were repointed from `managed_conn` to `get_conn`.

**Reasoning:** Every `SELECT` was running a pointless `conn.commit()` through a write-oriented
context manager. `query_only` makes a dashboard write *physically impossible* rather than
merely discouraged — the reader/writer split becomes enforced, not conventional.

**Bug found en route:** `delete_trade` (a genuine `DELETE`) was wrongly using the read context
manager.

---

## ADR-002 — Custom session-state tab bar instead of `st.tabs()`
**Date:** 2026-06-30 · **Status:** Accepted · *(backfilled)*

**Decision:** Build the six-tab navigation from styled buttons in a keyed container.

**Reasoning:** `st.tabs()` exposes no API to switch the active tab programmatically, which
Mission Control's "→ View Chart" drill-down requires.

**Tradeoff:** Hand-rolled navigation plus the `pending_*` → `*_select` promotion pattern to
work around Streamlit's "cannot modify widget after instantiation" restriction. **Another
input to the M5.0 re-evaluation.**

---

## ADR-001 — Retract the IV-ratio favorability claim; demote to HYPOTHESIS
**Date:** 2026-06-25 · **Status:** Accepted · *(backfilled — the project's most consequential decision)*

**Decision:** Retract the claim that IV ratio < 1.0 is "favorable" and "maximizes
transformation credit." Demote regime favorability to an explicitly unvalidated
`HYPOTHESIS`. Correct the inverted backwardation/contango terminology. De-valence the
dashboard's regime colors.

**Reasoning:** The claim rested on a single paper trade (Category D evidence). Black-Scholes
analysis suggested the *opposite* structural relationship: higher front IV means more
extrinsic value on the short legs, hence more decay to harvest.

**Alternatives:** Keep the original claim (rejected — unsupported); flip to the opposite rule
(**explicitly rejected** — a handful of modeled scenarios with assumed IV paths is not
sufficient to install the reverse as proven either).

**Tradeoff:** The dashboard offers no validated entry signal, only neutral context. Accepted
deliberately: honest uncertainty beats a confident wrong rule.

**Validation mechanism:** the Regime Analysis 2×2 (M6.2), which needs ~20+ trades. Currently
6. **This is the decision that ADR-016 exists to protect.**

> **Standing rule established here:** no claim enters `DOCUMENTATION.md` as fact using words
> like *confirmed / proven / favorable / optimal / maximizes* unless it is mathematically
> derived (with the derivation shown) or backed by a stated minimum sample size.
