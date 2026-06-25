# DOCUMENTATION.md
# SPX Diagonal Calendar Analyzer — Project Reference Manual

> **Canonical Authority:** This document is the authoritative source of truth for the
> SPX Diagonal Calendar Analyzer project. In any conflict between this document,
> dashboard labels, source code, DEV_JOURNAL.md, or any future conversation,
> **this document governs**. Update this document whenever a definition, formula,
> or design decision changes.

---

## Table of Contents

1. [Document Change Log](#1-document-change-log)
2. [Project Overview](#2-project-overview)
3. [Strategy Documentation](#3-strategy-documentation)
   - 3.1 [Diagonal Calendar Spread](#31-diagonal-calendar-spread)
   - 3.2 [Transformation to Iron Condor](#32-transformation-to-iron-condor)
4. [Trading Concepts Reference](#4-trading-concepts-reference)
5. [Dashboard Reference](#5-dashboard-reference)
   - 5.1 [Header Strip](#51-header-strip)
   - 5.2 [Selector Row](#52-selector-row)
   - 5.3 [IV Structure Panel](#53-iv-structure-panel)
   - 5.4 [Calendar Edge Panel](#54-calendar-edge-panel)
   - 5.5 [Transform Credit Panel](#55-transform-credit-panel)
   - 5.6 [ATM Term Structure Strip](#56-atm-term-structure-strip)
   - 5.7 [Selected-Strike IV Chart](#57-selected-strike-iv-chart)
   - 5.8 [ATM IV Chart](#58-atm-iv-chart)
   - 5.9 [Historical Range Stats](#59-historical-range-stats)
   - 5.10 [Trade Quality Score](#510-trade-quality-score)
   - 5.11 [Options Chain Table](#511-options-chain-table)
6. [Mathematical Definitions](#6-mathematical-definitions)
7. [Data Architecture Reference](#7-data-architecture-reference)
8. [Dashboard Design Philosophy](#8-dashboard-design-philosophy)
9. [Assumptions and Known Limitations](#9-assumptions-and-known-limitations)
10. [Future Roadmap](#10-future-roadmap)

---

## 1. Document Change Log

| Version | Date | Author | Summary of Changes |
|---------|------|--------|--------------------|
| 1.0 | 2026-06-25 | Chandan Singh | Initial documentation. Covers full project architecture through Dashboard v1. Includes all three analytics panels (IV Structure, Calendar Edge, Transform Credit), regime classification system, transform credit formula from forensic analysis, data architecture, and design philosophy decisions made through the 2026-06-23 paper trade forensic. |

---

## 2. Project Overview

### 2.1 What This Project Is

The **SPX Diagonal Calendar Analyzer** is a personal, locally-hosted options analytics dashboard designed for a single trader executing diagonal calendar spread strategies on the SPX index. It runs as a Streamlit web application on a local Windows machine, reads live options chain data collected from the Charles Schwab Developer API, and displays real-time analytics to support two specific decisions:

1. **Is now a good time to enter a diagonal calendar spread on SPX?**
2. **Has the position reached the point where it can be transformed into a near-risk-free Iron Condor?**

It is not a general-purpose options screener. It is not a trade execution system. It is a decision engine built around one specific strategy with two specific decision gates.

### 2.2 Why It Exists

Standard brokerage platforms (ThinkOrSwim, Schwab StreetSmart Edge, tastytrade, etc.) show you the current options chain. They do not:

- Show you how implied volatility is structured *across expiries at the same strike* in real time
- Track whether the IV term structure is in a regime that is historically favorable for your strategy
- Calculate the theoretical transformation credit (the dollar amount you would lock in if you transformed to an Iron Condor right now)
- Project how long until a transformation becomes viable based on theta decay
- Record IV history per-strike so you can see how the structure has been drifting over the last 30 minutes, 5 days, or month

This dashboard is inspired by the FLUX analytics product from NavigationTrader, focused specifically on the metrics relevant to this strategy.

### 2.3 The Three Phases of the Strategy

Understanding the dashboard requires distinguishing three distinct activities:

**Phase 1 — Entry**
Opening a new diagonal calendar spread. The decision is driven by IV term structure: is the structure currently in a regime that offers structural edge? The IV Structure Panel, Calendar Edge Panel, and ATM Term Structure Strip all inform this decision.

**Phase 2 — Monitoring**
Watching an open position as the market moves and IV evolves. The Transform Credit Panel is the primary monitoring tool. It answers: "How much profit is locked in if I transform right now?"

**Phase 3 — Transformation**
Converting the diagonal into an Iron Condor (or closing the position) once a profit threshold has been reached. The dashboard shows when the threshold is crossed but does not execute trades. The trader executes the transformation manually through their brokerage.

### 2.4 What "Risk-Free Transformation" Means

A transformation is considered **risk-free** when the theoretical credit received from transforming the diagonal into an Iron Condor exceeds the maximum possible loss on the resulting Iron Condor structure.

In practice for this strategy, the threshold is defined as:

> **Theoretical Credit ≥ Threshold ($5.00 paper / ~$6.50–$7.00 live)**

After transformation, the Iron Condor has a defined maximum loss. If the profit already locked in equals or exceeds that maximum loss, the worst-case scenario is break-even — the trade cannot lose money regardless of where SPX goes. This is what "risk-free" means in this context.

**Important:** "Risk-free" here means risk-free *on this position after transformation*. It does not mean the transformation is guaranteed to be executed at the dashboard's theoretical credit value. Fill slippage across four legs is the primary reason the live threshold is higher than the paper-trade threshold. See Section 9 for full assumptions.

---

## 3. Strategy Documentation

### 3.1 Diagonal Calendar Spread

#### Structure

A diagonal calendar spread involves four contracts on the same underlying (SPX), using two expiration dates and two strikes:

| Leg | Action | Strike | Expiry | Premium |
|-----|--------|--------|--------|---------|
| Short Call | Sell | Call Strike (OTM) | Front (near-term) | Collect |
| Short Put | Sell | Put Strike (OTM) | Front (near-term) | Collect |
| Long Call | Buy | Call Strike (same) | Back (far-term) | Pay |
| Long Put | Buy | Put Strike (same) | Back (far-term) | Pay |

**Key constraint:** The call strike is the same on both the front and back legs. The put strike is the same on both legs. This is not always true for diagonal spreads in general, but it is the specific structure used by this strategy.

#### Example

SPX is trading at 7,478.

- **Front expiry:** June 26 (2 DTE)
- **Back expiry:** June 29 (5 DTE)
- **Call strike:** 7,500 (OTM call, above current price)
- **Put strike:** 7,400 (OTM put, below current price)

Legs:
- Sell 1 × June 26 7500C @ $1.20 → collect $120
- Sell 1 × June 26 7400P @ $0.95 → collect $95
- Buy 1 × June 29 7500C @ $5.60 → pay $560
- Buy 1 × June 29 7400P @ $4.65 → pay $465

**Net debit = (amount paid for back legs) − (amount collected from front legs)**
= ($560 + $465) − ($120 + $95) = $1,025 − $215 = **$810 per spread** (or $8.10 in option premium units where 1 point = $100)

In option premium units, this would be: (5.60 + 4.65) − (1.20 + 0.95) = 10.25 − 2.15 = **$8.10 net debit**.

The dashboard uses premium units (not dollar units) throughout. $8.10 means a $810 real-money cost per spread.

#### Long Legs vs Short Legs

**Short legs (front month):** Options you have sold (short). You are obligated to fulfill these contracts if they are exercised. For SPX (cash-settled), this means paying the intrinsic value at expiration if the option is in-the-money. The short legs generate theta income (they decay in your favor as time passes).

**Long legs (back month):** Options you have bought (long). You own these contracts and benefit when they gain value — either from SPX moving toward or past the strike (delta gain), from IV increasing in the back month (vega gain), or simply from their intrinsic value as the front legs expire. The long legs protect the short legs from unlimited risk (the back call caps the short call risk; the back put caps the short put risk).

#### Expiration Selection

- **Front expiry:** Near-term, typically 1–5 DTE. Short enough for rapid theta decay on the short legs.
- **Back expiry:** Far enough to retain meaningful time value, typically 3–10 DTE beyond the front. Chosen for IV structure (see Section 4) rather than a fixed DTE gap.

The dashboard collects data for all expirations within 20 calendar days from today (configured as `MAX_EXPIRY_DTE = 20`), covering approximately 10–11 SPX expirations (Mon/Wed/Fri weeklies).

#### Strike Selection

- **Call strike:** OTM above current SPX price, giving the short call some cushion before going in-the-money.
- **Put strike:** OTM below current SPX price, same logic.
- **Typical spread width:** 100 points (e.g., 7500 call, 7400 put when SPX is at ~7,450).

The dashboard defaults call strike to nearest 5-point increment above SPX, put strike to SPX − 100.

#### Greeks Exposure

| Greek | Front Short Legs | Back Long Legs | Net Position |
|-------|-----------------|----------------|--------------|
| Delta | Slightly negative (short call) / slightly positive (short put) | Slightly positive (long call) / slightly negative (long put) | Near-neutral (market-neutral structure) |
| Theta | Positive (collect) | Negative (pay) | **Net positive theta** — time is working for you |
| Vega | Negative (short vega on front) | Positive (long vega on back) | **Net vega depends on regime** — see below |
| Gamma | Negative (short gamma) | Positive (long gamma) | Net slightly negative |

**Vega note:** When back IV > front IV (inverted term structure, ratio < 1.0), the back legs carry more vega exposure than the front shorts. This is why the dashboard tracks the IV term structure so carefully — the regime determines whether your overall position benefits or suffers from IV changes.

---

### 3.2 Transformation to Iron Condor

#### Why Transform?

When the diagonal has appreciated significantly (the back legs have gained value and/or the front legs have decayed), you can "lock in" that profit by converting the structure to an Iron Condor. The Iron Condor has:

- **Defined maximum loss** (limited downside)
- **Defined maximum profit** (collected premium)
- **Near-zero downside if the locked credit ≥ max loss**

This is the "risk-free" transformation described in Section 2.4.

#### Conditions Required

The transformation is triggered when:

**Theoretical Credit ≥ Transform Threshold**

Where:
- Theoretical Credit = back_legs_value − close_cost − entry_debit (see Section 6)
- Transform Threshold = $5.00 (current paper-trade setting, sidebar-configurable)

The dashboard's Transform Credit Panel shows this value in real time.

#### How the Transformation Works

The transformation involves closing or adjusting the diagonal legs and adding new wings:

**Step 1 — Close the short front legs**
Buy back the short front calls and puts (these are the Iron Condor's inner strikes / the body).

**Step 2 — Close or restructure the long back legs**
Depending on the specific adjustment strategy, the back long legs are either closed (taking profit) or converted to the outer wings of the Iron Condor.

**Step 3 — Add new protective wings (if creating an IC)**
Sell new OTM options slightly wider than the original strikes to define the Iron Condor's risk boundaries.

#### Resulting Iron Condor Behavior

After a standard transformation (closing the diagonal and opening a defined-risk IC structure):

- **Max profit:** The credit collected from the IC's short options
- **Max loss:** The spread width minus credit collected
- **Breakeven:** Defined by the strikes and credits — the structure cannot lose more than max loss

If the theoretical credit from the diagonal transformation ≥ the IC's maximum loss, the position is in profit regardless of outcome. This is the "risk-free zone."

#### Mathematical Example

Starting position:
- Entry debit: $9.00
- Back legs current value (marks): Call $8.10 + Put $6.15 = **$14.25**
- Front legs close cost (asks): Call $0.60 + Put $0.40 = **$1.00**
- Diagonal mark = $14.25 − $1.00 = **$13.25**
- **Theoretical Credit = $13.25 − $9.00 = +$4.25**

Threshold = $5.00. Status: ⏳ Watching ($0.75 below threshold).

After theta and/or favorable movement, if back legs grow to $15.50 and close cost stays at $1.00:
- Diagonal mark = $15.50 − $1.00 = $14.50
- **Theoretical Credit = $14.50 − $9.00 = +$5.50** ✅ Transform viable.

---

## 4. Trading Concepts Reference

Each entry below defines a term as it is used specifically within this project.

---

### Implied Volatility (IV)

**Plain English:** The market's current guess about how much SPX will move over a future period, expressed as an annualized percentage. Higher IV = market expects more movement = options are more expensive.

**Mathematical Definition:**
IV is the value σ (sigma) that, when plugged into the Black-Scholes formula, produces the observed market price for an option. It is back-solved from market prices rather than computed directly.

**Units:** Percentage (e.g., 18.5 means 18.5% annualized). The database stores IV as a decimal (0.185); the dashboard displays it multiplied by 100 (18.5%).

**Why It Matters:** IV is the primary driver of option premium. When IV is high, options cost more. When IV falls (IV crush), options lose value rapidly. The entire strategy's edge is based on the relationship between front-month and back-month IV.

**Dashboard Usage:** Displayed in IV Structure Panel (per-strike), Calendar Edge Panel (per-side differential), and ATM Term Structure Strip.

---

### IV Term Structure

**Plain English:** The pattern of implied volatility across different expiration dates for the same underlying and strike. Describes whether near-term options or longer-term options have higher IV.

**Why It Matters:** The shape of the IV term structure determines whether the diagonal calendar has structural edge. This project uses the IV Ratio as the primary measure of term structure.

---

### Normal Term Structure (Contango)

**Plain English:** Near-term IV < far-term IV. The curve slopes upward with time. Far-dated options carry more uncertainty, so they have higher IV. This is the most common regime for SPX under calm conditions.

**In This Project:** Contango (ratio > 1.0) is classified as **less favorable** for diagonal calendar entry. When you sell front options at lower IV and buy back options at higher IV, you are paying a higher IV premium for your protection than you are collecting.

**Regime Label:** `NEAR-PARITY` (1.00–1.05), `CONTANGO` (1.05–1.15), or `STEEP CONTANGO` (> 1.15).

---

### Inverted Term Structure (Backwardation)

**Plain English:** Near-term IV > far-term IV. The curve slopes downward with time. This typically happens during or after a spike event — the near-term uncertainty is elevated, while the market expects calm to return further out.

**Wait — which direction is favorable?**
In this project, the confirmed favorable regime is **ratio < 1.0**, where **back IV > front IV** (backwardation). This is counterintuitive relative to what you might expect, so it deserves careful explanation:

When back-month IV > front-month IV:
- Your long back legs carry higher IV → they are worth more in absolute terms
- When you transform the diagonal, the back legs contribute more to the theoretical credit
- The front legs (which you sold) carry lower IV → they are cheaper to buy back, reducing close cost

The net effect: inverted structure (back elevated relative to front) maximizes the theoretical transformation credit.

**Confirmed by:** 2026-06-23 paper trade forensic, where call IV ratio = 0.85 and put IV ratio = 0.82 (both inverted) and the trade was successfully profitable.

**Regime Label:** `INVERTED ●` (< 0.90) or `INVERTED` (0.90–1.00).

---

### IV Ratio

**Definition:** `IV_Ratio = Front_IV / Back_IV`

**Units:** Dimensionless ratio.

**Interpretation:**
- `< 1.00`: Inverted (back IV elevated) → **Favorable**
- `1.00–1.05`: Near-parity → Neutral
- `> 1.05`: Contango (front IV elevated) → Less favorable

**Dashboard Usage:** Primary metric in IV Structure Panel (per-strike), ATM Term Structure Strip (ATM-level), and Historical Range Stats.

---

### IV Spread

**Definition:** `IV_Spread = Front_IV − Back_IV`

**Units:** Percentage points (e.g., −3.2 means front IV is 3.2 percentage points below back IV).

**Difference from IV Ratio:** The spread is an absolute difference; the ratio is a relative difference. At low absolute IV levels, a small ratio difference corresponds to a very small spread. Both are tracked; the ratio is the primary metric.

---

### Calendar Edge

**Definition (per-side):**
- `Call_Edge = Front_Call_IV − Back_Call_IV`
- `Put_Edge  = Front_Put_IV  − Back_Put_IV`

**Units:** Percentage points.

**Interpretation:**
- Negative edge (front < back, back-elevated): **Favorable**
- Near-zero edge: Neutral
- Positive edge (front > back, contango): Less favorable

**Dashboard Usage:** Calendar Edge Panel shows call-side and put-side edge independently with today's trend. This allows you to see asymmetric opportunities — one side strengthening while the other weakens.

---

### Delta (Δ)

**Plain English:** How much the option's price changes for a $1 move in SPX. A delta of 0.25 means the option gains $0.25 for every $1 SPX rises (for a call).

**Units:** Per $1 of underlying move. Options have delta between −1 and +1.

**Dashboard Usage:** Shown in the Options Chain Table for reference. The diagonal is structured to be near-delta-neutral (the call and put deltas largely offset).

---

### Theta (Θ)

**Plain English:** How much an option loses in value per calendar day, all else equal. Also called "time decay." Theta is always negative for long options and positive for short options.

**Units:** Dollars per day (per contract).

**Why It Matters:** The diagonal collects theta from the short front legs while paying a slower theta on the long back legs. The net positive theta is the "carry" on the position — it gains value every day the position is held without adverse movement.

**Dashboard Usage:** Theta Advantage component in Trade Quality Score (currently a placeholder). Used in rough Theta ETA estimate in Transform Credit Panel.

---

### Vega (ν)

**Plain English:** How much the option's price changes for a 1 percentage point change in IV. A vega of 0.50 means the option gains $0.50 if IV rises 1%.

**Units:** Dollars per 1% IV move.

**Why It Matters:** The diagonal has net vega exposure (long vega from back legs, short vega from front legs). In an inverted regime, back IV > front IV, so changes in the overall IV level affect the back legs more.

---

### Gamma (Γ)

**Plain English:** How fast delta changes as SPX moves. High gamma means the position's directional exposure changes rapidly with price movement.

**Units:** Per $1 of underlying move, per $1 of underlying move (second derivative).

**Why It Matters:** The diagonal has net negative gamma (short the front, which has higher near-term gamma). Large rapid SPX moves hurt the position.

---

### Extrinsic Value (Time Value)

**Plain English:** The part of an option's price that is not intrinsic value. Essentially, what you are paying for the possibility that the option moves further in-the-money before expiration, plus the time for that to happen.

**Formula:** `Extrinsic_Value = Option_Price − Intrinsic_Value`

**Why It Matters:** Diagonal calendars are fundamentally a trade on extrinsic value. The short front legs decay their extrinsic value quickly (theta positive); the long back legs retain extrinsic value longer.

---

### Intrinsic Value

**Plain English:** The amount an option is in-the-money by. For a call, it is `max(0, SPX − Strike)`. For a put, it is `max(0, Strike − SPX)`. Out-of-the-money options have zero intrinsic value.

---

### DTE (Days to Expiration)

**Plain English:** Calendar days remaining until the option's expiration date.

**Dashboard Usage:** Shown in expiry selectors and expiry detail. Used in Theta ETA calculation and Expected Move check.

---

### Net Theta Advantage

**Plain English:** The net dollars per day the entire position gains from time decay, accounting for both the premium collected from short legs and the premium lost on long legs.

**Formula (conceptual):** `Net_Theta = |Theta_Short_Front| − |Theta_Long_Back|`

**Units:** Dollars per calendar day.

**Status:** Phase 3 — not yet implemented. Currently shown as placeholder (50/100) in Trade Quality Score.

---

### Transform Credit

**Plain English:** The dollar amount you would lock in right now if you closed the entire diagonal and declared victory. It is the correct answer to "how profitable is this position right now?"

**Formula:**
```
Transform_Credit = Back_Legs_Value − Close_Cost − Entry_Debit

Where:
  Back_Legs_Value = Back_Call_Mark + Back_Put_Mark
  Close_Cost      = Front_Call_Ask + Front_Put_Ask
  Entry_Debit     = What you originally paid to open the diagonal
```

**Why not just use the diagonal mark?**
The diagonal mark (`Back_Legs_Value − Close_Cost`) tells you what you could close the position for today. But to know your actual profit, you subtract what you paid to enter. The Transform Credit is the profit, not the position value.

**Dashboard Usage:** Primary display in Transform Credit Panel. Drives the ✅/⏳/⛔ status indicators.

---

### Net Locked Profit

**Synonym for Transform Credit.** Used interchangeably in discussions. The Transform Credit is the net amount locked in if you transform today.

---

### Risk-Free Threshold

**Definition:** The minimum Transform Credit at which the transformation is considered viable and creates a near-risk-free structure.

**Current Value (paper trades):** $5.00
**Expected Value (live trades):** $6.50–$7.00

The higher live threshold accounts for fill slippage across four legs. See Section 9.

---

### Transformation Score

**Status: Rejected.** An early design concept that would have combined multiple metrics into a 0–100 score for transformation viability. Rejected in favor of showing the raw Transform Credit dollar value directly. See Section 8 for rationale.

---

### SPX vs Strike Distance

**Definition:** `Distance = |SPX_Price − Strike|`

**Units:** Points.

**Dashboard Usage:** Planned for display in selector row. Currently inferred from default strike suggestions (call at SPX rounded up to nearest 5-point increment; put at SPX − 100).

---

## 5. Dashboard Reference

### 5.1 Header Strip

**Purpose:** Instant orientation on current market status.

**Components:**
| Element | Description | Formula / Source |
|---------|-------------|-----------------|
| SPX Price | Current index level | `snapshots.underlying_price` |
| VIX | CBOE Volatility Index value | `snapshots.vix_value` |
| Data Staleness | How old the latest snapshot is | `now() − snapshots.snapshot_timestamp` |
| Snapshot Timestamp | UTC timestamp of latest snapshot | `snapshots.snapshot_timestamp` |
| Refresh Rate | How often the dashboard re-reads the DB | `config.POLL_INTERVAL_NORMAL` (300s) or `POLL_INTERVAL_EVENT` (60s) |

**Staleness Color Coding:**
- 🟢 Green: < 10 minutes (collector is running normally)
- 🟡 Yellow: 10–60 minutes (possible slowdown or market close)
- 🔴 Red: > 60 minutes (collector likely offline)

**Limitations:** SPX price and VIX are as of the last snapshot, not truly real-time. At 5-minute polling, they can be up to 5 minutes stale during normal trading.

---

### 5.2 Selector Row

**Purpose:** Choose the expiries and strikes that define the diagonal you are analyzing.

**Components:**
| Element | Type | Default | Description |
|---------|------|---------|-------------|
| Front Expiry | Selectbox | Nearest expiry | The short (sold) leg expiry |
| Back Expiry | Selectbox | 2nd nearest expiry | The long (bought) leg expiry |
| Call Strike | Number input | Nearest 5-pt above SPX | Strike for call legs (both front and back) |
| Put Strike | Number input | SPX − 100, nearest 5-pt | Strike for put legs (both front and back) |

**Data Source:** Available expiries from `chain_df["expiry"].unique()` (loaded from `option_rows` for the latest snapshot). Strikes available in the same table.

**Design Note:** The same call strike applies to both the front short call and the back long call. Same for the put strike. This reflects the confirmed strategy structure (same strikes, different expiries).

---

### 5.3 IV Structure Panel

**Purpose:** Show per-strike IV ratio and regime classification for both the call and put sides of the selected diagonal, with a 30-minute history sparkline.

**Why Per-Strike (Not ATM):** ATM IV represents a floating reference that shifts as SPX moves. When you have selected specific strikes for a trade, the IV at *those strikes* is what determines the actual premium collected and paid. ATM IV is macro context; per-strike IV is the trade reality.

**Components per Side:**

| Element | Formula | Source |
|---------|---------|--------|
| IV Ratio | `Front_Strike_IV / Back_Strike_IV` | `option_rows.iv × 100` for selected strike/side/expiry |
| Regime Badge | See regime table below | Computed by `iv_engine.iv_regime()` |
| Front IV % | `option_rows.iv × 100` at front expiry, selected strike, selected side | `option_rows` |
| Back IV % | Same at back expiry | `option_rows` |
| 30-min Sparkline | Ratio history filtered to last 30 minutes | `option_rows` via `db.get_contract_iv_history()` |

**Regime Classification Table:**

| IV Ratio | Label | Color | Interpretation |
|----------|-------|-------|----------------|
| < 0.90 | `INVERTED ●` | `#00d97e` (emerald green) | Strong structural edge. Back IV well above front — maximize transformation credit opportunity. |
| 0.90–1.00 | `INVERTED` | `#4ecdc4` (teal) | Structural edge present. Favorable for entry and transformation. |
| 1.00–1.05 | `NEAR-PARITY` | `#ffd32a` (amber) | Flat structure. Reduced edge — monitor for regime shift before committing. |
| 1.05–1.15 | `CONTANGO` | `#ff9f43` (orange) | Front IV elevated. Compression on transformation credit. Caution on new entries. |
| > 1.15 | `STEEP CONTANGO` | `#ff4757` (red) | Unfavorable. Front IV significantly above back. Consider waiting for reversion. |

**Sparkline Design:** Periwinkle line (`#7b8cde`) with a `ratio=1.0` reference line in faint white. Shows last 30 minutes only. If fewer than 2 data points exist in the window, shows "30m history building..." — this is expected behavior on startup.

**Limitations:**
- If the selected strike does not exist in the chain (e.g., strike is between two available strikes), `iv_engine.strike_contract()` falls back to the nearest available strike and shows a warning.
- 30-minute sparklines require matching timestamps between front and back contract histories (joined on timestamp). If the collector missed a cycle for one leg, that sparkline point is dropped.

---

### 5.4 Calendar Edge Panel

**Purpose:** Show the IV differential (front − back) per side independently. Allows you to see if the call side and put side have different regime strength.

**Why Separate Sides Matter:** In a double diagonal, the call side and put side can be at very different strikes with different skew characteristics. The call side at 7500 may be in a strongly inverted regime while the put side at 7400 is near-parity. Without separating the sides, this asymmetry is invisible.

**Components per Side:**

| Element | Formula | Source |
|---------|---------|--------|
| Edge Value | `Front_Side_IV − Back_Side_IV` | Computed from `option_rows.iv × 100` |
| Directional Label | See table below | Computed by `_edge_label()` in `app.py` |
| IV Ratio | `Front_Side_IV / Back_Side_IV` | Same source |
| Today Trend Sparkline | Edge history for today | `db.get_contract_iv_history()` for both expiries, merged on timestamp |

**Edge Color Coding:**

| Edge Value | Color | Label |
|-----------|-------|-------|
| < −0.5% | `#00d97e` (green) | Back-Elevated ↑ |
| −0.5% to 0 | `#4ecdc4` (teal) | Mildly Inverted |
| 0 to +0.5% | `#ffd32a` (amber) | Near-Parity |
| > +0.5% | `#ff4757` (red) | Front-Elevated ↓ |

**Sparkline Design:** Same color as the edge value (so the chart visually communicates regime at a glance). Zero reference line in faint white. Shows all of today's history (not just 30 minutes, unlike IV Structure Panel).

**Interpretation:** A negative edge means front IV is lower than back IV at that specific strike and side. This is the inverted regime that is favorable. A trend toward more-negative edge means the favorable regime is strengthening. A trend toward zero or positive means it is weakening — watch for a shift before or after opening a position.

---

### 5.5 Transform Credit Panel

**Purpose:** The primary monitoring tool for an open position. Shows in real-time whether the transformation threshold has been crossed and exactly which leg values are driving the credit.

**Inputs (from sidebar):**
| Input | Description | Default |
|-------|-------------|---------|
| Entry Debit | What you paid to open the diagonal | $9.00 |
| Transform Threshold | Minimum credit to justify transformation | $5.00 |

These persist in `st.session_state` for the duration of the browser session. **Reset them whenever you open a new trade.**

**Calculation:**

```
Back_Legs_Value = Back_Call_Mark + Back_Put_Mark
Close_Cost      = Front_Call_Ask + Front_Put_Ask
Diagonal_Mark   = Back_Legs_Value − Close_Cost
Transform_Credit = Diagonal_Mark − Entry_Debit
Gap_to_Threshold = Transform_Threshold − Transform_Credit
```

**Source columns:**
| Value | Column | Table | Note |
|-------|--------|-------|------|
| Back_Call_Mark | `mark` | `option_rows` | Pre-computed mid; fallback to `(bid+ask)/2` |
| Back_Put_Mark | `mark` | `option_rows` | Same |
| Front_Call_Ask | `ask` | `option_rows` | Best ask to close short |
| Front_Put_Ask | `ask` | `option_rows` | Best ask to close short |

**Why `ask` for closing cost?** When you buy back your short legs (to close), you pay the ask price. Using mid would understate the true cost by approximately half the bid-ask spread per leg.

**Status Icons:**
| Condition | Icon | Color | Meaning |
|-----------|------|-------|---------|
| Credit ≥ Threshold | ✅ | Green | Transform now. Viable. |
| 0 < Credit < Threshold | ⏳ | Amber | Watching. Not yet. |
| Credit ≤ 0 | ⛔ | Red | Position underwater. |

**Theta ETA (rough estimate):**
```
Daily_Theta_Est         = Close_Cost / Front_DTE
Trading_Hrs_to_Threshold = Gap_to_Threshold / Daily_Theta_Est × 6.5
```

Where 6.5 is the approximate number of trading hours per day.

**Critical disclaimer on Theta ETA:** This estimate treats the front legs as linearly decaying and ignores: back-leg theta drag, vega effects, delta exposure from SPX movement, and gamma risk. It is a directional estimate only ("roughly 4 hours away") not a precise prediction. Do not use it to schedule exits.

**Limitations:**
- If `entry_debit = 0.00` (sidebar not set), the panel displays an instruction to set it.
- If `mark` is null for any leg (e.g., no trades in that contract today), falls back to `(bid+ask)/2`. If both are null, the panel shows "Mark data unavailable."
- The panel shows *theoretical* credit. Actual filled credit will differ due to slippage (see Section 9).

---

### 5.6 ATM Term Structure Strip

**Purpose:** Macro-level IV term structure overview at the floating ATM strike. Complements the per-strike panels by showing the market-wide regime context.

**Components:**

| Metric | Formula | Source |
|--------|---------|--------|
| IV Ratio (F/B) ATM | `ATM_Front_IV / ATM_Back_IV` | Computed by `iv_engine.term_structure()` |
| Front ATM IV % | Average IV of nearest strike calls and puts for front expiry | `option_rows.iv × 100`, filtered by `chain_df["expiry"] == front_expiry`, nearest strike to SPX |
| Back ATM IV % | Same for back expiry | Same, back expiry |
| IV Index | Mean of mean IVs across all loaded expiries | `chain_df.groupby("expiry")["iv"].mean().mean()` |
| ATM Regime | Label and color from `iv_engine.iv_regime(ts.ratio)` | Derived |

**ATM vs Per-Strike:** The ATM ratio uses a *floating* nearest-strike, not a fixed strike. When SPX moves, the "ATM strike" changes. This means the ATM ratio is a macro signal for the overall regime, while the per-strike ratio in the IV Structure Panel is the actual signal for your specific trade legs.

**Known Limitation:** Near end of day (EOD), the ATM IV ratio can become unreliable when 0DTE options expire and the nearest-strike reference shifts. If the front expiry is 0DTE late in the session, treat the ATM ratio with skepticism.

---

### 5.7 Selected-Strike IV Chart

**Purpose:** Historical view of IV levels and ratio at your exact trade strikes across the selected time range.

**Chart Traces:**

| Trace | Color | Axis | Description |
|-------|-------|------|-------------|
| Front [call_strike]C | `#00d97e` (green, solid) | Left (IV %) | Front call IV over time |
| Back [call_strike]C | `#3498db` (blue, solid) | Left (IV %) | Back call IV over time |
| Call Ratio | `#e74c3c` (red, solid) | Right (ratio) | Front/Back call IV ratio |
| Front [put_strike]P | `#00d97e` (green, dotted) | Left (IV %) | Front put IV over time |
| Back [put_strike]P | `#3498db` (blue, dotted) | Left (IV %) | Back put IV over time |
| Put Ratio | `#e74c3c` (red, dotted) | Right (ratio) | Front/Back put IV ratio |
| Ratio = 1.0 reference | White dotted | Right axis | Visual regime boundary |

**Data Source:** `db.get_contract_iv_history(DB_PATH, expiry, strike, right, days)` → joined on timestamp, multiplied by 100.

**Time Range:** Controlled by period selector (Today / 5D / 10D / 15D / 1M).

**Limitations:** This chart will be blank if no contract-specific history exists for the selected range. A newly set strike will show "No per-strike history found" until at least one snapshot has been collected while those strikes were set as the active selector values. The collector always records all strikes — this chart simply needs any matching data in the DB.

---

### 5.8 ATM IV Chart

**Purpose:** Historical view of floating-ATM IV for front and back expiry, plus their ratio. Labeled "macro context" because it shows the overall IV regime trend independent of specific strikes.

**Chart Traces:** Same structure as Selected-Strike IV Chart but sourced from `atm_iv_by_expiry.atm_avg_iv × 100`.

**Data Source:** `db.get_atm_iv_history(DB_PATH, expiry, days)` → `atm_iv_by_expiry` table.

---

### 5.9 Historical Range Stats

**Purpose:** FLUX-style range bars showing where the current ATM IV ratio sits relative to its range over different look-back periods.

**Layout:** Five columns — Today, 5D, 10D, 15D, 1M.

**For each column:**
1. Load ATM IV history for front and back expiry over that period.
2. Join on timestamp, compute ratio series.
3. Compute `range_stats(ratio_series, current_ratio)`.
4. Display low/high values with a dot marking the current position.

**Formula:**
```
Position_Pct = (current_ratio − period_low) / (period_high − period_low) × 100
```

The red dot position on the bar = `Position_Pct` (0 = left, 100 = right).

**Interpretation:**
- Dot near the left (position_pct low): Current ratio is near the bottom of its recent range. For ratio < 1.0, this means the inversion is at a recent extreme — strongest structural edge observed in this period.
- Dot near the right: Ratio is near recent highs — if ratio > 1.0, contango is elevated.

---

### 5.10 Trade Quality Score

**Purpose:** A composite signal for entry quality. Answers: "Relative to recent history and current liquidity, how good is this setup right now?"

**Formula:**
```
Score = 0.45 × IV_Edge_Pct + 0.30 × Liquidity_Score + 0.25 × Theta_Advantage
```

**Component definitions:**

| Component | Formula | Weight | Current Status |
|-----------|---------|--------|----------------|
| IV Edge Pct | Percentile rank of current ATM ratio vs. period history | 45% | Live |
| Liquidity Score | `min(Volume/500, 1)×50 + min(OI/2000, 1)×50` | 30% | Live |
| Theta Advantage | Net daily theta benefit | 25% | **Placeholder (50)** — Phase 3 |

**IV_Edge_Pct Calculation:**
```
IV_Edge_Pct = fraction of historical ATM ratio observations < current ratio × 100
```
A value of 85 means the current ratio is higher than 85% of historical observations. For ratio < 1.0 (inverted), this percentile is computed on the raw ratio — a lower current ratio means a *lower* percentile rank. When using this for entry decisions, also check the regime label to ensure direction.

**Liquidity Score Calibration:**
- Volume 500+ at front ATM → max volume sub-score (50). SPX typically exceeds this easily.
- OI 2,000+ at front ATM → max OI sub-score (50). SPX also typically exceeds.

**Limitation — Theta Advantage placeholder:** Until Phase 3 is implemented, the overall score is artificially deflated toward 50 (the placeholder value). Do not treat the overall score as a precise signal until all three components are real.

**Important design note:** This score is shown as supplementary context. It is **not** a go/no-go entry trigger. The raw per-strike IV ratio, regime badge, and Transform Credit are the primary decision metrics. See Section 8 for rationale.

---

### 5.11 Options Chain Table

**Purpose:** Reference table for the front expiry's full option chain, allowing strike lookup and liquidity verification.

**Columns shown (if available):**

| Column | Source | Notes |
|--------|--------|-------|
| `strike` | `option_rows.strike` | |
| `side` | Derived from `option_rows.right` | "CALL" or "PUT" |
| `bid` | `option_rows.bid` | |
| `ask` | `option_rows.ask` | |
| `mark` | `option_rows.mark` | Pre-computed or (bid+ask)/2 |
| `iv` | `option_rows.iv × 100` | Displayed as % |
| `volume` | `option_rows.volume` | |
| `open_interest` | `option_rows.open_interest` | |
| `delta` | `option_rows.delta` | |
| `dte` | `option_rows.dte` | |

**Sorted by:** `strike` ascending, then `side`.

---

## 6. Mathematical Definitions

This section defines every formula used in the project with variable definitions, units, and worked examples.

---

### 6.1 ATM IV

**Purpose:** Extract the floating-ATM IV for a given expiry from the chain.

**Formula:**
```
S = nearest strike to underlying price
ATM_IV(expiry) = mean(IV(S, CALL, expiry), IV(S, PUT, expiry))
```

**Variables:**
- S: Strike nearest to current SPX price (point with minimum |strike − SPX_price|)
- IV(S, side, expiry): Implied volatility of the option at that strike/side/expiry, in percentage form

**Implementation:** `iv_engine.atm_iv(chain_df, expiry, underlying_price)`

---

### 6.2 IV Ratio

**Formula:** `IV_Ratio = Front_IV / Back_IV`

**Variables:**
- Front_IV: IV at the selected strike and side for the front (near-term) expiry
- Back_IV: IV at the same strike and side for the back (far-term) expiry
- Both in percentage form (e.g., 18.5, not 0.185)

**Units:** Dimensionless

**Worked Example:**
- Front_Call_IV = 16.2%
- Back_Call_IV = 19.1%
- Call_IV_Ratio = 16.2 / 19.1 = **0.848** → INVERTED ● (< 0.90)

---

### 6.3 IV Spread

**Formula:** `IV_Spread = Front_IV − Back_IV`

**Worked Example:**
- Front_Call_IV = 16.2%
- Back_Call_IV = 19.1%
- Call_IV_Spread = 16.2 − 19.1 = **−2.9%** (negative = back elevated)

---

### 6.4 Calendar Edge (per side)

**Formula:**
```
Call_Edge = Front_Call_IV − Back_Call_IV   [at call_strike]
Put_Edge  = Front_Put_IV  − Back_Put_IV   [at put_strike]
```

**Note:** This is the same as IV Spread computed at the per-strike level, separated by side.

**Implementation:** `iv_engine.calendar_edge(chain_df, front_expiry, back_expiry, call_strike, put_strike)`

---

### 6.5 Transform Credit

**Formula:**
```
Back_Legs_Value  = Back_Call_Mark + Back_Put_Mark
Close_Cost       = Front_Call_Ask + Front_Put_Ask
Diagonal_Mark    = Back_Legs_Value − Close_Cost
Transform_Credit = Diagonal_Mark − Entry_Debit
```

**Variables:**
- `Back_Call_Mark`: Current mid-market value of back long call (mid = (bid + ask) / 2 if pre-computed mark absent)
- `Back_Put_Mark`: Current mid-market value of back long put
- `Front_Call_Ask`: Current ask on front short call (cost to buy it back and close)
- `Front_Put_Ask`: Current ask on front short put
- `Entry_Debit`: Original net debit paid to enter the diagonal (user-input, sidebar)

**Units:** Option premium units (points). $1.00 point = $100 real money per standard SPX contract.

**Worked Example:**
```
Back_Call_Mark  = $8.10
Back_Put_Mark   = $6.15
Front_Call_Ask  = $0.60
Front_Put_Ask   = $0.40

Back_Legs_Value  = $8.10 + $6.15  = $14.25
Close_Cost       = $0.60 + $0.40  = $1.00
Diagonal_Mark    = $14.25 − $1.00 = $13.25
Transform_Credit = $13.25 − $9.00 = +$4.25   (entry debit was $9.00)
```

**Implementation:** `iv_engine.transform_credit(chain_df, front_expiry, back_expiry, call_strike, put_strike, entry_debit, front_dte, threshold)`

---

### 6.6 Theta ETA (rough estimate)

**Formula:**
```
Daily_Theta_Est          = Close_Cost / Front_DTE
Gap_to_Threshold         = Transform_Threshold − Transform_Credit
Trading_Hrs_to_Threshold = (Gap_to_Threshold / Daily_Theta_Est) × 6.5
```

**Variables:**
- `Close_Cost`: Current total cost to close front legs (from 6.5)
- `Front_DTE`: Days to expiration for the front expiry
- `Transform_Threshold`: User-configured minimum credit (sidebar)
- `Transform_Credit`: Current theoretical credit (from 6.5)
- 6.5: Approximate trading hours per market day

**Assumptions and Limitations:**
- Treats front leg theta decay as linear over remaining DTE
- Ignores back-leg theta drag (back legs also decay, partially offsetting)
- Ignores vega effects (changes in IV affect the credit differently from theta)
- Ignores delta/gamma effects from SPX movement
- Use as a rough directional estimate only

---

### 6.7 Liquidity Score

**Formula:**
```
Vol_Score       = min(Volume / 500, 1.0) × 50
OI_Score        = min(Open_Interest / 2000, 1.0) × 50
Liquidity_Score = Vol_Score + OI_Score
```

**Range:** 0–100

**Calibration:** Volume threshold of 500 and OI threshold of 2,000 are calibrated for SPX which is typically highly liquid. For most SPX strikes during market hours, Liquidity_Score will be near 100.

---

### 6.8 Percentile Rank

**Formula:**
```
Percentile_Rank = (count of historical observations < current_value) / (total observations) × 100
```

**Range:** 0–100

**Example:**
- Historical ratio observations (sorted): [0.80, 0.85, 0.88, 0.92, 0.95, 0.98, 1.02, 1.05]
- Current ratio: 0.87
- Observations < 0.87: [0.80, 0.85] → 2 out of 8
- Percentile_Rank = 2/8 × 100 = **25.0**

**Implementation:** `iv_engine.percentile_rank(history_series, current_value)`

---

### 6.9 Trade Quality Score

**Formula:**
```
Score = 0.45 × IV_Edge_Pct + 0.30 × Liquidity_Score + 0.25 × Theta_Advantage
```

**Range:** 0–100

**Weights rationale:**
- IV Edge (45%): The dominant signal. IV structure regime determines whether structural edge exists at all.
- Liquidity (30%): SPX is always liquid, but OI and volume still matter for execution quality.
- Theta Advantage (25%): Net carry — how fast the position earns if nothing moves.

---

### 6.10 Range Stats Position

**Formula:**
```
Position_Pct = (current_value − period_low) / (period_high − period_low) × 100
Clamped to [0, 100]
```

**Purpose:** Positions the indicator dot on the historical range bar in the Historical Range Stats panel.

---

### 6.11 Expected Move (Informational)

**Formula:**
```
EM_1SD = SPX_Price × (ATM_IV / 100) × √(DTE / 365)
EM_2SD = 2 × EM_1SD
```

**Purpose:** Used by `iv_engine.expected_move_log_check()` to verify that the configured strike fetch window (±300 points) is wide enough to cover a 2-standard-deviation move. Logged only — never gated.

---

## 7. Data Architecture Reference

### 7.1 System Architecture

```
┌─────────────────────────────────────┐
│  Charles Schwab API                  │
│  (SPX options chain + VIX quote)     │
└───────────────┬─────────────────────┘
                │  schwab_client.py
                │  (OAuth, get_option_chain, get_quote)
                ▼
┌─────────────────────────────────────┐
│  collector.py  (background process)  │
│  - Polls on schedule (5min / 60s)    │
│  - Processes and filters chain data  │
│  - Writes to SQLite                  │
└───────────────┬─────────────────────┘
                │  db.py (write functions)
                ▼
┌─────────────────────────────────────┐
│  dashboard.db (SQLite)               │
│  (single file, local only)           │
└───────────────┬─────────────────────┘
                │  db.py (read functions)
                ▼
┌─────────────────────────────────────┐
│  app.py  (Streamlit dashboard)       │
│  - Pure reader, no writes            │
│  - No API calls                      │
│  - All analytics in iv_engine.py     │
└─────────────────────────────────────┘
```

**Critical architecture rule:** `app.py` and `collector.py` are fully independent. `app.py` never writes to the database. `collector.py` never reads from session state or the UI. This separation ensures that a UI reload never corrupts collected data and a collector restart never disrupts the UI.

---

### 7.2 Database Tables

#### Table: `snapshots`

The root table. Every data collection cycle creates one row here.

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | INTEGER PRIMARY KEY | Auto-increment |
| `snapshot_timestamp` | TEXT | UTC ISO8601 timestamp of collection |
| `underlying_price` | REAL | SPX price at collection time |
| `vix_value` | REAL | VIX quote at collection time |

---

#### Table: `option_rows`

One row per option contract per snapshot.

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | INTEGER | FK → `snapshots.snapshot_id` |
| `expiry_date` | TEXT | Expiration date string (e.g., '2026-06-26') |
| `strike` | REAL | Strike price |
| `right` | TEXT | 'C' (call) or 'P' (put) |
| `bid` | REAL | Best bid |
| `ask` | REAL | Best ask |
| `mark` | REAL | Pre-computed mid = (bid + ask) / 2 |
| `iv` | REAL | **Decimal form** (e.g., 0.185 = 18.5%). Multiply by 100 for display. |
| `volume` | INTEGER | Contracts traded today |
| `open_interest` | INTEGER | Open contracts |
| `delta` | REAL | Option delta |
| `dte` | INTEGER | Days to expiration |
| `time_value` | REAL | Extrinsic (time) value |
| `intrinsic_value` | REAL | Intrinsic value |

**Critical index:**
```sql
CREATE INDEX idx_option_rows_contract_snap
ON option_rows (expiry_date, strike, right, snapshot_id);
```
This index is required for fast lookup. Without it, per-strike queries would be unacceptably slow.

---

#### Table: `atm_iv_by_expiry`

Pre-aggregated ATM IV per expiry per snapshot. Used for historical charts and range stats.

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | INTEGER | FK → `snapshots.snapshot_id` |
| `expiry_date` | TEXT | Expiration date |
| `atm_call_iv` | REAL | **Decimal form** — ATM call IV |
| `atm_put_iv` | REAL | **Decimal form** — ATM put IV |
| `atm_avg_iv` | REAL | **Decimal form** — average of call and put ATM IV |

**IV Scale Note:** All three IV columns in this table store values as decimals (0.185, not 18.5%). Every read function in `app.py` multiplies by 100 immediately after loading. This multiplication happens at the "load boundary" — the first line of code that touches the value — and is never done inside `iv_engine.py` functions.

---

#### Table: `collection_gaps`

Tracks periods where collection was interrupted.

| Column | Type | Description |
|--------|------|-------------|
| `gap_start` | TEXT | UTC timestamp gap started |
| `gap_end` | TEXT | UTC timestamp gap ended |
| `gap_seconds` | INTEGER | Duration of gap in seconds |
| `reason` | TEXT | Reason for gap (e.g., 'market closed', 'error') |

---

### 7.3 Data Lineage — "Where Does This Number Come From?"

#### Example: Transform Credit Panel display value

```
Schwab API: options chain for selected strikes
  → schwab_client.get_option_chain() → raw Python dict
  → collector.py: parses bid, ask, mark for each contract
  → db.save_option_row(): stores in option_rows.mark (pre-computed as decimal)
  → db.get_option_chain(): returns rows for latest snapshot_id
  → app.py chain_df build: option_rows.iv × 100 (scale boundary)
                            option_rows.mark used as-is (already in $ premium units)
  → iv_engine.transform_credit(): Back_Legs_Value − Close_Cost − Entry_Debit
  → app.py Transform Credit Panel: displays as $+X.XX
```

#### Example: IV Structure Panel regime badge

```
Schwab API: options chain for selected strike
  → stored in option_rows.iv (decimal form)
  → app.py: chain_df["iv"] = chain_df["iv"] * 100 (scale boundary)
  → iv_engine.strike_contract(chain_df, front_expiry, call_strike, "CALL"):
      filters chain_df to matching row → returns StrikeContract with iv in %
  → iv_engine.strike_contract(chain_df, back_expiry, call_strike, "CALL"):
      same for back expiry
  → ratio = fc.iv / bc.iv
  → iv_engine.iv_regime(ratio) → (label, color)
  → app.py IV Structure Panel: renders label and colored badge
```

#### Example: Historical Range Stats bar

```
DB query: atm_iv_by_expiry for front_expiry, period_days
  → db.get_atm_iv_history() → list of (snapshot_timestamp, atm_avg_iv) rows
  → app.py _load_atm_hist(): atm_avg_iv × 100, timestamp → ET tz
  → Same for back_expiry
  → pd.merge on timestamp (inner join — only matching timestamps)
  → ratio series = front["atm_iv"] / back["atm_iv"]
  → iv_engine.range_stats(ratio_series, ts.ratio):
      low, high of period; position_pct of current ratio within [low, high]
  → app.py: renders HTML bar with dot at position_pct
```

---

## 8. Dashboard Design Philosophy

### 8.1 Core Principle: Decision Quality Over Information Quantity

The dashboard exists to support two decisions, as stated in Section 2.1. Every metric either directly answers one of those two questions, or provides validated supporting context. Metrics that do neither — regardless of their analytical interest — are excluded.

This principle was established in the original project design and was reinforced during the first several build sessions. Information overload is not just an aesthetic problem; it is a trading risk. A dashboard that requires 30 seconds of interpretation before each trade decision is a dashboard that causes hesitation at the wrong moments.

### 8.2 Why Certain Metrics Were Selected

**IV Ratio (per-strike):** The ratio, not the raw IV values, tells you the *structure* of the market. Two scenarios with identical IV levels can have opposite IV ratio regimes and therefore opposite entry quality. The ratio is the signal; the raw IVs are context.

**Transform Credit (not Diagonal Mark):** The diagonal mark shows position value. The Transform Credit shows your profit. You do not trade marks; you trade profits. A position worth $13.25 that cost you $9.00 is a $4.25 gain — and that is the number that determines whether transformation is viable.

**Per-Strike, Not ATM:** ATM IV is a proxy for the overall market regime. But when you select specific strikes, the IV at *those strikes* determines the premium of your actual legs. The dashboard tracks ATM IV as macro context (labeled explicitly as such) and per-strike IV as the trade-relevant signal.

**Separate Call and Put Sides:** Because this is a double structure, the call side and put side can be in different regimes. If the put side's edge has collapsed while the call side remains strong, a trader monitoring only blended metrics would miss the asymmetry. The Calendar Edge Panel separates them deliberately.

**30-Minute Sparklines in IV Structure Panel:** Regime badges show current status. The sparkline shows trend. A ratio of 0.88 that was 0.82 twenty minutes ago is weakening; a ratio of 0.88 that was 0.95 twenty minutes ago is strengthening. The trend matters for entry timing.

### 8.3 Why Certain Metrics Were Rejected

**Composite "Magic Score" (Transformation Score, 0–100):** Demoted to "Do Not Build." The concern is that a composite score obscures which component is driving the value. If the score is 72, is that a liquidity problem, an IV structure problem, or a theta problem? Raw decision-relevant numbers are always preferred over abstracted scores. The Trade Quality Score is retained as supplementary context (clearly labeled as such) but is never presented as a primary entry/exit signal.

**Automatic Event Detection for Polling:** The system uses a manual sidebar toggle (Event Mode) instead of automatic detection of high-impact events. Automatic detection based on IV thresholds has inherent lag — it fires after the spike has already started. The trader knows their economic calendar in advance; manual, anticipatory activation is faster and more reliable than any automatic system.

**Payoff Diagram (Phase 5):** Not yet built. Visual payoff diagrams are genuinely useful for initial strategy setup but add significant complexity for a live monitoring use case where the diagonal parameters are already established. Deferred until the core monitoring workflow is proven.

**Historical Win Rate by IV Regime:** Requires substantial trade history (Phase 4 position logging) before it has statistical meaning. Building it now would display meaningless numbers. Deferred.

### 8.4 Must Have / Nice To Have / Do Not Build

**Must Have (current dashboard has these):**
- Per-strike IV ratio with regime classification (IV Structure Panel)
- Call and put edge separately (Calendar Edge Panel)
- Theoretical Transform Credit with leg breakdown (Transform Credit Panel)
- ATM term structure overview with regime badge
- Historical range stats for IV ratio
- Options chain table with marks

**Must Have (planned, not yet built):**
- Net Theta Advantage ($/day) — Phase 3
- Days to Risk-Free estimate — Phase 3
- Position tracker with entry debit storage — Phase 4

**Nice To Have:**
- Payoff diagram (diagonal before transformation, IC after)
- IV percentile with adequate history (requires 3+ months of data)
- Mean reversion estimate (already in `iv_engine.py`, not yet surfaced in UI)

**Do Not Build:**
- Composite Transformation Score (0–100 magic number)
- Automatic event mode triggering
- SaaS multi-user features
- Trade execution through the dashboard

---

## 9. Assumptions and Known Limitations

### 9.1 Paper Trade Fill Assumptions

All current threshold values ($5.00 transform threshold) were developed during paper trading. Paper trades are executed at mid-market prices (mark). Live trades are executed at the market bid/ask.

**Estimated slippage across four legs:** $2.00–$4.00 total (approximately $0.50–$1.00 per leg). This is a systematic overestimation of fill quality in paper trading.

**Implication:** The live Transform Threshold should be approximately $6.50–$7.00 to account for the fact that the actual filled credit will be $2–$4 less than the theoretical credit shown in the dashboard.

**Action required:** Calibrate the live threshold from the first 5–10 live trade transformations. Until then, use $6.50 as a conservative starting estimate.

### 9.2 Mid-Price Mark Assumption

When `option_rows.mark` is null or absent, the dashboard falls back to `(bid + ask) / 2`. This mid-price assumption:
- Overestimates the value of illiquid options where the bid-ask spread is wide
- Understates the true cost to close a position (you buy at ask, not mid)

The dashboard specifically uses `ask` (not `mark`) for the front leg close cost in the Transform Credit calculation to avoid this bias on the closing side. The back leg marks are still mid-prices, which may slightly overstate the value you could receive from closing them.

### 9.3 IV Accuracy Assumptions

The IV values in `option_rows.iv` are pulled from Schwab's API response. Schwab's IV is computed by their system and may differ from other vendors' IV computations. Known issues:
- Stale IV (option hasn't traded recently, Schwab shows last-computed value)
- Zero-volume options may carry stale IV from the prior session
- Near end-of-day, IV for expiring options can spike or collapse rapidly

**Mitigation:** The collector filters out options with zero bid (no market). However, stale IV in low-volume strikes is still possible.

### 9.4 Theta ETA Assumptions

The Theta ETA estimate in the Transform Credit Panel is a rough approximation:
- Treats front-leg decay as linear over remaining DTE (theta is not linear — it accelerates as expiration approaches)
- Ignores back-leg theta drag (back legs also lose time value)
- Ignores vega (if IV falls, option values can drop faster than theta alone)
- Ignores delta (SPX movement affects marks independently of theta)
- Treats a trading day as exactly 6.5 hours

Use Theta ETA as an order-of-magnitude estimate ("roughly 4 hours away") not as a scheduled target.

### 9.5 ATM IV Ratio Near EOD

The `atm_iv_by_expiry` table's ATM ratio becomes unreliable near end of day when a 0DTE expiry is close to expiration. As 0DTE options approach their last hour, their IV can become erratic and disconnected from the IV of longer-dated contracts. The pre-computed `atm_avg_iv` may reference these erratic values.

**Mitigation:** Trust the per-strike IV in the IV Structure Panel over the ATM IV ratio for trading decisions in the final hour before a front expiry. The per-strike ratio for your specific strikes is not affected by the ATM reference-expiry shifting problem.

### 9.6 Historical Percentile Reliability

Percentile calculations require sufficient history to be statistically meaningful. The `sample_size_warning()` function in `iv_engine.py` warns when fewer than 200 observations are available.

200 observations at 5-minute polling intervals = approximately 16 trading hours = 2–3 trading days. At that point, percentiles are computed against a very short history and should be treated as directional only. Full reliability requires 3–6 months of continuous collection.

### 9.7 Collector Independence

The collector (`collector.py`) runs as a separate background process. The dashboard (`app.py`) is purely a reader. If the collector stops running for any reason (system sleep, network error, token expiry), the dashboard will continue to display the last-collected data, with the staleness indicator turning yellow or red.

**Schwab token expiry:** Schwab refresh tokens expire after 7 days. The first login requires a manual copy-paste OAuth flow (`client_from_manual_flow`). After that, the token auto-refreshes. Re-authentication is required approximately once per week.

---

## 10. Future Roadmap

### 10.1 Planned

These features have been approved in design discussions and will be built in the next phases.

**Phase 3 — Net Theta Advantage ($/day)**
Compute the actual net daily theta of the diagonal position (short front theta minus long back theta) in dollar terms. Requires reliable `theta` values in `option_rows`. Currently not implemented; the Trade Quality Score uses a placeholder.

**Phase 3 — Days to Risk-Free Estimate**
Replace the rough Theta ETA calculation with a proper estimate that incorporates theta from both legs. Still an estimate (not accounting for vega and delta), but more accurate than the current single-leg approximation.

**Phase 4 — Position Tracker**
Log actual diagonal entries: strikes, expiries, entry debit, entry date, and eventually close/transform records. This creates a historical trade log that enables Phase 4 analytics (win rate by IV regime, actual vs. theoretical transform credit comparison).

**Phase 4 — Transformation Calculator with IC Payoff**
Given a logged open position, show the exact resulting Iron Condor structure after transformation: max loss, max profit, breakeven levels, and net risk-free status.

**Phase 5 — Payoff Diagrams**
Visual P&L curve for the diagonal (pre-transformation) and resulting Iron Condor (post-transformation). Will use Black-Scholes for pre-expiration diagonal value and exact intrinsic values for the IC.

---

### 10.2 Under Investigation

These ideas require validation from live trading data before a build decision is made.

**Live Threshold Calibration**
The current paper-trade transform threshold of $5.00 will need to be recalibrated from actual live fills. Working estimate for live threshold: $6.50–$7.00. Requires 5–10 live transformations to establish a confident value.

**IV Mean Reversion Estimate (UI Surface)**
`iv_engine.mean_reversion_estimate()` is already implemented but not yet surfaced in the UI. Under investigation: whether this adds decision value beyond the regime badge and sparkline, or whether it creates noise by presenting imprecise estimates as actionable signals.

**Vega-Weighted Theta ETA**
Including vega effects in the Theta ETA calculation. Would require an assumption about IV trajectory, making the estimate more complex without a reliable basis for the assumption. Under investigation.

---

### 10.3 Rejected

These features have been explicitly excluded. They should not be revisited without a specific validated reason.

**Composite Transformation Score (0–100)**
Rejected in favor of raw decision-relevant numbers. A composite score obscures which dimension is limiting transformation viability. The Transform Credit dollar value is unambiguous; a score is not. **Do not build.**

**Automatic High-Impact Event Detection**
Considered as a mechanism to automatically switch to 60-second polling when IV spikes or known event times are approached. Rejected because: (1) automatic IV-based detection fires after the spike starts, not before; (2) calendar-based detection requires external data feed maintenance; (3) manual Event Mode toggle activated by the trader 10–15 minutes before a known event is faster and more reliable than any automatic system. **Do not build.**

**Multi-User / SaaS Version**
This is a personal trading tool. Distributing it as a product introduces regulatory and compliance concerns around financial software, and adds unnecessary infrastructure complexity. **Do not build.**

**Cross-Underlying Extension (QQQ, IWM, etc.)**
The dashboard is designed specifically for SPX dynamics (weekly/monthly expiry structure, cash settlement, specific strike spacing, specific liquidity profile). Extending to other underlyings would require validating that every threshold, filter, and assumption holds for the different instrument. **Not planned.**

---

*End of DOCUMENTATION.md — Version 1.0 — 2026-06-25*
