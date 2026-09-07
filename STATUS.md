# PROJECT STATUS

**Updated:** 2026-09-07 · **Branch:** `m6-gamma-live-clock`, merged to `main` and saved online.
**State:** **1,491 checks pass. Everything is committed.** Stage 5 done; stage 6 running. The new
screen was checked in a real browser against the live record after the changes below.
> Self-contained: read this file alone to start a session. Replaced entirely by `/wrap`.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX) — contracts to buy or sell at a set price
before a set date. He sells options expiring soon, buys similar ones expiring later, and pays the
small difference; the soon ones lose value faster and that gap is the profit. Once it is worth
enough he restructures into a safer shape that locks the gain and caps the loss. **It is all about
timing**, and brokers throw away today's prices — **so the historical record IS the product**.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts with Windows. **Running.** |
| **Database** | One file, 6,387 snapshots and 19.3M option rows since 23 June. Newest: 2026-09-04 16:01 New York — 7 Sep is Labor Day, so no new data is expected today. Irreplaceable: the broker won't sell you last Tuesday's prices. |
| **Old dashboard** | Web page, 6 tabs. Reads only. **Running on port 8501.** Still the live one. |
| **Data service** | A separate program serving the same record to anything that asks. **Running on port 8899.** No auto-restart: restart it by hand after any change to it. |
| **New screen** | The replacement being built beside the old one, under `web/`. 4 of 6 tabs. Not in use yet. |
| **Journal** | Diary of actual trades. 6 practice entries, to be discarded. |

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **done** →
`3 stop database growing` **done bar 3.5** → `4 data service` **done** → `5 decide on rebuilding
the screen` **done — decided: rebuild** → `6 answer trading questions with real results`
← **in progress, as the rebuild** → `7 machine learning` → `8 run unattended`
Order is fixed: **you can't safely rearrange code you can't check automatically.** Stages 6 and 7
also need ~20 and ~100 real trades; there are 6 practice ones.

## This session

Four things Chandan asked for from his own screen. **None was a bug report; three were faults.**
Full detail and the measurements: `docs/progress_log.md` entry 16. **Two charts showed London
time**, because the charting library reads the clock part of a timestamp and **ignores the timezone
attached to it** — a check confirming only the timezone passes on a chart labelled with the wrong
hour. Checks pin the hour now.

**The "wick" behind each bar — that strike's high and low so far today — was on two charts out of
six**, and is now on all six. Each range is computed by calling **that measure's own function**,
never re-derived: the plus/minus conventions differ per measure, and a copy would drift silently.

**The countdown to each expiry showed Friday's numbers on Sunday** ("8 Sep — 4 DTE" on the 6th).
Each row carries the countdown as it was *when recorded*; the screen read it as "days from now". It
now counts from the reader's own date, **but only on the newest day's data** — Chandan's choice,
since re-basing a replayed older day empties every filter window (ADR-053). A settled expiry reads
negative, not 0: 0 is the bucket a trader acts on. Separately, filter buttons that narrowed the
list and left the selection alone now select.

**Forty-five deliberate breaks, forty-four caught**, and **three of my own checks were rewritten
because a break survived them** — one passed when fed the wrong measure entirely. **A check that
cannot fail is worse than none.**

## What to do next

1. **Research, then Entry Analysis** are the two tabs not started. **Entry Analysis has a known
   hole**: six figures it needs are computed inside the old screen's startup code, served nowhere.
2. **The lower half of the Gamma Exposure tab is still unbuilt** — the time panels, the 0DTE flow
   board, dealer structure, net flow and the replay.
3. **Two questions wait on Chandan.** Whether the Calendar Edge gap chart matches the old one side
   by side; and **whether the new screen should lock entries** — that means letting the data
   service *write*, and **must not be added without his word**.
4. **`scripts/audit.py` has still not been run on a live morning** since BUG-030 — carried since
   2026-09-03 and still the only real proof that fault is closed.

## Open problems

**BUG-001 (high, blocked on Chandan)** — old unexplained report; needs a symptom and screenshot.
**BUG-023 (high)** — only the morning third-Friday option is shown; the afternoon one is recorded
but never displayed. **DEBT-029 (high)** — two features of the old screen's library are past their
removal dates, used in ~36 places. **DEBT-042** — the new Scanner's "seen 4x" counts only advance
while the old dashboard is open. **DEBT-031** — the 5-point threshold is written four times, two
copies disagreeing at exactly 5.00. **DEBT-036** — `pinned_pairs.json` is dead; deleting needs a word.

## Settled decisions

- **The rebuild is not for speed** — the old screen's own cost is 0.04 seconds a click. It stays
  live until the last tab moves, so a stall leaves a working screen (`docs/m6_migration_plan.md`).
- **No formula may exist in the new language.** Rounding rules, key formats, thresholds and date
  comparisons live in Python, under test; a missing label is a field on the answer.
- **Reading never writes** (ADR-052) — a read must be safe to repeat; the new screen retries.
  **Countdowns follow the clock only on the newest day's data** (ADR-053).
- **The two third-Friday contracts are different options the record distinguishes** (ADR-046/047),
  and the morning one is over at the opening print at 9:30 New York (ADR-048).
- **Collection runs 09:30–16:02** (ADR-049); **old prices cleared 90 days past expiry, summaries
  kept forever** (ADR-044); **history windows count sessions on record** (BUG-035), in New York time.
- **Closing a problem means deleting its row. Never re-record a failing check to make it pass.**

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or programs, starting/stopping the collector, or sending anything off this machine.
**No check may touch the real database. Trade numbers are never reused. Missing price → blank, not
0. Prove checks by breaking the code**, never the live file. **Verify on the real system after
deploying** — the data service does not reload itself. **When the record and the data disagree,
read the database**, and a check of one place finding nothing proves nothing.
**Deeper detail:** `docs/` — `OPERATIONS.md` · `TROUBLESHOOTING.md` · `DATABASE.md` · `plan.md` · `backlog.md` · `decisions.md` · `progress_log.md` · `m6_migration_plan.md` · `web/README.md`.
