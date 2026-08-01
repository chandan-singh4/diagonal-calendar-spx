# PROJECT STATUS

**Updated:** 2026-08-01 · **Branch:** `main` — **stage 2 merged and saved online.**
**State:** Two faults fixed and checked on the real screen. 693 checks pass. **Stage 3 starts
next; nothing blocks it.** This session's last two changes are saved on this machine but **not
yet sent to GitHub** — send them first.
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

**Stage 2 is signed off.** Chandan looked at the charts — the one thing last session's automatic
word-by-word comparison could not do. Everything then merged into the main line of work.

**A saved trade could be charted as a different trade — fixed at the cause, Chandan's idea.**
The collector only records prices near where the index is **today**; a saved position is fixed at
the price it was opened at. As the index drifts, that position's prices stop being recorded, so
the screen couldn't find them, quietly substituted its nearest guess, and drew a confident chart
of the wrong trade. The first plan was only to warn on screen. Chandan asked whether those prices
could simply keep being recorded instead. **Checking the stored data showed he was right and it
was nearly free — the broker was already sending them and we were throwing them away.** Both were
done. Verified live: saved a position, restarted the collector, opened its chart — correct.
**A claim made here was wrong and measuring took a minute:** the broker's limit was said to bind
much sooner, making this expensive. It doesn't — the assumption was wrong in the direction that
would have cost the most work.

**Four summary figures at the top of the Scanner were deleted — Chandan's call.** They vanished by
accident a month ago while the sums behind them kept running. He chose deleting over restoring,
knowing that is the direction that can't be undone by looking at the screen; `docs/decisions.md`
(ADR-043) records the exact commands to bring them back.

**For the third time, something that looked dead was holding something up.** Ten of the eleven
values behind those figures were unused; the eleventh feeds a badge still on screen. Removing the
lot **was tried on a copy first and broke all six tabs** — and all 693 checks still passed.
`python scripts/render_check.py` caught it, as twice before. **Run it after any screen change.**

## What to do next

1. **Send this session's work to GitHub** (`git push origin main`) — ask Chandan first.
2. **Start stage 3 — stop the database growing.** Nothing blocks it. Build one thing alongside it:
   the app must save market conditions *with each trade*, or trades logged from now on lose that
   context once old prices are cleared out.
3. **After a day of collection, read `collector.log`** for lines beginning `strike window:
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
- **Keeping a saved position's prices is forward-only** — it cannot fill in history from before
  the position was saved. That is why the on-screen warning stays.

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or installed programs, stopping/starting the collector, or sending anything off this
machine. **No check may touch the real database. Trade numbers are never reused. Missing price →
blank, not 0. Prove checks by breaking the code on a copy**, never the live file — the dashboard
reloads the moment a file is saved, **and that reload rewrites the saved-opportunities file.**
**Deeper detail:** `docs/` — `plan.md` (stages) · `backlog.md` (open problems only) · `decisions.md` (why) · `progress_log.md` (per session).
