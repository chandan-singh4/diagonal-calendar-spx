# PROJECT STATUS

**Updated:** 2026-08-09 · **Branch:** `m3-data-hardening` — **stage 3 under way.**
**State:** Three parts of stage 3 are built and checked. 788 checks pass. The work is saved on
this machine and **sent to GitHub on a branch, not merged into `main`** — merging is Chandan's
call. Nothing in it has touched the real database.
> Self-contained: read this file alone to start a session. Replaced entirely by `/wrap`.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX) — contracts to buy or sell at a set price
before a set date. His strategy: sell options expiring soon, buy similar ones expiring later, pay
the small difference. The soon-expiring ones lose value faster, and that gap is the profit; once
it's worth enough he restructures into a safer shape that locks the gain and caps the loss. **It's
all about timing**, and brokers discard today's prices rather than keep them. **So the historical
record IS the product** — the screen is just a window onto it. **Honest condition:** record and
checking are in good shape; the screen's known faults are listed below and are now fewer.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts with Windows. |
| **Database** | One file, **2.0 GB** since 23 June, growing ~82 MB a trading day. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs: Scanner, Entry Analysis, Calendar Edge, Strike Detail, Historical Stats, Research. Reads only. |
| **Journal** | Diary of actual trades. 6 practice entries, to be discarded. |

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **done** →
`3 stop database growing` **← here, 3 of 9 parts done** → `4 data service` → `5 decide on rebuilding the screen` →
`6 answer trading questions with real results` → `7 machine learning` → `8 run reliably unattended`
Order is fixed: **you can't safely rearrange code you can't check automatically.** Stages 6 and 7
also need ~20 and ~100 real trades; there are 6 practice ones.

## This session

**The collector had been blind and nothing said so.** The permission slip the broker issues (the
"token") had quietly expired. No prices were lost — the markets were shut all weekend — but
Monday's opening would have been lost in silence. Chandan renewed it. **This is why the next
piece of work is a watchdog that shouts when collection stops** (see below).

**Stage 3, part 1: the rule for clearing out old prices — Chandan's decision, written down.**
Per-strike prices may be deleted **90 days after the option they describe has expired**. The
daily *summaries* are kept **forever** (all of them together are 5 MB — 0.26% of the file, so
keeping the whole history costs nothing). **Any expiry a real trade actually used is never
deleted, at any age** — logging a trade protects its own data automatically, with nothing to
remember. Recorded in `docs/decisions.md` as ADR-044, along with the alternatives he turned down.

**Something had to be built first, or the rule would have destroyed the journal's memory.**
The screen worked out the market conditions at the moment a trade was opened by *going back and
reading the old prices*. Clear those out and that question becomes permanently unanswerable —
and **silently**: the chart would simply show fewer trades each month with nothing saying why.
So the answer is now **written onto the trade itself the moment it is saved**, by the saving code
rather than by the screen, so no future screen can forget to do it. Old trades still fall back to
the old method.

**Stage 3, part 2: the clearing-out tool exists — and it deletes nothing unless asked three
times.** `python scripts/prune.py` **reports** by default; deleting needs `--execute`, which
refuses to run without a backup newer than the database, and then asks for the **exact number of
rows in figures**. "y" is rejected on purpose — a number has to be read off the report first. If
nobody is at the keyboard it cancels. Rehearsed against the real database in report mode: asked
what it *would* do in December, it correctly held back the 8 expiries belonging to the practice
trades. 31 new checks cover it.

**The thing to understand about that: it clears nothing today, and nothing until about November.**
Collection only started 23 June — 47 days ago — so nothing is yet 90 days past expiry. The tool
had to be built before the data aged into it, but the file keeps growing until then.

**Stage 3, part 4: something now shouts when collection stops — and it isn't the screen.**
Every 10 minutes, day and night, Windows runs a small check that asks one question: *during market
hours, is a price arriving as often as it should be?* If not, a pop-up appears on this machine
**and an email is sent to Chandan's phone**. It only watches — it never restarts anything, never
renews the permission slip, and never writes to the database. Tested live: pop-up seen, email
received, and the 10-minute schedule confirmed firing.

**The reason it had to live outside the dashboard.** The red TOKEN EXPIRED banner on the screen was
working perfectly this morning. Nobody was looking at it. **An alarm you can only hear by opening a
page is not an alarm.** The banner stays — it is right, and it costs nothing — but it is no longer
the only thing that tells you.

**Most of the effort went into it staying quiet when nothing is wrong.** An alarm that goes off
every evening, every weekend, at 09:31 every morning, and once between every pair of price updates
is an alarm you learn to ignore — and then miss on the day it is right. It stays silent overnight,
at weekends and on holidays, allows a few minutes at the opening for the first prices to land,
waits for **two** missed updates rather than one, and repeats itself at most once an hour during a
single outage.

**One found bug is worth naming, because it was the dangerous kind.** It came out of Chandan asking
whether a check every 10 minutes meant an email every 10 minutes. At 16:00 the market shuts and the
check goes quiet — so a collector that had been dead all afternoon would have triggered an email
saying **"RECOVERED: prices are arriving again."** They were not. The market had simply closed and
the watchdog had gone blind. **A false all-clear is the worst thing an alarm can say**, because it
is the message that stops you checking. It now says "I have no news" rather than "all is well", and
only announces a recovery after actually seeing fresh data.

**The screen now has a ticking clock and counts the other way.** The old *"Next update in: 42s"* is
gone. In its place: the live time, ticking every second so you can see the page is not frozen, and
**"Time since last data"** counting upward. The countdown had a resting state that looked healthy —
with the collector dead it showed `0s` and sat there. Counting upward has no such state: the longer
it is broken, the bigger the number gets. It turns amber at Chandan's thresholds (1 minute in the
first and last half hour, 5 minutes midday) and red at half again as long — **deliberately not red
exactly on the threshold**, because at the 5-minute cadence the age reaches 5 minutes just before
every single update and would flash red all day for no reason.

**A mistake worth remembering: `git checkout` undid an hour of unsaved work.** It was used to undo
a deliberate temporary break in a file — and it reverted every other unsaved change in that same
file with it. The project rule says rehearse on a *copy*; `git checkout` is not a copy, it is the
opposite. Later checks copied the file aside first and put it back by hand.

## What to do next

1. **Decide whether `m3-data-hardening` merges into `main`.** The work is on GitHub as a branch.
   Merging is a one-line job whenever Chandan says so.
2. **Write down the permission-slip renewal steps (part 8).** The watchdog now tells you the
   moment collection stops; it does not tell you what to do about it, and that chore comes round
   about every week. This is the natural pair to what was just built.
3. **The watchdog has one thing still unproven.** Pop-up, email and schedule are all confirmed
   working, but a *real* outage has never travelled the whole path — that cannot be staged without
   stopping the collector or altering the database, both of which need Chandan's word. The first
   genuine outage is the test.
4. **Then parts 5 and 6.** The database has recorded every gap in collection since day one and the
   screen has never once shown them. And there is a standing puzzle worth explaining: the collector
   reports **"160 of 3,156 rows discarded" on nearly every cycle**. A number that steady is a
   pattern, not chance.
5. **After a day of collection, read `collector.log`** for lines beginning `strike window:
   broker supplied` — they show how much room exists beyond what's kept. Nothing depends on it yet.

## Open problems

- **ENH-011 (high)** — tab clicks are slow. Cause **not established**; measure first, and don't
  start by tuning the cache timers. **BUG-001 (high, blocked on Chandan)** — old unexplained
  report; nothing can be done until he gives a symptom and a screenshot. **BUG-018 (medium)** —
  on expiry day one tile says "set strikes" when they are already set.
- **DEBT-029** — two screen-library features are past their removal dates, used in ~36 places.
  The screen runs only because the old versions still work; any upgrade may break it.
- **DEBT-034** — data loaded and converted for a column nothing reads. **DEBT-036/037** — a dead
  file and ~50 lines of unused styling, both left on purpose; deleting needs Chandan's word.
  **DEBT-038** — problem numbers reused twice, breaking the documented way to look up a closed one.

## Settled decisions

- **The screen stays as it is until stage 5**; whether to move off the current screen technology
  is **not pre-committed** — decided with evidence at stage 5, not before.
- **Closing a problem means deleting its row**, never ticking it off — if the fix leaves a lesson,
  write it up in `docs/decisions.md` first. **Never re-record a failing check to make it pass.**
- **Move code first, rename second, separately** (two renames outstanding: DEBT-033, DEBT-035).
  **The 6 practice trades are blocked** on Chandan at the keyboard with a confirmed backup.
- **Old prices are cleared 90 days past expiry, summaries kept forever, traded expiries never
  cleared, and it never happens on a timer** — only when Chandan runs it and confirms (ADR-044).
  **Reclaiming the disk space needs a separate `VACUUM` step**, which locks the file and needs
  free space equal to the database; the tool prints the command rather than running it.
- **The watchdog watches and tells you; it never acts.** No restarting, no renewing, no writing.
  Deciding what to do about a dead collector stays with Chandan — automatic recovery is a stage 8
  job. And **"no news" is never reported as "all is well"** (ADR-045).
- **Keeping a saved position's prices is forward-only** — it cannot fill in history from before
  the position was saved. That is why the on-screen warning stays.

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or installed programs, stopping/starting the collector, or sending anything off this
machine. **No check may touch the real database. Trade numbers are never reused. Missing price →
blank, not 0. Prove checks by breaking the code on a copy**, never the live file — the dashboard
reloads the moment a file is saved, **and that reload rewrites the saved-opportunities file.**
**Deeper detail:** `docs/` — `plan.md` (stages) · `backlog.md` (open problems only) · `decisions.md` (why) · `progress_log.md` (per session).
