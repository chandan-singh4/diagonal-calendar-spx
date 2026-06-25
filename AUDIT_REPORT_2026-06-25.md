# DOCUMENTATION.md — Critical Audit Report
**Date:** 2026-06-25
**Scope:** Full critical review of DOCUMENTATION.md v1.0
**Instruction honored:** No changes made to DOCUMENTATION.md. This is the audit report only.

---

## Executive Summary

You were right to push on this. The audit found **one critical problem that invalidates the document's central organizing claim**, plus a terminology contradiction, two factual errors against implementation, several overstated-certainty issues, and one inconsistent strategy description.

The headline finding: **the document's core claim — that IV Ratio < 1.0 (back IV > front IV) is "favorable" and "maximizes transformation credit" — is not mathematically proven, is not empirically validated, and my own Black-Scholes testing suggests it is most likely backwards.** It rests entirely on a single paper trade (Category D evidence) and a terminology confusion. This claim propagates into at least seven sections, so correcting it is not a small edit.

I want to be clear about my own epistemic status too: I ran numerical tests below and they point one direction, but a handful of Black-Scholes scenarios with assumed IV paths is *also* not sufficient to establish the opposite rule as ground truth. The correct resolution is not "flip the sign and call it proven." It is to **demote the entire regime-favorability claim from 'confirmed ground truth' to 'unvalidated hypothesis'** until you have a real sample of trades.

---

# PART 1 — List of Every Issue Found

### Issue 1 — The central IV-ratio favorability claim is unproven and probably inverted
The document states (Sections 2, 3.1, 4, 5.3, 8.2) that ratio < 1.0 is favorable and "maximizes transformation credit," citing the 2026-06-23 forensic. My testing (Part 1A below) shows the opposite structural relationship under most assumptions. At minimum the claim is unproven; at worst it is backwards.

### Issue 2 — Internal terminology contradiction: "inverted" is used for the wrong direction
The document labels ratio < 1.0 (front IV *below* back IV) as "INVERTED." In standard volatility term-structure language, an **inverted / backwardated** curve is when the **near term has higher IV** (front > back, ratio > 1.0). The document has the label attached to the opposite condition. Section 4's "Inverted Term Structure" entry even acknowledges the result is "counterintuitive" — that flag was the tell that the labeling is inverted.

### Issue 3 — Transformation workflow is described incorrectly
Section 3.2 "Step 1 — Close the short front legs" contradicts the actual strategy. The intended workflow keeps the short front legs, closes the back-dated longs, and buys protective wings in the front expiration to form the Iron Condor. The documented sequence is wrong.

### Issue 4 — Expiry collection logic conflicts with implementation
Sections 3.1 and 7 state "all expirations within 20 calendar days" / `MAX_EXPIRY_DTE = 20`. The actual implementation collects **exactly 20 expirations by count** (not by DTE), reaching roughly 35–50 DTE. The documented rule is factually wrong and would mislead anyone reading source against the doc.

### Issue 5 — Strike width stated as if it were a rule
Sections 3.1 and 5.2 present "typical spread width of 100 points" in a way that reads like a strategy parameter. Strike selection actually depends on expected move, IV environment, and discretion. 100 points is one example, not a rule.

### Issue 6 — Theta ETA presented as a dashboard feature despite being built on assumptions you reject
Sections 5.5, 6.6, 9.4 document a Theta ETA metric that ignores vega, delta, gamma, and back-leg theta. This conflicts with your stated principle of not shipping metrics built on rough assumptions rather than data.

### Issue 7 — "Risk-free" terminology overstates certainty
Section 2.4 defines a "risk-free transformation." Even with the caveat present, the label asserts more certainty than slippage-exposed, fill-dependent live execution can support.

### Issue 8 — Greeks table in 3.1 states net signs as fact without derivation
The net theta/vega/gamma sign claims depend on the IV regime and the specific strikes/DTE. They are presented as fixed properties of the structure.

### Issue 9 — Trade Quality Score percentile direction is ambiguous/contradictory
Section 5.10 computes IV_Edge_Pct as a percentile where higher ratio = higher percentile, but the (claimed) favorable regime is *low* ratio. The score therefore rewards the direction the rest of the doc calls unfavorable. Even setting aside Issue 1, the score's direction is internally inconsistent.

### Issue 10 — Liquidity Score thresholds presented as calibrated
Section 6.7 states volume-500 / OI-2000 thresholds as if validated for SPX. They are reasonable guesses, not calibrated values.

### Issue 11 — "IV Index" metric defined but of unclear decision value
Section 5.6 defines IV Index as mean-of-mean IV across expiries. Its purpose for either decision gate is not established; risks being information for its own sake (violates Section 8.1 philosophy).

### Issue 12 — Change-log claims canonical authority on day one
Section 1 asserts the document is "ground truth" while simultaneously encoding an unvalidated hypothesis as fact. The authority framing and the evidence quality are mismatched.

---

## Part 1A — Mathematical Investigation of the IV Ratio Claim (Issues 1 & 2)

I tested the two scenarios you specified using Black-Scholes (European pricing, correct for cash-settled SPX). SPX = 7478, call strike 7500, put strike 7400, front 2 DTE, back 5 DTE, r = 4.5%. Same IV applied to both sides within a scenario.

### Entry pricing

| | Scenario A: F=20% B=30% (ratio 0.67) | Scenario B: F=30% B=20% (ratio 1.5) |
|---|---|---|
| Front legs collected (short) | 49.96 | 90.28 |
| Back legs paid (long) | 164.46 | 97.40 |
| **Net entry debit** | **114.50** | **7.12** |

The doc's "favorable" regime (A) costs **16× more** to enter than the "unfavorable" regime (B). Higher capital at risk per spread, same notional structure.

### Effect on each leg
- **Long (back) legs:** higher back IV → more extrinsic value → you *pay more* for the legs you're long. Higher absolute value, but you bought it at that price; it is not free edge.
- **Short (front) legs:** lower front IV → *less* extrinsic value on the legs you're short → **less theta fuel to harvest**. This is the core problem with the "favorable" label.

### Effect on transform credit (1 day later, IV held constant, SPX flat)

| | A (inverted, ratio 0.67) | B (contango, ratio 1.5) |
|---|---|---|
| Transform credit | **+0.24** | **+21.82** |

Under pure time decay, the regime the doc calls *unfavorable* produced **~90× more** transform credit. Reason: high front IV (Scenario B) means the short legs carry large extrinsic value that decays into your pocket as buyback cost collapses.

### Effect under IV reversion (the bet the strategy actually makes)

| Scenario | Setup | Transform credit |
|---|---|---|
| True inverted, front IV crushes (F30→20, B22→21) | ratio 1.36 at entry | **+40.75** |
| Doc-inverted, back IV crushes (F20→19, B30→22) | ratio 0.67 at entry | **−45.12** |

When the near-term IV is elevated and reverts — the classic event-vol-crush setup — the position wants **front > back (ratio > 1.0)**, the opposite of the doc's rule.

### Structural confirmation (extrinsic value available to harvest on short legs)

| Front IV | Front-leg extrinsic (theta fuel) |
|---|---|
| 15% | 31.39 |
| 20% | 49.96 |
| 30% | 90.28 |
| 35% | 111.20 |

More front IV = more extrinsic on the short legs = more decay to harvest. This favors ratio > 1.0.

### Conclusion on Issue 1 & 2
Across pure decay, IV reversion, and structural extrinsic-value analysis, the math points toward **front IV > back IV (ratio > 1.0) being the structurally advantageous regime for harvesting transform credit** — the reverse of the document. The June-23 trade was profitable at ratios 0.85/0.82, but: (a) one trade is not a sample; (b) a diagonal can profit from direction or back-leg vega independent of the term-structure regime, so its profit does not isolate the regime as the cause; (c) the profit does not establish that the *opposite* regime would have done worse.

**This does not mean "ratio > 1.0 is now the proven rule."** It means the favorability direction is an open empirical question and must be documented as a hypothesis, not ground truth, until you have data.

---

# PART 2 — Classification of Each Issue

| # | Issue | Classification |
|---|-------|----------------|
| 1 | IV-ratio favorability backwards/unproven | **Strategy hypothesis requiring validation** (currently presented as Factual) + **Unsupported assumption** |
| 2 | "Inverted" label on wrong direction | **Factual error** (terminology) |
| 3 | Transformation Step 1 wrong | **Factual error** (conflicts with actual strategy) |
| 4 | "Within 20 calendar days" vs 20-by-count | **Documentation drift** / **Factual error** (conflicts with implementation) |
| 5 | 100-point width as rule | **Ambiguous wording** |
| 6 | Theta ETA on rough assumptions | **Strategy hypothesis requiring validation** / design-principle conflict |
| 7 | "Risk-free" overstates certainty | **Unsupported assumption** |
| 8 | Greeks net signs as fact | **Unsupported assumption** |
| 9 | Trade Quality percentile direction | **Factual error** (internal inconsistency) |
| 10 | Liquidity thresholds "calibrated" | **Unsupported assumption** |
| 11 | IV Index unclear value | **Ambiguous wording** / philosophy conflict |
| 12 | Day-one canonical authority vs unvalidated content | **Documentation drift** (process) |

---

# PART 3 — Proposed Replacement Text

> Proposed only. Nothing below has been written into DOCUMENTATION.md. Review, then tell me which to apply.

### 3.1 — Replacement for the IV Ratio regime claim (Issues 1, 2)

Replace the regime-favorability framing everywhere it appears (Sections 2.3, 3.1, 4 "Inverted/Normal Term Structure", 5.3, 5.6, 8.2) with hypothesis-grade language and corrected terminology:

> **IV Ratio and Term Structure (HYPOTHESIS — NOT VALIDATED)**
>
> `IV_Ratio = Front_IV / Back_IV`, computed per strike and side.
>
> **Terminology (standard vol conventions):**
> - Ratio > 1.0 → front IV above back IV → **backwardation / inverted** term structure (typical around near-term events).
> - Ratio < 1.0 → front IV below back IV → **contango / normal** term structure.
> - Ratio ≈ 1.0 → flat.
>
> **Favorability is currently an open question.** Black-Scholes analysis (see audit 2026-06-25) suggests that harvesting transform credit structurally favors *higher front IV relative to back* (ratio > 1.0), because the short front legs then carry more extrinsic value to decay. This is the **opposite** of the v1.0 documentation's original claim, which was based on a single paper trade and is now retracted.
>
> Neither direction is established as ground truth. The dashboard displays the ratio and regime label as **neutral context**, not as a buy/avoid signal. Do not treat any ratio threshold as a trade trigger until a minimum sample of live trades (target: 20+) has been logged and analyzed. See Roadmap §10.2.

The regime color badges should be relabeled to neutral descriptive terms (e.g., "FRONT-ELEVATED" / "FLAT" / "BACK-ELEVATED") with a single neutral accent color, removing the green=good / red=bad encoding until favorability is established.

### 3.2 — Replacement for Transformation section (Issue 3)

> **3.2 Transformation to Iron Condor**
>
> When the diagonal has gained enough value to lock in profit, it is converted into an Iron Condor. The actual workflow is:
>
> 1. **Keep the short front legs in place.** The front short call and short put remain open — they become the short strikes (the body) of the Iron Condor.
> 2. **Close the back-dated long legs.** Sell to close the back-month long call and long put, realizing their current value.
> 3. **Buy protective wings in the front expiration.** Buy a further-OTM call above the short call and a further-OTM put below the short put, both in the **same (front) expiration** as the shorts. These define the maximum loss.
>
> The result is a standard same-expiration Iron Condor: short strikes from the original front legs, long wings just purchased. Maximum loss is the wider wing distance minus net credit; maximum profit is the net credit retained.
>
> **Why realized profit can reduce downside to near zero:** if the value already banked from closing the back legs (minus what the wings cost) meets or exceeds the Iron Condor's maximum loss, the combined position cannot finish below break-even regardless of where SPX settles. This is the sense in which the position becomes "risk-reduced." (See §2.4 on the limits of the "risk-free" label.)

### 3.3 — Replacement for expiry collection (Issue 4)

> The collector loads **exactly 20 expirations by count**, starting from the nearest, regardless of their DTE. In current SPX conditions this reaches roughly **35–50 DTE**. (Configuration is by expiration count, not a DTE ceiling. Any reference to a 20-calendar-day window is obsolete and incorrect.)

### 3.4 — Replacement for strike selection (Issue 5)

> **Strike selection is discretionary and condition-dependent.** There is no fixed spread width. The trader chooses call and put strikes based on the current expected move, the IV environment, where they want the short strikes relative to spot, and risk tolerance. Any specific width (for example, 100 points) appearing in this document is an **illustrative example only**, not a strategy rule.

### 3.5 — Recommendation on Theta ETA (Issue 6)

**Recommendation: remove Theta ETA from the dashboard.** It violates your principle against metrics built on rough assumptions rather than data: it ignores back-leg theta, vega, delta, and gamma, and presents a single-leg linear-decay guess as a time-to-threshold figure precise enough to act on. If a "time to viability" indicator is wanted later, it should be built in Phase 3 from stored per-leg Greeks, not from `close_cost / front_dte`. Proposed doc change: move Theta ETA from §5.5/§6.6 active features into §10.3 Rejected, with this reasoning. (Code change in `iv_engine.py`/`app.py` would follow separately once you approve.)

### 3.6 — Replacement for "risk-free" framing (Issue 7)

> Replace "risk-free transformation" with **"risk-reduced transformation"** throughout. Define it as: a transformation after which the realized/locked value meets or exceeds the resulting Iron Condor's maximum theoretical loss, *assuming fills at or near the modeled prices*. Because live fills across multiple legs incur slippage, true zero-risk is not guaranteed; the term describes the target condition, not a certainty.

### 3.7 — Greeks table (Issue 8)

> Add a header note to the §3.1 Greeks table: "Net Greek signs depend on the IV regime, the chosen strikes, and DTE. The signs below describe a typical near-the-money, near-dated configuration and are not invariant properties of the structure."

### 3.8 — Trade Quality Score direction (Issue 9)

> Because regime favorability is unvalidated (§3.1 hypothesis), the IV_Edge_Pct component currently has **no justified direction** and should be treated as neutral context, not scored as good/bad. Recommendation: either (a) remove the IV component's contribution to the composite until favorability is validated, or (b) display the three components separately with no composite, consistent with the §8.3 rejection of composite scores. Flag the existing direction as a known inconsistency until resolved.

### 3.9 — Liquidity thresholds (Issue 10)

> Reword §6.7 to: "Thresholds of volume 500 and OI 2,000 are **initial estimates**, not calibrated values. They have not been validated against fill quality or SPX liquidity data and should be revisited once trade data exists."

### 3.10 — IV Index (Issue 11)

> Either justify IV Index against a specific decision, or mark it for removal. Proposed note: "IV Index (mean of per-expiry mean IV) is currently displayed as general context. Its value for either decision gate is unestablished; candidate for removal under §8.1 (decision quality over information quantity)."

### 3.11 — Change log / authority (Issue 12)

> Add a v1.1 row documenting this audit, and soften §1's authority statement to: "This document is the intended source of truth. Sections marked HYPOTHESIS are working assumptions awaiting validation and do **not** carry canonical authority until confirmed by data." Add a standing rule: no claim enters this document as fact using words like *confirmed / proven / favorable / optimal / maximizes* unless it is either mathematically derived (with the derivation shown) or backed by a stated minimum sample size.

---

## Recommended Change-Log Entry (for when you approve changes)

| Version | Date | Author | Summary of Changes |
|---|---|---|---|
| 1.1 | 2026-06-25 | Chandan Singh | Critical audit. Retracted the ratio<1.0 "favorable" claim (single-trade evidence; BS analysis suggests reverse) — demoted to unvalidated hypothesis with corrected backwardation/contango terminology. Corrected transformation workflow (keep shorts, close backs, add front-expiry wings). Fixed expiry-collection description (20 by count, not 20 DTE). Reframed strike width as example not rule. Recommended removing Theta ETA. "Risk-free"→"risk-reduced." Flagged Greeks-sign, Trade-Quality-direction, liquidity-threshold, and IV-Index claims as unvalidated. |

---

## What I'd Suggest Doing Next

1. **Approve the terminology + transformation + expiry + strike fixes (Issues 2, 3, 4, 5)** — these are clean factual corrections with no judgment call.
2. **Decide on Issue 1's framing.** My recommendation is hypothesis-grade neutrality, not flipping to the opposite rule. If you want, I can design a minimal trade-logging schema so the favorability question gets answered from your real fills instead of from anyone's priors.
3. **Approve or reject the Theta ETA removal (Issue 6).**
4. Once you've decided, I'll apply the approved changes to DOCUMENTATION.md and log v1.1.

One caution on my own analysis: the numerical tests above assume a single IV per scenario and specific reversion paths. They are strong enough to retract a one-trade claim, but they are **not** strong enough to install the opposite as proven. The honest end state is "unknown, pending data," and that's what the proposed text says.
