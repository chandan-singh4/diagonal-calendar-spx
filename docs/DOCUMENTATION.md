# DOCUMENTATION.md
# SPX Diagonal Calendar Analyzer — Project Reference Manual

> **Canonical Authority (qualified):** This document is the intended source of truth
> for the SPX Diagonal Calendar Analyzer. **However, sections marked `HYPOTHESIS` are
> working assumptions awaiting validation and do NOT carry canonical authority until
> confirmed by data.** No claim may enter this document as fact using words like
> *confirmed, proven, favorable, optimal,* or *maximizes* unless it is either
> mathematically derived (with the derivation shown) or backed by a stated minimum
> sample size. In any conflict between this document, dashboard labels, source code,
> and DEV_JOURNAL.md, this document governs — except that a `HYPOTHESIS` block never
> overrides observed data.

---

## Table of Contents

1. [Document Change Log](#1-document-change-log)
2. [Project Overview](#2-project-overview)
3. [Strategy Documentation](#3-strategy-documentation)
   - 3.1 [Diagonal Calendar Spread](#31-diagonal-calendar-spread)
   - 3.2 [Transformation to Iron Condor](#32-transformation-to-iron-condor)
4. [Trading Concepts Reference](#4-trading-concepts-reference)
5. [Dashboard Reference](#5-dashboard-reference)
6. [Mathematical Definitions](#6-mathematical-definitions)
7. [Data Architecture Reference](#7-data-architecture-reference)
8. [Dashboard Design Philosophy](#8-dashboard-design-philosophy)
9. [Assumptions and Known Limitations](#9-assumptions-and-known-limitations)
10. [Future Roadmap](#10-future-roadmap)
11. [Dashboard v3 — Changes & New Analytics (detailed)](#11-dashboard-v3--changes--new-analytics-detailed)
12. [Trade Journal — v3.1 Reference](#12-trade-journal--v31-reference)
13. [Dashboard v3.2 — Entry Analysis, Layout Reorder, Normalized Metrics (detailed)](#13-dashboard-v32--entry-analysis-layout-reorder-normalized-metrics-detailed)
14. [Dashboard v4.0 — Premium Redesign, Mission Control, Journal P&L Lifecycle Fix](#14-dashboard-v40--premium-redesign-mission-control-journal-pl-lifecycle-fix)
15. [Dashboard v4.1 — Design Language, Strike Channel, Performance, Stabilization & Silent Refresh](#15-dashboard-v41--design-language-strike-channel-performance-stabilization--silent-refresh)

---

## 1. Document Change Log

| Version | Date | Author | Summary of Changes |
|---------|------|--------|--------------------|
| 1.0 | 2026-06-25 | Chandan Singh | Initial documentation through Dashboard v1 (IV Structure, Calendar Edge, Transform Credit panels). |
| 1.1 | 2026-06-25 | Chandan Singh | **Critical audit corrections.** (1) Retracted the claim that IV ratio < 1.0 is "favorable" / "maximizes transformation credit" — it rested on a single paper trade and Black-Scholes analysis suggests the reverse; demoted to an unvalidated `HYPOTHESIS` with neutral framing. (2) Fixed inverted/contango terminology to standard conventions. (3) Corrected the transformation workflow (keep shorts, close backs, add front-expiry wings). (4) Fixed expiry collection (20 expirations by count, not within 20 DTE). (5) Reframed 100-pt strike width as an example, not a rule. (6) Removed the Theta ETA metric (assumption-based). (7) "Risk-free" → "risk-reduced." (8) Flagged Greeks-sign, Trade-Quality-direction, liquidity-threshold, and IV-Index claims as unvalidated. (9) Added approved trade-logging schema to the roadmap as the validation mechanism. Authority statement softened to exclude HYPOTHESIS blocks. |
| 1.2 | 2026-06-26 | Chandan Singh | **Dashboard v2 — complete layout rewrite.** (1) Replaced single-pair static layout with a Pair Scanner showing all valid (front, back) expiry combinations from the current session, ranked by intraday Drop%. (2) Added Pinned Pairs persistent watchlist (`pinned_pairs.json`). (3) Added GEX display (Max \|Net GEX\| strike + call/put dominated) computed from `option_rows.gamma` — no schema change required. (4) SPX daily change corrected to use prior session's last COMPLETE snapshot (`get_prior_session_close`) rather than the first intraday snapshot. (5) Mini SPX intraday sparkline embedded in the header. (6) Expiry Detail + Strike Detail panel restored (ATM IV + tick-change per expiry; per-leg IV + mark price at selected strikes). (7) Calendar Edge moved above Historical Statistics — now immediately follows the IV chart. (8) Max Gap filter moved from Controls Row to the Pair Scanner filter row. (9) Three new DB read functions added: `get_prior_session_close`, `get_spx_intraday_today`, `get_all_expiry_atm_iv_today`. (10) Section 5 of this document fully rewritten for v2. |
| 1.3 | 2026-06-26 | Chandan Singh | **Dashboard v3 — layout polish + IV-ratio/level analytics.** Layout: (1) compaction CSS (tighter top padding + section rhythm); (2) removed the header sparkline; (3) moved the `pts ↔ %` toggle beside the change value; (4) DTE shown in both expiry dropdowns, e.g. `2026-06-29 (3D)`; (5) Expiry Detail shows date + DTE together; (6) the Today/5D/10D/20D selector moved to the right of the chart (shared across both charts). Multi-day charts: (7) non-trading time (overnight/weekend/holiday via `config.MARKET_HOLIDAYS`) collapsed with Plotly `rangebreaks` so multi-day lines are continuous, not diagonal ramps. New analytics (Calendar Edge, additive — the original dual-axis chart is **kept**): (8) a **stacked panel** (Front/Back IV on one axis + a regime-colored ratio line); (9) a **Front-vs-Back scatter** colored by time of day. New **Regime Analysis** sub-tab on the Trade Journal page: (10) a Front-vs-Back entry scatter split into four quadrants by **median level (√(F·B))** and **median ratio**, colored by realized transform credit, plus a stratified 2×2 cell-mean table — the test for whether IV Ratio adds outcome information beyond IV level. (11) New DB read helper `get_entry_iv_context` reconstructs entry-time term structure from snapshots (no schema change; works retroactively). See new Section 11 for detailed explanations and examples. |
| 1.4 | 2026-06-27 | Chandan Singh | **Dashboard v3.1 — Trade Journal CRUD, guided edit wizard, direct-close path, live IC P&L, P&L terminology standardisation.** Added Section 12 covering: trade lifecycle (Open/Transformed/Closed/Expired), `close_type` field, full `trades` table schema, P&L terminology definitions (Realized/Unrealized/Net), strategy statistics formulas, all CRUD operations, the two-step guided edit wizard, unsaved-changes protection, inspect-trade auto-navigation, live IC per-leg P&L, and new code surfaces in `pages/journal.py` and `db.py`. |
| 1.5 | 2026-06-29 | Chandan Singh | **Dashboard v3.2 — Entry Analysis overhaul, layout reorder, IC payoff chart, normalized metrics, Transform Order Mark, weekend fallbacks.** (1) "Transform Credit" panel replaced by "Entry Analysis" with six real metrics: Diagonal Mark (dual-format pts+$), ATM Straddle, Normalized Debit, Net Daily θ/contract, Transform Order Mark, Transform Difference (with progress indicator + green signal at ≥5). (2) Section order rewritten: Entry Analysis→Calendar Edge→Historical Stats→Strike Detail→Pinned Pairs→Scanner→Research scatter. (3) IC Risk Profile payoff-at-expiration chart added to journal.py (replaces P&L by Expiry Zone table). (4) Three new `iv_engine.py` functions: `atm_straddle_price`, `normalized_debit`, `theta_differential` (+ `ThetaDifferential` dataclass). (5) New `db.py` function: `get_diagonal_history` for research scatter. (6) Put/Call order swapped to Put-left, Call-right throughout. (7) Period radio moved into Calendar Edge section header; `atm_merged_90d` (fixed 90-day) used for Entry Analysis percentile. (8) Weekend/gap fallback added to `_load_atm_hist_fb` and `_load_contract_hist`: Today falls back to last available session. (9) Historical Stats enhanced: percentile rank + LOW/MID/HIGH label per column. (10) Calendar Edge expanders removed — stacked view and intraday scatter now inline. (11) x-axis pinned to 09:30–16:15 ET on Today view. Added Section 13. |
| 1.6 | 2026-06-29 | Chandan Singh | **Dashboard v3.3 — Transformation Opportunity Scanner, design system v2, layout cleanup.** (1) Transformation Opportunity Scanner: core new feature — batch version of Entry Analysis scanning all expiry pairs for a user-specified strike gap; put/call offset dropdowns inline above table; exact-match wing validation; nearest-common-strike resolution for main legs; bisect-based O(log n) mark cache replacing per-call DataFrame filters. (2) Token expiry banner: amber pulse at day 6, red flash at day 7+, inline re-auth command. (3) Collector-aware refresh: dashboard auto-detects OPEN (9:30–10:00) and CLOSE (3:30–4:00) sessions and switches to 60s to match collector. (4) Countdown timer anchored to collector's last DB write, not the browser session. (5) SPX change display simplified to static `+64.0 (0.87%)` — toggle removed. (6) Strike selectors changed from `number_input` to `selectbox` showing only strikes present in both expiries. (7) Design system v2: Inter + JetBrains Mono fonts; 2rem metric values; `hr { display:none }`; no decorative lines on headings; card hover lift; `config.toml` for base theme; page icon 📈. (8) Pinned Pairs and Pair Scanner removed. (9) Front/Back ATM IV duplicate metrics removed. (10) Period radio moved inline with Contango/Backwardation info. (11) SPX price font increased to 2.6rem. |
| 1.7 | 2026-06-30 | Chandan Singh | **Dashboard v4.0 — Premium redesign, Mission Control, Trade Journal P&L lifecycle fix.** (1) Design System v3.4: dark navy palette (`--bg: #060b12`), teal-green `#10d4a3`, `pulseGreen` glow animation on best-diff card. (2) Custom session-state tab navigation (6 tabs) replacing vertical scroll — enables programmatic tab switching from Mission Control cards. (3) Persistent Controls Bar above all tabs. (4) Mission Control: cross-sectional opportunity discovery replacing single-instrument chart inspection — Phase A (21-offset sweep, cached) + Phase B (history queries for candidate set only); non-ATM eligibility registry (`eligible_history.json`); opportunistic backfill from Calendar Edge; never-empty panel guarantee. (5) Attention Strip in header: Eligible / Approaching / New counts + best opportunity, visible on every tab. (6) New DB function `get_transform_mark_history`. (7) New chart: Diagonal vs. Transform Order Mark with green-shaded transform windows. (8) Chart Appearance sidebar: color pickers persisted to `chart_colors.json`. (9) Scanner row selection → "→ View Chart" one-click drill-down. (10) Trade Journal: `resolved_pl(t)` function as single source of truth for all P&L display; `ic_expiry_pnl_per_share` for IC assignment calculation; Mark Expired fully automated for transformed trades (manual input removed); P&L Lifecycle Breakdown panel in Expiration tab. See Section 14. |
| 1.8 | 2026-07-03 | Chandan Singh | **Dashboard v4.1 — Design language, Strike Channel, performance & stabilization, silent refresh.** (1) Reusable chart-card system (`st.container(key="chartcard_*")` + `div[class*="st-key-chartcard_"]` CSS), `_render_note()` explanation callouts, `.chart-cap` captions; Calendar Edge charts + Strike Channel wrapped in cards, plot backgrounds moved to card interior `#0c1421`; always-on Gap fill (`fill="tonexty"`) under the ≥-threshold shading. (2) **Strike Channel subplot** stacked under the Diagonal/Transform chart (shared `_gap_xaxis` + `_SYNC_MARGIN_L/R`): SPX line through a neutral strike band with ▲/▼ strike-crossing markers; reads cached `_gap_df`, no new query. (3) **Performance:** `init_db` moved behind `@st.cache_resource` (was committing a write every rerun → Ctrl+C hang); read-only non-committing connections (`_make_conn(read_only=True)`/`PRAGMA query_only=ON`, `get_conn` repointed for 20 read fns; `delete_trade` write-path bug fixed); six snapshot-keyed cached loaders; `max_entries`+`ttl` on all 12 caches. (4) **Data integrity / sawtooth root cause:** duplicate `option_rows` fan out across the six-leg history joins — fixed with `GROUP BY s.snapshot_id` **and** a one-time dedupe migration + `UNIQUE(snapshot_id, expiry_date, strike, right)` index + `INSERT OR IGNORE`. (5) Far-OTM slowness (same fan-out) resolved (~56 ms after fix); timing caption added. (6) **Silent refresh:** `st_autorefresh` replaced by a change-triggered `st.fragment` poller that reruns only on a new snapshot. (7) Gap-chart tooltip pinned to a single master template in fixed order (SPX, Diagonal, Transform, Gap). No trading-math/scanner/Mission-Control logic changed. See Section 15. |

---

## 2. Project Overview

### 2.1 What This Project Is

The **SPX Diagonal Calendar Analyzer** is a personal, locally-hosted options analytics dashboard for a single trader executing diagonal calendar spread strategies on the SPX index. It runs as a Streamlit web application on a local Windows machine, reads live options-chain data collected from the Charles Schwab Developer API, and displays analytics to support two decisions:

1. **Is now a reasonable time to enter a diagonal calendar spread on SPX?**
2. **Has an open position reached the point where it can be transformed into a risk-reduced Iron Condor?**

It is not a general-purpose screener, not a trade-execution system, and not a source of validated entry signals. It surfaces decision-relevant numbers; the trader makes the calls.

### 2.2 Why It Exists

Standard brokerage platforms show the current chain but do not track IV structure across expiries at the same strike over time, do not compute the theoretical transformation credit, and do not store per-strike IV history. This dashboard fills those gaps, inspired by the FLUX analytics product.

### 2.3 The Three Phases of the Strategy

**Phase 1 — Entry:** Opening a new diagonal. IV term-structure context informs the decision, but note: **no regime has been validated as favorable** (see §3.1 HYPOTHESIS). The structure metrics are context, not signals.

**Phase 2 — Monitoring:** Watching an open position. The Transform Credit Panel is the primary tool — it answers "how much profit is locked in if I transform right now?"

**Phase 3 — Transformation:** Converting the diagonal into an Iron Condor once a dollar-profit threshold is met. The dashboard shows when the threshold is crossed; the trader executes manually.

### 2.4 What "Risk-Reduced Transformation" Means

> **Terminology change in v1.1:** the project previously used "risk-free." That overstated certainty. We now use **"risk-reduced."**

A transformation is **risk-reduced** when the realized/locked value at transformation meets or exceeds the resulting Iron Condor's maximum theoretical loss, **assuming fills at or near the modeled prices.** If that condition holds, the combined position cannot finish below break-even regardless of where SPX settles — *in theory*.

It is not literally risk-free because live fills across multiple legs incur slippage, so the realized credit is typically less than the modeled credit. "Risk-reduced" names the target condition, not a guarantee. See §9 for the slippage assumptions that make this distinction matter.

---

## 3. Strategy Documentation

### 3.1 Diagonal Calendar Spread

#### Structure

Four contracts on SPX, two expiries, two strikes:

| Leg | Action | Strike | Expiry |
|-----|--------|--------|--------|
| Short Call | Sell | Call Strike (OTM) | Front (near-term) |
| Short Put | Sell | Put Strike (OTM) | Front (near-term) |
| Long Call | Buy | Call Strike (same) | Back (far-term) |
| Long Put | Buy | Put Strike (same) | Back (far-term) |

The call strike is identical on the front and back legs; likewise the put strike. This is the specific structure this strategy uses.

#### Example

SPX at 7,478. Front June 26 (2 DTE), back June 29 (5 DTE), call strike 7,500, put strike 7,400.

- Sell June 26 7500C, sell June 26 7400P (collect premium — short legs)
- Buy June 29 7500C, buy June 29 7400P (pay premium — long legs)

**Net debit = (back legs paid) − (front legs collected).** All values are in option premium points where 1.00 point = $100 of real money per SPX contract.

#### Long Legs vs Short Legs

**Short (front) legs:** options you sold. They generate positive theta (decay in your favor). Buying them back to close costs the **ask**.

**Long (back) legs:** options you bought. They cap the risk of the short legs and gain from favorable movement or back-month vega. Their current value is taken at the **mark** (mid).

#### Expiration Selection

- Front: near-term, typically 1–5 DTE (rapid short-leg decay).
- Back: chosen for the structure you want, typically a few days to a couple of weeks beyond the front.

The collector loads **exactly 20 expirations by count** from the nearest outward (see "Collection scope" below and §7).

#### Collection Scope (implementation fact)

> **Corrected in v1.1.** The collector loads **exactly 20 expirations by count**, starting from the nearest, **regardless of DTE.** In current SPX conditions this reaches roughly **35–50 DTE.** Any earlier reference to "all expirations within 20 calendar days" is obsolete and incorrect. Configuration is by expiration count, not a DTE ceiling.

#### Strike Selection

> **Clarified in v1.1.** Strike selection is **discretionary and condition-dependent.** There is no fixed spread width. The trader chooses strikes based on the current expected move, the IV environment, where they want the short strikes relative to spot, and risk tolerance. Any specific width (for example, 100 points between the call and put strikes) appearing in this document is an **illustrative example only, not a strategy rule.** The dashboard's default strikes (call at the nearest 5-pt increment above spot, put at spot − 100) are convenience defaults, not recommendations.

#### Greeks Exposure

> **Note added in v1.1:** Net Greek signs depend on the IV regime, the chosen strikes, and DTE. The signs below describe a *typical* near-the-money, near-dated configuration and are **not invariant properties** of the structure.

| Greek | Typical net position | Meaning |
|-------|---------------------|---------|
| Delta | Near-neutral | Market-neutral by design (call/put deltas largely offset) |
| Theta | Typically net positive | Time tends to work for the position |
| Vega | Regime-dependent | Sign depends on front-vs-back IV and strikes |
| Gamma | Typically net slightly negative | Large fast moves tend to hurt |

#### IV Term Structure — `HYPOTHESIS` (favorability not validated)

> **This block is a HYPOTHESIS, not ground truth.**
>
> `IV_Ratio = Front_IV / Back_IV`, computed per strike and side.
>
> **Standard terminology (corrected in v1.1):**
> - Ratio **> 1.0** → front IV above back IV → **backwardation / inverted** term structure (common around near-term events).
> - Ratio **< 1.0** → front IV below back IV → **contango / normal** term structure.
> - Ratio **≈ 1.0** → flat.
>
> **Favorability is an open question.** Black-Scholes analysis (audit 2026-06-25) indicates that harvesting transform credit structurally favors **higher front IV relative to back (ratio > 1.0)**, because the short front legs then carry more extrinsic value to decay. This is the **opposite** of v1.0's original claim, which was based on a single paper trade and is now **retracted.**
>
> Neither direction is established. The earlier evidence (the 2026-06-23 paper trade, profitable at call/put ratios ≈ 0.85/0.82) is **Category D — anecdotal, one trade.** A diagonal can profit from direction or back-leg vega independent of the term-structure regime, so that trade does not isolate the regime as the cause, nor does it show the opposite regime would have done worse.
>
> **Dashboard treatment:** the ratio and regime label are shown as **neutral context** (descriptive labels FRONT-ELEVATED / FLAT / BACK-ELEVATED, non-valenced colors). They are **not** buy/avoid signals. Do not treat any ratio threshold as a trigger until the validation mechanism in §10.1 has produced a sufficient sample.

---

### 3.2 Transformation to Iron Condor

> **Corrected in v1.1.** The v1.0 workflow ("close the short front legs first") was wrong. The actual workflow is below.

#### The Workflow (plain language)

1. **Keep the short front legs in place.** The front short call and short put stay open — they become the short strikes (the body) of the Iron Condor.
2. **Close the back-dated long legs.** Sell to close the back-month long call and long put, realizing their current value.
3. **Buy protective wings in the front expiration.** Buy a further-OTM call above the short call, and a further-OTM put below the short put — both in the **same (front) expiration** as the shorts. These define the maximum loss.

The result is a standard same-expiration Iron Condor: short strikes inherited from the original front legs, long wings just purchased.

#### Why Transform?

Closing the back legs banks their accumulated value. Adding defined-risk wings caps the downside. If the value banked (minus wing cost) meets or exceeds the Iron Condor's maximum loss, the position is risk-reduced (see §2.4).

#### Resulting Iron Condor Behavior

- **Max profit:** net credit retained
- **Max loss:** wing distance − net credit
- **Break-even:** defined by the short strikes and net credit

#### Why Realized Profit Can Reduce Downside to Near Zero

If the realized value from closing the back legs, net of wing cost, ≥ the Iron Condor's maximum loss, the combined position cannot finish below break-even regardless of where SPX settles — assuming modeled fills. This is the sense in which the position becomes risk-reduced.

#### Mathematical Example

- Entry debit: $9.00
- Back legs closed for: Call $8.10 + Put $6.15 = **$14.25** (realized)
- Front shorts kept; front-expiration wings bought for, say, **$1.00** total
- Net banked relative to entry ≈ $14.25 − $1.00 − $9.00 = **+$4.25** locked

If the resulting Iron Condor's max loss ≤ $4.25, the position is risk-reduced (cannot finish below break-even under modeled fills). Threshold logic uses the Transform Credit metric in §5 / §6.

---

## 4. Trading Concepts Reference

Definitions as used specifically within this project.

### Implied Volatility (IV)
Market's annualized estimate of future movement, back-solved from option prices via Black-Scholes. Stored as a decimal (0.185); displayed ×100 (18.5%). Higher IV = pricier options.

### IV Term Structure
The pattern of IV across expiries at the same strike. Measured here by the IV Ratio.

### Contango (Normal Term Structure)
**Ratio < 1.0**: front IV below back IV (curve slopes up with time). The common SPX state under calm conditions. **No favorability is asserted** (see §3.1 HYPOTHESIS).

### Backwardation (Inverted Term Structure)
**Ratio > 1.0**: front IV above back IV (curve slopes down with time). Typical around near-term events. **No favorability is asserted.**

> **Terminology correction (v1.1):** v1.0 used "inverted" for ratio < 1.0. That was backwards. Inverted/backwardation = near-term IV *higher* = ratio > 1.0.

### IV Ratio
`Front_IV / Back_IV`. Dimensionless. Primary term-structure metric. Direction of advantage is unvalidated (§3.1).

### IV Spread
`Front_IV − Back_IV`. Percentage points. Absolute counterpart to the ratio.

### Calendar Edge
Per-side IV differential: `Call_Edge = Front_Call_IV − Back_Call_IV`; `Put_Edge = Front_Put_IV − Back_Put_IV`. The word "edge" here is historical naming for the *differential* — it does **not** imply a validated trading edge.

### Delta (Δ)
Option price change per $1 SPX move. Diagonal is near-delta-neutral by design.

### Theta (Θ)
Option value lost per day from time decay. Positive for the short legs (good), negative for the long legs. Net typically positive.

### Vega (ν)
Option price change per 1 percentage-point IV change. Net vega is regime-dependent.

### Gamma (Γ)
Rate of change of delta. Net typically slightly negative.

### Extrinsic (Time) Value
`Option_Price − Intrinsic_Value`. The decaying part. The short front legs are the position's theta fuel; more front extrinsic = more to harvest.

### Intrinsic Value
In-the-money amount: `max(0, SPX − Strike)` for a call; `max(0, Strike − SPX)` for a put.

### DTE (Days to Expiration)
Calendar days to expiry.

### Net Theta Advantage
Net dollars/day from time decay across all legs. **Phase 3 — not yet implemented.** Shown as "Phase 3" placeholder in the dashboard.

### Transform Credit (a.k.a. Net Locked Profit)
The profit locked in if you transform now: `Back_Legs_Value − Close_Cost − Entry_Debit`. The correct profitability metric (see §6.5). Independent of the IV-regime hypothesis.

### Risk-Reduced Threshold
Minimum Transform Credit at which transformation is considered viable. **$5.00 paper** (current sidebar default); **~$6.50–$7.00 expected live** (slippage-adjusted; to be calibrated from real fills — §9.1).

### Transformation Score
**Rejected.** A 0–100 composite was demoted to "Do Not Build" (§8). Raw dollar Transform Credit is preferred.

### Strike Distance
`|SPX_Price − Strike|`, in points.

---

## 5. Dashboard Reference

> **Version note:** this section describes Dashboard v3.3 (current). The v1 layout (two-column with sidebar panels), v2, and v3.2 layouts are retired. The v3.3 layout is a single linear flow — all sections are full-width or use an explicit column split.

### 5.1 Header

| Element | Source | Notes |
|---------|--------|-------|
| SPX price | `snapshots.underlying_price` | Latest COMPLETE snapshot |
| Daily change (pts or %) | `current − get_prior_session_close()` | Previous session's last COMPLETE snapshot ≈ official close. Toggle between points and % with the pts↔% button. Falls back to first intraday snapshot if no prior-session data exists. |
| Mini sparkline | `get_spx_intraday_today()` | 60px Plotly chart, no axes, green/red line, embedded in the SPX price column. |
| VIX | `snapshots.vix_value` | |
| Max \|GEX\| Strike | Computed from `option_rows.gamma × option_rows.open_interest × 100 × SPX_price × ±1` | Aggregated by strike across all expirations in the chain. Shows the strike with largest absolute net GEX and whether it is call-dominated or put-dominated. Computed from the ±300pt strike window only. |
| Staleness dot | `(now − snapshot_timestamp).seconds` | 🟢 <10 min, 🟡 10–60 min, 🔴 >60 min. Text label removed — countdown timer communicates freshness instead. |
| Countdown timer | `poll_interval − snap_age_secs` | Ticks down in-browser every second; anchored to collector's last DB write, not the browser session. Shows "overdue" if collector is 1.5× interval late. |
| Refresh interval | Time-aware: 60s during OPEN (9:30–10:00) and CLOSE (3:30–4:00); 300s otherwise | Displayed in header. Event Mode sidebar toggle overrides to 60s manually. |

### 5.2 Controls Row

Four columns: **Front Expiry**, **Back Expiry**, **Put Strike**, **Call Strike**.

Column order is Put-left, Call-right throughout the dashboard. Put and Call Strike inputs are `st.selectbox` dropdowns showing only strikes present in **both** front and back expiry (intersection). Typing a non-existent strike is not possible — eliminates the phantom-strike bug from nearest-strike fallback silently producing wrong numbers. Defaults to nearest available strike to ATM−100 (put) and ATM (call).

> **Max Gap is not here.** It lives in the Pair Scanner filter row (§5.8). It is a scanner parameter, not a trade-setup parameter.

### 5.3 Period Radio

Range selector: **Today / 5D / 10D / 20D**. Rendered in the Calendar Edge section header (right-aligned, next to the "Calendar Edge" subheader). Controls the time window for the Calendar Edge ATM charts and the Selected-Strike IV chart (§5.6). Historical Statistics (§5.5) always displays all four windows independently and is not affected.

**Weekend / holiday fallback:** when Today (days=1) returns no data, both `_load_atm_hist_fb` and `_load_contract_hist` automatically retry with a 5-day window and trim to the most recent session date with data. Friday is shown on Saturday; Thursday is shown when Friday is a holiday.

**x-axis bounds:** when Today is selected, all time-series charts pin their x-axis to `[session_date 09:30, session_date 16:15]` ET, forcing the axis to start at open and fill dynamically. Prior-session data is never carried into the current-day view.

### 5.4 Expiry Detail + Strike Detail / Selected-Strike IV Chart

Two-column section (`[1, 3]` ratio).

**Left — Expiry Detail:**

For each of Front and Back expiry:
- ATM IV % (large, prominent)
- Tick change ↑/↓ vs previous snapshot, color-coded green/red

Source: `db.get_latest_atm_iv_snapshots(n=2)` → `atm_iv_by_expiry.atm_avg_iv × 100`.

**Left — Strike Detail:**

For each leg (Call, Put):

| Row | Formula | Source |
|-----|---------|--------|
| `IV → F x.xx% / B x.xx%` | `option_rows.iv × 100` for front and back | Front IV / Back IV at the selected strike |
| `Ratio x.xxxx` | `Front_IV / Back_IV` | Dimensionless |
| `Mark → F $x.xx / B $x.xx` | `StrikeContract.mark` (`option_rows.mark`, fallback `(bid+ask)/2`) | Dollar mid-price per contract |

**Right — Selected-Strike IV Chart:**

Dual-axis Plotly chart showing:
- Front IV % (solid green), Back IV % (solid blue) for the call strike — left Y axis
- Call IV Ratio F/B (solid red) — right Y axis
- Put legs as dotted lines of the same colors
- Blank until per-strike history exists in `option_rows` at those specific strikes

Source: `db.get_contract_iv_history()` over `period_days`.

### 5.5 Calendar Edge

Full-width section. The ATM IV chart — macro context for the overall term-structure shape, distinct from the specific-strike view in §5.4.

**Metric strip (4 columns):**
- ATM IV Ratio (F/B)
- Front ATM IV %
- Back ATM IV %
- IV Index (mean of per-expiry mean IVs — informational; see §9 limitation)

**Interpret-curve text box:** neutral description of the term-structure shape (backwardation / flat / contango) with no favorability claim.

**ATM IV chart:** `atm_iv_by_expiry.atm_avg_iv × 100` for front and back expiry over `period_days`, plus IV Ratio on the right axis.

**Day-change strip (2 columns):** `st.metric` for Front and Back ATM IV showing latest value and the per-snapshot tick change.

> **Known limitation:** ATM ratio is unreliable near EOD when a 0DTE expiry expires and the nearest-strike reference shifts. Prefer the per-strike view (§5.4) in the final hour.

### 5.6 Historical Statistics

Section 5 of the dashboard. Always shows **Today / 5 Days / 10 Days / 20 Days** — four columns, one per window. Not controlled by the period radio. Weekend/gap fallback (`_load_atm_hist_fb`) applies to all four columns including Today.

Each column shows:
- Range bar: `[low ──●── high]` where ● is the current position. Formula: `(current − low) / (high − low) × 100` (§6.10).
- **Current value** (numeric, e.g. `1.1362`).
- **Percentile rank** within the window's distribution (§6.8), e.g. `82nd pct`.
- **Context label**: `LOW` (< 25th pct) / `MID` (25–75th) / `HIGH` (> 75th pct). Non-valenced — describes position in range, makes no favorability claim.

Source: `db.get_atm_iv_history()` for front and back, merged on timestamp, ratio computed in-app.

### 5.7 Pinned Pairs

Persistent watchlist of specific (front, back) expiry pairs. Stored in `pinned_pairs.json` in the project root (`pinned_pairs.json` must be in `.gitignore`).

Displayed from the same scanner DataFrame as §5.8 — no additional DB query. Always shown regardless of the DTE/gap filters active in the Pair Scanner. If a pinned pair's expiry has lapsed, it is silently omitted from the table.

**Pin:** select rows in the Pair Scanner (§5.8) → click "Pin N New."  
**Unpin:** select rows in this table → click "Unpin N Selected."

### 5.8 Pair Scanner

All valid (front, back) expiry combinations from the current session's `atm_iv_by_expiry` data, computed via `_compute_pair_scanner(session_date)` which pivots the session's ATM IV rows into a (timestamp × expiry) matrix.

**Filter row:** Min DTE | Max DTE | **Max Gap (days)** | Rescan button.

- **Max Gap:** maximum calendar days between front and back expiry dates. Mon→Tue = 1 day. Fri→Mon = 3 days. Default 1.
- Rescan button forces a fresh re-read from the DB.

**Table columns:**

| Column | Definition |
|--------|-----------|
| Front | Front expiry date + DTE |
| Back | Back expiry date + DTE |
| Ratio | Current `Front_ATM_IV / Back_ATM_IV` for this session |
| Day Chg | Ratio change from first to last snapshot of the session |
| Drop% | `(current − session_high) / session_high × 100` ≤ 0 |
| Rise% | `(current − session_low) / session_low × 100` ≥ 0 |
| Chart | Unicode bar sparkline of the ratio series (▁▂▃▄▅▆▇█), 10 sampled points |

Default sort: Drop% ascending (biggest intraday drop first). Click any column header to re-sort client-side.

> **Session boundary:** `session_date = snap_ts_str[:10]` — the date of the latest snapshot, not the current UTC clock. This ensures the scanner populates after market hours without returning 0 rows.

### 5.9 Entry Analysis

Section 3 of the dashboard (immediately after Controls). Answers the decision question: *what is this position offering right now, and am I close to the transformation threshold?*

**`atm_merged_90d`** — computed before Entry Analysis using a fixed 90-day window, independent of the period selector. Used for IV Ratio Percentile so the percentile reflects long-run context regardless of chart zoom.

**Row 1 — position cost + theta (require strikes):**

| Metric | Formula | Notes |
|--------|---------|-------|
| Diagonal Mark | `(bc_call.mark + bc_put.mark) − (fc_call.mark + fc_put.mark)` | Dual display: `X.XX pts · $X,XXX`. Dollar = pts × 100. |
| ATM Straddle | `SPX × (front_iv/100) × √(2·front_dte / 365·π)` | Normalization denominator. See §6.12. |
| Normalized Debit | `Diagonal Mark / ATM Straddle` | 4 decimal places. HYPOTHESIS. See §6.13. |
| Net Daily θ / contract | `(−front_sum + back_sum) × 100` | `front_sum = fc_call.theta + fc_put.theta`. Theta breakdown in caption. HYPOTHESIS. See §6.14. |

**Row 2 — transform signal + market conditions:**

| Metric | Formula | Notes |
|--------|---------|-------|
| Transform Order Mark | `(bc_call.mark + bc_put.mark) − (fc_wing_call.mark + fc_wing_put.mark)` | Wings on **front expiry** at `call_strike+5` / `put_strike−5`. Dual display. HYPOTHESIS. See §6.15. |
| Transform Difference | `Transform Order Mark − Diagonal Mark` | = `fc_call.mark + fc_put.mark − fc_wing_call.mark − fc_wing_put.mark`. Signal threshold = 5.0. HYPOTHESIS. See §6.16. |
| IV Ratio Percentile | `percentile_rank(atm_merged_90d["iv_ratio"], ts_now.ratio)` | Fixed 90-day window. HYPOTHESIS (§9.4). |
| Liquidity (ATM) | `min(Vol/500,1)×50 + min(OI/2000,1)×50` | Thresholds unvalidated (§6.7). |

**Transform Difference visual states:**

- *Difference < 5.0:* amber Unicode block progress bar `████░░░░░░ 40%` + "X.XX pts until threshold" sub-caption.
- *Difference ≥ 5.0:* dark-green pill `✓ Transformation threshold reached · Ready to transform · +X.XX pts above threshold`.

**Research scatter** (§5.10) is placed at the very bottom of the page (Section 9) — not in Entry Analysis.

---

## 6. Mathematical Definitions

### 6.1 ATM IV
`S = nearest strike to spot`; `ATM_IV(expiry) = mean(IV(S,CALL,expiry), IV(S,PUT,expiry))`. Percentage form.

### 6.2 IV Ratio
`IV_Ratio = Front_IV / Back_IV`. Dimensionless.
Example: Front 16.2%, Back 19.1% → 0.848 → **BACK-ELEVATED (contango)**. (Favorability not asserted — §3.1.)

### 6.3 IV Spread
`IV_Spread = Front_IV − Back_IV`. Percentage points. Example: 16.2 − 19.1 = −2.9%.

### 6.4 Calendar Edge (per side)
`Call_Edge = Front_Call_IV − Back_Call_IV`; `Put_Edge = Front_Put_IV − Back_Put_IV`. Percentage points. "Edge" = differential, not validated advantage.

### 6.5 Transform Credit
```
Back_Legs_Value  = Back_Call_Mark + Back_Put_Mark
Close_Cost       = Front_Call_Ask + Front_Put_Ask
Diagonal_Mark    = Back_Legs_Value − Close_Cost
Transform_Credit = Diagonal_Mark − Entry_Debit
```
Variables: back marks = mid (fallback (bid+ask)/2); front asks = cost to buy back shorts; entry debit = user input. Units: premium points (1.00 = $100/contract).

Worked example: back 8.10 + 6.15 = 14.25; close 0.60 + 0.40 = 1.00; diagonal mark 13.25; credit 13.25 − 9.00 = **+4.25**.

> **Theta ETA formula REMOVED (v1.1).** No longer part of the project. See §5.5.

### 6.6 *(removed)* Theta ETA
Removed in v1.1. Reserved section number; do not reuse for an assumption-based metric.

### 6.7 Liquidity Score
```
Vol_Score = min(Volume/500, 1.0) × 50
OI_Score  = min(Open_Interest/2000, 1.0) × 50
Liquidity_Score = Vol_Score + OI_Score
```
Range 0–100. **Thresholds (500 / 2000) are initial estimates, not validated** against SPX liquidity or fill quality; revisit once trade data exists.

### 6.8 Percentile Rank
`(count of history < current) / total × 100`. Range 0–100.

### 6.9 Trade Quality Score
`Score = 0.45×IV_Edge_Pct + 0.30×Liquidity_Score + 0.25×Theta_Advantage`.
**Caveat (v1.1):** IV_Edge_Pct has no validated direction; Theta_Advantage is a placeholder. Treat the composite as non-authoritative (§5.10, §8.3).

### 6.10 Range Stats Position
`Position_Pct = (current − low) / (high − low) × 100`, clamped [0,100].

### 6.11 Expected Move (informational)
`EM_1SD = Spot × (ATM_IV/100) × √(DTE/365)`; `EM_2SD = 2 × EM_1SD`. Logged only, never gated.

### 6.12 ATM Straddle Price
```
ATM_Straddle = SPX × (atm_iv_pct / 100) × √(2 × dte / (365 × π))
```
`atm_iv_pct` in percentage form (e.g. 18.5); `dte` in calendar days. Simplified Black-Scholes ATM straddle approximation. Equals the market's implied ±1σ move expressed in dollar terms. Used as the normalization denominator for Normalized Debit. Returns None if any input is ≤ 0.

### 6.13 Normalized Debit
```
Normalized_Debit = Diagonal_Mark / ATM_Straddle_Price
```
`Diagonal_Mark = (bc_call.mark + bc_put.mark) − (fc_call.mark + fc_put.mark)`.  
Removes SPX price-level drift and vol-regime shift — a $12 debit at SPX 5500 and a $12 debit at SPX 7500 represent different exposure fractions; Normalized Debit expresses both as a fraction of the expected move. Typical range: 0.08–0.14 for SPX diagonals.  
**HYPOTHESIS — not yet validated as a predictor of transform profit.**

### 6.14 Theta Differential (Net Daily Position Theta)
```
front_sum = fc_call.theta + fc_put.theta          (raw chain theta — negative)
back_sum  = bc_call.theta + bc_put.theta           (raw chain theta — negative)
Net_Daily_Theta   = (−front_sum) + back_sum        (per share per day)
Net_Daily_Theta_$ = Net_Daily_Theta × 100          (per contract per day)
```
Convention: raw chain theta is always negative (the option loses value per day against its holder). For the diagonal, front legs are short (their decay earns us −front_sum > 0) and back legs are long (their decay costs us +back_sum < 0). Positive net = position gains time value each calendar day.  
**HYPOTHESIS — whether magnitude at entry predicts transform speed or profit has not been validated.**

### 6.15 Transform Order Mark
```
Transform_Order_Mark = (bc_call.mark + bc_put.mark) − (fc_wing_call.mark + fc_wing_put.mark)
```
Where:
- `fc_wing_call` = front-expiry call at `call_strike + 5`
- `fc_wing_put`  = front-expiry put  at `put_strike − 5`

Represents the net credit value of the transformation order: credit from closing back legs (Sell to Close) minus cost of buying front-expiry protective wings (Buy to Open). Both wing legs are on the **front expiry** — matching the actual transformation order that is placed alongside the diagonal entry.  
Dual display: `X.XX pts · $X,XXX` (pts × 100).  
**HYPOTHESIS — signal threshold and favorability not yet validated.**

### 6.16 Transform Difference
```
Transform_Difference = Transform_Order_Mark − Diagonal_Mark
                     = fc_call.mark + fc_put.mark − fc_wing_call.mark − fc_wing_put.mark
```
The algebraic simplification shows this equals the short front legs' current premium minus the wing cost. Economically: how much more the position's existing short legs are worth relative to the wings needed to cap the risk.

**Signal rule:** `Transform_Difference ≥ 5.0` → green "Transformation threshold reached" state.  
**Threshold = 5.0** is a working assumption (matching the §6.5 Transform Credit framework). Calibrate from live fills once 10+ transformations are completed.  
**HYPOTHESIS — threshold magnitude not yet validated against actual fill economics.**

---

## 7. Data Architecture Reference

### 7.1 System Architecture

```
Charles Schwab API
   │  schwab_client.py (OAuth, chain, quote)
   ▼
collector.py  (background; 5-min / 60-sec polling; writes only)
   │  db.py (writes)
   ▼
dashboard.db (SQLite, local)
   │  db.py (reads)
   ▼
app.py (Streamlit; pure reader; analytics in iv_engine.py)
```

**Critical rule:** `app.py` never writes; `collector.py` never reads UI state. Full independence.

### 7.2 Database Tables

**`snapshots`** — one row per collection cycle: `snapshot_id` (PK), `snapshot_timestamp` (UTC ISO8601), `underlying_price`, `vix_value`.

**`option_rows`** — one row per contract per snapshot: `snapshot_id` (FK), `expiry_date`, `strike`, `right` ('C'/'P'), `bid`, `ask`, `mark`, `iv` (**decimal** — ×100 for display), `volume`, `open_interest`, `delta`, **`gamma`** (stored; used for GEX computation in app.py), `theta`, `vega`, `dte`, `time_value`, `intrinsic_value`.
Critical index: `idx_option_rows_contract_snap` on `(expiry_date, strike, right, snapshot_id)`.

**`atm_iv_by_expiry`** — `snapshot_id` (FK), `expiry_date`, `atm_call_iv`, `atm_put_iv`, `atm_avg_iv` (all **decimal**).

**`collection_gaps`** — `gap_start`, `gap_end`, `gap_seconds`, `reason`.

**`pinned_pairs.json`** — not a DB table; a JSON file in the project root managed by `app.py`. Format: `[{"front_expiry": "YYYY-MM-DD", "back_expiry": "YYYY-MM-DD"}, ...]`. Must be in `.gitignore`.

> **Planned (v1.1):** a `trades` table for the validation mechanism — see §10.1.

**Retention (added M3, 2026-08-09 — ADR-044).** `option_rows` is the only prunable table:
per-strike rows are deletable **90 days after their expiry date** (`config.RETENTION_DAYS`).
`snapshots`, `atm_iv_by_expiry` and `collection_gaps` are kept **forever** — measured at 5.3 MB
for the first 47 days, 0.26% of the database. **Expiries used by a trade are exempt at any age**
(`db.get_protected_expiries`). **Nothing reads `RETENTION_DAYS` on a schedule**: pruning happens
only when `python scripts/prune.py` is run by hand, and that script reports unless given
`--execute`, which then requires a fresh backup and the row count typed in figures.
Measured with `dbstat`: `option_rows` is 1,399.6 MB of table plus 636.4 MB of indexes on it, so
**a deleted row reclaims ~2.2× its own bytes** — but only after a separate manual `VACUUM`,
which the script prints rather than runs (it needs exclusive access and free disk equal to the
database). **Note the timeline:** collection began 2026-06-23, so a 90-day rule reaches nothing
until roughly November 2026.

**IV scale rule:** every IV column is stored as a decimal. `app.py` multiplies ×100 at the load boundary; `iv_engine.py` functions always receive percentage-form IV.

**New read functions added in v1.2:**
- `get_prior_session_close(db_path, session_date)` — last COMPLETE snapshot price before `session_date`; used for SPX daily change.
- `get_spx_intraday_today(db_path, session_date)` — intraday SPX price series for the current session.
- `get_all_expiry_atm_iv_today(db_path, session_date)` — ATM IV for all expiries across the session; powers the Pair Scanner pivot.

All three use `session_date = snap_ts_str[:10]` (date of latest snapshot) rather than `date('now')`, so they return data regardless of when the dashboard is opened.

### 7.3 Data Lineage Examples

**Transform Credit value:** Schwab chain → `collector.py` parses bid/ask/mark → `option_rows` → `db.get_option_chain()` → `app.py` (iv ×100; mark as-is) → `iv_engine.transform_credit()` → panel.

**IV Structure regime badge:** `option_rows.iv` (decimal) → `app.py` ×100 → `iv_engine.strike_contract()` front & back → ratio → `_neutral_regime(ratio)` → neutral label + non-valenced color.

---

## 8. Dashboard Design Philosophy

### 8.1 Decision Quality Over Information Quantity
Every metric must serve one of the two decisions (§2.1) or be validated context. Information for its own sake is a trading risk, not a feature. (IV Index is currently on probation under this rule — §5.6.)

### 8.2 Why Metrics Were Selected
- **IV Ratio (per-strike):** shows term-structure *shape*. Shown as context; favorability unvalidated (§3.1).
- **Transform Credit (not diagonal mark):** profit, not position value — the number that determines transformation viability.
- **Per-strike, not just ATM:** your actual legs' IV drives premium; ATM is macro context.
- **Separate call/put sides:** a double structure can be asymmetric.
- **30-min sparklines:** trend, not just snapshot.

### 8.3 Why Metrics Were Rejected
- **Composite "Magic Score":** obscures which dimension drives the value. Raw numbers preferred. Trade Quality Score is retained only as labeled, non-authoritative context.
- **Automatic event detection:** fires after a spike starts; manual anticipatory Event Mode is faster.
- **Theta ETA (removed v1.1):** built on assumptions (ignored back-leg theta, vega, delta, gamma); inconsistent with the data-over-guesswork principle.
- **Regime favorability coloring (removed v1.1):** green/red good-bad encoding implied a validated edge that does not exist.

### 8.4 Must Have / Nice To Have / Do Not Build
**Must Have (built in v1–v2):** per-strike IV ratio with neutral regime label; selected-strike IV chart; ATM Calendar Edge chart; Expiry Detail + Strike Detail panel (ATM IV per expiry + per-leg IV and mark price); Pair Scanner (all valid front/back pairs from current session, intraday Drop%/Rise%/sparkline); Pinned Pairs watchlist; GEX (max |net GEX| strike + dominance); SPX daily change vs prior session close; mini intraday SPX sparkline; Historical range stats (Today/5D/10D/20D); Transform Credit scaffold.
**Must Have (planned — v3):** Net Theta Advantage ($/day, Phase 3); proper time-to-viability (Phase 3); `trades` logging + favorability validation (§10.1).
**Nice To Have:** payoff diagrams; IV percentile with adequate history; mean-reversion estimate (in engine, not surfaced).
**Do Not Build:** composite Transformation Score; automatic event triggering; SaaS/multi-user; in-dashboard execution; **valenced regime coloring until favorability is validated.**

---

## 9. Assumptions and Known Limitations

### 9.1 Paper-Trade Fill Assumptions
Thresholds were set under paper trading (fills at mid). Live fills cross the spread. Estimated slippage across four legs: **$2–$4 total**. Implication: live Transform Threshold should be ~**$6.50–$7.00**. Calibrate from the first 5–10 live transformations; until then use $6.50 as a conservative start.

### 9.2 Mid-Price Mark Assumption
When `mark` is null, fallback `(bid+ask)/2`. Mid overstates exit value on illiquid legs. The Transform Credit deliberately uses `ask` for front-leg close cost to avoid this bias on the closing side; back-leg marks remain mid and may slightly overstate exit value.

### 9.3 IV Accuracy
IV comes from Schwab; may be stale on low-volume strikes and erratic near EOD for expiring contracts. Collector filters zero-bid options, but stale IV is still possible.

### 9.4 Regime Favorability — UNVALIDATED
> The single most important caveat. The direction of advantage in IV term structure is **not established** (§3.1). Black-Scholes analysis suggests front-elevated (ratio > 1.0) may be structurally better for harvesting transform credit, but a handful of modeled scenarios with assumed IV paths is **not** sufficient to install that as a rule either. **Status: unknown, pending trade data.** Do not trade the regime as if its sign were known.

### 9.5 ATM Ratio Near EOD
Unreliable when a 0DTE expiry nears expiration; prefer per-strike IV in the final hour.

### 9.6 Historical Percentile Reliability
`sample_size_warning()` fires below 200 observations (~2–3 trading days at 5-min polling). Full reliability needs 3–6 months.

### 9.7 Collector Independence & Token Expiry
Dashboard shows last-collected data if the collector stops (staleness turns yellow/red). Schwab refresh tokens expire ~weekly; the first login uses the manual OAuth flow, then auto-refresh until expiry.

### 9.8 Collector Liveness Monitoring — `scripts/watchdog.py` (M3.4, ADR-045)

**Why it is not part of the dashboard.** The red `TOKEN EXPIRED` banner (`ui/header.py::render_token_banner`) was working correctly on 2026-08-09 while the collector recorded nothing all weekend. Nobody had the page open. **An alarm reachable only through a page you have to open is not an alarm** — hence a separate process on a separate schedule.

**What it does and, as importantly, does not.** Every 10 minutes, all day, every day, Windows Task Scheduler runs `watchdog.py`. It reads the newest `snapshots` timestamp, compares it against what `core.session` says should be arriving, and on a problem raises a desktop notification (PowerShell toast, `ctypes` `MessageBox` fallback) and an email. It **never** writes to the database, restarts the collector, or re-authenticates. Recovery belongs to M8; this component's only job is to be believable when everything else is suspect.

**Anti-cry-wolf controls** (`config.py`), all four of which correspond to a way a naive version fires when nothing is wrong:

| Setting | Default | Prevents |
|---|---|---|
| `core.session.session_of` → `None` | — | Alarms overnight, at weekends, on holidays |
| `WATCHDOG_OPEN_GRACE_MINUTES` | 5 | Alarms at 09:31, when the newest price is legitimately yesterday's close |
| `WATCHDOG_LATE_MULTIPLE` | 2.5 | Alarms in the moment before every scheduled poll, when the age legitimately *reaches* the interval |
| `WATCHDOG_REALERT_MINUTES` | 60 | Six emails an hour for one outage |

**`informative` — the false-all-clear guard.** `_result(..., informative=False)` marks the two states in which the watchdog **cannot see**: market shut, and inside the opening grace period. Without it, 16:00 turns a collector that has been dead all afternoon into `ok`, and the state transition fires **"RECOVERED: prices are arriving again."** A false all-clear is the one message that stops you checking. `should_alert` returns `"no news"` for uninformative results, and `main` carries the previous `alarming` flag forward unchanged, so **an all-clear requires positively observing fresh data**. Covered by five tests in `tests/test_watchdog.py`, two of which tie `informative` to the real `check()` path so the rest cannot pass vacuously.

**A negative age is an alarm, not a reassurance.** A snapshot timestamped in the future means the clock is wrong, and every verdict here is a comparison against a clock — so a wrong clock invalidates "collecting normally" just as much as it invalidates the alarms. `age < -60` is its own alarm branch.

**Exit codes** (visible as `LastTaskResult`): `0` all well *or* market shut · `1` a problem was found **and reported** — the watchdog working, not failing · `2` the check itself could not run.

**Configuration.** `ALERT_EMAIL_TO / ALERT_EMAIL_FROM / ALERT_SMTP_HOST / ALERT_SMTP_PORT / ALERT_SMTP_PASSWORD` are read from the environment via `.env` (gitignored); nothing is stored in the repository. Leaving them all blank skips email silently — a legitimate choice. Setting *some* of them is refused loudly, because believing you are covered when you are not is the exact failure this component exists to prevent. Gmail requires an App Password.

**State.** `data/watchdog_state.json` (gitignored) holds "have I already complained about this?" — the alarm flag and the last-alert time.

**Manual use.** `python scripts/watchdog.py --dry-run` prints the verdict and sends nothing; `--test-alert` forces both channels so the delivery path can be proven without an outage. Registration: `powershell -ExecutionPolicy Bypass -File .\scripts\register_watchdog_task.ps1` (no elevation needed — a repeating **time** trigger, unlike `ONLOGON`, does not require it).

### 9.9 `core/session.py` — the single answer about market sessions (M3.4)

`is_trading_day(d, holidays)`, `session_of(now_et, holidays)`, `expected_interval(session, event_secs, normal_secs)`. Pure — it takes the holiday set as an argument because `core/` may not import `config` (enforced by `tests/test_layering.py`).

Extracted from `collector.py` when a third caller appeared. The dashboard header's freshness thresholds are **not** a second policy that happens to agree with the collector's polling cadence — they *are* that cadence, so two copies of the number would eventually disagree, and the disagreement would surface as a dashboard that either cries wolf or stays quiet through a real outage. `collector.get_session` / `_poll_interval` / `_is_trading_day` are now thin wrappers; `tests/test_session.py::test_the_collector_and_the_header_cannot_disagree` compares the two directly.

**`expected_interval` returns `None`, never `0`, for a shut market.** `None` means "no expectation"; a caller reading it as zero concludes a price is due every zero seconds and reports the data as permanently late from 16:00 until 09:30 — a dashboard glowing red all evening and a watchdog emailing at midnight.

Boundaries: `OPEN` 09:30–10:00 (60s) · `MIDDAY` 10:00–15:30 (300s) · `CLOSE` 15:30–16:00 (60s) · otherwise `None`. Collection stops at 16:00 because SPX freezes at the equity close.

### 9.10 Header liveness strip (M3.4)

`ui/header.py::_render_liveness_strip(snap_age_secs, expected_interval)` replaced the `⏱ Next update in: Ns` countdown. It renders `🕐 HH:MM:SS ET · Time since last data: Xm Ys`, ticking client-side via `setInterval` (guarded by `window.__spxLive` so repeated Streamlit reruns cannot stack timers) rather than by per-second reruns, which would collide with BUG-020.

**Why the countdown had to go rather than be improved:** counting *toward* an event has a resting state that reads as healthy. Collector dead, no price for an hour, and it displayed `0s` and sat there. Counting *upward* has no resting state — the longer it is broken, the louder the number.

**What each half proves.** The wall clock proves the browser tab is alive; it would keep ticking if the Streamlit process behind it died, so it proves nothing about freshness. The age proves freshness. Neither replaces the watchdog, because both require somebody looking.

**Colour.** Green → **amber** at `expected_interval` → **red** at `_RED_MULTIPLE` (1.5×). Deliberately not red exactly at the threshold: at the 300-second midday cadence the age reaches 300s immediately before every poll, so a threshold-exact red would flash once per cycle all day and train the reader to ignore it. With `expected_interval is None` the strip reads "— market closed, collector idle".

---

## 10. Future Roadmap

### 10.1 Planned

**Trade Logging + Favorability Validation (APPROVED — the mechanism that resolves §3.1 and §9.4).**
Add a `trades` table and a lightweight logging step so that regime favorability is answered from real fills rather than from priors (anyone's, including the model's). Proposed schema:

```sql
CREATE TABLE trades (
    trade_id           INTEGER PRIMARY KEY,
    -- entry
    entry_timestamp    TEXT,      -- UTC ISO8601
    front_expiry       TEXT,
    back_expiry        TEXT,
    call_strike        REAL,
    put_strike         REAL,
    entry_debit        REAL,      -- actual filled debit
    -- regime snapshot at entry (the variables under test)
    entry_call_ratio   REAL,      -- front_call_iv / back_call_iv at entry
    entry_put_ratio    REAL,
    entry_atm_ratio    REAL,
    entry_spx          REAL,
    -- transform / exit
    transform_timestamp TEXT,
    transform_credit_modeled REAL,  -- what the dashboard showed
    transform_credit_actual  REAL,  -- actual filled credit (key for slippage calibration)
    outcome_pnl        REAL,       -- realized P&L on the trade
    was_transformed    INTEGER,    -- 1 = transformed to IC, 0 = closed/expired otherwise
    notes              TEXT
);
```

Analysis enabled once ~20+ trades exist: correlate `entry_*_ratio` with `outcome_pnl` and with `transform_credit_actual` to test whether any regime direction has a real, signed relationship to results — and to calibrate the live threshold (§9.1) from `modeled` vs `actual` credit.
*(This is documented here as the approved plan; the collector/db implementation is a separate build task.)*

**Net Theta Advantage ($/day) — Phase 3.** From reliable per-leg theta in `option_rows`.

**Proper Time-to-Viability — Phase 3.** Replaces the removed Theta ETA; built from per-leg Greeks (theta from both legs at minimum), still labeled an estimate.

**Position Tracker / Transformation Calculator — Phase 4.** Uses the `trades` table; shows resulting Iron Condor max loss / max profit / break-evens / risk-reduced status.

**Payoff Diagrams — Phase 5.** Diagonal (BS pre-expiry) and resulting IC (intrinsic at expiry).

### 10.2 Under Investigation
- **Live threshold calibration** (~$6.50–$7.00, pending live fills).
- **Mean-reversion estimate UI surface** (function exists; unclear if it adds decision value or noise).
- **Whether any IV regime is tradeable at all** — the §3.1 question, to be answered by §10.1 data, not assumed in either direction.

### 10.3 Rejected
- **Composite Transformation Score (0–100).** Obscures the limiting dimension. Do not build.
- **Automatic event detection.** Lags the spike; manual Event Mode is faster. Do not build.
- **Theta ETA (assumption-based).** Removed v1.1; do not reintroduce without per-leg Greeks. Do not build in the old form.
- **Valenced regime coloring.** No green/red *good–bad* encoding of IV regime until favorability is validated. **v3 nuance:** the IV-ratio line is colored by *regime band* (teal ≥1.30, green 1.00–1.30, periwinkle 0.70–1.00, amber <0.70) at the user's request for readability. The legend uses *regime names*, not valence words, and the amber band reads as a 0DTE/EOD *caution/artifact* zone — so this remains a regime label, not a "this regime is favorable, enter" signal. Favorability stays unvalidated (see §3.1 HYPOTHESIS and §11.4).
- **Multi-user / SaaS.** Personal tool. Do not build.
- **Cross-underlying extension.** Every threshold/assumption is SPX-specific. Not planned.

---

## 11. Dashboard v3 — Changes & New Analytics (detailed)

This section documents Dashboard v3 in full: the layout changes, the multi-day
chart-continuity fix, and the three new analytics surfaces (stacked panel,
Front-vs-Back scatter, and the Regime Analysis sub-tab). It explains each with a
worked example.

### 11.1 Layout & continuity changes (`app.py`)

These are presentation-only; no metric definitions changed.

- **Compaction.** A CSS block after `set_page_config` reduces the main container's
  top padding and tightens the vertical rhythm between sections. Goal: less
  scrolling without crowding. The two dials are `.block-container { padding-top }`
  and the vertical-block `gap`.
- **Header.** The intraday sparkline was removed. The `pts ↔ %` toggle now sits
  directly beneath the SPX change value for one-tap switching.
- **Expiry dropdowns** show DTE inline, e.g. `2026-06-29  (3D)`, via a `format_func`
  over an `{expiry: dte}` map. The dropdown's *value* is still the raw date, so no
  downstream code changed.
- **Expiry Detail** shows date and DTE together, e.g. `Front · 2026-06-26 · 0 DTE`.
- **Period selector** (Today/5D/10D/20D) moved to the right of the Selected-Strike
  IV chart and remains a single **shared** control driving both that chart and the
  Calendar Edge chart. Calendar Edge shows a read-only `Range:` indicator.
- **Multi-day continuity.** All multi-day IV charts collapse non-trading time with
  Plotly `rangebreaks`: weekends, the 16:00→09:30 ET overnight window, and full-day
  holidays from `config.MARKET_HOLIDAYS` (the dashboard now reads this set; it was
  collector-only before). Bounds are in `America/New_York`, so they are DST-safe.
  *Effect:* on 5D/10D/20D the line is continuous across sessions instead of drawing
  long diagonal ramps across empty overnight/weekend bands. *Known residual:* a
  mid-session collector outage (a data hole during trading hours that is not a
  holiday) is neither broken nor collapsed and will draw a straight connector across
  the hole — rare, and arguably a useful data-quality signal.

### 11.2 Stacked panel — Front/Back IV + regime-colored ratio (Calendar Edge)

Lives in a collapsed expander under the **existing** Calendar Edge dual-axis chart
(which is retained). Two panels share one x-axis:

- **Top:** Front ATM IV and Back ATM IV on the *same* IV% axis. Because they share a
  scale, the vertical gap between the lines *is* the term-structure spread — read
  directly, with no second-axis distortion.
- **Bottom:** the IV Ratio (F/B) as a single **continuous** line whose color changes
  by regime band, with reference lines at 1.00 (solid) and 0.70 / 1.30 (dotted).

**Bands (thresholds 0.70 / 1.00 / 1.30):** teal `≥1.30` (strong backwardation),
green `1.00–1.30` (backwardation, front rich), periwinkle `0.70–1.00` (contango,
normal), amber `<0.70` (deep contango / likely 0DTE-EOD artifact). Colors are
**regime labels, not favorability** (see §11.4 and §10.3).

**How the continuous coloring works (and a worked example).** Coloring a line by
y-value normally leaves gaps at band changes. Instead, where the series crosses a
threshold the exact crossing point is interpolated and inserted, and each band emits
one trace that is non-None only inside its band — but **boundary points belong to
both adjacent bands**, so the segments touch.

> *Example.* Ratio goes 0.95 → 1.06 between two snapshots. It crosses 1.00. We solve
> for the fraction of the segment at which R=1.00: `frac = (1.00 − 0.95)/(1.06 − 0.95)
> = 0.4545`, interpolate the timestamp at that fraction, and insert the point
> (t*, 1.00). The periwinkle (0.70–1.00) segment ends exactly at (t*, 1.00); the
> green (1.00–1.30) segment begins exactly there. The eye sees one unbroken line that
> turns from periwinkle to green precisely at the 1.00 line.

### 11.3 Front-vs-Back scatter — intraday trajectory (Calendar Edge)

A collapsed expander plotting each snapshot as a dot: **x = Back IV, y = Front IV**,
with the `y = x` (R=1) line drawn, colored by **time of day**.

**How to read it.** Above the line = backwardation (front richer, R>1); below =
contango. Perpendicular distance from the line ∝ the spread (F−B). Distance from the
origin ∝ the overall vol level. So one dot encodes level (radius) and structure
(angle) at once.

> *Example.* A dot at (Back 16%, Front 20%) sits above the line (R = 1.25,
> backwardation) and far from the origin (high level). A dot at (Back 11%, Front 12%)
> sits just above the line (R ≈ 1.09) and near the origin (low level) — same broad
> "front rich" structure, very different premium environment. The two-line time
> series can't show that distinction at a glance; the scatter can.

**The diagnostic.** A cloud hugging one ray through the origin ⇒ ratio ≈ constant
(adds little beyond level). A cloud that fans across angles ⇒ ratio varies
independently of level (adds information). Intraday, color typically shows the cloud
starting high and above the line at the open, then spiraling inward and downward as
the front leg crushes faster than the back.

### 11.4 Regime Analysis sub-tab (Trade Journal → `📈 Regime Analysis`)

The formal test of the §3.1 question: **does IV Ratio carry outcome information
beyond IV level?** It reconstructs entry-time term structure for every logged trade
and asks whether the *structure* dimension matters after the *level* dimension is
held fixed.

**Data path (no schema change).** For each trade, `initial_legs` JSON yields the
front/back expiries and the call/put strikes; `entry_date`+`entry_time` (ET) is
converted to UTC; `db.get_entry_iv_context` finds the nearest COMPLETE snapshot and
returns the **at-strike** Front/Back IV (averaged across the call and put legs you
actually traded), plus ATM context. This works **retroactively** on existing trades.

**Why level = √(F·B), not Front IV.** Intraday, R = F/B ≈ F/(sticky back), so Front
IV and Ratio are *correlated* — splitting on Front IV × Ratio leaves two quadrants
nearly empty and confounds the test. Level `L = √(F·B)` and `R = F/B` are an exact,
near-orthogonal reparametrization of (F, B): knowing the geometric-mean vol tells you
almost nothing about the ratio, so all four quadrants populate and "does R matter
after controlling for level?" becomes cleanly separable. (`F = L·√R`, `B = L/√R`.)

**The visualization.** The same Front-vs-Back scatter, now divided by an **orange ray**
(front = median-R × back) splitting high/low ratio and a **purple hyperbola**
(`front = median-level² / back`) splitting high/low level. The four regions are the
quadrants. Points are colored by **realized transform credit** (`profit_locked_in`,
the validated metric; red→green diverging, centered at 0); open trades render as grey
hollow markers.

**The stratified 2×2 table** reports mean transform credit and **n** per cell.

> *Worked example.* Suppose, once enough trades exist:
>
> | Level \ Ratio | High R | Low R |
> |---|---|---|
> | **High level** | +6.9 (n=12) | +5.1 (n=11) |
> | **Low level**  | +6.4 (n=10) | +4.8 (n=13) |
>
> Reading **across each row** (holding level fixed): High-R beats Low-R by ~+1.8 at
> high level and ~+1.6 at low level. Because the ratio effect **survives within both
> level strata**, IV Ratio is adding information beyond level — a real reason to put it
> in the entry criteria. If instead the rows were flat across the ratio columns and all
> the variation were top-to-bottom (level), the ratio would just be proxying level and
> would *not* earn a place in the entry rule.

**What NOT to conclude (enforced in the UI).** (1) **Sample size** — with a handful of
trades none of this is significant; cells with n<5 are flagged as noise; ~10–15 per
cell is the floor. (2) **Pre-commit** to transform credit as the primary outcome and
the median splits *before* the data fills in — do not tune the 0.70/1.30 bands or the
split points to what looks good (overfitting). (3) **Selection bias** — outcomes exist
only for regimes actually entered; an empty quadrant means "never traded there", not
"bad". (4) **Confounds** — front-DTE and the 0DTE end-of-day artifact distort the
ratio; entries near the close are least reliable.

**Status:** `HYPOTHESIS`. This sub-tab is the *mechanism* to validate or refute the
IV-ratio-favorability question; it asserts nothing until the cells carry real n.

### 11.5 New code surface (v3)

| Item | File | Notes |
|---|---|---|
| `_SESSION_RANGEBREAKS` | `app.py` | Weekend + overnight + holiday collapse for multi-day charts. |
| `_RATIO_BANDS`, `_RATIO_THRESHOLDS` | `app.py` | Regime band edges/colors for the ratio line. |
| `_banded_ratio_traces()` | `app.py` | Continuous multicolor ratio line via threshold interpolation. |
| Stacked panel + scatter | `app.py` | Additive expanders under Calendar Edge; original chart retained. |
| `get_entry_iv_context()` | `db.py` | Read-only reconstruction of entry-time F/B/R/level from snapshots. No schema change. |
| `render_regime_analysis()` + nav entry | `pages/journal.py` | New `📈 Regime Analysis` sub-tab. |

---


## 12. Trade Journal — v3.1 Reference

This section is the canonical reference for the Trade Journal feature set as of v3.1. It covers the data model, the trade lifecycle, all CRUD workflows, the guided edit wizard, and the strategy statistics panel.

### 12.1 Trade Lifecycle

A trade moves through the following statuses in `trades.status`:

```
Open  ──► Transformed ──► Expired
  │
  └──────► Closed
```

- **Open** — diagonal calendar spread entered; no transformation yet.
- **Transformed** — short front legs kept; back longs closed; front-expiry protective wings bought. The position is now an Iron Condor. Realized P&L is locked.
- **Expired** — the IC has reached its expiration date. Final P&L recorded via "⏰ Mark Expired".
- **Closed** — all legs closed manually before or without IC transformation (`close_type = "direct"`). Final P&L recorded at close time; no separate Mark Expired step needed.

`"Expired"` and `"Closed"` are treated identically for all strategy statistics.

---

### 12.2 Close Type (`trades.close_type`)

| Value | Meaning |
|---|---|
| `"transform"` | IC conversion path. Transformation legs recorded; IC fields populated. |
| `"direct"` | All legs closed manually. `transform_date/time/spx_at_transform/credit_received/profit_locked_in` store the close details; no IC fields populated. |
| `NULL` | Legacy records created before v3.1. Treated as `"transform"` for display purposes. |

---

### 12.3 Database Schema — `trades` table

All columns in full, as of v3.1 plus the eight `entry_*` columns added 2026-08-09 (M3):

| Column | Type | Description |
|---|---|---|
| `trade_id` | TEXT PK | Sequential ID e.g. `T001`, `T002` |
| `status` | TEXT | Open / Transformed / Expired / Closed |
| `close_type` | TEXT | `"transform"` / `"direct"` / NULL (v3.1 addition) |
| `entry_date` | TEXT | ISO date of entry |
| `entry_time` | TEXT | HH:MM ET |
| `day_of_week` | TEXT | Monday … Friday |
| `spx_at_entry` | REAL | SPX price at entry |
| `contracts` | INTEGER | Number of contracts |
| `commissions` | REAL | Entry-step commissions/fees ($) |
| `initial_legs` | TEXT | JSON array of 4 legs — the diagonal |
| `total_debit` | REAL | Net debit paid / share at entry |
| `notes` | TEXT | Freeform trader notes |
| `entry_iv_snapshot_id` | INTEGER | ↓ **The eight `entry_*` columns below are the M3 entry-IV snapshot (ADR-044/ADR-016).** They record the term structure as it stood when the trade was opened, because `db.get_entry_iv_context()` derives that by reading historical `option_rows`, and `scripts/prune.py` deletes those 90 days past expiry. **They are written by `db.insert_trade()` and recomputed by `db.update_trade()`, never by the caller** — a call site that could forget them would lose the context permanently and silently. `db.snapshot_entry_iv_context()` returns all-None on any failure: a trade must always be recordable. NULL on pre-M3 rows, which fall back to reconstruction (`pages/journal.py::_stored_entry_iv` returns None and Regime Analysis reports the split). `db.ENTRY_IV_COLUMNS` is the single definition. ── The snapshot this was taken from |
| `entry_iv_snapshot_ts` | TEXT | Timestamp of that snapshot |
| `entry_front_iv` | REAL | Front-expiry IV at the traded strike, **decimal** (0.18 = 18%) |
| `entry_back_iv` | REAL | Back-expiry IV at the traded strike, decimal |
| `entry_iv_ratio` | REAL | front ÷ back |
| `entry_iv_level` | REAL | Front IV's percentile against its own history |
| `entry_atm_front_iv` | REAL | At-the-money front IV, decimal |
| `entry_atm_back_iv` | REAL | At-the-money back IV, decimal |
| `transform_date` | TEXT | ISO date of transformation or direct close |
| `transform_time` | TEXT | HH:MM ET of transformation or direct close |
| `transform_minutes` | INTEGER | Minutes from entry to transformation (IC path only) |
| `spx_at_transform` | REAL | SPX at transformation or direct close |
| `transform_legs` | TEXT | JSON array of 4 legs — the transformation (IC path only) |
| `credit_received` | REAL | Credit received / share (transformation or net close proceeds) |
| `profit_locked_in` | REAL | `credit_received − total_debit` (Realized P&L / share) |
| `transform_commissions` | REAL | Commissions/fees at transformation or direct close (v3.1 addition) |
| `ic_expiry_date` | TEXT | IC expiry date (IC path only) |
| `ic_short_call` | REAL | Short call strike |
| `ic_long_call` | REAL | Long call strike (wing) |
| `ic_short_put` | REAL | Short put strike |
| `ic_long_put` | REAL | Long put strike (wing) |
| `ic_call_wing` | REAL | \|ic_long_call − ic_short_call\| in points |
| `ic_put_wing` | REAL | \|ic_short_put − ic_long_put\| in points |
| `ic_max_profit` | REAL | `profit_locked_in × 100 × contracts` ($) |
| `ic_worst_case` | REAL | Max IC loss if not risk-free; guaranteed minimum profit if risk-free ($) |
| `ic_risk_free` | INTEGER | 1 if locked credit ≥ max IC loss; 0 otherwise |
| `result_date` | TEXT | Expiry / close date |
| `spx_at_expiry` | REAL | SPX at expiry / IC close |
| `final_pl` | REAL | Final Realized P&L / contract ($) |
| `expired_inside_wings` | INTEGER | 1 if SPX settled between long wings |
| `expired_between_shorts` | INTEGER | 1 if SPX settled between short strikes (max-profit zone) |
| `outcome` | TEXT | Auto-detected: Maximum Profit / Partial Profit / Minimum Profit (Risk-Free) / Maximum Loss / Closed at Profit / Break Even / Closed at Loss |
| `updated_at` | TEXT | UTC ISO8601, auto-updated on every write |

---

### 12.4 P&L Terminology

These three terms have precise, non-interchangeable meanings everywhere in the journal:

**Realized P&L** — locked or final profit **before fees**. Set at the moment of transformation or close and does not change afterward.
- IC path: `profit_locked_in = transform_credit − entry_debit` per share.
- Direct close: `profit_locked_in = net_proceeds − entry_debit` per share.
- Per contract: `profit_locked_in × 100 × contracts`.

**Unrealized P&L** — current IC position value vs the fills at which each leg was opened. Only meaningful while the IC is open. Displayed in the Iron Condor tab.
- Short legs: `(fill − current_mark) × 100 × contracts` (positive when mark < fill, i.e. position has gained).
- Long legs: `(current_mark − fill) × 100 × contracts` (positive when mark > fill).
- Total IC Unrealized = sum of all four leg unrealized P&Ls.

**Net P&L** — the bottom-line number after fees.
- Completed trade: `final_pl − total_fees(trade)` per contract.
- Open IC trade: `(profit_locked_in × 100 × contracts) + IC_Unrealized − total_fees(trade)`.

**Total Fees** = `commissions + transform_commissions`. Covers all commission/fee fields across the trade lifecycle.

---

### 12.5 Strategy Statistics Panel

Fifteen KPIs in three rows of five. Denominator for all rate/average statistics is `status in ("Expired", "Closed")` — i.e. all completed trades regardless of whether they went through IC transformation.

| KPI | Formula |
|---|---|
| Total Trades | `count(all_trades)` |
| Win Rate | `count(final_pl > 0) / count(completed) × 100` |
| Average Winner | `mean(final_pl)` for winning trades |
| Average Loser | `mean(final_pl)` for losing trades |
| Profit Factor | `sum(wins) / abs(sum(losses))` |
| Expectancy | `(win_rate × avg_win) + ((1−win_rate) × avg_loss)` |
| Avg Entry Debit | `mean(total_debit)` across all trades |
| Avg Close Credit | `mean(credit_received)` for trades with a recorded credit (IC or direct close) |
| Avg Holding (days) | `mean(result_date − entry_date)` for completed trades |
| Avg Time to Transform | `mean(transform_minutes)` for IC-path trades |
| Avg Max Drawdown | *Requires intraday mark history — not yet implemented* |
| Largest Winner | `max(final_pl)` |
| Largest Loser | `min(final_pl)` |
| Total Fees | `sum(commissions + transform_commissions)` across all trades |
| Total Net P&L | `sum(final_pl) − Total Fees` for completed trades |

---

### 12.6 CRUD Operations Reference

**Log a Trade** — creates a new trade record. Status set to `"Open"`. Success message: "Trade logged successfully."

**Edit (initial entry)** — available only via the Master Log actions row (Edit button). Opens Log a Trade pre-populated. In standalone mode: saves and returns to Overview with "Changes saved successfully." In wizard mode: saves and proceeds to Close / Transform (Step 2) with "Initial Trade saved. Review Close / Transform record below."

**Delete (trade)** — available only via the Master Log actions row (Delete button). Requires inline confirmation. Calls `db.delete_trade()`. Irreversible.

**Record Transformation** (IC path) — available via "🔄 Close / Transform" sidebar page or Step 2 of the wizard. Records `transform_*` fields, populates all `ic_*` fields via `derive_ic()`. Success: routes to "⏰ Mark Expired" (wizard) or Overview (standalone).

**Record Close** (direct path) — available via "🔄 Close / Transform" page with "Close Position Directly" toggle active. Records close details; sets `status = "Closed"`, `close_type = "direct"`, `result_date`, `final_pl`, `outcome`. Success: routes to Overview.

**Edit Transformation / Edit Close** — in v3.1 the Edit and Delete buttons were removed from the Trade Detail Transformation tab. The only edit entry point is the guided wizard, launched from the Master Log.

**Mark Expired** — records expiry result for IC-path trades. Auto-detects `outcome` from SPX at expiry vs IC strikes. Sets `status = "Expired"`.

**Edit Notes** — standalone sidebar page for freeform notes on any trade. Calls `update_trade(notes=...)`.

---

### 12.7 Guided Edit Wizard

The wizard is a two-step guided flow launched exclusively from the Master Log "✏️ Edit" button. It replaces the previous pattern of separate edit entry points for initial trade vs transformation.

**Session state keys:**

| Key | Type | Purpose |
|---|---|---|
| `_wizard_mode` | bool | Whether the wizard is active |
| `_wizard_trade_id` | str | Anchor trade ID; persists across both steps |
| `_pending_nav` | str\|None | Pending page navigation; applied before the radio widget renders |
| `_pending_close_mode` | str\|None | Pending close-mode toggle selection; applied before the toggle renders |
| `_show_leave_warning` | bool | Unsaved-changes warning is active |
| `_interrupted_nav_dest` | str\|None | Where the user was trying to go when the warning fired |
| `_show_no_data_warning` | bool | "Nothing entered" warning in Close/Transform |

**Step 1 — Log a Trade:**

```
┌─────────────────────────────────┐
│ ← Cancel Edit  Move to Step 2 → │  ← outside the form
├─────────────────────────────────┤
│  [all form fields pre-populated] │
│           💾 Save Changes        │  ← inside the form
└─────────────────────────────────┘
```

- Cancel Edit: clears wizard state, returns to Overview. Zero DB writes.
- Move to Step 2: navigates to Close / Transform with existing record intact. Zero DB writes. Message: "Log Entry unchanged. Review Close / Transform record below."
- Save Changes: saves edits, navigates to Close / Transform. Message: "Initial Trade saved. Review Close / Transform record below."

**Step 2 — Close / Transform:**

```
┌─────────────────────────────────┐
│ ← Go Back          Cancel       │  ← outside the form
├─────────────────────────────────┤
│ [Transform to IC | Close Direct] │  ← close mode toggle
│  [form fields pre-populated]    │
│           💾 Save Changes        │  ← inside the form
└─────────────────────────────────┘
```

- Go Back: restores `edit_trade_id = wizard_trade_id`, navigates to Step 1. Form shows last saved DB state.
- Cancel: clears all wizard state, returns to Overview.
- Save Changes (values entered, IC path): saves transformation, routes to "⏰ Mark Expired".
- Save Changes (values entered, Direct path): saves close record, routes to Overview.
- Save Changes (nothing entered — credit ≤ 0 for IC, no close_time for Direct): shows "Position hasn't been transformed or closed." with Overview button. Nothing is saved.

---

### 12.8 Unsaved Changes Protection

A post-radio guard runs after the sidebar navigation radio widget renders and before any page content. It detects when the user navigates away from an active edit form.

**Condition:** `edit_trade_id` or `edit_transform_id` is set AND `page_mode` does not match the expected edit page.

**Behaviour:** the guard sets `_show_leave_warning = True`, stores the intended destination in `_interrupted_nav_dest`, and uses `_pending_nav` to redirect the radio back to the edit page on the next render. The edit page then shows an inline warning above the form.

**Dialog options:**
- "Leave (discard changes)" — clears edit state, navigates to the originally intended page.
- "Stay on page" — clears the warning, stays on the edit page.

**Known limitation:** Streamlit's `st.form` only delivers widget values on submit. The guard detects edit-mode activation rather than field-level value changes. Navigating away from a form you have not yet modified will still show the warning if an edit session is active.

---

### 12.9 Inspect Trade Auto-Navigation

Selecting a trade from the "Inspect Trade" sidebar dropdown while on any non-Overview page automatically navigates to Overview to show the trade detail.

**Implementation:** `_last_selected_id` session state key tracks the previous selection. When `selected_id != _prev_sel` and `page_mode != "📊 Overview"` and no leave-warning is currently showing, `_pending_nav = "📊 Overview"` is set and `st.rerun()` called. If an unsaved-changes guard would fire, it takes precedence.

---

### 12.10 Live IC Position Monitoring

The Iron Condor tab shows per-leg fill prices and unrealized P&L alongside the existing live marks table. This requires the transformation legs to have been recorded with fill prices.

**Fill price source:**
- Short Call / Short Put fills: from `initial_legs` JSON (the original diagonal short legs).
- Long Call / Long Put fills: from `transform_legs` JSON (the "Buy to Open" wing legs).

**Per-leg Unrealized P&L:**
- Short legs: `(fill − mark) × 100 × contracts`
- Long legs: `(mark − fill) × 100 × contracts`

**Summary metrics displayed:**

| Metric | Formula |
|---|---|
| Realized P&L / contract | `profit_locked_in × 100 × contracts` |
| IC Unrealized P&L / contract | `sum(per-leg unrealized) per contract` |
| Total Fees | `commissions + transform_commissions` |
| Net P&L / contract | Realized + IC Unrealized − Total Fees |

---

### 12.11 New code surfaces (v3.1)

| Item | File | Notes |
|---|---|---|
| `total_fees(t)` | `pages/journal.py` | Sums `commissions + transform_commissions` safely across legacy rows |
| `get_ic_fills(init_json, tf_json)` | `pages/journal.py` | Extracts fill prices for all 4 IC legs from stored JSON |
| `get_close_type(t)` | `pages/journal.py` | Safe `close_type` read from `sqlite3.Row`; returns None for legacy records |
| `compute_stats` — fixed fees | `pages/journal.py` | Now uses `total_fees(r)` per row; previously only summed entry `commissions` |
| `compute_stats` — Closed unification | `pages/journal.py` | Filter now `status in ("Expired", "Closed")` |
| `_SS_DEFAULTS` dict | `pages/journal.py` | Single-source session state initialisation; all keys and defaults in one place |
| `_pending_nav` pattern | `pages/journal.py` | Write-before-render intermediary; prevents Streamlit keyed-widget write error |
| `_pending_close_mode` pattern | `pages/journal.py` | Same pattern for the close-mode radio toggle |
| Wizard session state | `pages/journal.py` | `_wizard_mode`, `_wizard_trade_id`, `_show_no_data_warning` |
| Unsaved-changes guard | `pages/journal.py` | Post-radio check; uses `_pending_nav` to redirect + `_show_leave_warning` |
| `delete_trade(db_path, trade_id)` | `db.py` | `DELETE WHERE trade_id = ?`; called only after user confirmation |
| `transform_commissions REAL` migration | `db.py` | `ALTER TABLE` inside `init_trades_table`; safe on existing databases |
| `close_type TEXT` migration | `db.py` | Same pattern; drives display branching throughout journal |
| 8 × `entry_*` migration | `db.py` | Same pattern, driven by `ENTRY_IV_COLUMNS`. This makes **ten** hand-rolled add-if-missing `ALTER TABLE`s in `init_trades_table` — the argument for M3.3's real migration framework |
| `snapshot_entry_iv_context()` | `db.py` | Derives front/back expiry and call/put strikes from `initial_legs`, converts the ET entry time to UTC via `config.DISPLAY_TIMEZONE`, calls `get_entry_iv_context()`. All-None on any failure |
| `_stored_entry_iv(trade)` | `pages/journal.py` | Reads the stored context in reconstruction shape; None when the columns are absent or front/back IV is NULL, so Regime Analysis falls back |
| `get_protected_expiries()` | `db.py` | Every expiry named by any trade's `initial_legs` / `transform_legs` / `ic_expiry_date`. **Fails closed** — the `json.loads` is deliberately unguarded, because a swallowed parse error would silently drop a trade's protection |
| `plan_prune()` / `execute_prune()` | `db.py` | Read-only planning, separated from acting, so the report shown and the rows deleted cannot be two different answers. `execute_prune` deletes only `option_rows`, only the plan's exact expiries, in one transaction |

---

*End of Section 12 — added in DOCUMENTATION.md v1.4 (2026-06-27)*

---

## 13. Dashboard v3.2 — Entry Analysis, Layout Reorder, Normalized Metrics (detailed)

Added in DOCUMENTATION.md v1.5 (2026-06-29).

### 13.1 Layout Order (v3.2)

The complete top-to-bottom section order as of v3.2:

| # | Section | Key decision question answered |
|---|---------|-------------------------------|
| 1 | Header | Is the market open? What is the broad environment? |
| 2 | Controls | What setup am I analyzing? |
| 3 | Entry Analysis | What is this position offering? Am I near the transform threshold? |
| 4 | Calendar Edge | Why is the setup priced this way? (macro vol context) |
| 5 | Historical Statistics | Is today's ratio unusual or normal? |
| 6 | Strike Detail + Selected-Strike IV | Do the individual legs support what I think I'm building? |
| 7 | Pinned Pairs | Saved pair watchlist |
| 8 | Pair Scanner | Discovery tool |
| 9 | Research — IV Ratio vs. Normalized Debit | Long-run scatter for hypothesis observation |

The guiding principle: *decision-critical information first, deeper analytics below*.

### 13.2 Dependency Architecture (v3.2 `app.py`)

Two pre-computation blocks run between Controls and Entry Analysis:

```
Controls (expiry + strikes)
  │
  ├── ts_now = iv_engine.term_structure(front_iv_atm, back_iv_atm)   ← always early
  │
  ├── atm_merged_90d  (fixed 90-day, no radio) ─────────────────────► Entry Analysis IV pct
  │
  ▼ Calendar Edge renders period radio
  │
  ├── period_days (from radio)
  ├── atm_merged (period-specific, from _load_atm_hist_fb) ──────────► Calendar Edge charts
  │
  ▼ Strike Detail (inherits period_days) ──────────────────────────► Strike IV chart
```

The `atm_merged_90d` / `atm_merged` split ensures the IV Ratio Percentile in Entry Analysis always reflects 90-day context regardless of the chart zoom the user has selected.

### 13.3 IC Risk Profile Chart (`pages/journal.py`)

The Inspect Trade → Iron Condor tab now shows an interactive payoff-at-expiration chart instead of the P&L by Expiry Zone table. The chart uses piecewise-linear payoff (the only correct form for IC at expiry):

```
price ≤ lp          →  wing_val (−max_loss or +wc if rf=True)
lp < price ≤ sp     →  linear from wing_val to +mp
sp < price ≤ sc     →  +mp (max profit plateau)
sc < price ≤ lc     →  linear from +mp to wing_val
price > lc          →  wing_val
```

Breakevens: `BE_lower = lp + wc·(sp−lp)/(mp+wc)`, `BE_upper = sc + mp·(lc−sc)/(mp+wc)`.

Five rendering layers: green fill (profit zone), red fill (loss zone), green curve, red curve, invisible 24px hover trace. Amber vertical marks current SPX from `marks["spx"]` (hoisted before chart). Function: `_ic_payoff_chart(lp, sp, sc, lc, mp, wc, rf, current_spx=None) → go.Figure`.

### 13.4 New Code Surfaces (v3.2)

| Item | File | Notes |
|------|------|-------|
| `_ic_payoff_chart()` | `pages/journal.py` | Pure function — no `st` calls. Returns `go.Figure`. |
| `atm_straddle_price()` | `iv_engine.py` | §6.12. Takes IV in pct form. |
| `normalized_debit()` | `iv_engine.py` | §6.13. Returns None if straddle zero. |
| `ThetaDifferential` dataclass | `iv_engine.py` | Full decomposition: per-leg, per-side, net, contract. |
| `theta_differential()` | `iv_engine.py` | §6.14. `available=False` when Greeks absent. |
| `get_diagonal_history()` | `db.py` | Four-leg mark history for research scatter. Excludes rows where any leg mark is NULL. |
| `_load_atm_hist_fb()` | `app.py` | Weekend/holiday fallback for ATM IV history. |
| `_load_contract_hist()` | `app.py` | Same fallback added inline. |
| `atm_merged_90d` | `app.py` | Fixed 90-day merge; pre-computed before Entry Analysis. |

### 13.5 HYPOTHESIS Metrics Register (v3.2)

All four new metrics are explicitly labeled HYPOTHESIS throughout the dashboard and this document. They display raw values for observation; none are wired into any composite score or automatic decision rule.

| Metric | What it would need to graduate from HYPOTHESIS |
|--------|----------------------------------------------|
| Normalized Debit | ≥20 completed trades with logged entry norm_debit; regression showing significant correlation with transform credit or holding time |
| Theta Differential | ≥20 trades with valid Greeks at entry; correlation with days-to-transformation |
| Transform Order Mark / Difference | ≥10 transformations with before/after difference logged; calibration of threshold vs actual fill economics |
| IV Ratio Percentile | Already under test via Regime Analysis (§11.4); awaiting cell population |

*End of Section 13 — added in DOCUMENTATION.md v1.5 (2026-06-29)*

---

## 14. Dashboard v4.0 — Premium Redesign, Mission Control, Journal P&L Lifecycle Fix

Added in DOCUMENTATION.md v1.7 (2026-06-30).

### 14.1 Navigation Architecture (v4.0)

The dashboard migrated from a vertically-scrolled single page to a **custom session-state-driven tab bar**. Six tabs:

`🔭 Scanner | 📊 Entry Analysis | 📈 Calendar Edge | 🎯 Strike Detail | 📉 Historical Stats | 🔬 Research`

`st.tabs()` was replaced with a `st.container(key="topnav")` of styled buttons because `st.tabs()` has no API to programmatically switch the active tab from code. The Mission Control "View Chart" button sets `active_tab = "edge"` in session_state and calls `st.rerun()` — this is only possible with the custom nav.

A **persistent Controls Bar** (Front Expiry / Back Expiry / Put Strike / Call Strike) sits above all tabs and is always visible. Drill-down values from Mission Control cards are staged under `pending_*` session-state keys and promoted to `*_select` keys before the Controls Bar widgets render (avoids the "cannot modify widget after instantiation" Streamlit restriction).

### 14.2 Mission Control — Cross-Sectional Opportunity Discovery

The Scanner tab evolved from a browsable table to a trading cockpit.

#### 14.2.1 The Problem With Single-Strike Chart Inspection

The original workflow required manually selecting each expiry/strike combination to see its Transform Gap history — a single-instrument time-series view. This doesn't scale. The new design answers a cross-sectional question at every refresh: **which combinations across the entire chain are eligible, approaching eligibility, or have recently been eligible?**

#### 14.2.2 Two-Phase Scan Architecture

**Phase A — every refresh, in-memory only:**
Sweeps `_SWEEP_OFFSETS = list(range(0, 105, 5))` (21 symmetric offsets, every 5 points from ATM to ATM±100) against every valid (front, back) expiry pair. Uses chain data already loaded in memory — no extra DB calls. Classifies combos as Eligible (Transform Diff ≥ 5.0) or Approaching (Diff ∈ [4.0, 5.0)).

**Phase B — only for the small Eligible+Approaching candidate set (capped at 20/tier):**
Calls `db.get_transform_mark_history()` to retrieve per-snapshot mark history. Computes:
- **Duration Active:** length of the trailing contiguous window where gap ≥ 5 (for currently-eligible combos).
- **ETA:** linear extrapolation of the last 3–6 gap readings to threshold, in minutes. Only shown when slope > 0.01 and gap < threshold (rising trend).
- **Sparkline:** unicode bar chart of recent gap trajectory.

**Caching:** `_compute_mc_core` is `@st.cache_data` keyed on `snapshot_id`. Every tab click, widget change, and color pick between two collector snapshots hits the cache — no recomputation. Only new snapshots (every 60–300s) trigger Phase A+B.

#### 14.2.3 Non-ATM Eligibility Registry

**File:** `eligible_history.json` (local, excluded from git).

**Why it exists:** Phase A only sees the current snapshot. A transformation window that opened and closed between refreshes would be missed forever without a log. Also: Phase A sweeps symmetric offsets at 5-point granularity — a combo at offset 30 (between swept values 25 and 50) would never be found by the automated scan.

**What it records (per combo key = `front|back|put|call`):**

| Field | Meaning |
|-------|---------|
| `first_seen` | Timestamp of first observed crossing (naive UTC string) |
| `last_seen` | Timestamp of most recent crossing |
| `last_gap` | Gap value at `last_seen` |
| `max_gap` | Peak gap ever observed for this combo |
| `hit_count` | Number of snapshots where gap ≥ threshold |
| `iv_ratio` | IV ratio at most recent observation |

**Two write paths:**
1. **Automated (per-snapshot):** `_update_eligible_history()` called inside `_compute_mc_core`. Upserts all non-ATM combos currently ≥ threshold. Fires once per new snapshot due to caching.
2. **Opportunistic backfill** (`_backfill_eligible_history()`): hooked into Calendar Edge's Diagonal vs. Transform Order Mark chart. Whenever a user manually investigates any combo, its entire mark history is scanned for ≥5 crossings and registered. This catches off-grid combos (e.g. offset 30) the moment someone looks. tz normalization is applied: Calendar Edge timestamps are tz-aware (display timezone); registry stores naive UTC strings.

#### 14.2.4 Non-ATM Panel Ranking

The panel explicitly excludes ATM combinations (`put_strike == call_strike`). Ranking is a transparent multi-key sort — no composite score:

1. Currently live (gap ≥ 5 right now) outranks historical-only.
2. Raw gap (live) or max_gap (historical), descending.
3. `hit_count` descending — directly answers "which strikes repeatedly become transformable."
4. Most recent crossing as tiebreak.

**Never-empty guarantee:** when fewer than `min_display=6` combos fall within the selected lookback window, remaining slots are filled from the registry outside the window, flagged with an "OUTSIDE WINDOW" badge. The panel is always useful rather than occasionally empty.

#### 14.2.5 Persistent Attention Strip

Renders above the Controls Bar on every tab, regardless of which tab is active. Shows:
- Count: `🔥 N Eligible · N Approaching · N New`
- Best opportunity: `Best: 7235P/7525C  Gap +7.8  Active 2h12m`

"New" = combos that entered the Eligible set between the previous snapshot and this one. The "New" diff is gated on `snapshot_id` change, not script rerun, so normal widget interactions don't falsely re-badge things as new.

### 14.3 New DB Function: `get_transform_mark_history`

Location: `db.py`, after `get_diagonal_history`.

**Signature:**
```python
get_transform_mark_history(db_path, front_expiry, back_expiry, call_strike, put_strike, days=90) → list
```

**What it returns (one row per COMPLETE snapshot):**
`snapshot_timestamp, spx, front_call_mark, back_call_mark, front_put_mark, back_put_mark, front_wing_call_mark, front_wing_put_mark`

**Wing strikes:** `front_wing_call = call_strike + 5`, `front_wing_put = put_strike − 5` (front expiry only — matching the actual transformation order).

**Caller computes:**
```
diagonal_mark  = (back_call + back_put) − (front_call + front_put)
transform_mark = (back_call + back_put) − (front_wing_call + front_wing_put)
transform_gap  = transform_mark − diagonal_mark
```

Rows where ANY of the six required legs is missing a computable mark are excluded by the `IS NOT NULL` WHERE conditions.

### 14.4 Diagonal vs. Transform Order Mark Chart

**Location:** Calendar Edge tab — first chart (primary visualization).

**Purpose:** Shows historically how long the transformation window stayed open and at what magnitude, making the question "when could I have transformed?" answerable at a glance.

**Green shading:** Every contiguous window where Transform Gap ≥ 5 is shaded with `fig.add_vrect(fillcolor="rgba(16,212,163,0.14)", line_width=0)`. The shading uses per-snapshot granularity — transition into/out of the green region is visually immediate.

**Calendar Edge chart order (v4.0):**
1. Diagonal vs. Transform Order Mark (new — primary decision chart)
2. Front vs Back ATM IV + Ratio (stacked, 2-row)
3. Dual-axis IV + Ratio with secondary axis (moved from top)
4. Front vs Back IV scatter (unchanged)

### 14.5 Chart Appearance System

**File:** `chart_colors.json` (local preference, excluded from git).

**Default colors:**
| Key | Label | Default |
|-----|-------|---------|
| `diagonal_mark` | Diagonal Mark | `#5b9cff` |
| `transform_mark` | Transform Order Mark | `#f0a429` |
| `front_iv` | Front IV % | `#10d4a3` |
| `back_iv` | Back IV % | `#5b9cff` |

**Extension:** Add one entry to `DEFAULT_CHART_COLORS` in `app.py`. The sidebar color picker loop, reset button, and `CHART_COLORS` dict all iterate this dict automatically.

**Applied consistently across:** Diagonal vs. Transform Order Mark chart, Calendar Edge stacked chart, Calendar Edge dual-axis chart, Strike Detail call/put traces.

### 14.6 Trade Journal — P&L Lifecycle Fix

#### 14.6.1 The Problem

For transformed trades, `final_pl` in the database only captured the IC expiration outcome. The transformation gain (`profit_locked_in = credit_received − entry_debit`) was not included. This caused a trade where SPX expired outside the IC wings — but that was already risk-free due to the transformation — to display as a loss.

#### 14.6.2 The Two P&L Components

A transformed trade has two distinct realized P&L components:

**Component 1 — Transformation gain (settled at transformation time):**
```
transformation_gain = profit_locked_in × 100 × contracts
```
This is **permanent** — it cannot be reduced by subsequent IC outcomes. Once the transformation executes, this credit is realized regardless of where SPX settles.

**Component 2 — IC expiry adjustment (settled at IC expiration):**
```python
def ic_expiry_pnl_per_share(spx, lp, sp, sc, lc):
    if spx <= lp:   return -(sp - lp)      # max put-wing loss
    if spx < sp:    return -(sp - spx)     # partial put
    if spx <= sc:   return 0.0             # between shorts — max IC profit
    if spx < lc:    return -(spx - sc)     # partial call
    return -(lc - sc)                      # max call-wing loss
```

**Total:**
```
final_pl = (profit_locked_in + ic_expiry_adj) × 100 × contracts
```

#### 14.6.3 `resolved_pl(t)` — Single Source of Truth

Added to `pages/journal.py`. All display paths in the journal call `resolved_pl(t)` instead of reading `t["final_pl"]` directly:

| Trade status | `resolved_pl` returns |
|---|---|
| Expired / Closed — Transformed + IC data + SPX at expiry | Formula result (Component 1 + Component 2) |
| Expired / Closed — direct close or incomplete IC data | `float(t["final_pl"])` (stored value) |
| Transformed (IC still open) | `profit_locked_in × 100 × contracts` |
| Open | `None` |

**Affected surfaces:** Master Log table, `compute_stats` (win rate, averages, profit factor, expectancy, totals), Expiration tab metric cards, Net P&L after fees.

#### 14.6.4 Mark Expired — Fully Automated for Transformed Trades

The `Final Realized P&L / contract` manual input field is **removed** for transformed trades. The form now:
1. Shows the auto-calculated breakdown (transformation gain + IC expiry adjustment = total).
2. Writes `round(locked_ct + adj_ct)` to `final_pl` in the DB on confirm.
3. Guards: if SPX = 0 for a transformed trade, blocks submit rather than saving null.

For Open trades (no IC data), manual entry is retained.

#### 14.6.5 P&L Lifecycle Breakdown (Expiration Tab)

For any expired trade with IC data + SPX at expiry, the Expiration tab shows:

```
P&L LIFECYCLE BREAKDOWN
1. Transformation gain       +$590
2. IC expiry adjustment      −$500   (SPX 7501 vs shorts 7320–7380)
─────────────────────────────────────
Total Realized P&L           +$90
```

The total is always computed from the formula — not from `t["final_pl"]`. This means the display is correct even for trades where the stored `final_pl` value is stale or wrong from a previous manual entry.

### 14.7 Scanner — Row-Level View Chart Navigation

The Full Scanner table (inside the expander) now supports one-click navigation:
- `on_select="rerun"`, `selection_mode="single-row"` on `st.dataframe`.
- Selecting a row reveals a summary line + "→ View Chart" button.
- Clicking the button stages the combo into `pending_*` session-state keys, sets `active_tab = "edge"`, and calls `st.rerun()`.
- Calendar Edge opens pre-scoped to that exact strike/expiry combination.
- Uses the same `pending_*` → `*_select` promotion pattern as Mission Control cards.

### 14.8 New Local Preference Files (v4.0)

| File | Purpose | Git status |
|------|---------|-----------|
| `eligible_history.json` | Non-ATM crossing registry | **Add to `.gitignore`** |
| `chart_colors.json` | User color preferences | Already in `.gitignore` |

*End of Section 14 — added in DOCUMENTATION.md v1.7 (2026-06-30)*


---

## 15. Dashboard v4.1 — Design Language, Strike Channel, Performance, Stabilization & Silent Refresh

*Added in DOCUMENTATION.md v1.8 (2026-07-03). This section is the in-depth companion to the v4.1 overview in DEV_JOURNAL.md.*

### 15.0 Scope discipline (what did NOT change)

Every change in v4.1 is one of three kinds: **presentation** (CSS, chart styling, tooltips), **data-plumbing** (connection lifecycle, caching, refresh mechanism), or **data-integrity** (duplicate prevention). No options math, IV-engine formula, scanner logic, Mission Control ranking, or Trade Journal P&L rule was modified. The `$5.00` transform threshold and the demotion of the Trade Quality Score to raw metrics both stand unchanged (see §9 and §13).

---

### 15.1 Design-language system

The dashboard previously rendered charts directly onto the page background, which read as flat. v4.1 introduces a small, reusable visual vocabulary.

**Chart cards.** Any chart can be given its own bordered, elevated panel by wrapping its render in a keyed container:

```python
with st.container(key="chartcard_gap"):
    st.plotly_chart(fig_gap, use_container_width=True)
```

Streamlit emits the key as a CSS class `st-key-chartcard_gap` on the container element. A single wildcard rule styles every such card, so adding a new carded chart needs no new CSS:

```css
div[class*="st-key-chartcard_"] {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 1.05rem 1.15rem .8rem;
  box-shadow: var(--shadow);
}
```

Cards in use: `chartcard_gap` (Diagonal vs. Transform), `chartcard_spx` (Strike Channel), `chartcard_stack` (IV term structure), `chartcard_atm` (dual-axis IV + ratio), `chartcard_intra` (IV scatter). Each chart's Plotly `paper_bgcolor`/`plot_bgcolor` was changed from the page color `#060b12` to the card interior `#0c1421` so the plot sits *inside* the card rather than punching through it.

**Explanation callouts.** A helper renders an unobtrusive "what am I looking at" note beneath a chart:

```python
def _render_note(text: str, *, kind: str = "info", icon: str | None = None) -> None:
    ...  # renders <div class="note"> / <div class="note good">
```

Styled by `.note` (blue left-accent) and `.note.good` (green). Used sparingly — at most one per chart — to keep information density high per the design philosophy in §8.

**Caption strip.** `.chart-cap` renders a thin legend/aside line under a chart (e.g. "Filled band = live Gap … ▲▼ = Diagonal crosses Transform").

**Always-on Gap fill.** On the Diagonal vs. Transform chart, the area *between* the two lines is now shaded — the Gap drawn directly, so it reads at a glance instead of by eye-comparing two lines. It is implemented as `fill="tonexty"` on the Transform trace (filling down to the Diagonal trace), in **discovery mode only** (in position-management/lock mode the chart tells a different story — distance vs. a fixed entry — so no band). The pre-existing bright-green ≥-threshold shading layers on top, giving two honest signals in one fill: *there is always a gap* and *the gap is currently transformable*.

**KPI panel.** The four Calendar Edge KPIs (ATM IV Ratio, Front/Back ATM IV, IV Index) were given their own bounded panel rather than floating on the bare background, with matching card elevation.

---

### 15.2 Strike Channel subplot

**Problem it solves.** The Diagonal and Transform marks move primarily because SPX moves relative to the selected strikes. Plotting raw SPX underneath does not help — what matters is *where SPX sat relative to the position*, not its absolute level. The Strike Channel answers: **"where was SPX relative to my strikes when these option values changed?"**

**Design chosen (and alternatives rejected).** Candidates considered were: distance-from-strike as two lines; a normalized "wings from strike" scale; moneyness; and a raw SPX overlay. The chosen design plots **SPX as a literal price line through a shaded band bounded by the two short strikes**, because (a) price reads natively — the user already thinks "SPX is at 7480, my call is 7450" — with no mental translation, and (b) the band makes "inside the position vs. a short strike being tested" obvious at a glance. Normalization was explicitly deferred to a future cross-trade comparison view (it loses the point value that P&L is denominated in). A composite position score was rejected on the standing "no unvalidated blended metrics" principle.

**Placement & axis sync.** The channel is a **separate Plotly figure** (`fig_spx`) rendered in its own `chartcard_spx` immediately beneath the gap chart. It shares the gap chart's x-axis configuration (`_gap_xaxis`, which carries the session `rangebreaks` and autoranges off the same `_gap_df["timestamp"]`) and the same left/right margins (`_SYNC_MARGIN_L`, `_SYNC_MARGIN_R`). Identical margins + identical x-domain ⇒ the two panels align vertically, so a spike in the gap chart and SPX poking out of the band fall on the same vertical.

**Components:**
- **SPX line** — light gray `#d7deea`, width 2, so it doesn't compete with the blue/amber/green already spoken for.
- **Strike band** — `add_hrect(y0=min(strikes), y1=max(strikes))` in neutral slate `rgba(124,148,199,0.09)`, `layer="below"`. Deliberately **not** green: SPX being inside the channel is a fact, not a validated favorable signal.
- **Strike reference lines** — dashed `#4a5d80` `add_hline` at the put and call strikes, labelled `"7400 P"` / `"7450 C"` on the right.
- **Directional crossing markers** — `▲` (`triangle-up`) where SPX crosses **up** through a strike, `▼` (`triangle-down`) for down, in amber to match the crossing vocabulary on the gap chart. Direction is carried by shape alone (no green/red — favorability isn't validated).
- **Market-open dividers** — reuses `_add_market_open_lines(fig_spx, …)` so multi-day views get the same 09:30 markers.
- **Y-range** — padded around both the band and the SPX path so the band never collapses to a sliver.

**Hover.** SPX line hover shows `SPX: 7,503.20`, `vs Put: +103`, `vs Call: +53` via `customdata`. (Distance-to-strike was removed from the *gap* chart tooltip but belongs here, where it is the chart's whole purpose.)

**Data cost.** Zero new queries — the channel reads the `spx` column already present in the cached `_gap_df` (from `get_transform_mark_history`).

---

### 15.3 Tooltip architecture (Diagonal vs. Transform chart)

**Requirement (from the design pass).** The gap-chart tooltip must contain, in this order: **SPX, Diagonal Mark, Transform Order Mark, Gap**.

**Why a naive approach failed.** Under `hovermode="x unified"`, Plotly does not reliably honor trace insertion order, and with SPX sitting on a hidden secondary axis the order silently reversed (observed as *Gap, Transform, Diagonal, SPX*). Relying on trace/axis order is fragile.

**Solution — a single master tooltip.** All four values are carried by **one** trace (Transform), via `customdata`, with a fixed multi-line `hovertemplate`:

```python
_master_cd = np.column_stack([
    _gap_df["spx"], _gap_df["diagonal_mark"],
    _gap_df["transform_mark"], _diff_series,
])
_master_ht = ("SPX: %{customdata[0]:,.2f}"
              "<br>Diagonal Mark: $%{customdata[1]:.2f}"
              "<br>Transform Order Mark: $%{customdata[2]:.2f}"
              f"<br>{_diff_hover_label}: $%{{customdata[3]:.2f}}<extra></extra>")
```

Every other trace (the SPX axis-2 trace, the Diagonal line, the invisible diff trace, the carets) has its hover suppressed after the figure is built:

```python
for _tr in fig_gap.data:
    if _tr.name != "Transform Order Mark":
        _tr.hoverinfo = "skip"
        _tr.hovertemplate = None
```

The result is exactly four lines in a guaranteed order, independent of Plotly's internal ordering. In position-management (lock) mode the fourth line's label becomes "Live Difference (vs. entry)"; the order is unchanged.

---

### 15.4 Performance — connection & caching model

Three problems were diagnosed and fixed. All symptoms shared a root family: heavy, write-touching, uncached work running on every Streamlit rerun against a WAL database the collector is actively writing.

**(a) `init_db` was writing on every rerun → the Ctrl+C hang.** `init_db()` executes the full DDL script and **commits a write transaction**. It was being called at the top of the script, so every rerun (tab click, widget change, autorefresh tick) issued a database write, contending with the collector's write lock; a blocked connection with `timeout=15` meant a `Ctrl+C` during that wait stalled for up to 15 s. Fixed by running it once per process:

```python
@st.cache_resource(show_spinner=False)
def _init_db_once(_db_path: str) -> bool:
    db.init_db(_db_path)
    return True
_init_db_once(config.DB_PATH)
```

Because the dashboard is otherwise a pure reader and WAL lets readers run concurrently with the writer, removing this single per-rerun write is what eliminated the hang.

**(b) Read-only, non-committing connections.** All read helpers had been routed through `managed_conn`, a context manager intended for *writes* that runs `conn.commit()` on every exit — so every `SELECT` did a pointless commit. `db.py` was changed as follows:
- `_make_conn(db_path, *, read_only=False)` — when `read_only=True`, sets `PRAGMA query_only = ON` (the connection physically cannot take a write lock) and skips the WAL/foreign-key PRAGMAs.
- `get_conn` (the read context manager) now opens read-only and **does not commit**.
- The **20 pure-read `get_*` functions** were repointed from `managed_conn` to `get_conn`.
- **Bug fixed along the way:** `delete_trade` (a `DELETE`) was wrongly using the read context manager; it now uses `managed_conn`.

Writers (collector inserts, journal writes, `init_db`) are untouched and still use `managed_conn` / direct write connections.

**(c) Snapshot-keyed read caches.** The option chain was re-queried and rebuilt into a DataFrame on every rerun, and per-tab history reads fired each time. Six cached loaders were added, each keyed on `snapshot_id` (the only thing that changes their result), so tab clicks within one snapshot are cache hits:

| Loader | Wraps | Cache key(s) |
|--------|-------|--------------|
| `_load_chain_df` | `get_option_chain` + DataFrame build | `snapshot_id` |
| `_load_spx_intraday` | `get_spx_intraday_today` | `session_date, snapshot_id` |
| `_load_prior_close` | `get_prior_session_close` | `session_date` |
| `_load_transform_marks` | `get_transform_mark_history` | strikes, expiries, days, `snapshot_id` |
| `_load_latest_atm_iv` | `get_latest_atm_iv_snapshots` | `exp_date, snapshot_id` |
| `_load_diagonal_hist` | `get_diagonal_history` | strikes, expiries, days, `snapshot_id` |

`st.cache_data` returns a fresh copy on each call, so downstream code that mutates these frames (adding `diagonal_mark`, `timestamp`, etc.) cannot corrupt the cache. Per-tab **lazy loading was already in place** — tabs dispatch via `if st.session_state["active_tab"] == …`, so a tab's history queries only run when it is active; no restructuring was needed.

**(d) Bounded cache growth → fixes the long-session slowdown.** None of the `@st.cache_data` loaders capped their size, and several snapshot-keyed ones (the full chain frame, the scanner, Mission Control) had no `ttl`, so every new snapshot (every 60–300 s) minted new entries that were never evicted — memory grew all session, degrading performance. `max_entries` (and a `ttl` backstop where missing) was added to **all twelve** cached loaders:

| Loader group | `ttl` | `max_entries` |
|--------------|------:|--------------:|
| `_load_chain_df`, `_load_spx_intraday`, `_load_prior_close`, `_compute_mc_core` | 120–300 s | 3 |
| `_compute_transform_scanner` | 120 s | 8 |
| `_load_transform_marks`, `_load_latest_atm_iv`, `_load_diagonal_hist` | 55 s | 32 |
| `_load_atm_hist`, `_load_atm_hist_fb`, `_load_contract_hist` | 55 s | 48 |
| `_candidate_signals` | 60 s | 64 |

---

### 15.5 Data integrity — `option_rows` uniqueness & the sawtooth fan-out

**Symptom.** The Diagonal vs. Transform chart rendered as a tight, regular **sawtooth band** with the correct value range — most visible on wide / far-OTM strike pairs.

**Root cause (not markers, not scaling, not ordering).** `option_rows` had **no uniqueness guarantee** on `(snapshot_id, expiry_date, strike, right)` — its only key is an autoincrement `id`. A re-fetch or overlapping poll could therefore store the same contract more than once within a single snapshot. The mark-history queries (`get_transform_mark_history` — six legs; `get_diagonal_history` — four legs + two ATM-IV joins) use `LEFT JOIN`s on those columns, so duplicates **fan out**: one snapshot emits several rows with identical marks, and the line connects them high→low→high→low. Consistent duplicate structure ⇒ regular sawtooth. Wide/OTM strikes place the ±5 wing legs near the edge of the collector's fetch window where duplicates are likelier, which is why those pairs were worst (and also why they were **slow** — more rows to materialize; this was issue #3).

**Two-layer fix:**

1. **Query level (all callers benefit):** `GROUP BY s.snapshot_id` added before `ORDER BY s.snapshot_timestamp` in both history queries. Duplicates within a snapshot are identical (same poll), so grouping collapses the fan-out to the correct single row per snapshot.

2. **Foundational (prevents recurrence at the data core):**
   - `init_db` runs a **one-time, guarded migration**: if the unique index is absent, it deletes duplicate rows keeping the earliest per contract, then creates the index:
     ```sql
     DELETE FROM option_rows WHERE id NOT IN (
       SELECT MIN(id) FROM option_rows
       GROUP BY snapshot_id, expiry_date, strike, right);
     CREATE UNIQUE INDEX uq_option_rows_contract
       ON option_rows(snapshot_id, expiry_date, strike, right);
     ```
     Guarded on the index's existence, so the (potentially expensive) `DELETE` runs only the first time and logs the number of rows removed.
   - `insert_option_rows` now uses `INSERT OR IGNORE`, so a re-fetched contract is a silent no-op instead of a duplicate. The collector needs no change — all inserts flow through this function.

**Operational note.** The **first launch after this change** runs the one-time dedupe `DELETE`, which may take a moment on a database with many historical duplicates. Subsequent launches are no-ops.

---

### 15.6 Far-OTM load performance & instrumentation

Far-OTM strike selection was slow. It was the **same fan-out** (wider strikes → more duplicate rows → larger result set). The foundational fix resolved it: a genuinely far-OTM pair (≈ call +144 / put −186 points from spot) now loads its marks in **~56 ms** (query) with an ~80 ms chart build.

A lightweight timing caption was added beneath the gap chart to keep this observable:

```
⏱ marks query 56 ms · chart build 80 ms · 177 pts · call +144 / put -186 pts from spot
```

A cache **miss** (a new/OTM pair) shows the true query cost; a cache **hit** reads ~0 ms. This separates *data retrieval* from *chart generation* so any future regression can be attributed correctly. The caption is diagnostic and may be removed once no longer needed.

---

### 15.7 Silent, change-triggered refresh (fragment poller)

**Problem.** `st_autorefresh` forced a **full-page rerun** every `poll_interval` (60 s open/close, 300 s midday) regardless of whether new data existed — resetting charts (losing Plotly zoom/pan) and interrupting analysis. Over long sessions this, combined with unbounded caches, produced the "gets sluggish then does a noticeable refresh" complaint.

**Fix.** The blind autorefresh is replaced by a background **fragment** that polls for a new completed snapshot and reruns the app **only when the snapshot id changes**:

```python
_LIVE_POLL_SECONDS = max(5, min(int(poll_interval), 20))
_fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)

if _fragment is not None:
    @_fragment(run_every=_LIVE_POLL_SECONDS)
    def _live_refresh_poller():
        _snap = db.get_latest_complete_snapshot(config.DB_PATH)   # cheap indexed read
        _latest_id = _snap["snapshot_id"] if _snap else None
        if _latest_id is not None and _latest_id != st.session_state.get("_active_snapshot_id"):
            st.rerun()
    _live_refresh_poller()
else:
    st_autorefresh(interval=poll_interval * 1000, key="autorefresh")   # fallback
```

The main render records the snapshot it was built on: `st.session_state["_active_snapshot_id"] = snapshot_id`.

**Behavior matrix:**

| Situation | What the poller does |
|-----------|----------------------|
| Mid-analysis, same snapshot | Ticks, finds no change, does nothing → **no rerun, no chart reset** |
| After hours (collector idle) | Same — page stays completely static |
| New snapshot lands | One clean full-app rerun refreshes all data, within ~20 s |

**Known limitation.** When a new snapshot *does* arrive, the rerun is still full-page, so charts re-render (a zoomed Plotly view resets) at that moment. Truly in-place updates would require moving the charts themselves into fragments — deferred. The win here is that refreshes now happen **only on real data changes**, not on a blind timer. A `getattr` shim keeps a fallback to `st_autorefresh` for any Streamlit build lacking the fragment API.

---

### 15.8 Files & surfaces touched in v4.1

| File | Changes |
|------|---------|
| `app.py` | Chart-card CSS + `_render_note` + `.chart-cap`; gap-fill, master tooltip, Strike Channel figure; `_init_db_once`; six cached loaders + `max_entries`/`ttl` on all caches; fragment refresh poller + `_active_snapshot_id`; timing caption. |
| `db.py` | `_make_conn(read_only=…)` + `query_only`; `get_conn` read-only/non-committing; 20 read fns repointed; `delete_trade` write-path fix; `GROUP BY s.snapshot_id` in both history queries; `init_db` dedupe migration + `UNIQUE` index; `insert_option_rows` → `INSERT OR IGNORE`. |

### 15.9 Deferred / follow-ups

- In-place silent refresh (charts inside fragments) so zoom/pan survives a new snapshot.
- Remove the ▲/▼ carets from the Diagonal/Transform chart (low information once data is clean; the meaningful crossings live on the Strike Channel).
- Live calibration of the `$5.00` transform threshold toward `$6.50–$7.00` from real fills.
- Full Journal integration for entry locks (`journal_trade_id` scaffold already present).

*End of Section 15 — added in DOCUMENTATION.md v1.8 (2026-07-03)*
