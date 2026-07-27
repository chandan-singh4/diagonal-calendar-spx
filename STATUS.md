# PROJECT STATUS

**Updated:** 2026-07-26 · **Branch:** `main` · 6 saved points added today, all saved online.
**State:** **Stage 1 is finished.** Checking grew 329 → **450 checks**, all passing, run
automatically whenever work is saved. One fault found and fixed. The price record is untouched
and healthy; the collector is running.

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
| **Database** | One file, ~1.4 GB of prices since 23 June. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs, charts built from that history. Reads only, never writes. |
| **Journal** | Diary of actual trades, to later check results against predictions. |

**Condition:** trading logic and collector are good, and now genuinely checked. The screen's code
is still one huge file — that is the next job.

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **← here next** →
`3 stop database growing` → `4 data service` → `5 decide on rebuilding the screen` → `6 answer
trading questions with real results` → `7 machine learning` → `8 run reliably unattended`

Order is fixed: **you can't safely rearrange code you can't check automatically.** Stage 1 existed
to earn the right to start stage 2. That's now done.

## This session (26 July, second sitting)

1. **The part that fetches prices is checked at last** — every price ever stored came through it,
   and it had no checks while the alarm system guarding it had 38. It is now driven end to end
   against a throwaway database.
2. **The broker connection is checked** — the piece most exposed to a change nobody here controls.
   The broker can rename a field without warning; prices would quietly stop arriving while
   everything reported healthy. The expected shape is now written down, so that fails loudly.
3. **A fault found and fixed (BUG-017): a snapshot could overstate how much it held.** It recorded
   the number of prices *offered* to the database, not the number actually stored. This was
   **already written down and left** months of work ago — it survived because nothing failed when
   it was wrong. It matters because the record can't be re-fetched: a snapshot claiming 3,096
   prices while holding 2,000 reads as intact, and nothing later could tell it from a real one.
4. **A hole in the safety net closed.** The opportunity screen works out a price when the broker
   supplies none. That calculation could be changed to anything and every check still passed. The
   lesson generalises and is now recorded: **a "did anything change?" check only protects what
   appears in its result.** Those rows existed and the code ran on them — the answers were just
   thrown away before reaching the screen.
5. **Two "pending" tasks were already done.** Auto-starting the collector was recorded as awaiting
   approval; it has worked since June via a startup shortcut, verified running. The version marker
   was recorded as pending; it already existed. Both corrected.

## What to do next

1. **Start stage 2 — breaking up the 3,891-line screen file.** Nothing blocks it. Before moving
   any piece, check whether its effect actually reaches the captured screen output; if not, pin it
   first, or the move is unprotected however green things look (ADR-025).
2. **Discard the 6 practice trades** — safe, and waiting only for you to be at the keyboard. Note
   the standing commitment below.
3. **One question for Chandan:** the statistics code works out a "break-even trades" figure and
   never shows it — display intended and forgotten, or just leftover?
4. Deliberately **not** doing yet, and both agreed: tidying the 85 code-style findings, and adding
   logging to the screen. Both touch the big file that stage 2 is about to take apart.

## Open problems, priority order

| Problem | Meaning | Stage |
|---|---|---|
| **Unexplained July issue — BLOCKED ON CHANDAN** | "Still having some issue." No leads. **Needs: what looked wrong, which tab, screenshot.** | — |
| **Dashboard reachable from other devices on the network** | Confirmed. Chandan judged the risk acceptable for now — sole user, own laptop. Revisit before any remote access. | — |
| Errors silently ignored in the profit screen | ~30 places; a failure shows as a blank | 2 |
| 3,891-line dashboard file | Appearance, calculations and data all mixed together | 2 |
| Backups manual; database growing without limit | Backups work but nobody runs them on a schedule; ~82 MB per trading day | 3 |

## Settled — don't reopen

- **Screen stays as-is for now.** Revisit at stage 5.
- **Trade numbers are never reused** — a deleted trade leaves a permanent gap. The diary is
  evidence, so a number must never change meaning.
- **When a price is missing, show nothing rather than zero.**
- **The 6 practice trades will be discarded**, diary restarts clean. **Consequence:** the app must
  save market conditions *with each trade* before serious trading resumes.
- **Record a fault first, fix it second**, and **prove a check works by deliberately breaking the
  code.** 18 faults were injected into the collector and broker code this session; all 18 caught.
- **Windows Task Scheduler is not used** for the collector — it needs administrator rights. The
  startup shortcut is the mechanism and it works.

## How to work here

Plan before coding · rehearse risky changes on a copy · **prove checks work by deliberately
breaking the code** · challenge assumptions with evidence · say plainly when something is
unverified · **ask before** deleting, changing the database, saving work, or changing system
settings · `/structured-task` covers ordinary work · finish with `/wrap`.
**Deeper detail:** `docs/` — `plan.md` (tasks) · `backlog.md` (open problems) · `decisions.md`
(why; ADR-025 newest) · `progress_log.md` · `DOCUMENTATION.md` (strategy/maths).
