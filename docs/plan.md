# plan.md — Current Implementation Plan

**Last updated:** 2026-07-26
**Current milestone:** M1 — Test Foundation
**Status:** 🔄 IN PROGRESS (started 2026-07-26). M0 ✅ COMPLETE and merged to `main`.
M1.1–1.5 and 1.9 done, 1.6 partial — **329 tests**, run automatically on every commit. `iv_engine` and `db.py` at 100%.
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
| 1.5 | `db.py` tests (temporary SQLite DB, `integration` marker) | ✅ Done | **111 tests, 100% statement coverage.** Mutation-verified on an isolated copy of the source: 26 injected faults, **24 caught**; the 2 survivors are proven *equivalent* mutants, documented in-test rather than left looking like holes. Covers the read-only guarantee, the FK cascade, transaction rollback, the dedup migration, every read query's filters/ordering/date-window, and the trades table. Found BUG-014, BUG-015, BUG-016, and **uncovered DEBT-008 as a silent data-loss risk (ADR-022)** — whose reporting half was then fixed in the same session. All three bugs were fixed later the same day (ADR-023); this file is now 123 tests. |
| 1.6 | `collector.py` tests — session logic, gap classification | 🔄 Partial | **Gap classification done: 38 tests, BUG-005 closed (ADR-024).** Pinned before fixing, so the fix is a visible diff. Mutation-verified on an isolated copy: 23 of 24 caught, 1 proven equivalent. **Still untested:** the collection cycle itself, session/poll-interval selection, token expiry handling, retry and backoff. |
| 1.7 | `schwab_client.py` tests — chain filtering, strike window | ⬜ Not started | Needs a fake API response fixture; no live calls. |
| 1.8 | Reach ~70% coverage of non-UI code | 🔄 Partial | `iv_engine` 100%, **`db.py` 100%**, `collector.py` gap logic covered; `schwab_client.py` still at 0% and most of `collector.py`'s cycle untested. Enforced on every commit by the M1.9 hook. |
| 1.9 | Checks that run on every save/commit without being remembered | ✅ Done | `.githooks/pre-commit` now runs the full suite when any `.py` file is staged (docs-only commits skip it, so editing the backlog stays instant). `core.hooksPath` was already set, so it is live. **Exercised, not assumed:** verified it skips on a docs-only commit, passes with `VIRTUAL_ENV` unset (the venv is outside the project — DEBT-020), **blocks a deliberately failing test**, and honours the `SKIP_TESTS=1` bypass. |

**Scaffolding with an expiry date:** `tests/journal_loader.py` and `tests/app_loader.py` pull
functions out of Streamlit scripts via AST so they can be tested without launching a page or
touching the database. Both are **deleted at M2**, when the extracted modules can simply be
imported. Both raise rather than guess if a target moves.

---

## Immediate next actions

1. **Finish M1.6 — the rest of `collector.py`.** The gap classifier is done
   (ADR-024); the collection cycle, session selection, retry/backoff and token
   handling are still bare. This is the largest untested surface left.
2. **The 6 practice trades can now safely be discarded.** BUG-016 was the blocker — the
   next ID after a deletion collided with a live PRIMARY KEY and the save raised. Fixed
   2026-07-26 (ADR-023 §1) and covered by a test that runs exactly that sequence. Note the
   related commitment in STATUS.md: the app must save market conditions *with each trade*
   before serious trading resumes.
3. **Close DEBT-014** — capture a scanner fixture whose near-the-money rows have no stored
   price, so the bid/ask fallback is actually protected before M2 moves it.
4. **Install `ruff`** into the shared venv. It is declared in the `dev` optional-dependency
   group but was never installed, so nothing is linted — including the M1.5 test files. Once
   present, add it to the M1.9 pre-commit hook alongside the test run.
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
