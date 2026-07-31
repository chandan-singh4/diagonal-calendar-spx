"""What every tab is handed.

WHY THIS EXISTS. Before step 2.4 the tab bodies were top-level `if` blocks
reading module-level names that app.py's prelude happened to have computed
further up — `front_expiry`, `ts_now`, `_diag_mark`, and the memoised
`_load_*` wrappers. That works only while everything shares one namespace,
which is precisely the thing being dismantled. A view that reads a global is
a view you cannot move, cannot read in isolation, and cannot reason about
without holding all 3,900 lines in your head.

GROWN ONE TAB AT A TIME, ON PURPOSE. This carries exactly the fields the
tabs extracted so far actually use, and gains fields as each further tab
moves. Declaring all ~30 up front would mean guessing at what the Calendar
Edge tab needs before reading it, and an unused field is indistinguishable
from one whose wiring was quietly dropped.

FROZEN. A view draws; it does not alter the state the next view will read.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ViewContext:
    # ── The snapshot being displayed ──────────────────────────────────────
    snapshot_id: int
    spx_price: float
    session_date: str
    # The snapshot's full timestamp. `session_date` is its first ten
    # characters and was all any tab needed until step 2.5; the Mission
    # Control card renderer stamps the whole thing onto a Journal deep-link,
    # so the full value has to travel too.
    snapshot_ts: str
    # The whole option chain for this snapshot. Handed over rather than
    # re-read: it is one DataFrame that app.py has already loaded through a
    # memo, and every tab that needs it needs the SAME one.
    chain_df: pd.DataFrame

    # ── The current selection, from the Position Controls bar ─────────────
    front_expiry: str
    back_expiry: str
    front_dte: int
    back_dte: int
    call_strike: float
    put_strike: float
    strikes_set: bool

    # ── Derived once in app.py, used by more than one tab ─────────────────
    # ts_now is iv_engine.term_structure(...) and theta_diff is
    # iv_engine.theta_differential(...); both typed loosely so this module
    # stays free of anything it does not itself use.
    ts_now: Any
    diag_mark: float | None
    norm_deb: float | None

    # Added for the Entry Analysis tab. Every one of these is None or absent
    # under some real condition — no strikes chosen, wing strikes missing
    # from the chain, fewer than 90 days of history — and the tab has a
    # distinct message for each. Optionality is the behaviour here, not an
    # oversight, so it stays visible in the types.
    straddle: float
    theta_diff: Any
    ic_mark: float | None
    iv_pct: float | None
    liquidity: float

    # ── Memoised loaders, injected ────────────────────────────────────────
    # These are app.py's @st.cache_data wrappers, passed in rather than
    # imported. Same seam as `compute=` in core/ and `load=` in dataaccess/
    # (ADR-032): the cache belongs to the page, the caller belongs here, and
    # a view that reached for dataaccess directly would silently re-query on
    # every rerun with no visible symptom.
    load_atm_hist_fb: Callable[..., pd.DataFrame]
    load_diagonal_hist: Callable[..., pd.DataFrame]
    load_latest_atm_iv: Callable[..., list]
    load_contract_hist: Callable[..., pd.DataFrame]
    load_transform_marks: Callable[..., pd.DataFrame]
    # Not a database read but the same seam and the same hazard: this is the
    # MEMOISED scanner. core/scanner.py holds the pure one, and calling that
    # by mistake returns identical rows while re-running 21 offset sweeps on
    # every rerun (ADR-032).
    compute_transform_scanner: Callable[..., pd.DataFrame]

    # ── Mission Control, computed once per script run ─────────────────────
    # `mc` is the dict _run_mission_control returns. It is built in app.py
    # regardless of which tab is active, because the header strip needs it
    # too, so it is a result to pass on rather than work for a view to do.
    mc: dict
    sc_max_rows: int

    # ── Entry-lock and registry actions, injected ─────────────────────────
    # These are app.py's thin wrappers over state/, and each one binds
    # config.STATE_DIR. Injecting them is what keeps views/ free of BOTH
    # config and state: a view that imported state.entry_locks would still
    # need to be told which directory, and reaching for config.STATE_DIR to
    # answer that is the hidden global the state/ layer rule was written to
    # prevent (DEBT-011, ADR-035).
    #
    # These WRITE — locks are created and cleared, the registry is
    # backfilled. That is the one place a view does more than draw, and it
    # is deliberate: the writes are user actions behind buttons, not a side
    # effect of rendering.
    entry_lock_key: Callable[..., str]
    create_entry_lock: Callable[..., None]
    clear_entry_lock: Callable[..., None]
    get_entry_lock: Callable[..., dict | None]
    render_all_locks_popover: Callable[..., None]
    backfill_eligible_history: Callable[..., None]

    # ── User settings that live in a JSON sidecar ─────────────────────────
    # Passed as a plain dict for the same reason as the loaders: the file is
    # read and written by app.py through state/, and a view that reached for
    # it directly would need config.STATE_DIR — the hidden global that
    # DEBT-011 and the state/ layer rule exist to prevent.
    chart_colors: dict[str, str]
