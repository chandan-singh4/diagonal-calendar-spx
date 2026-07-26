# plan.md — Current Implementation Plan

**Last updated:** 2026-07-26
**Current milestone:** M1 — Test Foundation
**Status:** 🔄 IN PROGRESS (started 2026-07-26). M0 ✅ COMPLETE and merged to `main`.
**Reference:** [`AUDIT_2026-07-25.md`](AUDIT_2026-07-25.md) §10 for the full roadmap

---

## Where we are

Phase 1 (audit) is complete and approved. M0 is done and merged. We are executing **M1**,
the second of three milestones on the critical path:

```
M0 Cleanup ──► M1 Tests ──► M2 Refactor ──┬──► M3 Data Hardening ──► M8 Deployment
      done      ▲ YOU ARE HERE             ├──► M4 API ──► M5 Dashboard (gated at 5.0)
                                          └──► M6 Analytics ──► M7 ML (gated on trades)
```

**Nothing else is safe until M0–M2 complete.** No feature work, no dashboard
migration, no analytics until the foundation exists.

---

## M0 — Stabilize & Clean

**Goal:** make the repository safe to work in. No behaviour change.
**Exit criteria:** clean `git status`, reproducible environment, linted, backed up,
~318 MB reclaimed, context files live.

| # | Task | Status | Notes |
|---|---|---|---|
| 0.1 | Automated backup of `data/dashboard.db` + verified restore | ✅ Done | 1.81 GB via SQLite online-backup API (safe against live writer). Integrity `ok`, FK clean, all 9 tables row-identical, checksums match. → `C:\Users\chand\Python\spx-dashboard-backups\` |
| 0.2 | Fix `.gitignore`; untrack runtime state/logs/DBs | ✅ Done | Root cause was UTF-16LE corruption. 6 files untracked (kept on disk), verified byte-identical. |
| 0.3 | Verify `.env`/`token.json` untracked; secret pre-commit hook | ✅ Done | `.githooks/pre-commit` installed via `core.hooksPath`. Blocks credentials, DBs, logs, runtime state, >1 MB files, and NULL bytes in `.gitignore`. Tested both directions. |
| 0.4 | Clean dirty index; delete orphans + superseded docs | ✅ Done | Removed `dashboard.db` (0 B orphan), `pinned_pairs.json`, June audit, original implementation plan. Index `AD` entries cleared. |
| 0.5 | `pyproject.toml`, pinned deps, lockfile, dev deps | ✅ Done | Upper bounds on all deps; `requirements.lock` = 63 packages pinned with provenance via `uv pip compile`. |
| 0.6 | `ruff` + `black` + `mypy` config; format in one isolated commit | 🔄 Partial | Config written into `pyproject.toml`. **Formatter run deferred** — see Decisions. |
| 0.7 | `.env.example`; rewrite `README.md` | ✅ Done | Both complete. README now documents real architecture, setup, backups, and conventions. |
| 0.8 | Establish `plan.md`, `progress_log.md`, `decisions.md`, `backlog.md`, `handoff.md` | ✅ Done | This file is part of it. |
| 0.9 | Backfill `decisions.md` with historical ADRs | ✅ Done | 12 ADRs backfilled from `DEV_JOURNAL.md` + 4 new decisions from this session. |
| 0.10 | Drop redundant indexes + legacy tables; `VACUUM` | ✅ Done | Rehearsed on backup clone first. **1.810 GB → 1.423 GB (387 MB, 21.4%)**. Zero data loss, integrity `ok`, no query regression. `db.py` `_DDL` updated so `init_db()` cannot resurrect the indexes. |
| 0.11 | Delete dead code; remove `db.py:1039` artifact | ✅ Done | 9 functions + 2 dataclasses + `demo_data.py` + `DEMO_MODE`/`DEMO_DB_PATH` + artifact removed. `transform_credit`/`calendar_edge`/`get_gaps` **retained** as M2/OPS targets. Cross-module reference check + smoke test pass. |
| 0.12 | Log rotation; add logging to dashboard modules | 🔄 Partial | `RotatingFileHandler` (1 MB × 5) in `collector.py`; log path now module-relative. Dashboard logging deferred to M2.13 (needs the decomposed modules to be useful). |
| 0.13 | Register collector scheduled task; relative paths in `.bat` | 🔄 Partial | `.bat` rewritten: `%~dp0`-relative, venv auto-discovery, unattended mode. **Task registration awaiting user approval** (modifies the system). |
| 0.14 | `CHANGELOG.md`; tag current state `v4.2` | 🔄 Partial | `CHANGELOG.md` created with full history reconstructed back to v0.1. Git tag pending first commit. |

---

## M1 — Test Foundation

**Goal:** be able to change code and know whether it still works. No behaviour change,
except where a test uncovers a defect worth fixing immediately.
**Exit criteria:** ~70% coverage of non-UI code, the money maths covered, scanner output
frozen before M2 moves it, and checks that run without being remembered.

| # | Task | Status | Notes |
|---|---|---|---|
| 1.1 | pytest + pytest-cov installed; `tests/` established | ✅ Done | Config already existed in `pyproject.toml` from M0.5. No fixture touches `data/dashboard.db`. |
| 1.2 | `iv_engine.py` unit tests | ✅ Done | 73 tests, **100% statement coverage**. Mutation-verified: 3 injected faults produced 6 failures. Also pins 3 decisions — no favorability claim, no framework imports (AST-checked), M0.11 removals stay removed. Found BUG-006, BUG-007. |
| 1.3 | Journal P&L maths (`resolved_pl`, `ic_expiry_pnl_per_share`, `derive_ic`, `compute_stats`) | ✅ Done | 53 tests. All 5 condor payoff regions + a cent either side of all 4 strikes. Asserts `resolved_pl` and `auto_final_pl` agree. Mutation-verified. Found BUG-009…013; **BUG-011 and BUG-012 fixed** (see ADR-021). |
| 1.4 | Scanner golden/characterization net before M2 | ✅ Done | 14 tests over 2 real snapshots (2608, 2482) captured read-only. Asserts no correctness — only that M2 changes nothing. Mutation-verified. **Known gap: DEBT-014**, the bid/ask fallback branch is not protected. |
| 1.5 | `db.py` tests (temporary SQLite DB, `integration` marker) | ⬜ Not started | **Next.** Everything reads through it and it has no tests. Marker already declared in `pyproject.toml`. |
| 1.6 | `collector.py` tests — session logic, gap classification | ⬜ Not started | Gap classifier is BUG-005; test it *before* fixing so the fix is a visible diff (ADR-019). |
| 1.7 | `schwab_client.py` tests — chain filtering, strike window | ⬜ Not started | Needs a fake API response fixture; no live calls. |
| 1.8 | Reach ~70% coverage of non-UI code | 🔄 Partial | `iv_engine` 100%; `db.py`/`collector.py`/`schwab_client.py` at 0%. |
| 1.9 | Checks that run on every save/commit without being remembered | ⬜ Not started | `.githooks/pre-commit` exists from M0.3 but only blocks secrets — it does not run the tests. |

**Scaffolding with an expiry date:** `tests/journal_loader.py` and `tests/app_loader.py` pull
functions out of Streamlit scripts via AST so they can be tested without launching a page or
touching the database. Both are **deleted at M2**, when the extracted modules can simply be
imported. Both raise rather than guess if a target moves.

---

## Immediate next actions

1. **M1.5 — test `db.py`.** Highest value remaining: every other module reads through it,
   and it has no tests at all. Use a temporary SQLite database per test (the `integration`
   marker is already declared); never the real one.
2. **M1.9 — make the checks run themselves.** They currently only run when someone
   remembers to type the command, which is the failure mode M1 exists to remove.
3. **M1.6 — test `collector.py`**, covering the gap classifier *before* fixing BUG-005, so
   the fix lands as a visible change to an existing test (ADR-019).
4. **Close DEBT-014** — capture a scanner fixture whose near-the-money rows have no stored
   price, so the bid/ask fallback is actually protected before M2 moves it.
5. **Still blocked on the user: BUG-001.** The duplicate-collector theory is dead (ADR-018).
   Needs a symptom, a tab, and a screenshot.

---

## Open decisions blocking later milestones

| Decision | Blocks | Detail |
|---|---|---|
| **Entry-IV-context snapshotting** | M3.2 | ~~Blocker~~ **downgraded 2026-07-26**: the 6 existing trades are being discarded, so no backfill is needed and pruning can proceed freely. The design fix still stands for *future* trades — `get_entry_iv_context()` reads historical `option_rows`, so any trade logged from now on loses its entry context once its expiries are pruned. Implement entry-IV snapshotting into `trades` **alongside** the pruner in M3, before the journal accumulates trades that matter. See ADR-016. |
| **M5.0 re-evaluation** | M5 | Whether Streamlit still hurts enough to migrate, decided with evidence after M2. Not pre-committed. |
| **Formatter run timing** | 0.6 | See ADR-015. |

---

## Explicitly deferred (do not start)

- Any dashboard/UI migration work — gated behind M2 + the M5.0 re-evaluation
- Any analytics or ML work — M6 needs ~20+ trades (currently 6); M7 needs ~100+
- Containerization / deployment — M8
- The open v4.1.1 "still having some issue" report — needs a symptom, view, and
  screenshot from the user before it can be diagnosed (see `backlog.md` BUG-001)
