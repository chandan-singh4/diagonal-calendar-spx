# PROJECT STATUS

**Updated:** 2026-09-10 · **Branch:** `main`, merged and pushed (`f2c592a`). **1,728 automatic
checks pass**, and the pre-commit hook ran them on each commit. Two faults Chandan reported are
fixed and confirmed against live data.
> Self-contained: read this file alone to start a session. Replaced entirely by `/wrap`.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX) — contracts to buy or sell at a set price
before a set date. He sells options expiring soon, buys similar ones expiring later, and pays the
small difference; the soon ones lose value faster and that gap is the profit, until he restructures
into a shape that locks it in. **It is all about timing**, and brokers discard today's prices — **so
the historical record IS the product.**

| Part | What it does |
|---|---|
| **Collector** | Every 1–5 min while markets are open, records all option prices. Starts with Windows. **Running.** |
| **Database** | One file, ~6,500 snapshots, 19M+ rows since 23 June. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Old dashboard** | 6 tabs, reads only. **Port 8501.** Still the live one. |
| **Data service** | Serves the record to anything that asks. **Port 8899.** Does NOT restart itself — restart by hand after changing it. |
| **New screen** | The replacement being built beside the old one, under `web/`. 4 of 6 tabs. **Port 5173.** |
| **Briefing daemon** | New. Wakes at ~15 set times daily, has an AI explain the figures in plain words, sends it to Telegram, and writes it down so its predictions can be scored later. Started by hand — see next steps. The **Ask panel**, a chat button on the new screen, answers questions from the same figures. |

## The 9-stage plan

`0 clean up` · `1 checking` · `2 break up big files` **done** → `3 stop database growing` **done
bar 3.5** → `4 data service` **done** → `5 rebuild the screen?` **decided: yes** → `6 answer trading
questions with real results` ← **in progress, as the rebuild** → `7 machine learning` → `8 run
unattended`. Order is fixed: **you can't safely rearrange code you can't check automatically.**

## This session

**Both things Chandan reported were features we believed finished that were not reaching him at
all**, and neither was caught by us. That is the pattern worth noticing.

**The new screen was not refreshing itself** (ADR-057). The "new data has arrived" channel was built
four days ago on the server and **nothing in the browser was ever written to listen to it**; a
second fault sat behind it, so even a listener could not have connected. **The screen contradicted
itself and nobody read it that way** — the top strip refreshes on its own timer, so it said "two
minutes old" while every chart beside it still showed data from when the tab was opened.

**No briefings had been sent all day** — the daemon was simply not running. **Eleven missed, 09:45
to 15:15, and nothing anywhere said so.** Started; the 15:30 one arrived. Missed ones are
deliberately not sent late: a late briefing would be scored as if made on time.

**Built: the Ask panel** (ADR-058) — chat on the new screen, with a choice of AI model, a
thinking-effort setting and a microphone. **It cannot see the screen or read pictures**: it gets the
*same computed figures* the charts use. Briefings were rewritten in plain words too.

**Three mistakes of my own, all found by Chandan, all fixed.** Asked "what is the delta at 7,500"
it could not answer, having had only totals and peaks — **it cannot look up what it was not given.**
Three questions drew near-identical answers because **my own instruction said to "walk the whole
ladder" every time.** He also corrected me on a fact and was right. Separately, the data service's
auto-restart **hangs** (BUG-045), serving old code while fixes appeared to do nothing.

## What to do next

1. **Start the briefing daemon so it survives** — double-click `start_briefings.bat`, leave the
   window open. Nothing restarts it, and nothing reports its absence (DEBT-044).
2. **Two lint findings came in with `core/dealer.py`** (`zip()` without `strict=`), left alone
   because `strict=` changes behaviour when the inputs differ in length. Worth a deliberate look.
3. **Check both fixes tomorrow** — briefings from 09:45, and charts updating with nothing touched.
4. `docs/plan.md`'s milestone table is stale (says stage 4 "not started"); worth one pass. The lower
   half of Gamma Exposure is unbuilt, and **Research**/**Entry Analysis** are not started.

## Open problems

**BUG-045 (P1)** — auto-restart hangs, and killing it leaves orphans holding the port; **run
WITHOUT `--reload`.** **DEBT-044 (P1)** — nothing notices when the briefing daemon dies.
**BUG-041r (P1)** — old stored zeros are unrecoverable and repair means writing a guess into the
irreplaceable file; **do not, until a live afternoon proves what the broker sends.** **BUG-001
(P0, blocked on Chandan)** — old report, needs a symptom and screenshot. **BUG-023 (P1)** — only
the morning third-Friday option is shown. Others: `docs/backlog.md`.

## Settled decisions

- **The Schwab login dies 7 days after a manual sign-in and cannot be automated.** Last done
  2026-09-07, so it **expires Monday 14 September**; a closed market is the only free time to redo it.
- **A channel is not delivered until something listens on it** (ADR-057). **Where a feature spans
  two programs, the acceptance test crosses the boundary**, or it is not accepted.
- **The AI is handed computed figures and judges none of them** (ADR-058). Levels (strikes, prices)
  get printed; sizes become words like "heavy". Blank means "not measured", never zero (BUG-041).
- **Reading never writes**, except entry locks (ADR-052/054). **Charts sharing a clock share a
  frame** (ADR-055), and are drawn on whole sessions, not the extent of their data.
- **Closing a problem means deleting its row** (ADR-017) — never striking it through as DONE. A
  struck-through row reads as finished; that is exactly how the half-built refresh went unnoticed.

## How to work here

**Ask first** before: saving online, any database write, deleting files or rows, changing Windows
settings, starting/stopping the collector, or sending anything off this machine. **No check may
touch the real database. Missing price → blank, not 0. Prove checks by breaking the code**, never
the live file. **Verify on the real system after deploying**, and **when a fix seems not to take,
check you are running it** before refining it. **When the record and the data disagree, read the
database.** **Detail:** `docs/` — `OPERATIONS.md` · `TROUBLESHOOTING.md` · `DATABASE.md` ·
`plan.md` · `backlog.md` · `decisions.md` · `progress_log.md` · `web/README.md`.
