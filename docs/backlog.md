# backlog.md — TODOs, Enhancements, Bugs, Technical Debt, Future Ideas

**Last updated:** 2026-07-25
**Source:** `AUDIT_2026-07-25.md` §7 + follow-ups scattered through `DEV_JOURNAL.md`

Priority: **P0** blocks the current milestone · **P1** next milestone · **P2** scheduled ·
**P3** someday

---

## 🐞 Bugs

| ID | P | Item | Notes |
|---|---|---|---|
| BUG-001 | **P0** | **Unresolved v4.1.1 report: user said "still having some issue" with the canonical `app.py`; session ended before specifics were captured** | Needs exact symptom, which view/tab, and a screenshot. Per the 2026-07-07 journal: do NOT assume it is the same rendering bug — verify from evidence. Confirm the canonical file actually replaced the local one (look for `app_backup.py`), confirm a clean restart, confirm collector running. **Hypothesis ruled out 2026-07-26:** Chandan reported the issue "started with" two `collector.py` processes that neither he nor a previous session could explain or stop. There is no duplicate. `.venv\Scripts\python.exe` is a **uv trampoline** (241 KB) that re-execs the real interpreter (`~\AppData\Roaming\uv\python\cpython-3.14-…\python.exe`, 91 KB) as a **child process**, so one collector always appears as two `python.exe` entries in Task Manager — parent/child, started a second or two apart. Verified via `Win32_Process` `ParentProcessId` + `ExecutablePath`. Only one Startup-folder shortcut exists; no scheduled task, no `Run` key. **The duplicate was never the cause; BUG-001 remains open with no leads.** |
| BUG-002 | P1 | Selected-strike IV chart may draw a connector across a holiday | `_break_sessions()` is wired into `_gap_df` and `atm_merged` but not the cm/pm frames. One-line fix. (ADR-006) |
| BUG-003 | P2 | Unsaved-changes guard false-positives on untouched forms | `st.form` only delivers values on submit, so the guard detects edit-mode *activation*, not field changes. Documented in `DOCUMENTATION.md` §12.8. Likely unfixable within Streamlit. |
| BUG-004 | P2 | `MARKET_HOLIDAYS` covers 2026 only | Will silently misclassify collection gaps from Jan 2027. Make multi-year or compute. |
| BUG-005 | **P1** | **`_classify_gap()` misclassifies every routine overnight/weekend gap as `COLLECTOR_OFFLINE`** | Discovered 2026-07-25. **All 46 rows in `collection_gaps` are `COLLECTOR_OFFLINE`; the classifier has never produced `MARKET_CLOSED` or `HOLIDAY`.** ≥19 are plainly routine (15:59 → 09:30 next morning). Root cause — two off-by-ones against the collector's own window at `collector.py:182-186`: `after_close = start.time() >= 16:00` but the last write of the day lands at 15:59; `before_open = end.time() < 09:30` but the collector restarts at 09:30–09:31. Both False ⇒ falls through to `COLLECTOR_OFFLINE`. No weekend awareness beyond a crude `>3600 min` heuristic. **Fix approach:** compute actual *market minutes* inside the gap window; classify `MARKET_CLOSED` when ≈0. **Blocks M3.4** (liveness alerting on a classifier that cries wolf 100% of the time is useless) and makes OPS-005 render 46 false alarms. Existing rows need a one-time reclassification. |

| BUG-006 | P2 | `interpret_curve()` reports a NaN ratio as "Backwardation" | Found by the M1.2 tests, 2026-07-26. When `back_iv` is 0, `term_structure()` sets `ratio = NaN`. Every NaN comparison is False, so both branches in `iv_engine.py:117-122` fall through to the final `else` and the function states *"Backwardation (inverted) — front IV above back"* as fact, with no signal the input was unusable. **Fix:** guard `math.isnan(ts.ratio)` first and return an explicit "insufficient data" string. Current behaviour is pinned by `test_interpret_curve_reports_backwardation_for_a_nan_ratio` — fixing it must update that test deliberately. Low impact today (a 0 back IV would be a data fault, not a market state). |
| BUG-007 | P3 | Truthiness guards on floats treat a legitimate `0.0` as missing | Found by the M1.2 tests, 2026-07-26. `calendar_edge()` uses `if (fc.iv and bc.iv)` (`iv_engine.py:336-339`), so an IV of exactly `0.0` yields `edge=None`, indistinguishable from absent data. **No impact today** — 0.0 IV is not a physically meaningful SPX quote. Logged because the same `and`-on-floats pattern is a latent trap if copied to a quantity where `0.0` is valid; theta and edge both legitimately reach 0.0. **Fix:** compare `is not None` explicitly. Pinned by `test_calendar_edge_treats_zero_iv_as_missing`. |
| BUG-008 | P3 | Startup gap logged on every restart regardless of cause | Instance of BUG-005. Restarting the collector on a Sunday logged *"Startup gap recorded: 2432 min of missing data (~486 snapshots lost). Reason: COLLECTOR_OFFLINE"* for a weekend during which nothing could have been collected. Fixed by the BUG-005 market-minutes classifier. |
| BUG-009 | P3 | `ic_max_profit` is rounded, and float error decides which way | Found by the M1.3 tests, 2026-07-26. `derive_ic()` does `max_p = round(locked * 100 * contracts)` (`pages/journal.py:592`) while nothing else in the returned dict is rounded. With credit 10.005 and debit 4.0, the locked value is 6.005000000000001 in binary float, so ×100 lands a hair above the midpoint and rounds **up** to 601 where exact arithmetic gives 600. Half a dollar is immaterial alone, but `ic_max_profit` feeds `ic_worst_case` and the `ic_risk_free` boolean, so a representation artefact reaches a displayed figure and a flag. Pinned by `test_derive_ic_max_profit_is_rounded_to_whole_dollars`. |
| BUG-010 | P3 | A flawless record and a missing calculation look identical on screen | `compute_stats()` returns `Profit Factor: None` when there are no losses, because the divisor is 0. Rendered as blank — indistinguishable from "not computed". Defensible, but worth an explicit "no losses" label. Pinned by `test_compute_stats_all_winners_gives_no_profit_factor`. |
| BUG-011 | P2 | A break-even trade is counted as a loser and flatters every other statistic | Found by the M1.3 tests, 2026-07-26. `compute_stats()` splits on `p > 0` for wins and `p <= 0` for losses (`pages/journal.py:541-542`), so a scratch trade (P&L exactly 0) lowers the win rate **and** enters `Average Loser` as a zero — pulling the average loss toward zero, which inflates both Profit Factor and Expectancy at once. Reachable: a Transformed trade whose locked credit exactly offsets its assignment cost. **Fix:** three-way split (win / loss / scratch), scratches excluded from both averages. Pinned by `test_compute_stats_counts_a_breakeven_trade_as_a_loss`. |
| BUG-012 | **P1** | **A NULL `total_debit` crashes the entire statistics panel** | Found by the M1.3 tests, 2026-07-26. `compute_stats()` builds `debits = [float(r["total_debit"]) for r in rows]` (`pages/journal.py:555`) with no None guard — unlike the `credit_received` comprehension on the very next line, which does guard. One row with a NULL `total_debit` raises `TypeError` out of `compute_stats()`, so the whole panel fails to render, not just that one average. Needs a legacy or hand-edited row to trigger today (`total_debit` is required at entry). **Fix:** mirror the `credit_received` guard. Pinned by `test_compute_stats_crashes_on_a_null_total_debit`. |
| BUG-013 | P2 | Dead duplicate of `total_fees()` body stranded inside `resolved_pl()` | Found while reading for the M1.3 tests, 2026-07-26. `pages/journal.py:290-298` is an orphaned copy of `total_fees()`'s docstring and body sitting **after** `return None` at the end of `resolved_pl()` — unreachable, and evidently a `def` line lost in an edit. Harmless at runtime but actively misleading: it reads as if `resolved_pl()` computes fees. Delete during M2.13. |

---

## 🔧 Technical Debt

### Must clear before major feature work

| ID | P | Item | Milestone |
|---|---|---|---|
| DEBT-001 | **P0** partly cleared | ~~No automated tests anywhere (9,628 lines)~~ — 137 tests as of 2026-07-26: `iv_engine` at 100%, the journal P&L maths, and a scanner golden net. **Still bare:** `db.py`, `collector.py`, `schwab_client.py`, and the rest of `app.py`. | M1 |
| DEBT-014 | P2 | Scanner golden fixtures do not exercise the bid/ask midpoint fallback | Found 2026-07-26 by mutation-testing the golden net: altering the midpoint formula inside `_compute_transform_scanner` changed nothing, so the net does **not** protect that branch during M2. Both captured snapshots do contain NULL-`mark` rows (77 of 3,096 in snapshot 2608), but none reach the top-50 output — the branch runs and its result is discarded. **Fix:** capture a third fixture chosen for NULL marks near the money, or add a synthetic chain. | M2 |
| DEBT-002 | **P0** | `app.py` is a 4,230-line procedural script (757 lines CSS, 257 top-level statements) | M2 |
| DEBT-003 | **P0** | Unbounded DB growth (~82 MB/trading day, ~20 GB/yr) | M3 |
| DEBT-004 | **P0** | `$5.00` transform threshold duplicated ×4 as literals (`app.py:1575, 3103, 3686, 3704`); not in `config.py` | M2.2 |
| DEBT-005 | **P0** | `DOCUMENTATION.md` drifted from code — see DEBT-010 | M2.14 |
| DEBT-006 | **P0** | `±5` wing offset hardcoded in `app.py:2559` **and** inside SQL in `db.py:815-900` | M2.2 |

### Clear during refactor

| ID | P | Item | Milestone |
|---|---|---|---|
| DEBT-007 | P1 | Silent exception swallowing: 11× `except: pass`, 4× bare `except:` in `journal.py` — in P&L display paths | M2.13 |
| DEBT-008 | P1 | `INSERT OR IGNORE` masks legitimate write failures; no rowcount logged (ADR-004) | M3.6 |
| DEBT-009 | P1 | 757-line CSS embedded in `app.py`, targeting Streamlit-internal DOM classes | M2.7 |
| DEBT-010 | P1 | Doc drift: `transform_credit()`/`calendar_edge()` documented as core but never called; Entry Locks undocumented; doc stops at v4.1; §14.8 wrongly claims `chart_colors.json` is gitignored; Pinned Pairs/Pair Scanner documented but removed in v3.3 | M2.14 |
| DEBT-011 | P1 | Sidecar JSON (`eligible_history.json` 599 KB, `entry_locks.json`) has no schema, no validation, no atomic write, no backup | M2.6 |
| DEBT-012 | P1 | Duplicated helpers: `_safe_float` ×2, mark-fallback `(bid+ask)/2` ×4, expected-move calc ×2 | M2 |
| DEBT-013 | P1 | No logging in `app.py`/`journal.py` — loggers created, never called | M0.12 |
| DEBT-014 | P1 | ~6 dangling citations to the deleted June audit in `DOCUMENTATION.md` §3.1/§9.4/changelog and `iv_engine.py` lines 81-84, 110-111, 360-361 (ADR-011). Recovery SHA `6329fa28` | M2.14 |
| DEBT-015 | P2 | Dead code: ~13 functions + `demo_data.py` + `DEMO_MODE` + 3 legacy DB tables | M0.11 |
| DEBT-016 | P2 | Redundant indexes: `idx_option_rows_contract` (218 MB), `idx_option_rows_snapshot_id` (100 MB) | M0.10 |
| DEBT-017 | P2 | Code-generator artifact string at `db.py:1039-1045`; mid-file `import json as _json` | M0.11 |
| DEBT-018 | P2 | `_run_mission_control` ~170 ln; Calendar Edge tab body ~750 ln | M2.10 |

### Opportunistic

| ID | P | Item |
|---|---|---|
| DEBT-019 | P2 | `README.md` fully obsolete (describes an MVP scaffold, references nonexistent `.env.example` flow, Demo Mode) |
| DEBT-020 | P2 | `start_collector.bat` hardcodes absolute paths; venv lives *outside* the project at `C:\Users\chand\Python\.venv` and is shared |
| DEBT-021 | P2 | `collector.log` unrotated (486 KB and growing) |
| DEBT-022 | P3 | No `LICENSE`, no `CONTRIBUTING.md` |
| DEBT-023 | P3 | No `__version__` / VERSION file; "v4.2" exists only in a commit message |
| DEBT-024 | P3 | Deleted `trade_forensics_2026_06_23.ipynb` (1,397 ln) no longer discoverable; in history only |

---

## ⚙️ Operations

| ID | P | Item |
|---|---|---|
| ~~OPS-001~~ | ~~P0~~ | ~~`register_collector_task.ps1` was never registered — collection depends on manual start.~~ **CLOSED 2026-07-26 — the audit finding was wrong.** Auto-start has worked since 2026-06-22 via a **Startup folder shortcut** (`shell:startup` → `.venv\Scripts\python.exe collector.py`, working dir = project root). Verified: shortcut intact, all paths resolve, collector running continuously since the 7/16 logon with full 126-snapshot sessions daily. `DEV_JOURNAL.md` 2026-06-22 documents why Task Scheduler was rejected — the `.bat` was blocked by Windows Smart App Control and the PowerShell script failed without admin rights (reproduced 2026-07-26: `Register-ScheduledTask` and `schtasks /sc onlogon` both return Access Denied, because ONLOGON triggers require elevation). The Startup shortcut is the correct solution for this machine; do not replace it. |
| OPS-001b | P1 | **No crash recovery for the collector.** The Startup shortcut starts it at logon but nothing restarts it if the process dies mid-session — the capability Task Scheduler's `RestartCount` would have provided. This is the *real* residual gap behind the original OPS-001 concern, and it is much smaller: a crash costs part of one session, not every session. Pairs with OPS-003. |
| OPS-002 | **P0** | Backups now exist (M0.1) but are **manual**. Automate + rotate. |
| OPS-003 | P1 | No collector liveness alerting — if it dies mid-session, only a staleness dot changes |
| OPS-004 | P1 | Schwab token re-auth is a manual weekly copy-paste chore; needs a documented runbook |
| OPS-005 | P1 | `collection_gaps` is populated but never surfaced anywhere in the UI |
| OPS-006 | P2 | Confirm Streamlit binds localhost only before any Tailscale exposure (ADR-012) |
| OPS-007 | P2 | No dependency vulnerability scanning (`pip-audit` now in dev deps, not yet wired) |

---

## ✨ Enhancements

| ID | P | Item | Milestone |
|---|---|---|---|
| ENH-001 | P1 | FastAPI layer over `core/` — needed for mobile access (ADR-012) | M4 |
| ENH-002 | P1 | WebSocket push on new snapshot, replacing polling | M4.4 |
| ENH-003 | P2 | Threshold calibration from real fills ($5.00 → ~$6.50–7.00); needs ≥10 transformations | M6.1 |
| ENH-004 | P2 | Complete Regime Analysis validation; needs ~20+ trades (**currently 6**) | M6.2 |
| ENH-005 | P2 | Net Theta Advantage / time-to-viability from per-leg Greeks — the honest replacement for the removed Theta ETA | M6.4 |
| ENH-006 | P2 | Payoff diagrams: diagonal (BS pre-expiry) + IC (intrinsic at expiry) | M6.5 |
| ENH-007 | P3 | Backtest engine: replay stored IV history against entry rules | M6.3 |
| ENH-008 | P3 | In-place chart updates preserving zoom/pan across new data | M5.3 |
| ENH-009 | P3 | Remove ▲/▼ carets from the Diagonal/Transform chart (low information once data is clean) | M5 |
| ENH-010 | P3 | Full Journal integration for entry locks (`journal_trade_id` scaffold already present) | M6 |

---

## 💡 Future Ideas (unscheduled, not committed)

- Data-quality flagging: IV outliers, stale quotes, missing legs
- Narrower "hot" table (`snapshot_id, expiry, strike, right, mark, iv`) for chart queries
- Mobile-specific layout once M5 lands (ADR-012 makes this likely to matter)
- Desktop notifications when a transform threshold is crossed
- Schwab Streamer (websocket) API instead of polling — only if polling ever feels slow
- VIX term structure as an additional regime dimension

---

## ✅ Recently Completed

| Date | Item |
|---|---|
| 2026-07-25 | M0.1 — Verified database backup (1.81 GB, online backup API, restore verified) |
| 2026-07-25 | M0.2 — `.gitignore` UTF-16 corruption fixed; 6 runtime files untracked |
| 2026-07-25 | M0.4 — Orphans and superseded docs removed; dirty index cleared |
| 2026-07-25 | M0.8/0.9 — Context files established; 16 ADRs recorded |
| 2026-07-25 | Phase 1 engineering audit completed and approved |
