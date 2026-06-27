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
