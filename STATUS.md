# PROJECT STATUS

**Updated:** 2026-07-31 · **Branch:** `m2-core-extraction`, **pushed to GitHub, not yet merged.**
**State:** **Stage 2 is finished — all 5 steps.** 659 checks pass. Price record healthy (126
recordings today, latest 15:59 New York). All six tabs run, checked by machine — **but Chandan has
not looked at the screen since the change.**
> Self-contained: read this file alone to start a session. Replaced entirely by `/wrap`.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX) — contracts to buy or sell at a set price
before a set date. His strategy: sell options expiring soon, buy similar ones expiring later, pay
the small difference. The soon-expiring ones lose value faster, and that gap is the profit; once
it's worth enough he restructures into a safer shape that locks the gain and caps the loss. **It's
all about timing**, and brokers discard today's prices rather than keep them. **So the historical
record IS the product** — the screen is just a window onto it. **Honest condition:** record and
checking are in good shape; the screen has real faults, all known and listed below. Backed up to
GitHub; nothing *runs* anywhere but this machine.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts with Windows. |
| **Database** | One file, ~1.8 GB since 23 June. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs: Scanner, Entry Analysis, Calendar Edge, Strike Detail, Historical Stats, Research. Reads only. |
| **Journal** | Diary of actual trades. 6 practice entries, to be discarded. |

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **done** →
`3 stop database growing` **← here** → `4 data service` → `5 decide on rebuilding the screen` →
`6 answer trading questions with real results` → `7 machine learning` → `8 run reliably unattended`
Order is fixed: **you can't safely rearrange code you can't check automatically.** Stages 6 and 7
also need ~20 and ~100 real trades; there are 6 practice ones.

## This session

**The main screen file went from 2,505 lines to 392 — and from 4,283 when the stage began.** It
now only assembles: load prices, work out the few numbers several tabs share, hand over to the tab
being shown. Styling, sums, database questions and the scanning engine each have their own folder
now, each with a rule a check enforces.

**The task as written didn't add up, and saying so first changed the job.** The notes named two
pieces to move. Both are done — but together they were 872 of 2,505 lines, landing at ~1,630 and
missing this step's own "under 400" target fourfold. Chandan saw the arithmetic and chose to finish
it properly.

**Partway through, one move broke all six tabs — and all 639 checks still passed.** One reference
didn't follow its value into a new file; the checks exercise pieces of code, not the screen. **What
caught it** was running the whole screen twice — old version and new, same database — and comparing
every word each drew. Done after all eight stages of the work; all eight identical.

**A check written last session turned out to be blind.** It guards against a chart whose time axis
would silently shift four or five hours, but looked for a name spelled one way where every place
that matters has an extra underscore — so half of it had never done anything. Found only by
deliberately breaking the code: **13 breakages attempted, 13 caught** after the fix.

## What to do next

1. **Have Chandan open the screen and look at the charts.** The comparison proves every *word* is
   unchanged but **cannot see inside a chart** — check Calendar Edge and Strike Detail by eye.
2. **Then start stage 3 — stop the database growing**; nothing blocks it. Get Chandan's decision
   on BUG-019 and BUG-022 when convenient — both wait on him, not on work.

## Open problems

- **BUG-022 (high) — blocked on Chandan.** Clicking "View Chart" on a saved lock can silently show
  a **different** diagonal than the one clicked, when the strike or back expiry isn't in today's
  data. Expiry was one cause and is fixed; two remain that hit *live* positions. He asked whether
  that fix made this moot — it doesn't — and hasn't said whether to do it.
- **BUG-019 (medium) — blocked on Chandan.** Four summary figures vanished from the top of the
  Scanner a month ago, by accident; the code behind them still runs. He must choose: restore the
  missing line, or delete the 40 lines behind it. **Don't default to deleting** — that is the
  choice that can't be undone by looking at the screen.
- **ENH-011 (high)** — tab clicks are slow. Cause **not established**; measure first, and don't
  start by tuning the cache timers. **BUG-001 (high, blocked on Chandan)** — old unexplained
  report, needs a symptom and a screenshot. **BUG-018 (medium)** — on expiry day one tile says
  "set strikes" when they are already set.
- **DEBT-029** — two screen-library features past their removal dates, in ~36 places. **DEBT-034**
  — data loaded and converted for a column nothing reads; check history for a removed chart first.

## Settled decisions

- **The screen stays as it is until stage 5**, and whether to move off the current screen
  technology is **not pre-committed** — decided with evidence at stage 5, not before.
- **Closing a problem means deleting its row**, never ticking it off — if the fix leaves a lesson,
  write it up in `docs/decisions.md` first. **Never re-record a failing check to make it pass**;
  a deliberate break that changes nothing is evidence about the code, not a check to bend.
- **Move code first, rename second, in separate commits** — that is what makes a move provable
  (two renames are deliberately outstanding: DEBT-033, DEBT-035).
- **The 6 practice trades are blocked** on Chandan at the keyboard with a confirmed backup, and the
  app must save market conditions *with each trade* before real trading resumes.

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or installed programs, stopping/starting the collector, or sending anything off this
machine. **No check may touch the real database. Trade numbers are never reused. Missing price →
blank, not 0. Prove checks by breaking the code on a copy**, never the live file — the dashboard
reloads the moment a file is saved, **and that reload rewrites the saved-opportunities file.**
**Deeper detail:** `docs/` — `plan.md` (stages) · `backlog.md` (open problems only) · `decisions.md` (why) · `progress_log.md` (per session).
