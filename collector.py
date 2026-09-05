"""
collector.py — Background SPX data collector for the Diagonal Calendar Dashboard.

Runs as a standalone process, independently of app.py. This is the ONLY component
that talks to the Schwab API and writes to the new snapshot-anchored SQLite schema.

USAGE
-----
  python collector.py              # runs indefinitely; Ctrl+C to stop
  python collector.py --once       # one cycle then exit (useful for testing)
  python collector.py --db PATH    # override database path

BEHAVIOR
--------
  - Auto-detects US market hours (9:30 AM – 4:00 PM ET, Mon–Fri, excl. holidays)
  - Sleeps outside market hours; activates at open without restart
  - OPEN session (9:30–10:00):  60-second polling (POLL_INTERVAL_EVENT)
  - MIDDAY session (10:00–15:30): 300-second polling (POLL_INTERVAL_NORMAL)
  - CLOSE session (15:30–16:00): 60-second polling (POLL_INTERVAL_EVENT)
  - No collection after 4:00 PM ET — SPX underlying freezes; IVs are unreliable
  - Detects and records collection gaps on startup and mid-session
  - Handles Schwab API timeouts, token expiry, and partial chain responses

FIRST RUN
---------
  If data/token.json does not exist, a browser window will open for Schwab OAuth.
  Copy-paste the redirect URL back into the terminal when prompted.
  After initial auth, the token is cached and auto-refreshed. Re-auth is needed
  approximately once per week (Schwab's 7-day refresh token limit).

REQUIREMENTS
------------
  pip install tzdata   # required on Windows for IANA timezone support (zoneinfo)
"""
from __future__ import annotations

import argparse
import atexit
import logging
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import config
import db
import schwab_client
from core import contract
from core import expiry as core_expiry
from core import pins as core_pins
from core import session as core_session
from state import entry_locks

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_ET = ZoneInfo("America/New_York")

# Market session time boundaries (Eastern Time, no seconds/microseconds).
# THE DEFINITIONS NOW LIVE IN core/session.py and are aliased here so the rest
# of this file reads unchanged. They moved in M3.4 because the dashboard header
# and the watchdog need the same boundaries, and the header's "prices are late"
# threshold must BE the collector's polling interval rather than a second copy
# of it that agrees today.
_OPEN_START = core_session.OPEN_START
_OPEN_END   = core_session.OPEN_END
_MIDDAY_END = core_session.MIDDAY_END
_CLOSE_END  = core_session.CLOSE_END

# Collector reliability settings
_BACKOFF_SECONDS          = 30    # Sleep between retries after a cycle failure
_AUTH_RETRY_SECONDS       = 60    # Sleep before retrying after auth failure
_MAX_CONSECUTIVE_FAILURES = 5     # Log CRITICAL after this many failures in a row

# Minimum elapsed time before an unexpected gap gets recorded in collection_gaps.
# Set to 1.5× the normal 5-minute poll interval to avoid recording one-off slow cycles.
_GAP_THRESHOLD_MINUTES = 8.0

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    """
    INFO+   → stdout (visible when running in a terminal).
    WARNING+→ collector.log (persistent record of errors and gaps).

    The file handler ROTATES (added 2026-07-25, M0.12). Previously it was a
    plain FileHandler, so collector.log grew without bound — it had reached
    486 KB and, because it was also tracked in git, every commit carried a log
    diff. Rotation caps total on-disk log at ~5 x 1 MB; the file is now
    gitignored.

    Paths are resolved relative to this file, not the process working
    directory, so the log always lands beside the code regardless of where the
    collector is launched from (Task Scheduler starts it from C:\\Windows\\System32).
    """
    fmt     = "%(asctime)s  %(levelname)-8s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    log_path = Path(__file__).resolve().parent / "collector.log"

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,     # 1 MB per file
        backupCount=5,          # keep 5 rotations => ~5 MB ceiling
        encoding="utf-8",
    )
    file_handler.setLevel(logging.WARNING)

    logging.basicConfig(
        level=logging.DEBUG,
        format=fmt,
        datefmt=datefmt,
        handlers=[stdout_handler, file_handler],
    )


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Market Hours Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _is_holiday(d: date) -> bool:
    """True if d is a US market holiday as defined in config.MARKET_HOLIDAYS."""
    return d.isoformat() in config.MARKET_HOLIDAYS


def _is_trading_day(d: date) -> bool:
    """True if d is a weekday and not a market holiday.

    Delegates to core/session.py (M3.4); this wrapper supplies the holiday set
    that a pure function is not allowed to reach for itself.
    """
    return core_session.is_trading_day(d, config.MARKET_HOLIDAYS)


def get_session(now_et: datetime) -> str | None:
    """
    Return the current market session name, or None if the market is closed.

    Sessions and their poll intervals:
      'OPEN'   → 09:30–10:00 ET  → POLL_INTERVAL_EVENT  (60s)
      'MIDDAY' → 10:00–15:30 ET  → POLL_INTERVAL_NORMAL (300s)
      'CLOSE'  → 15:30–16:00 ET  → POLL_INTERVAL_EVENT  (60s)
      None     → market closed (overnight, weekend, holiday)

    Collection stops at 16:00 ET — not 16:15 — because SPX (a cash-settled index)
    stops updating at equity-market close. IVs computed after 16:00 use a frozen
    underlying price, making them analytically unreliable.

    Delegates to core/session.py (M3.4). Kept as a named function here because
    the collector calls it in several places and the tests name it.
    """
    return core_session.session_of(now_et, config.MARKET_HOLIDAYS)


def _poll_interval(session: str) -> int:
    """Return the configured poll interval (seconds) for a market session."""
    return core_session.expected_interval(
        session, config.POLL_INTERVAL_EVENT, config.POLL_INTERVAL_NORMAL
    )


def market_minutes_between(start_utc: datetime, end_utc: datetime) -> float:
    """
    Minutes of ACTUAL open market inside [start_utc, end_utc).

    This is the single measurement the gap classifier needs, and it replaces
    three separate heuristics that each guessed at it (BUG-005). Sums the
    overlap of the window with the 09:30–16:02 ET session of every trading day
    it touches; weekends and holidays contribute nothing because they are not
    trading days. The window runs two minutes past the equity close on purpose
    (ADR-049), so a full trading day is 392 collectable minutes, not 390.

    Returns 0.0 for a window that is entirely outside market hours, however
    long it is — an overnight break and a three-day weekend both cost zero
    collectable minutes, which is the whole point.
    """
    if end_utc <= start_utc:
        return 0.0

    start_et = start_utc.astimezone(_ET)
    end_et   = end_utc.astimezone(_ET)

    total = 0.0
    day = start_et.date()
    while day <= end_et.date():
        if _is_trading_day(day):
            session_open  = datetime.combine(day, _OPEN_START, tzinfo=_ET)
            session_close = datetime.combine(day, _CLOSE_END, tzinfo=_ET)
            overlap_start = max(start_et, session_open)
            overlap_end   = min(end_et, session_close)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds() / 60
        day += timedelta(days=1)

    return total


# How much missed market time still counts as "routine". Not a fudge factor —
# it absorbs the collector's own cadence at the session boundaries:
#   - the last write of the day lands at 15:59:xx, up to ~1.0 min before 16:00
#   - the first write of the morning lands at 09:30:00–09:31:00, up to ~1.0 min
# Worst case ~2.0 minutes of session time that no configuration could have
# collected. 3.0 gives margin without masking anything meaningful: a genuine
# outage costing under 3 minutes of market time is under one MIDDAY poll cycle.
_ROUTINE_GAP_TOLERANCE_MINUTES = 3.0


# How long the loop idles when the market is shut. Named because it is now an
# upper bound rather than the sleep itself: near the open the loop sleeps less.
_CLOSED_IDLE_SECONDS = 60.0


def _classify_gap(gap_start_utc: datetime, gap_end_utc: datetime) -> str:
    """
    Classify why a collection gap occurred, by measuring how much open market
    it actually covers.

    Returns one of:
      'HOLIDAY'           — nothing was collectable, and a market holiday is why
      'MARKET_CLOSED'     — nothing was collectable (overnight / weekend)
      'COLLECTOR_OFFLINE' — open market was missed; this is a real fault

    REWRITTEN 2026-07-26 (BUG-005). The previous version stacked three
    heuristics and got the answer wrong in both directions:

      - `start.time() >= 16:00 and end.time() < 09:30` — the collector writes
        its last snapshot at 15:59 and restarts at 09:30–09:31, so neither test
        ever passed and EVERY ordinary night was reported as a fault.
      - `gap_minutes > 3600 → MARKET_CLOSED` — assumed any gap over 60 hours
        was a weekend, so a collector dead from Monday to Thursday, losing
        three full trading days, was reported as routine and then suppressed.
      - the holiday scan returned HOLIDAY if ANY day in the range was a
        holiday, so a week-long outage containing 3 July was filed as a
        holiday.

    The last two are the dangerous ones: they hid real data loss, which is the
    exact case the M3.4 liveness alert is meant to catch.

    Measuring market minutes replaces all three. A gap is routine if and only
    if there was nothing to collect during it, which is the actual question.
    """
    missed = market_minutes_between(gap_start_utc, gap_end_utc)

    if missed > _ROUTINE_GAP_TOLERANCE_MINUTES:
        return "COLLECTOR_OFFLINE"

    # Nothing collectable was missed. Say WHY, preferring the more specific
    # answer: a weekday that would have been a trading day but for a holiday.
    day = gap_start_utc.astimezone(_ET).date()
    last = gap_end_utc.astimezone(_ET).date()
    while day <= last:
        if day.weekday() < 5 and _is_holiday(day):
            return "HOLIDAY"
        day += timedelta(days=1)

    return "MARKET_CLOSED"


def _midsession_gap_reason(prev_utc: datetime, now_utc: datetime,
                            poll_interval: int) -> str | None:
    """
    Decide whether a mid-session gap is worth recording, and why.
    Returns the reason string, or None if the gap is routine and should not be
    recorded at all.

    EXTRACTED AND FIXED 2026-07-26 (BUG-005). This decision used to be inline
    in main(), where it hardcoded reason='COLLECTOR_OFFLINE' and never
    consulted the classifier. Since `prev_snapshot_ts` is not cleared when the
    market closes, the first cycle of every trading morning compared against
    15:59 the previous day — ~1,051 minutes against a 2.5-minute threshold —
    and wrote a false fault row. Once per trading day, which is a far larger
    source of the bad rows than the startup path the backlog blamed.
    """
    gap_min = (now_utc - prev_utc).total_seconds() / 60

    # Judge the gap against the SLOWER of the two cadences (BUG-005, third
    # defect — found 2026-07-26 by running the fixed classifier over the real
    # collection_gaps rows).
    #
    # The interval changes from 300s to 60s at 15:30 when MIDDAY becomes CLOSE.
    # Comparing a gap PRODUCED at the 300s cadence against the new 60s
    # threshold makes an ordinary 5-minute MIDDAY interval look like a stall:
    # 5.0 > 2.5. That fired at the session change every single trading day and
    # accounts for 22 of the 47 recorded rows — more than the overnight
    # misclassification did. The reverse transition at 10:00 (OPEN → MIDDAY) is
    # harmless but is covered by the same rule.
    prev_session   = get_session(prev_utc.astimezone(_ET))
    prev_interval  = _poll_interval(prev_session) if prev_session else poll_interval
    slowest        = max(poll_interval, prev_interval)

    if gap_min <= (slowest / 60) * 2.5:
        return None

    reason = _classify_gap(prev_utc, now_utc)
    if reason in ("MARKET_CLOSED", "HOLIDAY"):
        return None
    return reason


# ─────────────────────────────────────────────────────────────────────────────
# Chain Processing Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    """Return float(val), or None if val is null, NaN, zero, or unconvertible."""
    try:
        v = float(val)
        return v if (v == v and v != 0.0) else None   # v != v is the NaN check
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    """Return int(val), or None if val is null or unconvertible."""
    try:
        v = float(val)
        return int(v) if v == v else None   # guards NaN
    except (TypeError, ValueError):
        return None


def _shown_contract_only(df):
    """The single contract per (expiry, strike, side) the dashboard displays.

    Almost every SPX expiry is an SPXW weekly and therefore P.M.-settled; only
    the third-Friday monthly also lists an A.M. contract. Filtering on
    settlement != 'PM' would discard nearly the whole chain (BUG-026). The rule
    is "prefer the a.m. contract where one exists", read off the data rather
    than from calendar arithmetic.
    """
    if df.empty or "settlement" not in df.columns:
        return df
    is_am = df["settlement"].eq("AM")
    if not is_am.any():
        return df
    keys = pd.MultiIndex.from_frame(df[["expiry", "strike", "side"]])
    has_am_twin = keys.isin(keys[is_am.to_numpy()])
    shadowed = df["settlement"].eq("PM").to_numpy() & has_am_twin
    return df[~shadowed]


def _get_approx_atm_iv_pct(chain_df: pd.DataFrame, underlying_price: float) -> float | None:
    """
    Quick ATM IV estimate (as a percentage) used for the 2SD informational
    window check in filter_chain_by_strike_window. Not stored anywhere.
    """
    if chain_df.empty:
        return None
    # One contract only — see _shown_contract_only (BUG-023 / BUG-026).
    calls = _shown_contract_only(chain_df)
    calls = calls[calls["side"] == "CALL"].copy()
    if calls.empty:
        return None
    calls["_dist"] = (calls["strike"] - underlying_price).abs()
    nearest = calls.nsmallest(1, "_dist")
    return _safe_float(nearest["iv"].iloc[0]) if not nearest.empty else None


def _build_option_rows(filtered_df: pd.DataFrame,
                        underlying_price: float,
                        snapshot_id: int) -> list[dict]:
    """
    Convert the filtered chain DataFrame into a list of dicts for
    db.insert_option_rows().

    Transformations:
      side 'CALL'/'PUT' → right 'C'/'P'
      settlement carried through unchanged ('AM'/'PM'/None — see BUG-023)
      iv (Schwab %) ÷ 100 → iv (decimal, e.g. 0.184)
      bid + ask → mark = (bid + ask) / 2
      underlying_price + strike + right → intrinsic_value, time_value
      Rows with no IV (stale / no market) are silently skipped.
    """
    rows = []

    for _, row in filtered_df.iterrows():
        iv_pct = _safe_float(row.get("iv"))
        if iv_pct is None:
            continue   # No IV → illiquid or no market; not worth storing

        right  = "C" if row["side"] == "CALL" else "P"
        strike = float(row["strike"])
        bid    = _safe_float(row.get("bid"))
        ask    = _safe_float(row.get("ask"))
        mark   = (bid + ask) / 2.0 if (bid is not None and ask is not None) else None

        if right == "C":
            intrinsic = max(0.0, underlying_price - strike)
        else:
            intrinsic = max(0.0, strike - underlying_price)

        time_val = (mark - intrinsic) if mark is not None else None

        rows.append({
            "snapshot_id":     snapshot_id,
            "expiry_date":     str(row["expiry"]),
            "dte":             _safe_int(row.get("dte")),
            "strike":          strike,
            "right":           right,
            "settlement":      row.get("settlement"),   # 'AM' | 'PM' | None (BUG-023)
            "bid":             bid,
            "ask":             ask,
            "mark":            mark,
            "last":            _safe_float(row.get("last")),
            "iv":              iv_pct / 100.0,   # percentage → decimal
            "delta":           _safe_float(row.get("delta")),
            "gamma":           _safe_float(row.get("gamma")),
            "theta":           _safe_float(row.get("theta")),
            "vega":            _safe_float(row.get("vega")),
            "volume":          _safe_int(row.get("volume")),
            "open_interest":   _safe_int(row.get("open_interest")),
            "intrinsic_value": round(intrinsic, 4),
            "time_value":      round(time_val, 4) if time_val is not None else None,
        })

    return rows


def _compute_atm_iv_records(filtered_df: pd.DataFrame,
                              underlying_price: float,
                              snapshot_id: int) -> list[dict]:
    """
    For each CONTRACT in the filtered DataFrame, compute pre-aggregated ATM IV
    metrics and return a list of dicts for db.insert_atm_iv_records().

    ATM strike = strike closest to underlying_price at collection time.
    All IVs stored as decimals (÷ 100).

    iv_spread_to_front and iv_ratio_to_front are computed after sorting by DTE,
    so records[0] is always the front (shortest-DTE) expiry. The front expiry
    itself has None for these fields.

    These records are the primary input to IV percentile calculations and term
    structure charts — they exist so the analytics layer doesn't have to scan
    the full option_rows table for every dashboard query.
    """
    records = []

    # BUG-028: on the third Friday the frame holds BOTH the a.m. and p.m.
    # contracts, so the grouping is by contract and not by date. It used to drop
    # the p.m. one here to keep atm_iv_by_expiry's one-row-per-date shape, which
    # meant the whole analytics layer could not see the p.m. contract at all.
    # The table now carries a settlement column and both rows are written.
    #
    # dropna=False because settlement is None on any chain that arrives without
    # it; groupby would otherwise discard those rows entirely and write nothing.
    frame = filtered_df
    if not frame.empty and "settlement" not in frame.columns:
        frame = frame.assign(settlement=None)
    grouped = ([] if frame.empty
               else frame.groupby(["expiry", "settlement"], dropna=False))

    for (expiry_date, settlement), group in grouped:
        dte_val = _safe_int(group["dte"].dropna().iloc[0]) if not group["dte"].dropna().empty else None
        if dte_val is None:
            continue

        unique_strikes = group["strike"].dropna().unique()
        if len(unique_strikes) == 0:
            continue

        atm_strike = float(min(unique_strikes, key=lambda s: abs(s - underlying_price)))
        atm_rows   = group[group["strike"] == atm_strike]

        call_rows = atm_rows[atm_rows["side"] == "CALL"]
        put_rows  = atm_rows[atm_rows["side"] == "PUT"]

        call_iv_pct = _safe_float(call_rows["iv"].iloc[0] if not call_rows.empty else None)
        put_iv_pct  = _safe_float(put_rows["iv"].iloc[0]  if not put_rows.empty  else None)

        # Convert to decimal
        atm_call_iv = call_iv_pct / 100.0 if call_iv_pct is not None else None
        atm_put_iv  = put_iv_pct  / 100.0 if put_iv_pct  is not None else None

        # Average IV: use both sides if available, fall back to whichever exists
        if atm_call_iv is not None and atm_put_iv is not None:
            atm_avg_iv = (atm_call_iv + atm_put_iv) / 2.0
        else:
            atm_avg_iv = atm_call_iv or atm_put_iv

        records.append({
            "snapshot_id":        snapshot_id,
            "expiry_date":        str(expiry_date),
            "settlement":         settlement if settlement in (contract.AM, contract.PM) else None,
            "dte":                dte_val,
            "atm_strike":         atm_strike,
            "atm_call_iv":        atm_call_iv,
            "atm_put_iv":         atm_put_iv,
            "atm_avg_iv":         atm_avg_iv,
            "iv_spread_to_front": None,   # Computed below after sort
            "iv_ratio_to_front":  None,   # Computed below after sort
        })

    # Sort ascending by DTE so records[0] is always the front expiry. The third
    # Friday's two contracts share a DTE, so the tie is broken the same way the
    # dropdown breaks it — a.m. first, because it settles at the open and really
    # does end first.
    #
    # Honest note: groupby already emits "AM" before "PM" because it sorts its
    # keys, so removing this line does not change today's answer and no test
    # catches it. It is here to STATE which contract the term structure is
    # measured from, rather than leave that resting on pandas' key order.
    records.sort(key=lambda r: (r["dte"], 0 if r["settlement"] == contract.AM else 1))

    # Compute spreads and ratios relative to the front expiry
    if records:
        front_avg = records[0]["atm_avg_iv"]
        for i, rec in enumerate(records):
            if i == 0:
                continue   # Front expiry: these fields are intentionally None
            this_avg = rec["atm_avg_iv"]
            if front_avg is not None and this_avg is not None and front_avg > 0:
                rec["iv_spread_to_front"] = round(this_avg - front_avg, 6)
                rec["iv_ratio_to_front"]  = round(this_avg / front_avg, 6)

    return records


# ─────────────────────────────────────────────────────────────────────────────
# Startup Gap Detection
# ─────────────────────────────────────────────────────────────────────────────

def _check_startup_gap(db_path: str) -> None:
    """
    Called once on collector startup. Compares the last snapshot timestamp in
    the database to the current time. If the gap is large enough and occurred
    during expected market hours, records it in collection_gaps so the analytics
    layer has an accurate picture of data coverage.

    Market-closed and holiday gaps are detected and suppressed — only unexpected
    gaps (collector was offline during market hours) get recorded.
    """
    last_ts_str = db.get_last_snapshot_timestamp(db_path)

    if last_ts_str is None:
        logger.info("Startup: fresh database — no prior snapshots.")
        return

    last_dt = datetime.fromisoformat(last_ts_str)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=UTC)

    now_utc     = datetime.now(UTC)
    gap_minutes = (now_utc - last_dt).total_seconds() / 60

    logger.info(
        "Startup: last snapshot was %.0f min ago (%s UTC).",
        gap_minutes, last_ts_str,
    )

    if gap_minutes <= _GAP_THRESHOLD_MINUTES:
        return   # Normal gap — no record needed

    reason = _classify_gap(last_dt, now_utc)

    if reason in ("MARKET_CLOSED", "HOLIDAY"):
        logger.info(
            "Startup gap (%.0f min) is classified as %s — not recorded.",
            gap_minutes, reason,
        )
        return

    # Unexpected gap: record it. Snapshots lost is counted from MARKET minutes,
    # not wall clock (BUG-005) — a restart at 09:40 after a Friday close used to
    # be billed for the whole weekend, reporting ~486 snapshots lost when the
    # real figure was 2.
    missed_market_min = market_minutes_between(last_dt, now_utc)
    expected_lost = int(missed_market_min / (config.POLL_INTERVAL_NORMAL / 60))
    now_str       = now_utc.strftime("%Y-%m-%d %H:%M:%S")

    db.record_gap(
        db_path=db_path,
        gap_start=last_ts_str,
        gap_end=now_str,
        gap_minutes=gap_minutes,
        expected_snapshots_lost=expected_lost,
        reason=reason,
        notes="Detected on collector startup",
    )

    logger.warning(
        "Startup gap recorded: %.0f min of missing data "
        "(~%d snapshots lost). Reason: %s.",
        gap_minutes, expected_lost, reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Single Collection Cycle
# ─────────────────────────────────────────────────────────────────────────────

def _load_pins() -> core_pins.Pins:
    """Which expiries and strikes the entry locks require us to keep (BUG-022).

    NEVER RAISES. This is the collector's first read of a file the DASHBOARD
    owns, and the snapshot is worth more than the protection: a lock file that
    is missing, empty, corrupt or being rewritten as we read costs the locks
    their exemption for this cycle and nothing else. `state.store.read_json`
    already handles missing and corrupt (quarantine, return {}) and writes
    atomically via os.replace, so a half-written read is not possible — the
    catch here is for the cases neither of us thought of.
    """
    try:
        return core_pins.from_locks(entry_locks.load(config.STATE_DIR))
    except Exception:
        logger.exception("could not read entry locks; collecting unpinned this cycle")
        return core_pins.Pins()


def _run_cycle(client, db_path: str, session: str, poll_interval: int) -> int:
    """
    Execute one complete collection cycle. Returns snapshot_id on success.
    Raises on unrecoverable errors (API failure, empty chain, DB error).

    Cycle steps:
      1. Fetch SPX quote (bid, ask, last, mark)
      2. Fetch VIX spot (non-fatal if unavailable)
      3. Create snapshot record with status='PARTIAL'
      4. Fetch SPX option chain (all expirations ≤ MAX_EXPIRY_DTE)
      5. Flatten chain to DataFrame; apply ±300pt strike filter
      6. Build option_rows (one dict per contract)
      7. Compute atm_iv_by_expiry records (one dict per expiry)
      8. Determine snapshot status (COMPLETE / PARTIAL / FAILED)
      9. Write option_rows and atm_iv_by_expiry to database
     10. Finalize snapshot with status and metadata

    Snapshot is created as PARTIAL in step 3 so that if the process crashes
    during steps 4–9, an auditable record exists (no orphaned data, no silent loss).
    """
    cycle_start  = time.monotonic()
    now_utc      = datetime.now(UTC)
    now_utc_str  = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    snapshot_id: int | None = None

    try:
        # ── 1. SPX quote ─────────────────────────────────────────────────────
        quote = schwab_client.get_spx_quote_full(client)
        underlying_price = quote.get("mark") or quote.get("last")
        if not underlying_price:
            raise ValueError("SPX quote returned no usable price")

        # ── 2. VIX (non-fatal) ───────────────────────────────────────────────
        vix = schwab_client.get_vix_quote(client)   # returns None on failure

        # ── 3. Create PARTIAL snapshot ───────────────────────────────────────
        snapshot_id = db.create_snapshot(
            db_path            = db_path,
            snapshot_timestamp = now_utc_str,
            market_session     = session,
            poll_interval_used = poll_interval,
            underlying_price   = underlying_price,
            underlying_bid     = quote.get("bid"),
            underlying_ask     = quote.get("ask"),
            vix_value          = vix,
        )

        # ── 4. Fetch option chain ────────────────────────────────────────────
        today    = date.today()
        max_date = today + timedelta(days=config.MAX_EXPIRY_FETCH_DAYS)
        raw_chain = schwab_client.get_option_chain(client, today, max_date)

        if not raw_chain:
            raise ValueError("Option chain API response was empty")

        # ── 5. Process chain ─────────────────────────────────────────────────
        chain_df = schwab_client.chain_to_dataframe(raw_chain)
        if chain_df.empty:
            raise ValueError("Option chain contained no contracts after parsing")

        # Locked legs are exempt from BOTH narrowings below (BUG-022). Read once
        # per cycle, before either, so the two exemptions cannot disagree.
        pinned = _load_pins()

        # NEW: keep only the nearest MAX_EXPIRY_COUNT expirations (sorted by date)
        all_expiries = sorted(chain_df["expiry"].unique())
        keep_expiries = set(all_expiries[:config.MAX_EXPIRY_COUNT])

        # A locked expiry the broker returned is kept even if it falls outside
        # the nearest N. Intersected with what actually arrived, so a lock on an
        # expiry beyond the fetch window widens nothing and invents nothing.
        pinned_expiries = pinned.expiry_dates & (set(all_expiries) - keep_expiries)
        if pinned_expiries:
            logger.info(
                "keeping %d expiry(ies) beyond the nearest %d because a lock "
                "depends on them (BUG-022): %s",
                len(pinned_expiries), config.MAX_EXPIRY_COUNT,
                ", ".join(sorted(pinned_expiries)),
            )
            keep_expiries |= pinned_expiries

        # An expiry the broker did not return because it ALREADY EXPIRED is not
        # news: no broker lists a contract that is over, and the lock holding it
        # is finished. Warning about it anyway printed this line every minute of
        # every session for as long as the stale lock sat in the file -- and the
        # only thing that clears such a lock is `entry_locks.purge_expired`,
        # whose sole caller is the DASHBOARD. A collector running for a fortnight
        # with nobody opening the app therefore warned ~5,500 times about a
        # position that finished in August.
        #
        # That is precisely how BUG-030 hid for eight weeks behind 2,181
        # identical warnings. This line has to stay rare to stay readable: it is
        # the one that says a LIVE position has stopped being recorded.
        #
        # Only the message is filtered, never the pin. core/pins.py deliberately
        # does not judge expiry, and a stale lock pinning a few extra strikes
        # costs a handful of rows -- the safe direction, unchanged here.
        missing = pinned.expiry_dates - set(all_expiries)
        unexpected = {
            e for e in missing
            if not core_expiry.is_expired(e, datetime.now(UTC))
        }
        if unexpected:
            logger.warning(
                "a lock depends on expiry(ies) the broker did not return: %s — "
                "the dashboard will show defaults for that lock (BUG-022)",
                ", ".join(sorted(unexpected)),
            )
        if missing - unexpected:
            # Said once, at DEBUG, so the record still explains the silence.
            logger.debug(
                "ignoring %d expired pinned expiry(ies) the broker did not "
                "return: %s — the lock is over and awaits purge_expired",
                len(missing - unexpected), ", ".join(sorted(missing - unexpected)),
            )

        chain_df = chain_df[chain_df["expiry"].isin(keep_expiries)]

        raw_expiry_count = chain_df["expiry"].nunique()

        # Approx ATM IV and max DTE — used only for the 2SD informational check
        atm_iv_pct = _get_approx_atm_iv_pct(chain_df, underlying_price)
        max_dte    = _safe_int(chain_df["dte"].max()) if not chain_df["dte"].empty else None

        filtered_df = schwab_client.filter_chain_by_strike_window(
            chain_df,
            underlying_price,
            atm_iv_pct = atm_iv_pct,
            max_dte    = max_dte,
            keep_strikes = pinned.strikes,
        )

        # ── 6. Build option rows ─────────────────────────────────────────────
        option_rows = _build_option_rows(filtered_df, underlying_price, snapshot_id)

        # ── 7. Compute ATM IV records ────────────────────────────────────────
        atm_iv_records      = _compute_atm_iv_records(filtered_df, underlying_price, snapshot_id)
        # Distinct DATES, not records. There is one record per CONTRACT now
        # (BUG-028), so counting records would report 21 of 20 expiries on the
        # third Friday and make the coverage check below meaningless.
        actual_expiry_count = len({r["expiry_date"] for r in atm_iv_records})

        # ── 8. Determine snapshot status ─────────────────────────────────────
        status    = "COMPLETE"
        error_msg = None

        if not option_rows:
            status    = "FAILED"
            error_msg = "No option rows after filtering (all had null IV)"
        elif actual_expiry_count < raw_expiry_count:
            # Some expiries had no ATM IV — partial data
            status    = "PARTIAL"
            error_msg = (
                f"ATM IV computed for {actual_expiry_count}/{raw_expiry_count} expiries; "
                f"{len(option_rows)} option rows written"
            )

        # ── 9. Write to database ─────────────────────────────────────────────
        #
        # Use what the database actually STORED, not what was offered (BUG-017,
        # 2026-07-26). insert_option_rows() uses INSERT OR IGNORE, which SQLite
        # applies to every constraint — not just uniqueness — so a row failing
        # a CHECK or NOT NULL is silently skipped rather than raised (ADR-022).
        # Recording the offered count made a snapshot that had lost rows report
        # full coverage, which is worse than losing them loudly: the gap is
        # unrecoverable and the record reads as intact.
        rows_stored = db.insert_option_rows(db_path, option_rows) if option_rows else 0
        if atm_iv_records:
            db.insert_atm_iv_records(db_path, atm_iv_records)

        # ── 10. Finalize snapshot ─────────────────────────────────────────────
        latency_ms = int((time.monotonic() - cycle_start) * 1000)
        db.finalize_snapshot(
            db_path               = db_path,
            snapshot_id           = snapshot_id,
            status                = status,
            strikes_fetched       = rows_stored,
            expiries_fetched      = actual_expiry_count,
            collection_latency_ms = latency_ms,
            error_message         = error_msg,
        )

        if status == "COMPLETE":
            logger.info(
                "✓ snap=%-6d | %-6s | SPX=%7.2f | VIX=%-5s | "
                "rows=%-5d | exp=%2d | %dms",
                snapshot_id, session, underlying_price,
                f"{vix:.2f}" if vix else "N/A",
                rows_stored, actual_expiry_count, latency_ms,
            )
        else:
            logger.warning(
                "⚠ snap=%d | status=%s | %s", snapshot_id, status, error_msg
            )

        return snapshot_id

    except Exception as exc:
        # Mark the snapshot FAILED so there's an auditable record
        if snapshot_id is not None:
            latency_ms = int((time.monotonic() - cycle_start) * 1000)
            try:
                db.finalize_snapshot(
                    db_path               = db_path,
                    snapshot_id           = snapshot_id,
                    status                = "FAILED",
                    strikes_fetched       = 0,
                    expiries_fetched      = 0,
                    collection_latency_ms = latency_ms,
                    error_message         = str(exc)[:500],
                )
            except Exception as finalize_err:
                logger.error(
                    "Could not finalize FAILED snapshot %d: %s",
                    snapshot_id, finalize_err,
                )
        raise   # Re-raise so main() can classify and handle


# ─────────────────────────────────────────────────────────────────────────────
# Main Loop
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Schwab token expiry warning
#
# Schwab expires the refresh token 7 days after the interactive login, and there
# is nothing this code can do about that — renewing it needs a browser login and
# a copy-pasted redirect URL, by design.
#
# What this code CAN do is stop the expiry being a surprise. Until 2026-07-26 the
# collector only noticed after the fact: the token lapsed, calls started failing,
# and a whole session's prices were lost before anyone looked at the log. These
# warnings fire while there is still time to act.
# ─────────────────────────────────────────────────────────────────────────────

_REFRESH_TOKEN_LIFETIME_DAYS = 7.0
_TOKEN_WARN_AFTER_DAYS       = 6.0    # ~1 day of warning
_TOKEN_CHECK_INTERVAL_SEC    = 3600   # hourly; the value only moves on a re-login


def token_days_remaining(age_days: float | None) -> float | None:
    """Days left on the refresh token, or None if the age is unknown.

    Split out as a pure function so it is testable without a token file, a
    clock, or the Schwab API.
    """
    if age_days is None:
        return None
    return _REFRESH_TOKEN_LIFETIME_DAYS - age_days


def _check_token_expiry() -> None:
    """Log a warning when the Schwab token is close to expiring, or has expired."""
    try:
        age = schwab_client.get_token_age_days()
    # A broken check must never stop collection: this catch is deliberately
    # broad. (The `# noqa: BLE001` that carried this note was redundant --
    # BLE is not in the enabled rule set -- so ruff removed it, and the
    # reasoning with it.)
    except Exception:
        return

    remaining = token_days_remaining(age)
    if remaining is None:
        logger.warning(
            "No Schwab token found. Run `python scripts/reauth.py` to log in."
        )
        return

    if remaining <= 0:
        logger.critical(
            "SCHWAB TOKEN EXPIRED %.1f days ago — data collection will fail until you "
            "run `python scripts/reauth.py`. This needs a browser login and cannot be "
            "automated.", abs(remaining),
        )
    elif age >= _TOKEN_WARN_AFTER_DAYS:
        logger.warning(
            "Schwab token expires in %.1f days (%.1f days old). Run "
            "`python scripts/reauth.py` before then to avoid losing a session.",
            remaining, age,
        )
    else:
        logger.info("Schwab token OK — %.1f days remaining.", remaining)


# ─────────────────────────────────────────────────────────────────────────────
# Single-instance guard
#
# WHY THIS EXISTS (2026-07-26): two collectors were found running side by side,
# both started at logon two seconds apart. There is only ONE launcher — the
# Startup-folder shortcut — but Windows 11's "automatically restart my apps
# when I sign back in" is enabled by default, so Windows relaunched the
# collector left running from the previous session while the shortcut launched
# it again. Two instances poll Schwab twice and write interleaved rows for the
# same snapshot.
#
# Guarding here rather than in start_collector.bat is deliberate: the guard must
# hold however the process is launched — shortcut, Windows app-restart, VS Code
# terminal, or Task Scheduler.
#
# The lock is an OS file lock, not a PID file, so it cannot go stale: if the
# collector is killed or the machine loses power, the kernel drops the lock.
# ─────────────────────────────────────────────────────────────────────────────

_LOCK_PATH = Path(__file__).resolve().parent / ".collector.lock"

# Module-level so the handle stays open — and the lock held — for the process
# lifetime. A local variable would be garbage-collected and release the lock.
_lock_handle = None


def _acquire_single_instance_lock() -> None:
    """Take an exclusive lock, or exit(0) if another collector already holds it.

    Exit code 0, not 1: a second copy declining to start is correct behaviour,
    not a failure. A non-zero code would make Task Scheduler and any future
    supervisor treat the normal case as an error worth retrying.
    """
    global _lock_handle

    _lock_handle = open(_LOCK_PATH, "a+")  # noqa: SIM115 — held for process lifetime

    try:
        if os.name == "nt":
            import msvcrt

            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Held by another process. Report to the console as well as the log —
        # a user who double-clicked the launcher only sees the console, and
        # needs to know why the window closed without collecting anything.
        msg = (
            f"Another collector is already running (lock: {_LOCK_PATH}). "
            f"This instance is exiting so the two do not both poll Schwab "
            f"and write duplicate snapshot rows."
        )
        print(msg)  # noqa: T201 — pre-logging, user-facing
        logger.warning(msg)
        _lock_handle.close()
        _lock_handle = None
        sys.exit(0)

    # Record who holds it. Purely diagnostic — the lock itself is what enforces
    # exclusivity, so a truncate/write failure here must not stop the collector.
    try:
        _lock_handle.seek(1)
        _lock_handle.truncate()
        _lock_handle.write(
            f" pid={os.getpid()} started={datetime.now(_ET).isoformat(timespec='seconds')}\n"
        )
        _lock_handle.flush()
    except OSError:
        pass

    atexit.register(_release_single_instance_lock)


def _release_single_instance_lock() -> None:
    """Release the lock on clean exit. The OS also releases it on a hard kill."""
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        _lock_handle.close()
        _lock_handle = None


# ─────────────────────────────────────────────────────────────────────────────
# Loop decisions
#
# EXTRACTED 2026-07-26 (M1.6). These three judgements used to be inline inside
# main()'s `while True`, which no test can enter: it never returns, it sleeps in
# real time, and it calls the Schwab API. That is why the retry and re-auth
# paths were the last part of the collector with no checks at all — not because
# they were unimportant, but because the loop shape made them unreachable.
#
# Each is a pure function of its inputs, in the same style as
# token_days_remaining() and _midsession_gap_reason(). main() reads as before;
# the decisions can now be examined directly.
# ─────────────────────────────────────────────────────────────────────────────

# Substrings that mark a failure as an authentication problem rather than a
# data or network one. Matched case-insensitively against the exception text,
# because Schwab reports auth failures through several unrelated shapes: a bare
# HTTP 401, a JSON error body, and schwab-py's own exception text.
_AUTH_ERROR_MARKERS = (
    "401", "unauthorized", "token", "expired", "authentication",
)


def is_auth_error(error_text: str) -> bool:
    """True if a cycle failure looks like an authentication problem.

    Auth failures are handled differently from every other failure: the client
    is discarded so the next cycle logs in again. Getting this wrong is
    survivable in both directions but not free — a missed auth error means
    every remaining cycle of the session fails against a dead client, and a
    false positive throws away a working client for no reason.

    Deliberately substring-based rather than exception-type-based: the same
    condition arrives as an HTTPError, a ValueError, or a plain RuntimeError
    depending on which layer noticed it first.
    """
    lowered = error_text.lower()
    return any(marker in lowered for marker in _AUTH_ERROR_MARKERS)


def failure_is_critical(consecutive_failures: int) -> bool:
    """True once repeated failures deserve a CRITICAL log entry.

    The collector keeps retrying either way — it never gives up on its own,
    because a collector that exits on a bad afternoon loses the rest of the
    day's prices. This only controls how loudly the run is reported.
    """
    return consecutive_failures >= _MAX_CONSECUTIVE_FAILURES


def sleep_after_cycle(poll_interval: int, elapsed_seconds: float) -> float:
    """Seconds to sleep so cycles land on the poll interval, not after it.

    Drift correction: the cycle itself takes 5–13 seconds against a 300-second
    interval. Sleeping the full interval after each one would push every
    snapshot progressively later, so the time spent working is subtracted.

    Never negative. A cycle slower than its own interval sleeps zero and the
    next one starts immediately — falling behind is recorded by the gap
    detector, not corrected by sleeping backwards.
    """
    return max(0.0, poll_interval - elapsed_seconds)


def should_recheck_token(now_mono: float, last_check_mono: float) -> bool:
    """True when the token expiry check is due again (roughly hourly).

    Exists so a collector left running for days keeps warning about an expiring
    token, rather than warning once at startup and then going quiet for the
    rest of the week.
    """
    return (now_mono - last_check_mono) >= _TOKEN_CHECK_INTERVAL_SEC


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SPX Diagonal Dashboard — background data collector"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one collection cycle then exit (for testing / verification)",
    )
    parser.add_argument(
        "--db", default=None, metavar="PATH",
        help="Override database path (default: config.DB_PATH)",
    )
    args = parser.parse_args()

    _setup_logging()

    # Before any API call or database write: refuse to be a second instance.
    _acquire_single_instance_lock()

    config.validate()

    db_path = args.db or config.DB_PATH
    db.init_db(db_path)

    logger.info("=" * 62)
    logger.info("SPX Diagonal Collector starting")
    logger.info("Database    : %s", db_path)
    logger.info("Max DTE     : %d days", config.MAX_EXPIRY_COUNT)
    logger.info("Strikes     : %d per expiry / ±%d pt window",
                config.STRIKE_COUNT, config.STRIKE_FETCH_WIDTH_POINTS)
    logger.info("Poll normal : %ds  |  event: %ds",
                config.POLL_INTERVAL_NORMAL, config.POLL_INTERVAL_EVENT)
    logger.info("Sessions    : OPEN 09:30–10:00 | MIDDAY 10:00–15:30 | CLOSE 15:30–16:00 ET")
    logger.info("=" * 62)

    _check_startup_gap(db_path)
    _check_token_expiry()

    client               = None
    consecutive_failures = 0
    last_token_check     = time.monotonic()
    prev_snapshot_ts: str | None = None

    while True:
        now_utc = datetime.now(UTC)
        now_et  = now_utc.astimezone(_ET)
        session = get_session(now_et)

        # ── Market closed ────────────────────────────────────────────────────
        if session is None:
            if args.once:
                logger.info("Market is closed. --once mode: exiting.")
                sys.exit(0)
            # Sleep the usual minute -- UNLESS the open is nearer than that,
            # in which case sleep exactly up to it. Without this the 60s phase
            # is arbitrary and the day's first poll landed anywhere in
            # 09:30:00-09:30:59 (measured: 09:30:11 to 09:30:55). See
            # core.session.seconds_until_open for why the answer is not to
            # start before 09:30.
            until_open = core_session.seconds_until_open(
                now_et, config.MARKET_HOLIDAYS
            )
            idle = _CLOSED_IDLE_SECONDS
            if until_open is not None and until_open < idle:
                idle = max(until_open, 0.0)
                logger.info(
                    "Market opens in %.1fs. Sleeping exactly that so the first "
                    "poll lands on the open.", idle
                )
            else:
                logger.debug("Market closed (%s ET). Sleeping %.0fs.",
                             now_et.strftime("%H:%M"), idle)
            time.sleep(idle)
            continue

        poll_interval    = _poll_interval(session)
        cycle_start_mono = time.monotonic()

        # Re-check the token roughly hourly. The value only moves on a re-login,
        # so this is cheap; the point is that a collector left running for days
        # keeps warning, rather than warning once at startup and going quiet.
        if should_recheck_token(cycle_start_mono, last_token_check):
            _check_token_expiry()
            last_token_check = cycle_start_mono

        # ── Authenticate (lazy; re-init after auth failures) ─────────────────
        if client is None:
            logger.info("Authenticating with Schwab...")
            try:
                client = schwab_client.get_client()
                logger.info("Authentication successful.")
            except Exception as auth_err:
                logger.error("Authentication failed: %s", auth_err)
                logger.info(
                    "Retrying in %ds. If this persists the 7-day refresh token has "
                    "expired: run `python scripts/reauth.py` (an interactive browser "
                    "login — it cannot be done from here).", _AUTH_RETRY_SECONDS
                )
                time.sleep(_AUTH_RETRY_SECONDS)
                continue

        # ── Collection cycle ─────────────────────────────────────────────────
        try:
            snapshot_id = _run_cycle(client, db_path, session, poll_interval)
            consecutive_failures = 0

            # Mid-session gap detection: flag if time since the previous snapshot
            # is much larger than expected (indicates a stall or silent failure)
            current_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            if prev_snapshot_ts is not None:
                prev_dt = (
                    datetime.fromisoformat(prev_snapshot_ts)
                    .replace(tzinfo=UTC)
                )
                now_dt    = datetime.now(UTC)
                gap_min   = (now_dt - prev_dt).total_seconds() / 60
                reason    = _midsession_gap_reason(prev_dt, now_dt, poll_interval)
                if reason is not None:
                    # Count what was actually lost — market minutes, not wall
                    # clock. A gap straddling the close used to be billed for
                    # the overnight hours as well (BUG-005).
                    missed_market_min = market_minutes_between(prev_dt, now_dt)
                    db.record_gap(
                        db_path                 = db_path,
                        gap_start               = prev_snapshot_ts,
                        gap_end                 = current_ts,
                        gap_minutes             = gap_min,
                        expected_snapshots_lost = int(
                            missed_market_min / (poll_interval / 60)),
                        reason                  = reason,
                        notes = "Detected mid-session: gap exceeded 2.5× expected interval",
                    )
                    logger.warning(
                        "Mid-session gap recorded: %.0f min between snapshots "
                        "(%.0f min of open market missed).", gap_min, missed_market_min
                    )
            prev_snapshot_ts = current_ts

            if args.once:
                logger.info("--once mode: cycle complete. Exiting.")
                sys.exit(0)

        except KeyboardInterrupt:
            logger.info("Stopped by user (Ctrl+C).")
            sys.exit(0)

        except Exception as cycle_err:
            consecutive_failures += 1
            err_str = str(cycle_err)

            # Detect token / auth errors to force re-authentication next cycle
            if is_auth_error(err_str):
                logger.warning(
                    "Auth error detected — will re-authenticate next cycle. Error: %s",
                    cycle_err,
                )
                client = None
                time.sleep(_BACKOFF_SECONDS)
            else:
                logger.error(
                    "Cycle failure #%d: %s", consecutive_failures, cycle_err
                )
                if failure_is_critical(consecutive_failures):
                    logger.critical(
                        "%d consecutive failures. Check Schwab API status. "
                        "Collector is still running and will keep retrying.",
                        consecutive_failures,
                    )
                time.sleep(_BACKOFF_SECONDS)

            if args.once:
                logger.error("--once mode: cycle failed. Exiting with error.")
                sys.exit(1)
            continue

        # ── Drift-corrected sleep ────────────────────────────────────────────
        elapsed    = time.monotonic() - cycle_start_mono
        sleep_time = sleep_after_cycle(poll_interval, elapsed)
        logger.debug("Cycle: %.1fs elapsed. Sleeping %.1fs.", elapsed, sleep_time)
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
