"""Every database read the dashboard makes, memoised.

THE MEMO IS THE ONLY THING HERE. The nine reads themselves are
dataaccess/queries.py; each wrapper below does three things and no more:
apply `@st.cache_data`, supply `config.DB_PATH`, and carry the snapshot_id
that keys the cache (ADR-033).

WHY THE SPLIT EXISTS AT ALL. A new snapshot is the ONLY thing that changes any
of these results, so within a snapshot window every rerun — tab click, widget
change, autorefresh tick — is a cache hit rather than a fresh query and
DataFrame rebuild. `st.cache_data` returns a COPY on each call, so downstream
code can safely mutate the frames it gets.

The snapshot_id arguments never reach the query: they were always cache keys
alone, which is why they do not appear in dataaccess/queries.py.

MOVED HERE IN M2 STEP 2.5. `dataaccess/` cannot hold these — it must not
import streamlit, and it must not decide which database to read (DEBT-027).
`core/` cannot either. That is what services/ is for.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
import db
from core.scanner import compute_transform_scanner as _compute_transform_scanner_pure
from dataaccess import queries

# ─────────────────────────────────────────────────────────────────────────────
# Helper — unicode sparkline
# ─────────────────────────────────────────────────────────────────────────────

# sparkline, fmt_duration and fmt_eta moved to core/format.py;
# banded_ratio_traces moved to core/charts.py (ADR-032).


# ─────────────────────────────────────────────────────────────────────────────
# Helper — ATM IV history
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# The nine reads themselves are dataaccess/queries.py. What stays here is the
# memo and nothing else (ADR-033).
#
# Each wrapper below does three things and no more: apply @st.cache_data, supply
# config.DB_PATH, and carry the snapshot_id that keys the cache. A new snapshot
# is the ONLY thing that changes any of these results, so within a snapshot
# window every rerun — tab click, widget change, autorefresh tick — is a cache
# hit rather than a fresh query and DataFrame rebuild. st.cache_data returns a
# COPY on each call, so downstream code can safely mutate the frames it gets.
#
# The snapshot_id arguments never reach the query: they were always cache keys
# alone, which is why they do not appear in dataaccess/queries.py.

@st.cache_data(ttl=55, show_spinner=False, max_entries=48)
def _load_atm_hist(expiry: str, days: int) -> pd.DataFrame:
    return queries.load_atm_hist(config.DB_PATH, expiry, days)

@st.cache_data(ttl=55, show_spinner=False, max_entries=48)
def _load_atm_hist_fb(expiry: str, days: int) -> pd.DataFrame:
    # load= hands the fallback the MEMOISED loader above, so its second read
    # reuses saved results instead of querying again. See dataaccess/queries.py.
    return queries.load_atm_hist_fb(config.DB_PATH, expiry, days,
                                     load=_load_atm_hist)

@st.cache_data(ttl=55, show_spinner=False, max_entries=48)
def _load_contract_hist(expiry: str, strike: float,
                         side: str, days: int) -> pd.DataFrame:
    return queries.load_contract_hist(config.DB_PATH, expiry, strike, side, days)

@st.cache_data(ttl=120, show_spinner=False, max_entries=3)
def _load_chain_df(snapshot_id: int) -> pd.DataFrame:
    return queries.load_chain_df(config.DB_PATH, snapshot_id)

@st.cache_data(ttl=120, show_spinner=False, max_entries=3)
def _load_spx_intraday(session_date: str, snapshot_id: int) -> pd.DataFrame:
    return queries.load_spx_intraday(config.DB_PATH, session_date)

@st.cache_data(ttl=300, show_spinner=False, max_entries=3)
def _load_prior_close(session_date: str) -> "float | None":
    return queries.load_prior_close(config.DB_PATH, session_date)

@st.cache_data(ttl=55, show_spinner=False, max_entries=32)
def _load_transform_marks(front: str, back: str, call_s: float, put_s: float,
                           days: int, snapshot_id: int) -> pd.DataFrame:
    return queries.load_transform_marks(config.DB_PATH, front, back,
                                         call_s, put_s, days=days)

@st.cache_data(ttl=55, show_spinner=False, max_entries=32)
def _load_latest_atm_iv(expiry: str, snapshot_id: int, n: int = 2) -> list:
    return queries.load_latest_atm_iv(config.DB_PATH, expiry, n)

@st.cache_data(ttl=55, show_spinner=False, max_entries=32)
def _load_diagonal_hist(front: str, back: str, call_s: float, put_s: float,
                         days: int, snapshot_id: int) -> pd.DataFrame:
    return queries.load_diagonal_hist(config.DB_PATH, front, back,
                                       call_s, put_s, days=days)

# ─────────────────────────────────────────────────────────────────────────────
# The scanner itself is core/scanner.py. The memo stays HERE, because core/
# cannot import streamlit — and it is load-bearing: this wrapper's saved
# results are shared between the Scanner tab and the 21-offset Phase A sweep,
# so every sweep after the first is nearly free. scan_all_offsets is handed
# this wrapper explicitly (see _compute_mc_core); left to its default it would
# call the uncached function and recompute all 21 offsets on every rerun.
compute_transform_scanner = st.cache_data(
    ttl=120, show_spinner=False, max_entries=8
)(_compute_transform_scanner_pure)

# ─────────────────────────────────────────────────────────────────────────────
# Database init + latest complete snapshot
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _init_db_once(_db_path: str) -> bool:
    """Ensure schema exists — but only ONCE per dashboard process.

    Previously db.init_db() ran on every script rerun (every tab click, widget
    change, and autorefresh tick). init_db() executes the full DDL script and
    COMMITS a write transaction, so the dashboard — nominally a pure reader —
    was issuing a database write on every interaction, contending with the
    collector's write lock. @st.cache_resource runs this exactly once and
    caches the result for the life of the process.
    """
    db.init_db(_db_path)
    return True


# The Gamma Exposure tab's time-aware panels. Keyed on snapshot_id as well as
# the date so a new snapshot invalidates the memo — the same pattern as
# _load_spx_intraday, and the reason the argument is present but unused.
@st.cache_data(ttl=55, show_spinner=False, max_entries=4)
def _load_intraday_strike_metrics(session_date: str, snapshot_id: int,
                                  dte_max: "int | None" = None) -> pd.DataFrame:
    return queries.load_intraday_strike_metrics(config.DB_PATH, session_date, dte_max)


@st.cache_data(ttl=300, show_spinner=False, max_entries=3)
def _load_prior_session_oi(session_date: str) -> pd.DataFrame:
    return queries.load_prior_session_oi(config.DB_PATH, session_date)
