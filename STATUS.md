# PROJECT STATUS

**Updated:** 2026-07-26 · **Branch:** `main` · 9 saved points added today, all saved online.
**State:** Checking grew 151 → **329 checks**, all passing, now **run automatically** on every
save. Five faults fixed. The price record is untouched and healthy.

> Self-contained: read this file alone to start a session. Replaced entirely by `/wrap`.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX). An option is a contract to buy or sell at a
set price before a set date. His strategy: sell options expiring soon, buy similar ones expiring
later, pay the small difference. Options expiring soon lose value faster — that gap is the profit.
Once it's worth enough, he restructures into a safer shape that locks the gain and caps the loss.
**It's all about timing.**

Brokers show today's prices then discard them. Judging the moment needs weeks of history on the
exact contracts he'd trade, and nobody keeps that for you. **So the historical record IS the
product** — the screen is just a window onto it.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts automatically when Windows starts. |
| **Database** | One file, ~1.3 GB of prices since 23 June. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs, charts built from that history. Reads only, never writes. |
| **Journal** | Diary of actual trades, to later check results against predictions. |

**Condition:** trading logic and collector are good; the checking net is now real and
self-running. The screen's code is still one huge file.

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **← here, mostly done** → `2 break up big files` →
`3 stop database growing` → `4 data service` → `5 decide on rebuilding the screen` → `6 answer
trading questions with real results` → `7 machine learning` → `8 run reliably unattended`

Order is fixed: **you can't safely rearrange code you can't check automatically.**

## This session (26 July)

1. **The checks now run on their own** whenever work is saved — the exact weakness stage 1
   exists to remove. It proved its worth the same day, catching a change that broke 34 checks.
2. **The database go-between is fully checked** (111 checks, every line) — the piece every
   other part reads through, which had none.
3. **Five faults fixed**, each recorded first and fixed second, so the fix shows as a visible change.
   - Deleting a trade made the *next* trade fail to save — this blocked discarding the 6
     practice trades. Now safe.
   - A missing price was shown as £0.00 in profit figures.
   - Asking for one past day of prices returned that day *and every day after it*.
   - The collector called every ordinary night and weekend a breakdown.
   - A chart drew a straight line across the 3 July holiday, inventing price movement.
4. **Two corrections to things previously believed true.** The "collector cries wolf" fault was
   **also deaf**: a collector dead for three trading days was filed as routine and discarded,
   leaving no trace — the worst loss possible here, and invisible. It had also been blamed on the
   wrong code; fixing only that would have left the real source untouched and looked successful.
5. **The old damage figures were nonsense**: records claimed 19,759 lost readings, more than the
   database has ever held. True figure is 145. Corrected in place, old values kept.
6. **Code proofreading tool installed** and run for the first time. Found no faults, but produced
   an exact list of the "silently ignore all errors" spots in the profit-and-loss screen.

## What to do next

1. **Finish checking the collector.** Its gap logic is done; the actual price-fetching cycle,
   retries and login handling are still unchecked. Largest unchecked area left.
2. **Discard the 6 practice trades** — now safe. Note the standing commitment below.
3. **Fix the "silently ignore all errors" spots** in the profit screen (DEBT-007; line numbers
   in `docs/backlog.md`). A failed calculation currently shows a blank with no reason.
4. **One question for Chandan:** the statistics code works out a "break-even trades" figure and
   never shows it — display intended and forgotten, or just leftover?
5. Then stage 2 — breaking up the 4,230-line screen file.

## Open problems, priority order

| Problem | Meaning | Stage |
|---|---|---|
| **Unexplained July issue — BLOCKED ON CHANDAN** | "Still having some issue." No leads. **Needs: what looked wrong, which tab, screenshot.** | — |
| **Dashboard is reachable from other devices on the network** | Confirmed today, not just suspected. Should be locked to this machine before any remote access work. | — |
| Errors silently ignored in the profit screen | ~30 places; a failure shows as a blank | 2 |
| 4,230-line dashboard file | Appearance, calculations and data all mixed together | 2 |
| Backups manual; database growing without limit | Backups work but nobody runs them on a schedule; ~82 MB per trading day | 3 |

## Settled — don't reopen

- **Screen stays as-is for now.** Revisit at stage 5.
- **Trade numbers are never reused** — a deleted trade leaves a permanent gap. The diary is
  evidence, so a number must never change meaning.
- **When a price is missing, show nothing rather than zero.**
- **The 6 practice trades will be discarded**, diary restarts clean. **Consequence:** the app must
  save market conditions *with each trade* before serious trading resumes.
- **Record a fault first, fix it second** — otherwise you can't tell whether the check proved the
  code right or the code was bent to fit the check.

## How to work here

Plan before coding · rehearse risky changes on a copy · **prove checks work by deliberately
breaking the code** · challenge assumptions with evidence · say plainly when something is
unverified · **ask before** deleting, changing the database, saving work, or changing system
settings · finish with `/wrap`.
**Deeper detail:** `docs/` — `plan.md` (tasks) · `backlog.md` (open problems) · `decisions.md`
(why; ADR-022…024) · `progress_log.md` · `DOCUMENTATION.md` (strategy/maths).
