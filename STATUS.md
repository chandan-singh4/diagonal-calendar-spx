# PROJECT STATUS

**Updated:** 2026-09-05 · **Branch:** `main` — **stage 5 done; stage 6 started, running beside it.**
**State:** 1,221 checks pass, all pushed (`74f6606`). Nine commits today. **Six faults fixed, four
of them mine, two found by Chandan looking at his own screen.** The big one: the Gamma Exposure tab
took 4.5 seconds to open and **none of it was the framework** — it was one query, and it is now
0.02s.
> Self-contained: read this file alone to start a session. Replaced entirely by `/wrap`.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX) — contracts to buy or sell at a set price
before a set date. His strategy: sell options expiring soon, buy similar ones expiring later, pay
the small difference. The soon-expiring ones lose value faster, and that gap is the profit; once
it's worth enough he restructures into a safer shape that locks the gain and caps the loss. **It's
all about timing**, and brokers discard today's prices rather than keep them. **So the historical
record IS the product** — the screen is just a window onto it.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts with Windows (a Startup-folder shortcut, not a scheduled task). **Running.** |
| **Database** | One file, **3.5 GB**, 6,387 snapshots and 19.3M option rows since 23 June. Newest: 2026-09-04 16:01 New York. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs: Scanner, Entry Analysis, Calendar Edge, Strike Detail, Gamma Exposure, Research. Reads only. **Running on port 8501.** |
| **Data service** | A second, separate program that serves the same record over the web to anything that asks. 17 addresses. Not started by default. |
| **New screen** | A fresh front end being built beside the old one, under `web/`. Nothing of it is in use yet. |
| **Journal** | Diary of actual trades. 6 practice entries, to be discarded. |

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **done** →
`3 stop database growing` **done bar 3.5** → `4 data service` **done** → `5 decide on rebuilding
the screen` **done — decided: rebuild** → `6 answer trading questions with real results`
← **started, as the rebuild** → `7 machine learning` → `8 run unattended`
Order is fixed: **you can't safely rearrange code you can't check automatically.** Stages 6 and 7
also need ~20 and ~100 real trades; there are 6 practice ones.

## This session

**Chandan asked whether to move off Streamlit. The honest answer was measured, and it was no —
so the tab was fixed instead, and only then was the rebuild started for different reasons.**
Streamlit's own cost is **0.04 seconds a click**. The 4.5 seconds that made the Gamma Exposure tab
feel broken was **one query** (BUG-039): asked the obvious way, the database picked one contract
across all 52 sessions ever collected out of a 19.3-million-row table, then threw away all but
today. Selecting the day first took it **2.20s → 0.02s** with byte-identical results, and the tab
from 4.53s → 1.07s. **That fault was mine, introduced the same morning by ENH-014**, and it would
have got worse every day the record grew. Every correctness check passed throughout and none could
see it, so it is now guarded at the source.

**Rebuilding was chosen anyway, for what Streamlit cannot do at all** — not for speed, and it is
written down that way in `docs/m6_migration_plan.md` so nobody later thinks it was sold on
performance. One tab at a time, both screens running, and a real redesign. The rule that governs it:
**no formula may exist in the new language.** Anything on screen is computed once, in Python, under
test.

**Four faults in the Gamma Exposure work, two reported by Chandan from his own screen.**
**BUG-038** — the replay's "gamma at each level" panel was filtered to the biggest *movers*, so a
strike holding 275 million that simply didn't trade vanished from a panel claiming to show every
level; he spotted the hole by comparing two charts. **BUG-036** — "gamma added today" counted only
the 28 bars drawn, understating by 29–35%, and understated *more* the busier the day. **BUG-037** —
switching tabs showed the old tab's contents for two seconds, because the screen library patches by
position. **BUG-035** — "10D" drew eight sessions and "20D" drew fifteen; the windows counted
calendar days, and 5D was right only by coincidence of the weekday.

**BUG-040, and it was my mistake that found it.** `GET /mission/new` recorded to the database *by
default*, so merely reading it wrote. I called it to look at its response shape and it wrote 53 rows
into a table that had been empty — **my call created the first-ever recording**, which is now the
baseline every later "what's new" comparison measures from. Nothing is corrupted and the dashboard
would have recorded that snapshot anyway, but state changed that should not have. The address is now
split: reading never writes, and a separate one records. It had **no check at that layer at all**
beforehand.

**Cleanup.** Two old database copies from 19 August deleted with Chandan's word — **5.4 GB freed**.
The 3 September copy is kept. Nothing else moved: the source folders are already properly separated
and enforced by a check, and the seven loose files in the main folder are live settings and logs the
running programs hold open.

## What to do next

1. **The new Scanner tab cannot be finished yet** — one of its three card grids (the non-ATM
   opportunities panel) is still page-only. **DEBT-041.** Everything else it needs is served.
2. **Then build it** — `web/` has the toolchain installed and proven, and nothing else.
3. **The data service is not running.** Start it with the command in `OPERATIONS.md` before working
   on the new screen; the new screen has no other way to reach the record.
4. **`scripts/audit.py` has still not been run on a live morning** since BUG-030 — carried over from
   2026-09-03 and still the only real proof that fault is closed.

## Open problems

  **BUG-001 (high, blocked on Chandan)** — old unexplained report; needs a symptom and screenshot.
  **BUG-023 (high)** — the screen shows only the morning third-Friday option; the afternoon one is
  never displayed. **DEBT-029 (high)** — two screen-library features are past their removal dates,
  used in ~36 places; the rebuild may overtake this. **DEBT-041** — above. **DEBT-031** — the
  5-point threshold is written out four times and two copies disagree at exactly 5.00.
  **ENH-016** — the replay covers two charts; the rest of the tab could wind back with them.
  **DEBT-036** — `pinned_pairs.json` is a dead orphan; deleting it needs Chandan's word.

## Settled decisions

- **The rebuild is not for speed** (`docs/m6_migration_plan.md`). It is for what a re-run-everything
  screen cannot do. Streamlit stays live until the last tab is moved, so a stalled migration leaves
  a working dashboard.
- **No formula may exist in TypeScript.** The service serves computed answers, never raw rows for
  the browser to add up — two definitions of a number will diverge.
- **Reading never writes** (BUG-040). A web address that only looks something up must be safe to
  retry, because the new screen's data library retries and refetches on its own.
- **The morning third-Friday contract is over at the opening print, 9:30 New York** (ADR-048).
  **The two third-Friday contracts are different options and the record says which** (ADR-046/047);
  a blank means "not recorded", never "morning".
- **Collection runs 09:30–16:02** (ADR-049). **Old prices cleared 90 days past expiry, summaries
  kept forever, traded expiries never cleared, never on a timer** (ADR-044). **The watchdog watches,
  never acts** (ADR-045).
- **History windows count sessions on record, not calendar days** (BUG-035), and sessions are cut in
  New York time, not UTC.
- **Closing a problem means deleting its row.** **Never re-record a failing check to make it pass.**

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or programs, starting/stopping the collector, or sending anything off this machine.
**No check may touch the real database. Trade numbers are never reused. Missing price → blank, not
0. Prove checks by breaking the code**, never the live file. **Verify on the real system after
deploying** — this session, three things worked under test and only the real server showed the
token path had never once run. The written record has been wrong where the data was right three
times — **when they disagree, read the database.** And a check of one place finding nothing proves
nothing.
**Deeper detail:** `docs/` — `OPERATIONS.md` · `TROUBLESHOOTING.md` · `DATABASE.md` · `plan.md` · `backlog.md` · `decisions.md` · `progress_log.md` · `m6_migration_plan.md`.
