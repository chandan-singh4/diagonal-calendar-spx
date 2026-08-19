"""The nine reads the dashboard makes against the price history.

Each is a thin wrapper over one `db.py` query plus the normalisation that has to
happen at the load boundary: rows to a DataFrame, implied volatility from
decimal to percent, timestamps into display time.

Pinned by tests/test_mc_pipeline_golden.py, which exercises them through
`app.py`'s memoised wrappers against a real temporary database.

TWO THINGS DELIBERATELY NOT DONE HERE (M2 step 2.2, ADR-033):

  * `snapshot_id` is gone from the four signatures that carried it. It was
    never read — it existed only to key Streamlit's cache. The cache lives in
    app.py, so its key does too.

  * ~~The timezone conversion stays~~ — DONE 2026-07-30, DEBT-030 closed by
    ADR-038. These reads now return ZONED UTC and take no display decision.
    Whoever draws converts, through `core.charts.to_display_time`, which is
    the one place that knows Plotly's rangebreaks need a naive value.

    If you add a read here: return the timestamp as stored. Do not localise
    it however convenient that is for the caller in front of you — the next
    caller is not a chart. The one place market time is still unavoidable is
    `load_contract_hist`'s "today" filter, where the calendar date IS a
    trading-session question; it converts locally and does not write it back.
"""
from __future__ import annotations

import pandas as pd

import config
import db
from core import contract


def load_atm_hist(db_path, expiry: str, days: int) -> pd.DataFrame:
    """At-the-money IV history for one expiry.

    Takes a display key but reads by DATE, so on the third Friday both
    contracts return the same series. `atm_iv_by_expiry` is a daily summary
    with no settlement column, so there is only one row to return — recorded
    as BUG-028. Everything derived from `option_rows` does tell them apart.
    """
    rows = db.get_atm_iv_history(db_path, contract.date_of(expiry), days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={"snapshot_timestamp": "timestamp",
                             "atm_avg_iv": "atm_iv"})
    df["atm_iv"] = df["atm_iv"] * 100
    # Zoned UTC, exactly as stored. Converting to a local wall-clock is the
    # chart's job (core.charts.to_display_time) — DEBT-030, ADR-038.
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    return df


def load_atm_hist_fb(db_path, expiry: str, days: int, *, load=None) -> pd.DataFrame:
    """load_atm_hist, falling back to the last populated day when today is empty.

    `load` — the per-expiry loader to call, taking (expiry, days). Production
    passes app.py's memoised `_load_atm_hist` so the fallback's second read
    reuses saved results instead of hitting the database again. Defaults to the
    uncached function above; see the package docstring.
    """
    def fetch(exp: str, d: int) -> pd.DataFrame:
        return load(exp, d) if load is not None else load_atm_hist(db_path, exp, d)

    df = fetch(expiry, days)
    if df.empty and days == 1:
        df = fetch(expiry, 5)
        if not df.empty:
            last_date = df["timestamp"].dt.date.max()
            df = df[df["timestamp"].dt.date == last_date]
    return df


def load_contract_hist(db_path, expiry: str, strike: float,
                       side: str, days: int) -> pd.DataFrame:
    """IV history for one exact contract, widening to 5 days if today is empty.

    `expiry` is a display key, so the third Friday's two contracts get two
    different charts rather than one blended one.
    """
    right_char = "C" if side == "CALL" else "P"
    exp_date, settlement = contract.parse(expiry)
    rows = db.get_contract_iv_history(db_path, exp_date, strike, right_char,
                                      days, settlement)
    if not rows and days == 1:
        rows = db.get_contract_iv_history(db_path, exp_date, strike, right_char,
                                          5, settlement)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["iv"] = df["iv"] * 100
    # Zoned UTC, exactly as stored — DEBT-030, ADR-038.
    df["timestamp"] = pd.to_datetime(df["snapshot_timestamp"],
                                     format="ISO8601", utc=True)
    if days == 1 and not df.empty:
        # "Today" means the last TRADING day, so this date comparison has to
        # happen in market time and cannot follow the column into UTC. A
        # 20:00 New York row is already tomorrow in UTC, so comparing UTC
        # dates would split one session across two and return only the
        # after-hours tail. The conversion is local to this filter and is
        # deliberately NOT written back to the column.
        local_date = df["timestamp"].dt.tz_convert(config.DISPLAY_TIMEZONE).dt.date
        df = df[local_date == local_date.max()]
    return df


def load_chain_df(db_path, snapshot_id: int) -> pd.DataFrame:
    """Full option chain for a snapshot, built into the working DataFrame once.

    `expiry` is the DISPLAY KEY, not a date: the third Friday appears twice,
    once as "2026-08-21" for the p.m. contract and once as "2026-08-21 (AM)"
    for the a.m. one (core/contract.py). Everything downstream keys off this
    column, so the two contracts stay apart all the way to the screen without
    every caller needing to know they exist.

    `expiry_date` is kept alongside it as the plain date, because charts and
    day-count arithmetic need a real date and must not parse the key back.
    """
    rows = db.get_option_chain(db_path, snapshot_id)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    if "settlement" not in df.columns:
        df["settlement"] = None
    df["expiry"] = [
        contract.key(d, s)
        for d, s in zip(df["expiry_date"], df["settlement"], strict=True)
    ]
    df["side"] = df["right"].map({"C": "CALL", "P": "PUT"})
    df["iv"] = df["iv"] * 100  # decimal -> percent, at the load boundary
    return df


def load_spx_intraday(db_path, session_date: str) -> pd.DataFrame:
    """Intraday SPX path for the session."""
    rows = db.get_spx_intraday_today(db_path, session_date)
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def load_prior_close(db_path, session_date: str) -> float | None:
    """Prior session close — stable for the whole session."""
    return db.get_prior_session_close(db_path, session_date)


def load_transform_marks(db_path, front: str, back: str, call_s: float,
                         put_s: float, *, days: int) -> pd.DataFrame:
    """Transform/diagonal mark history for one strike pair (gap chart)."""
    rows = db.get_transform_mark_history(db_path, front, back,
                                          call_s, put_s, days=days)
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def load_latest_atm_iv(db_path, exp_date: str, n: int = 2) -> list:
    """The n most recent ATM-IV snapshots for an expiry (as plain dicts).

    Reads by date — see load_atm_hist on why the two contracts share these.
    """
    rows = db.get_latest_atm_iv_snapshots(db_path, contract.date_of(exp_date), n=n)
    return [dict(r) for r in rows] if rows else []


def load_diagonal_hist(db_path, front: str, back: str, call_s: float,
                       put_s: float, *, days: int) -> pd.DataFrame:
    """Diagonal net-debit history for one strike pair (scatter)."""
    rows = db.get_diagonal_history(db_path, front, back,
                                    call_s, put_s, days=days)
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
