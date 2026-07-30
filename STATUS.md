# PROJECT STATUS

**Updated:** 2026-07-30 · **Branch:** `m2-core-extraction`, **pushed to GitHub, not yet merged.**
**State:** Stage 2 is 4 steps of 5 done. **639 checks**, all passing. Price record healthy (126
recordings today, latest 15:59 New York). **The screen was used today,** and a freeze that made it
unusable was found and fixed — see "This session".
> Self-contained: read this file alone to start a session. Replaced entirely by `/wrap`.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX) — contracts to buy or sell at a set price
before a set date. His strategy: sell options expiring soon, buy similar ones expiring later, pay the
small difference. The soon-expiring ones lose value faster, and that gap is the profit; once it's
worth enough he restructures into a safer shape that locks the gain and caps the loss. **It's all
about timing**, and brokers discard today's prices rather than keep them. **So the historical record
IS the product** — the screen is just a window onto it.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Starts with Windows. |
| **Database** | One file, ~1.8 GB since 23 June. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs: Scanner, Entry Analysis, Calendar Edge, Strike Detail, Historical Stats, Research. Reads only. |
| **Journal** | Diary of actual trades. 6 practice entries, to be discarded. |

**Honest condition:** the record and the checking are in good shape. The screen has real faults, all
known and listed below. The code is backed up to GitHub; nothing *runs* anywhere but this machine.

## The 9-stage plan

`0 clean up` **done** → `1 automatic checking` **done** → `2 break up big files` **← here, 4 of 5** →
`3 stop database growing` → `4 data service` → `5 decide on rebuilding the screen` → `6 answer
trading questions with real results` → `7 machine learning` → `8 run reliably unattended`
Order is fixed: **you can't safely rearrange code you can't check automatically.** Stages 6 and 7
also need ~20 and ~100 real trades; there are 6 practice ones.

## This session

**The dashboard froze completely, at 100% of a processor core, whenever new prices arrived.** Every
tab stopped responding and the charts ignored the expiry and strike controls. **I misdiagnosed this
twice** — first as the work in progress, then as a stale leftover process — and stated the second
with more confidence than the evidence supported. Both were wrong. The real cause: the code that
refreshes the page on new prices restarted it *before* recording which prices it was showing, so it
restarted forever, never getting far enough to draw anything. Fixed, and confirmed working by
Chandan. What cracked it was the one symptom neither theory explained: **a slow page is still a
responding page.** No automated check could have caught this — the testing tool cannot trigger
background timers at all.

**Expired locks now delete themselves.** Chandan saves "locks" to track diagonals he's monitoring.
Three were still listed, the newest expired ten days earlier, because the cleanup he'd designed was
never built. A lock now disappears once its front expiry is past, or at 4:15 PM New York on expiry
day. **Deleted rather than hidden, at his request** — which makes the rule dangerous if wrong, since
firing a day early destroys a lock on a live position. So it is tested to the minute from both sides
and proved by deliberately breaking it seven ways: **7 of 7 caught.** One of my own new checks was
blind, and only that exercise revealed it. **Also finished:** all six tabs moved into their own
modules, each proved character-for-character identical to what it replaced, then the leftover debt
from those moves cleared. 4,283 → 2,505 lines.

## What to do next

1. **Step 2.5 — the last of stage 2.** Get the big file under 400 lines. Of the 2,505 left, **757
   are styling** and **115 are one screen section** needing its inputs passed in rather than read
   from around it. Neither is hard; both are fiddly.
2. **Get Chandan's decision on BUG-019 and BUG-022** — both wait on him, not on work.

## Open problems

- **BUG-022 (high) — blocked on Chandan.** Clicking "View Chart" on a saved lock can silently show a
  **different** diagonal than the one clicked, when the strike or back expiry isn't in today's data.
  Expiry was one cause and is fixed; two remain that hit *live* positions. He asked whether the
  expiry fix made this moot — it doesn't — and hasn't yet said whether to do it.
- **BUG-019 (medium) — blocked on Chandan.** Four summary figures vanished from the top of the
  Scanner a month ago, by accident, in a large edit. The code that calculates them still runs. He
  must choose: put the missing line back, or delete the 40 lines behind it. **Don't default to
  deleting** — that's the choice that can't be undone by looking at the screen.
- **ENH-011 (high)** — tab clicks are slow. Cause **not yet established**; measure first, and don't
  start by tuning the cache timers.
- **BUG-018 (medium)** — on expiry day one tile says "set strikes" when the strikes are already set.
- **BUG-001 (high) — blocked on Chandan.** An old unexplained report; needs a symptom and a
  screenshot; the duplicate-collector theory is dead.
- **DEBT-029** — two screen-library features are past their removal dates, in 34 places.

## Settled decisions

- **The screen stays as it is until stage 5**, and whether to move off the current screen technology
  is **not pre-committed** — that is decided with evidence at stage 5, not before.
- **Closing a problem means deleting its row**, never ticking it off — if the fix leaves a lesson,
  write it up in `docs/decisions.md` first. **Never re-record a failing check to make it pass**, and
  treat a deliberate break that changes nothing as evidence about the code, not a check to bend.
- **The 6 practice trades are still blocked** on Chandan at the keyboard with a confirmed backup,
  and the app must save market conditions *with each trade* before real trading resumes.

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings or installed programs, stopping/starting the collector, or sending anything off this
machine. **No check may touch the real database. Trade numbers are never reused. Missing price →
blank, not 0. Prove checks by breaking the code on a copy**, never the live file — the dashboard
reloads the moment a file is saved.

**Deeper detail:** `docs/` — `plan.md` (stages) · `backlog.md` (open problems only) · `decisions.md` (why) · `progress_log.md` (per session).
