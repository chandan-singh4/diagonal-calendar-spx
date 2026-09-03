# PROJECT STATUS

**Updated:** 2026-09-03 · **Branch:** `m3-data-hardening` — **stage 3, 5 of 9 parts done.**
**State:** 889 checks pass, everything **saved to GitHub**. The collector was restarted 12:12 ET
onto the new 16:02 window and is collecting normally. **Today's close is the first ever recorded.**
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

**The restart this session opened with was not needed** — the database showed both contracts' rows every day since 20 August.

**The closing price was never being recorded — not once since 23 June.** Chandan spotted it.
The window ran to 16:00 with the end excluded, so the last poll of every day landed at
**15:59:5x** and every "close" in the record is a quote from up to a minute earlier. It now runs
to **16:02** (ADR-049) — two minutes, not the one asked for, because SPX is struck from its
components' closing auction prints, which arrive over the seconds *after* the bell; a 16:00 poll
would record a close that is not the close. **Still not 16:15**, where the options stop: SPX is
frozen by then and the IVs would be computed against a stale underlying. Costs ~1.3 MB a day.
**Second time in three sessions that data was never captured and nothing could tell** — the
record looked complete both times.

**Stage 3.8 is done — the weekly broker-permission runbook.** The *streamlining* half was built
already (`scripts/reauth.py`, which **puts the old permission back if you abort**), but **nothing
in `docs/` or `README.md` mentioned it existed** — precisely the failure 3.8 names.
`docs/RUNBOOK_REAUTH.md` covers how you learn it is due, the steps, the failure modes, and the
`get_client()` trap. **The 7 days is Schwab's and cannot be automated away.**

**The watchdog's alarm path is proven — a real outage already did it.** Four collector failures
at 12:30 ET on 19 August, `last_alert_utc` 8 minutes later, then recovery; Chandan remembers both
messages. Detection was also **staged in isolation** — `DB_PATH` and `STATE_DIR` are overridable,
so the outage the old note said needed the collector stopped took ten minutes and found BUG-029.

## What to do next

1. **Save this session's work to GitHub** (needs Chandan's word) — eight files touched, nothing
   pushed since 19 August.
2. **Then stage 3.5** (show the collection gaps never displayed) **or 3.3** (a proper way to
   change the database's shape). 3.7 and 3.9 remain too; 3.9 stays last.
3. **BUG-029, found today** — the watchdog kills itself on its own output if that output goes to
   a file or a pipe, and it dies *before* alerting. **The live alarm is unaffected** — the
   scheduled task redirects nothing — but any future log capture would silence it.

## Open problems

- **BUG-029 (medium, new)** — the watchdog crashes on redirected output, before it alerts.
  **ENH-011 (high)** — tab clicks are slow; cause **not established**, measure first.
  **BUG-001 (high, blocked on Chandan)** — old unexplained report; needs a symptom and screenshot.
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
