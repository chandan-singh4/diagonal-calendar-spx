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
    """At-the-money IV history for one contract.

    `expiry` is a display key and is passed on as one: `atm_iv_by_expiry` now
    carries a settlement column, so the third Friday's two contracts return two
    different series rather than sharing one (BUG-028, closed 2026-08-19).
    """
    rows = db.get_atm_iv_history(db_path, expiry, days)
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


def load_session_chain_df(db_path, session_date: str,
                          dte_max: int | None = None,
                          expiry: str | None = None) -> pd.DataFrame:
    """Every snapshot of one session's chain, in the working DataFrame shape.

    THE SAME BOUNDARY AS `load_chain_df`, ONE SESSION WIDE. Every transform
    here is that function's, applied to many snapshots at once rather than to
    one: the display key, the `side` column, and `iv` decimal -> percent.

    THE IV CONVERSION IS THE ONE THAT MATTERS. `core.gex.vanna_by_strike` and
    `charm_by_strike` take `iv` IN PERCENT — `_second_order_frame` says so —
    and the database stores it as a decimal. A session frame that skipped this
    line would hand 0.15 where 15.0 was meant, and Black-Scholes would return
    a number for it rather than an error: every vanna and charm wick would be
    wrong, plausibly shaped, and wrong in a direction nobody could eyeball.
    That is why this shares the boundary rather than reimplementing it.

    `snapshot_id`, `snapshot_timestamp` and `underlying_price` ride along, so
    a caller can group by snapshot and still have that snapshot's own spot —
    which every dollar-scaled measure needs, because scaling the morning by
    the afternoon's price folds the index's own move into the answer.
    """
    rows = db.get_session_chain(db_path, session_date, dte_max, expiry)
    if not rows:
        return pd.DataFrame()
    # NOT `[dict(r) for r in rows]`, which is what `load_chain_df` does. That
    # builds 410,000 dictionaries and costs 12s here; a Row is already a
    # sequence, so pandas can take the rows as they are. `load_chain_df` keeps
    # the simpler form because it handles one snapshot -- ~3,200 rows -- where
    # the difference is unmeasurable.
    df = pd.DataFrame(rows, columns=rows[0].keys())
    if "settlement" not in df.columns:
        df["settlement"] = None
    # ONE KEY PER CONTRACT, NOT ONE PER ROW. A session repeats the same ~40
    # (date, settlement) pairs across every snapshot and every strike, so the
    # per-row comprehension `load_chain_df` uses would call `contract.key`
    # 410,000 times to produce 40 distinct answers.
    pairs = df[["expiry_date", "settlement"]].drop_duplicates()
    keys = {(d, s): contract.key(d, s)
            for d, s in zip(pairs["expiry_date"], pairs["settlement"],
                            strict=True)}
    df["expiry"] = [keys[(d, s)] for d, s in
                    zip(df["expiry_date"], df["settlement"], strict=True)]
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


def load_latest_atm_iv(db_path, expiry: str, n: int = 2) -> list:
    """The n most recent ATM-IV snapshots for one contract (as plain dicts).

    Takes a display key — see load_atm_hist.
    """
    rows = db.get_latest_atm_iv_snapshots(db_path, expiry, n=n)
    return [dict(r) for r in rows] if rows else []


def load_diagonal_hist(db_path, front: str, back: str, call_s: float,
                       put_s: float, *, days: int) -> pd.DataFrame:
    """Diagonal net-debit history for one strike pair (scatter)."""
    rows = db.get_diagonal_history(db_path, front, back,
                                    call_s, put_s, days=days)
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def load_intraday_strike_metrics(db_path, session_date: str,
                                 dte_max: int | None = None,
                                 expiry: str | None = None) -> pd.DataFrame:
    """Per-strike, per-snapshot gamma/OI/volume for one session.

    `expiry` scopes to one contract by display key; `dte_max` bounds
    days-to-expiry. See get_intraday_strike_metrics for why they are separate.

    Timestamps come back as ZONED UTC, not stripped. Turning them into local
    wall-clock is a DISPLAY decision and belongs in core.charts.to_display_time
    at the last moment before drawing — the read layer handing out a bare
    "14:30" with nothing saying where is precisely DEBT-030.
    """
    rows = db.get_intraday_strike_metrics(db_path, session_date, dte_max, expiry)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["timestamp"] = pd.to_datetime(df["snapshot_timestamp"], utc=True)
    return df


def load_prior_session_oi(db_path, session_date: str,
                          expiry: str | None = None) -> pd.DataFrame:
    """Open interest per strike at the close of the previous session.

    `expiry` scopes it to one contract date, and must match the scope of
    whatever it is being subtracted from — see get_prior_session_oi.

    Empty when there is no prior session; the caller decides what to say about
    that, because "the first day of collection" and "a strike that is new
    today" are different stories.
    """
    rows = db.get_prior_session_oi(db_path, session_date, expiry)
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
