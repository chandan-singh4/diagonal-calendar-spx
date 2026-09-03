# PROJECT STATUS

**Updated:** 2026-09-03 · **Branch:** `m3-data-hardening` — **stage 3, 5 of 9 parts done.**
**State:** 879 checks pass. Work since 2026-08-19 is **on this machine only, not yet saved to
GitHub.** The collector is running normally and was deliberately **not** restarted — see below.
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

**A short session, and three of its four items closed by checking rather than building.**

**The collector did not need restarting.** The previous STATUS said it was running pre-fix code
and its afternoon volatility line had stopped growing. **Reading the database back disproved
that** — every day from 20 August has both morning and afternoon rows (yesterday: 126 and 2,520);
only 19 August and earlier are unlabelled. It had already been restarted. Restarting mid-session
would have cost a real ~2-minute hole in today's prices for nothing, so it was left alone.
**Same lesson as last session: the written record was wrong and the data was right.**

**Stage 3.8 is done — the weekly broker-permission runbook.** The *streamlining* half turned out
to be built already (`scripts/reauth.py`, which sets the old permission aside and **puts it back
if you abort**). But **nothing in `docs/` or `README.md` mentioned that it existed**, which is
precisely the failure 3.8 names. `docs/RUNBOOK_REAUTH.md` now covers the three ways you find out
it is due, the seven steps, what to do when it goes wrong, and the `get_client()` trap written
down as a thing never to do. **The 7 days is Schwab's and cannot be automated away.**

**The watchdog's alarm path is proven, and a real outage already proved it.** Chandan recalls
receiving both the pop-up and the email on a stop *and* the recovery, and the record bears it
out: four consecutive collector failures at 12:30 ET on 19 August, `last_alert_utc` 8 minutes
later — one watchdog cycle — then recovery. Separately, an outage was **staged in isolation** to
test detection: the old note said that needed the collector stopped, but `DB_PATH` and
`STATE_DIR` are both overridable, so a throwaway database with one three-hour-old price did it
without touching anything real. It correctly returned "no prices for 3h 0m", against a control
run on the live database returning ✅ in the same breath.

## What to do next

1. **Save this session's work to GitHub** (needs Chandan's word) — eight files touched, nothing
   pushed since 19 August.
2. **Then stage 3.5** (show the collection gaps the dashboard has never displayed) **or 3.3**
   (a proper way to change the database's shape). 3.7 and 3.9 also remain; 3.9 stays last.
3. **BUG-029, found today** — the watchdog kills itself on its own output if that output is sent
   to a file or a pipe, and it dies *before* alerting. **The live alarm is unaffected and always
   has been** — the scheduled task redirects nothing — but any future log capture would silence
   it. A watchdog that dies printing its own headline is the one failure it cannot have.

## Open problems

- **BUG-029 (medium, new today)** — the watchdog crashes on redirected output, before it alerts.
- **ENH-011 (high)** — tab clicks are slow; cause **not established**, measure first.
  **BUG-001 (high, blocked on Chandan)** — old unexplained report; needs a symptom and screenshot.
  **BUG-018 (medium)** — on expiry day one tile says "set strikes" when they already are.
  **DEBT-029** — two screen-library features are past their removal dates, used in ~36 places.

## Settled decisions

- **The morning third-Friday contract is over at the opening print, 9:30 New York** (ADR-048,
  closing BUG-027) — not 4:15 like everything else. Chandan chose the open over the contract's
  true last trade the evening before, because **this rule is the only one that DELETES a record**
  and the open is the later, safer of the two accurate answers. Verified against the live locks
  file: nothing is deleted today; it first matters on **18 September**.
- **The two third-Friday contracts are different options and the record says which** (ADR-046),
  including the daily volatility summary (ADR-047). A blank means "not recorded", never "morning".
- **Old prices cleared 90 days past expiry, summaries kept forever, traded expiries never cleared,
  never on a timer** (ADR-044). **The watchdog watches, never acts** (ADR-045). **The screen stays as it is until stage 5.**
- **Closing a problem means deleting its row.** **Never re-record a failing check to make it pass.**

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or programs, starting/stopping the collector, or sending anything off this machine.
**No check may touch the real database. Trade numbers are never reused. Missing price → blank, not
0. Prove checks by breaking the code on a copy**, never the live file. **And verify on the real
system after deploying.** Twice now the written record has been wrong where the data was right —
**when the two disagree, read the database.**
**Deeper detail:** `docs/` — `plan.md` (stages) · `backlog.md` (open problems) · `decisions.md` (why) · `progress_log.md` (per session) · `RUNBOOK_REAUTH.md` (the weekly chore).
