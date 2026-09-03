# PROJECT STATUS

**Updated:** 2026-09-03 · **Branch:** `m3-data-hardening` — **stage 3, 5 of 9 parts done.**
**State:** 925 checks pass. Collector restarted 12:12 ET onto the new 16:02 window, so **today's
close should be the first ever recorded — verify it.** This session's work is **not yet pushed.**
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
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts with Windows. |
| **Database** | One file, **3.55 GB** since 23 June, growing ~82 MB a trading day. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs: Scanner, Entry Analysis, Calendar Edge, Strike Detail, Historical Stats, Research. Reads only. |
| **Journal** | Diary of actual trades. 6 practice entries, to be discarded. |

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **done** →
`3 stop database growing` **← here, 5 of 9 parts done** → `4 data service` → `5 decide on rebuilding
the screen` → `6 answer trading questions with real results` → `7 machine learning` → `8 run unattended`
Order is fixed: **you can't safely rearrange code you can't check automatically.** Stages 6 and 7
also need ~20 and ~100 real trades; there are 6 practice ones.

## This session

**The closing price was never being recorded — not once since 23 June.** Chandan spotted it. The
window ran to 16:00 with the end excluded, so the last poll of every day landed at **15:59:5x**
and every "close" in the record is a quote up to a minute earlier. It now runs to **16:02**
(ADR-049) — two minutes, not the one asked for, because SPX is struck from its components' closing
auction prints, which arrive in the seconds *after* the bell. **Not 16:15** either, where the
options stop: SPX is frozen by then. Costs ~1.3 MB a day.

**Stage 3.7 is done, and it found a bug on its first run.** `scripts/audit.py` asks a question no
test can: not "does the code work" but **"is the record actually complete?"** Every test passed
throughout all three silent-data-loss bugs — they check what the code is *believed* to do. It reads
the record **read-only by construction** (SQLite refuses a write), files the two known ten-week
holes as *history* not faults, and calls a short day the collector owned up to a note, not an
alarm; an audit that cries wolf gets skimmed.

**BUG-030 is fixed and the record is repaired.** Schwab sends **-999.0** when it has no value, and it was
stored verbatim. Wider than the audit could see: 5,127 rows carry a volatility of -9.99 **and 5,081
carry each of the four greeks at -999.0**. **Exact equality, deliberately** — -9.99 is an ordinary
theta and 38 rows really hold it, so a tolerance band would delete real data tidying up a sentinel.

**BUG-029 is fixed** — printing can no longer stop the watchdog alerting. **Its alarm path is
proven; a real outage already did it** (19 Aug, 12:30 ET; alert 8 min later, then recovery).

**Stage 3.8 is done — the weekly broker-permission runbook.** `scripts/reauth.py` already existed
(and **puts the old permission back if you abort**) but **nothing in `docs/` mentioned it** — the
exact failure 3.8 names. `docs/RUNBOOK_REAUTH.md` covers it, `get_client()` trap included.

## What to do next

1. **Restart the collector** (needs Chandan's word) — started 12:12, *before* the BUG-030 fix, so
   **the marker returns at 09:30 tomorrow** until it does. Then delete BUG-030's row.
2. **Verify today's close landed** — after 16:02 ET, snapshots at ~16:00 and ~16:01.
3. **Push** (needs Chandan's word).
4. **Then 3.5** (show the collection gaps) **or 3.3** (a way to change the database's shape).

## Open problems

- **BUG-030 (high)** — parser fixed, rows repaired; **only the collector restart remains**.
  **ENH-011 (high)** — tab clicks slow; cause **not established**, measure first. **BUG-001 (high,
  blocked on Chandan)** — old unexplained report; needs a symptom and screenshot.
  **BUG-018 (medium)** — on expiry day one tile says "set strikes" when they already are.
  **DEBT-029** — two screen-library features are past their removal dates, used in ~36 places.

## Settled decisions

- **The morning third-Friday contract is over at the opening print, 9:30 New York** (ADR-048,
  closing BUG-027) — not 4:15 like everything else. Chandan chose the open over the contract's
  true last trade the evening before, because **this rule is the only one that DELETES a record**
  and the open is the later, safer of the two accurate answers. Verified against the live locks
  file: nothing is deleted today; it first matters on **18 September**.
- **The two third-Friday contracts are different options and the record says which** (ADR-046,
  ADR-047). A blank means "not recorded", never "morning".
- **Collection runs 09:30–16:02** (ADR-049) so the settled close is captured; a trading day is
  392 collectable minutes, not 390. **Old prices cleared 90 days past expiry, summaries kept
  forever, traded expiries never cleared, never on a timer** (ADR-044). **The watchdog watches,
  never acts** (ADR-045). **Screen unchanged until stage 5.**
- **Closing a problem means deleting its row.** **Never re-record a failing check to make it pass.**

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or programs, starting/stopping the collector, or sending anything off this machine.
**No check may touch the real database. Trade numbers are never reused. Missing price → blank, not
0. Prove checks by breaking the code**, never the live file. **Verify on the real system after
deploying.** The written record has now been wrong where the data was right three times —
**when the two disagree, read the database.**
**Deeper detail:** `docs/` — `plan.md` · `backlog.md` · `decisions.md` · `progress_log.md` · `RUNBOOK_REAUTH.md`.
