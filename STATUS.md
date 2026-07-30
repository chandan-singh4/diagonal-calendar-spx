# PROJECT STATUS

**Updated:** 2026-07-29 · **Branch:** `main` · 5 saved points, **none sent online yet.**
**State:** **Stage 2's safety net is finished; the rebuilding itself has not started.** Checking grew
450 → **549 checks**, all passing. Price record healthy, collector running (126 recordings today,
latest 15:59 New York). The screen itself was not opened or tested today.
> Self-contained: read this file alone to start a session. Replaced entirely by `/wrap`.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX) — contracts to buy or sell at a set price
before a set date. His strategy: sell options expiring soon, buy similar ones expiring later, pay the
small difference. The soon-expiring ones lose value faster, and that gap is the profit; once it's
worth enough he restructures into a safer shape that locks the gain and caps the loss. **It's all
about timing.** Brokers show today's prices then discard them, and judging the moment needs weeks of
history on the exact contracts he'd trade. **So the historical record IS the product** — the screen
is just a window onto it.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts automatically with Windows. |
| **Database** | One file, ~1.6 GB since 23 June, 9.0 million rows. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs, charts built from that history. Reads only, never writes. |
| **Journal** | Diary of actual trades, to check results against predictions later. |

**Condition:** trading logic and collector are good and genuinely checked. The screen's code is
still one 4,283-line file — breaking it up is next, and now safe to attempt.

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **← here** →
`3 stop database growing` → `4 data service` → `5 decide on rebuilding the screen` → `6 answer
trading questions with real results` → `7 machine learning` → `8 run reliably unattended`
Order is fixed: **you can't safely rearrange code you can't check automatically.**

## This session

**The health report was lying two ways, now fixed and visibly working:** it crashed instead of
printing when output went to a file, and its "recordings today" figure fell to zero every evening
because it counted days in London time, not New York time.

**Built the safety net that makes stage 2 survivable — 88 new checks.** Stage 1 froze what the code
*calculates*; nothing froze what the *screen shows*. A wrong number can be checked against the
database; a panel whose rows come back in a different order just looks normal. Now covered: card
order, chart shape and colour, number formatting, and the opportunity-finding pipeline end to end.
**The most valuable one:** the code sorts candidates *before* trimming to 20 — reversed, the
asymmetric setups Chandan actually trades are discarded before sorting and vanish, and only a comment
was enforcing it. **The dashboard would have quietly stopped showing the trades he takes.**

**Mistakes in my own work.** Of 43 deliberately-broken code versions, 40 were caught — but six checks
first passed against code I had broken on purpose, each because the *example data* was too simple to
show the fault (a $6 gap cannot test a $5 threshold; one card cannot show an order). All fixed; three
survivors proved harmless. **A belief of mine was also wrong:** stage 2's main danger is not a lost
sorting step — it is duplicated, and the copy that matters was already protected weeks ago.

**Two real defects found by writing the checks**, neither breaking anything today: the opportunity-
history file uses a *relative* name, so launching from the wrong folder makes it read as empty; and
one function accepts a setting it ignores. **Two symptoms were logged then removed at Chandan's
request** (slowness, charts looking wrong) — noticed once, no specifics, and a record of guesses
invites fixing what nobody measured.

## What to do next

**Start stage 2 — break up the 4,283-line screen file, in this order:**

1. **`core/`** — pure calculations, no database and no screen: scanner maths, ranking, formatting.
2. **`data/`** — the eleven database-reading functions, returning data not screen-shaped rows.
3. **`state/`** — the small saved files (opportunity history, entry locks, chart colours), currently
   read and written from inside drawing code. **Fix the relative-filename defect here.**
4. **`views/`** — one file per tab; mostly mechanical once 1–3 are out.
5. **`app.py`** — page setup and tab switching only. Target: under 400 lines.

Steps 1–3 carry the value; step 4 looks like the work and is the least interesting part. Move small
pieces, run the checks after each, and expect them to catch real mistakes — that is their job.
**Never re-record a failing check to make it green.**

## Open problems, priority order

| Problem | Meaning | Stage |
|---|---|---|
| **Unexplained July issue — BLOCKED** | "Still having some issue." No leads. **Needs: what looked wrong, which tab, screenshot.** | — |
| **Discard the 6 practice trades — BLOCKED** | Safe now; the blocking bug is fixed and checked. Needs Chandan at the keyboard, and it writes to the database — confirm a backup first. | — |
| **"Break-even trades" — BLOCKED** | Calculated, never shown. Intended or leftover? One line decides fix-or-delete. | — |
| **Dashboard reachable from other devices** | Confirmed. Risk accepted — sole user, own laptop. Revisit before any remote access. | — |
| Errors silently ignored in the profit screen | ~30 places; a failure shows as a blank cell | 2 |
| 85 tidiness warnings, left deliberately | Most vanish during stage 2. **Don't switch the warning gate on until zero** — one that always fails teaches you to bypass it. | 2 |
| Backups manual; database growing without limit | Backups work, nobody runs them on a schedule; ~82 MB/trading day | 3 |

## Settled — don't reopen

- **The old audit is a frozen snapshot; `docs/plan.md` is the live plan** and wins where they differ.
- **No online build service** — the check-on-save hook replaces it; the testing guide was retired.
- **Prove checks by breaking the code on a copy**, never the live file (the dashboard reloads on save).
- **A break that changes nothing is evidence about the code** — investigate; never bend a check red.
- **Some behaviour is frozen while arguably wrong**, on purpose, reasoning beside it — Chandan's call.
- **No check may touch the real database. Trade numbers are never reused.** Missing price → blank, not 0.
- **The 6 practice trades will be discarded. Consequence:** the app must save market conditions *with
  each trade* before serious trading resumes.
- **No Windows Task Scheduler** for the collector (needs admin); the startup shortcut works.
- **Screen stays as-is** until stage 5.

## How to work here

`.claude/skills/structured-task/SKILL.md` for code work · `explain-simply` for explanations · finish
with `/wrap`. **Ask first** before: saving online, any database write, deleting files or rows,
changing Windows settings or installed programs, stopping/starting the collector, or sending anything
off this machine. Say plainly when something is unverified.

**Deeper detail:** `docs/plan.md` (stages, tasks) · `backlog.md` (open problems only) ·
`decisions.md` (why; ADR-029/030/031 are this session) · `progress_log.md` · `DOCUMENTATION.md`.
