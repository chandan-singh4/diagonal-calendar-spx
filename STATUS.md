# PROJECT STATUS

**Updated:** 2026-07-26 · **Branch:** `main` · everything merged, committed and saved online (`c0180e3`)
**State:** Foundation cleanup done. Automatic checking started — **140 checks, all passing.**
**Not re-checked by eye this session:** the screen was never opened. See "What to do next", item 1.

> Self-contained: read this file alone to start a session. No need to open other files unless doing deep work.
> Replaced entirely by `/wrap` each session. Keep under 100 lines.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX). An option is a contract to buy or sell at a set
price before a set date. His strategy: sell options expiring soon, buy similar ones expiring later, pay the
small difference. Options expiring soon lose value faster — that gap is the profit. Once it's worth enough,
he restructures into a safer shape that locks the gain and caps the loss. **It's all about timing.**

Brokers show today's prices then discard them. Judging the moment needs weeks of history on the exact
contracts he'd trade, and nobody keeps that for you. **So the historical record IS the product** — the
screen is just a window onto it.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts automatically when Windows starts. |
| **Database** | One file, 1.42 GB of prices since 23 June. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs, charts built from that history. Reads only, never writes. |
| **Journal** | Diary of actual trades, to later check results against predictions. |

**Condition:** the trading logic and the collector are genuinely good. What was missing was everything
around them. The biggest gap — nothing checked the software after a change — is now half closed.

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **← we are here, about half done** → `2 break up big files`
→ `3 stop database growing` → `4 data service` → `5 decide on rebuilding the screen` → `6 answer trading
questions with real results` → `7 machine learning` → `8 run reliably unattended`

Order is fixed: **you can't safely rearrange code you can't check automatically.** Stages 0→1→2 are a chain.

## This session (26 July)

1. **Cleanup work merged into the main line** and saved online. It had been parked on a side branch.
2. **Built the checking system from nothing to 140 automatic checks.** Covers the calculation engine
   completely, the profit-and-loss maths, and the opportunity scanner.
3. **Proved the checks actually work.** Broke the code ten different ways on purpose, confirmed the checks
   caught each one, then put everything back. A check that never fails gives false confidence.
4. **Found seven faults; fixed the two that mattered.** One counted a break-even trade as a loss, making
   average losses look smaller. The other crashed the statistics panel if one trade lacked an entry cost.
5. **Solved the "two collectors" mystery — there was only ever one.** The thing that looks like a second
   copy in Task Manager is a small launcher that starts the real one. Seeing two is normal and healthy.
6. **Stopped the backlog file growing forever.** Fixed items are deleted; the history is kept elsewhere.

**Three honest notes:**
**(a) I destroyed about two weeks of data, and it is not recoverable.** Switching branches silently
overwrote three small working files. Restored from a 10 July copy, so the scanner's record of which
opportunities appeared when, and any entry locks set since, are gone. **The 1.42 GB price database is
intact and was never at risk.**
**(b) The "two collectors" was thought to be the cause of the July problem. It wasn't** — that problem is
still unexplained, with no leads.
**(c) The checks have a known hole:** one part of the scanner isn't protected — found by a deliberate
break the checks failed to notice.

## What to do next

1. **Open the dashboard and look at the Journal statistics panel.** The fix changes what it shows. The
   automatic checks pass, but **nobody has looked at the screen**. Do this before trusting a number on it.
2. **Add checks for `db.py`** — the part every other piece reads through, and it currently has none.
   Use a temporary throwaway database for this, never the real one.
3. **Make the checks run by themselves.** Right now they only run when someone remembers to ask.
   That's the exact weakness this stage exists to remove.
4. Then the collector's own checks, and closing the scanner hole above.

## Open problems, priority order

| Problem | Meaning | Stage |
|---|---|---|
| **Unexplained July issue — BLOCKED ON CHANDAN** | "Still having some issue." The two-collector theory is dead. **Needs: what looked wrong, which tab, screenshot.** | — |
| Most of the code still unchecked | Database, collector and broker-connection parts have no checks | 1 |
| Checks don't run themselves | Only run when remembered | 1 |
| 4,230-line dashboard file | Appearance, calculations and data all mixed together | 2 |
| Overnight pauses labelled as failures | All routine pauses recorded as breakdowns; blocks alerting | 3 |

## Settled — don't reopen

- **Screen stays as-is for now.** Real case to rebuild it, but not before checks exist. Revisit at stage 5.
- **Old detailed price data gets trimmed eventually**, summary history kept forever.
- **The 6 practice trades will be discarded**, diary restarts clean. **Consequence:** the app must save
  market conditions *with each trade* before serious trading resumes.
- **Fixed items are deleted from the backlog**, not marked done. Lessons worth keeping go in `decisions.md`.
- **When testing money-handling code, record the fault first and fix it separately** — otherwise you can't
  tell whether the check proved the code right or the code was bent to fit the check.
- **Single user, one machine**, viewable from phone later.

## How to work here

Plan before coding · **copy the small working files aside before switching branches** (this caused this
session's data loss) · rehearse destructive changes on a copy · challenge assumptions with evidence rather
than agreeing · say plainly when something is unverified or wrong · **ask before** deleting, changing the
database, saving work, or changing system settings · finish with `/wrap`.

**Deeper detail:** `docs/plan.md` (tasks) · `docs/backlog.md` (open bugs) · `docs/decisions.md` (why —
see ADR-017…021) · `docs/progress_log.md` · `docs/DOCUMENTATION.md` (strategy/maths) · `DEV_JOURNAL.md`.