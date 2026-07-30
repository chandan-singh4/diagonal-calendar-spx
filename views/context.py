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

    # ── The current selection, from the Position Controls bar ─────────────
    front_expiry: str
    back_expiry: str
    front_dte: int
    back_dte: int
    call_strike: float
    put_strike: float
    strikes_set: bool

    # ── Derived once in app.py, used by more than one tab ─────────────────
    # ts_now is iv_engine.term_structure(...); typed loosely so this module
    # stays free of anything it does not itself use.
    ts_now: Any
    diag_mark: float | None
    norm_deb: float | None

    # ── Memoised loaders, injected ────────────────────────────────────────
    # These are app.py's @st.cache_data wrappers, passed in rather than
    # imported. Same seam as `compute=` in core/ and `load=` in dataaccess/
    # (ADR-032): the cache belongs to the page, the caller belongs here, and
    # a view that reached for dataaccess directly would silently re-query on
    # every rerun with no visible symptom.
    load_atm_hist_fb: Callable[..., pd.DataFrame]
    load_diagonal_hist: Callable[..., pd.DataFrame]
