# PROJECT STATUS

**Updated:** 2026-08-19 · **Branch:** `m3-data-hardening` — **stage 3, 4 of 9 parts done.**
**State:** 876 checks pass. Everything is **saved to GitHub** — nothing waiting on this machine.
The collector is running today's earlier fix; it has NOT been restarted onto tonight's, which is
why the afternoon contract's volatility line is only three readings long so far.
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
| **Database** | One file, **3.10 GB** since 23 June, growing ~82 MB a trading day. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs: Scanner, Entry Analysis, Calendar Edge, Strike Detail, Historical Stats, Research. Reads only. |
| **Journal** | Diary of actual trades. 6 practice entries, to be discarded. |

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **done** →
`3 stop database growing` **← here, 4 of 9 parts done** → `4 data service` → `5 decide on rebuilding
the screen` → `6 answer trading questions with real results` → `7 machine learning` → `8 run unattended`
Order is fixed: **you can't safely rearrange code you can't check automatically.** Stages 6 and 7
also need ~20 and ~100 real trades; there are 6 practice ones.

## This session

**Half the third-Friday prices were never being recorded, and never had been, since day one.**
Chandan spotted it on screen. On the third Friday of each month SPX lists **two** different options
for the same date and strike: the traditional monthly, settling at the **opening** price and
stopping trading the evening before, and the weekly, trading all day and settling at the **close**.
The broker sends both. The program threw away the one field telling them apart, and the rule meant
to stop duplicates saw them as the same option and dropped one. Today the same strike stood at
**17.15 for the morning contract and 19.80 for the afternoon one** — 2.65 apart, because the
afternoon one has a full extra day of life. Both are now recorded.

**This also solved the standing "160 of 3,156 rows discarded" puzzle** — 2,181 warnings, every one
reading exactly 160, because 160 = 80 calls + 80 puts = one expiry date. Unexplained for eight weeks.
**And the real database was changed for the first time:** a new column on a 2.7 GB file of
irreplaceable history — backed up, rehearsed on a copy, then done in 31 seconds, zero rows lost.

**Two mistakes of my own reached the live system; only checking afterwards caught them.** The
first would have *deleted* already-collected prices on the next restart. The second: I assumed the
afternoon contract was the unusual one and told the screen to hide it. It is the reverse — nearly
every SPX expiry is afternoon-settled, the morning one exists only on that one monthly date — so
the instruction hid **94% of all prices**, for three cycles. **Every check passed throughout**,
because they were written against what I believed rather than what the data says; what found it was
reading the database back and counting rows. Both fixed, the new checks proved by breaking a copy.
**A third earlier conclusion was wrong too:** I recorded that the old unlabelled prices could never
be sorted into morning and afternoon. They can — matched on **open interest**, which does not move
intraday, **170 of 170** were the morning contract.

## What to do next

1. **Restart the collector when convenient** (needs Chandan's word). The running copy predates
   tonight's change, so it still writes one daily volatility row per date. Nothing is lost —
   the prices behind every chart are recorded in full either way — but the afternoon contract's
   volatility line stops growing until it restarts.
2. **One piece of the third-Friday work is deliberately left undone.** The morning contract really
   stops trading the evening before; both are still treated as ending together, on purpose — the
   accurate rule **deletes** saved positions a day sooner, and being early destroys the entry price
   a live position is measured against (**BUG-027**). It does not affect daily use.
3. **Then stage 3 part 8** — write down the weekly broker-permission renewal steps.
4. **The watchdog has one thing unproven** — no *real* outage has travelled the whole alarm path;
   it cannot be staged without Chandan's word, so the first real one is the test.

## Open problems

- **The four damaged snapshots are repaired** — 4801–4804, rebuilt tonight from the prices they
  were derived from, rehearsed first on a copy of the whole 3.10 GB file. 4805–4808 remain a real
  ~2-minute gap from restarting the collector, correctly recorded; nothing to repair there.
- **ENH-011 (high)** — tab clicks are slow; cause **not established**, measure first.
  **BUG-001 (high, blocked on Chandan)** — old unexplained report; needs a symptom and screenshot.
  **BUG-018 (medium)** — on expiry day one tile says "set strikes" when they already are.
  **DEBT-029** — two screen-library features are past their removal dates, used in ~36 places.

## Settled decisions

- **The two third-Friday contracts are different options and the record now says which** (ADR-046),
  including the daily volatility summary (ADR-047), which now has one row per contract and a rule
  stopping them ever sharing one again. A blank means "not recorded", never "morning".
- **Old prices cleared 90 days past expiry, summaries kept forever, traded expiries never cleared,
  never on a timer** (ADR-044). **The watchdog watches, never acts** (ADR-045). **The screen stays as it is until stage 5.**
- **Closing a problem means deleting its row.** **Never re-record a failing check to make it pass.**

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or programs, starting/stopping the collector, or sending anything off this machine.
**No check may touch the real database. Trade numbers are never reused. Missing price → blank, not
0. Prove checks by breaking the code on a copy**, never the live file. **And verify on the real
system after deploying** — today, every check passed while the live screen was wrong.
**Deeper detail:** `docs/` — `plan.md` (stages) · `backlog.md` (open problems) · `decisions.md` (why) · `progress_log.md` (per session).
