# plan.md — Current Implementation Plan

**Last updated:** 2026-07-30
**Current milestone:** M2 — Architecture Refactor *(pre-work COMPLETE; steps 1–4 of 5 done — `core/`, `dataaccess/`, `state/` and `views/` all extracted)*
**Status:** M0 ✅ and M1 ✅ COMPLETE and merged to `main`. **623 tests**, run automatically on every
commit. **80% coverage of non-UI code** against a ~70% target; `iv_engine`, `db.py`,
`schwab_client.py` and `config.py` at 100%. Every module mutation-verified rather than assumed —
**56 injected across M1, 54 caught, 2 proven equivalent**, plus 7 in the `check_db.py` work, 9 in
the display pinning (ADR-029), 43 across the Mission Control pinning (ADR-030/031), 7 + 10 across
the `views/` extraction (ADR-036/037) and 6 clearing the debt behind it (ADR-038).
**M2's pre-work is COMPLETE and DEBT-026 is closed.** 88 characterization tests cover what reaches
the screen. **Steps 2.1–2.4 are done: `app.py` is 4,283 → 2,480 lines,** down 42% and no longer the
largest file in the repo. All six tabs live in `views/`, and every one of the six bodies was proved
byte-identical to the version it replaced before anything was renamed. **The debt those safe moves
deliberately left behind is now cleared too** — DEBT-028, DEBT-030 and DEBT-032, all closed
2026-07-30 (ADR-038). **Step 2.5 is the remaining work**, targeting `app.py` under 400 lines; of
what is left, 757 lines are the CSS block and 115 are `_render_mc_section`.
**Reference:** [`AUDIT_2026-07-25.md`](AUDIT_2026-07-25.md) §10 for the full M0–M8 roadmap —
a **frozen snapshot**, superseded by this file wherever they differ (see the banner at its head,
and ADR-028 for the two M1 tasks settled differently).

---

## Where we are

Phase 1 (audit) is complete and approved. M0 and M1 are both done and merged. Next is **M2**,
the last of the three milestones on the critical path:

```
M0 Cleanup ──► M1 Tests ──► M2 Refactor ──┬──► M3 Data Hardening ──► M8 Deployment
      done          done      ▲ YOU ARE HERE
                                          ├──► M4 API ──► M5 Dashboard (gated at 5.0)
                                          └──► M6 Analytics ──► M7 ML (gated on trades)
```

**Nothing else is safe until M0–M2 complete.** No feature work, no dashboard
migration, no analytics until the foundation exists.

---

## All nine milestones

The full M0–M8 set at a glance. Task-level detail for the milestones reached so far is in the
sections below; the rest is in [`AUDIT_2026-07-25.md`](AUDIT_2026-07-25.md) §10.

**Complexity:** **S** ≈ hours · **M** ≈ 1–2 days · **L** ≈ 3–5 days · **XL** ≈ 1–2 weeks
(solo, part-time). The spread is per task, not a total — a milestone of 14 M-sized tasks is
weeks of work, not days.

| # | Milestone | Goal | Exit criteria | Complexity (task spread) | Depends on | Blocks | State |
|---|---|---|---|---|---|---|---|
| **M0** | Stabilize & Clean | Make the repo safe to work in. No behaviour change. **Blocks everything.** | Clean `git status`, reproducible env, linted, backed up, ~318 MB reclaimed, context files live | 14 tasks — 10 S, 4 M | — | **everything** | ✅ Done, merged |
| **M1** | Test Foundation | Make change safe. **Keystone — everything after depends on it.** | ≥70% coverage of non-UI code, checks that run unprompted, regressions in M2 detectable | 9 tasks — 3 S, 4 M, 2 L | M0 | M2 → all | ✅ Done (80%, 462 tests) |
| **M2** | Architecture Refactor | Decompose the monolith. Behaviour-preserving, verified by M1. | `app.py` < 400 lines (composition only); `core/` framework-agnostic and fully tested | 14 tasks — 10 M, 4 L | M1 | M3, M4, M6 (so M5, M7, M8 too) | ← **next** |
| **M3** | Data Layer Hardening | Put the data pipeline on a sustainable footing | Bounded DB growth, monitored collection, documented operations | 9 tasks — 3 S, 4 M, 2 L | M2 | M8; 3.2 also gates M6.3 | Not started |
| **M4** | Backend API *(gated)* | Stable contract over `core/`; prerequisite for any UI migration | Documented API; Streamlit still running unchanged | 5 tasks — 1 S, 3 M, 1 L | M2 | M5 | Not started |
| **M5** | Dashboard Modernization *(decision point)* | Resolve the Streamlit ceiling — **only if it still hurts after M2** | Live-updating UI without full-page reruns; charts hold zoom across updates | 7 tasks — 1 S, 1 M, 3 L, 2 XL | M2 + M4 (5.0 gate) | — *(leaf)* | Gated at 5.0 |
| **M6** | Analytics Engine | Answer the questions the project set out to answer | Core unvalidated questions answered from data, or explicitly retired | 6 tasks — 2 M, 3 L, 1 XL | M2 (6.3 also needs 3.2) + ~20 trades | M7 | Blocked: needs ~20 trades, have 6 |
| **M7** | Machine Learning *(explicitly gated)* | Only if it beats a naive baseline — below the trade threshold any model fits noise | Ship only if it beats the naive baseline out-of-sample | 4 tasks — 1 M, 2 L, 1 XL | M6 (backtest engine 6.3) + ≥100 trades | — *(leaf)* | Blocked: have 6 trades |
| **M8** | Production Deployment | Operate reliably without babysitting | Containerized, supervised, monitored, offsite backups, tested DR runbook | 7 tasks — all M | M3 | — *(leaf)* | Not started |

**The shape:** `M0 → M1 → M2` is a strict chain, then it forks three ways. Only M8 sits at the end
of a branch; M5 and M7 are the other two leaves.

```
M0 ──► M1 ──► M2 ──┬──► M3 ──► M8
                   ├──► M4 ──► M5 (gated at 5.0)
                   └──► M6 ──► M7 (gated on trade count)
```

**Three gates are deliberate refusals to pre-commit, not just "later":** **M5.0** re-decides
whether Streamlit is still painful *after* M2 has made that decision cheap and reversible;
**M6.2** needs ~20+ trades; **M7** needs ~100+. See ADR-013 and ADR-001.

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
| 0.6 | `ruff` + `black` + `mypy` config; format in one isolated commit | 🔄 Partial | Config written into `pyproject.toml` in M0 but **ruff was never installed**, so nothing was linted until 2026-07-26. Now installed and run: noise families silenced with reasons, 55 mechanical items auto-fixed, 85 judgement calls left (DEBT-025). **Formatter run still deferred** — ADR-015 holds while `app.py` and `pages/journal.py` remain untested. |
| 0.7 | `.env.example`; rewrite `README.md` | ✅ Done | Both complete. README now documents real architecture, setup, backups, and conventions. |
| 0.8 | Establish `plan.md`, `progress_log.md`, `decisions.md`, `backlog.md`, `handoff.md` | ✅ Done | This file is part of it. |
| 0.9 | Backfill `decisions.md` with historical ADRs | ✅ Done | 12 ADRs backfilled from `DEV_JOURNAL.md` + 4 new decisions from this session. |
| 0.10 | Drop redundant indexes + legacy tables; `VACUUM` | ✅ Done | Rehearsed on backup clone first. **1.810 GB → 1.423 GB (387 MB, 21.4%)**. Zero data loss, integrity `ok`, no query regression. `db.py` `_DDL` updated so `init_db()` cannot resurrect the indexes. |
| 0.11 | Delete dead code; remove `db.py:1039` artifact | ✅ Done | 9 functions + 2 dataclasses + `demo_data.py` + `DEMO_MODE`/`DEMO_DB_PATH` + artifact removed. `transform_credit`/`calendar_edge`/`get_gaps` **retained** as M2/OPS targets. Cross-module reference check + smoke test pass. |
| 0.12 | Log rotation; add logging to dashboard modules | 🔄 Partial | `RotatingFileHandler` (1 MB × 5) in `collector.py`; log path now module-relative. Dashboard logging deferred to M2.13 (needs the decomposed modules to be useful). |
| 0.13 | Auto-start the collector; relative paths in `.bat` | ✅ Done | `.bat` rewritten: `%~dp0`-relative, venv auto-discovery, unattended mode. **Auto-start works today via a Startup-folder shortcut** (`shell:startup`, in place since 2026-06-22). **Verified live 2026-07-26:** shortcut present, collector running from it, scheduled task absent. **Task Scheduler is deliberately NOT used** — `ONLOGON` triggers require elevation, so `register_collector_task.ps1` cannot register from a normal shell (re-confirmed 2026-07-26). The script is kept for a future machine with an elevated shell, where restart-on-crash would be worth having (OPS-001b). Registering it now would add a second launcher beside the shortcut for no gain. **The earlier "awaiting user approval" note was wrong** — this was a settled decision, not a pending one. |
| 0.14 | `CHANGELOG.md`; tag current state `v4.2` | ✅ Done | `CHANGELOG.md` created with full history reconstructed back to v0.1. **Tag `v4.2` exists at `104d582`** (2026-07-25, the M0 cleanup commit) — verified 2026-07-26. The earlier "pending" note was stale. |

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
| 1.4 | Scanner golden/characterization net before M2 | ✅ Done | 14 tests over 2 real snapshots (2608, 2482) captured read-only. Asserts no correctness — only that M2 changes nothing. Mutation-verified. **DEBT-014 closed 2026-07-26:** 6 further tests drive the bid/ask midpoint fallback with a built chain, asserting real arithmetic (a midpoint is not a matter of opinion). Proven by re-running the mutations: **5 of 6 that the golden fixtures MISSED are now caught, 0 survivors.** The lesson, recorded at the point of use: *a characterization test can only protect what appears in its output* — the fixtures did contain NULL-`mark` rows, but none reached the top-50 result. |
| 1.5 | `db.py` tests (temporary SQLite DB, `integration` marker) | ✅ Done | **111 tests, 100% statement coverage.** Mutation-verified on an isolated copy of the source: 26 injected faults, **24 caught**; the 2 survivors are proven *equivalent* mutants, documented in-test rather than left looking like holes. Covers the read-only guarantee, the FK cascade, transaction rollback, the dedup migration, every read query's filters/ordering/date-window, and the trades table. Found BUG-014, BUG-015, BUG-016, and **uncovered DEBT-008 as a silent data-loss risk (ADR-022)** — whose reporting half was then fixed in the same session. All three bugs were fixed later the same day (ADR-023); this file is now 123 tests. |
| 1.6 | `collector.py` tests — session logic, gap classification | ✅ Done | **Gap classification: 38 tests, BUG-005 closed (ADR-024).** Pinned before fixing, so the fix is a visible diff. Mutation-verified: 23 of 24 caught, 1 proven equivalent. **Collection cycle (2026-07-26, session 5): 23 tests over `_run_cycle`,** driven end-to-end against a temporary database with Schwab patched — assertions read back stored rows, not mock calls. Mutation-verified: **7 faults, 7 caught.** Found and fixed BUG-017. **Loop decisions (same session): 47 tests.** Four judgements were extracted from `main()`'s infinite loop first — `is_auth_error`, `failure_is_critical`, `sleep_after_cycle`, `should_recheck_token` — because no test can enter a loop that never returns, sleeps in real time and calls Schwab. Extraction changed no behaviour (suite green across it). Mutation-verified: **11 faults, 11 caught, 0 survivors.** **Remaining by design:** `main()`'s own body — process wiring, not logic. |
| 1.7 | `schwab_client.py` tests — chain filtering, strike window | ✅ Done | **45 tests, 100% statement coverage.** No live calls: a `FakeResponse` supplies canned payloads and token tests use `tmp_path`. Mutation-verified: **14 injected faults, 14 caught, 0 survivors.** Pins the response SHAPE as executable fixtures, so a Schwab field rename fails here loudly instead of thinning the database silently. Also pins three asymmetries that are deliberate: IV stays a **percentage** at this layer (÷100 belongs to the collector), a **VIX** failure is non-fatal while an **SPX quote** failure is fatal, and the 2 SD expected-move check only ever warns. |
| 1.8 | Reach ~70% coverage of non-UI code | ✅ Done | **80% overall.** `iv_engine`, `db.py`, `schwab_client.py`, `config.py` all at 100%; `collector.py` at 56% — the shortfall is `main()`'s body, the logging setup and the single-instance lock, none of which are reachable from a test and none of which hold logic. Enforced on every commit by the M1.9 hook. |
| 1.9 | Checks that run on every save/commit without being remembered | ✅ Done | `.githooks/pre-commit` now runs the full suite when any `.py` file is staged (docs-only commits skip it, so editing the backlog stays instant). `core.hooksPath` was already set, so it is live. **Exercised, not assumed:** verified it skips on a docs-only commit, passes with `VIRTUAL_ENV` unset (the venv is outside the project — DEBT-020), **blocks a deliberately failing test**, and honours the `SKIP_TESTS=1` bypass. |

**Numbering differs from `AUDIT_2026-07-25.md` §10 — these numbers are the real ones,** and are
what commit messages refer to. Execution reordered the middle of M1: the audit's 1.4 (collector)
became 1.6, its 1.6 (`schwab_client`) became 1.7, and its 1.7 (scanner net) was pulled forward to
1.4. Two audit tasks were also settled differently rather than done — **1.8 GitHub Actions CI**,
met instead by the pre-commit hook at 1.9, and **1.9 `docs/TESTING.md`**, retired. Both recorded
in **ADR-028**. The audit's exit criterion "≥70% coverage" was promoted to a task here (1.8).

**Scaffolding with an expiry date:** `tests/journal_loader.py` and `tests/app_loader.py` pull
functions out of Streamlit scripts via AST so they can be tested without launching a page or
touching the database. Both are **deleted at M2**, when the extracted modules can simply be
imported. Both raise rather than guess if a target moves.

---

## M2 — Architecture Refactor · pre-work

**Goal of the pre-work:** before anything moves, make a silent behaviour change impossible to
miss. M1 pinned what the code *computes*; this pins what the screen *shows*.

**Status: the pre-work is complete.** 2.0a and 2.0b are both done, DEBT-026 is closed, and the
decomposition below can start. 88 characterization tests now stand between a refactor and a
silently changed screen.

| # | Task | Status | Notes |
|---|---|---|---|
| 2.0a | Pin the pure display layer | ✅ Done 2026-07-29 | **28 tests** in `tests/test_display_golden.py` over card ordering (`_rank_for_panel`), IV-ratio chart geometry (`_banded_ratio_traces`), the on-card formatters (`_sparkline`, `_fmt_duration`, `_fmt_eta`) and card identity (`_card_key`). Ordering is asserted against the **same real production snapshots** the scanner goldens use, so the chain *snapshot → scanner → ranked panel* is pinned end to end. Mutation-verified: **9 faults injected into a copy of `app.py`, 9 caught** — but only after fixing 2 assertions that were too weak to fail (the lesson is ADR-029, and it is the one worth reading). `tests/app_loader.py` gained a second entry point, `load_display_functions()`, with its own namespace so the scanner's "no I/O, no drawing" guarantee survives intact. |
| 2.0b | Pin the DB-backed Mission Control pipeline | ✅ Done 2026-07-29 | **60 tests over two files, and DEBT-026 is closed.** `test_mission_control_golden.py` (22) covers `_candidate_signals` — Duration Active, the ETA, the sparkline, the trend arrow. `test_mc_pipeline_golden.py` (38) covers `_compute_mc_core`, `_build_non_atm_panel`, `_run_mission_control`, the persisted eligibility registry and all nine `_load_*` wrappers. Everything runs against a **real temporary database** built through `db.py`'s own writers — never the production one. **The single most valuable test:** `_compute_mc_core` must rank *before* capping, or the asymmetric combos this strategy actually trades are discarded before ranking and vanish from the panel while the page still looks normal. Mutation-verified across both files: **43 injections, 40 caught, 3 proven equivalent** (ADR-030, ADR-031). Found DEBT-027 (two sites) and the relative-path hazard now on DEBT-011. |

**Then the decomposition itself, in this order** — pure maths first, appearance last, because
appearance is the part that cannot be tested and so should be the thinnest thing remaining:

| # | Task | Status | Notes |
|---|---|---|---|
| 2.1 | `core/` — pure functions, no DB and no Streamlit | ✅ Done 2026-07-29 | 4 modules: `format.py` (`_sparkline`, `_fmt_duration`, `_fmt_eta`), `charts.py` (`_break_sessions`, `_banded_ratio_traces` + rangebreaks and ratio bands), `ranking.py` (`_rank_for_panel`, `_card_key`), `scanner.py` (`_compute_transform_scanner`, `_scan_all_offsets` + Phase A thresholds). `app.py` **4,283 → 3,991** lines. **Scope rule: only code the 549 tests already pinned** — moving unpinned code is where a silent break would hide. **The `@st.cache_data` memo stayed in `app.py`** wrapping the moved scanner, because two callers share those saved results and `core/` cannot import Streamlit; `_scan_all_offsets` takes the wrapper through a new `compute=` argument (ADR-032). Zero test edits beyond repointing `tests/app_loader.py` at two sources and the tripwire it was built with. **10 new tests** in `test_core_layering.py` enforce the layer rule on the import list. |
| 2.2 | `dataaccess/queries.py` — the **nine** database reads | ✅ Done 2026-07-30 | **Named `dataaccess/`, not `data/`** — that directory already holds `dashboard.db` and `token.json`, and source code does not belong beside them (ADR-033). **The count was wrong here: nine, not eleven.** Three other `_load_*` functions read small JSON settings files, not the database; they belong to 2.3. Every function takes `db_path` as its first argument, which **closes DEBT-027 site 1** — `_candidate_signals` now takes the path too, defaulting to the global so production is unchanged. `snapshot_id` is gone from four signatures: it was never read, existing only to key the cache, which lives in `app.py`. The nine `@st.cache_data` wrappers stay there and are now memo-and-nothing-else. 564 tests (4 new); mutation-verified 6 injected, 6 caught. **Not done: returning data rather than display shapes** — the naive wall-clock conversion is a display concern in the data layer, but moving it changes every chart's x-axis and is its own job (DEBT-030). |
| 2.3 | `state/` — the JSON-file persistence | ✅ Done 2026-07-30 | 4 modules: `store.py` (absolute paths, atomic write, quarantine), plus `chart_colors.py`, `entry_locks.py`, `eligible_history.py`. **`config.STATE_DIR` is absolute and anchored to the project root** — following the convention `DB_PATH` already used, so no file moved. Closes **three of DEBT-011's five parts**, the three that could lose data; schema and scheduled backup remain (M3). **The dangerous one was not the relative path** but the loader that read an unreadable file as `{}` and let the next write destroy it. `state/` may not import `config` — it is handed its directory and timezone. 591 tests (22 new); 6 injected, 6 caught. Real files backed up and hash-verified before any change. |
| 2.4 | `views/` — one module per tab | ✅ **Done 2026-07-30** | All six tabs out: `historical.py`, `research.py`, `entry.py`, `strike.py`, `scanner.py`, `edge.py`, over a frozen `ViewContext`. **`app.py` 4,283 → 2,486** across the whole milestone, 3,945 → 2,486 in step 2.4 alone. **Every one of the six bodies is byte-identical to the version it replaced**, each compared against the commit in which it was still inline — that is the step's entire evidentiary basis, and it is why nothing was renamed, no threshold was corrected and no dead line was deleted along the way. 613 tests (22 new); mutation-verified **10 injected, 10 caught**. Lint +1 against baseline, and that one is a pre-existing dead assignment the extraction made *visible* (DEBT-032). All six tabs render, and all six produce byte-identical rendered text against a worktree of the previous commit on the same live database. **What is still in `app.py` and must move at 2.5:** `_render_mc_section`, 115 lines, injected rather than moved because it reads two prelude globals and relocating it means a signature change — the rename phase, not the move phase. **Entry needed no loaders at all** — eight metric tiles over numbers the prelude had already derived — so it was the cheapest possible test of whether the context grows cleanly: five new fields, no new seam, 600 tests. It also found two things by being read closely, neither caused by the move and both left alone: **DEBT-031** (the 5-point transform threshold hardcoded three times in this one tab, disagreeing with `core/scanner.py` at exactly 5.00 because one test is `>` and the rest are `>=`) and **BUG-018** (on expiry day the default front expiry is 0 DTE, so there is no straddle, and the Normalized Debit tile renders "— (set strikes)" beside a Diagonal Mark computed from those same strikes). Fixing either inside a move would have destroyed the one property that makes the move checkable. **The pattern being proven, and the reason for the tab order:** a tab body sat one indent level in under `if st.session_state[...]` and sits one level in under `def render(ctx)`, so each body moves with **zero reindentation** and the only new lines are rebinds of `ctx.x` to the name the body already used. That makes the move *provable* — a script diffs each moved body against `git show HEAD:app.py` and both came back **byte-identical**. This is the untestable layer; "nothing was rewritten in transit" is the strongest claim available, so the extraction is shaped to make it checkable. Renaming to `ctx.` throughout is DEBT-028, after all six are out. **7 new tests** in `test_layering.py`: `views/` may not import `db`/`sqlite3`/`dataaccess` (the last would bypass app.py's memos and re-query on every rerun while looking identical), `render` takes `ctx` and nothing else, no module-level drawing, and app.py must actually dispatch each one. 598 tests; mutation-verified **7 injected, 7 caught**. `test_chart_breaks.py` failed on its own anchor and was re-pointed, not weakened — and got stronger, since the module now IS the tab and the 2,000-character window is gone. |
| 2.5 | `app.py` — page config, sidebar, tab dispatch | ☐ | Target: under 400 lines. |

Steps 2.1–2.3 carry the value. Step 2.4 looks like the work and is the least interesting part of it.

**Debt cleared straight after 2.4, before starting 2.5** (ADR-038). Three rows that had been waiting
on the decomposition, all closed 2026-07-30. **DEBT-028:** `core/`'s 14 public names lost their
leading underscores, the 63 `ctx.` rebind lines that made each tab move provable were removed, and
`nearest_idx` finally moved to `core/` with the six tests the backlog said it lacked. **DEBT-030:**
`dataaccess/` now returns zoned UTC and `core.charts.to_display_time` converts at draw time — the
tripwire tests from ADR-034 failed on cue, which is what forced every chart site to be handled in
the same change. **DEBT-032:** half was dead code and was deleted; the other half turned out not to
be dead at all and is now **BUG-019**.

**The before/after check that 2.4 actually rests on.** `scripts/render_check.py` proves a tab does
not raise; it counts elements, and a count cannot see a lost decimal or a reordered panel. So each
moved tab is also compared by CONTENT: a scratch harness runs `AppTest` against a git worktree of
HEAD and against the working tree — same database, state redirected so nothing writes to the real
registry — and diffs every rendered string. Both moved tabs came back identical. **Two cautions
learned doing it, both worth more than the result:** the first version of that harness wrote *empty*
files (its output crashed on cp1252) and two empty files diff clean, so it now refuses to report a
dump of nothing; and **charts are not covered at all** — Streamlit 1.58's `AppTest` exposes no
accessor for `plotly_chart`, so for a chart tab the diff sees the captions and nothing else. Element
counts are also unusable for the Scanner tab, whose Mission Control panel legitimately changes
between two runs minutes apart as the collector adds snapshots.

---

## Immediate next actions

1. **M1 is complete and DEBT-014 is closed — M2 is unblocked.** Every task 1.1–1.9 is
   done, the coverage target is exceeded, and the scanner net no longer has the hole
   that would have let M2 change the midpoint fallback unnoticed. **M2 has now started:**
   step 2.1 is done and `app.py` is down to 3,991 lines, with appearance, calculations
   and data access still mixed together in what remains.
2. **The 6 practice trades can now safely be discarded.** BUG-016 was the blocker — the
   next ID after a deletion collided with a live PRIMARY KEY and the save raised. Fixed
   2026-07-26 (ADR-023 §1) and covered by a test that runs exactly that sequence. Note the
   related commitment in STATUS.md: the app must save market conditions *with each trade*
   before serious trading resumes.
3. **Work DEBT-025 down toward zero, then gate on it.** 85 lint findings remain after the
   first pass. Most dissolve during the M2 decomposition. Only wire `ruff check` into the
   M1.9 pre-commit hook once it is at zero — a gate that fails on every commit teaches
   everyone to bypass it.
4. **Still blocked on the user: BUG-001.** The duplicate-collector theory is dead (ADR-018).
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
