# decisions.md — Engineering Decision Log (ADRs)

Every significant engineering decision, with reasoning, alternatives, tradeoffs, and date.
Newest first. Historical entries (ADR-001…012) were backfilled from `DEV_JOURNAL.md` and
`DOCUMENTATION.md` on 2026-07-25 — dates reflect when the decision was *made*, not when
it was recorded here.

---

## ADR-046 — The a.m. and p.m. third-Friday options are two different contracts, and the record now says which
**Date:** 2026-08-19 · **Status:** Accepted

**Context:** On the third Friday of each month SPX lists two options for the same date and
strike: the traditional monthly, which settles at the OPENING price and stops trading the
evening before, and the weekly (SPXW), which trades all day and settles at the CLOSE. On expiry
day one is already finished while the other has a full session of decay left — for a strategy
built on the decay gap between two expiries, they are not interchangeable.

Schwab returns both under one expiry key. `chain_to_dataframe` discarded the contract symbol —
the only field distinguishing them — and `uq_option_rows_contract(snapshot_id, expiry_date,
strike, right)` had no room for the difference, so `INSERT OR IGNORE` dropped the second.
**Exactly 160 rows per cycle, 2,181 identical warnings, for the whole life of the project.**
The steady number was itself the evidence: 160 = 80 calls + 80 puts = precisely one expiry.

**Decision:**
1. **Store both.** A `settlement` column on `option_rows` holds `'AM'`, `'PM'`, or NULL, read
   from Schwab's `settlementType` and falling back to the `SPXW` symbol root.
2. **NULL means "not recorded", never "a.m."** Every pre-2026-08-19 row is NULL.
3. **Uniqueness now spans settlement**, via `COALESCE(settlement, '?')` — SQLite treats each
   NULL in a UNIQUE index as distinct, so indexing the bare column would have stopped
   deduplicating the legacy rows and reopened the six-leg fan-out of ADR-022.
4. **Every existing read shows one contract per (expiry, strike, side): the a.m. one where an
   a.m. one exists, otherwise the p.m. one.** `atm_iv_by_expiry` is computed from that same
   slice. Nothing on screen changes. Showing the shadowed p.m. prices is a separate, deliberate
   decision.
5. **That rule is read off the data, never from calendar arithmetic** — a `NOT EXISTS` lookup
   for an a.m. twin at the same expiry, strike and side. It needs no list of third Fridays, no
   holiday table, and it stays correct on a monthly whose a.m. contract does not list every
   strike the p.m. one does.

**Why the readers had to be pinned in the same change:** `atm_iv_by_expiry` stores one row per
expiry per snapshot and has **no uniqueness constraint**. An unguarded grouping would have
written two conflicting rows for every third Friday and silently corrupted the term structure
the whole analytics layer rests on. Storing the p.m. contract without pinning the readers would
have been a worse bug than the one being fixed.

**Amendment, same day — the legacy rows ARE attributable, at read time.** This ADR originally
held that the pre-2026-08-19 rows could never be sorted out. That was too pessimistic. The
attribution is deterministic: a weekly only ever had one contract (p.m.); on a monthly, a row
recorded *before* expiry day is the a.m. contract, and one recorded *on* expiry day is the p.m.
one. Verified by matching the unlabelled 21 Aug rows against both labelled contracts on open
interest — which does not move intraday, so it identifies a contract independently of price:
**170 of 170 matched a.m., 0 matched p.m.** This is applied **when reading, never by rewriting
the stored rows** — the stored NULL stays honestly "not recorded", the derivation stays visible
and arguable, and nothing about it is irreversible. Point 2 above still stands unchanged: NULL in
the column means "not recorded".

**Alternatives rejected:** *Stamp the legacy rows 'AM'* — wrong on precisely the day that
matters, because on each past expiry day the a.m. contract had already settled out of the chain
and the p.m. one took the slot unnoticed (BUG-024). *Store only the p.m. contract* — discards
the a.m. contract's much larger open interest and breaks every existing chart. *Keep dropping
one and document it* — the loss is permanent and unrecoverable; every cycle deferred is p.m.
data that cannot be bought back.

**The near-miss worth recording, because the first attempt shipped it.** Point 4 was first
written as `settlement IS NOT 'PM'`, on the assumption that p.m. was the *extra* contract. It is
the reverse: almost every SPX expiry is an SPXW weekly and therefore p.m.-settled, and the a.m.
contract exists **only** on the third-Friday monthly. That guard threw away ~94% of the chain.
It reached the live collector, which reported `ATM IV computed for 1/20 expiries` for three
cycles before the post-deployment check caught it (BUG-026). **The lesson: a guard phrased as
"exclude the special case" only works if you have correctly identified which case is special.**
Phrased as "prefer the a.m. contract where one exists" the rule cannot make that mistake,
because it names what to keep rather than what to drop.

**Tradeoff:** p.m. history begins 2026-08-19 and there is no way to obtain any earlier. The
database grows ~5% faster (roughly 160 extra rows per cycle on ~3,000).

**Rehearsed, not assumed:** run against a consistent read-only copy of the real 2.7 GB file —
**36.2 s, 14,305,769 rows before and after, `PRAGMA integrity_check` ok.** Adding the column is
O(1) in SQLite; rebuilding the index is the only part that reads the table, and it changes no row.

**Deployment order is load-bearing:** only `collector.py` calls `init_db()`, so the collector
must be restarted — which runs the migration — **before** the dashboard serves the new code.
Opening the dashboard first against an unmigrated database fails with `OperationalError: no
such column: settlement`.

---

## ADR-045 — The watchdog runs outside the dashboard, and "no news" is not "all clear"
**Date:** 2026-08-09 · **Status:** ACCEPTED · **Closes M3.4** · **Supersedes nothing**

**THE DECISION.** Collector liveness is monitored by `scripts/watchdog.py`, run every 10 minutes
by Windows Task Scheduler, alerting via a desktop notification and email. It **observes only** —
it never writes to the database, never restarts the collector, and never re-authenticates.

**WHY, IN ONE FACT.** This session opened with the Schwab token expired and the collector
recording nothing. The dashboard has had a full-width red `TOKEN EXPIRED` banner since M1
(`ui/header.py::render_token_banner`) and **it was working perfectly**. Nobody was looking at it.
The markets were shut, so nothing was lost; Monday would have cost a session, and the broker will
not sell you last Tuesday's prices.

So the rule this is built around: **an alarm that can only reach you through a page you have to
open is not an alarm.** The banner is not removed — it is right, and it is free — but it is not
the monitoring, and M3.4 could not be closed by improving it.

**WHY IT ONLY WATCHES.** A watchdog that restarts things is a second unattended process that can
go wrong, and this one is meant to be the thing trusted when everything else is suspect. Deciding
what to do about a dead collector stays with Chandan. The cost is real — a 3 a.m. crash is not
self-healing — and that is accepted deliberately; it belongs to M8, with a supervisor, not to the
thing whose job is to tell the truth.

**MOST OF THE WORK WAS IN NOT CRYING WOLF.** A muted alarm is worse than no alarm, because you
believe you are covered. Four ways a naive version fires when nothing is wrong, all handled:

| Case | Naive behaviour | Handled by |
|---|---|---|
| Overnight, weekends, holidays | Alarms every night | `core.session.session_of` returns None = market shut |
| 09:32, before the first cycle | Alarms every morning — the newest price is legitimately yesterday's close | `WATCHDOG_OPEN_GRACE_MINUTES` |
| The moment before each poll lands | Age *reaches* the interval every cycle; that is the cadence working | `WATCHDOG_LATE_MULTIPLE` = 2.5, so two missed cycles, not one slow one |
| One outage, a 10-minute schedule | 6 emails an hour | `WATCHDOG_REALERT_MINUTES` = 60, plus one explicit all-clear |

**THE FALSE ALL-CLEAR — the one that would have mattered most.** Found because Chandan asked
whether a 10-minute schedule meant an email every 10 minutes; explaining why it did not exposed
it. At 16:00 the market shuts and the check starts answering *"ok — market closed"*. A collector
dead all afternoon would therefore flip from alarming to ok, and the watchdog would send
**RECOVERED: prices are arriving again**. They were not. The market had closed and the watchdog
had stopped being able to see.

A false all-clear is the worst thing an alarm can say, because it is precisely the message that
stops you looking. The two blind states — market shut, and the opening grace period — now return
`informative=False`, meaning *"I have no news"*, not *"all is well"*; the alert decision and the
saved state both leave the alarm exactly as they found it. **An all-clear now requires positively
observing fresh data.** Pinned by four tests, and the fix was reverted on a copy to confirm two of
them fail without it rather than passing by construction.

**A SECOND BUG, FOUND BY PROBING BEFORE ANY TEST EXISTED.** Called with a past timestamp, `check()`
reported *"collecting normally — newest price -2001584s old"*. A price newer than now means the
clock is lying, and **every judgement this script makes is a comparison against a clock** — so a
wrong clock makes the reassuring answers meaningless too, not just the alarming ones. Now an alarm
in its own right.

**core/session.py — WHY THE SESSION LOGIC MOVED.** Chandan's threshold for the dashboard ("over 5
minutes midday, over a minute in the first and last half hour") is not a second policy that
happens to agree with the collector's polling interval. **It IS the collector's polling interval.**
Two copies of a number that must agree is a number that will eventually disagree, and the
disagreement shows up as a dashboard that either cries wolf or stays quiet through a real outage.
So the boundaries and intervals left `collector.py` for a pure `core/` module, handed their
holiday set because `core/` may not import `config`. Verified by sabotage: changing a boundary on
a copy failed **both** the new test and the pre-existing collector test, which is the evidence
that the collector genuinely delegates rather than keeping a copy.

**THE HEADER COUNTDOWN WAS REPLACED, NOT DECORATED (Chandan's call).** `⏱ Next update in: 42s`
became a ticking wall clock plus **Time since last data**, counting upward. The countdown ran
*toward* a moment rather than away from one, so its worst case — collector dead, no price for an
hour — displayed `0s` and sat there, which reads like everything is fine. Counting upward has no
such resting state: the longer it is broken, the louder the number gets.

One deliberate departure from what was asked, and it was reported rather than done quietly: the
number turns **amber** at Chandan's threshold and **red** at 1.5× it. At a 300-second cadence the
age reaches 300 seconds immediately before every new price lands, so turning red exactly on the
threshold would flash red once per cycle, all day — the muted-alarm failure again, in miniature.

**AND WHAT THE WALL CLOCK DOES NOT PROVE, documented where it lives.** It ticks in the browser, so
it would keep ticking if the dashboard's Python engine died behind it. It proves the tab is not
frozen. It does not prove the data is fresh — which is why the age sits beside it, and why neither
replaces the watchdog.

**ALTERNATIVES REJECTED.**
- *A louder dashboard banner* — repeats the exact failure of 2026-08-09.
- *A market-aware schedule* (only run 09:25–16:05 on weekdays) — puts a second copy of the market
  calendar somewhere nobody updates each January. A dumb schedule and a smart script instead; the
  script already knows and stays silent. It also means a collector that died at 02:00 is reported
  at 09:35 rather than found at lunchtime.
- *A notification library* — an alerting path with a dependency breaks on the day the environment
  is rebuilt, which is a plausible day for the collector to be down too. Uses only what ships with
  Windows, with a message box as the fallback.
- *Restart-on-failure* — see above; belongs to M8.

**CREDENTIALS.** Email settings read from `.env` (gitignored); no password is stored in the
repository or printed anywhere. An empty mailbox is a valid choice and skips email quietly, but a
**half-configured** one refuses to send and says so loudly — believing you are covered when you
are not is the failure this whole ADR is about. Gmail requires an App Password; ordinary Google
passwords are rejected from scripts.

**STATUS AS SHIPPED.** Desktop and email paths both verified live with `--test-alert`; the
schedule verified firing (`LastTaskResult 0`). **Not yet proven end to end:** a real outage
travelling all the way to the phone. That cannot be manufactured without stopping the collector or
tampering with the database, and both need Chandan's word. The first genuine outage is the test.

---

## ADR-044 — Retention policy: 90 days past expiry, summaries forever, deletion never automatic
**Date:** 2026-08-09 · **Status:** ACCEPTED · **Closes M3.1** · **Unblocks M3.2**

**THE POLICY.** `option_rows` is deleted for an `expiry_date` more than **90 days** in the past.
`atm_iv_by_expiry`, `snapshots` and `collection_gaps` are **kept forever**. Deletion is **never
automatic** — no scheduled task, no collector hook, no app trigger. It runs only when Chandan
runs it, and it reports what it would delete before it deletes anything.

Chandan chose both numbers with the alternatives in front of him (30 / 90 / 365 / archive-instead,
and manual / scheduled / dry-run-only). 90 days and manual are the middle of each range.

**WHY PER-STRIKE DETAIL IS THE ONLY THING WORTH DELETING — measured 2026-08-09, not assumed:**

```
option_rows                    1399.6 MB     ← the target
idx_option_rows_contract_snap   322.9 MB     ← shrinks with it
uq_option_rows_contract         313.5 MB     ← shrinks with it
atm_iv_by_expiry                  5.3 MB     ← 47 days of it. Keep forever.
snapshots / collection_gaps       0.4 MB
```

Two things follow. **A deleted row reclaims ~2.2× its own bytes**, because both indexes on
`option_rows` carry every row — the audit's estimate counted table bytes only and understated the
saving. And **keeping the summaries forever is free**: 5.3 MB is 0.26% of a 2,044 MB database, and
it already powers most of the historical charts. The choice is not "history vs. space." It is
"per-strike detail vs. space", with the history retained either way.

**WHAT THIS POLICY DOES NOT DO, stated plainly because the number is misleading.** Collection
began 2026-06-23. On the day this was written the oldest expiry in the database was 47 days past,
so **a 90-day rule deletes zero rows today and reclaims nothing until roughly November 2026**.
Growth continues at ~82 MB/trading-day until then. This is not an argument against the policy —
the policy has to exist before the data ages into it, and building it now is what makes the
November cutover uneventful. It *is* an argument against believing M3.2 has solved growth the day
it merges. **It has not. It has armed a mechanism that fires later.** Near-term relief, if wanted,
is a separate decision (downsampling old intraday snapshots to hourly — audit §5.8 option (c));
that is deliberately not bundled in here.

**EXPIRIES REFERENCED BY A TRADE ARE NEVER PRUNED, at any age.** The whole point of the record is
the trades that were actually taken. Any `expiry_date` appearing in `trades.initial_legs` or
`trades.ic_expiry_date` is exempt permanently. This is a rule of the policy, not an option on the
pruner — a flag that could disable it would eventually get passed.

**ENTRY CONTEXT IS SNAPSHOTTED BEFORE ANY PRUNING RUNS — this is the gate (ADR-016).**
`get_entry_iv_context()` reconstructs a trade's entry-time IV term structure by reading historical
`option_rows`. Prune those and the answer is gone permanently, for every trade, with no error —
the Regime Analysis would simply report fewer matched trades each month. So the entry context is
written onto the `trades` row at logging time, and the reconstruction becomes a fallback for rows
that predate the columns. **Ordering is not negotiable: snapshotting ships first, pruning second.**

**WHY MANUAL.** The data is irreplaceable and deletion is the one direction that cannot be undone
by looking at the screen (the ADR-043 formulation). A scheduled pruner deletes while nobody is
watching, and the first time its cutoff arithmetic is wrong is also the last time it matters.
Manual invocation is revisitable once it has run correctly several times; the reverse is not.

**WHY NOT ARCHIVE TO A SECOND FILE (audit §5.8 option (b)).** It preserves everything, which is
genuinely attractive for irreplaceable data. Rejected for now on complexity: it needs a second
schema, an attach-on-demand read path, and its own backup story, and it does not stop total disk
growth — it relocates it. Revisit if 90 days ever proves too short in practice.

---

## ADR-043 — The Scanner's KPI cards are deleted, not restored — and how to get them back
**Date:** 2026-08-01 · **Status:** ACCEPTED · **Closes BUG-019**

**Chandan's call, made with the tradeoff in front of him.** Four summary cards — Diagonals
Scanned, Diff > 5, Best Difference, Avg IV Ratio — sat above the Scanner table until 29 June,
when a large refactor deleted the one line that drew them while leaving the ~40 lines that fed
them running on every refresh. He was shown that this was reversible in one direction only, and
chose deletion anyway. **Recorded plainly: this is the irreversible direction**, and the standing
advice in the backlog row was not to take it by default. He took it deliberately, not by default.

**THE RECOVERY PATH, which is the point of this ADR.** The objection to deleting was that it
cannot be undone by looking at the screen. That is answered by writing down where the code is
rather than by refusing the decision:

```
git show 875b257:views/scanner.py | sed -n '283,327p'    # the block, last commit before removal
git show b782ec3 -- app.py                                # the 29 June commit that dropped the render
```

The CSS is still in `assets/theme.css` (`.kpi-grid`, `.kpi-card`, `.kpi-hl`, `.kpi-icon`,
`.kpi-v`, `.kpi-l`, `.kpi-sub`, lines ~496–546), untouched by this change — see DEBT-037.
Restoring is: paste the block back, add `st.markdown(kpi_html, unsafe_allow_html=True)`.

**The accident, confirmed rather than assumed.** Commit `b782ec3` ("V3.4", 29 June, 41
insertions / 173 deletions) did three things to this block at once: removed two of six cards,
**refitted the grid to `repeat(4,1fr)` — exactly the four that remained** — and deleted the
render. Nobody re-fits a layout to four cards in the commit where they mean to delete all four.
The render was caught by an over-broad deletion. Worth keeping in the record because the *reason
for removal* is now permanent, and "he decided against them" and "he never saw them" are very
different histories.

**ONE LOCAL WAS NOT DEAD, AND THIS IS THE SECOND TIME.** `_ready_count` feeds the "N combinations
ready to transform" badge *below* the cards, which is still on screen. Ten of the block's eleven
locals were dead; that one never was. Deleting the block wholesale was rehearsed on a copy and
**took down all six tabs**, not just the Scanner — the code runs on the initial pass before any
tab is selected, so a `NameError` there is a dead dashboard, not a broken panel.

This is exactly the shape of the M2 step 2.2 failure (`_exp_label`) and of DEBT-032, which
recorded this very block as "dead code" and would have made the 29 June accident permanent.
**Three times now, something that read as dead was load-bearing in one place.** The check that
caught it each time is the same one: `scripts/render_check.py`, which runs the page rather than
its functions. The test suite passed on all 693 checks with the wholesale deletion in place.

**Why no new test.** There is nothing left to characterise — the feature is gone, and a test
asserting the absence of four cards would pin an accident of history rather than a behaviour.
The badge that survives has `_ready_count` as its input and is covered by the render check.

---

## ADR-042 — A locked diagonal outranks the collection window, and the screen admits what it dropped
**Date:** 2026-08-01 · **Status:** ACCEPTED · **Closes BUG-022**

**The defect, stated properly.** Both of the collector's narrowings are centred on TODAY —
the nearest `MAX_EXPIRY_COUNT` expiries, and strikes within `STRIKE_FETCH_WIDTH_POINTS` of
spot. A lock is centred on the fill and never moves. So as SPX drifts, a locked strike walks
to the edge of the recorded window and then out of it, and the legs of a position actually
being held stop being recorded. "View Chart" then found the strike absent from today's chain,
dropped it (it must — Streamlit raises "not in options" and the page dies otherwise), and each
dropdown fell back to its default. **The trader clicked a position they were holding and was
shown a different diagonal, drawn with the same confidence, with nothing saying so.**

**Two fixes, because they answer different questions.** Pinning removes the two known *causes*.
The notice covers the *class* — any future reason a staged value goes missing (a collector
outage, a gap day, a lock edited by hand) lands back on the same guard, and BUG-022's actual
wording was that *there is no path where the app admits it could not honour the request*.
Pinning alone would have left that sentence true.

**Chandan's idea, and it was the better one.** The first proposal here was the notice alone.
He asked whether the locked legs could simply keep being collected. Measuring the stored data
settled it: every snapshot is clipped at almost exactly ±300 points, which means **the Python
backstop is the binding fence and the broker is already supplying the strikes we discard**.
So the exemption widens what we KEEP, never what we ASK FOR — no extra API call, no extra
latency, and it cannot invent data. A locked strike the broker never sent is logged as a
warning and left missing, not fabricated.

**A correction made mid-discussion, recorded because it nearly changed the design.** It was
asserted here that the API's 80-strike limit would bind at roughly ±200 points, which would
have made this expensive (a second targeted fetch per cycle). The stored data disproved it.
**The measurement was cheap and the assumption was wrong in the direction that would have
cost the most work** — worth remembering the next time a fetch-width question comes up.

**The headroom question is now answered continuously, not guessed.** `filter_chain_by_strike_window`
logs how wide the broker actually went, BEFORE it filters, because the filter destroys the
evidence: once stored, every snapshot is clipped at exactly ±width and can never answer how
much room was left. That log is what will decide whether a drifting strike ever needs a
second fetch.

**Where it lives.** The rule in `core/pins.py` (pure — a locks dict in, expiries and strikes
out; no file, no config, no clock, the same shape as `core/expiry.py`). The exemption in
`schwab_client.filter_chain_by_strike_window` and the expiry widening in `collector._run_cycle`.
The admission in `ui/controls.py`, scoped by a plain local dict — promotion and both guards run
inside one `render()` call, so a value recorded there belongs to the click that triggered that
rerun and nothing else.

**Deliberate refusals.**

* **The notice fires only for lock clicks — Chandan's scoping call.** A value the trader set by
  hand going stale (the tab left open overnight while the chain rolled) stays silent. A banner
  that fires during ordinary browsing teaches you to ignore the banner that matters.
* **A malformed lock costs that lock its protection, never the snapshot.** The cycle is
  unrepeatable; a hand-edited lock file is not worth losing one over. Same refusal as
  `purge_expired`'s unparseable `front_expiry`.
* **`core/pins.py` does not judge expiry.** `state.entry_locks.purge_expired` owns that and runs
  against the same file. A lock that should have been purged and was not pins a few extra
  strikes — a handful of rows, and the safe direction.

**What this does NOT fix, and it is not a defect.** Pinning is **forward only**: it protects a
lock from the next snapshot onward and cannot fill in history recorded before the lock existed.
A lock created today has no history for the strike yesterday, and never will. The notice is
what covers that stretch honestly.

**Proving it.** 19 deliberate breakages, 19 caught — but one **survived the first run**, and it
mattered: narrowing `_load_pins`'s `except Exception` to a type that never occurs broke nothing,
because `state.store.read_json` already quarantines a corrupt file and returns `{}`. The guard
was real but unreachable from the test that claimed to cover it. **A test that cannot fail is
not evidence**, so the failure is now injected at the seam instead, and both layers are pinned
separately because they fail for different reasons.

---

## ADR-041 — M2 step 2.5: two more layers, and what "under 400 lines" actually required
**Date:** 2026-07-31 · **Status:** ACCEPTED · **Closes M2 · Closes DEBT-002**

**Decision:** Finish the decomposition. `app.py` 2,505 → **392 lines**, by adding two layers
rather than stretching the four that existed:

| New home | What moved | Lines |
|---|---|---|
| `assets/theme.css` | the stylesheet | 754 |
| `services/` | memoised loaders, sidecar bindings, the whole Mission Control pipeline | ~670 |
| `ui/` | theme, sidebar, refresh poller, header, controls bar, locks popover | ~560 |
| `core/` | `exp_label`, `market.py` (daily change, GEX), `position.py` (derived values) | ~180 |
| `views/scanner.py` | `_render_mc_section`, with the signature ADR-037 deferred | ~130 |

**The scope in STATUS.md did not add up, and saying so first was the point.** It named two
pieces — 757 lines of CSS and the 115-line `_render_mc_section`. Both were real and both were
done, but they total 872 of 2,505, landing at ~1,630 and leaving the step's own stated target
(under 400) missed by a factor of four. `plan.md` had the honest version all along: *"roughly
870 of the remaining 2,486 are already identified and spoken for"* — i.e. 1,616 were not.
Chandan chose the full evacuation once the arithmetic was in front of him. **A target and a
task list that disagree are a decision waiting to be made by whoever notices last.**

**Why two new layers, not four stretched ones.** Both candidates failed on a rule that is
load-bearing rather than tidy:

* The Mission Control pipeline reads `st.session_state` — the "New" badge is a diff against
  what the *previous* snapshot showed, so the state must survive reruns and cannot sit inside
  a cached function. That bars `core/` absolutely, and `dataaccess/` is told which database
  where these wrappers *decide* it. Hence `services/`: **the only layer permitted both
  `config` and `streamlit`**, which is precisely the pair every other layer is kept from.
* The header, sidebar and controls bar are not tabs. They run before any tab is chosen, and
  the controls bar *returns* the selection every tab then reads. `views/` answers to "expose
  `render(ctx)` and nothing else"; forcing chrome in would have meant loosening that rule for
  all six tabs to accommodate four things that are not tabs. Hence `ui/`, with views'/ two
  rules intact: **it draws, and it never fetches.**

**Alternatives rejected:** (a) do only the two named pieces — leaves M2 open at ~1,630 lines
and defers the same decision to the next session; (b) one `page/` package for both new layers
— it would contain both "must not touch streamlit's caches" and "is nothing but drawing", so
no import rule could be written for it, and an unenforceable layer is a comment.

**Tradeoff:** eleven new files and one genuinely non-obvious import graph
(`app → ui → services → {core, dataaccess, state}`) in exchange for a top-level file that can
be read in one sitting. Two ordering constraints that were previously implicit in a single
long script are now split across modules and documented in prose at both ends — `ui/controls.py`
holds three of them, and two fail only at runtime.

**What the step actually rests on, and it is not the test suite.** Phase 3 moved
`_ELIGIBLE_HISTORY_RETENTION_DAYS` into `services/sidecars.py` and left the reference behind.
**All 639 tests passed and every one of the six tabs raised on load.** That is not a gap in the
suite, it is structural and `scripts/render_check.py` has said so since M2.2: the tests exercise
functions, and until this step the page could not be exercised at all. So each of the eight
phases was verified by a before/after **render comparison** — `AppTest` against a git worktree of
HEAD and against the working tree, same database, state redirected, every rendered string
diffed. All eight came back identical across all six tabs.

**Two things that comparison could not see, both handled separately.** (1) It redacts the
checkout path as known noise, because a worktree legitimately differs there — so it was blind to
`reauth_command`, whose `Path(__file__).parent` had to become `.parent.parent` when it moved
into `ui/`. Get that wrong and the dashboard hands you a `cd` into the wrong directory, which
you discover only once the token has expired. Asserted directly instead. (2) Charts remain
uncovered entirely — Streamlit 1.58 exposes no `plotly_chart` accessor — unchanged from 2.4.

**A harness that drifts is worse than none.** The first comparison run showed "Seen 2× → 3×" on
every card. Not the code: Chandan's dashboard was running, Streamlit reloads it on every source
save, and each reload recomputed Mission Control and bumped `hit_count` in the real registry
that the harness was copying as its starting state. Frozen once, then reused — **the fix was to
remove the drift, not to filter the symptom out of the diff.**

**13 injected faults, 13 caught** — and the thirteenth is the finding. The DEBT-030 guard
(*every timestamped read must be display-converted before it reaches a chart*) matched
`load_atm_hist`, while every call site outside `views/` is named `_load_atm_hist`. **One
underscore had made half that guard decorative since the day it was written**; `views/` was
covered only because a view happens to call `ctx.load_atm_hist_fb(...)` without one. Mutation
found it, nothing else would have, and this is the second consecutive session in which the
break-it-deliberately exercise found a blind check rather than confirming a healthy one.

**Left deliberately untouched, and recorded rather than fixed:** `spx_intraday["ts_et"]` is
computed and never read (DEBT-034), and `kpi_html` is still BUG-019 and still Chandan's call.
Fixing either inside a move destroys the one property that makes the move checkable — the same
reasoning as step 2.4, applied to the same class of temptation.

---

## ADR-040 — What BUG-020 cost, and the blind spot that let it run for a month
**Date:** 2026-07-30 · **Status:** ACCEPTED · **Closes BUG-020**

Recorded because the fix is one line and the lesson is not. The row is deleted per ADR-017;
this is the reasoning worth keeping.

**The defect.** `st.rerun()` aborts the running script *where it stands*. The live-refresh
poller called it on seeing a newer snapshot, but the line recording which snapshot was on
screen sat ~100 lines further down. It never ran. Every subsequent execution found the same
stale value and rerun again — forever, at 100% of a core, never reaching the code that draws
anything. Fixed by adopting the snapshot id *before* rerunning.

**The comment three lines above described this exact trap** and guarded the first-run branch.
The newer-snapshot branch had the identical hazard and no guard. A known danger, half-handled.

**Why no test could have caught it: `AppTest` does not fire fragment timers at all.** Not
"we forgot to test it" — there was no way to reach that code from a test, `render_check.py`
included. "All six tabs render" was true throughout and worth nothing here; they render fine,
they just never got the chance. It is now pinned by asserting on the *source*
(`test_the_refresh_poller_adopts_the_snapshot_before_it_reruns`), which is the weaker tool
you fall back to when there is no seam to call.

**A second `AppTest` blind spot found alongside it, still open:** it cannot change a strike.
Both `select()` and `select_index()` feed the formatted label ("7,410") back through
`format_func=lambda s: f"{int(s):,}"`, which then fails on the comma. That is harness misuse
on my part rather than an app defect, but the consequence is real — **the single most common
interaction on this dashboard has never been exercised automatically.**

**The diagnostic lesson, which cost the most.** I misdiagnosed this twice: first as the work
in progress, then as a stale process — and stated the second with more confidence than the
evidence carried. What actually cracked it was one detail in Chandan's report that neither
theory explained: *the charts stopped responding to the expiry and strike controls.* A slow
app is still a responding app. Both of my theories predicted slowness; only an aborted script
predicts controls that do nothing. **The symptom a theory cannot explain is worth more than
the three it can.**

---

## ADR-039 — An entry lock is deleted when its front leg expires, not hidden
**Date:** 2026-07-30 · **Status:** ACCEPTED · **Closes BUG-021** · **Opens BUG-022**

**The defect.** Nothing anywhere removed an expired lock. `state/entry_locks.py` could create,
read, edit and delete-on-request; the automatic cleanup Chandan had designed was never built.
Three dead locks were still listed on 2026-07-30, the newest expired ten days earlier.

**Delete, not archive — Chandan's call:** *"I don't think I'd look once it expires so no point
in archiving it."* Noted here because it makes the clock rule load-bearing in a way a display
filter would not be. A rule that fires one day early now **destroys a lock on a live position**,
and the entry price it holds is the number every chart is measured against. That is why the
rule is a pure function with the clock injected, tested to the minute from both sides, and why
the mutation run covers a 4:00-vs-4:15 close, an off-by-one at the boundary, and a timezone
that is relabelled rather than converted. 7/7 caught.

**The rule.** Expired when the front expiry date is past, or it is that date and the New York
time is ≥ 16:15 — the cash-index close, not the 4:00 PM equity bell.

**Where it lives.** The rule in `core/expiry.py` (pure, clock injected); the file write in
`state/entry_locks.py`; the call inside app.py's `_load_entry_locks`, which is the single
funnel every reader passes through. **Filtering the popover instead was the obvious cheap
option and is wrong:** the visible list would look clean while the chart and the
current-combo lookup carried on seeing the dead record.

**Two deliberate refusals in the purge.** A lock whose `front_expiry` will not parse is **kept**
— deleting what you cannot read is how data goes missing quietly. And when nothing has expired
the file is not rewritten at all: this runs on every script run, and a no-op rewrite is still
a chance to lose the file.

**What this does NOT fix, now BUG-022.** Chandan asked whether the expiry fix made the
scrambled-controls problem moot. It does not, and the correction matters: "View Chart" stages
the lock's expiries and strikes, and a guard drops any value absent from today's chain, after
which each dropdown silently falls back to its default. Expiry was one way to trigger that.
Two remain on a **live** lock — a strike drifting outside the collector's window as SPX moves,
and a back expiry beyond the furthest one collected. In both cases the trader clicks a position
they are actually holding and is shown a different diagonal, with nothing saying so.

---

## ADR-038 — Clearing the debris the safe moves left: names, scaffolding, and the clock
**Date:** 2026-07-30 · **Status:** ACCEPTED · **Closes DEBT-028, DEBT-030, DEBT-032** · **Opens BUG-019**

Three rows that existed because M2's moves were done in a way that could be *proved*. Each was
created or preserved on purpose; each is now due, and none could have been done earlier without
destroying the evidence that made the moves safe.

**DEBT-028 — the labels now match the building.** 14 of `core/`'s 16 module-level names dropped
their leading underscore across 168 references. The convention means "internal, do not call from
outside", and every one of these is called from outside. `_RATIO_BANDS` and `_RATIO_THRESHOLDS`
stay private: only `charts.py` calls them, and `tests/app_loader.py` names them because it
*rebuilds the module namespace* to exec a function in a synthetic scope — simulating being inside
the module, not consuming an API.

**A hidden coupling, found by breaking it.** app.py's memoised wrapper and core's pure function
were both called `_compute_transform_scanner`. Renaming only core's broke 17 tests: `app_loader`
execs app.py's source in a namespace built from `core/`, so it had silently depended on the two
names being identical. They match again deliberately, and the dependency is now written down.

**The 63 rebind lines are gone,** and with them the byte-identical property. That was the whole
point of collecting it first: move, prove, *then* rename. **The first attempt was wrong and Python
rejected it** — a ``-anchored regex turned `spx_price=spx_price` into
`ctx.spx_price = ctx.spx_price`, because a keyword argument's NAME looks exactly like a variable.
It failed to parse, so it failed loudly; the same class of mistake on a name that stayed
syntactically valid would not have. Redone against the parse tree, rewriting only `ast.Name` nodes
in Load context. **The second attempt was also wrong,** and an assertion caught it before anything
was written: `ast` reports column offsets in UTF-8 *bytes*, and these files are full of
box-drawing rules.

**DEBT-030 — the read layer stops deciding what the charts see.** `dataaccess/` returned a bare
"14:30" with nothing saying where. It now returns zoned UTC, and `core.charts.to_display_time`
strips the zone at the point of drawing, which is where Plotly's rangebreaks requirement actually
lives. Applied at every consumer that previously received naive Eastern — **including the two that
draw no time axis**, because converting them too costs nothing and makes "nothing downstream
changed" true rather than merely likely.

**The trap, caught before writing.** `load_contract_hist(days=1)` keeps the last calendar date, and
that date is a *trading-session* question. A 20:05 New York row is already tomorrow in UTC, so
letting the filter follow the column would split one evening session in two and keep only its tail.
The filter converts locally now and does not write it back.

**ADR-034's tripwire fired exactly as designed** and is the reason this was survivable. Four
assertions failed the moment the fix landed; all four were updated deliberately, and the protection
moved one step down the pipeline rather than being removed — *what a chart receives must still be
New York wall-clock*. A new structural guard covers the failure mode that has no symptom at all:
miss one chart site and its x-axis moves four hours, every session break lands wrong, and the
picture still looks entirely plausible. No golden test can see that (the fixtures would simply be
recaptured against the shifted values) and neither can the render comparison, since `AppTest`
cannot read inside a chart.

**DEBT-032 — the row was half wrong, and the wrong half mattered more.** It recorded two pieces of
dead code. `_save_entry_locks` was dead and is deleted. `kpi_html` was **not dead — it is a dropped
feature**, and following the row as written would have made a month-old accident permanent. Commit
`b782ec3` removed two of six KPI cards, re-fitted the grid to `repeat(4,1fr)` for the four
remaining, *and* deleted the line that renders them. Nobody re-fits a grid to four columns in the
same commit in which they mean to delete the row. The Scanner has been missing four summary figures
since 29 June. Now **BUG-019**, and the decision — restore or delete — is Chandan's, because
deleting is the option nobody can spot by looking at the screen.

**Two of my own mistakes, neither caught by the suite.** `views/strike.py` used
`config.DISPLAY_TIMEZONE` without importing `config`: 623 tests passed and the tab raised on load,
found by `render_check.py` — the ADR-034 scenario repeated almost to the letter. And the first
today-filter test wrote rows five minutes apart at whatever time the suite ran, which sit on one UTC
date almost always, so the broken and correct filters agreed and **the mutation survived**. Third
time an example has been too simple to exhibit the fault it was written for (ADR-029, ADR-030, this).

**A measurement caveat worth keeping.** The before/after render comparison showed the Calendar Edge
observation count dropping 126 → 125. That is not the change: `days=1` is a **rolling 24-hour
window**, not a calendar day, so rows age out between two runs minutes apart. Re-running the pair in
the reverse order produced identical output, which is what separates the clock from the code.

**Verified.** 623 tests (10 new). Mutation-verified on an isolated copy: 6 injected, 6 caught — 5
before the today-filter test was strengthened, which is how the sixth was found. Lint back to
baseline 93 after each step. All six tabs render, and all six produce identical rendered text across
both DEBT-028 and DEBT-030.

---

## ADR-037 — Finishing `views/`: what a view is allowed to be handed, and the one import that survived
**Date:** 2026-07-30 · **Status:** ACCEPTED · **Completes:** M2 task 2.4 · **Opens DEBT-032**

Extends ADR-036, which set the method on the first three tabs. The last three — Strike Detail,
Scanner and Calendar Edge — raised three questions the easy tabs never did.

**1. Helpers the tab calls: move, or inject?** The rule that fell out is *what does it touch?*
`_render_note` touches nothing but `st` — no file, no directory, no configuration — and the edge tab
is its only caller, so it moved. Everything else app.py hands the edge tab is a thin wrapper that
binds `config.STATE_DIR` — the entry-lock actions, the registry backfill, the locks popover — and
those are **injected as callables**. The alternative, importing `state.entry_locks` inside the view,
only relocates the problem: the view would then need to know which directory, and reaching for
`config.STATE_DIR` to answer that is the exact hidden global ADR-035 removed.

**This is where a view stops being read-only, and it is deliberate.** The edge tab creates locks,
clears them and backfills the registry. Those are user actions behind buttons, not a side effect of
drawing, and routing them through injected callables keeps the writing visible in the context's type
list rather than buried in a chart function.

**2. `_render_mc_section` is injected, not moved,** and that is a compromise worth naming. It is 115
lines of card renderer used only by the Scanner tab, so it plainly belongs in `views/scanner.py`. It
also reads `snap_ts_str` and `spx_price` straight from app.py's namespace, so moving it means turning
two globals into parameters — a signature change. **ADR-034 is the record of what a missed call site
costs:** 569 tests green and every tab raising `TypeError`. So it stays, injected, and step 2.5 must
move it with its call sites checked. app.py cannot reach its 400-line target without doing so.

**3. One view imports `config`, and it is fenced rather than forbidden.** `views/edge.py` needs
`config.DISPLAY_TIMEZONE` on a single line. Rebinding it through the context would have meant editing
the moved body — the one thing this step does not do. So the import stays, and
`test_a_view_never_reaches_for_the_configured_database` names the two attributes that would make it
dangerous: `DB_PATH` and `STATE_DIR`. A view that can find the database can query it, and the memo it
would bypass has no symptom when it goes missing. Same shape as the rule `dataaccess/` already has.

**A copy left behind, found and then guarded against.** `_render_note` was moved into `views/edge.py`
and the original stayed in `app.py`, called by nothing. Two definitions of one function is worse than
either mistake alone — the page runs one, anything reading app.py's source measures the other, and
nothing raises. It is precisely the case
`test_break_sessions_is_defined_once_where_the_loader_expects_it` was written for in ADR-032, so the
guard is now generalised: `test_no_view_helper_is_also_left_behind_in_app`.

**The extraction made a linter useful.** Lint finished +1 against baseline, and the one addition is
`kpi_html` in `views/scanner.py` — dead since long before this step, and **unreportable until now**,
because an unused module-level variable is not an error while an unused local is. The only thing that
changed is that the code acquired a boundary. Six unused imports in app.py were removed as part of
the same accounting; both dead pieces that remain are DEBT-032, left alone because deleting a line
inside a body ends the byte-identical property that this step's evidence rests on.

**Verified.** 613 tests, 22 new. Mutation-verified on an isolated copy: **10 injected, 10 caught** —
including a view reading `config.DB_PATH`, a helper left behind as a copy, a dropped `_break_sessions`
call and a dropped dispatch. All six tabs render, and all six produce **byte-identical rendered text**
against a worktree of the previous commit on the same live database. `test_chart_breaks.py` was
re-pointed a second time, again on its own anchor; its 4,000-character window was deliberately **kept**
rather than widened to the whole module, since widening it would make every assertion in it easier to
satisfy, and a re-point is not the place to loosen a check.

---

## ADR-036 — `views/` extracted as *provable* moves, because this layer cannot be tested
**Date:** 2026-07-30 · **Status:** ACCEPTED · **Advances:** M2 task 2.4 (3 of 6 tabs) · **Opens DEBT-031, BUG-018**

**The problem this answers.** Steps 2.1–2.3 followed one rule: *only move code the tests already
pin.* That rule cannot survive step 2.4. A tab body is drawing — eight metric tiles, a scatter, a
strip of HTML — and a panel whose rows come back in a different order still looks like a panel.
There is no assertion to hide behind here, so the extraction had to be shaped so that a *different*
claim becomes checkable instead.

**Decision.** Each tab moves **verbatim**, and the verbatim-ness is verified mechanically rather
than asserted in a commit message. Three things make that possible:

1. **Zero reindentation.** A tab body sat one level in under `if st.session_state[...] == "x":` and
   sits one level in under `def render(ctx):`. Same column. So the body is copied, not rewritten.
2. **A rebind preamble.** The only new lines are `front_expiry = ctx.front_expiry` and friends,
   directly under the docstring. The body still refers to the names it always did — including the
   underscore-prefixed ones, which look wrong in a package and are DEBT-028 on purpose.
3. **A diff against the commit where the tab was still inline.** Each moved body is compared
   line-for-line with `git show <rev>:app.py`. All three came back **byte-identical**.

**Why not tidy the names in the same step.** Because then the diff proves nothing, and the diff is
the only evidence available. `ctx.diag_mark` throughout would be better code and an unverifiable
change; the sequence *move, prove, then rename* costs one extra pass and keeps every step provable.
This is the same reasoning as ADR-032's decision to keep the underscores through step 2.1.

**The layer rule that matters most.** `views/` may not import `dataaccess`. The memo wrappers live
in app.py and arrive on the context. A view that imported the reads directly would render an
identical page, return identical numbers, pass every test — and re-query SQLite on every rerun. It
is the same invisible-failure shape as ADR-032's missing `compute=`, and it is now enforced on the
import list, along with: `render` takes `ctx` and nothing else (a defaulted parameter is an input
the caller never has to think about), nothing draws at module import (Streamlit reruns the script,
not the imports, so a module-level `st.` call draws once into whichever tab happened to be first and
never again), and app.py must actually dispatch each view — an extracted tab nothing calls is a
blank page, and no other check would notice.

**A test failed and was re-pointed, not weakened.** `test_the_statistics_frame_is_deliberately_not_broken`
reads app.py's source for a section that had moved, and tripped its own `"anchor moved"` guard —
exactly as ADR-032 predicted this class of test would behave. It now reads the view module, and got
*stronger*: the module IS the tab, so the 2,000-character search window is gone and a
`_break_sessions` call sitting just past the old cut-off can no longer hide.

**The before/after check, and its two holes.** `render_check.py` proves a tab does not raise; it
counts elements, and a count cannot see a lost decimal. So each moved tab is also compared by
content — `AppTest` run against a git worktree of the previous commit and against the working tree,
same database, state redirected so nothing touches the real registry, every rendered string diffed.
All three tabs identical. **Two cautions are worth more than that result.** First, the harness's
first version wrote **empty files** (its output crashed on the console's cp1252 codec) and two empty
files diff clean — a green result proving nothing, which is ADR-029's lesson arriving in a new
costume; it now refuses to report a dump of nothing. Second, **charts are not covered at all**:
Streamlit 1.58's `AppTest` exposes no accessor for `plotly_chart`, so for a chart tab the diff sees
the captions and stops. Element counts are separately useless for the Scanner tab, whose panel
legitimately differs between two runs minutes apart as the collector adds snapshots.

**Two defects found by reading, and deliberately not fixed.** DEBT-031: the 5-point transform
threshold is hardcoded three times in the Entry tab while `core/scanner.py` defines it once, and at
exactly 5.00 two panels in that one tab contradict each other because one test is `>` and the rest
are `>=`. BUG-018: on any expiry day the front-expiry default is 0 DTE, so there is no straddle, and
the Normalized Debit tile reads `"— (set strikes)"` next to a Diagonal Mark computed from those very
strikes. **Both were left alone.** A move that changes a trading threshold is not a move, and BUG-018
is a wording decision that is Chandan's to make. Fixing either would have destroyed the byte-identical
property that is the whole evidentiary basis of this step.

**Found by looking at the numbers, not the verdict.** BUG-018 was sitting in the *output* of the
before/after comparison, which reported IDENTICAL — correctly, since the bug predates the move. The
comparison was built to answer "did this change anything"; it answered that, and the defect was
visible only because the captured values were read as well.

---

## ADR-035 — `state/` extracted and DEBT-011 closed: an absolute home, an atomic write, and a corrupt file that no longer erases itself
**Date:** 2026-07-30 · **Status:** ACCEPTED · **Completes:** M2 task 2.3 · **Closes three of DEBT-011's five parts**

**Scope note, so the claim is accurate.** DEBT-011 lists five faults: relative paths, no atomic
write, no validation, no schema, no backup. This closes the first three — the ones that could lose
data. **Still open: no formal schema** (the check is "is it an object", not "does it have the right
shape") **and no scheduled backup** (which belongs with M3's backup work, not here). The row stays,
reduced.

**Decision.** Move the JSON sidecar persistence into `state/` — `store.py` (the primitives),
`chart_colors.py`, `entry_locks.py`, `eligible_history.py` — and add `config.STATE_DIR`, absolute
and anchored to the project root. Every function in the package takes `state_dir` as an argument.

**The fix already existed in the same file.** `config.py` has had `PROJECT_ROOT` since the
beginning, and `DB_PATH` was always built from it. The database got this right; the three JSON files
were `Path("eligible_history.json")` and friends — **relative**, resolved against the working
directory. `STATE_DIR` follows the convention that was already there, so no file moves and the
existing ~700 KB registry is found exactly where it always was.

**Three guarantees, in increasing order of how much they mattered.**

1. *Absolute paths* — the headline of DEBT-011. Launched from anywhere but the project root, the
   dashboard would find no registry, create an empty one there, and render a Mission Control panel
   that had forgotten every past opportunity, with no error.

2. *Atomic writes* — the registry is rewritten **in full on every new snapshot**, roughly 126 times
   a trading day. Interrupt one and the old code left a half-written file. Now written to a
   temporary file in the same directory and renamed into place.

3. **An unreadable file is quarantined, not silently replaced — and this was the dangerous one.**
   The old loaders caught `JSONDecodeError` and returned `{}`. That is not a failed read; it is
   **data loss on a delay**: the empty dict comes back, Mission Control writes it out on the next
   snapshot, and 700 KB of history is gone with no copy anywhere — all four files are gitignored.
   `read_json` now moves the unreadable file to `<name>.corrupt-<timestamp>` first.

**The tests found a real bug in the quarantine itself.** `os.replace` overwrites its target
silently, so two corruptions within the same second produced the same filename and the second rescue
copy destroyed the first — defeating the entire point. `test_quarantine_survives_a_second_bad_file`
failed on the first version of the function. The name is now made unique before the move.

**One test asserts a mechanism rather than a behaviour, deliberately.** Atomicity cannot be observed
from outside — you cannot pull the power out mid-write from a test. And the obvious behavioural test
is not enough: an unserialisable payload raises *before* any bytes are written, so a plain
`write_text(json.dumps(...))` passes it too. `test_the_write_goes_via_a_temporary_file_and_a_rename`
therefore spies on `os.replace`. Given the file has no copy in version control, the coupling is
worth it.

**The loader's guard changed shape and got stronger.** `load_pipeline(eligible_history_path=...)`
existed so a test could not overwrite the real registry. With the location now coming from
`config.STATE_DIR`, that argument is gone; instead `load_pipeline()` **refuses to run at all** if
`STATE_DIR` still points at the project root. Same protection, one fewer mechanism, and it now
covers all three sidecar files rather than only the registry.

**`state/` may not import `config`.** Unusually strict, and achievable: it is handed its directory
and, for entry locks, its timezone. Nothing in the package reads configuration of its own, which is
what makes every function testable against `tmp_path` with nothing patched.

**Verified.** 569 pre-existing tests pass unchanged; 22 new; **591 total.** Mutation-verified on a
copy: **6 faults injected, 6 caught** — including reintroducing DEBT-011 itself, and removing the
quarantine-collision fix. All six tabs render with no exception. **The real files were backed up to
`spx-dashboard-backups/state-20260730-091914` and verified by SHA256 before any code changed**; after
the full run, `entry_locks.json` and `chart_colors.json` are byte-identical to that backup and the
registry has grown 2,150 → 2,174 entries, which is the app doing its normal job.

---

## ADR-034 — DEBT-027 fully closed, DEBT-030 given a safety net — and the day 569 green tests hid a broken dashboard
**Date:** 2026-07-30 · **Status:** ACCEPTED · **Closes:** DEBT-027 · **Guards:** DEBT-030

**Decision.** `_exp_label` takes the expiry table as a parameter instead of reading a module global,
closing DEBT-027 site 2 and the whole item. Separately, add two characterization tests pinning the
wall-clock conversion in `dataaccess/queries.py`, so DEBT-030 can no longer be changed silently —
without doing DEBT-030 itself.

**Why site 2 was fixed now rather than at 2.4, reversing earlier advice.** The estimate was wrong.
`_exp_label` had two direct call sites, four lines apart, in one function — not a step-4-sized
change. And the defect was sharper than the backlog recorded: the guard tested membership against
the **parameter** while the lookup used the **global**. Same objects in production, so it worked;
the day they diverged, the guard would pass, the lookup return `None`, and the card would render
`Friday, Aug 21, 2026` with `(23 DTE)` silently missing. Carrying that into a refactor of the code
around it was the worse option.

**Why DEBT-030 was NOT fixed, and what was done instead.** `_break_sessions` and the rangebreak
settings appear **10 times** in `app.py`. Returning zoned timestamps means re-adding the conversion
at all ten chart sites — in the least-tested code in the repo, every line of which 2.4 moves anyway.
The *danger*, though, was never the change: it was that **no test could see it.** The goldens were
captured against the current behaviour, so shifting every chart by four hours produces output
matching its own recorded reference and passes. `tests/test_query_timestamps.py` now asserts the
behaviour directly from a known stored value, computing the expected answer via `zoneinfo` rather
than `config.DISPLAY_TIMEZONE` so it cannot merely agree with itself. **Those two tests are designed
to fail when DEBT-030 is done** — which is the point: whoever does it must update them and the ten
chart sites together.

**THE FINDING THAT MATTERS MOST TODAY. All 569 tests passed while every tab of the dashboard raised
`TypeError` on load.** `_exp_label` gained a required argument; `grep '_exp_label('` found the two
call sites and the definition. It did not find the two places the function is handed to Streamlit
**by reference** as a selectbox `format_func`, where Streamlit calls it with one argument. The suite
could not catch it: it exercises functions, and this was the page.

Three consequences, all acted on:
1. The two selectboxes now bind the table through a small named wrapper rather than passing the
   function bare.
2. **`scripts/render_check.py` is now part of the repo**, not a scratch file. It executes `app.py`
   via `AppTest` once per tab and exits non-zero if any raises. Run it after any change to `app.py`.
   A plain HTTP fetch of the running server proves nothing — Streamlit only executes the script when
   a client session connects.
3. **Searching for a function by `name(` is not a search for its uses.** A reference passed as a
   callback is invisible to it. Grep for the bare name when changing any signature.

**A second seam, found by injection.** Testing `_exp_label` directly proved the function honours its
argument; it did **not** prove the caller passes the right one. Re-injecting the original defect at
the call site left every other test green, because nothing asserted a label. That survivor produced
`test_the_panel_labels_cards_with_the_table_it_was_handed`. **Third time in three steps that the
gap was a wrong or missing CALL rather than a wrong calculation** — the pattern is now reliable
enough to plan around: after changing any signature, test the call site, not just the function.

**The loader lost an argument.** `load_pipeline(dte_by_expiry=...)` existed solely to inject the
global this ADR removed. It is gone, and nothing injects that name any more — so a function that
starts reading it again fails with `NameError` instead of silently picking up a stale table.

**Verified.** 564 pre-existing tests pass unchanged; 5 new; **569 total.** Mutation-verified on a
copy: **4 faults injected, 3 caught, 1 survivor** (the call site) **which produced a fifth test,
then re-injected and caught.** All six tabs render with output identical to before the change. Lint
unchanged at 94.

---

## ADR-033 — `dataaccess/` extracted: the database location becomes an argument, and the package is not called `data/`
**Date:** 2026-07-30 · **Status:** ACCEPTED · **Completes:** M2 task 2.2 · **Closes:** DEBT-027 site 1

**Decision.** Move the nine database reads out of `app.py` into `dataaccess/queries.py`, each
taking `db_path` as its **first argument**. `app.py` keeps a thin `_load_*` wrapper per query whose
entire job is the `@st.cache_data` memo, `config.DB_PATH`, and the cache key.

**The package is called `dataaccess/`, not `data/` as the plan said.** `data/` already exists and
holds `dashboard.db` — 1.57 GB of irreplaceable market data — and `token.json`, the broker
credentials. Only `.gitkeep` in it is tracked. Putting source code in that directory would mix
program files with a database the collector holds open and a secret that must never be committed;
it would also make the whole thing an importable package. The plan's structure is unchanged in
substance. Renaming later is a directory move and one import line.

**The count in the plan was wrong: nine, not eleven.** Three other functions match `_load_*` but
read small JSON settings files — chart colours, entry locks, the eligibility registry — not the
database. They belong to step 2.3, `state/`. Corrected in `plan.md`.

**`db_path` as an argument is the substance of this step, not tidiness.** Before it,
`_candidate_signals` called `db.get_transform_mark_history(config.DB_PATH, ...)`, so nothing could
aim it at another database and all 22 tests in `test_mission_control_golden.py` had to monkeypatch
that global — **a test modifying the thing it is testing.** The parameter defaults to the global, so
production and every existing caller are unchanged, and the 22 tests that exercise the default path
were deliberately left alone: production calls it without the argument, so that path is the one
that has to keep working.

**A new parameter that no test uses is not a fix, it is a claim.** Adding `db_path` changed nothing
observable — all 563 tests passed with it ignored, because they all set the global. So
`test_the_database_location_can_be_given_instead_of_patched` points `config.DB_PATH` at a file that
does not exist and passes the real fixture as the argument: signals can only come back if the
argument won. Injection 6 confirms it fails when the argument is ignored.

**`snapshot_id` is gone from four signatures.** `_load_spx_intraday`, `_load_transform_marks`,
`_load_latest_atm_iv` and `_load_diagonal_hist` all accepted it and **none of them ever read it** —
it existed solely to key Streamlit's cache. The cache stays in `app.py`, so its key does too. The
wrappers still take it; the queries no longer pretend to.

**Second memo seam, same shape as ADR-032.** `load_atm_hist_fb` falls back to a wider window when
today is empty, and that second read must reuse the memoised loader rather than query again. It
takes a `load=` argument that `app.py` supplies, exactly as `_scan_all_offsets` takes `compute=`.
Both now have source-level wiring guards, because dropping either keyword changes no number and no
behavioural test can see it. **This is the second time the same trap has appeared in two
consecutive steps — assume it will appear again in `state/` and `views/`.**

**Deliberately not done: "return data rather than display shapes."** The plan asks for it, and the
reads mostly already do — rename columns, scale IV to percent. The exception is real:
`.dt.tz_localize(None)` produces a naive wall-clock timestamp *because Plotly's rangebreaks require
one*. That is a display concern living in the data layer. Changing it moves every chart's x-axis,
which is not something to do in the same step as an extraction and cannot be verified by the tests
that exist. Recorded as DEBT-030.

**Verified.** 560 pre-existing tests pass unchanged; 4 new; **564 total.** Mutation-verified on a
copy: **6 faults injected, 6 caught, no survivors.** The most valuable is injection 4 — dropping the
IV percent conversion *in its new home* fails the pipeline goldens, proving the moved code is still
genuinely pinned where it now lives. The dashboard was also run: all six tabs execute with no
exception, with output identical to before the change.

---

## ADR-032 — `core/` extracted: move only what is pinned, and keep the memo out of the pure layer
**Date:** 2026-07-29 · **Status:** ACCEPTED · **Completes:** M2 task 2.1

**Decision.** Create `core/` — pure calculation, no database, no page, no files — and move eight
functions and six constants into it from `app.py` (**4,283 → 3,991 lines**): `format.py`
(`_sparkline`, `_fmt_duration`, `_fmt_eta`), `charts.py` (`_break_sessions`,
`_banded_ratio_traces`), `ranking.py` (`_rank_for_panel`, `_card_key`), `scanner.py`
(`_compute_transform_scanner`, `_scan_all_offsets`).

**Scope rule: only move what the tests already pin.** Every function moved is covered by the 88
characterization tests written in 2.0a/2.0b, so the move is verifiable rather than hopeful. The
one obvious candidate left behind is `_nearest_idx` — four pure lines, but untested, and untested
code is exactly where a silent break hides. It moves with `views/` (DEBT-028).

**The memo stays in `app.py`, and that is not a compromise.** `_compute_transform_scanner` carries
`@st.cache_data(ttl=120, max_entries=8)`, and **two callers share those saved results**: the Scanner
tab and the 21-offset Phase A sweep. `core/` cannot import Streamlit, so moving the function
naively would have left the sweep calling an uncached function — identical numbers, and every
existing test still green, while the dashboard recomputed 21 offsets on every rerun. The decorator
therefore stays in `app.py` wrapping the imported pure function, and `_scan_all_offsets` gained a
keyword-only `compute=` argument that production supplies.

*Alternatives rejected.* (a) Move the decorator into `core/` behind an import guard — defeats the
purpose of the layer. (b) Have `app.py` rebind `core.scanner._compute_transform_scanner` after
import — works, via late global lookup, but action at a distance: nothing at the call site would
say the sweep's speed depends on a line in another file. (c) Accept the slowdown — no; "slowness"
was a withdrawn symptom report this week (ADR-031), and knowingly introducing real slowness while
that question is open would be indefensible.

**The seam has a cost, and the mutation run found it.** Dropping `compute=` is invisible: numbers
identical, all 559 tests green. It **survived** the injection run, so it now has its own guard —
`test_the_offset_sweep_is_handed_the_memoised_scanner`, asserted against `app.py`'s AST, the same
shape as the BUG-002 wiring test. This is the third time on this project that a *missing call*, not
a wrong calculation, has been the defect worth guarding; the pattern is now explicit.

**Names keep their leading underscores, for now.** `core.scanner._scan_all_offsets` reads oddly —
an underscore means "private to this module", which is wrong for a package API. Renaming was
deliberately deferred so that this step is a **pure move**: every one of the 549 existing tests
passes unchanged, which is the evidence that nothing was altered in transit. Renaming touches every
call site and deserves its own verifiable step (DEBT-028).

**The test loader now reads two sources, and rejects a half-move.** `tests/app_loader.py` searches
`core/` first, then `app.py`. A name defined in *both* is now an error rather than a silent
preference for `core/` — that state means the dashboard runs one copy while the tests measure the
other. Assignments only count for constants, which is what lets `app.py` keep the memo-wrapping
binding under the same name as the core function without shadowing it. Extraction through the AST
was kept rather than switching to plain imports: each caller still supplies its own namespace, so a
core function reaching for the database raises `NameError` at load. `tests/test_core_layering.py`
(11 tests) enforces the same rule from the import side, with a per-module reason for every banned
name.

**The tripwire worked.** `test_app_still_defines_break_sessions_where_the_loader_expects_it`, added
last session against exactly this move, failed as designed and was repointed — now asserting the
function has *exactly one* home, `core/charts.py`.

**Verified.** 549 pre-existing tests pass unchanged; 11 new; 560 total. Mutation-verified on a
copy: **5 faults injected, 4 caught immediately, 1 survivor** (the memo seam) **which produced the
560th test — then re-injected and caught.** Deliberately *not* verified: the dashboard has not been
opened, so nothing here proves the page still renders. `app.py` parses and imports cleanly, and the
extraction touched no page code, but that is an argument, not a check.

---

## ADR-031 — DEBT-026 closed: the whole Mission Control pipeline is pinned, and what that surfaced
**Date:** 2026-07-29 · **Status:** ACCEPTED · **Closes:** DEBT-026 · **Completes:** M2 task 2.0b

**Decision.** Pin the remainder of the pipeline — `_compute_mc_core`, `_build_non_atm_panel`,
`_run_mission_control`, the eligibility registry, and all nine `_load_*` query wrappers — in
`tests/test_mc_pipeline_golden.py`. 38 tests. With ADR-029 and ADR-030 this closes DEBT-026:
**462 → 549 tests** across the three files.

**The single most valuable test in the set.** `_compute_mc_core` ranks *before* capping to
`_MC_HISTORY_CAP`. Its own comment says why, and the comment was the only thing enforcing it. The
test builds 25 symmetric combos at $4.90 and 3 asymmetric ones at $4.10, with a cap of 20: rank
first and all three asymmetric combos survive at the top; cap first by raw gap and every one of
them disappears. A symmetric combo is a degenerate straddle, not this strategy's structure, so
that failure means **the dashboard silently stops showing the trades that are actually taken** —
while still rendering, still looking sorted, and erroring nowhere. Injection 1 confirmed the test
catches exactly that reordering.

**Two module-level hazards found while writing the tests, both real.**
1. `_ELIGIBLE_HISTORY_PATH` is `Path("eligible_history.json")` — **relative**, resolved against the
   working directory. The first draft of these tests would have overwritten the live 599 KB
   registry in the repo root. `load_pipeline()` therefore takes the path as an argument, the same
   shape of guard as `conftest.temp_db`'s production-database assertion. It also means the
   production registry silently depends on where the dashboard was launched from; noted on
   DEBT-011 rather than opened as a new item.
2. `_build_non_atm_panel` accepts a `dte_by_expiry` **parameter** and passes it nowhere;
   `_exp_label()` reads a module **global** of the same name. In production they are one object, so
   the parameter is decorative. Filed with `config.DB_PATH` under DEBT-027 — same defect, same fix
   during the extraction.

**A stand-in for `st.session_state`, and why a real dict rather than a mock.** The "New" badge
logic is entirely about values *persisting between calls*: a card must be new on the first look at
a snapshot and not new on the second. A recording mock would verify that writes happened while
proving nothing about the behaviour that matters. `FakeStreamlit` carries a real dict, and
`FakeStreamlit` having exactly one attribute is itself a check — if it ever needs a second, logic
has started reaching for the page from inside the data layer.

**Stubbing `_scan_all_offsets` was necessary and is defensible.** The rank-before-cap tie cannot be
coaxed out of a real option chain, and the scanner is already pinned by `test_scanner_golden.py`.
Controlling the collaborator is what makes the decision under test observable; testing it through a
real chain would have tested the chain.

**Tally: 32 injections, 31 caught, 1 proven equivalent.** The four initial survivors were
instructive and only one was genuinely equivalent:
- *`is_live` flipped from `>=` to `>`* — missed because every test used gaps like 6.0 or 7.0, which
  cannot distinguish the two. Now tested at exactly $5.00.
- *Call/put mapping inverted* — missed because the assertion was `set(df["side"]) <= {"CALL",
  "PUT"}`, which passes just as happily when C and P are swapped. Now asserts the mapping.
- *The asymmetric-first tier dropped from `likely_next`* — missed because only one card had a
  computable ETA, so the ordering was unobservable. Fixed by teaching the fixture writer to take
  strikes, so two histories can exist at once.
- *`best` ignoring liveness* — **genuinely equivalent.** `_build_non_atm_panel` already sorts live
  cards first, so `next(c for c in panel if c["is_live"])` and `panel[0]` cannot disagree while
  that sort holds. Defence-in-depth, documented at the test rather than forced red.

**The lesson, third occurrence in two days, now with a pattern to it.** ADR-029 said an assertion
protects only what its test data can express. Every one of the three fixable misses above was that
same failure, and each had the same tell: **an assertion over a set, a boundary, or an ordering,
made against data with only one case in it.** One card cannot demonstrate an order. A gap of 6.0
cannot probe a threshold at 5.0. A membership check cannot detect a permutation. Worth reading
before writing the next batch of characterization tests.

---

## ADR-030 — Build the fixture database through `db.py`'s own writers, and let the fixture be the deliverable
**Date:** 2026-07-29 · **Status:** ACCEPTED · **Extends:** ADR-029 · **Partially closes:** DEBT-026

**Decision.** Pin `_candidate_signals` — the source of Duration Active, the ETA, the trend glyph
and the trend arrow on every Mission Control card — against a small **real** SQLite database,
built by calling `db.create_snapshot` / `insert_option_rows` / `finalize_snapshot`. 22 tests in
`tests/test_mission_control_golden.py`, plus a reusable `mc_db` fixture and
`make_transform_history()` writer in `conftest.py`.

**Why the fixture goes through db.py rather than raw INSERTs.** Hand-written `INSERT` statements
would be shorter and would not break when the schema changes — which is exactly the objection.
A fixture that survives a schema change keeps testing a shape production no longer has, and
reports green while the real pipeline is broken. Going through the writers means the fixture
fails loudly the day the schema moves, which is the behaviour worth paying for.

**Why the gap is engineered to equal one leg's mark.** The transform gap is
`(front_call + front_put) − (wing_call + wing_put)` once the back legs cancel. Setting
`front_put = 10` and both wings to `5` makes **gap == front call mark** exactly. So
`write([4.0, 5.5])` means what it says, and a test about the `$5` threshold reads honestly
instead of hiding the number behind six leg prices.

**Two constraints that would have made the fixture rot silently, both written into it:**
1. The query filters `snapshot_timestamp >= datetime('now', '-N days', 'utc')`, so timestamps
   are computed **relative to now**. Hardcoded dates would pass today and quietly return zero
   rows forever after — a test that decays into a no-op without ever going red.
2. Any snapshot missing a computable mark on **any** of the six joined legs is excluded whole.
   The fixture can produce that case on purpose (`missing_leg_indices`) because it is the
   dangerous one: the snapshot is `COMPLETE` and looks healthy.

**The correction this task produced — and it changes what we believe protects what.** I wrote
that the headline M2 risk was `df.sort_values("timestamp")` being lost when the query splits out
into `data/queries.py`. Injecting exactly that changed nothing; all 19 tests passed. Rather than
bend a test to force it red, I checked: the pandas sort is genuinely **redundant**. The SQL ends
in `ORDER BY s.snapshot_timestamp`, that clause *is* load-bearing — stripped, rows return in
insertion order, verified directly — and it is **already pinned**, in
`test_db.py::test_transform_mark_history_is_ordered_oldest_first`, which inserts snapshots 1, 3
and 2 days back precisely so insertion order and time order disagree. So the protection was
real and already existed; it simply lives in the query's tests. The useful M2 instruction is
therefore different from the one I first wrote down: **when that query moves, its ordering test
must move with it.** This is the third time the ADR-027 rule has paid: an injection that changes
nothing is evidence about the code, not a dud test.

**Second proven-equivalent mutant, with a small finding attached.** Removing
`.dt.tz_convert(config.DISPLAY_TIMEZONE)` changes none of the four outputs — confirmed by running
the real function under `America/New_York`, `Asia/Tokyo` and `UTC` and getting identical results.
It cannot matter: every output is either a **difference** between timestamps (timezone-invariant)
or derived from gap values alone, and the function returns no timestamp at all. The comment on
that line reads *"naive wall-clock: required by Plotly rangebreaks"*, which is true where it was
copied from and misleading here, since this function never plots. Harmless; noted so an M2
tidy-up is an informed choice.

**Also pinned deliberately: one arguably-wrong behaviour.** The eligibility streak is contiguous
in **snapshots**, not in time. With a 90-minute hole between polls — a collector outage — the two
readings either side count as one unbroken streak, so "Duration Active" spans a period during
which nothing was observed. Frozen rather than fixed: the alternative (break the streak on a time
gap, as `_break_sessions` does for charts) is a decision about what *active* should mean, and it
belongs to whoever owns the strategy, not to a refactor.

**Scope deliberately left open.** `_compute_mc_core`, `_build_non_atm_panel` and
`_run_mission_control` are still unpinned; DEBT-026 stays open for them. `_candidate_signals` was
taken first because it is self-contained and proves the fixture design before the composite
functions depend on it.

**Tally:** 11 injections, **9 caught, 2 proven equivalent.** Two of the nine only became catchable
after fixing test *data* that could not express the fault — a 12-reading sparkline window cannot be
tested with 8 readings, and a 3-reading trend window cannot be tested with a series where 2 and 3
agree. Same lesson as ADR-029, now twice observed.

---

## ADR-029 — Pin the display layer before M2, and write assertions a fault can actually fail
**Date:** 2026-07-29 · **Status:** ACCEPTED · **Extends:** ADR-025 · **Precedes:** M2

**Decision.** Before M2 moves any code, add golden/characterization tests over the *display*
layer of `app.py` — the part between a computed number and the screen: card ordering
(`_rank_for_panel`), chart geometry (`_banded_ratio_traces`), the on-card formatters
(`_sparkline`, `_fmt_duration`, `_fmt_eta`) and card identity (`_card_key`). 28 tests, in
`tests/test_display_golden.py`.

**Why this layer, and why now.** M1.4 pinned what the scanner *computes*. Nothing pinned what
the screen *shows*. Those are different failures with very different visibility: a scanner
that returns wrong numbers is at least checkable against the database, whereas a panel whose
rows come back in a different order still looks entirely plausible. Ordering is not cosmetic —
the top card is the one that gets traded. A refactor that reversed a sort key would be
invisible until it had influenced real decisions for weeks.

**Why not launch the app instead.** `streamlit.testing.v1.AppTest` exists and would be a truer
end-to-end test. It was rejected *for now*: `app.py` queries the production database at module
level, so an AppTest either points at the real 1.4 GB database — which `conftest.py` forbids
outright — or needs a full snapshot fixture, which is a larger piece of work than the whole of
this task. Pinning the pure layer costs almost nothing and covers the failure mode we actually
fear. The DB-backed pipeline (`_compute_mc_core`, `_candidate_signals`, the eleven `_load_*`
queries) remains **unpinned**, and is logged as DEBT-026 rather than quietly left out.

**The reason `tests/app_loader.py` grew a second entry point rather than one wider namespace.**
Each caller supplies its own execution namespace. The scanner is deliberately given no plotly
and no `db`/`config`/`streamlit`, so a scanner that starts reaching for I/O or drawing fails
loudly in the loader. The display layer legitimately needs plotly. Merging the two namespaces
would have quietly destroyed the scanner's guarantee to save one function.

**The lesson worth keeping — the sibling of ADR-025.** Nine faults were injected into a *copy*
of `app.py` (never the real file; the dashboard is running and Streamlit hot-reloads). Seven
were caught first time. The two survivors were both **weak assertions, not missing tests**:

1. *"Ranking must not mutate its input."* The function copies its argument first, so the
   scratch column could never reach the input. The assertion was checking something that
   cannot break. The leak shows in the **return value** — a stray column on the panel.
2. *"A long series must be downsampled, not truncated."* Fed `range(100)`, downsampling and
   truncation produce the **identical** glyph, because a straight line looks the same however
   you sample it. The fault only appears on a series that changes direction, so the test now
   uses one that rises then falls — where truncation would report a peak as a climb.

ADR-025 said a characterization test protects only what reaches its *output*. This adds:
**an assertion protects only what its *input* can express.** Both failures looked like
green tests. Choosing test data is part of writing the assertion, not a detail beneath it.

**Also pinned deliberately: one arguably-wrong behaviour.** `_RATIO_BANDS` compares inclusively
at both ends, so a ratio of exactly 1.00 is drawn in two overlapping bands. It is invisible in
practice (a float never lands exactly on a boundary) and "fixing" it means deciding which band
owns the edge — a real decision, not a typo. Frozen with the reasoning inline so a future
change is made on purpose rather than by accident.

---

## ADR-028 — The pre-commit hook stands in for CI; `docs/TESTING.md` is retired, not deferred
**Date:** 2026-07-28 · **Status:** ACCEPTED · **Closes:** the two open items from `AUDIT` §10 M1.8 and M1.9

**Why this exists at all.** M1 was declared complete, but two tasks from the original roadmap had
quietly not happened: GitHub Actions CI, and a `docs/TESTING.md`. Neither was refused — both just
stopped being mentioned. Found 2026-07-28 by reading the audit against `plan.md`. An unrecorded
omission is indistinguishable from an oversight, and gets re-proposed forever. These are now
decisions.

### 1. CI: the pre-commit hook is the answer, and that is deliberate

**Decision:** `.githooks/pre-commit` running the full suite on any staged `.py` file **satisfies**
the M1 exit criterion. No GitHub Actions workflow will be added at this stage.

**Reasoning.** The criterion was written as "CI green", but the *intent*, stated in the same
milestone, was **checks that run without being remembered**. The hook delivers that intent: it is
live via `core.hooksPath`, it blocks a failing commit (verified by deliberately breaking a test),
and it skips docs-only commits so editing the backlog stays instant. For a single developer on a
single machine, a workflow would re-run the same suite minutes later and change no decision.

**The costs, accepted with eyes open — this is the part that matters if it is revisited:**
- It runs **only on this machine.** A commit pushed from anywhere else is unchecked.
- It is **bypassable** with `SKIP_TESTS=1`, by design, for when the environment rather than the
  code is broken. A bypass leaves no trace in the history.
- It runs **before** the commit, not after the push, so nothing verifies what actually landed on
  `origin`.

**Revisit when any of these becomes true:** a second machine or a second person commits; the repo
gains an outside contributor; or M8 arrives, where "operate reliably without babysitting" makes an
unbypassable post-push check worth its cost. Until then this is sufficient, not merely tolerated.

**Alternative rejected:** adding the workflow now "because it is cheap". It is cheap to add and
not free to own — a red build nobody is obliged to fix teaches everyone to ignore builds, which is
the same failure mode ADR-015 avoids by refusing to gate on 85 unresolved lint findings.

### 2. `docs/TESTING.md`: retired

**Decision:** it will not be written. The audit's MEDIUM rating is **withdrawn**, not deferred.

**Reasoning.** The document was specified in July 2025 for a repo with **zero tests**, where a
written map was the only possible orientation. That premise is gone. There are now 462 tests, and
the conventions live where they are used and cannot drift from it:
- `conftest.py` carries the scope rule (no fixture may touch the 1.4 GB production database, with
  an assert enforcing it), the IV percentage-vs-decimal convention, and why `FakeRow` imitates
  `sqlite3.Row` rather than using a dict.
- Each test module's docstring states what layer it covers and why it is shaped that way —
  `test_check_db.py` explains why it must own a subprocess, `test_collector_cycle.py` why the
  chain fixture is raw Schwab JSON rather than a parsed frame.

**The deciding argument, and it is the user's:** writing the document accurately would mean
re-reading all 462 tests, and keeping it accurate would mean doing that again after every
milestone. That is a large, permanently recurring cost for a summary of information that is
already correct at the point of use — and a summary that goes stale is worse than none, because it
is believed. **A second description of the tests is a second thing that can be wrong about them.**

**Tradeoff accepted:** there is no single front door to the test suite. Someone new — including a
future session with no context — must open `conftest.py` and read module docstrings rather than
one page. Judged acceptable: that reading is *guaranteed current*, which no separate document can
promise.

**What would reopen this:** a second contributor who needs to add tests without reading the suite
first. Then write it, and gate merges on it being updated — because at that point it has an owner
and a forcing function, which is exactly what it lacks today.

---

## ADR-027 — Store in UTC, but report in Eastern; the storage choice does not settle the reporting one
**Date:** 2026-07-28 · **Status:** ACCEPTED · **Closes:** BUG-019

**In plain terms:** every timestamp in the database is UTC, which is correct and stays. But the
health check's "snapshots today" figure was *counted* in UTC too, and those are separate
decisions. UTC's day rolls over at 8pm New York time, so from 8pm onward the count dropped to 0
while the collector was working perfectly — and 0 on the top line of the session-start check looks
exactly like a dead collector. Meanwhile the hours before that silently mixed in the previous
evening. Confirmed live: 126 at 19:59 UTC, then 0 an hour later, same healthy database.

**Decision:** count against the **Eastern trading day**, and print the date alongside the figure
(`Snapshots today : 126  (2026-07-28 ET)`) so the boundary in use is never left to guess. The
ambiguity is what turned an off-by-one into a suspected outage.

**How.** Compute the day's UTC window in Python and pass explicit bounds to a half-open range
query. Not in SQL: SQLite has no timezone support, only fixed `'localtime'` and `'utc'` modifiers,
so an in-SQL version would either hardcode an offset that breaks twice a year or depend on the
machine's own timezone — a different bug in the same clothes. The range also uses the existing
`idx_snapshots_timestamp` index instead of converting every row.

**A correction worth recording, because it was believed and written down before it was checked.**
The first version of this reasoning claimed that computing the day's end as `start + timedelta(
days=1)` is a daylight-saving bug. **It is not.** Arithmetic on a zoneinfo-aware datetime is
wall-clock arithmetic, so it lands on the next local midnight, not 24 hours later. The genuine
trap is converting to UTC *first* and then adding 24 hours — that one is wrong on the two
changeover days, and it is what the DST tests actually pin. The mistaken version was caught only
because a deliberately injected fault **failed to fail**: injecting it changed nothing, which is
the signature of a belief about the code that the code never held. **A no-op injection is
evidence, not a dud.**

**Scope.** `scripts/check_db.py`. A sweep found no other `DATE('now')` in the project. Separately
noted, not fixed: `collector.py` uses `date.today()` for the option-chain fetch window, which is
the *machine's local* date. It is right today only because this machine is set to Eastern, and
being a day out at the edge of a fetch window is harmless. Left alone deliberately rather than
widened into an unrequested change.

---

## ADR-026 — Some faults live between the process and its output, and only a subprocess can see them
**Date:** 2026-07-28 · **Status:** ACCEPTED · **Closes:** BUG-018

**In plain terms:** the health check run at the start of every session draws its report with box
characters (═ ─ →). It died before printing a single line — but only sometimes, which is why it
survived this long. When its output goes straight to a terminal window, Windows hands those
characters over intact. When the output is **redirected or piped** — into a file, into Git Bash,
into any wrapper that captures it — Python instead falls back to the machine's regional encoding,
cp1252, which has no way to write those characters at all. The script then stopped with an error
instead of a report.

**Decision:** widen the stream to UTF-8 at the start of `main()`, rather than replacing the box
characters with plain ASCII. The aligned rules are what make the report scannable at a glance, and
this is the very first thing run in a session. A second safeguard converts anything still
unwritable into `?`, because a blemished health check beats no health check.

**The transferable lesson, and the reason this is an ADR at all.** This fault is **invisible to an
ordinary test.** Running the script inside the test suite proves nothing, because pytest replaces
the output stream with its own object that handles these characters happily — the very thing that
breaks is never present. The bug does not live in the code; it lives in the relationship between
the program and wherever its output is going. **A test that owns only a function cannot see a
fault that lives in the process.** So these tests launch the script as a real separate process
with the output deliberately set to cp1252.

**Alternatives rejected.** *Strip the characters to ASCII* — cheapest, but degrades the artefact
to dodge an environment problem, and any future use of a non-ASCII character reopens it silently.
*Tell people to set `PYTHONIOENCODING`* — a fix that has to be remembered is not a fix; that is
precisely how it went unnoticed for months. *Switch the whole project to UTF-8 mode* — larger
blast radius than the fault deserves, and it would hide the same class of bug elsewhere rather
than fix it.

**Scope.** `scripts/check_db.py` only. Other scripts printing non-ASCII to a redirected stream
have the same latent fault; none is on the session-start path, so none is fixed here.

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
