# backlog.md — TODOs, Enhancements, Bugs, Technical Debt, Future Ideas

**Last updated:** 2026-07-25
**Source:** `AUDIT_2026-07-25.md` §7 + follow-ups scattered through `DEV_JOURNAL.md`

Priority: **P0** blocks the current milestone · **P1** next milestone · **P2** scheduled ·
**P3** someday

---

## 🐞 Bugs

| ID | P | Item | Notes |
|---|---|---|---|
| BUG-001 | **P0** | **Unresolved v4.1.1 report: user said "still having some issue" with the canonical `app.py`; session ended before specifics were captured** | Needs exact symptom, which view/tab, and a screenshot. Per the 2026-07-07 journal: do NOT assume it is the same rendering bug — verify from evidence. Confirm the canonical file actually replaced the local one (look for `app_backup.py`), confirm a clean restart, confirm collector running. |
| BUG-002 | P1 | Selected-strike IV chart may draw a connector across a holiday | `_break_sessions()` is wired into `_gap_df` and `atm_merged` but not the cm/pm frames. One-line fix. (ADR-006) |
| BUG-003 | P2 | Unsaved-changes guard false-positives on untouched forms | `st.form` only delivers values on submit, so the guard detects edit-mode *activation*, not field changes. Documented in `DOCUMENTATION.md` §12.8. Likely unfixable within Streamlit. |
| BUG-004 | P2 | `MARKET_HOLIDAYS` covers 2026 only | Will silently misclassify collection gaps from Jan 2027. Make multi-year or compute. |
| BUG-005 | **P1** | **`_classify_gap()` misclassifies every routine overnight/weekend gap as `COLLECTOR_OFFLINE`** | Discovered 2026-07-25. **All 46 rows in `collection_gaps` are `COLLECTOR_OFFLINE`; the classifier has never produced `MARKET_CLOSED` or `HOLIDAY`.** ≥19 are plainly routine (15:59 → 09:30 next morning). Root cause — two off-by-ones against the collector's own window at `collector.py:182-186`: `after_close = start.time() >= 16:00` but the last write of the day lands at 15:59; `before_open = end.time() < 09:30` but the collector restarts at 09:30–09:31. Both False ⇒ falls through to `COLLECTOR_OFFLINE`. No weekend awareness beyond a crude `>3600 min` heuristic. **Fix approach:** compute actual *market minutes* inside the gap window; classify `MARKET_CLOSED` when ≈0. **Blocks M3.4** (liveness alerting on a classifier that cries wolf 100% of the time is useless) and makes OPS-005 render 46 false alarms. Existing rows need a one-time reclassification. |

---

## 🔧 Technical Debt

### Must clear before major feature work

| ID | P | Item | Milestone |
|---|---|---|---|
| DEBT-001 | **P0** | No automated tests anywhere (9,628 lines) | M1 |
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
| OPS-001 | **P0** | **`register_collector_task.ps1` exists but was NEVER registered** — collection depends on manual start. A missed session is permanently lost data. |
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
