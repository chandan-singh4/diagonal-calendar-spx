# progress_log.md — Chronological Development Log

Newest first. Every session appends an entry: what was completed, what was discovered,
what broke, and what remains.

---

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
kind of change from removing a `<div>` and deserves its own deliberate commit. Logged as DEBT-031.

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
