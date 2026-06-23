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
import logging
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import config
import db
import schwab_client

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_ET = ZoneInfo("America/New_York")

# Market session time boundaries (Eastern Time, no seconds/microseconds)
_OPEN_START = dtime(9, 30)    # OPEN session begins
_OPEN_END   = dtime(10, 0)    # OPEN ends / MIDDAY begins
_MIDDAY_END = dtime(15, 30)   # MIDDAY ends / CLOSE begins
_CLOSE_END  = dtime(16, 0)    # CLOSE ends — SPX underlying freezes at this point

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
    INFO+ → stdout (visible when running in terminal).
    WARNING+ → collector.log (persistent record of errors and gaps).
    """
    fmt     = "%(asctime)s  %(levelname)-8s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)

    file_handler = logging.FileHandler("collector.log", encoding="utf-8")
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
    """True if d is a weekday and not a market holiday."""
    return d.weekday() < 5 and not _is_holiday(d)


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
    """
    if not _is_trading_day(now_et.date()):
        return None

    # Strip seconds/microseconds for clean boundary comparison
    t = now_et.time().replace(second=0, microsecond=0)

    if _OPEN_START <= t < _OPEN_END:
        return "OPEN"
    if _OPEN_END <= t < _MIDDAY_END:
        return "MIDDAY"
    if _MIDDAY_END <= t < _CLOSE_END:
        return "CLOSE"
    return None


def _poll_interval(session: str) -> int:
    """Return the configured poll interval (seconds) for a market session."""
    if session in ("OPEN", "CLOSE"):
        return config.POLL_INTERVAL_EVENT
    return config.POLL_INTERVAL_NORMAL


def _classify_gap(gap_start_utc: datetime, gap_end_utc: datetime) -> str:
    """
    Heuristic classification of why a collection gap occurred.

    Returns one of:
      'HOLIDAY'           — gap aligns with a known market holiday
      'MARKET_CLOSED'     — entire gap falls outside market hours (overnight / weekend)
      'COLLECTOR_OFFLINE' — gap occurred during expected market hours

    This is used by the analytics layer to distinguish expected gaps (which should
    be excluded from data quality warnings) from unexpected ones.
    """
    gap_minutes = (gap_end_utc - gap_start_utc).total_seconds() / 60

    # Check each calendar day in the gap for a known holiday
    start_date = gap_start_utc.date()
    for offset in range(int(gap_minutes // (60 * 24)) + 2):
        check = start_date + timedelta(days=offset)
        if check > gap_end_utc.date():
            break
        if _is_holiday(check):
            return "HOLIDAY"

    # Large gaps (> 60 hours) almost certainly span a weekend
    if gap_minutes > 3600:
        return "MARKET_CLOSED"

    # If both endpoints are outside market hours on their respective days,
    # the gap is overnight / market-closed
    gap_start_et = gap_start_utc.astimezone(_ET)
    gap_end_et   = gap_end_utc.astimezone(_ET)

    after_close  = gap_start_et.time() >= _CLOSE_END
    before_open  = gap_end_et.time()   <  _OPEN_START

    if after_close and before_open:
        return "MARKET_CLOSED"

    return "COLLECTOR_OFFLINE"


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


def _get_approx_atm_iv_pct(chain_df: pd.DataFrame, underlying_price: float) -> float | None:
    """
    Quick ATM IV estimate (as a percentage) used for the 2SD informational
    window check in filter_chain_by_strike_window. Not stored anywhere.
    """
    if chain_df.empty:
        return None
    calls = chain_df[chain_df["side"] == "CALL"].copy()
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
    For each expiry in the filtered DataFrame, compute pre-aggregated ATM IV metrics
    and return a list of dicts for db.insert_atm_iv_records().

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

    for expiry_date, group in filtered_df.groupby("expiry"):
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
            "dte":                dte_val,
            "atm_strike":         atm_strike,
            "atm_call_iv":        atm_call_iv,
            "atm_put_iv":         atm_put_iv,
            "atm_avg_iv":         atm_avg_iv,
            "iv_spread_to_front": None,   # Computed below after sort
            "iv_ratio_to_front":  None,   # Computed below after sort
        })

    # Sort ascending by DTE so records[0] is always the front expiry
    records.sort(key=lambda r: r["dte"])

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
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    now_utc     = datetime.now(timezone.utc)
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

    # Unexpected gap: record it
    expected_lost = int(gap_minutes / (config.POLL_INTERVAL_NORMAL / 60))
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
    now_utc      = datetime.now(timezone.utc)
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
        max_date = today + timedelta(days=config.MAX_EXPIRY_DTE)
        raw_chain = schwab_client.get_option_chain(client, today, max_date)

        if not raw_chain:
            raise ValueError("Option chain API response was empty")

        # ── 5. Process chain ─────────────────────────────────────────────────
        chain_df = schwab_client.chain_to_dataframe(raw_chain)
        if chain_df.empty:
            raise ValueError("Option chain contained no contracts after parsing")

        raw_expiry_count = chain_df["expiry"].nunique()

        # Approx ATM IV and max DTE — used only for the 2SD informational check
        atm_iv_pct = _get_approx_atm_iv_pct(chain_df, underlying_price)
        max_dte    = _safe_int(chain_df["dte"].max()) if not chain_df["dte"].empty else None

        filtered_df = schwab_client.filter_chain_by_strike_window(
            chain_df,
            underlying_price,
            atm_iv_pct = atm_iv_pct,
            max_dte    = max_dte,
        )

        # ── 6. Build option rows ─────────────────────────────────────────────
        option_rows = _build_option_rows(filtered_df, underlying_price, snapshot_id)

        # ── 7. Compute ATM IV records ────────────────────────────────────────
        atm_iv_records      = _compute_atm_iv_records(filtered_df, underlying_price, snapshot_id)
        actual_expiry_count = len(atm_iv_records)

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
        if option_rows:
            db.insert_option_rows(db_path, option_rows)
        if atm_iv_records:
            db.insert_atm_iv_records(db_path, atm_iv_records)

        # ── 10. Finalize snapshot ─────────────────────────────────────────────
        latency_ms = int((time.monotonic() - cycle_start) * 1000)
        db.finalize_snapshot(
            db_path               = db_path,
            snapshot_id           = snapshot_id,
            status                = status,
            strikes_fetched       = len(option_rows),
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
                len(option_rows), actual_expiry_count, latency_ms,
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
    config.validate()

    db_path = args.db or config.DB_PATH
    db.init_db(db_path)

    logger.info("=" * 62)
    logger.info("SPX Diagonal Collector starting")
    logger.info("Database    : %s", db_path)
    logger.info("Max DTE     : %d days", config.MAX_EXPIRY_DTE)
    logger.info("Strikes     : %d per expiry / ±%d pt window",
                config.STRIKE_COUNT, config.STRIKE_FETCH_WIDTH_POINTS)
    logger.info("Poll normal : %ds  |  event: %ds",
                config.POLL_INTERVAL_NORMAL, config.POLL_INTERVAL_EVENT)
    logger.info("Sessions    : OPEN 09:30–10:00 | MIDDAY 10:00–15:30 | CLOSE 15:30–16:00 ET")
    logger.info("=" * 62)

    _check_startup_gap(db_path)

    client               = None
    consecutive_failures = 0
    prev_snapshot_ts: str | None = None

    while True:
        now_utc = datetime.now(timezone.utc)
        now_et  = now_utc.astimezone(_ET)
        session = get_session(now_et)

        # ── Market closed ────────────────────────────────────────────────────
        if session is None:
            if args.once:
                logger.info("Market is closed. --once mode: exiting.")
                sys.exit(0)
            logger.debug("Market closed (%s ET). Sleeping 60s.", now_et.strftime("%H:%M"))
            time.sleep(60)
            continue

        poll_interval    = _poll_interval(session)
        cycle_start_mono = time.monotonic()

        # ── Authenticate (lazy; re-init after auth failures) ─────────────────
        if client is None:
            logger.info("Authenticating with Schwab...")
            try:
                client = schwab_client.get_client()
                logger.info("Authentication successful.")
            except Exception as auth_err:
                logger.error("Authentication failed: %s", auth_err)
                logger.info(
                    "Retrying in %ds. If this persists, delete data/token.json "
                    "and re-run the OAuth flow.", _AUTH_RETRY_SECONDS
                )
                time.sleep(_AUTH_RETRY_SECONDS)
                continue

        # ── Collection cycle ─────────────────────────────────────────────────
        try:
            snapshot_id = _run_cycle(client, db_path, session, poll_interval)
            consecutive_failures = 0

            # Mid-session gap detection: flag if time since the previous snapshot
            # is much larger than expected (indicates a stall or silent failure)
            current_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if prev_snapshot_ts is not None:
                prev_dt = (
                    datetime.fromisoformat(prev_snapshot_ts)
                    .replace(tzinfo=timezone.utc)
                )
                gap_min   = (datetime.now(timezone.utc) - prev_dt).total_seconds() / 60
                threshold = (poll_interval / 60) * 2.5   # 2.5× expected interval
                if gap_min > threshold:
                    db.record_gap(
                        db_path                 = db_path,
                        gap_start               = prev_snapshot_ts,
                        gap_end                 = current_ts,
                        gap_minutes             = gap_min,
                        expected_snapshots_lost = int(gap_min / (poll_interval / 60)),
                        reason                  = "COLLECTOR_OFFLINE",
                        notes = "Detected mid-session: gap exceeded 2.5× expected interval",
                    )
                    logger.warning(
                        "Mid-session gap recorded: %.0f min between snapshots.", gap_min
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
            is_auth_error = any(
                kw in err_str.lower()
                for kw in ("401", "unauthorized", "token", "expired", "authentication")
            )

            if is_auth_error:
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
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
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
        sleep_time = max(0.0, poll_interval - elapsed)
        logger.debug("Cycle: %.1fs elapsed. Sleeping %.1fs.", elapsed, sleep_time)
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
