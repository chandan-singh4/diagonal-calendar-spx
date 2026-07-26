# SPX Diagonal Calendar Analyzer

A locally-hosted options analytics platform for timing **SPX diagonal calendar spread**
entries and their transformation into risk-reduced **iron condors**.

Single user. Runs on one machine. Not a trade-execution system — it surfaces
decision-relevant numbers; the trader makes the calls.

---

## What it does

Retail brokerage platforms show the current option chain but do not persist it. The signal
this strategy depends on is **temporal and cross-sectional**, not point-in-time. So the
core of this project is a historical store of the SPX chain, and the dashboard is a view
over it.

It answers two questions:

1. **Is now a reasonable time to enter a diagonal calendar spread?**
   IV term structure at the specific strikes you'd trade, tracked over time — not just ATM,
   not just today.
2. **Has an open position reached the point where it can be transformed into an iron condor?**
   The Transform Difference (short-leg premium minus protective-wing cost) tracked
   continuously against a threshold.

> **On trading signals:** the dashboard deliberately offers **no validated entry signal**.
> Metrics labelled `HYPOTHESIS` are displayed for observation only and are not wired into
> any composite score or automatic rule. See [`decisions.md`](decisions.md) ADR-001 for why
> the project's original central claim was retracted rather than inverted.

---

## Architecture

Two independent processes sharing one SQLite database:

```
   Charles Schwab API  (OAuth, 7-day refresh token)
            │
            ▼  schwab_client.py
   ┌──────────────────────┐
   │    collector.py      │   SOLE WRITER — headless daemon
   │  60s open/close      │   Polls the chain during market hours, writes
   │  300s midday         │   immutable snapshots. Never reads UI state.
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │  data/dashboard.db   │   SQLite (WAL)
   │  snapshots           │   one row per collection cycle
   │  option_rows         │   one row per contract per snapshot
   │  atm_iv_by_expiry    │   pre-aggregated ATM IV
   │  collection_gaps     │   audit log of missed windows
   │  trades              │   trade journal
   └──────────┬───────────┘
              ▼  db.py (read-only: PRAGMA query_only = ON)
   ┌──────────────────────┐
   │  app.py (Streamlit)  │   PURE READER — never writes market data
   │  + pages/journal.py  │   Analytics in iv_engine.py
   └──────────────────────┘
```

**The critical invariant:** `collector.py` is the only writer; the dashboard physically
cannot take a write lock (enforced at the connection level, not by convention). WAL mode
lets both run concurrently. Either process can be restarted without affecting the other.

| Module | Responsibility |
|---|---|
| `collector.py` | Polling daemon: session detection, gap classification, retry/backoff |
| `schwab_client.py` | Schwab API wrapper: OAuth, quotes, chain fetch, flattening |
| `db.py` | All SQL. No other module issues SQL directly. |
| `iv_engine.py` | Pure analytics — no I/O, no UI, no database |
| `app.py` | Streamlit dashboard (6 tabs) |
| `pages/journal.py` | Trade journal: CRUD, lifecycle, P&L |
| `config.py` | Central configuration from `.env` |

---

## Setup

### Requirements
- Python 3.13–3.14
- A Schwab developer account with an approved **Market Data Production** app
- Windows (the collector's scheduled-task tooling is Windows-specific; the rest is portable)

### 1. Install

```bash
git clone <repo-url>
cd spx-diagonal-dashboard

python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate

pip install -r requirements.lock        # exact pinned versions
pip install -e ".[dev]"                 # optional: test + lint tooling
```

### 2. Get Schwab credentials

1. Create an account at [developer.schwab.com](https://developer.schwab.com) — this is
   separate from your trading login.
2. Create an app and select the **Market Data Production** product. This tool only reads
   data; it never places orders.
3. Set the callback URL to `https://127.0.0.1:8182`.
4. Submit and wait for approval — typically 1–3 business days.

### 3. Configure

```bash
cp .env.example .env        # PowerShell: Copy-Item .env.example .env
```

Fill in `SCHWAB_APP_KEY` and `SCHWAB_APP_SECRET`. `.env` is gitignored and must never be
committed.

### 4. First authentication

```bash
python -c "import schwab_client; schwab_client.get_client()"
```

This prints a URL. Open it, log in, authorize, then paste the resulting redirect URL
(it will look broken — that's expected) back into the terminal. The token caches to
`data/token.json`.

> **Schwab expires refresh tokens every ~7 days.** Re-run this command when the dashboard
> shows the token-expiry banner. This is a Schwab platform limit, not something this code
> can avoid.

---

## Running

**Collector** (start this first — it's what builds your history):

```bash
python collector.py              # runs indefinitely; Ctrl+C to stop
python collector.py --once       # single cycle, for testing
```

It sleeps outside market hours and wakes at the open without a restart. It collects
09:30–16:00 ET on trading days only.

**Dashboard:**

```bash
streamlit run app.py
```

**Health check:**

```bash
python check_db.py               # snapshot counts, latest data, recorded gaps
```

> ⚠️ **A missed session is permanently lost data.** The collector currently depends on
> being started manually. `register_collector_task.ps1` exists but has not been
> registered — see [`backlog.md`](backlog.md) OPS-001.

> ℹ️ **On Windows, one collector shows as two `python.exe` processes.** The `.venv`
> launcher shim spawns the real interpreter as a child. Count pairs, not processes, and
> check `ExecutablePath` (not `CommandLine`) to tell them apart.

---

## Backups

The database holds **irreplaceable market history** — it cannot be re-fetched from Schwab.

Back up with SQLite's online backup API (safe against the running collector; a plain file
copy of a live WAL database can capture a torn state):

```python
import sqlite3
src = sqlite3.connect('file:data/dashboard.db?mode=ro', uri=True)
dst = sqlite3.connect(r'C:\path\to\backups\dashboard-YYYYMMDD.db')
src.backup(dst); dst.close(); src.close()
```

Verify any backup before trusting it: `PRAGMA integrity_check` plus a row-count comparison
against the source.

---

## Documentation

| File | Contents |
|---|---|
| [`DOCUMENTATION.md`](DOCUMENTATION.md) | Strategy, metric definitions, dashboard reference, data architecture |
| [`AUDIT_2026-07-25.md`](AUDIT_2026-07-25.md) | Engineering audit + roadmap |
| [`plan.md`](plan.md) | Current implementation plan |
| [`decisions.md`](decisions.md) | Engineering decision log (ADRs) |
| [`backlog.md`](backlog.md) | Bugs, technical debt, enhancements |
| [`progress_log.md`](progress_log.md) | Chronological development log |
| [`handoff.md`](handoff.md) | Latest session handoff |
| [`DEV_JOURNAL.md`](DEV_JOURNAL.md) | Detailed historical development journal |

> **Note:** `DOCUMENTATION.md` is the intended source of truth for strategy and metrics, but
> has known drift from the code (see `backlog.md` DEBT-010). Reconciliation is scheduled for
> M2. Where they disagree today, **the code is authoritative.**

---

## Project status

Currently in a structured modernization effort. Roadmap: `M0` cleanup → `M1` test
foundation → `M2` architecture refactor, then data hardening, API, and a dashboard
decision. See [`plan.md`](plan.md).

**Known limitations:**
- No automated test coverage yet (M1)
- Database grows ~82 MB per trading day with no retention policy yet (M3)
- Charts reset zoom/pan when new data arrives (a Streamlit rerun constraint; M5)

---

## Conventions

- **IV scale:** stored as decimals (`0.185`). Multiply ×100 at the load boundary — nowhere
  else. `iv_engine` functions always receive percentage form.
- **Premium units:** points, where `1.00 point = $100` per SPX contract.
- **Timestamps:** stored UTC; converted to `America/New_York` for display only.
- **Put/Call ordering:** put-left, call-right throughout the UI.
