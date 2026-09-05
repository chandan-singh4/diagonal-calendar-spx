# PROJECT STATUS

**Updated:** 2026-09-03 · **Branch:** `m3-data-hardening` — **stage 3 COMPLETE bar 3.5.**
**State:** 942 checks pass, all pushed (`b7168c9`). **The close was captured for the first time
ever today** — 16:00 and 16:01, and the old 15:59 "close" was wrong by 2.39 points. BUG-030 fixed,
record repaired, collector restarted 16:35 onto the fixed code.
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
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts with Windows (a Startup-folder shortcut, not a scheduled task). |
| **Database** | One file, **3.55 GB** since 23 June, growing ~82 MB a trading day. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs: Scanner, Entry Analysis, Calendar Edge, Strike Detail, Historical Stats, Research. Reads only. |
| **Journal** | Diary of actual trades. 6 practice entries, to be discarded. |

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **done** →
`3 stop database growing` **← done bar 3.5** → `4 data service` → `5 decide on rebuilding
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

**3.3, 3.6, 3.7, 3.8 and 3.9 all landed — stage 3 is done bar 3.5.** **3.3 (ADR-051)** —
schema changes are now a numbered, forward-only list with a runner. It replaces
`try: ALTER ... except Exception: pass`, **ten times over**, whose "column already exists" comment
was a *guess*: it could not tell that from a full disk, a locked database or a misspelled type, and
called all of them success. It also left **no record** — ten changes applied to the live file and
`schema_version` still saying 1. **3.7** — `scripts/audit.py` asks what no test can:
not "does the code work" but **"is the record complete?"**, read-only by construction. **3.6 (ADR-050)** — a discarded
row is now told apart as *harmless duplicate* or *prices gone for good*, with SQLite's own reason
logged; one message for both is what let **2,181 identical warnings** go unread for eight weeks.
**3.8** — `reauth.py` existed and **nothing in `docs/` mentioned it**. **3.9** — `OPERATIONS.md`,
`TROUBLESHOOTING.md` (by symptom, not cause) and `DATABASE.md`.

**BUG-030 fixed, record repaired** — Schwab's **-999.0** "no value" marker was stored verbatim:
5,127 rows at -9.99 volatility **and 5,081 in each of the four greeks**. **Exact equality,
deliberately** — -9.99 is an ordinary theta and 38 rows hold it. **BUG-029 fixed** — printing can no
longer stop the watchdog alerting; its alarm path is proven on a real 19 Aug outage.

## What to do next

1. **At 09:30 tomorrow, run `scripts/audit.py`** — the first live morning on the fixed parser, and
   the only real proof BUG-030 is closed.
2. **The live database is still at schema v1.** Migrating it adds nothing and stamps v2/v3; it
   happens on the next collector start or dashboard open. Proved a no-op by test.
3. **Then stage 4** (the data service) — or **3.5**, which is Chandan's call: he considers the
   alerting need met by the watchdog, and what 3.5 adds is the gap *history* on screen.

## Open problems

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
deploying.** The written record has been wrong where the data was right three times —
**when they disagree, read the database.** And a check of one place finding nothing proves nothing.
**Deeper detail:** `docs/` — `OPERATIONS.md` · `TROUBLESHOOTING.md` · `DATABASE.md` · `plan.md` · `backlog.md` · `decisions.md` · `progress_log.md`.
