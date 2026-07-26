# PROJECT STATUS

**Updated:** 2026-07-26 · **Branch:** `m0-stabilize-and-clean` (9 commits, tagged `v4.2`, **not merged to main**)
**State:** Foundation cleanup (M0) complete. Dashboard opened and verified — all 6 tabs render, no issues.

> Self-contained: read this file alone to start a session. No need to open other files unless doing deep work.
> Replaced entirely by `/wrap` each session. Keep under 100 lines.

## What this project is

Chandan trades **options** on the S&P 500 index (SPX). An option is a contract to buy or sell at a set
price before a set date. His strategy: sell options expiring soon, buy similar ones expiring later, pay the
small difference. Options expiring soon lose value faster — that gap is the profit. Once it's worth enough,
he restructures into a safer shape that locks the gain and caps the loss. **It's all about timing.**

Brokers show today's prices then discard them. To judge if today is a good moment you need weeks of history
on the exact contracts he'd trade. Nobody keeps that for you. **So the historical record IS the product** —
the screen is just a window onto it.

| Part | What it does |
|---|---|
| **Collector** | Background program. Every 1–5 min while markets are open, records all option prices. Auto-starts at Windows logon via a Startup-folder shortcut. |
| **Database** | One file, 7.8M price records since 23 June, 1.42 GB. Irreplaceable — the broker won't sell you last Tuesday's prices. |
| **Dashboard** | Web page, 6 tabs, charts built from that history. Reads only, never writes. |
| **Journal** | Diary of actual trades, to later check results against predictions. |

**Condition:** trading logic and the collector are genuinely good. Everything around them was missing —
no automated checks, no backup, one 4,230-line dashboard file, broken housekeeping. Like a well-equipped
kitchen with no fire extinguisher and out-of-date recipes on the wall.

## The 9-stage plan

`M0 clean up` **✅ done** → `M1 automated checks` **← next** → `M2 break up big files` → `M3 stop database
growing` → `M4 data service` → `M5 decide on rebuilding the screen` → `M6 answer trading questions with real
results` → `M7 machine learning` → `M8 run reliably unattended`

Order is fixed: **you can't safely rearrange code you can't check automatically.** M0→M1→M2 is a chain.

## This session (25–26 July)

Read the whole project, measured the database directly, wrote an audit + 9-stage plan, got approval, then:

1. **Backup made and restore proven** — a month of irreplaceable data had existed in one place only. Biggest risk closed.
2. **Fixed corrupted settings file** — a Windows encoding accident had been silently saving private/temp files into version history for weeks.
3. **Database 21% smaller** (1.81→1.42 GB) — two internal shortcut lists were exact duplicates. Rehearsed on a copy first to prove nothing broke. Saves are faster too.
4. **Deleted unused code** — ~a dozen functions plus an orphaned file.
5. **Locked software versions** — project claimed one set, was running versions two major releases newer, with no warning.
6. **Rewrote setup docs, organised folders** into `docs/`, `scripts/`, `migrations/`.
7. **Added safety guard** blocking passwords/databases/logs from version history.
8. **Started written memory** — plan, progress log, decision log, backlog, handoff.

**Three honest notes:** (a) our own audit was wrong that the collector needed manual starting — it's
auto-started since June; corrected. (b) We introduced a bug deleting code and caught it with a purpose-built
check — exactly what M1 automates. (c) Found a pre-existing bug: every overnight/weekend pause is labelled
"collector broke" — all 46 of them. Harmless now, must fix before building alerts.

## What to do next

1. **Merge to main.** Work is parked on a branch, dashboard verified. Optionally run one live trading day on
   the branch first — Monday would exercise the collector, log rotation, and smaller database for real.
2. **Start M1 — automated checks.** Set up the test framework, then cover the calculation engine
   (`iv_engine.py` — small, self-contained, no dependencies) first, then profit-and-loss maths in
   `pages/journal.py` (`resolved_pl`, `ic_expiry_pnl_per_share`, `derive_ic`, `compute_stats`) — that one
   handles real money. Then a safety net capturing current scanner output before M2 changes it. Target ~70%
   coverage, plus automated checks on every save.

## Open problems, priority order

| Problem | Meaning | Stage |
|---|---|---|
| No automated checks | Nothing verifies software after a change | M1 |
| 4,230-line dashboard file | Appearance + calculations + data all mixed together | M2 |
| Database grows forever | ~82 MB/trading day (~20 GB/yr), no cleanup | M3 |
| Overnight pauses mislabelled as failures | All 46 records wrong; blocks alerting | M3 |
| Manual drifted from reality | Describes 2 unused features, omits 1 used one | M2 |
| Nothing restarts a crashed collector | Starts fine daily; mid-day crash goes unnoticed | M3 |
| **Unexplained July issue — BLOCKED ON CHANDAN** | He reported "still having some issue", session ended before details. **Needs: what looked wrong, which tab, screenshot.** | — |

## Settled — don't reopen

- **Screen stays as-is for now.** Real case to rebuild it, but not before checks exist — otherwise no way to
  prove a rebuilt screen shows the same numbers. Revisit at M5.
- **Old detailed price data gets trimmed eventually**, summary history kept forever.
- **The 6 practice trades will be discarded**, diary restarts clean. **Consequence:** before serious trading
  resumes, the app must save market conditions *with each trade* — otherwise trimming old data later erases
  what's needed to judge if the strategy works.
- **Single user, one machine**, viewable from phone later.

## How to work here

Plan before coding · rehearse destructive changes on a copy · challenge assumptions with evidence, don't
just agree · report honestly when something is unverified or wrong · **ask before** deleting, changing the
database, committing/pushing, or changing system settings · finish with `/wrap`.

**Deeper detail if needed:** `docs/plan.md` (task states) · `docs/backlog.md` (all bugs/debt) ·
`docs/decisions.md` (why) · `docs/AUDIT_2026-07-25.md` (full assessment) · `docs/DOCUMENTATION.md` (strategy
and maths) · `docs/progress_log.md` · `docs/DEV_JOURNAL.md`.
