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
