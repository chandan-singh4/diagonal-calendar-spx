---
description: Start a session — read STATUS.md, verify it against reality, recap, propose a plan, request permissions
---

# /STARTUP — Begin a working session

## Step 1 — Read STATUS.md, and stop there

Read **`STATUS.md`** (repo root). That is the whole briefing. It is written to be
self-contained, so **do not open `docs/` files, source files, or go exploring the codebase.**
Doing so burns tokens re-deriving what that file already tells you — avoiding exactly that
is why it exists.

Then run these two cheap checks, because `STATUS.md` is a snapshot and may be stale:

```
git log --oneline -5 && git status --short && git branch -vv
python scripts/check_db.py
```

That confirms the branch/commit state and whether the collector is healthy and the data
current. **If reality disagrees with `STATUS.md`, say so explicitly and trust reality.**

Only read further files if the user's request genuinely needs depth `STATUS.md` doesn't
carry — and say which file you're opening and why.

## Step 2 — Brief the user

Keep it tight. This is a recap, not a re-read.

**1. Background** — 3–4 sentences in plain language: what this project is and what it's for.
Enough to re-orient someone returning after a break.

**2. Where things stand** — current stage, what the last session finished, branch state
(committed? merged? pushed?), collector health and data freshness.

**3. What's open** — priority order, plainly described. Flag anything that is:
- **blocked on the user** — and exactly what you need from them
- **time-sensitive** — a window closing, data degrading
- **a correction** to something previously believed true

**4. What I propose for this session** — a specific ordered plan for *today*, not the whole
roadmap. What you'd do first, what it depends on, how far you expect to get. If the obvious
next step is blocked, say what you'd do instead.

**5. Permissions I need** — only what this session's plan actually requires. For each: what
it is, why it's needed, how reversible it is. Anything that:
- deletes or overwrites files, database rows, or indexes
- modifies the database (confirm a current backup exists first)
- commits, tags, merges, or pushes
- changes system settings — scheduled tasks, startup items, installed packages
- stops or starts the collector
- sends anything off this machine

Then **stop and wait for approval.** Never brief and begin work in the same turn.

## Rules for the session that follows

- **Plan before coding** — understand, inspect, explain reasoning, then implement.
- **Rehearse destructive changes on a copy** where possible.
- **Challenge assumptions with evidence** — technical excellence, not agreement.
- **Report honestly** — say when something is unverified, broken, or when you got it wrong.
- **Make ordinary judgement calls yourself**; ask only when genuinely ambiguous, and state
  what you assumed.
- **Keep `docs/plan.md`, `docs/progress_log.md`, `docs/decisions.md`, `docs/backlog.md`
  current as you go**, not at the end.
- **Finish with `/wrap`.**
