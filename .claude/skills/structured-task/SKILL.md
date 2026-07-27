---
name: structured-task
description: Execute any development task in this repo — writing or changing code, fixing a fault, adding checks, updating docs. Load at the start of the task, before opening files or making changes. Do not load for pure questions, or for /STARTUP and /wrap, which have their own workflows.
---

# Structured Task Execution

Every development task runs the same way: predictable, transparent, token-efficient.
Plan first, work in the open, finish with a short summary anyone can read.

## Step 1 — Plan before touching anything

1. **State the task back** in one or two sentences, as you understand it.
2. **Name the files** you expect to change, and why each one.
3. **Explain the approach** briefly — what you'll do, and one sentence on why that way
   rather than an obvious alternative.
4. **Build the checklist** with the to-do tool, so the steps are visible up front and the
   current step is visible at any moment during a long task.

A typical checklist:

- Understand the current behaviour
- Record the fault as a failing check *(bug fixes only — see Rule 1)*
- Make the change
- Update whichever `docs/` files the change actually affects
- Review the diff

**Then start work in the same turn.** Do not stop for approval on ordinary code work —
that's what this skill exists to avoid.

## Step 2 — Stop only for these

Clear requirements are not permission. Regardless of how unambiguous the task is,
**ask before**:

- deleting or overwriting files, database rows, or indexes
- any write to the database *(confirm a current backup exists first)*
- saving work — committing, tagging, merging, or pushing
- changing system settings — scheduled tasks, startup items, installed packages
- stopping or starting the collector
- sending anything off this machine

Also stop if requirements are genuinely ambiguous — meaning two readings would produce
materially different work. Otherwise make the judgement call yourself and say what you assumed.

## Step 3 — While working

Keep the checklist current: exactly one item in progress, finished items closed as you go.
Narrate in prose only when something changes the plan:

- **Discovered work** — add it to the checklist. If it's outside the task, add it to
  `docs/backlog.md` instead and keep going. Don't silently widen the task.
- **Blocked** — say so in one sentence, with exactly what you need. Then do every other
  part of the task that isn't blocked before coming back.
- **A previous belief was wrong** — say it plainly and immediately, including if it was
  your own earlier conclusion.

Don't re-explain what you've already said. No progress commentary for its own sake.

## Development rules

1. **Record the fault first, fix it second.** For any bug: write the failing check, watch it
   fail, then fix. Otherwise you can't tell whether the check proved the code right or the
   code was bent to fit the check.
2. **Prove new checks work by deliberately breaking the code**, on a copy, then reverting.
   A check that has never failed has never been tested.
3. **Smallest safe change.** Preserve the existing architecture unless the task is to change
   it. No refactoring that wasn't asked for.
4. **Rehearse risky changes on a copy** before touching the real thing.
5. **Challenge assumptions with evidence.** Technical excellence, not agreement. If the
   request rests on something untrue, say so — then deliver the work.
6. **Keep `docs/` current as you go, not at the end** — but only the files this task actually
   changes. Do not open a file to confirm it needs nothing; that costs tokens for no result.
   - `plan.md` — task states
   - `progress_log.md` — what happened
   - `decisions.md` — why, as a plain-English ADR
   - `backlog.md` — open problems only; closing an item means deleting its row (ADR-017)
7. **Report honestly.** If something is unverified, broken, or was got wrong, say so plainly.
   Never describe work as done over a failure.

## Step 4 — Task summary

Short, plain language, understandable by someone with no software or trading background.
Explain any term on first use.

**Done** — what changed and why it mattered, in a few lines. Not a list of files touched.

**Issues found** — bugs, risks, blockers. `None.` if none.

**New tasks** — work discovered but not done, and where it's recorded. `None.` if none.

**Files changed** — plain list.

**Verified** — what was actually checked and what that proved. Then, separately and honestly,
**what is still untested**. Those are two different claims; keep them apart.

**Verdict** — exactly one:

- ✅ **Complete** — done, nothing unresolved
- ⚠️ **Partly done** — works, but follow-up needed (say what)
- ❌ **Blocked** — can't continue (say what's needed, and from whom)

This summary is per *task*. It does not replace `/wrap`, which remains the final step of the
session and the only thing that rewrites `STATUS.md` and commits.
