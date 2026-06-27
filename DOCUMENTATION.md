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

---

## 1. Document Change Log

| Version | Date | Author | Summary of Changes |
|---------|------|--------|--------------------|
| 1.0 | 2026-06-25 | Chandan Singh | Initial documentation through Dashboard v1 (IV Structure, Calendar Edge, Transform Credit panels). |
| 1.1 | 2026-06-25 | Chandan Singh | **Critical audit corrections.** (1) Retracted the claim that IV ratio < 1.0 is "favorable" / "maximizes transformation credit" — it rested on a single paper trade and Black-Scholes analysis suggests the reverse; demoted to an unvalidated `HYPOTHESIS` with neutral framing. (2) Fixed inverted/contango terminology to standard conventions. (3) Corrected the transformation workflow (keep shorts, close backs, add front-expiry wings). (4) Fixed expiry collection (20 expirations by count, not within 20 DTE). (5) Reframed 100-pt strike width as an example, not a rule. (6) Removed the Theta ETA metric (assumption-based). (7) "Risk-free" → "risk-reduced." (8) Flagged Greeks-sign, Trade-Quality-direction, liquidity-threshold, and IV-Index claims as unvalidated. (9) Added approved trade-logging schema to the roadmap as the validation mechanism. Authority statement softened to exclude HYPOTHESIS blocks. |
| 1.2 | 2026-06-26 | Chandan Singh | **Dashboard v2 — complete layout rewrite.** (1) Replaced single-pair static layout with a Pair Scanner showing all valid (front, back) expiry combinations from the current session, ranked by intraday Drop%. (2) Added Pinned Pairs persistent watchlist (`pinned_pairs.json`). (3) Added GEX display (Max \|Net GEX\| strike + call/put dominated) computed from `option_rows.gamma` — no schema change required. (4) SPX daily change corrected to use prior session's last COMPLETE snapshot (`get_prior_session_close`) rather than the first intraday snapshot. (5) Mini SPX intraday sparkline embedded in the header. (6) Expiry Detail + Strike Detail panel restored (ATM IV + tick-change per expiry; per-leg IV + mark price at selected strikes). (7) Calendar Edge moved above Historical Statistics — now immediately follows the IV chart. (8) Max Gap filter moved from Controls Row to the Pair Scanner filter row. (9) Three new DB read functions added: `get_prior_session_close`, `get_spx_intraday_today`, `get_all_expiry_atm_iv_today`. (10) Section 5 of this document fully rewritten for v2. |
| 1.3 | 2026-06-26 | Chandan Singh | **Dashboard v3 — layout polish + IV-ratio/level analytics.** Layout: (1) compaction CSS (tighter top padding + section rhythm); (2) removed the header sparkline; (3) moved the `pts ↔ %` toggle beside the change value; (4) DTE shown in both expiry dropdowns, e.g. `2026-06-29 (3D)`; (5) Expiry Detail shows date + DTE together; (6) the Today/5D/10D/20D selector moved to the right of the chart (shared across both charts). Multi-day charts: (7) non-trading time (overnight/weekend/holiday via `config.MARKET_HOLIDAYS`) collapsed with Plotly `rangebreaks` so multi-day lines are continuous, not diagonal ramps. New analytics (Calendar Edge, additive — the original dual-axis chart is **kept**): (8) a **stacked panel** (Front/Back IV on one axis + a regime-colored ratio line); (9) a **Front-vs-Back scatter** colored by time of day. New **Regime Analysis** sub-tab on the Trade Journal page: (10) a Front-vs-Back entry scatter split into four quadrants by **median level (√(F·B))** and **median ratio**, colored by realized transform credit, plus a stratified 2×2 cell-mean table — the test for whether IV Ratio adds outcome information beyond IV level. (11) New DB read helper `get_entry_iv_context` reconstructs entry-time term structure from snapshots (no schema change; works retroactively). See new Section 11 for detailed explanations and examples. |
| 1.4 | 2026-06-27 | Chandan Singh | **Dashboard v3.1 — Trade Journal CRUD, guided edit wizard, direct-close path, live IC P&L, P&L terminology standardisation.** Added Section 12 covering: trade lifecycle (Open/Transformed/Closed/Expired), `close_type` field, full `trades` table schema, P&L terminology definitions (Realized/Unrealized/Net), strategy statistics formulas, all CRUD operations, the two-step guided edit wizard, unsaved-changes protection, inspect-trade auto-navigation, live IC per-leg P&L, and new code surfaces in `pages/journal.py` and `db.py`. |

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

> **Version note:** this section describes Dashboard v2 (current). The v1 layout (two-column with sidebar panels) is retired. The v2 layout is a single linear flow — all sections are full-width or use an explicit 2-column split.

### 5.1 Header

| Element | Source | Notes |
|---------|--------|-------|
| SPX price | `snapshots.underlying_price` | Latest COMPLETE snapshot |
| Daily change (pts or %) | `current − get_prior_session_close()` | Previous session's last COMPLETE snapshot ≈ official close. Toggle between points and % with the pts↔% button. Falls back to first intraday snapshot if no prior-session data exists. |
| Mini sparkline | `get_spx_intraday_today()` | 60px Plotly chart, no axes, green/red line, embedded in the SPX price column. |
| VIX | `snapshots.vix_value` | |
| Max \|GEX\| Strike | Computed from `option_rows.gamma × option_rows.open_interest × 100 × SPX_price × ±1` | Aggregated by strike across all expirations in the chain. Shows the strike with largest absolute net GEX and whether it is call-dominated or put-dominated. Computed from the ±300pt strike window only. |
| Staleness | `(now − snapshot_timestamp).seconds` | 🟢 <10 min, 🟡 10–60 min, 🔴 >60 min |

### 5.2 Controls Row

Four columns: **Front Expiry**, **Back Expiry**, **Call Strike**, **Put Strike**.

Same call/put strike applies to both front and back legs. Defaults are convenience values, not recommendations. A brief per-leg summary line appears below the controls: `IV → F x.xx% / B x.xx% · Ratio x.xxxx`.

> **Max Gap is not here.** It lives in the Pair Scanner filter row (§5.8). It is a scanner parameter, not a trade-setup parameter.

### 5.3 Period Radio

Range selector: **Today / 5D / 10D / 20D**. Controls the time window for both the Selected-Strike IV chart (§5.4) and the Calendar Edge ATM chart (§5.5). Historical Statistics (§5.6) always displays all four windows independently and is not affected by this control.

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

Always shows **Today / 5 Days / 10 Days / 20 Days** — four range bars, one per column. Not controlled by the period radio.

Each bar shows: `[low ──●── high]` where ● is the current ATM IV ratio position within the range. Formula: `(current − low) / (high − low) × 100` (see §6.10). **Descriptive only** — a position near an extreme is not labeled good or bad.

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

### 5.9 Transform Credit

Bottom panel. Placeholder pending Phase 3.

| Element | Formula | Status |
|---------|---------|--------|
| Overall Score | `0.45×IV_Edge_Pct + 0.30×Liquidity + 0.25×Theta_Adv` | Non-authoritative (§5.10 caveat applies) |
| IV Edge (percentile) | Percentile rank of current ATM ratio vs `period_days` history | Direction unvalidated — see §9.4 |
| Liquidity | `min(Vol/500,1)×50 + min(OI/2000,1)×50` | Thresholds unvalidated — see §6.7 |
| Theta Adv. | 50 (fixed placeholder) | Will be `Net Theta Advantage` in Phase 3 |

Full transform credit calculator (back-leg value − wing cost − entry debit) deferred to Phase 3 once per-leg Greeks are confirmed reliable.

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

All columns in full, as of v3.1:

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

---

*End of Section 12 — added in DOCUMENTATION.md v1.4 (2026-06-27)*
