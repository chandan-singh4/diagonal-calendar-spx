# Changelog

All notable changes to the SPX Diagonal Calendar Analyzer.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is
loosely semantic — this is a single-user local tool, so "major" tracks dashboard
generations rather than API compatibility.

Historical entries (v1.0–v4.2) were reconstructed on 2026-07-25 from
`DOCUMENTATION.md` §1 and `DEV_JOURNAL.md`. Before this file existed, version
numbers appeared only in commit messages and document changelogs — commit
`bfc78c0` is titled "V4.2 added" but contains no source changes at all.

---

## [Unreleased] — M0 Stabilization

### Added
- `AUDIT_2026-07-25.md` — full engineering audit and 9-milestone roadmap
- Persistent context files: `plan.md`, `progress_log.md`, `decisions.md`,
  `backlog.md`, `handoff.md`
- `decisions.md` seeded with 16 ADRs (12 backfilled from `DEV_JOURNAL.md`)
- `pyproject.toml` — dependencies pinned with **upper bounds**, dev extras,
  and ruff/black/mypy/pytest configuration
- `requirements.lock` — 63 packages fully pinned with provenance
- `.env.example` — was referenced by the README for weeks but never existed
- `.githooks/pre-commit` — blocks credentials, databases, logs, runtime state,
  files >1 MB, and NULL bytes in `.gitignore`
- `CHANGELOG.md` (this file)

### Changed
- **`.gitignore` rewritten as clean UTF-8/LF.** It had been corrupted with
  UTF-16LE bytes appended by PowerShell; the intended `pinned_pairs.json` entry
  was stored NULL-separated and matched nothing, so five runtime-state files
  were tracked for weeks.
- `collector.py` — log handler is now `RotatingFileHandler` (1 MB × 5) instead
  of an unbounded `FileHandler`; log path resolves relative to the module, not
  the process working directory
- `start_collector.bat` — paths resolved via `%~dp0` instead of hardcoded
  absolute paths; venv auto-discovery; no `pause` when `SPX_UNATTENDED=1`
- `README.md` — rewritten; it had described an "MVP scaffold" four versions out
  of date, referenced a nonexistent `.env.example`, and instructed users to
  toggle a Demo Mode that no longer exists
- `db.py` — module docstring corrected to list the functions that actually exist

### Removed
- **Database: 2 redundant indexes + 3 orphaned legacy tables.**
  `idx_option_rows_contract` and `idx_option_rows_snapshot_id` were each a strict
  left-prefix of another index. `expiry_snapshots`, `strike_snapshots`, and
  `positions` were pre-v2 leftovers. **1.810 GB → 1.423 GB (387 MB, 21.4%)**,
  plus ~3,000 fewer index writes per collection cycle. Rehearsed on a backup
  clone first; `EXPLAIN QUERY PLAN` and measured timings confirmed no regression.
- Dead code (all verified unreachable): `iv_regime`, `mean_reversion_estimate`
  + `ReversionEstimate`, `trade_quality_score`, `expected_move_log_check`
  + `ExpectedMoveCheck`, `get_term_structure`, `get_iv_spread_history`,
  `get_snapshots`, `get_all_expiry_atm_iv_today`, `update_snapshot_notes`
- `demo_data.py` and `data/demo_dashboard.db` — orphaned; still wrote to the
  pre-v2 `strike_snapshots` schema
- `config.DEMO_MODE` / `config.DEMO_DB_PATH` — zero consumers
- Code-generator artifact in `db.py` (a stray `"""APPEND THIS ENTIRE FILE TO THE
  BOTTOM OF db.py"""` string literal committed as if it were a docstring)
- Untracked from git (kept on disk): `collector.log`, `eligible_history.json`,
  `entry_locks.json`, `chart_colors.json`, `data/demo_dashboard.db`, `.docx`
- Deleted: `dashboard.db` (0-byte root orphan), `pinned_pairs.json` (feature
  removed in v3.3), `AUDIT_REPORT_2026-06-25.md`, `spx_dashboard_implementation_plan.md`

### Fixed
- `seed_t001` would have raised `NameError` — the `json` import was removed
  alongside the adjacent code-generator artifact. Import hoisted to module top
  and `_json` alias dropped. (Caught by a cross-module reference check; this is
  precisely the class of defect the M1 test suite exists to catch automatically.)

### Notes
- **Retained deliberately:** `transform_credit()` and `calendar_edge()` are
  currently uncalled, but they are the M2.2 extraction targets — `app.py`
  duplicates their logic inline. Wiring `app.py` to call them removes the four
  duplicated copies of the `$5.00` threshold and both hardcoded `±5` wing
  offsets. `get_gaps()` is retained for backlog OPS-005.
- No trading math, collector logic, scanner ranking, or P&L rule was changed.

---

## [4.2] — 2026-07-10
Commit `bfc78c0`. Journal and generated-state updates only; **no source changes.**
Removed `trade_forensics_2026_06_23.ipynb` (1,397 lines) and its PNG.

## [4.1.1] — 2026-07-07
### Fixed
- **Multi-day chart rendering corruption.** Root cause: any *per-date* Plotly
  rangebreak corrupts point positioning for all data rendered after it (ghost
  lines, out-of-order hover, dead tooltips). Only weekday-name and hour-pattern
  bounds are safe. Market holidays now render as an honest one-session gap.
- Reinstated `_break_sessions()` NaN line-breaker for gaps > 60 minutes
- All timestamps reaching Plotly are now naive ET wall-clock
- Infinite rerun loop in `_live_refresh_poller` on fresh sessions
- `dedupe_option_rows.py` — chunked, resumable one-time dedupe migration

## [4.1] — 2026-07-03
### Added
- Reusable chart-card system; `_render_note()` callouts; always-on gap fill
- **Strike Channel** subplot — SPX through a strike band with crossing markers
### Changed
- `init_db` moved behind `@st.cache_resource` (was committing a write on every
  rerun, contending with the collector's lock and causing Ctrl+C hangs)
- Read-only non-committing connections (`PRAGMA query_only = ON`); 20 readers repointed
- Six snapshot-keyed cached loaders; `max_entries` + `ttl` on all 12 caches
- Blind `st_autorefresh` replaced by a change-triggered `st.fragment` poller
### Fixed
- **Sawtooth artifact / far-OTM slowness.** `option_rows` had no uniqueness
  guarantee, so duplicates fanned out across six-leg joins. Added
  `UNIQUE(snapshot_id, expiry_date, strike, right)`, `GROUP BY s.snapshot_id`,
  and `INSERT OR IGNORE`.
- `delete_trade` was using the read-only context manager

## [4.0] — 2026-06-30
Premium redesign; custom session-state tab bar (6 tabs); **Mission Control**
cross-sectional scanner with two-phase architecture; non-ATM eligibility
registry; attention strip; `get_transform_mark_history`; chart color pickers;
Trade Journal P&L lifecycle fix (`resolved_pl()` as single source of truth).

## [3.3] — 2026-06-29
Transformation Opportunity Scanner; token expiry banner; collector-aware
refresh; design system v2. **Removed** Pinned Pairs and Pair Scanner.

## [3.2] — 2026-06-29
Entry Analysis replaces Transform Credit panel; IC risk-profile payoff chart;
`atm_straddle_price`, `normalized_debit`, `theta_differential`; weekend fallbacks.

## [3.1] — 2026-06-27
Trade Journal CRUD; guided two-step edit wizard; direct-close path; live IC P&L;
P&L terminology standardised.

## [3.0] — 2026-06-26
Layout polish; multi-day chart continuity via rangebreaks; stacked IV panel;
front-vs-back scatter; **Regime Analysis** sub-tab (the mechanism intended to
validate or refute the IV-ratio hypothesis).

## [2.0] — 2026-06-26
Pair Scanner; Pinned Pairs; GEX display; corrected SPX daily change; expiry and
strike detail panels.

## [1.1] — 2026-06-25
### Changed
- **Retracted the claim that IV ratio < 1.0 is "favorable."** It rested on a
  single paper trade; Black-Scholes analysis suggested the opposite. Demoted to
  an explicitly unvalidated `HYPOTHESIS` rather than inverted — see
  `decisions.md` ADR-001.
- Corrected inverted backwardation/contango terminology
- Corrected the transformation workflow (keep shorts, close backs, add
  front-expiry wings)
- "Risk-free" → "risk-reduced"
- Regime colors de-valenced
### Removed
- Theta ETA metric (ignored back-leg theta, vega, delta, gamma)

## [1.0] — 2026-06-25
Initial documented dashboard: IV structure, calendar edge, transform credit.

## [0.1] — 2026-06-21
Initial scaffold: Schwab OAuth (manual flow), snapshot-anchored SQLite schema,
collector daemon, Streamlit dashboard.
