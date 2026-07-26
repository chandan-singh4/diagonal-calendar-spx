# backlog.md — TODOs, Enhancements, Bugs, Technical Debt, Future Ideas

**Last updated:** 2026-07-26
**Source:** `AUDIT_2026-07-25.md` §7 + follow-ups scattered through `DEV_JOURNAL.md`

Priority: **P0** blocks the current milestone · **P1** next milestone · **P2** scheduled ·
**P3** someday

> **OPEN items only — closing something means DELETING its row, never marking it fixed.**
> Git keeps the text (`git log -S "BUG-011" -- docs/backlog.md`). Git does *not* keep the
> reasoning: if the fix leaves a lesson or a ruled-out theory worth having later, write a
> short ADR in `decisions.md` first. Details: ADR-017.

---

## 🐞 Bugs

| ID | P | Item | Notes |
|---|---|---|---|
| BUG-001 | **P0** | **Unresolved v4.1.1 report: user said "still having some issue" with the canonical `app.py`; session ended before specifics were captured** | Needs exact symptom, which view/tab, and a screenshot. Per the 2026-07-07 journal: do NOT assume it is the same rendering bug — verify from evidence. Confirm the canonical file actually replaced the local one (look for `app_backup.py`), confirm a clean restart, confirm collector running. **Ruled out 2026-07-26 — see ADR-018:** the "two collector processes" Chandan reported this starting with are a single collector (a uv launcher plus the real interpreter). Not the cause; **still open, no leads.** |
| BUG-003 | P2 | Unsaved-changes guard false-positives on untouched forms | `st.form` only delivers values on submit, so the guard detects edit-mode *activation*, not field changes. Documented in `DOCUMENTATION.md` §12.8. Likely unfixable within Streamlit. |
| BUG-004 | P2 | `MARKET_HOLIDAYS` covers 2026 only | Will silently misclassify collection gaps from Jan 2027. Make multi-year or compute. |
| BUG-006 | P2 | `interpret_curve()` reports a NaN ratio as "Backwardation" | Found by the M1.2 tests, 2026-07-26. When `back_iv` is 0, `term_structure()` sets `ratio = NaN`. Every NaN comparison is False, so both branches in `iv_engine.py:117-122` fall through to the final `else` and the function states *"Backwardation (inverted) — front IV above back"* as fact, with no signal the input was unusable. **Fix:** guard `math.isnan(ts.ratio)` first and return an explicit "insufficient data" string. Current behaviour is pinned by `test_interpret_curve_reports_backwardation_for_a_nan_ratio` — fixing it must update that test deliberately. Low impact today (a 0 back IV would be a data fault, not a market state). |
| BUG-007 | P3 | Truthiness guards on floats treat a legitimate `0.0` as missing | Found by the M1.2 tests, 2026-07-26. `calendar_edge()` uses `if (fc.iv and bc.iv)` (`iv_engine.py:336-339`), so an IV of exactly `0.0` yields `edge=None`, indistinguishable from absent data. **No impact today** — 0.0 IV is not a physically meaningful SPX quote. Logged because the same `and`-on-floats pattern is a latent trap if copied to a quantity where `0.0` is valid; theta and edge both legitimately reach 0.0. **Fix:** compare `is not None` explicitly. Pinned by `test_calendar_edge_treats_zero_iv_as_missing`. **Now the last surviving site of this pattern** — the three in `db.py` were fixed 2026-07-26 (ADR-023 §2). |
| BUG-009 | P3 | `ic_max_profit` is rounded, and float error decides which way | Found by the M1.3 tests, 2026-07-26. `derive_ic()` does `max_p = round(locked * 100 * contracts)` (`pages/journal.py:592`) while nothing else in the returned dict is rounded. With credit 10.005 and debit 4.0, the locked value is 6.005000000000001 in binary float, so ×100 lands a hair above the midpoint and rounds **up** to 601 where exact arithmetic gives 600. Half a dollar is immaterial alone, but `ic_max_profit` feeds `ic_worst_case` and the `ic_risk_free` boolean, so a representation artefact reaches a displayed figure and a flag. Pinned by `test_derive_ic_max_profit_is_rounded_to_whole_dollars`. |
| BUG-010 | P3 | A flawless record and a missing calculation look identical on screen | `compute_stats()` returns `Profit Factor: None` when there are no losses, because the divisor is 0. Rendered as blank — indistinguishable from "not computed". Defensible, but worth an explicit "no losses" label. Pinned by `test_compute_stats_all_winners_gives_no_profit_factor`. |
| BUG-013 | P2 | Dead duplicate of `total_fees()` body stranded inside `resolved_pl()` | Found while reading for the M1.3 tests, 2026-07-26. `pages/journal.py:290-298` is an orphaned copy of `total_fees()`'s docstring and body sitting **after** `return None` at the end of `resolved_pl()` — unreachable, and evidently a `def` line lost in an edit. Harmless at runtime but actively misleading: it reads as if `resolved_pl()` computes fees. Delete during M2.13. |

---

## 🔧 Technical Debt

### Must clear before major feature work

| ID | P | Item | Milestone |
|---|---|---|---|
| DEBT-001 | **P0** partly cleared | ~~No automated tests anywhere (9,628 lines)~~ — 329 tests, and the codebase is now linted as of 2026-07-26: `iv_engine` at 100%, the journal P&L maths, a scanner golden net, token expiry, and **`db.py` at 100% statement coverage / 24-of-26 mutation score** (M1.5). **Still bare:** `collector.py`, `schwab_client.py`, and the rest of `app.py`. | M1 |
| DEBT-014 | P2 | Scanner golden fixtures do not exercise the bid/ask midpoint fallback | Found 2026-07-26 by mutation-testing the golden net: altering the midpoint formula inside `_compute_transform_scanner` changed nothing, so the net does **not** protect that branch during M2. Both captured snapshots do contain NULL-`mark` rows (77 of 3,096 in snapshot 2608), but none reach the top-50 output — the branch runs and its result is discarded. **Fix:** capture a third fixture chosen for NULL marks near the money, or add a synthetic chain. | M2 |
| DEBT-002 | **P0** | `app.py` is a 4,230-line procedural script (757 lines CSS, 257 top-level statements) | M2 |
| DEBT-003 | **P0** | Unbounded DB growth (~82 MB/trading day, ~20 GB/yr) | M3 |
| DEBT-004 | **P0** | `$5.00` transform threshold duplicated ×4 as literals (`app.py:1575, 3103, 3686, 3704`); not in `config.py` | M2.2 |
| DEBT-005 | **P0** | `DOCUMENTATION.md` drifted from code — see DEBT-010 | M2.14 |
| DEBT-006 | **P0** | `±5` wing offset hardcoded in `app.py:2559` **and** inside SQL in `db.py:815-900` | M2.2 |

### Clear during refactor

| ID | P | Item | Milestone |
|---|---|---|---|
| DEBT-007 | P1 | Silent exception swallowing in P&L display paths | M2.13 |

> **DEBT-007 detail (measured 2026-07-26, first real ruff run).** The estimate is now exact: **4 bare `except:`** (`pages/journal.py` 1298, 1507, 1546, and one more) and **~30 blind `except Exception:`** across `journal.py` (18), `app.py` (3), `collector.py` (3) and `db.py` (2). These swallow every error silently, so a failed P&L calculation renders as a blank cell indistinguishable from a real value — the same confusion as BUG-010. `ruff check --select BLE001,E722` reproduces the list with line numbers. Note `collector.py`'s and `db.py`'s are deliberate and now carry a comment saying so; `journal.py`'s are not.

| DEBT-008 | P1 (was P0) — **half fixed** | ~~The caller is told discarded rows were stored~~ — fixed 2026-07-26 (M1.5): `insert_option_rows()` now returns `cursor.rowcount` and logs a WARNING naming the shortfall. **Still open:** `OR IGNORE` continues to discard constraint-violating rows rather than raising, and the collector still records `strikes_fetched = len(option_rows)` (the offered count) rather than the stored one. Per-constraint behaviour is the M3.6 decision (ADR-022 step 2). | M3.6 |

> **DEBT-008 detail (raised to P0, 2026-07-26, M1.5 tests).** The severity in ADR-004 was understated. `OR IGNORE` is not scoped to uniqueness — SQLite applies it to *every* constraint on the statement, so a `CHECK` or `NOT NULL` violation skips the row rather than raising. `insert_option_rows()` compounds this by returning `len(rows)`, computed before the statement runs. **Combined failure mode:** if Schwab ever changed its `right` convention from `'C'` to `'CALL'`, every option row would be silently discarded, `insert_option_rows()` would return 3,096, and `collector.log` would record a full healthy cycle — indefinitely, until someone noticed the charts had stopped moving. Nothing in the system would say otherwise, and the missing data is unrecoverable (the broker will not sell you last Tuesday's prices). **Minimum fix, cheap and independent of the M3.6 refactor:** compare `cursor.rowcount` against `len(rows)` and log any shortfall as a warning. Pinned by `test_insert_option_rows_silently_discards_a_row_failing_the_check` and `test_insert_option_rows_keeps_the_good_rows_when_one_is_bad`.
| DEBT-009 | P1 | 757-line CSS embedded in `app.py`, targeting Streamlit-internal DOM classes | M2.7 |
| DEBT-010 | P1 | Doc drift: `transform_credit()`/`calendar_edge()` documented as core but never called; Entry Locks undocumented; doc stops at v4.1; §14.8 wrongly claims `chart_colors.json` is gitignored; Pinned Pairs/Pair Scanner documented but removed in v3.3 | M2.14 |
| DEBT-011 | P1 | Sidecar JSON (`eligible_history.json` 599 KB, `entry_locks.json`) has no schema, no validation, no atomic write, no backup | M2.6 |
| DEBT-012 | P1 | Duplicated helpers: `_safe_float` ×2, mark-fallback `(bid+ask)/2` ×**5**, expected-move calc ×2 | M2 |
| DEBT-013 | P1 | No logging in `app.py`/`journal.py` — loggers created, never called | M0.12 |
| DEBT-014 | P1 | ~6 dangling citations to the deleted June audit in `DOCUMENTATION.md` §3.1/§9.4/changelog and `iv_engine.py` lines 81-84, 110-111, 360-361 (ADR-011). Recovery SHA `6329fa28` | M2.14 |
| DEBT-015 | P2 | Dead code: ~13 functions + `demo_data.py` + `DEMO_MODE` + 3 legacy DB tables | M0.11 |
| DEBT-016 | P2 | Redundant indexes: `idx_option_rows_contract` (218 MB), `idx_option_rows_snapshot_id` (100 MB) | M0.10 |
| DEBT-017 | P2 | Code-generator artifact string at `db.py:1039-1045`; mid-file `import json as _json` | M0.11 |
| DEBT-018 | P2 | `_run_mission_control` ~170 ln; Calendar Edge tab body ~750 ln | M2.10 |

### Opportunistic

| ID | P | Item |
|---|---|---|
| DEBT-025 | P2 | **85 ruff findings remain after the first lint pass** | Config was written in M0 but ruff was never installed, so nothing had ever been linted. Installed 2026-07-26; noise families silenced, 55 mechanical items auto-fixed, **85 judgement calls left**: 18 PLR0917 (too many positional args — pervasive in `db.py` readers), 12 PLC0415 (imports not at top), 12 E741 (variables named `l`, in `journal.py`'s leg parsing), 8 SIM118, 6 SIM105, 4 E722 + 4 B905 + 3 PLW0603, 2 F841. None is a defect; the `except` ones belong to DEBT-007. Mostly lands naturally during the M2 decomposition. **Do not wire ruff into the pre-commit hook until this is at zero** — a gate that fails on every commit teaches everyone to bypass it. |
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
| OPS-005 | P1 | `collection_gaps` is populated but never surfaced anywhere in the UI. **Unblocked 2026-07-26** — BUG-005 is fixed and the historical rows reclassified (ADR-024), so the table is now trustworthy enough to render: 28 real faults rather than 47 false alarms. See OPS-008 before building the view. |
| OPS-008 | P2 | **Decide what to do with the ~22 session-change artefact rows in `collection_gaps`** | They record non-events: an ordinary 5-minute MIDDAY interval judged against the 60s CLOSE threshold at the 15:30 session change (ADR-024). The collector no longer creates them. The existing rows were deliberately left alone by `migrations/reclassify_collection_gaps.py` — deleting rows from an audit log is a judgement call, not a migration's business. Options: delete them, or add a distinct `reason` such as `CADENCE_ARTEFACT`. Wants deciding before OPS-005 renders the table. |
| OPS-006 | P2 | Confirm Streamlit binds localhost only before any Tailscale exposure (ADR-012). **Confirmed 2026-07-26: it does NOT** — launching `streamlit run app.py` advertises a Network URL and an External URL, so it binds 0.0.0.0 and is reachable from the LAN today. Needs `--server.address=127.0.0.1` (or the equivalent in `.streamlit/config.toml`) before any further exposure. |
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

<!-- No "Recently Completed" section here (ADR-017): progress_log.md, plan.md and git already record done work. -->

