# SPX Diagonal Calendar Analyzer

A personal, local-only dashboard for timing SPX diagonal calendar entries, tracking IV term
structure, and managing the transformation into iron condors after profit lock-in.

This is an MVP scaffold. It will run, but you must (1) add your Schwab API credentials and
(2) let a couple of IV history snapshots accumulate before the percentile/mean-reversion
features have real data to work with.

---

## 1. Architecture (Plain English)

Think of this as three layers stacked on top of each other:

```
┌─────────────────────────────────────────────┐
│  SCHWAB API (external)                       │
│  Real options chain + quote data              │
└───────────────────┬───────────────────────────┘
                    │  schwab_client.py (OAuth + requests)
                    ▼
┌─────────────────────────────────────────────┐
│  BACKEND / ANALYTICS ENGINE (Python)          │
│  - db.py        → SQLite storage              │
│  - iv_engine.py → term structure, percentile,  │
│                   mean-reversion math          │
└───────────────────┬───────────────────────────┘
                    │  plain function calls
                    ▼
┌─────────────────────────────────────────────┐
│  DASHBOARD UI (Streamlit)                     │
│  app.py → chain viewer, IV charts, scorer      │
└─────────────────────────────────────────────┘
```

There is no separate "frontend server" and "backend server" for the MVP — Streamlit runs
the Python analytics code directly and renders the UI in the same process. That's the
right call for a single-user local tool: fewer moving parts, faster iteration, nothing to
deploy. A FastAPI backend only becomes worth the extra complexity if you later want a
React frontend or multiple consumers of the data (see Section 9, "Next Upgrades").

## 2. Why this stack

| Decision | Choice | Reasoning |
|---|---|---|
| Language | Python | Schwab's best-maintained community client (`schwab-py`) is Python. `numpy`/`scipy`/`pandas` make the IV math trivial. No reason to introduce Node.js for a single-user analytics tool. |
| UI framework | Streamlit (MVP) → Next.js/React (v2, optional) | Streamlit gets you a working, good-looking dashboard in one file with no HTML/CSS/JS knowledge required. You can have live charts and clickable strike selection today. Swap to React only once you outgrow Streamlit's interactivity ceiling (e.g., you want a custom drag-to-adjust payoff diagram). |
| Database | SQLite | Single user, single machine, no concurrent writers, zero setup (it's a file). Postgres adds an install + server process you don't need. Migrate to Postgres only if you ever run this on a server for multiple strategies/users simultaneously. |
| Real-time updates | Polling (5–15s) for MVP → Schwab Streamer API later | Polling is 20 lines of code and is plenty fast for human decision-making (you're not scalping ticks). Schwab does offer a websocket streaming endpoint, but it adds real complexity (persistent connection management, reconnect logic) that isn't worth it until polling actually feels slow. |

## 3. Build Plan (MVP → Advanced)

**Phase 0 — Plumbing (today)**
1. Register app on Schwab Developer Portal, get approved (takes 1-3 business days — do this first, it's the longest lead time item).
2. Get `schwab_client.py` authenticating and pulling one live SPX quote.
3. Confirm you can pull a full SPX option chain for two expirations.

**Phase 1 — Data capture**
4. Save snapshots of front/back ATM IV to SQLite on a timer (even just every few minutes during market hours).
5. Let this run for a few sessions so you have *some* history before trusting percentile calculations (3-6 months is the long-term target; you'll bootstrap with whatever you've got).

**Phase 2 — Core analytics**
6. Implement `iv_engine.py`: term structure (spread/ratio), percentile-vs-history, simple mean-reversion estimate.
7. Surface these in the Streamlit dashboard as numbers + a time-series chart.

**Phase 3 — Decision support**
8. Add the Trade Quality Score (0-100) combining IV edge + liquidity + theta advantage.
9. Add an alert/highlight when IV spread percentile crosses your threshold (e.g., >85th percentile).

**Phase 4 — Position tooling**
10. Add a simple position tracker: log your actual diagonal entries (strikes, expiries, debit paid).
11. Add the diagonal → iron condor transformation calculator: given current position + a target locked profit, show the adjusted strikes and resulting max loss/max profit.

**Phase 5 — Visualization (you said this can wait)**
12. Payoff diagrams (diagonal, and post-transformation iron condor) using Black-Scholes for the diagonal's pre-expiration value, exact intrinsic value for the condor.

I've scaffolded through Phase 2 below so you have something to run today, plus the file
structure for the rest so you know where things go.

## 4. Setup

```bash
cd spx-diagonal-dashboard
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your Schwab APP_KEY, APP_SECRET, CALLBACK_URL (see below)
```

### Getting Schwab API credentials
1. Create an account at https://developer.schwab.com (separate from your trading login).
2. Dashboard → Create App. Select the **Market Data Production** API product (that's all you need for this tool — you are not placing trades through the API).
3. Callback URL: use `https://127.0.0.1:8182` for local development (must match exactly what you put in `.env`).
4. Submit and wait for approval — typically 1-3 business days.
5. Once approved, copy the **App Key** and **App Secret** into `.env`.

### First run
**Demo Mode is on by default** — you can run the dashboard right now, before your
Schwab credentials are even wired up:
```bash
streamlit run app.py
```
This uses synthetic IV data (clearly labeled as such in the sidebar) so you can see
the actual chart, expirations panel, and historical stats working end to end.

When you're ready for live data, toggle "Demo Mode" off in the sidebar. The first
time, a browser window will open asking you to log into Schwab and authorize the
app. After that, `schwab_client.py` caches a token to `data/token.json` and refreshes
it automatically. You'll need to re-do the browser login about once a week (Schwab expires
refresh tokens after 7 days — this is a Schwab platform limit, not something we can avoid).

## 5. Dashboard UI Layout (wireframe, text description)

```
┌──────────────────────────────────────────────────────────────────┐
│  SPX  6,847.32  ▲ +0.4%        [Auto-refresh: ON   every 10s]     │
├──────────────────────────────────────────────────────────────────┤
│  EXPIRY SELECTOR                                                  │
│  Front: [6/26 ▾]      Back: [6/29 ▾]      DTE gap: 3 days         │
├───────────────────────────────┬────────────────────────────────────┤
│  IV TERM STRUCTURE             │  IV SPREAD HISTORY (6mo)            │
│  Front IV:  18.4%              │   [line chart: spread over time]   │
│  Back IV:   15.1%              │   Current: 92nd percentile          │
│  Spread:    3.3 pts            │   ████████████████████░░  ← gauge  │
│  Ratio:     1.22                                                    │
├───────────────────────────────┴────────────────────────────────────┤
│  TRADE QUALITY SCORE:  78 / 100                                    │
│  IV edge: 85   Liquidity: 90   Theta advantage: 60                 │
├──────────────────────────────────────────────────────────────────┤
│  OPTIONS CHAIN (front expiry, ATM ± 10 strikes)                    │
│  Strike │ Call Bid/Ask │ Call IV │ Vol │ OI │ Put Bid/Ask │ Put IV │
│  7500   │  ...         │  ...    │ ... │ ...│  ...        │  ...   │
│  [rows clickable to populate strike selector below]                │
├──────────────────────────────────────────────────────────────────┤
│  STRIKE SELECTOR (builds the diagonal)                             │
│  Call: Sell [7500] front / Buy [7500] back                         │
│  Put:  Sell [7400] front / Buy [7400] back                         │
│  Net debit (live): $9.05                                           │
├──────────────────────────────────────────────────────────────────┤
│  POSITION SIMULATOR (Phase 4)                                      │
│  [Before: diagonal P&L curve]  [After: iron condor P&L curve]      │
└──────────────────────────────────────────────────────────────────┘
```

## 6. Common Beginner Mistakes in This Type of System

- **Treating Schwab's IV field as ground truth without sanity-checking it.** Garbage in/out — if you build percentile stats on bad ticks (e.g. a stale quote with IV=0 because the option hasn't traded), your whole signal is noise. Filter out zero-volume / zero-bid quotes before storing.
- **Comparing IV across strikes without controlling for moneyness.** "Front IV vs back IV" only means something if you're comparing the *same* moneyness (e.g., both ATM). Comparing front 7500-strike IV to back 7400-strike IV mixes skew into your term-structure signal.
- **No history before trusting percentiles.** A "92nd percentile" claim based on 4 days of data is meaningless. Show a confidence/sample-size indicator until you have real history.
- **Polling too aggressively and getting rate-limited.** Schwab's API has rate limits; a tight 1-second poll loop across many strikes/expiries will get you throttled. Start at 10-15s intervals.
- **Hardcoding strikes/expiries.** SPX strikes step differently near vs far from spot, and weekly vs monthly expiries have different liquidity. Always pull the live chain rather than assuming a strike exists.
- **Conflating implied vol with realized/historical vol.** Mean-reversion logic needs to be explicit about which one it's modeling — they're different things and the engine in this scaffold only models *implied* vol contraction.
- **Storing secrets in code.** Keep `APP_KEY`/`APP_SECRET`/tokens in `.env` and `data/` only — both are gitignored in this scaffold.

## 7. Suggested Next Upgrades (post-MVP)

- Swap polling for Schwab's streaming (websocket) API once the data volume justifies it.
- Move from Streamlit to a Next.js/React frontend if you want a fully custom payoff-diagram UI with drag-to-adjust strikes — at that point a FastAPI backend (serving `iv_engine.py`'s functions as JSON endpoints) becomes worth the complexity, since React needs something to call.
- Add a backtest mode: replay your stored IV history against your entry rules to see how often the "high IV spread percentile" signal would have preceded profitable diagonals.
- Expand Trade Quality Score with realized win-rate per IV-percentile bucket once you have enough trade history logged.
- Add position alerts (desktop notification / SMS) when your profit-lock threshold is hit, rather than requiring you to watch the dashboard.

## 8. Development Journal

See `DEV_JOURNAL.md`. Add an entry every time you change something — what changed, why,
and what it affects. This is mandatory project hygiene per your own requirements, not optional.
