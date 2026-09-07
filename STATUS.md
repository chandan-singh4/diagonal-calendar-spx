# PROJECT STATUS

**Updated:** 2026-09-07 · **Branch:** `m6-gamma-live-clock`, merged to `main` and saved online.
**State:** **1,530 checks pass.** The data service was restarted 2026-09-07 and serves the
SPX fix and `/locks`; the two screens have not been looked at since. Stage 5 done; stage 6 running. The new
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
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts with Windows. **Running** — restarted 2026-09-07 10:05 onto the BUG-041 fix. |
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
2. **The volume shade on Gamma Exposure is now a checkbox** ("Volume shade", tab toolbar,
   on by default, both layouts). It is NOT remembered across a reload — say so if it should be,
   since that would be the first browser storage on this screen.
3. **The lower half of the Gamma Exposure tab is still unbuilt** — the time panels, the 0DTE flow
   board, dealer structure, net flow and the replay.
4. **Three fixes are live on the data service and UNSEEN on the screens — look at Calendar
   Edge on both.** (a) The **SPX panel now
   runs to the close** even when the marks stop early — the index was being dropped whenever an
   option leg went unquoted. (b) The **three time charts are drawn in one shared frame**, so
   16:01 sits at the same pixel on all three; this is the fault reported four times, and the
   cause was the MARGINS, not the range (ADR-055). (c) Entry locks, below.
5. **Entry locks work and Chandan has one open** — Put 7700 / Call 7725, 11 Sep → 14 Sep,
   entry $9.30. The new screen now has **All Locks** (list, view, remove) beside the lock
   button, and the chart switches to **position management** when a lock is held: dashed entry
   line, the live diagonal dimmed and renamed hypothetical, and the tooltip measuring against
   entry. A parity check holds both screens to the same wordings. **Correcting a locked price
   is deliberately not built** — the old screen has it, nothing asked for it here.
   Older note, still true: The data service now
   has `/locks` (list, create, clear) and the new screen has the Lock Entry control above the
   Diagonal chart. **The data service must be restarted before any of it exists** — it does not
   reload itself. Then, on the new screen: lock a combo, confirm the badge shows the price and
   time, reload the page, and clear it. **Locking is a real write the collector reads** — it pins
   those strikes for future fetches — so do this on a combo worth pinning, or clear it after.
6. **Restart the data service, then look at Calendar Edge** — `api/reads.py` changed and it does
   not reload itself. Check on both Today and 5D: every session runs to 16:15, the 16:00-16:02
   closing readings are now drawn, and a session that lost its afternoon shows blank space rather
   than a short day. **The old screen changed too** (`views/edge.py`), so give 8501 a rerun.
7. **`views/entry.py` still holds `_THRESHOLD = 5.0`** — its own docstring admits it. Calendar Edge
   is clean and guarded now; Entry Analysis is the remaining copy (DEBT-031).

## Open problems

**BUG-041 remnant (high, needs Chandan's word)** — the collector no longer eats zeros, but
**191,855 stored bids and 977,220 gammas are null where the broker sent 0.0**, and nothing can now
tell those apart from a genuine absence. Repair means writing inferred values into the
irreplaceable file. **Do not, until a live afternoon logs the raw payload and proves the broker
sends 0.** Until then the Gamma tab under-counts pre-2026-09-07 0DTE sessions by ~42% of strikes.
**BUG-001 (high, blocked on Chandan)** — old unexplained report; needs a symptom and screenshot.
**BUG-023 (high)** — only the morning third-Friday option is shown; the afternoon one is recorded
but never displayed. **DEBT-029 (high)** — two features of the old screen's library are past their
removal dates, used in ~36 places. **DEBT-042** — the new Scanner's "seen 4x" counts only advance
while the old dashboard is open. **DEBT-031** — the 5-point threshold is written four times, two
copies disagreeing at exactly 5.00. **DEBT-036** — `pinned_pairs.json` is dead; deleting needs a word.

## Settled decisions

- **The Schwab token dies 7 days after an interactive login, and nothing can automate it.**
  Reauthenticated 2026-09-07 10:26, so **it expires Monday 14 September**. The watchdog now says so
  while the market is shut (BUG-043) — a closed market is the only time reauth is free.
- **The rebuild is not for speed** — the old screen's own cost is 0.04 seconds a click. It stays
  live until the last tab moves, so a stall leaves a working screen (`docs/m6_migration_plan.md`).
- **No formula may exist in the new language.** Rounding rules, key formats, thresholds and date
  comparisons live in Python, under test; a missing label is a field on the answer.
- **Reading never writes** (ADR-052), **narrowed by ADR-054**: the data service may write entry
  locks, a sidecar file, and nothing else. The database stays read-only to the API. A read must
  still be safe to repeat; the new screen retries — which is why **deleting a lock that is not
  there is a success, not a 404**, and why **locking a combo already locked is refused (409)
  rather than overwriting the time the position was taken**.
  **Countdowns follow the clock only on the newest day's data** (ADR-053).
- **The two third-Friday contracts are different options the record distinguishes** (ADR-046/047),
  and the morning one is over at the opening print at 9:30 New York (ADR-048).
- **Collection runs 09:30–16:02** (ADR-049); **old prices cleared 90 days past expiry, summaries
  kept forever** (ADR-044); **history windows count sessions on record** (BUG-035), in New York time.
- **Zero is a measurement, not an absence** (BUG-041) — everywhere except implied volatility,
  where a reported 0.0 means the broker could not price it.
- **Charts that share a clock share a FRAME, not just a range** (ADR-055,
  `web/src/edge/timeAxis.ts`). Plotly maps a range onto the container minus the margins, so
  three charts agreeing on the range and disagreeing on `margin.r` by 38px still do not line
  up. Reported four times and fixed three times against the innocent cause. The shared value is
  the widest requirement, or the dual-axis chart's labels are clipped. A check reads all three
  files. **When a fix does not take, re-measure the symptom instead of refining the fix.**
- **The index is not a column on an option row.** SPX is read by its own query
  (`db.get_underlying_history`), because the marks query drops any snapshot missing one of the
  six legs and the index was riding along — so a 0DTE afternoon ended the SPX panel an hour
  early. The marks still stop when the legs stop, and that is correct.
- **Charts are drawn on whole sessions, not on the extent of their data**
  (`core.series.session_axis_range`, every window including multi-day). An axis that stops where
  the data stops hides the hole; anchoring to a series also moves the axis depending on which
  query returned more rows.
- **The evening rangebreak starts at 16:15, not 16:00** (BUG-042) — collection runs to 16:02
  (ADR-049) and the old bound collapsed the closing print onto nothing. Two checks pin it.
- **Closing a problem means deleting its row. Never re-record a failing check to make it pass.**
  **A closure with no entry in `progress_log.md` will be resurrected by the next `/wrap`** — that
  is how the audit.py item came back four times after being closed and proven.

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or programs, starting/stopping the collector, or sending anything off this machine.
**No check may touch the real database. Trade numbers are never reused. Missing price → blank, not
0. Prove checks by breaking the code**, never the live file. **Verify on the real system after
deploying** — the data service does not reload itself. **When the record and the data disagree,
read the database**, and a check of one place finding nothing proves nothing.
**Deeper detail:** `docs/` — `OPERATIONS.md` · `TROUBLESHOOTING.md` · `DATABASE.md` · `plan.md` · `backlog.md` · `decisions.md` · `progress_log.md` · `m6_migration_plan.md` · `web/README.md`.
