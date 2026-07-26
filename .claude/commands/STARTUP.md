---
description: Start a session — read STATUS.md, recap the project, propose a plan, request permissions
---

# /STARTUP — Begin a working session

Orient yourself before doing anything. **Do not write, edit, or run anything that changes
state during this command** — reading and reporting only.

## Step 1 — Read

1. **`STATUS.md`** (repo root) — the plain-language snapshot from the last session. This is
   the primary source.
2. **`docs/plan.md`** — the current milestone and exact task states.
3. **`docs/backlog.md`** — open bugs, technical debt, priorities.
4. **`docs/handoff.md`** — the last session's sign-off and recommended next step.

Then check reality against those documents, because they may be stale:

- `git log --oneline -10`, current branch, and `git status`
- Whether the collector is running and how fresh the data is
  (`python scripts/check_db.py`)

**If the documents and reality disagree, say so explicitly and trust reality.**

## Step 2 — Report back

Give the user a briefing in this shape. Keep it tight — this is a recap, not a re-read.

### 1. Background
Three or four sentences: what this project is and what it's for, in plain language.
Enough that someone returning after two weeks is re-oriented, no more.

### 2. Where things stand
- Current milestone and how far through it we are
- What the last session completed
- Branch state, and whether anything is uncommitted or unmerged
- Whether the collector is healthy and the data current

### 3. What's open
The open items in priority order, plainly described. Flag anything that is:
- **blocked on the user** (and exactly what's needed from them)
- **time-sensitive** (a window that closes, data that degrades)
- **a correction** to something previously believed true

### 4. What I propose to work on
A specific, ordered plan for *this* session — not the whole roadmap. Say what you'd do
first, what it depends on, and roughly how far you expect to get. If the obvious next step
is blocked, say what you'd do instead.

### 5. Permissions I need from you
List explicitly, and only what this session's plan actually requires. For each: what it is,
why it's needed, and how reversible it is. Cover anything that:

- **deletes or overwrites** files, database rows, or indexes
- **modifies the database** in any way (always confirm a current backup exists first)
- **commits, tags, merges, or pushes** to git
- **changes the system** — scheduled tasks, startup items, installed packages
- **stops or starts** the collector
- **sends anything outside this machine**

Then **stop and wait for approval.** Do not begin work in the same turn as the briefing.

## Rules for the session that follows

- **Plan before coding.** Understand, inspect, explain reasoning, then implement.
- **Ask rather than guess** when requirements are genuinely ambiguous — but make ordinary
  judgement calls yourself and say what you assumed.
- **Rehearse destructive changes** on a copy where that's possible (the database work in M0
  was proven on a backup clone before touching the real file).
- **Challenge the user's assumptions** where you have evidence. Technical excellence, not
  agreement. Say so plainly and give the reasoning.
- **Report honestly.** If something is unverified, broken, or you got it wrong earlier,
  state it directly.
- **Keep the written memory current** — `docs/plan.md`, `docs/progress_log.md`,
  `docs/decisions.md`, `docs/backlog.md` — as you go, not at the end.
- **Finish with `/wrap`** to regenerate `STATUS.md` and commit.
