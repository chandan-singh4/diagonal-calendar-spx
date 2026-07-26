# PROJECT STATUS

**Last updated:** 2026-07-26 (end of the M0 session)
**Current state:** Foundation cleanup finished. Ready to start writing automated tests.
**Branch:** `m0-stabilize-and-clean` — 6 commits, tagged `v4.2`, not yet merged.
**Dashboard:** Opened and checked after the changes. All six tabs render. No issues.

> **Written for someone brand new**, with no background in software or trading assumed.
> The detailed technical versions live in `docs/`. Regenerated at the end of each session
> by the `/wrap` command.

---

## Part 1 — The background story

### What is this thing?

Chandan trades **options** on the S&P 500 stock index. An option is a contract that gives
you the right to buy or sell something at a set price before a set date. Options have
expiry dates, and their prices move constantly.

He uses one particular strategy. In plain terms:

1. **Open the position.** He sells options that expire soon, and buys similar options that
   expire a bit later. Selling brings money in; buying costs money. He pays the small
   difference to set it up.
2. **Wait.** Options that expire soon lose value faster than ones expiring later. That gap,
   working in his favour, is where the profit comes from.
3. **Convert it.** Once the position is worth enough, he restructures it into a safer shape
   that locks in the gain and caps how much he could still lose.

The whole game is **timing** — knowing when step 1 is worth doing, and when step 3 has
become worthwhile.

### Why build software for it?

Because brokers show you today's prices and then throw them away.

To judge whether today is a good moment, you need to know what "normal" looks like — and
that means having **weeks of history** on the exact contracts he'd actually trade. No
broker keeps that for you.

So that's the real insight here: **the historical record is the product.** The screen is
just a window onto it.

### The moving parts

| Piece | What it does | Think of it as |
|---|---|---|
| **The collector** | Runs quietly in the background all day. Every 1–5 minutes while markets are open it asks the broker "what do all these option prices look like right now?" and writes the answer down. | A diligent clerk taking inventory every few minutes |
| **The database** | One large file holding every reading ever taken — currently **7.8 million** price records, going back to 23 June. | A filing cabinet that only ever gets fuller |
| **The dashboard** | A web page with six tabs, showing charts and numbers built from that history. | The window onto the filing cabinet |
| **The journal** | Where he records trades he actually made, so results can later be checked against what the tool predicted. | A trading diary |

The collector and the dashboard are kept deliberately **separate**. The collector only
*writes*; the dashboard only *reads*. Either can crash without harming the other. That
separation was designed well from the very start and is one of the project's real
strengths.

### What condition was it in?

Genuinely good where it counts, and neglected everywhere else.

**The strong parts.** The trading calculations are careful. The collector is robust — it
survives the broker's connection dropping, recovers by itself, and records when it missed
anything. And most importantly: when an earlier review found the project's *central trading
assumption was probably wrong*, that assumption was publicly retracted and marked
"unproven" rather than quietly patched. That kind of honesty is rare, and it's the most
valuable thing in the project.

**The weak parts.** Almost all the ordinary engineering discipline was missing:

- **No automated checks at all.** Nothing verified the software still worked after a
  change — every check meant a human clicking through the screen. One bug in July cost
  *days* of hunting that an automated check would have caught in seconds.
- **No backup.** A month of price history that genuinely cannot be re-obtained (the broker
  will not sell you last Tuesday's prices) sat on one hard drive, in one file.
- **One enormous file.** The dashboard lives in a single file of 4,230 lines, mixing
  appearance, calculations, and data handling together.
- **Housekeeping problems.** A corrupted settings file meant private and temporary files
  were being saved into the project's version history. The setup instructions described a
  version of the app that hadn't existed for months.

A fair analogy: **a well-equipped professional kitchen with no fire extinguisher, no
inventory system, and recipes taped to the wall that no longer match what's being cooked.**
The cooking is good. Everything *around* the cooking was missing.

### The plan

Nine stages. We have just finished the first.

```
  M0  Clean up the foundations   ← DONE
  M1  Write automated checks     ← NEXT
  M2  Break up the huge files
  M3  Stop the database growing forever
  M4  Build a proper data service
  M5  Decide whether to rebuild the screen
  M6  Answer the trading questions using real results
  M7  Machine learning (only with enough trade history)
  M8  Make it run reliably without babysitting
```

The order is not negotiable: **you cannot safely rearrange code you can't check
automatically, and you shouldn't start changing anything you can't undo.** M0 → M1 → M2 is
a chain.

---

## Part 2 — What we completed

### First, an inspection

Before touching anything, the entire project was read end to end — every code file, both
long history documents, and the database measured directly rather than estimated. That
produced a written report (`docs/AUDIT_2026-07-25.md`) covering what exists, what's wrong,
how serious each problem is, and the nine-stage plan. It was reviewed and approved before
any change was made.

### Then the cleanup

| # | What we did | Why it mattered |
|---|---|---|
| 1 | **Made a proper backup** and proved it could actually be restored | A month of irreplaceable data existed in exactly one place. This was the single biggest risk in the project. |
| 2 | **Fixed a corrupted settings file** | A Windows text-encoding accident had silently broken the rule that keeps private and temporary files out of version history. They'd been saved for weeks. |
| 3 | **Made the database 21% smaller** — 1.81 GB → 1.42 GB | Two internal "shortcut lists" were exact duplicates of others. Removing them freed 387 MB *and* made every save faster. Rehearsed on a copy first to prove nothing would break. |
| 4 | **Deleted unused code** | Around a dozen functions nobody called, plus an entire orphaned file. |
| 5 | **Locked down software versions** | The project claimed to use certain versions of its building blocks; it was actually running versions two major releases newer, with no warning of the drift. Now pinned exactly. |
| 6 | **Rewrote the setup instructions and added proper documentation** | The old README described the app as an early prototype and told users to copy a file that didn't exist. |
| 7 | **Added an automatic safety guard** | Refuses to save passwords, databases, or logs into version history. Tested in both directions. |
| 8 | **Organised the folders** | Everything had been dumped into one directory. Now grouped into `docs/`, `scripts/`, `migrations/`. |
| 9 | **Started a written memory** | Five living documents — plan, progress log, decision log, backlog, handoff — so knowledge survives between sessions instead of living in one person's head. |

### Three things worth knowing

**We found a mistake in our own report.** The audit claimed the collector had to be started
by hand each day. That was wrong — it has started automatically since June via a Windows
startup shortcut, and the project's own journal explained why the alternative had been
rejected. The finding was corrected rather than quietly dropped.

**We introduced a bug and caught it.** While deleting unused code, a needed line was
removed alongside it. The software still appeared fine and would only have failed later,
during trade logging. A purpose-built check found it. This is exactly the kind of thing the
automated checks in M1 exist to catch, and it took deliberate effort to catch by hand.

**We found a real bug nobody knew about.** The system labels *every* overnight and weekend
pause as "the collector broke." All 46 recorded pauses say this; not one has ever been
correctly labelled "market was closed." Harmless today, but it must be fixed before any
alerting is built on top — an alarm that fires every single night is one people learn to
ignore.

### Does it still work?

Yes. After all changes, the dashboard was opened and checked: all six tabs render
correctly, no issues reported.

---

## Part 3 — What's still open

### Do next

| What | Why it matters |
|---|---|
| **Merge the finished work into the main line** | It's parked on a side branch. The dashboard has been verified, so it's ready to go in. |
| **M1 — Write the automated checks** | **The most important thing left.** Nothing currently verifies that a change hasn't broken something. Every later stage depends on this. Start with the calculation engine (small and self-contained), then the profit-and-loss maths — that one handles real money, so it matters most. |

### Known problems, in priority order

| Problem | What it means |
|---|---|
| **No automated checks** | Nothing verifies the software after a change. Being fixed in M1. |
| **The 4,230-line file** | The dashboard is one giant file mixing appearance, calculations, and data handling. Slow to navigate, risky to change. M2. |
| **The database grows forever** | About 82 MB every trading day — roughly 20 GB a year — with no cleanup. Fine now, a problem within a year. M3. |
| **Overnight pauses mislabelled as failures** | The bug described above. Must be fixed before alerting is built. M3. |
| **The manual has drifted from reality** | The main reference document describes two features the software doesn't actually use, and completely omits one it does. M2. |
| **Nothing restarts a crashed collector** | It starts reliably each morning, but if it dies mid-day nothing notices. Costs part of one day at worst. M3. |
| **An unexplained issue from July** | In an earlier session Chandan reported "still having some issue" but the session ended before details were captured. **Needs from him: what exactly looked wrong, on which tab, and ideally a screenshot.** Cannot be investigated without that. |

### Decisions already settled — don't reopen

- **The screen stays as it is for now.** There's a genuine case for rebuilding it in
  different technology, but not before the checks and cleanup exist — with no automated
  checks there'd be no way to prove a rebuilt screen shows the same numbers. Revisit at M5.
- **Old detailed price data will eventually be trimmed**, keeping the summary history
  forever.
- **The six existing practice trades will be discarded** and the diary restarted clean.
  **One important consequence:** before serious trading resumes, the app must start saving a
  snapshot of market conditions *alongside each trade*. Otherwise trimming old data later
  will erase the very information needed to judge whether the strategy actually works.
- **Single user, one machine** — but viewable from a phone later on.

---

## Part 4 — Where to look for more

| Question | File |
|---|---|
| Full technical assessment and the nine-stage plan | `docs/AUDIT_2026-07-25.md` |
| What we're working on right now | `docs/plan.md` |
| Every bug, debt item, and idea | `docs/backlog.md` |
| Why each decision was made | `docs/decisions.md` |
| What happened in each session | `docs/progress_log.md` |
| Last session's sign-off | `docs/handoff.md` |
| The trading strategy and the maths | `docs/DOCUMENTATION.md` |
| Long-form development history | `docs/DEV_JOURNAL.md` |

---

*Regenerated by `/wrap` at the end of each session. Read by `/STARTUP` at the beginning of
the next one.*
