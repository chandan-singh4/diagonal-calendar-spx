# Development Journal — SPX Diagonal Calendar Analyzer

Log every change here. Format:

```
## YYYY-MM-DD — Short title
**Changed:** what you did
**Why:** the reason / problem it solves
**Impact:** effect on strategy logic or dashboard behavior
**Open questions / follow-ups:** anything left unresolved
```

---

## HOW TO START A NEW CHAT SESSION IF REPO IS PRIVATE
*(Read this every time before opening a new Claude chat)*

1. **Push any unsaved local changes first:**
   ```bash
   git add .
   git commit -m "brief description of what changed"
   git push
   ```
2. **Open a new Claude chat under the same Project.**
3. **Paste the full contents of this DEV_JOURNAL.md** into the first message.
   - This is the single file that tells Claude everything: what's been built,
     what bugs were fixed, what decisions were made, and what's next.
   - The repo is private so Claude cannot fetch it directly — pasting the journal
     is the handshake that gets Claude up to speed instantly.
4. **If Claude needs to see a specific file** (e.g. to debug app.py), paste that
   file's contents into the chat on request. No need to paste all files upfront.
5. **At the end of each session**, push again before closing the chat so the
   journal and any changed files are always current on GitHub.

GitHub repo: https://github.com/chandan-singh4/diagonal-calendar-spx
Primary branch: main
Local path: wherever your spx-diagonal-dashboard folder lives (parent folder contains .venv)


Newest entries begins from here - 

## 2026-06-29 — Dashboard v3.2: Entry Analysis overhaul, layout reorder, IC payoff chart, normalized metrics, weekend fallbacks

**Status:** Complete — all changes confirmed working by Chandan (live data verified in screenshots).
**Files changed:** `app.py` (primary), `iv_engine.py`, `db.py`, `pages/journal.py`.

---

### Summary of all changes this session

#### 1. IC Risk Profile Chart — `pages/journal.py`

Replaced the static `st.dataframe` "P&L by Expiry Zone" table in the Inspect Trade → Iron Condor tab with an interactive Plotly payoff-at-expiration chart.

- Pure piecewise-linear payoff at expiry (the only correct method for an IC; no Black-Scholes approximation).
- Five trace layers: green fill, red fill, green curve, red curve, invisible 24px hover trace.
- Annotations: LP/SP/SC/LC strike dotted verticals, BE dashed lines with arrow labels, amber vertical for current SPX price, emerald Max Profit callout, red Max Loss callout (omitted when `ic_risk_free=True`).
- `_ic_payoff_chart()` is a pure function (no `st` calls) — testable independently.
- `db.get_ic_marks()` hoisted above the chart so `marks["spx"]` is available as the price marker.
- `numpy` import added to `pages/journal.py`.

#### 2. New analytics functions — `iv_engine.py`

Three additions appended to the file; no existing functions touched.

**`atm_straddle_price(spx_price, atm_iv_pct, dte)`**
Formula: `S × σ × √(2T/π)` where `σ = atm_iv_pct/100`, `T = dte/365`. Takes IV in percentage form (matching chain_df convention after the ×100 load boundary). Returns None on non-positive inputs.

**`normalized_debit(net_debit, straddle_price)`**
`net_debit / straddle_price`. Removes SPX price-level drift and vol-regime shift so trade cost is comparable across dates. Returns None if straddle is zero. **HYPOTHESIS — not yet validated as a predictor of transform profit.**

**`ThetaDifferential` dataclass + `theta_differential(chain_df, front_expiry, back_expiry, call_strike, put_strike)`**
Computes position-level daily theta for the four-leg diagonal. Raw chain thetas are negative (convention). Net formula: `−front_sum + back_sum`. Positive when front decays faster than back (expected for a diagonal). Stores full decomposition (per-leg + per-side sums) so UI can display breakdown. `available` flag handles snapshots where Schwab omits Greeks. **HYPOTHESIS — magnitude at entry not yet validated as entry predictor.**

#### 3. New DB function — `db.py`

**`get_diagonal_history(db_path, front_expiry, back_expiry, call_strike, put_strike, days=90)`**
Returns one row per COMPLETE snapshot where all four diagonal leg marks are computable (`COALESCE(mark, (bid+ask)/2.0) IS NOT NULL` for all legs). Four LEFT JOINs on `option_rows` filtered by expiry/strike/right. Powers the research scatter. IVs returned in decimal form (caller multiplies ×100 if needed). Inserted after `get_iv_spread_history`.

#### 4. Entry Analysis section — `app.py` (formerly "Transform Credit")

Completely replaced Section 8 "Transform Credit" with "Entry Analysis". Removed the placeholder composite Trade Quality Score, `theta_adv = 50` fixed value, and the Overall Score / 100 metric. These had no validation basis.

**Row 1 — position cost metrics (require strikes):**
- **Diagonal Mark** — dual display: `12.55 pts · $1,255`. `(bc_call.mark + bc_put.mark) − (fc_call.mark + fc_put.mark)`. Per-share × 100 = dollar/contract.
- **ATM Straddle** — `iv_engine.atm_straddle_price()`. Normalization denominator.
- **Normalized Debit** — `iv_engine.normalized_debit()`. 4 decimal places.
- **Net Daily θ / contract** — `iv_engine.theta_differential()`. Shows `+$X.XX` / `−$X.XX`. Theta breakdown (per-side sums) in caption below.

Each metric has a one-line `st.caption()` explainability text below it in plain language.

**Row 2 — transform signal + market conditions:**
- **Transform Order Mark** — `(bc_call.mark + bc_put.mark) − (fc_wing_call.mark + fc_wing_put.mark)`. Wings are on **FRONT expiry** at `call_strike + 5` / `put_strike − 5` (matching actual transformation order: STC back legs + BTO front wings). Dual display: pts + dollar.
- **Transform Difference** — `Transform Order Mark − Diagonal Mark`. Resolves to `fc_call.mark + fc_put.mark − fc_wing_call.mark − fc_wing_put.mark` (front leg premium minus wing cost). **Signal threshold = 5.0** (green when ≥ 5).
  - *Below threshold:* amber Unicode block-character progress bar `████░░░░░░ 40%` + "X.XX pts until threshold" caption.
  - *At/above threshold:* dark green pill with `✓ Transformation threshold reached · Ready to transform · +X.XX pts above threshold`.
- **IV Ratio Percentile** — uses **fixed 90-day window** (`atm_merged_90d`, computed before Entry Analysis). Period-independent: the percentile reflects long-run context regardless of chart zoom. 
- **Liquidity (ATM)** — unchanged formula.

**Research scatter** — IV Ratio vs. Normalized Debit. Moved to bottom of page (Section 9). No longer in Entry Analysis. Historical points (teal), OLS trendline (dashed grey), current observation (amber diamond). Ratio = 1.0 reference line.

#### 5. Layout reorder — `app.py`

New section order (top → bottom):
1. Header (unchanged)
2. Controls (unchanged, with call/put swap below)
3. **Entry Analysis** ← promoted from position 8
4. **Calendar Edge** ← promoted from position 7
5. **Historical Statistics**
6. **Strike Detail + Selected-Strike IV chart** ← demoted from position 3
7. Pinned Pairs
8. Pair Scanner
9. **Research scatter** ← new bottom position

**Key dependency resolution:** `atm_merged_90d` (fixed 90-day, no radio) computed before Entry Analysis. Period radio moved into Calendar Edge section header. Calendar Edge builds its own `atm_merged` from `_load_atm_hist_fb(expiry, period_days)`. Strike Detail inherits `period_days` from Calendar Edge (renders first). Historical Stats uses its own fixed 1/5/10/20-day windows independently.

#### 6. Call/Put swap — `app.py`

Put is now consistently LEFT, Call consistently RIGHT across all three locations:
- Controls row: `c3 = Put Strike`, `c4 = Call Strike`
- Per-strike caption row beneath controls (subsequently removed — see §8)
- Strike Detail panel: Put first, Call second

Rationale: mirrors standard options visualization (puts to the left of underlying price, calls to the right).

#### 7. Calendar Edge changes — `app.py`

- **Period radio** moved into Calendar Edge section header (right-aligned column next to subheader). No longer a standalone pre-section row.
- **`atm_merged`** now computed inside Calendar Edge using `_load_atm_hist_fb` (includes weekend fallback).
- **Stacked view** and **intraday Front-vs-Back scatter** promoted from collapsed expanders to inline sections. `st.expander` removed entirely.
- **Range label** ("Range: 5D" read-only indicator) removed; the radio itself shows the selection.
- **x-axis bounds for Today:** when `period_label == "Today"`, both Calendar Edge primary chart and stacked panel pin `xaxis.range = [f"{session_date} 09:30", f"{session_date} 16:15"]`. Chart starts at open each day regardless of when first data point is.

#### 8. Per-strike caption row removed — `app.py`

The 2-column `Front IV: x% | Back IV: x% | Ratio: x` caption row that appeared directly below the Controls was removed. The same information appears in Strike Detail (Section 6) in a better format. Redundant at the top of the page.

#### 9. Weekend / gap fallback — `app.py`

**`_load_atm_hist_fb(expiry, days)`** — new helper wrapping `_load_atm_hist`. When `days=1` returns empty, retries with `days=5` and filters to `df["timestamp"].dt.date.max()`. Captures Friday data on Saturday, Thursday data when Friday is a holiday.

**`_load_contract_hist`** — same fallback logic added inline. The same `max()` date selection means it always shows the most recent session that has data, not a specific named day.

Used in: Calendar Edge `atm_merged` computation, Historical Stats all four columns, Strike Detail IV chart.

#### 10. Historical Statistics enhancements — `app.py`

Each column (Today / 5D / 10D / 20D) now shows:
- Range bar (existing `rs.low / rs.high / rs.position_pct`)
- **`iv_engine.percentile_rank()`** for the period's distribution
- **Current value** (`ts_now.ratio`) displayed numerically
- **Contextual label**: LOW (< 25th pct), MID (25–75th), HIGH (> 75th pct) — non-valenced
- Weekend fallback applied to all four columns via `_load_atm_hist_fb`

---

### Metrics labeled HYPOTHESIS (v3.2)

- **Normalized Debit** — whether low normalized debit at a given IV ratio predicts higher transform profit: unvalidated.
- **Theta Differential** — whether net daily theta magnitude at entry predicts speed/magnitude of transform: unvalidated.
- **Transform Order Mark / Transform Difference** — whether difference ≥ 5 is the right threshold and whether it predicts favorable transformation economics: unvalidated. Threshold = 5.0 is a working assumption pending live fill calibration.
- **IV Ratio Percentile** — whether high percentile at entry is favorable: HYPOTHESIS from v3.0, remains unvalidated.

---

### Commit command

```bash
git add app.py iv_engine.py db.py pages/journal.py DEV_JOURNAL.md DOCUMENTATION.md
git commit -m "v3.2: entry analysis overhaul, layout reorder, IC payoff chart, normalized metrics, transform order mark, weekend fallbacks"
git push
```

---


## 2026-06-27 — Dashboard v3.1 Complete: Trade Journal CRUD, Guided Edit Wizard, Direct-Close Path, Live IC P&L

**Status:** Complete — all changes confirmed working by Chandan.
**File changed:** `pages/journal.py` (sole file for this entire release).
**DB changes:** two additive `ALTER TABLE` column migrations added to `init_trades_table()` in `db.py`; a new `delete_trade()` function added after `update_trade()`.

---

### Context

v3.1 is an exclusively Trade Journal release. The main dashboard (`app.py`, `collector.py`, `iv_engine.py`, `schwab_client.py`, `config.py`) is untouched. All changes live in `pages/journal.py` and minor additions to `db.py`.

---

### 1. Schema additions (`db.py`)

Two new columns added via migrations inside `init_trades_table()` — safe on existing databases; `ALTER TABLE` is wrapped in `try/except` so it is a no-op if the column already exists.

- `transform_commissions REAL` — stores commissions/fees paid at the transformation or direct-close step, separate from the entry-level `commissions` column.
- `close_type TEXT` — values `"transform"` (IC conversion path), `"direct"` (all legs closed manually before/without transformation), or `NULL` for legacy records. Drives display branching throughout the journal.

New function `delete_trade(db_path, trade_id)` — single `DELETE WHERE trade_id = ?` query; called only after explicit user confirmation in the UI.

---

### 2. P&L terminology standardised throughout

Three terms now have precise, consistent meanings everywhere in the journal page, column labels, metric displays, and docstring:

- **Realized P&L** = locked/closed profit before fees. For IC trades: `profit_locked_in = transform_credit − entry_debit`. For direct closes: `net_proceeds − entry_debit`. Fixed at the moment of transformation or close.
- **Unrealized P&L** = current IC mark value vs fill prices for each leg. Only meaningful while the IC is still open. Displayed in the IC tab.
- **Net P&L** = Realized P&L − Total Fees. For open IC positions: Realized + Unrealized − Fees.

The old label "Net Profit Locked In" was renamed to "Realized P&L" throughout. The strategy-statistics KPI formerly called "Total Net Profit" is now "Total Net P&L".

---

### 3. Master Log enhancements (Overview page)

Three new columns added to the Master Log table:

- **Fees ($)** = `commissions + transform_commissions` (via new `total_fees(t)` helper). Zero-commission trades show "—".
- **Max Loss** = worst-case exposure at each trade status. Open trades: `−(total_debit × 100 × contracts)`. Transformed/not risk-free: `−ic_worst_case`. Transformed/risk-free: "Risk-Free". Expired/Closed: actual `final_pl` if negative.
- **Net P&L** = Realized P&L − Total Fees for completed and transformed trades; "—" for open positions.

**`compute_stats` fix:** Total Fees KPI was previously summing only entry `commissions`, silently ignoring `transform_commissions`. Fixed by replacing the list comprehension with `sum(total_fees(r) for r in rows)`.

**`compute_stats` Closed/Expired unification:** the completed-trade filter now reads `r["status"] in ("Expired", "Closed")` instead of `r["status"] == "Expired"`. Status `"Closed"` means a trade was manually closed before or without IC transformation (see §4 below). Both statuses count toward all statistics identically.

The statistic formerly called "Avg Transform Credit" is now "Avg Close Credit" (covers both IC transform credits and direct-close proceeds).

---

### 4. Direct-close path — trades that never transform (`close_type = "direct"`)

Added a first-class workflow for trades closed without converting to an IC.

**Sidebar navigation:** "🔄 Record Transformation" renamed to "🔄 Close / Transform".

**New toggle at the top of that page:** "Transform to Iron Condor" / "Close Position Directly". Both modes are supported in the same page; the radio is driven by `close_mode_radio` session state key, following the same `_pending_close_mode` intermediary pattern as the main navigation to allow programmatic pre-selection without triggering the Streamlit keyed-widget write error.

**"Close Position Directly" form:** Close Date, Close Time (ET), SPX at Close, Net Proceeds / share (allows negative for loss closes), Commissions. On save: status → `"Closed"`, `close_type = "direct"`, `result_date` and `final_pl` populated immediately — no separate "Mark Expired" step needed for this path.

**Transformation / Close tab (Trade Detail):** now branches on `close_type`. If `"direct"`: shows Close Date, SPX, Net Proceeds, Realized P&L/share and /contract, Net P&L (after fees). If `"transform"` or `NULL`: existing IC transformation display unchanged.

**IC tab guard:** if `close_type == "direct"`, shows "N/A — closed directly" instead of the IC display.

**`get_close_type(t)` helper:** safely reads `close_type` from a `sqlite3.Row` (column may not exist in rows from before this migration).

---

### 5. CRUD operations for all manually-entered data

Every piece of manually-entered data in the Trade Journal now supports full Create, Read, Update, Delete.

**Trade CRUD (initial entry):**
- Edit: via Master Log actions row (selectbox + "✏️ Edit" button) — opens Log a Trade pre-populated.
- Delete: "🗑️ Delete" button in Master Log → inline confirmation → `db.delete_trade()`.
- Success messages: "Trade logged successfully." (new) / "Changes saved successfully." (edit). Previously the `st.success()` was immediately followed by `st.rerun()` so the message was never rendered; fixed by storing the message in `_success_msg` session state and displaying it on the next render before clearing.

**Transformation / Close CRUD:**
- Edit Transformation: previously lived in the Transformation tab of the Trade Detail; in v3.1 it is removed from that location. The only edit entry point is the guided wizard (see §6).
- Edit Close: same removal — edit access is exclusively through the Master Log.
- Delete Transformation and Delete Close Record buttons are also removed from the Trade Detail. The Transformation / Close tab is now read-only.

**Navigation fix:** the `st.radio` sidebar widget was using `index=_NAV_OPTIONS.index(st.session_state["_nav_page"])` to allow programmatic navigation, which caused a double-click bug (the `index=` argument resets the widget's selection on every rerun, so first click reverted to the old page). Fixed with a `_pending_nav` intermediary: programmatic navigation writes to `_pending_nav`; at the top of each script run (before any widget renders), `_pending_nav` is transferred into `page_mode_radio` (the keyed radio widget's session state key) and cleared. This is the safe write-before-render pattern. The same pattern is used for `_pending_close_mode`.

---

### 6. Guided Edit Wizard (two-step flow)

Clicking "✏️ Edit" in the Master Log now launches a guided two-step workflow instead of dropping the user into a standalone edit form.

**Step 1 — Log a Trade (wizard mode):**
- Two buttons at the top, outside the form: "← Cancel Edit" and "Move to Step 2 →".
- **Cancel Edit:** clears all wizard session state, returns to Overview. Zero DB writes — the trade record is unchanged.
- **Move to Step 2 →:** zero DB writes. Navigates to Close / Transform with message "Log Entry unchanged. Review Close / Transform record below." The close mode radio is pre-selected based on the trade's existing `close_type`.
- **Save Changes (form button):** saves the initial trade, then navigates to Close / Transform with message "Initial Trade saved. Review Close / Transform record below."
- Both step-2 paths land on the same page; only the message and whether a DB write occurred differ.

**Step 2 — Close / Transform (wizard mode):**
- Two buttons at the top: "← Go Back" and "Cancel".
- **← Go Back:** restores `edit_trade_id = wizard_trade_id`, navigates back to Step 1. Form re-populates from the last saved DB state (unsaved Step-2 edits discarded by design — confirmed acceptable).
- **Cancel:** clears all wizard state, returns to Overview.
- **Save Changes with values entered:** IC Transform → saves → navigates to "⏰ Mark Expired" (IC still needs expiry tracking). Direct Close → saves → navigates to "📊 Overview" (trade is fully recorded).
- **Save Changes with nothing entered** (credit ≤ 0 for IC; no close_time for direct close): shows "Position hasn't been transformed or closed." with a single "← Return to Overview" button. Nothing is saved. The form does not render.

**Session state keys driving the wizard:** `_wizard_mode` (bool), `_wizard_trade_id` (str, the anchor trade ID that persists across both steps), `_pending_close_mode` (str, pre-selects the close-mode toggle).

---

### 7. Unsaved changes protection

When the user navigates away from an active edit form (Log a Trade or Close / Transform) via the sidebar radio, a guard intercepts and redirects them back with a warning.

**How it works:** after the radio renders, a post-radio check compares `page_mode` against the expected edit page (`"➕ Log a Trade"` if `edit_trade_id` is set; `"🔄 Close / Transform"` if `edit_transform_id` is set). If they disagree, the guard sets `_show_leave_warning = True`, stores the intended destination in `_interrupted_nav_dest`, and sets `_pending_nav` back to the edit page. On the next render the user sees the edit page with an inline warning.

**Dialog:** "You have unsaved changes. If you leave, edits will be discarded." — two buttons: "Leave (discard changes)" and "Stay on page". Leave clears edit state and navigates to the original intended destination. Stay clears the warning and stays put.

**Streamlit limitation noted:** Streamlit's `st.form` only delivers widget values on submit; there is no onChange for fields inside a form. The guard detects "you are in edit mode" (session state flag) rather than "you changed a specific field value." This is the practical equivalent and covers all real use cases.

---

### 8. Inspect Trade auto-navigation

Selecting any trade from the "Inspect Trade" sidebar dropdown while on a non-Overview page now automatically navigates to Overview to show the trade detail. Previously, selecting a trade from another page did nothing visible — the user had to manually navigate back.

**Implementation:** `_last_selected_id` session state key tracks the previous selection. When `selected_id != _prev_sel` and `page_mode != "📊 Overview"` (and no leave-warning is showing), `_pending_nav = "📊 Overview"` is set and `st.rerun()` is called. The unsaved-changes guard fires on the next run if applicable, preventing silent discard.

---

### 9. Live IC Position Monitoring (IC tab enhancement)

The "Live IC Marks" section in the Iron Condor tab now includes per-leg fill prices and unrealized P&L.

**New helper `get_ic_fills(initial_legs_json, transform_legs_json)`:** extracts fill prices for each IC leg from the stored JSON blobs. Short call and short put fills come from `initial_legs` (the original diagonal entry). Long call and long put fills come from `transform_legs` (the "Buy to Open" wing legs added at transformation).

**Enhanced per-leg table:** Strike | Fill | Bid | Ask | Mark | Unreal P&L /sh | Unreal P&L /ct. Per-leg unrealized P&L: short legs = `(fill − mark) × 100 × contracts` (positive when mark < fill); long legs = `(mark − fill) × 100 × contracts` (positive when mark > fill).

**P&L summary row (4 metrics):** Realized P&L /ct (locked at transformation), IC Unrealized P&L /ct (sum of per-leg mark-vs-fill), Total Fees, Net P&L /ct (Realized + IC Unrealized − Fees). Caption explains what each represents.

---

### Open questions / follow-ups for v3.2

- Validate IV Ratio favorability using real fill outcome data — the `trades` table schema now supports this; the Regime Analysis sub-tab is the mechanism.
- Calibrate live transformation threshold (~$6.50–$7.00 expected vs $5.00 paper) from real fills.
- Net Theta Advantage and Days to Risk-Free metrics remain on the roadmap (Phase 3).
- Consider whether "Mark Expired" should also be surfaced from within the wizard flow or stays as a separate step — currently wizard ends at Mark Expired for IC transforms.

---

## 2026-06-26 — v3 final: IV-ratio/level analytics (stacked panel, scatter, Regime Analysis sub-tab)

Closes Dashboard v3. Three new analytics surfaces plus the supporting read helper
and full DOCUMENTATION.md §11.

**`app.py` (Calendar Edge — additive; existing dual-axis chart KEPT):**
- `_RATIO_THRESHOLDS` / `_RATIO_BANDS` + `_banded_ratio_traces()` — builds a
  *continuous* multicolor ratio line by interpolating the exact crossing point at
  each threshold (0.70/1.00/1.30) and emitting one trace per band, with boundary
  points shared between adjacent bands so segments touch (no gaps). Validated on a
  synthetic series crossing all three thresholds both directions.
- New collapsed expander **"Stacked view"**: `make_subplots` 2-row — top Front/Back
  ATM IV on one axis (gap = spread), bottom the banded ratio with 1.00 solid +
  0.70/1.30 dotted reference lines. `_SESSION_RANGEBREAKS` on the shared x-axis.
- New collapsed expander **"Front vs Back scatter"**: x=Back IV, y=Front IV, y=x
  line, colored by time of day (Viridis). Reads level (radius) + structure (angle)
  in one view.
- Both in expanders (collapsed) to respect the anti-scroll preference.
- Colors are **regime labels, not favorability** — green ≥1 is requested shorthand
  for "backwardation"; amber <0.70 reads as 0DTE/EOD caution. Legend uses regime
  names. Reconciled against the §10.3 "no valenced coloring" rule in DOCUMENTATION.

**`db.py`:**
- `get_entry_iv_context(db_path, entry_ts_utc, front_expiry, back_expiry,
  call_strike, put_strike)` — read-only. Finds the COMPLETE snapshot nearest the
  entry timestamp (`ABS(strftime('%s',...))`), returns at-strike Front/Back IV
  (avg of the call+put legs actually traded) + ratio + level √(F·B), plus ATM
  context. Decimals (caller ×100). No schema change; works retroactively, incl.
  T-001. Defensive: None on missing snapshot/legs.

**`pages/journal.py`:**
- New `📈 Regime Analysis` entry in the existing sidebar `page_mode` nav + an
  `elif` branch calling `render_regime_analysis(all_trades)`. Chosen over `st.tabs`
  to avoid nesting inside the Trade-Detail tabs (older-Streamlit risk) and over a
  full-body reindent.
- `render_regime_analysis()`: per-trade entry-context reconstruction (parse
  `initial_legs` → front/back expiry + call/put strike; ET→UTC via zoneinfo;
  `db.get_entry_iv_context`). Front-vs-Back scatter split into 4 quadrants by
  **median level (√(F·B))** [purple hyperbola] and **median ratio** [orange ray],
  points colored by realized `profit_locked_in` (RdYlGn, cmid=0); open trades grey
  hollow. Stratified 2×2 cell-mean table with n; n<5 flagged as noise. Caveats
  expander (sample size, pre-commit/overfitting, selection bias, confounds).
- New imports: `math`, `plotly.graph_objects`, `zoneinfo.ZoneInfo`.

**Why level = √(F·B) not Front IV:** intraday R≈F/(sticky back), so Front IV and
Ratio are collinear — splitting on Front×Ratio empties two cells and confounds the
test. Level and R are a near-orthogonal reparametrization of (F,B), so all cells
populate and "does R matter after controlling for level?" is cleanly separable.

**Status:** the Regime Analysis sub-tab is the *mechanism* to validate the
IV-ratio-favorability HYPOTHESIS (§3.1). It asserts nothing until cells carry real n
(~10–15). With T-001 only, expect "insufficient context / n<5" states until live
collection logs more entries.

**Verification:** all three files `python -m py_compile` clean. Banded-line
continuity and the entry-context ET→UTC + level math validated with synthetic data.
**Not runtime-verifiable here** (no live DB): `get_entry_iv_context`'s
nearest-snapshot join and the populated regime scatter — confirm on first run that
T-001 either matches a snapshot or shows the graceful "not matched" state.

**Docs:** DOCUMENTATION.md → v1.3 (changelog row + new §11 with worked examples for
the banded line, the scatter, and the quadrant test; §10.3 valenced-coloring item
amended with the v3 regime-label nuance).

---



**Request:** After the rangebreaks fix, the 5D view looked right but showed a small break
between each session's close and the next session's open. Make the line continuous.

**Changed (all in `app.py`, charts only):**

1. **Removed `_session_breaks()` and all three calls** (`atm_merged`, `cm`, `pm`). That
   helper inserted a NaN between sessions specifically to break the line; removing it lets
   the line connect across the gap. Because `_SESSION_RANGEBREAKS` already collapses the
   empty time, the across-session connector is a short continuous segment, not a long
   diagonal — so the result is one continuous, smooth line.

2. **Added holidays to `_SESSION_RANGEBREAKS`:** `dict(values=sorted(config.MARKET_HOLIDAYS))`.
   Now that sessions are joined, a holiday sitting inside a 10D/20D window would otherwise
   reintroduce a diagonal (the line would connect across a data-less weekday that
   rangebreaks didn't collapse). Collapsing full-day holidays keeps that connector short
   too. Weekends, overnight, and holidays are now all collapsed; the line is continuous
   across every one of them.

3. **Reverted the `.dropna()`** on the `sample_size_warning` call — no NaN breakers are
   inserted anymore, so it's back to the original `atm_merged["iv_ratio"]`.

**Net effect:** Continuous line across sessions on all multi-day views; "Today" view
unchanged. All expected non-trading time (weekend/overnight/holiday) collapsed, so no
diagonal ramps anywhere.

**Impact:** Pure charting change. No DB, schema, collector, or `iv_engine` math touched.
`config.MARKET_HOLIDAYS` is now consumed by the dashboard for the first time (previously
collector-only); confirmed it loads as a 9-element set of 'YYYY-MM-DD' strings, which is
the exact format Plotly `rangebreaks.values` expects. `python -m py_compile` clean.

**Known residual edge case (intentional):** A *mid-session* collector outage (data hole
during 9:30–16:00 that isn't a holiday) is neither broken nor collapsed, so it would draw
a straight connector across the hole. This is rare and arguably useful as a visible
data-quality signal. If it ever becomes a nuisance, reintroduce a high-threshold
gap-break (e.g. only break holes > 60 min) — but that reopens the break-vs-join tradeoff,
so left as-is for now.

---



**Problem (from review):** On 5D/10D/20D views, the Calendar Edge and Selected-Strike
IV charts drew long diagonal lines between sessions. Cause: the collector is offline
outside 9:30–16:00 ET, so there are no points overnight/weekends; Plotly connected the
last point of one session straight across the empty hours to the first point of the next.
The dead time also consumed huge horizontal space ("time is huge on a scale"). "Today"
view looked fine because it is a single continuous session.

**Changed (all in `app.py`, charts only):**

1. **`_SESSION_RANGEBREAKS` constant** — Plotly `xaxis.rangebreaks` that collapse
   non-trading time: weekends (`bounds=["sat","mon"]`) and overnight
   (`bounds=[16, 9.5], pattern="hour"`). Bounds are in `DISPLAY_TIMEZONE`
   (America/New_York), so they are DST-safe. Applied to both the Selected-Strike IV
   chart (`fig_str`) and the Calendar Edge chart (`fig_atm`).

2. **`_session_breaks()` helper** — Inserts a single NaN row wherever the gap between
   consecutive points exceeds 30 min, so Plotly *breaks* the line across
   overnight/weekend/holiday/outage gaps instead of connecting them. Normal intraday
   polling tops out at 5 min (`POLL_INTERVAL_NORMAL=300`), so a continuous session is
   never broken. Applied to `atm_merged` (Calendar Edge) and to `cm`/`pm`
   (Selected-Strike calls/puts) right before plotting.

   Implementation note: the breaker timestamps must stay tz-aware — using
   `Series.values` strips the timezone and makes the subsequent `sort_values` raise
   "Cannot compare tz-naive and tz-aware". Fixed by keeping the tz-aware Series
   (`.shift(1)[gap] + 1min`) instead of `.values`.

3. **`sample_size_warning`** now receives `atm_merged["iv_ratio"].dropna()` so the
   inserted NaN breakers don't inflate the sample count.

**Net effect:** Within a session the line is continuous and smooth (unchanged from
before). Between sessions the line breaks cleanly and the empty time is collapsed, so
days sit adjacent — no diagonal ramps, no giant empty bands. "Today" view is
pixel-identical (no gaps to break, nothing outside one session to collapse).

**Why this approach over alternatives:** NaN-only would have left big blank vertical
bands (still "huge on scale"). Rangebreaks-only would leave short connectors that can
misread as smooth overnight moves and would still draw a diagonal across a holiday with
no data. Combining the two is robust to weekends, holidays, and collector outages alike
without hardcoding the holiday calendar.

**Impact:** Pure charting change. No DB, schema, collector, or `iv_engine` math touched.
Logic validated on a synthetic two-session series (correct breaker count/placement,
no within-session breaks, single-session input unchanged, dropna count intact).
`python -m py_compile` clean.

**Open questions / follow-ups:**
- 30-min break threshold is a constant in `_session_breaks`. If a future high-resolution
  mode ever polls slower than 30 min midday it would false-break; not a concern at
  current intervals.
- `MARKET_HOLIDAYS` exists in `config.py` but is intentionally *not* wired into
  rangebreaks — the gap-break handles holidays generically. Could switch to explicit
  holiday `values` rangebreaks later if a fully gapless axis is preferred.

---


**Changed (all in `app.py`):**

1. **Compaction CSS** — New `st.markdown(<style>)` block injected immediately after
   `set_page_config`. Reduces `.block-container` top padding (1.2rem), tightens the
   global vertical-block `gap` to 0.6rem, thins `hr` dividers, and dims metric labels.
   Tuned for "compact but not congested." This is the main fix for the excessive
   scrolling / wasted top whitespace called out in the v3 review video.

2. **Removed the header mini intraday sparkline.** The 60px Plotly line chart
   (`mini_fig`) under the SPX price is deleted. `spx_intraday` is still loaded because
   `get_prior_session_close` falls back to the first intraday snapshot; only the chart
   render is gone. Reviewer didn't like the line chart aesthetically and it cost
   vertical space.

3. **`pts ↔ %` toggle moved next to the change value.** The dedicated `h_btn` header
   column is removed; the toggle button now renders inside `h_spx` directly beneath the
   price/change line. Header columns went from `[4,1,2,2,4]` to `[5,2,2,4]`.

4. **DTE in expiry dropdowns.** New `dte_by_expiry` map + `_exp_label()` helper render
   each option as `"2026-06-29  (3D)"` via `format_func` on both Front/Back selectboxes.
   The selectbox value is still the raw date string, so nothing downstream changed.

5. **Expiry Detail now shows date AND DTE.** Label changed from `"Front (0 DTE)"` to
   `"Front · 2026-06-26 · 0 DTE"` in both the data and N/A branches.

6. **Period selector (Today/5D/10D/20D) moved to the right, shared.** Previously a
   standalone full-width radio above the left/right column split. Now rendered
   right-aligned at the top of `right_col`, directly above the Selected-Strike IV chart.
   It remains a **single shared** control: `period_days`/`period_label` are defined inside
   `right_col` (which executes before Calendar Edge) and drive both charts. Calendar Edge
   gets a right-aligned read-only `Range: <label>` indicator above its chart instead of a
   duplicate widget (Streamlit can't bind one widget to two render points).

**Why:** Direct implementation of the v3 review video. Decisions confirmed with Chandan:
(A) shared period selector, not independent; (B) **GEX left untouched.**

**Impact:**
- Pure UI/layout changes. No DB reads/writes, no schema, no `iv_engine` math touched.
- `collector.py` untouched. Reader/writer split preserved.
- `period_days` now defined inside `right_col`; verified it precedes every use
  (Selected-Strike IV chart at lines 648–651, Calendar Edge `_load_atm_hist` at 736–737).
  Historical Stats uses its own loop `days`, unaffected.
- `app.py` passes `python -m py_compile`. No stale refs to `mini_fig`/`h_btn`.

**Deliberately NOT changed (per review):**
- **Max |GEX| Strike — not made "live."** Confirmed it already recomputes every refresh
  from the latest snapshot; it looks frozen intraday because GEX is OI-dominated and OI
  is a once-daily figure (updated overnight by OCC). The strike flips only when 0DTE
  gamma explodes near the close — correct gamma-wall behavior, not a freshness bug.
  Chandan elected to leave it alone for now. A volume-weighted intraday "flow" variant
  remains available as a future option if he later wants an intraday-responsive number.
- VIX, the Front/Back/Strike control panels, and the contango/favorability info banner —
  left as-is per the review.

**Open questions / follow-ups:**
- The unused `spx_intraday["ts_et"]` column assignment (left over from the removed
  sparkline) still computes each run. Harmless and cheap; can be pruned in a later pass.
- `dc1/dc2` ATM IV metrics at the bottom of Calendar Edge still show only `(N DTE)` not
  the date — left for consistency-vs-scope reasons; trivially addable if wanted.

---



**Changed:**

**`db.py`** — Appended new section (from `db_additions.py`) containing:

- **`_TRADES_DDL`** — `trades` table + two indexes (`idx_trades_status`, `idx_trades_entry_date`). All monetary values stored per-share. Leg data as JSON text columns to keep schema flat. Separate from the main `_DDL` constant so `SCHEMA_VERSION` and `init_db()` are unaffected.
- **`init_trades_table(db_path)`** — Idempotent DDL executor for the trades table. Called by `journal.py` on every page load; never called by `init_db()` or `collector.py`.
- **`get_next_trade_id(db_path)`** — Returns the next sequential ID string (`T-001`, `T-002`, ...) based on COUNT(*).
- **`insert_trade(db_path, trade_dict)`** — Inserts a new trade; auto-populates `created_at`/`updated_at`.
- **`update_trade(db_path, trade_id, **fields)`** — Updates arbitrary columns dynamically; auto-sets `updated_at`.
- **`get_all_trades(db_path)`** — All trades newest-first.
- **`get_trade(db_path, trade_id)`** — Single trade by ID.
- **`get_eod_spx(db_path, date_str)`** — Last COMPLETE snapshot `underlying_price` on or before `date_str`. Used by the Mark Expired form to auto-suggest SPX close.
- **`get_ic_marks(db_path, ic_expiry_date, sc, lc, sp, lp, eod_date=None)`** — Queries `option_rows` for bid/ask/mark on the 4 IC legs from the latest COMPLETE snapshot. If `eod_date` is set, returns marks from the last snapshot of that date (enables EOD unrealized P&L for past sessions). Returns `cost_to_close = mark(sc) + mark(sp) - mark(lc) - mark(lp)`; caller computes `unrealized = profit_locked_in - cost_to_close`.
- **`seed_t001(db_path)`** — Inserts T-001 (first live trade, 2026-06-26) if not already present. No-op if T-001 exists.

**`pages/journal.py`** — New Streamlit page discovered automatically by Streamlit's multi-page mechanism. Navigate via the sidebar after `streamlit run app.py`.

Five modes (sidebar radio):

- **📊 Overview** — Strategy Statistics (14 KPIs in 5×3 grid) + Master Log table + Trade Detail for selected trade. Detail view has 5 tabs: Initial Position · Transformation · Iron Condor · Expiration · Notes. Iron Condor tab pulls live marks from `option_rows` and computes current unrealized P&L; also shows EOD marks for the entry day when the trade spans multiple days.
- **➕ New Trade** — 4-leg entry form. Day-of-week auto-computed from entry date.
- **🔄 Record Transformation** — Select an Open trade; enter 4 transformation legs + credit received. IC structure (strikes, wings, max profit, worst case, risk-free flag) is auto-derived from initial + transformation legs via `derive_ic()`.
- **⏰ Mark Expired** — Select an active trade; enter result date + SPX at expiry + final P&L. `expired_inside_wings`, `expired_between_shorts`, and `outcome` are auto-computed from SPX vs IC strikes. SPX close auto-suggested from `db.get_eod_spx()` if IC expiry date is in the past.
- **✏️ Edit Notes** — Free-text notes editor for any trade.

**Strategy Statistics (14 KPIs):**
Total Trades · Win Rate · Average Winner · Average Loser · Profit Factor · Expectancy · Avg Entry Debit · Avg Transform Credit · Avg Holding Time (days) · Avg Time to Transform (minutes) · Avg Max Drawdown (placeholder — requires intraday marks) · Largest Winner · Largest Loser · Total Fees · Total Net Profit

**T-001 auto-seeded** with full transformation data on first journal load.

**Why:**
User's question: "Is the trade journal automatic — can it pull mark prices from the collector and compute unrealized P&L?" Answer: yes, because `option_rows` already stores bid/ask/mark for every strike at every snapshot. The React browser artifact had no access to SQLite; the Streamlit page does. This replaces the browser-based artifact entirely for live data use. The React artifact remains available as a standalone offline/demo tool.

**Impact:**
- `db.py` writer/reader split preserved. `journal.py` is a pure reader for market data and a writer only for the `trades` table. `collector.py` never touches `trades`.
- `init_db()` and `SCHEMA_VERSION` unchanged. Backward compatible.
- The `trades` table schema includes all fields requested: SPX at entry, SPX at expiry, EOD unrealized P&L (computed on-demand, not stored), expired inside wings, expired between shorts, outcome, all 14 strategy stats.
- Avg Max Drawdown is shown as `—` pending intraday mark history. Would require storing per-snapshot IC marks; flagged as a future enhancement.

**Open questions / follow-ups:**
- Avg Max Drawdown requires querying `option_rows` at every snapshot during the trade's life. Feasible but adds a slow query; defer to v4.
- `pages/journal.py` should be renamed `pages/2_📒_Trade_Journal.py` for a cleaner sidebar label in Streamlit (optional, purely cosmetic).
- Transformation threshold calibration from T-001: $5.90 net credit achieved in 13 minutes. Target range confirmed as $5–6 for real fills.

---



## 2026-06-26 — Dashboard v2 Bug Fixes + Layout Refinements (session closed)

**Changed:**

**`db.py`** — Three new read functions added to the end of the Read Operations section:

- **`get_prior_session_close(db_path, session_date)`** — Returns `underlying_price` from the last COMPLETE snapshot strictly before `session_date`. Used by app.py to compute `daily_change = current_spx − prior_session_close` (≈ yesterday's closing price). Falls back to the first intraday snapshot only when no prior-session data exists (first ever collection day).
- **`get_spx_intraday_today(db_path, session_date=None)`** — Returns `(snapshot_timestamp, underlying_price)` for all COMPLETE snapshots on `session_date`. Used for the mini intraday sparkline in the header. Parameter is a 'YYYY-MM-DD' string derived from the latest snapshot's own timestamp.
- **`get_all_expiry_atm_iv_today(db_path, session_date=None)`** — Returns `(snapshot_timestamp, expiry_date, dte, atm_avg_iv)` for all COMPLETE snapshots on `session_date`. Powers the Pair Scanner's intraday IV ratio computation.

**Root-cause fix: `get_spx_intraday_today` missing `def` line.** A prior `str_replace` operation consumed the function definition line while inserting `get_prior_session_close`. The function body was present but the module attribute was invisible to Python. Caught by `AttributeError: module 'db' has no attribute 'get_spx_intraday_today'` on first run. Fixed by restoring the `def` line.

**Root-cause fix: Pair Scanner showing 0 pairs.** Original implementation used SQLite `date('now')` as the session boundary. `date('now')` returns the current UTC calendar day. When the dashboard is opened after market hours or before the next session, the current UTC date has no snapshots, so the scanner returned empty. Fix: derive `session_date = snap_ts_str[:10]` from the latest snapshot's own timestamp (not the UTC clock) and pass it as a parameter to both `get_spx_intraday_today` and `get_all_expiry_atm_iv_today`. Dashboard now always shows the most recent session's data regardless of when it is opened.

**`app.py`** — Major layout revision (v2 final state):

*Daily change fixed:*
- Previous: `open_price = first intraday snapshot of the day` → `daily_change = current - open`
- Corrected: `prev_close = db.get_prior_session_close(...)` → `daily_change = current - prev_close` (previous trading session's last COMPLETE snapshot ≈ official close). Reference line in the mini chart updated to say "Prev Close XXXX" instead of "Open XXXX".

*Max Gap moved from Controls Row to Pair Scanner filter row:*
- Controls Row reduced from 5 columns to 4: Front Expiry, Back Expiry, Call Strike, Put Strike. Max Gap is not a "trade setup" input; it is a scanner filter. Moved to the Pair Scanner filter row alongside Min DTE and Max DTE, consistent with the FLUX reference design.

*Header: mini SPX sparkline embedded:*
- Full-width SPX Intraday chart removed from main layout.
- A compact 60px Plotly line chart (no axes, no hover, no mode bar) embedded in the left column of the header row, directly below the SPX price text. Green if positive day, red if negative. Gives quick visual price context without consuming page space.

*IV Structure per Strike promoted to main chart area:*
- Previously at the bottom (Section 7). Now the first full chart below the Controls Row — the primary "is now a good time to enter?" visual.
- Shown in a `st.columns([1, 3])` layout: left column = Expiry Detail + Strike Detail, right column = the IV chart.

*Expiry Detail + Strike Detail panel restored:*
- Present in v1 of the dashboard; removed in the initial v2 rewrite. Brought back.
- **Expiry Detail:** shows ATM IV % and tick-change (↑/↓ with color) for both front and back expiries. Source: `db.get_latest_atm_iv_snapshots(n=2)`.
- **Strike Detail:** for each leg, shows `IV → F x.xx% / B x.xx% · Ratio x.xxxx` and `Mark → F $x.xx / B $x.xx`. Dollar mark is the new addition not previously in the v1 layout; sourced from `StrikeContract.mark`.

*Calendar Edge moved up:*
- Previously the second-to-last section (before Transform Credit). Now sits directly below the 2-column IV chart section, above Historical Statistics and Pair Scanner. Rationale: the ATM IV chart + regime interpretation text is pre-entry macro context; it should be visible before scrolling to the scanner.

*`ts_now` computation moved earlier:*
- Calendar Edge uses `ts_now` (ATM IV ratio). Historical Stats also uses it. Since Calendar Edge is now above Historical Stats, `ts_now` is computed once right after controls (before both sections). No functional change; just moved to satisfy the new dependency order.

**Final v2 section order (top to bottom):**
1. Header — SPX price + mini sparkline, pts/% toggle, VIX, Max|GEX| Strike, staleness
2. Controls Row — Front/Back Expiry, Call/Put Strike (4 cols)
3. Period radio — Today / 5D / 10D / 20D (controls Sections 3 and 4 charts)
4. [2-col] Expiry Detail + Strike Detail (left) | Selected-Strike IV chart (right)
5. Calendar Edge — ATM IV chart, metric strip (Ratio/Front IV/Back IV/IV Index), day-change metrics
6. Historical Statistics — Today / 5D / 10D / 20D ratio range bars
7. Pinned Pairs — persistent watchlist from `pinned_pairs.json`
8. Pair Scanner — Min DTE / Max DTE / Max Gap / Rescan; table sorted by Drop% ascending
9. Transform Credit — Trade Quality Score (placeholder pending Phase 3)

**Why:**
The v2 rewrite was delivered in one session, then iteratively corrected across multiple feedback cycles:
1. Scanner empty → `session_date` fix (fundamental design error: relying on UTC clock instead of snapshot's own date)
2. `def` line missing → pure `str_replace` tooling error (old_str consumed the first line of the next function)
3. Daily change wrong → prior session close is the correct reference, not the first intraday snapshot
4. Max Gap location → user flagged against the FLUX reference design
5. Mini chart, Expiry/Strike Detail → user flagged against the previous v1 dashboard's feature set

**Impact:**
- Dashboard v2 is now closed at this feature set. No further changes planned until v3.
- `pinned_pairs.json` must remain in `.gitignore` (user preference data, not code).
- `collector.py` and `iv_engine.py` were not touched in this session.
- All `db.py` new functions are pure reads; the writer/reader split is preserved.

**Open questions / follow-ups (carry to v3):**
- `trades` table + trade logging not yet implemented (approved in DOCUMENTATION.md §10.1 — build task for v3).
- Net Theta Advantage ($/day) and Days to Risk-Free are still placeholders in Transform Credit — Phase 3.
- Live transform threshold (~$6.50–$7.00) still needs calibration from real fills.
- GEX computation uses ±300pt strike window (collector's fetch width), not the full SPX open-interest distribution. Good enough for identifying the dominant strike near spot; not a complete GEX surface.
- `st.dataframe(on_select="rerun")` requires Streamlit ≥ 1.29. If pin/unpin buttons don't appear, upgrade Streamlit.


---


## 2026-06-25 — Dashboard v2: Pair Scanner, Pinned Pairs, SPX Intraday Chart, GEX, Layout Reorganization

**Changed:**

**`app.py`** — Complete replacement (~380 lines). Full layout restructure and five new capabilities:

*Layout changes (top to bottom):*
- **Section 1 — Header:** SPX price + daily change (pts or %, toggle button), VIX, Max|GEX| Strike, staleness badge.  2-column layout removed; single linear flow throughout.
- **Section 2 — Controls Row:** Five-column row: Front Expiry, Back Expiry, Call Strike, Put Strike, Max Gap (new). Live per-strike contract data (Front IV / Back IV / Ratio) shown directly below controls.
- **Section 3 — SPX Intraday Chart:** Green line if day positive, red if negative. Dotted horizontal reference line at today's open price (first snapshot of the day). Labeled "Open XXXX" at right edge.
- **Section 4 — Historical Statistics:** Always shows Today / 5D / 10D / 20D range bars (removed 15D and 1M). No longer controlled by the period radio — always fixed windows. Ratio range bars for the selected front/back pair.
- **Section 5 — Pinned Pairs:** Persistent pair watchlist from `pinned_pairs.json`. Shown regardless of DTE/gap filters. Row selection + "Unpin Selected" button.
- **Section 6 — Pair Scanner:** All valid (front, back) expiry combinations from today's session data. Min DTE / Max DTE controls + Rescan button. Default sort: Drop% ascending (biggest intraday drop first). Row selection + "Pin Selected" button. Native column-header sorting for ad-hoc sorts.
- **Period selector radio** (Today / 5D / 10D / 20D — 15D and 1M removed) placed above Section 7, controls only the IV charts in Sections 7 and 8.
- **Section 7 — IV Structure per Strike:** Moved lower (was above scanner).
- **Section 8 — Calendar Edge:** Moved lower. ATM IV chart + metric strip + day-change metrics unchanged.
- **Section 9 — Transform Credit:** Very bottom. Trade quality score unchanged.

*New features:*
- **Max Gap control:** `st.number_input` in the Controls Row. Default = 1. Filters Pair Scanner (not Pinned Pairs). Note: Fri→Mon = 3 calendar days for SPX dailies.
- **SPX intraday chart:** Reads from `db.get_spx_intraday_today()`. Open price = first COMPLETE snapshot of the UTC calendar day. Day color = green/red/grey (no data).
- **pts ↔ % toggle:** Session-state button in the header. Toggles between `+X.X pts` and `+X.XX%` for daily SPX change.
- **GEX computation:** Computed from `chain_df` (gamma and open_interest already in `option_rows`). Per-strike net GEX = gamma × OI × 100 × SPX × ±1. Max |net GEX| strike and call/put dominance displayed in header. No schema change required.
- **Pair Scanner:** `_compute_pair_scanner()` calls `db.get_all_expiry_atm_iv_today()`, pivots to (snapshot × expiry) matrix, computes ratio series for every (front, back) pair. Columns: Front, Back, Ratio, Day Chg, Drop%, Rise%, Chart (unicode sparkline), snapshots. Scanner DF computed once, shared by both Pinned Pairs (Section 5) and Pair Scanner (Section 6).
- **Pinned Pairs:** Persisted in `pinned_pairs.json` (project root). Always visible regardless of filters. Pin via row selection in Scanner. Unpin via row selection in Pinned table.
- **Unicode sparklines:** `_sparkline()` helper encodes up to 10 sampled ratio values as ▁▂▃▄▅▆▇█ string.

**`db.py`** — Two new read functions added before `update_snapshot_notes`:

- **`get_spx_intraday_today(db_path)`:** Returns `(snapshot_timestamp, underlying_price)` for all COMPLETE snapshots where `snapshot_timestamp >= date('now')` (UTC calendar day). Used for the intraday price chart and open-price daily change reference.
- **`get_all_expiry_atm_iv_today(db_path)`:** Returns `(snapshot_timestamp, expiry_date, dte, atm_avg_iv)` joining `atm_iv_by_expiry` + `snapshots` for all COMPLETE snapshots today. Used by `_compute_pair_scanner()`. At 5-min polling × 20 expiries × 6.5 market hours ≈ 1,560 rows/day — trivially fast.

Both functions use `date('now')` as the UTC day boundary — no timezone library needed because the collector only runs 13:30–20:00 UTC (within one UTC calendar day).

Module docstring updated to include the two new functions in the reader split.

**`pinned_pairs.json`** — New file written at runtime. Format: `[{"front_expiry": "YYYY-MM-DD", "back_expiry": "YYYY-MM-DD"}, ...]`. Add to `.gitignore` to avoid committing user-specific preferences.

**Why:**
The primary session goal was a major dashboard restructure that moves from a static pair selector to a live pair scanner — showing all valid (front, back) combinations across today's session and ranking by intraday ratio movement. The scanner answers "what's moving?" without requiring the user to manually try combinations. Pinned Pairs adds persistence so the specific pair under active management stays visible without re-selecting it.

Secondary goals: SPX intraday chart and GEX give market context (where is price relative to the open, what strike has the highest dealer exposure) without leaving the dashboard.

**Schema finding this session:** `gamma` IS stored in `option_rows` — confirmed from `db.py` review. GEX is therefore computable from `chain_df` with zero schema changes. This was discovered live during `db.py` inspection.

**Impact:**
- Old 2-column layout is gone. Single linear flow eliminates the "scroll left vs scroll right" problem.
- Historical Stats are now always a fixed 4-window view (Today/5D/10D/20D) rather than period-radio-controlled, giving a stable context strip regardless of chart zoom.
- The period radio now only controls the IV structure and Calendar Edge charts — a cleaner conceptual split.
- `db.py` collector write contract is unchanged — no collector.py changes required.
- `iv_engine.py` unchanged.
- `pinned_pairs.json` must be added to `.gitignore` to avoid committing pair selections between sessions.

**Open questions / follow-ups:**
- `pinned_pairs.json` should be added to `.gitignore` manually: `echo "pinned_pairs.json" >> .gitignore && git add .gitignore && git commit -m "ignore pinned pairs json"`.
- `st.dataframe(on_select="rerun")` requires Streamlit ≥ 1.29. If an older version is installed, pin/unpin buttons will not appear (no crash — the `hasattr(event, "selection")` guard handles it silently). Upgrade with `pip install --upgrade streamlit`.
- GEX is computed from the latest snapshot chain only (±300pt strike window). This is sufficient for identifying the dominant GEX strike near SPX but does not cover the full SPX open-interest distribution.
- `_compute_pair_scanner()` has no caching (`@st.cache_data` deliberately omitted). At ~1,560 rows/day the pivot completes in <10ms. If profiling ever shows this as a bottleneck, add `@st.cache_data(ttl=300)`.
- Net Theta Advantage ($/day) and Days to Risk-Free are still not built — both remain on the Must Have list from the June 23 session. Next build session should add those to Section 9 (Transform Credit).


---

## 2026-06-25 — Audit Pass v1.1: Retracted Regime Favorability, Removed Theta ETA, Fixed Terminology

**Changed:**

**`DOCUMENTATION.md`** — v1.0 → v1.1 (full rewrite, change-log row added):
- Retracted the "ratio < 1.0 is favorable / maximizes transformation credit" claim.
  It rested on a single paper trade (2026-06-23) — Category D evidence. Black-Scholes
  analysis run during the audit pointed the opposite way (high front IV relative to
  back gives more short-leg extrinsic to harvest), but a few modeled scenarios are not
  proof either. Net result: favorability demoted to an explicit `HYPOTHESIS` block,
  status "unknown, pending trade data."
- Fixed term-structure terminology to standard convention: backwardation/inverted =
  front IV > back (ratio > 1.0); contango/normal = front IV < back (ratio < 1.0).
  v1.0 had "inverted" attached to the wrong direction.
- Rewrote the Transformation → Iron Condor section to the actual workflow: KEEP the
  short front legs, CLOSE the back-dated longs, BUY protective wings in the front
  expiration. (v1.0 wrongly said "close the short front legs first.")
- Corrected expiry collection: "20 expirations by count (~35–50 DTE)", not "within
  20 calendar days."
- Reframed 100-pt strike width as an illustrative example, not a rule.
- Removed Theta ETA from the metric set; moved to §10.3 Rejected.
- "Risk-free" → "risk-reduced" throughout, with slippage caveat.
- Flagged as unvalidated: Greeks net-sign table, Trade Quality Score IV direction,
  liquidity thresholds (500/2000), IV Index value.
- Softened canonical-authority statement to exempt HYPOTHESIS blocks; added rule
  barring fact-words (confirmed/proven/favorable/optimal/maximizes) without derivation
  or stated sample size.
- Added APPROVED trade-logging mechanism to §10.1 (the `trades` table schema) as the
  means to answer the favorability question from real fills and calibrate the live
  threshold from modeled-vs-actual credit.

**`iv_engine.py`** — Complete replacement:
- `iv_regime(ratio)` rewritten to neutral, non-valenced output. Labels now
  BACK-ELEVATED / BACK-LEANING / FLAT / FRONT-LEANING / FRONT-ELEVATED with a
  blue↔purple non-valenced palette (no green=good / red=bad).
- `interpret_curve()` rewritten to describe shape only and state that favorability is
  unvalidated; removed all "FAVORABLE / structural edge / maximizes" language.
- `TransformCredit` dataclass: removed `daily_theta_est` and `trading_hrs_to_threshold`.
- `transform_credit()`: removed the `front_dte` parameter and the Theta ETA computation.
- `CalendarEdge` and `calendar_edge()` docstrings de-claimed (no favorability, correct
  backwardation/contango wording).

**`app.py`** — Terminology + Theta ETA cleanup:
- Term-structure labelling already neutralized via local helpers `_neutral_regime()`,
  `_edge_color()`, `_edge_label()`, `_describe_curve()` (FRONT/BACK-ELEVATED, neutral
  accents). Stale comment claiming the engine still held old labels was corrected — the
  engine is now neutral too; local helpers retained only for finer ↑↑/↓↓ banding.
- Removed the Theta ETA display block from the Transform Credit panel (replaced with a
  removal NOTE).
- Removed the now-invalid `front_dte=front_dte` argument from the `transform_credit()`
  call. (`front_dte` variable itself retained — still used for DTE display.)
- Transform Credit ✅/⏳/⛔ coloring intentionally KEPT: it tracks realized dollar profit
  vs a dollar threshold, which is legitimately valenced, unlike the IV regime.

**Why:**
A self-review flagged that v1.0 had elevated a single paper trade into project-wide
"ground truth." The audit confirmed the central regime-favorability claim was unproven
and likely backwards, found a terminology inversion, an incorrect transformation
description, two implementation conflicts (expiry scope, strike width), and an
assumption-based Theta ETA inconsistent with the project's data-over-guesswork principle.

**Impact:**
- The dashboard no longer implies any IV regime is good or bad. Regime is shown as
  neutral context only — removing the risk of trading a sign that isn't established.
- Transform Credit (the genuinely decision-relevant dollar metric) is unaffected and
  remains the primary monitoring number.
- Codebase and documentation are now consistent (no drift between labels, engine, and
  doc). Both Python files parse; transform_credit and calendar_edge run clean against a
  synthetic chain.
- The favorability question is now set up to be answered empirically via the planned
  `trades` table rather than asserted.

**Open questions / follow-ups:**
- `trades` table + logging step is approved in the doc (§10.1) but NOT yet implemented
  in db.py / collector.py — next-session build task.
- Phase 3 time-to-viability metric (proper, from per-leg Greeks) still to be built to
  replace the removed Theta ETA.
- Live Transform Threshold (~$6.50–$7.00) still needs calibration from the first
  5–10 live transformations (modeled vs actual credit).
- app.py keeps local regime helpers parallel to the engine's `iv_regime()`; both are
  now neutral and agree in meaning, but a future pass could consolidate to a single
  source if the finer banding is moved into the engine.

---

## 2026-06-25 — Dashboard v1: Three New Analytics Panels + Visual Overhaul

**Changed:**

**`app.py`** — Complete replacement. Major structural changes:
- Added **IV Structure Panel** (Panel 1 of 3): per-strike IV ratio for call and put legs
  independently, regime badge (INVERTED / NEAR-PARITY / CONTANGO) color-coded green/
  teal/amber/orange/red, 30-minute ratio sparkline in periwinkle (#7b8cde) with ratio=1
  reference line. Sparklines filter to last 30 minutes of today's contract history.
- Added **Calendar Edge Panel** (Panel 2 of 3): call-side edge (front call IV − back call IV)
  and put-side edge (front put IV − back put IV) shown as independent live numbers with
  color coding and today's edge trend sparkline per side.
- Added **Transform Credit Panel** (Panel 3 of 3): theoretical transformation credit
  (back_legs_value − close_cost − entry_debit), threshold viability status, full leg
  breakdown table, and rough theta ETA in trading hours. Entry Debit and Transform
  Threshold are sidebar inputs persisted in `st.session_state`.
- Selectors moved to a full-width 4-column row above the panels (front expiry, back
  expiry, call strike, put strike all visible simultaneously).
- Header streamlined to 4 columns (SPX price, VIX, data staleness, snapshot timestamp).
- ATM term structure metrics + regime badge moved to a 5-column strip below the panels.
- ATM IV interpretation updated to reflect correct regime direction: inverted (ratio < 1,
  back IV > front IV) is now labeled as favorable; contango is labeled unfavorable.
- Custom CSS block: GitHub-dark palette (#0d1117 / #161b22), panel tiles with
  `border-radius:4px`, monospace financial numbers, regime badges with colored background.
- `.streamlit/config.toml` added: dark base theme with `primaryColor = "#00d97e"`.

**`iv_engine.py`** — Complete replacement. New functions:
- `iv_regime(ratio)` → `(label, hex_color)`: 5-level regime classification matching
  updated interpret_curve() logic. Thresholds: <0.90 INVERTED●, <1.00 INVERTED,
  ≤1.05 NEAR-PARITY, ≤1.15 CONTANGO, >1.15 STEEP CONTANGO.
- `CalendarEdge` dataclass + `calendar_edge()`: computes call_edge, put_edge,
  call_ratio, put_ratio, and the 4 StrikeContract objects in one call.
- `TransformCredit` dataclass + `transform_credit()`: full transformation viability
  calculation. Formula: theoretical_credit = back_legs_value − close_cost − entry_debit.
  Includes rough daily_theta_est and trading_hrs_to_threshold for the ETA display.
  back legs use `mark` (pre-computed mid); front legs use `ask` (cost to close shorts).
- `StrikeContract` dataclass: added `mark` field (falls back to (bid+ask)/2 if pre-computed
  mark column absent or null).
- `interpret_curve()`: updated regime direction to match forensic findings from 2026-06-23:
  inverted (back IV > front IV) = favorable; contango (front IV > back IV) = unfavorable.

**`.streamlit/config.toml`** — New file. Sets dark theme globally so CSS panel styling
renders correctly without depending on user's local Streamlit theme preference.

**Why:**
The three panels were confirmed as the next build targets in the previous session.
The forensic analysis of the 2026-06-23 paper trade established the correct regime
interpretation (inverted = favorable, the confirmed call/put IV ratios were 0.85 / 0.82)
and the correct transformation metric (theoretical credit, not diagonal mark). Dashboard
v1 surfaces both of those learnings as live, per-strike, per-side numbers.

**Impact:**
- Transform Credit panel is the critical new addition: it now shows, in real-time, whether
  the transformation threshold has been crossed — the exact information gap that meant the
  June 23 transformation had to be executed manually without confirmation.
- Calendar Edge panel allows independent monitoring of call-side and put-side edge so
  asymmetric opportunities (one side strengthening while the other weakens) are visible.
- IV Structure panel's 30-minute sparklines show regime drift, not just current snapshot.
- Regime color coding is now consistent across all three panels, ATM metric strip, and
  interpret_curve() text — same thresholds everywhere.
- `StrikeContract.mark` is now populated (with (bid+ask)/2 fallback), enabling the
  Transform Credit calculation without requiring a separate DB query.

**Open questions / follow-ups:**
- Theta ETA in Transform Credit panel is a rough estimate (close_cost / front_dte),
  treating front leg theta as linear and ignoring back-leg drag and vega effects.
  It is directionally useful but should not be used for precision timing. Phase 3
  will replace this with actual stored theta from option_rows once it's confirmed
  that collector.py is populating the theta column reliably.
- The `pytz` import in app.py requires `pytz` to be installed
  (`pip install pytz` if not already present from schwab-py dependencies).
- IV Structure sparklines will show "30m history building..." until at least 2 matching
  timestamps exist in contract IV history for both front and back legs at the selected
  strikes. This is expected behavior — they populate within the first poll cycle.


---

## 2026-06-24 — app.py + db.py: Refactor dashboard to pure DB reader

**Changed:**
app.py: Removed all Schwab API calls, demo mode, and DB writes. Now reads
exclusively from the snapshot-anchored schema (snapshots, option_rows,
atm_iv_by_expiry). Added data staleness indicator to header. Added two
helper functions (_load_atm_hist, _load_contract_hist) that handle the
IV decimal→percentage conversion at the load boundary (×100).

db.py: Removed _LEGACY_DDL and 8 legacy functions (save_expiry_snapshot,
save_strike_snapshot, save_position, get_expiry_history,
get_latest_two_snapshots, has_any_data, get_strike_history,
get_open_positions). Added get_latest_complete_snapshot() and
get_latest_atm_iv_snapshots(). Removed _LEGACY_DDL so init_db() no longer
silently recreates the dropped legacy tables.

**Why:**
app.py and collector.py were two independent systems writing to different
tables in the same DB. Dashboard showed data only from the moment the
browser tab was opened. 559,942 rows collected by collector.py were
invisible to the dashboard.

**Impact:**
Dashboard now shows all history since 6/23 (183 snapshots, 559,942 rows).
No more duplicate data paths. app.py has zero Schwab API dependency —
it works as a pure SQLite reader with no credentials needed. The collector
is now the sole writer; the dashboard is a pure reader.

**IV scale note:**
option_rows and atm_iv_by_expiry store IVs as decimals (0.18 = 18%).
app.py multiplies by 100 at every load boundary. All iv_engine calls and
chart code continue to operate in percentage form unchanged.

---

## Session: June 23, 2026

### Type: Paper Trade Forensics + Dashboard Architecture Review

---

### 1. Paper Trade Executed

First live collection day. First paper trade executed end-to-end to validate the full
pipeline: collector → database → forensic analysis → dashboard insight.

**Entry (1:46 PM ET)**

| Leg | Expiry | Strike | Side | Fill |
|-----|--------|--------|------|------|
| Call | 6/26/2026 | 7500 | STO (short) | 11.70 |
| Put  | 6/26/2026 | 7300 | STO (short) | 19.30 |
| Call | 6/29/2026 | 7500 | BTO (long)  | 18.00 |
| Put  | 6/29/2026 | 7300 | BTO (long)  | 26.50 |

Net Debit: **$13.50**

**Transformation (3:50 PM ET)**

| Action | Leg | Fill |
|--------|-----|------|
| STC | 6/29 7500C | 14.00 |
| STC | 6/29 7300P | 39.00 |
| BTO | 6/26 7505C | 7.60  |
| BTO | 6/26 7295P | 25.60 |

Net Credit: **$19.85**

**Result**

- Locked P&L: +$6.35 per share (+$635 per contract)
- Resulting IC: Short 7500C / Long 7505C + Short 7300P / Long 7295P (all 6/26)
- Max Loss: ~$135 | Max Profit: ~$635

---

### 2. Forensic Notebook Built

File: `trade_forensics_2026_06_23.ipynb`

Built a 12-cell Jupyter notebook to analyze the paper trade against collected data.
Encountered and resolved two schema issues:

**Schema corrections from assumed vs actual:**

| Assumed | Actual |
|---------|--------|
| `snapshots.id` | `snapshots.snapshot_id` |
| `snapshots.collected_at` | `snapshots.snapshot_timestamp` |
| `option_rows.option_type` | `option_rows.right` |
| `option_rows.underlying_price` | `snapshots.underlying_price` |
| Computed `(bid+ask)/2` | Use stored `option_rows.mark` |
| Computed extrinsic manually | Use stored `option_rows.time_value` |
| `atm_iv_by_expiry.atm_iv` | `atm_call_iv`, `atm_put_iv`, `atm_avg_iv` |

Timestamps stored in UTC. 3:30 PM ET = 19:30 UTC. Confirmed by MIDDAY snapshots
appearing at 16:xx UTC (12:xx ET).

---

### 3. Collector Validation Results

**Snapshot coverage (CLOSE window, 3:30–3:55 PM ET):**

| Metric | Value |
|--------|-------|
| Total snapshots | 20 |
| Status | All COMPLETE |
| Market session | All CLOSE |
| Gaps > 90 seconds | 3 |

**Gaps detected:**

| UTC Time | ET Equivalent | Duration | Impact |
|----------|--------------|----------|--------|
| 19:37:38 | 3:37 PM | 2.9 min | 2 missed snapshots, mid-session |
| 19:48:38 | 3:48 PM | 2.0 min | 1 missed snapshot |
| 19:53:00 | **3:50 PM** | **3.4 min** | **Straddles transformation moment** |

The third gap is the most significant. The transformation at 3:50 PM occurred in a
3.4-minute window the collector did not capture. Last pre-transform snapshot was 19:49:38
(3:49:38 PM ET). First post-transform snapshot was 19:53:00 (3:53:00 PM ET).

**Collector verdict: Working correctly.** Gaps were from system behavior, not collector
failure. The 1-minute polling interval means any spike shorter than ~90 seconds has a
~50% chance of being missed. This is a known limitation, not a bug.

---

### 4. Key Forensic Findings

#### Finding 1: IV term structure was inverted and stable throughout

At the trade strikes:

| Metric | Call (7500) | Put (7300) |
|--------|------------|-----------|
| IV ratio (back/front) | 0.849–0.853 | 0.821–0.827 |
| IV spread (back−front) | −0.025 | −0.038 |
| Regime | Inverted all window | Inverted all window |

IV was **completely flat** across all 12 contracts (±1 strike, both expiries) from
3:30 to 3:55 PM. iv_change = 0.00 for every contract. The inversion was structural,
not caused by any IV event during the collection window.

#### Finding 2: The diagonal mark equals the total calendar edge

Because all four options were OTM throughout (SPX ~7370–7379, strikes at 7300/7500),
intrinsic value = 0 for all legs. Therefore mark = time_value for every leg, and:

```
total_calendar_edge == diagonal_mark  at every snapshot
```

This is a mathematical confirmation the collector is storing data correctly.
It is also the ideal diagonal structure — a purely time-value trade.

#### Finding 3: The diagonal mark is the wrong transformation metric

The diagonal mark measures the cost to close all four legs simultaneously. That is not
what the transformation does. The front legs are never closed — they remain open as the
short side of the resulting iron condor.

The correct metric is **Theoretical Transform Credit**:

```
Transform Credit = (back_call mark + back_put mark) - (wing_call mark + wing_put mark)
Net Locked Profit = Transform Credit - Entry Debit
Risk-Free when: Net Locked Profit > Spread Width
```

#### Finding 4: The position was NOT risk-free at any captured snapshot

Transform credit data (from theoretical transform credit query):

| Time | Net Locked Profit | Risk-Free? | Dollars to Threshold |
|------|------------------|------------|---------------------|
| 19:30–19:46 | $2.30–$2.45 | False | $2.55–$2.70 |
| 19:48:38 | $2.55 | False | $2.45 |
| 19:49:38 | $2.65 | False | $2.35 |
| **Actual fill** | **$6.35** | **True** | **−$1.35 (floor profit)** |

The jump from $2.65 to $6.35 came from the 3:50 PM fill at 39.0 on the back put —
which occurred in the collection gap.

#### Finding 5: Paper trading fills are not real fills

Paper trading fills at the mark (mid). Live trading fills at or near the bid when selling.
Across 4 legs, the paper trading advantage was approximately $2.40–$3.90 vs real market fills.

This means the actual live trading threshold should be:

```
Paper trading threshold:  Net Locked > $5.00 (spread width)
Live trading threshold:   Net Locked > $6.50–$7.00 (spread width + bid/ask friction)
```

The Theoretical Transform Credit panel should display a live-adjusted figure alongside
the mark-based figure once real trading begins.

#### Finding 6: The profit source was delta, not IV

SPX moved from ~7376 at 3:30 PM to a low of 7367.29 at 3:48 PM (−8.7 points).
The back put rose from 34.65 to 37.65 on delta alone. IV was flat throughout.
The spike to 39.0 at 3:50 PM was a brief additional delta push during end-of-day
volatility, amplified by widening market maker spreads in the final 10 minutes.

SPX fully recovered to 7376 by 3:53 PM. The back put returned to 34.55 — almost
exactly where it started. The entire transformation credit spike lasted ~90 seconds.

#### Finding 7: ATM IV ratio from atm_iv_by_expiry is unreliable near end of day

The `iv_ratio_to_front` column jumped from 0.97 to 1.28 at 19:53 (3:53 PM ET) —
not because IV changed, but because the 0DTE reference expiry expired at 4:00 PM
and the reference "front" shifted. Use strike-specific IV ratio computed directly
from option_rows, not from the pre-aggregated table, for all live dashboard signals.

#### Finding 8: Three transformation pathways confirmed

| Market Condition | Mechanism | Speed |
|----------------|-----------|-------|
| SPX drifts toward put strike | Back put delta spike | Fast (hours) |
| SPX drifts toward call strike | Back call delta spike | Fast (hours) |
| Choppy / range-bound | Theta decay differential | Slow (days) |
| Strong trend through short strike | Dangerous — transform BEFORE breach | Urgent |

The strategy is market-neutral by design. Transformation works in any direction.
Theta pathway is slowest but most reliable. Directional spike is fastest but requires
live platform monitoring — the collector cannot catch 90-second windows reliably.

---

### 5. Transformation Score — Architecture Correct, Input Was Wrong

The composite score (0–100) designed in this session has the right structure but
used the wrong profit pillar input. With diagonal mark as input, score was stuck at
50–52 all window (misleading). With corrected input, score would have been:

- During captured window: 67–69 ("be alert, not there yet") ✓ correct
- At actual transformation fill: ~87.5 ("act now") ✓ correct

**However:** After review, the transformation score was removed from Must Have and
demoted. Reason: if the underlying components (net locked profit, IV ratio, DTE) are
displayed directly and clearly, a composite score hides information without adding
decision value. A score of 73 means nothing six months from now. The raw numbers
mean everything.

---

### 6. Dashboard Architecture — Final Decisions

#### Must Have (build first, before any live trades)

**1. Strike Selector** *(moved to #1 — most impactful feature)*
Table of IV ratios at every OTM call and put strike within 300 points of SPX,
ranked by edge quality. Tells you immediately which strikes have the best structural
edge for a new entry. Already in the database. Just needs to be surfaced.

**2. IV Ratio at Trade Strikes**
Back month IV / Front month IV at your exact call and put strikes.
This is the structural edge. Below 1.0 = inverted = trade has edge.
At or above 1.0 = do not enter. Single most important number.

**3. SPX vs Strike Distance**
Current SPX with proposed strikes and distance in points and percentage.
Entry filter: want ~1–2% clearance on each side minimum.
Front DTE also displayed here. Want 5–10 DTE at entry.

**4. Theoretical Transform Credit**
Transform Credit, Entry Debit, Net Locked Profit, Spread Width, Dollars to Risk-Free.
Risk-free status (True/False) displayed prominently.
Eventually: live trading adjusted figure alongside mark-based figure.

**5. Net Theta Advantage** *(new — replaces VIX as primary operational metric)*
```
Net Theta Advantage = (front_call_theta + front_put_theta) 
                    - (back_call_theta + back_put_theta)
```
The engine of the trade. Tells you how fast time is working in your favor.
$0.20/day means the trade may take forever. $1.50/day means theta is strongly on
your side. Displayed as a single dollar number per day.

**6. Days to Risk-Free (theta-only estimate)**
```
Days to Risk-Free = (Spread Width - Net Locked Profit) / Net Theta Advantage
```
Directly answers: "how long would I have to wait if nothing else changes?"
All inputs already in option_rows. Computable today.

#### Nice to Have (build after 10 live trades)

- IV ratio 30-minute sparkline (trend confirmation, not entry decision)
- Calendar edge split (call edge vs put edge separately — forensic insight)
- Session time indicator (countdown to 4 PM ET, color shift after 3:45 PM)
- VIX single number with threshold color (green >15, yellow 12–15, red <12)
- Position legs table (entry vs now, per-leg mark and P&L)
- Live trading haircut display alongside mark-based transform credit

#### Do Not Build

- Diagonal mark chart (wrong metric for transformation)
- ATM IV term structure from pre-aggregated table (unreliable near close)
- IV spread (back − front) — perfectly redundant with IV ratio
- P&L attribution by leg (forensic only, not actionable live)
- Neighboring strike IV slope (never changes a live decision)
- Payoff diagram (known structure, stops being looked at after 10 trades)
- Historical IV percentile (requires 30+ days data; IV ratio already tells you entry quality)
- Composite transformation score (hides the components that matter; display raw numbers instead)
- Skew viewer (redundant with neighboring strike data; same verdict)

---

### 7. Final Dashboard Layout (5 sections)

**Section 1 — STRIKE SELECTOR** *(pre-entry, look before opening any position)*
Table of all OTM strikes ±300 pts, both sides, both expiries, IV ratio per strike.
Sorted by best edge. Pick your strikes here. Front DTE shown per expiry.

**Section 2 — ENTRY SIGNAL** *(pre-entry gate check)*
IV ratio at selected strikes (call and put, color-coded by regime).
SPX now. Strike distances in points and percent. Front DTE. GO / WAIT / NO verdict.

**Section 3 — TRANSFORMATION STATUS** *(primary panel while in a position)*
Transform Credit, Entry Debit, Net Locked Profit, Dollars to Risk-Free, Risk-Free status.
Net Theta Advantage ($/day). Days to Risk-Free (theta-only).

**Section 4 — IV RATIO TREND** *(glance every 15–20 min while in a position)*
30-minute sparkline for call-side and put-side IV ratio.
Horizontal reference line at 1.0. No numbers needed. Trend direction is the signal.

**Section 5 — POSITION LEGS** *(confirm structure once or twice per session)*
Four-row table: entry fill, current mark, per-leg P&L.
Total calendar edge. SPX now vs short strikes.

---

### 8. Mental Model Confirmed

```
Two phases:

ENTRY PHASE
  Single question: Is IV ratio below 1.0 at my strikes?
  Secondary check: Is SPX far enough from my strikes?
  Tool: Section 1 (Strike Selector) + Section 2 (Entry Signal)

POSITION MANAGEMENT PHASE
  Single question: Has net locked profit crossed the spread width?
  Secondary check: How many days of theta until it does?
  Tool: Section 3 (Transformation Status)
  Execution: Live Schwab platform — collector cannot catch 90-second spikes
```

The collector's job is structural confirmation. The execution is always a live call.

---

### 9. Paper Trade Assessment

The paper trade served its purpose completely:

- Validated the collector architecture (schema, UTC alignment, gap logging, CLOSE window)
- Identified the correct transformation metric (theoretical transform credit, not diagonal mark)
- Confirmed the IV inversion edge was real and present throughout
- Revealed that paper trading fills are optimistic by $2–4 vs real fills
- Generated the complete dashboard feature list through real analysis, not speculation
- Confirmed the 3-pathway transformation model (put spike, call spike, theta decay)

Going forward: live trades only. Dashboard and transformation logic will be calibrated
from real market fills, not paper trading assumptions.

---

### 10. Open Items for Next Session

1. Renovate dashboard (app.py) to implement the 5-section layout above
2. Fix transformation score profit pillar input (net_locked_profit / spread_width)
   OR remove score entirely in favor of displaying raw components directly
3. Add Net Theta Advantage as a live-computed metric
4. Add Days to Risk-Free estimate to Section 3
5. Build Strike Selector as Section 1 (query all OTM strikes, compute IV ratio per strike)
6. Add live trading haircut toggle to transform credit display
7. Investigate collector gap at 19:37 and 19:48 — confirm whether these are consistent
   or one-time events on first collection day
8. Query the full MIDDAY window (1:46–3:30 PM) to reconstruct complete trade history
   for the paper trade — the first 104 minutes of profit build-up are in the database

---

*Session closed. Next session: Dashboard renovation (app.py).*

## 6/23/2026 — app.py: Fixed broken reference to removed config constant

**What changed**
`app.py` referenced `config.MAX_EXPIRY_DTE` which was removed when the expiry
collection strategy changed from DTE-capped to count-based. Replaced all
occurrences with `config.MAX_EXPIRY_FETCH_DAYS` via VS Code Find and Replace
(Ctrl+H → Replace All).

**Why**
Dashboard failed on launch with: "Couldn't reach Schwab API: module 'config'
has no attribute 'MAX_EXPIRY_DTE'"

**Impact**
Dashboard loads normally again. No logic change — `MAX_EXPIRY_FETCH_DAYS = 90`
is the direct drop-in replacement for the old `MAX_EXPIRY_DTE = 20` in any
context where a date range in days is needed.

---

## 6/23/2026 — config.py + collector.py: Collect next 20 expirations by count

**What changed**
`config.py`: replaced `MAX_EXPIRY_DTE = 20` with two new constants:
  - `MAX_EXPIRY_COUNT = 20` — collect exactly this many expirations per snapshot
  - `MAX_EXPIRY_FETCH_DAYS = 90` — how far out to cast the net before trimming

`collector.py` (`_run_cycle`): two changes:
  1. `max_date` now uses `MAX_EXPIRY_FETCH_DAYS` instead of `MAX_EXPIRY_DTE`
  2. After `chain_to_dataframe`, chain is trimmed to the nearest
     `MAX_EXPIRY_COUNT` expirations before any further processing:

```python
all_expiries = sorted(chain_df["expiry"].unique())
keep_expiries = set(all_expiries[:config.MAX_EXPIRY_COUNT])
chain_df = chain_df[chain_df["expiry"].isin(keep_expiries)]
```

**Why**
Original design capped collection at expirations within 20 calendar days.
First live run showed only 14 expirations in that window. Collecting by count
instead guarantees exactly 20 expirations per snapshot regardless of how SPX
weekly calendars are spaced — typically reaching ~35–50 DTE for the 20th
expiry.

**Impact**
- `exp=14` → `exp=20` per snapshot
- `rows=2240` → `rows=3200` per snapshot (~43% more option rows)
- Storage: ~330,000 option rows per trading day vs ~230,000 previously
- Term structure view now spans ~6–7 weeks out rather than 3 weeks
- `iv_spread_to_front` column covers a fuller curve

---

## 6/23/2026 — check_db.py: New database health check script

**What changed**
New file `check_db.py` added to project root.

**Why**
Multiline `python -c "..."` commands don't work in PowerShell. Rather than
finding PowerShell-compatible syntax, a dedicated script is cleaner, reusable,
and readable. Replaces all ad-hoc database query snippets with one command:
`python check_db.py`

**What it shows**
- Total snapshots today and all-time (by status: COMPLETE / PARTIAL / FAILED)
- Total option rows stored
- Last 5 snapshots with SPX, VIX, row count, latency, status
- Full IV term structure from the most recent COMPLETE snapshot
- Recent collection gaps (last 5)

**Usage**
Run from any second terminal while the collector is running in Terminal 1.
Never writes to the database — read-only.

---

## 6/23/2026 — config.py: Fixed VIX symbol

**What changed**
`config.py`: `VIX_SYMBOL = "$VIX.X"` → `VIX_SYMBOL = "$VIX"`

**Why**
First live collection run showed:
`HTTP/1.1 400 Bad Request` for `$VIX.X`
Schwab's correct symbol for the VIX index is `$VIX` not `$VIX.X`.
VIX was logging as N/A in every snapshot.

**Impact**
VIX value now populates `snapshots.vix_value` correctly. Required collector
restart to take effect.

---

## 6/23/2026 — Collector: First live run confirmed working

**What happened**
First successful live collection cycle:

✓ snap=1 | MIDDAY | SPX=7397.57 | VIX=N/A | rows=2240 | exp=14 | 6467ms | COMPLETE

**Observations**
- 14 expirations within 20 DTE (prompted the count-based change above)
- 2240 rows = 14 × 80 strikes × 2 sides — math confirms design is correct
- 2SD warning fired: IV=21.3%, DTE=20 → expected move ±736 pts exceeds ±300pt
  window. Decision: leave `STRIKE_FETCH_WIDTH_POINTS = 300`. For diagonal
  calendar trading, strikes beyond ±150 pts are never traded. Warning is
  informational only and does not affect data quality.
- VIX returned 400 → fixed separately (see entry above)
- Collection latency 6467ms — normal for first authenticated cycle

**Startup confirmed**
Collector started via `python collector.py` in VS Code Terminal 1.
Startup shortcut added to Windows Startup folder (shell:startup) pointing
directly to `python.exe` to bypass Smart App Control block on .bat files.
Sleep mode set to Never (plugged in) so collection is uninterrupted during
market hours while laptop is locked.

---

## 6/22/2026 — Windows auto-start: Startup folder shortcut

**What changed**
Added shortcut to Windows Startup folder (`shell:startup`) pointing to:
`C:\Users\chand\Python\.venv\Scripts\python.exe`
with argument: `"C:\Users\chand\Python\spx-diagonal-dashboard\collector.py"`
and Start In: `C:\Users\chand\Python\spx-diagonal-dashboard`

`start_collector.bat` and `register_collector_task.ps1` were created but
could not be used:
- `.bat` file blocked by Windows Smart App Control
- PowerShell script failed silently (no Admin rights in VS Code terminal)

**Why shortcut instead of Task Scheduler**
Direct Python shortcut bypasses Smart App Control entirely (python.exe is a
trusted executable). No Admin rights needed. Equivalent reliability for a
personal machine.

**Behavior**
Collector starts automatically at every Windows logon. Sleeps outside market
hours (9:30 AM – 4:00 PM ET). No manual intervention required on trading days
except weekly Schwab OAuth re-authorization (~every 7 days).

---

## 6/22/2026 — collector.py: Initial implementation + supporting changes

### What changed
Three files modified or created:
- `config.py` — two additions
- `schwab_client.py` — three new functions + vega in chain_to_dataframe
- `collector.py` — new file (735 lines)

---

### config.py additions

**`VIX_SYMBOL = "$VIX.X"`**
Schwab's symbol for the CBOE VIX index. Used by collector to fetch VIX spot
alongside each option chain snapshot. Stored as `vix_value` in snapshots table
to provide volatility regime context for historical IV percentile analysis.

**`MARKET_HOLIDAYS`**
Set of US market holiday date strings for 2026. The collector uses this to
classify collection gaps as `HOLIDAY` vs `COLLECTOR_OFFLINE`, so weekend and
holiday gaps are suppressed from data quality warnings in the dashboard.
**Action required each January: add the next year's holidays.**

---

### schwab_client.py additions

**`_safe_float(val)`**
Internal utility. Returns `float(val)` or `None` if val is null, NaN, zero,
or unconvertible. Keeps API response sanitization in one place.

**`get_spx_quote_full(client) → dict`**
Returns `{bid, ask, last, mark}` for the SPX index. Used by collector to
populate `snapshots.underlying_bid`, `underlying_ask`, `underlying_price`.
The original `get_spx_quote()` (returns a float) is preserved unchanged for
backward compatibility with any existing `app.py` callers.

**`get_vix_quote(client) → float | None`**
Returns the VIX spot price, or None if the fetch fails. Deliberately non-fatal
— a VIX failure does not abort the collection cycle; the snapshot is recorded
with `vix_value = NULL`.

**`chain_to_dataframe` — added `vega` column**
`c.get("vega")` is now extracted alongside delta/gamma/theta. Vega is required
for transformation timing analysis: when vega is highest, IV contraction has
maximum impact on position value. This is a backward-compatible column addition.

---

### collector.py — new file

**Purpose**: The only component that talks to the Schwab API and writes to the
new snapshot-anchored SQLite schema. Runs independently of `app.py` as a
separate terminal process.

**Market session logic**
- OPEN (09:30–10:00 ET): 60-second polling
- MIDDAY (10:00–15:30 ET): 300-second polling
- CLOSE (15:30–16:00 ET): 60-second polling
- Collection stops at 16:00 ET — not 16:15 — because SPX (cash-settled index)
  stops updating at equity close; IVs after 16:00 use a frozen underlying.
- Sleeps outside market hours; self-activates at next open without restart.
- US market holidays read from `config.MARKET_HOLIDAYS`.

**Cycle lifecycle (10 steps)**
1. Fetch SPX quote (bid/ask/last/mark)
2. Fetch VIX (non-fatal)
3. Create snapshot as `PARTIAL` — ensures auditable record even on crash
4. Fetch option chain (all expirations ≤ MAX_EXPIRY_DTE = 20)
5. Flatten chain; apply ±300pt strike filter
6. Build `option_rows` list (one dict per contract)
7. Compute `atm_iv_by_expiry` records (one dict per expiry)
8. Determine status: COMPLETE / PARTIAL / FAILED
9. Write rows to database (separate transactions for each table)
10. Finalize snapshot to COMPLETE/PARTIAL/FAILED with metadata

**Snapshot status logic**
- `COMPLETE`: all rows written, ATM IV computed for all raw expiries
- `PARTIAL`: ATM IV computed for fewer expiries than raw chain contained
- `FAILED`: no option rows after filtering, or fatal API error

**Gap detection**
- On startup: compares `now()` to last snapshot timestamp; records unexpected
  gaps in `collection_gaps` table (HOLIDAY and MARKET_CLOSED gaps suppressed)
- Mid-session: flags gaps between consecutive snapshots > 2.5× expected interval

**Error handling**
- Auth errors (HTTP 401 / token expired): resets `client = None` to force
  re-authentication on the next cycle; sleeps 30s
- API / processing errors: logs failure count; sleeps 30s and retries
- After 5 consecutive failures: logs CRITICAL alert (collection continues)
- KeyboardInterrupt: clean shutdown

**Drift-corrected sleep**
Sleep duration = `poll_interval - cycle_elapsed_time`. Keeps collection times
close to wall-clock boundaries regardless of API latency variation.

**CLI flags**
- `python collector.py` — runs indefinitely
- `python collector.py --once` — one cycle then exit (for testing)
- `python collector.py --db PATH` — override database path

**Windows note**
Requires `pip install tzdata` for IANA timezone support (`zoneinfo` module).

---

### Impact
- `app.py`: no changes required — all legacy db.py functions unchanged
- `db.py`: no changes — uses existing `create_snapshot`, `insert_option_rows`,
  `insert_atm_iv_records`, `finalize_snapshot`, `record_gap` functions
- New snapshots accumulate in the `snapshots`, `option_rows`, and
  `atm_iv_by_expiry` tables; app.py reads from legacy tables until refactored

### Open questions
- When to refactor `app.py` to read from new schema (after collector has
  accumulated enough real data to make the new charts useful)
- Whether to add Windows Task Scheduler integration to auto-start collector
  at system boot (currently requires manual `python collector.py` each session)
- `MARKET_HOLIDAYS` needs to be updated each January


---

## 2026-06-22 — db.py: Full schema overhaul (snapshot-anchored design)

### What changed
`db.py` completely rewritten. The file grew from 164 lines to ~390 lines.
All existing functions are preserved with identical signatures — `app.py`
requires zero changes and continues to work as before.

### New tables added (new schema)

| Table | Purpose |
|---|---|
| `schema_version` | Version tracking; enables future migrations |
| `snapshots` | One row per collection cycle; anchor for all child data |
| `option_rows` | One row per contract per snapshot; irreplaceable intraday record |
| `atm_iv_by_expiry` | Pre-aggregated ATM IV per expiry; primary analytics query target |
| `collection_gaps` | Audit log of missed collection windows |

### Legacy tables kept (for existing app.py)

| Table | Status |
|---|---|
| `expiry_snapshots` | Kept — app.py still reads from this |
| `strike_snapshots` | Kept — app.py still reads from this |
| `positions` | Kept — unchanged |

Legacy tables will be removed in a future commit when `app.py` is
refactored to read from the new schema.

### New functions added

**Write (collector.py only):**
- `create_snapshot()` — opens a PARTIAL snapshot; returns snapshot_id
- `finalize_snapshot()` — seals to COMPLETE/PARTIAL/FAILED after child rows commit
- `insert_option_rows()` — bulk insert in single transaction
- `insert_atm_iv_records()` — bulk insert pre-aggregated ATM IV
- `record_gap()` — writes gap record on restart or missed cycle

**Read (future refactored app.py):**
- `get_last_snapshot_timestamp()` — for gap detection on startup
- `get_snapshots()` — time-range query, default status=COMPLETE
- `get_option_chain()` — chain reconstruction at any historical snapshot
- `get_contract_iv_history()` — IV time-series for a specific strike
- `get_atm_iv_history()` — ATM IV time-series for a specific expiry
- `get_term_structure()` — all expiries for a snapshot (for curve chart)
- `get_iv_spread_history()` — front/back spread for IV percentile engine
- `get_gaps()` — gap query with optional reason exclusion
- `update_snapshot_notes()` — narrow write exception permitted from app.py

### Connection management improvements
- `get_conn()` (legacy) now rolls back on exception — this was missing before
- `managed_conn()` (new) requires explicit db_path; no silent default writes
- Both context managers set WAL mode and `PRAGMA foreign_keys = ON` per connection
- `_make_conn()` shared internal factory with 15-second timeout

### Indexes added (9 total)
```
idx_snapshots_timestamp
idx_snapshots_status
idx_snapshots_timestamp_status      ← primary dashboard query index
idx_option_rows_snapshot_id
idx_option_rows_contract
idx_option_rows_contract_snap       ← most critical: covers per-contract IV history
idx_atm_iv_snapshot_id
idx_atm_iv_expiry_snap              ← primary percentile engine index
idx_gaps_start
```

### Why this design
Schwab provides no historical intraday option chain endpoint. Every row in
`option_rows` represents a moment that cannot be reconstructed from any external
source once missed. The snapshot-anchored design ensures:
- Every collection cycle is auditable (COMPLETE / PARTIAL / FAILED)
- Partial fetches don't silently pollute IV percentile statistics
- Chain reconstruction at any historical timestamp is a first-class query
- The `atm_iv_by_expiry` pre-aggregation keeps dashboard queries fast at scale
  (scans ~3,150 rows vs ~4.8M rows for a 30-day ATM IV history query)

### Impact on existing system
- `app.py` requires no changes
- `demo_data.py` requires no changes
- `iv_engine.py` requires no changes
- `schwab_client.py` requires no changes
- `collector.py` (not yet written) will use the new write functions

### Open questions
- When to refactor `app.py` to read from new schema (after collector.py is
  built and accumulating real data)
- `SCHEMA_VERSION = 1`: increment to 2 when the first schema change is needed
  post-deployment; add migration function to `init_db()` at that time


---
**Implementation Date:** 2026-06-22
**Implementation Status:** Complete — all changes verified live

### Files Modified

**`config.py`**
- Replaced `POLL_INTERVAL_SECONDS = 10` with `POLL_INTERVAL_NORMAL = 300` and `POLL_INTERVAL_EVENT = 60`
- Added `STRIKE_COUNT = 80`, `STRIKE_FETCH_WIDTH_POINTS = 300`, `MAX_EXPIRY_DTE = 20`
- Added `DISPLAY_TIMEZONE = "America/New_York"`

**`schwab_client.py`**
- `get_option_chain()` default `strike_count` changed from `20` → `config.STRIKE_COUNT` (80)
- Added `filter_chain_by_strike_window()` — Python-side safety filter enforcing ±300 pt hard boundary; includes optional 2 SD log warning if window becomes inadequate

**`iv_engine.py`**
- Added `ExpectedMoveCheck` dataclass and `expected_move_log_check()` — computes 1 SD and 2 SD expected move, checks adequacy of configured window; informational only, never gates the fetch

**`app.py`**
- `to_date` changed from hardcoded `+45 days` → `config.MAX_EXPIRY_DTE` (20 days)
- `filter_chain_by_strike_window()` called immediately after `chain_to_dataframe()`
- Event Mode toggle added to sidebar; `poll_interval` variable drives `st_autorefresh` and header caption
- Expected move log check wired into snapshot cycle (live mode only)
- All chart X-axis timestamps converted from UTC → `America/New_York`

**`db.py`**
- Verified only — no changes required; schema compatible with wider data

### Verified Working
- Strikes 7550C and 7350P resolved without "showing nearest" fallback
- Expiry selector now shows full 20 DTE window (~10 expirations)
- Event Mode toggle visible in sidebar; header reflects active poll interval
- Chart timestamps display in Eastern time

---

## Session: Architecture Review & Data Collection Redesign
**Date:** 2026-06-22
**Status:** Decisions finalized — implementation pending approval

### What Was Decided

This session was a full architectural review of the data collection layer,
triggered by a UX bug: strikes such as 7550C and 7350P were not found in
the dashboard when SPX was near 7478. The root cause was confirmed as an
API-level fetch limitation, not a storage or UI bug.

---

### Root Cause Confirmed

`schwab_client.py` was calling `get_option_chain()` with `strike_count=20`,
returning only ~40 strikes centered on ATM (±100 pts at 5-pt spacing).
No `range` parameter was passed, so Schwab defaulted to NTM behavior.
The `iv_engine.strike_contract()` fallback to "nearest" was working correctly —
it was simply receiving sparse input from upstream.

Failure point: API fetch layer only. All downstream logic (parsing, storage,
UI lookup) was functioning as designed.

---

### Strike Collection: Decision

**Chosen approach:** `strike_count=80` at API level + Python-side safety filter.

- `strike_count=80` requests 80 strikes above and 80 below ATM from Schwab
- Covers approximately ±300–400 points at 5-pt spacing near spot
- A Python-side filter (`filter_chain_by_strike_window()`) enforces a hard
  ±300-point boundary as a safety net against any overshoot
- `STRIKE_FETCH_WIDTH_POINTS = 300` stored in `config.py` for easy adjustment

**Why not `range='ALL'`:**
`range='ALL'` returns 300–600 unique strikes per expiry across the full SPX
chain (roughly 8,000 contracts per response, ~12 MB payload). At 2-minute
polling that is unnecessary bandwidth — we would download and discard ~70%
of every response. `strike_count=80` achieves the same practical coverage
at ~2.5 MB per call with zero waste.

**Why not dynamic 2 SD filtering:**
Calculating expected move before determining what to fetch creates a
chicken-and-egg dependency (IV is needed to compute SD, but IV comes from
the fetch). A fixed configurable window avoids this entirely. The 2 SD
calculation is preserved as a log-only informational check
(`expected_move_log_check()` in `iv_engine.py`) that flags when the
configured window may be too narrow — but never gates the fetch.

**Why ±300 points:**
Covers all practical diagonal calendar candidates at any expiry within the
20 DTE analysis window. At 5-pt spacing this is ~120 unique strikes.
Far-OTM tail strikes (beyond ±300 pts) are not relevant to short-dated
diagonal structures and are not worth storing.

---

### Expiration Collection: Decision

**Chosen approach:** All expirations from today through `today + 20 calendar days`.

- `MAX_EXPIRY_DTE = 20` stored in `config.py`
- Enforced via `from_date` / `to_date` parameters already supported by
  Schwab's `get_option_chain()` — no additional filtering needed
- Captures approximately 10–11 SPX expirations per fetch window
  (Mon/Wed/Fri weeklies + end-of-month within 20 days)

**Rationale:** Dashboard purpose is not to track one specific diagonal but
to compare IV across many strike/expiry combinations to identify where
front-dated IV is elevated relative to back-dated IV. 20 DTE covers all
realistic diagonal pairings without storing irrelevant longer-dated data.

---

### Polling Strategy: Decision

**Chosen approach:** Manual event mode toggle in the Streamlit sidebar.

- **Normal mode:** 5-minute polling (`POLL_INTERVAL_NORMAL = 300`)
- **Event mode:** 60-second polling (`POLL_INTERVAL_EVENT = 60`)
- Toggle control: `st.toggle("⚡ Event Mode (60s polling)")` in sidebar
- User activates event mode manually before known high-impact events
  (FOMC, CPI, NFP, PPI, Powell speeches, etc.)

**Why not fixed 2-minute:** Storage cost is 2.5x higher than 5-minute for
minimal analytical gain on normal days. IV term structure shifts over
minutes and hours, not seconds — 5-minute captures it faithfully.

**Why not automatic adaptive polling:** Automatic IV-threshold detection
has inherent lag (only switches after the spike has started). A calendar-
based approach requires external dependencies and ongoing maintenance.
The user already knows when major events occur and can activate event mode
10–15 minutes in advance — earlier than any automatic system would trigger.

**Why manual toggle wins:** Zero external dependencies, zero maintenance
burden, anticipatory rather than reactive, and trivial to implement.
The FOMC example (spread briefly spiking from $8 to $20 before reverting)
is exactly the scenario a manual pre-event toggle handles better than any
automatic system.

---

### Timezone Fix

All timestamp display and chart X-axis labels to be converted from UTC to
`America/New_York` (EST/EDT). A `DISPLAY_TIMEZONE` constant added to
`config.py` so timezone is defined in one place across the application.

---

### Files Affected By Implementation

| File | Nature of Change |
|---|---|
| `config.py` | Add 6 new constants |
| `schwab_client.py` | Update fetch params, add safety filter function |
| `iv_engine.py` | Add expected move log check function |
| `app.py` | Date range, filter call, sidebar toggle, timezone fix |
| `db.py` | Verification only — no changes expected |

### Schema Impact
None. Existing `strike_snapshots` table is compatible with wider data.
DB growth will increase proportionally to wider strike/expiry coverage —
accepted and expected per design.

---

## 2026-06-21 — Fix: db.py duplicated causing SCHEMA overwrite
**Changed:** Replaced db.py with a clean single-copy version.
**Why:** The file had been fully duplicated (two complete copies concatenated).
The second copy redefined SCHEMA without strike_snapshots, so Python always
used the old schema. init_db() never created strike_snapshots even on a
fresh database file.
**Impact:** strike_snapshots table now created correctly on startup.
**Open questions:** Check git history to find when the duplication was introduced
— likely a session where file content was appended instead of replaced.

## 2026-06-21 — Fix: strike_snapshots table missing on existing DB
**Changed:** Deleted data/demo_dashboard.db and data/dashboard.db so init_db()
recreates all tables from scratch including the new strike_snapshots table.
**Why:** DB files were created in a prior session before strike_snapshots was
added to db.py. Existing files don't auto-migrate — CREATE TABLE IF NOT EXISTS
only adds tables missing from a blank DB, not from an already-existing file
that predates the schema change.
**Impact:** Lost ~1 session of synthetic demo history (no real data existed).
All tables now match current db.py schema. Won't recur unless schema changes
again without a migration step.
**Open questions / follow-ups:** For future schema changes, consider adding
a simple migration check in init_db() that runs ALTER TABLE or CREATE TABLE
for any new tables/columns, so existing DB files don't need to be deleted.

## 2026-06-21 — GitHub repo created + sync workflow established
**Changed:** Created private GitHub repo `chandan-singh4/spx-diagonal-dashboard`.
All project files as of end of this session pushed to `main` branch. `.gitignore`
already excludes `.env`, `data/token.json`, `data/dashboard.db` — credentials and
local data never go to GitHub.
**Why:** Needed a persistent source of truth that survives between Claude chat
sessions. Claude's sandbox is ephemeral; GitHub is not.
**Impact:** From this point forward, GitHub `main` is the canonical version of
the code. Any changes made in a Claude session must be applied locally and pushed
before closing — otherwise the next session starts from stale code.
**Workflow confirmed:** Repo is private, so Claude cannot fetch files directly.
Instead: paste DEV_JOURNAL.md at the start of each new chat (see instructions
above), paste specific files on request. Claude makes changes in sandbox, gives
edited files back, you apply locally and push.
**Open questions / follow-ups:** None for this entry — workflow is established.

## 2026-06-21 — Feature: strike-specific IV chart + independent expiry selectors
**Changed:**
- `db.py`: Added `strike_snapshots` table (expiry, strike, side, iv, bid, ask, volume, OI)
  with `save_strike_snapshot()` and `get_strike_history()` functions. ATM snapshots
  (existing) and strike snapshots are written independently each poll.
- `iv_engine.py`: Added `StrikeContract` dataclass and `strike_contract()` function
  — looks up a specific strike/side/expiry in the live chain_df, falls back to
  nearest available strike with a `found_exact=False` flag if the typed strike
  doesn't exist in the chain (e.g. coarser spacing far from spot).
- `app.py`: Full layout restructure:
  - Left panel: expiry selectors (now with stable `key=` — truly independent),
    + new Strike Selection section with two `st.number_input` fields (call strike,
    put strike), free-typed, defaulting to ATM and ATM-100 as starting suggestions.
    Live contract data (Front IV / Back IV / Ratio) shown per strike beneath inputs.
  - Right panel: TWO stacked charts:
    - TOP — "Selected-Strike IV": front vs back for the typed call strike (solid lines)
      and put strike (dotted lines), plus their respective IV ratios. Only renders
      once strike-specific history exists (first poll after strikes are entered).
    - BOTTOM — "ATM IV": existing floating-ATM chart, now explicitly labeled as
      "macro context."
  - All four legs recorded each poll when strikes are set: front_call, front_put,
    back_call, back_put (same strike both expiries, per confirmed strategy).

**Why:** ATM-only IV was confirmed insufficient for actual entry decisions — the
IV at your specific strike is what determines the real edge of a given trade, not
the floating ATM proxy. Also resolved the front/back expiry coupling bug (missing
`key=`) and the "only-selected-expiries-recorded" history gap from the prior session.

**Design decision logged:** User confirmed diagonal structure uses same strike on
both front and back for a given side (not four independent strikes). Transformation
to IC/double-diagonal handled separately via limit orders, not modeled in the IV
dashboard — IV analysis is entry-only.

**Impact:** Strike-specific chart will be blank until a few data points accumulate
(per the ~10s poll interval). ATM chart behavior unchanged.

**Open questions / follow-ups:**
- Demo mode's synthetic chain data uses a basic smile model (IV rises away from ATM)
  so the strike-specific chart will work in demo mode too, but IV values won't
  reflect realistic skew for a specific SPX strike — that's fine for UI testing.
- Theta Advantage score component still placeholder at 50. Next phase item.

## 2026-06-21 — Fix: front/back expiry coupling, and only-2-expiries-recorded history gap
**Changed:**
- `app.py` expiry selectors: removed the `back_options = [e for e in available_expiries if e > front_expiry]` filtering and the lack of `key=` on both selectboxes. Both now use the full expiry list with stable `key="front_expiry_select"` / `key="back_expiry_select"`, plus a non-blocking warning if Back ends up earlier than or equal to Front.
- `app.py` snapshot saving: now loops over every expiry visible in `chain_df` each poll and records its ATM IV, instead of only recording the two currently-selected (front/back) expiries.

**Why:** (1) Without explicit `key=`s, Streamlit treated the Back selectbox as a brand-new widget every time its option list changed (which happened every time Front changed, since the list was filtered relative to Front) — so it always reset to `index=0` of the new filtered list, which looked like "Back auto-snaps to a fixed offset after Front." (2) Only ever recording IV for the actively-selected pair meant switching the Front/Back dropdowns reset visible history to zero for the newly-selected pair — compounding the (separate, expected) issue of having started live data collection only today.

**Impact:** Front and Back are now fully independent selections that persist across reruns. History now accumulates for every visible expiry regardless of which pair is on screen, so switching expiries won't lose data going forward. This does NOT retroactively create history for today before this fix — only data captured after this change is recorded per-expiry-broadly.

**Open questions / follow-ups:** Strike-specific IV (not just ATM) is the next real gap, per feedback from another chat in this project — ATM-only IV doesn't reflect the actual contracts once specific strikes are selected for a trade. Scoping that as the next feature: needs a strike selector UI, a schema extension to store per-strike IV history, and `iv_engine` functions for non-ATM strike lookup.

## 2026-06-21 — Fix: OAuth "Redirect server exited" — switch to manual flow
**Changed:** `schwab_client.py`'s `get_client()` — replaced `schwab.auth.easy_client()`
with a manual check: load the cached token via `client_from_token_file()` if one
exists, otherwise authenticate via `schwab.auth.client_from_manual_flow()`.
**Why:** Your registered Schwab callback URL is `https://127.0.0.1` (no port).
`easy_client`'s automatic flow (`client_from_login_flow` under the hood) requires
a port number so it can spin up a local listener to auto-capture the OAuth
redirect — without one it fails with "Redirect server exited." The fix would be
adding a port to your registered callback URL, but per `schwab-py`'s own docs,
changing a registered callback URL likely triggers Schwab re-approval (can take
days). `client_from_manual_flow()` avoids the local listener entirely — it has
you copy-paste the post-login redirect URL by hand — so it works with your
exact already-approved callback URL, no portal changes, no re-approval wait.
**Impact:** First-ever login now requires one manual copy-paste step in the
terminal instead of being fully automatic. Every login after that (i.e., once
`data/token.json` exists) is unaffected — same automatic token-file loading and
refresh as before.
**Open questions / follow-ups:** This is still untested against the real Schwab
OAuth endpoint from my side (sandbox network can't reach it) — next checkpoint
is whether the manual copy-paste flow completes successfully and `token.json`
gets created.

## 2026-06-21 — Fix: Demo Mode silently reverting after process restart
**Changed:** `config.py` — `DEMO_MODE` now defaults to OFF automatically once real
`SCHWAB_APP_KEY`/`SCHWAB_APP_SECRET` are present in `.env`, instead of always
defaulting to ON. An explicit `DEMO_MODE=` value in `.env` still overrides this
either way.
**Why:** Confirmed root cause of "nothing happens when I toggle Demo Mode off and
restart" — every Streamlit process restart resets widget state to its coded
default, and that default was hardcoded to `True`. So toggling Demo Mode off in
the browser, then restarting the process (e.g. to pick up new `.env` values, per
the normal setup flow), silently flipped it back to Demo Mode without any error —
confirmed by `demo_dashboard.db`'s modified timestamp continuing to update every
poll cycle while `dashboard.db`/`token.json` never got created.
**Impact:** First-time setup still defaults to Demo Mode with zero config (no
credentials = no reason to default to live). Once real credentials exist, a
process restart no longer silently reverts to synthetic data — closes the exact
gap that caused the confusion.
**Open questions / follow-ups:** Live OAuth flow itself still untested from my
side (sandbox can't reach Schwab) — next real checkpoint is whether the login
popup/error appears correctly on the next restart.

## 2026-06-21 — Fix: mixed-precision timestamp parsing crash
**Changed:** `app.py` line ~138 — `pd.to_datetime(merged["timestamp"])` now passes
`format="ISO8601"`.
**Why:** Found while running locally for the first time. Seeded demo timestamps and
live-saved timestamps had inconsistent precision — Python's `datetime.isoformat()`
omits the microseconds segment when it's exactly zero, so some stored rows looked
like `...T14:30:00+00:00` and others like `...T19:26:22.672015+00:00`. Pandas
inferred one rigid format from the data and crashed (`ValueError: time data ...
doesn't match format`) the first time a mixed batch got parsed.
**Impact:** Chart now renders correctly. No data was lost — this was a parsing
bug, not a storage bug.
**Open questions / follow-ups:** None — confirmed working after the fix.

## 2026-06-21 — Flux-style chart, demo mode, schema redesign
**Changed:**
- Rebuilt the dashboard layout to match the NavigationTrading FLUX reference (top metric strip: IV Ratio/Front IV%/Back IV%/IV Index; expirations panel with day-change; dual-axis Front/Back IV + Ratio chart; historical range bars).
- Added a time-range selector: Today / 5D / 10D / 15D / 1M (per your request, beyond Flux's Today/5D/20D).
- Redesigned the DB schema from "snapshot per front/back pair" to "snapshot per expiry," so changing the front/back dropdown doesn't require fresh history — any two expiries' stored history can be joined on timestamp after the fact.
- Added `demo_data.py`: a synthetic, mean-reverting IV generator and a fake option chain, writing to a separate `demo_dashboard.db` so it never touches real collected data. Added a "Demo Mode" toggle in the sidebar (`app.py`), on by default, so the dashboard is runnable today with zero Schwab credentials.
- Added `iv_engine.range_stats()` for the historical-statistics range bars.

**Why:** You wanted the FLUX-style visualization and to "build something today" — but this sandbox can't reach `api.schwabapi.com` (network allowlist), so live data has to come from your machine. Demo Mode closes that gap: same UI, same chart code path, synthetic data instead of live, so you can verify the dashboard works *today* and just flip a switch once your local Schwab auth is wired up.

**Impact:** Breaking schema change — `iv_snapshots` table replaced by `expiry_snapshots`. No real data existed yet, so no migration needed. `app.py`'s data-fetch branch is now demo/live conditional; downstream chart and stats code is identical either way, which is intentional — verifies the same code path you'll actually trade against.

**Open questions / follow-ups:**
- IV Index metric is a simple average-of-all-expiries approximation, not FLUX's proprietary calc — revisit if you want it to mean something more specific.
- Theta Advantage in the Trade Quality Score is still a placeholder (50 flat) — Phase 3 item.
- Smoke-tested the full demo data → chart pipeline in the sandbox (seed → fetch → merge → range stats) — all passed. Have NOT been able to test live Schwab auth/data calls from this environment; that first live run is on you, locally.

## 2026-06-21 — Initial scaffold
**Changed:** Created project structure: README, config, Schwab auth client, SQLite
storage layer, IV term-structure engine, Streamlit MVP dashboard.
**Why:** Establish the baseline architecture before connecting real credentials —
get the plumbing right (auth, storage schema, IV math) before layering on UI polish.
**Impact:** No live strategy logic yet — this is infrastructure only. Trade Quality
Score, transformation calculator, and payoff diagrams are stubbed for Phase 3-5.
**Open questions / follow-ups:**
- Need real Schwab app approval before any live data flows.
- IV percentile calculations will be unreliable until several weeks of history accumulate.
- Decide on polling interval (currently defaulted to 10s) once you've observed actual
  Schwab API rate limit behavior in practice.
