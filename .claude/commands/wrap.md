---
description: End the session — rewrite STATUS.md (max 100 lines, self-contained), commit
---

# /wrap — End-of-session wrap-up

Rewrite `STATUS.md` so the next session can start from that file **alone**, then commit.

## The hard constraints

1. **Maximum 100 lines.** This is a budget, not a target. If it doesn't fit, cut detail —
   never raise the limit. Detail belongs in `docs/`.
2. **Completely replaced every time.** `STATUS.md` is a snapshot of *now*. Never append;
   never keep stale content because it was there before. `docs/progress_log.md` is the log.
3. **Self-contained.** The next session must be able to read this one file and begin work
   immediately — knowing what the project is, what state it's in, and what to do next —
   **without opening any other `.md` or `.py` file.** This is the whole point: it exists to
   keep token usage low. If a fact is needed to start work, it goes in. If it's only needed
   once work is underway, it goes in `docs/` and gets a pointer.
4. **Plain language.** Written for someone with no software or trading background. Explain
   every term on first use. No unexplained jargon — not "refactor", "schema", "index",
   "API", "commit", "regression". Say what things *mean*, not what they're called.

## Step 1 — Establish the real state

Do not write from recollection. Check:

- `git log --oneline` for this session, `git status`, current branch, whether merged/pushed
- `docs/plan.md` — which tasks changed state
- `docs/backlog.md` — items opened, closed, re-prioritised
- `docs/decisions.md` — decisions recorded
- Anything left broken, unverified, or blocked on the user

If those `docs/` files are out of date with what actually happened, **update them first**.
`STATUS.md` is a plain-language summary of them, so they must be right before it is written.

## Step 2 — Rewrite STATUS.md

Cover, in roughly this order and this weighting:

| Section | Budget | Contents |
|---|---|---|
| Header | ~4 lines | Date, branch, commit/merge/push state, whether the dashboard is verified working |
| What this project is | ~15 lines | The tool, the trading strategy in plain steps, why software was needed, the moving parts, honest condition |
| The plan | ~6 lines | The stages, which is done, which is next, why the order is fixed |
| This session | ~15 lines | What was done and **why it mattered** — not what was touched. Include mistakes found in our own earlier work, bugs introduced and caught, and bugs discovered. |
| What to do next | ~10 lines | Specific and actionable — enough that work can start without further reading |
| Open problems | ~10 lines | Priority order, plain descriptions, flag anything **blocked on the user** and exactly what's needed |
| Settled decisions | ~8 lines | What not to reopen, and any consequence that must be honoured |
| How to work here | ~5 lines | Working rules and what needs permission |
| Deeper detail | ~3 lines | Pointers to `docs/` for when depth is genuinely needed |

Adjust the weighting to the session — a heavy code session earns more "what to do next",
a planning session more "settled decisions". The 100-line ceiling is fixed regardless.

## Step 3 — Tone

Factual and calm. Don't oversell. If something is unverified, say so. **If an earlier
conclusion turned out to be wrong, say that plainly** — a status file that hides its own
corrections is worse than none, because it gets trusted.

## Step 4 — Commit

Verify the line count (`wc -l STATUS.md`) is at or under 100. Stage `STATUS.md` plus any
`docs/` files updated in Step 1. Commit with a message summarising the session's outcome,
not "update status". Repo style: short subject, then a body explaining *why*.

**Push only if the user asked.**

## Step 5 — Report back

Two or three sentences: what changed, the commit, and the single most important thing for
the next session to pick up.
