"""
schwab_client.py — Authentication + thin data-access layer over the Schwab API.

Uses the `schwab-py` community library, which handles the OAuth dance and token
refresh for you. We wrap it so the rest of the app (iv_engine.py, app.py,
collector.py) never has to think about auth, tokens, or raw HTTP — it just calls
get_spx_quote() or get_option_chain() and gets clean data back.

Reference: https://schwab-py.readthedocs.io/
"""

import logging
import math
from pathlib import Path

import pandas as pd
import schwab

import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────────────

def get_client():
    """
    Returns an authenticated schwab-py client.

    Uses the MANUAL OAuth flow (client_from_manual_flow), not easy_client's
    automatic webapp flow. Why: easy_client/client_from_login_flow spin up a
    local HTTP server on the port specified in your callback URL to auto-capture
    the redirect — which requires your registered callback URL to include a
    port number (e.g. https://127.0.0.1:8182). If your callback URL is just
    https://127.0.0.1 with no port (a common default, e.g. from following
    Schwab's own setup guides), that flow fails with "Redirect server exited."
    Changing your registered callback URL to add a port also triggers Schwab
    re-approval, which can take days — not worth it just to skip a copy-paste
    step. The manual flow sidesteps all of this: it prints a URL, you log in
    and authorize in your browser, then copy-paste the resulting (broken-looking,
    that's expected) redirect URL back into the terminal. No portal changes needed.

    First run: walks you through that copy-paste login in the terminal, then
    caches the token to config.SCHWAB_TOKEN_PATH.

    Subsequent runs: loads the cached token directly and auto-refreshes it as
    needed — no login flow at all, as long as a token file already exists.

    You'll need to redo the login about once every 7 days (Schwab expires
    refresh tokens on that schedule — not something this code can change).
    """
    config.validate()

    token_path = Path(config.SCHWAB_TOKEN_PATH)

    if token_path.exists():
        return schwab.auth.client_from_token_file(
            token_path=config.SCHWAB_TOKEN_PATH,
            api_key=config.SCHWAB_APP_KEY,
            app_secret=config.SCHWAB_APP_SECRET,
        )

    return schwab.auth.client_from_manual_flow(
        api_key=config.SCHWAB_APP_KEY,
        app_secret=config.SCHWAB_APP_SECRET,
        callback_url=config.SCHWAB_CALLBACK_URL,
        token_path=config.SCHWAB_TOKEN_PATH,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    """
    Return float(val), or None if val is null, NaN, zero, or unconvertible.
    Used to sanitize Schwab API response fields before returning to callers.
    """
    try:
        v = float(val)
        return v if (v == v and v != 0.0) else None   # v != v catches NaN
    except (TypeError, ValueError):
        return None


# Schwab's "I have no value for this" marker, sent in place of a number for
# implied volatility and every greek. BUG-030.
#
# It is not a near-miss or a rounding artefact: across 18.7M stored rows, every
# single non-positive IV was exactly this value after the collector's /100, and
# every poisoned greek was exactly this before it. It arrives mostly at 09:30 on
# the longer-dated expiries, which have not traded yet when the bell rings — the
# broker has quotes for them but nothing to compute a volatility from.
#
# Storing it verbatim broke the standing "missing price -> blank, not 0" rule
# with something considerably worse than 0: as an IV it reads as -999%, and it
# dominates any average, minimum or ratio it enters.
SCHWAB_NO_VALUE = -999.0


def _value_or_none(val) -> float | None:
    """A Schwab numeric field, with the no-value marker turned into a blank.

    EXACT equality, deliberately. -9.99 is a perfectly ordinary theta — an
    option losing $9.99 a day — and 38 rows in the record legitimately hold it.
    A tolerance band, or testing `< -100`, would delete real data to tidy up a
    sentinel. Only the marker itself is a marker.
    """
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    return None if v == SCHWAB_NO_VALUE else v


# ─────────────────────────────────────────────────────────────────────────────
# Quotes
# ─────────────────────────────────────────────────────────────────────────────

def get_spx_quote(client) -> float:
    """
    Returns the current SPX index last price as a float.
    Kept for backward compatibility with existing callers (app.py).
    For new code, prefer get_spx_quote_full() which returns bid/ask as well.
    """
    resp = client.get_quote(config.UNDERLYING_SYMBOL)
    resp.raise_for_status()
    data = resp.json()
    return float(data[config.UNDERLYING_SYMBOL]["quote"]["lastPrice"])


def get_spx_quote_full(client) -> dict:
    """
    Returns SPX bid, ask, last, and mark as a dict.
    Used by collector.py to populate snapshot.underlying_price/bid/ask.

    SPX is an index (not a traded security), so bid and ask may be None —
    Schwab does not always publish a two-sided quote for the index itself.
    In that case, mark falls back to lastPrice.

    Return format:
        {
            "bid":  float | None,
            "ask":  float | None,
            "last": float | None,
            "mark": float | None,   # (bid+ask)/2, or last if bid/ask unavailable
        }
    """
    resp = client.get_quote(config.UNDERLYING_SYMBOL)
    resp.raise_for_status()
    q    = resp.json()[config.UNDERLYING_SYMBOL]["quote"]

    bid  = _safe_float(q.get("bidPrice") or q.get("bid"))
    ask  = _safe_float(q.get("askPrice") or q.get("ask"))
    last = _safe_float(q.get("lastPrice"))

    if bid is not None and ask is not None:
        mark = (bid + ask) / 2.0
    else:
        mark = last

    return {"bid": bid, "ask": ask, "last": last, "mark": mark}


def get_vix_quote(client) -> float | None:
    """
    Returns the current VIX spot value, or None if the fetch fails.

    VIX is stored alongside each SPX snapshot to provide volatility regime
    context for historical IV percentile analysis. A high VIX explains *why*
    front-month IV is elevated; without it, an 88th-percentile reading could
    be misread as SPX-specific when it's a broad market event.

    This call is deliberately non-fatal: if Schwab's VIX quote is unavailable,
    the snapshot is still recorded with vix_value=NULL. The caller should
    handle None gracefully.
    """
    try:
        resp = client.get_quote(config.VIX_SYMBOL)
        resp.raise_for_status()
        q = resp.json()[config.VIX_SYMBOL]["quote"]
        return _safe_float(q.get("lastPrice"))
    except Exception as exc:
        logger.warning("VIX quote fetch failed (non-fatal): %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Option Chain
# ─────────────────────────────────────────────────────────────────────────────

def get_option_chain(client, from_date, to_date,
                     strike_count: int = config.STRIKE_COUNT) -> dict:
    """
    Pulls the raw SPX option chain between from_date and to_date (datetime.date
    objects). to_date is typically set to today + config.MAX_EXPIRY_DTE (20 days)
    by the caller so all relevant diagonal expiries are included in one fetch.

    strike_count controls how many strikes above and below ATM Schwab returns per
    expiry. The default (config.STRIKE_COUNT = 80) covers approximately ±300–400
    points at SPX's typical near-ATM strike spacing of 5 points — wide enough to
    include all practical diagonal calendar candidates without pulling the entire
    listed chain. A Python-side filter (filter_chain_by_strike_window) enforces
    the hard ±300-point boundary after the fetch as a safety backstop.

    Why not range='ALL': the full SPX chain contains 300–600 strikes per expiry,
    producing ~12 MB payloads at 10 expirations. At 2-minute polling that is
    unnecessary bandwidth — roughly 70% of every response would be discarded.
    strike_count=80 achieves the same practical coverage at ~2.5 MB per call.
    """
    resp = client.get_option_chain(
        config.UNDERLYING_SYMBOL,
        from_date=from_date,
        to_date=to_date,
        strike_count=strike_count,
    )
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# AM vs PM settlement  (BUG-023)
# ─────────────────────────────────────────────────────────────────────────────
#
# On the third Friday of each month SPX lists TWO different options for the same
# date and strike: the traditional monthly, which settles at the OPENING price
# and stops trading the evening before, and the weekly (SPXW), which trades all
# day and settles at the CLOSE. They are not interchangeable — on expiry day one
# is already finished while the other has a full session of decay left.
#
# Schwab returns both under the same expiry key, so before this existed the
# parser could not tell them apart, and the database — whose uniqueness rule had
# no room for the difference — silently discarded the second one. Exactly 160
# rows per cycle, 2,181 times in the log, for the whole life of the project.
#
# Two signals, preferred in this order:
#   settlementType — Schwab's own field: 'A' (a.m.) or 'P' (p.m.)
#   symbol root    — 'SPXW' prefix means the weekly, hence p.m.
# The root is the fallback because settlementType is the broker's explicit
# answer, while the root is an inference from a naming convention.
#
# Returns None when neither signal is present. None means "not recorded", NOT
# "a.m." — see the note on legacy rows in db.py. Guessing here would put a wrong
# answer into the permanent record, which is worse than an honest blank.

def settlement_of(contract: dict) -> str | None:
    """Classify one Schwab contract as 'AM', 'PM', or None (unknown)."""
    raw = (contract.get("settlementType") or "").strip().upper()
    if raw.startswith("A"):
        return "AM"
    if raw.startswith("P"):
        return "PM"

    symbol = (contract.get("symbol") or "").strip().upper()
    if symbol.startswith("SPXW"):
        return "PM"
    if symbol.startswith("SPX"):
        return "AM"

    return None


def chain_to_dataframe(raw_chain: dict) -> pd.DataFrame:
    """
    Flattens Schwab's nested option chain JSON (callExpDateMap / putExpDateMap,
    each keyed by expiry-string -> strike-string -> [contract]) into one tidy
    DataFrame with one row per contract.

    Columns returned:
        expiry, dte, strike, side (CALL/PUT), settlement (AM/PM/None),
        bid, ask, last, volume, open_interest,
        iv (percentage, e.g. 18.4 for 18.4% — caller divides by 100 for storage),
        delta, gamma, theta, vega

    Note on IV: Schwab's "volatility" field is returned as a percentage.
    The collector divides by 100 before writing to the database (stored as
    decimal: 0.184). This conversion happens in collector.py, not here, so
    app.py code that reads from the legacy schema remains unaffected.

    Note on missing values (BUG-030): iv and the four greeks come back as
    SCHWAB_NO_VALUE when the broker has nothing to give — typically at 09:30,
    on expiries that have not traded yet. Those arrive here as None, not as a
    number, so nothing downstream has to know the marker exists. That /100 is
    also why the marker used to land in the database as -9.99 for IV while
    staying -999.0 for the greeks: same marker, one of them scaled.
    """
    rows = []
    for side, key in (("CALL", "callExpDateMap"), ("PUT", "putExpDateMap")):
        exp_map = raw_chain.get(key, {})
        for exp_str, strikes in exp_map.items():
            # Schwab formats expiry keys like "2026-06-26:5" (date:days-to-exp)
            expiry_date = exp_str.split(":")[0]
            dte = int(exp_str.split(":")[1]) if ":" in exp_str else None

            for strike_str, contracts in strikes.items():
                for c in contracts:
                    rows.append({
                        "expiry":         expiry_date,
                        "dte":            dte,
                        "strike":         float(strike_str),
                        "side":           side,
                        "settlement":     settlement_of(c),
                        "bid":            c.get("bid"),
                        "ask":            c.get("ask"),
                        "last":           c.get("last"),
                        "volume":         c.get("totalVolume"),
                        "open_interest":  c.get("openInterest"),
                        # BUG-030: these five are the fields Schwab answers with
                        # SCHWAB_NO_VALUE when it has nothing, so they are the
                        # five that go through _value_or_none. bid/ask/last are
                        # left alone — they are quotes, and the broker sends a
                        # real number or nothing at all for those.
                        "iv":             _value_or_none(c.get("volatility")),
                        "delta":          _value_or_none(c.get("delta")),
                        "gamma":          _value_or_none(c.get("gamma")),
                        "theta":          _value_or_none(c.get("theta")),
                        "vega":           _value_or_none(c.get("vega")),
                    })

    return pd.DataFrame(rows)


def filter_chain_by_strike_window(
    chain_df: pd.DataFrame,
    spot: float,
    width: int = config.STRIKE_FETCH_WIDTH_POINTS,
    atm_iv_pct: float | None = None,
    max_dte: int | None = None,
    keep_strikes: frozenset[float] | set[float] | None = None,
) -> pd.DataFrame:
    """
    Python-side safety filter: drops any strikes outside spot ± width points.
    This is a backstop for the API-level strike_count filter, not a replacement
    for it. In practice, strike_count=80 should never return strikes beyond
    ±300 points at SPX's spacing — but if strike spacing widens in the far wings
    (e.g. 25-point increments beyond ±200 pts), 80 strikes could theoretically
    reach further than intended. This filter ensures the stored dataset stays
    within the intended analytical window regardless.

    Optional 2 SD log check: if atm_iv_pct and max_dte are provided, the function
    computes the 2-standard-deviation expected move for the longest expiry in scope
    and logs a warning if that move exceeds the configured strike window. This is
    informational only — it never changes what gets stored. Use it as a signal that
    config.STRIKE_FETCH_WIDTH_POINTS should be reviewed.

    `keep_strikes` EXEMPTS LOCKED STRIKES FROM THE WINDOW (BUG-022). The window
    is centred on today's spot; a lock is fixed at the fill. As SPX moves, a
    locked strike drifts out of the window and stops being recorded, and the
    dashboard then silently charts a different diagonal. An exempt strike is
    kept only if the broker actually returned it — this widens what we KEEP,
    never what we ASK FOR, so it costs no extra API call and cannot invent data.

    Args:
        chain_df:    Full chain DataFrame from chain_to_dataframe().
        spot:        Current SPX underlying price.
        width:       Strike window half-width in points (default: config value).
        atm_iv_pct:  ATM IV as a percentage (e.g. 18.4), used for SD log check.
                     Pass None to skip the check.
        max_dte:     Longest DTE in the current fetch window, used for SD check.
                     Pass None to skip the check.
        keep_strikes: Strikes to retain regardless of the window (locked legs).
                     Pass None or an empty set for the ordinary behaviour.

    Returns:
        Filtered DataFrame containing only strikes within [spot - width, spot + width].
    """
    if chain_df.empty:
        return chain_df

    lower    = spot - width
    upper    = spot + width
    in_window = (chain_df["strike"] >= lower) & (chain_df["strike"] <= upper)

    # Locked legs survive the window (BUG-022). `.isin` on an empty collection
    # is all-False, so the no-locks case is byte-identical to the old behaviour.
    exempt = chain_df["strike"].isin(list(keep_strikes or ()))
    filtered = chain_df[in_window | exempt].copy()

    # HOW WIDE THE BROKER ACTUALLY WENT. Logged before the drop because the
    # filter destroys the evidence: once stored, every snapshot is clipped at
    # exactly ±width, so the stored data can never answer "how much headroom is
    # there?" — the question that decides whether a drifting locked strike can
    # be rescued from the response we already pay for, or needs a second fetch.
    _offsets = (chain_df["strike"] - spot)
    if not _offsets.empty:
        logger.info(
            "strike window: broker supplied -%.0f/+%.0f pts around spot %.2f; "
            "keeping ±%d (headroom -%.0f/+%.0f)",
            -_offsets.min(), _offsets.max(), spot, width,
            max(0.0, -_offsets.min() - width), max(0.0, _offsets.max() - width),
        )

    rescued = int((exempt & ~in_window).sum())
    if rescued:
        logger.info(
            "filter_chain_by_strike_window: kept %d contracts outside the window "
            "because their strike is locked (BUG-022)", rescued,
        )

    dropped = len(chain_df) - len(filtered)
    if dropped > 0:
        logger.debug(
            "filter_chain_by_strike_window: dropped %d contracts outside "
            "[%.0f, %.0f] (spot=%.2f, width=±%d)",
            dropped, lower, upper, spot, width,
        )

    # Optional: log a warning if 2 SD expected move exceeds the configured window.
    if atm_iv_pct is not None and max_dte is not None and max_dte > 0:
        iv_decimal = atm_iv_pct / 100.0
        em_2sd = 2 * spot * iv_decimal * math.sqrt(max_dte / 365)
        if em_2sd > width:
            logger.warning(
                "2 SD expected move (±%.0f pts, IV=%.1f%%, DTE=%d) exceeds "
                "configured strike window (±%d pts). Consider widening "
                "STRIKE_FETCH_WIDTH_POINTS in config.py.",
                em_2sd, atm_iv_pct, max_dte, width,
            )

    return filtered


def get_token_age_days() -> float | None:
    """
    Returns the age of the Schwab token in days since the initial OAuth login.

    Reads `creation_timestamp` from the token JSON file — this is the field
    schwab-py writes when client_from_manual_flow() completes.  It does NOT
    update on routine access-token refreshes, so it accurately tracks the
    7-day refresh-token clock regardless of how often the collector runs.

    Returns None if the token file is missing (never authenticated or deleted).
    Safe to call from app.py — does not touch the Schwab API.
    """
    import time as _time

    token_path = Path(config.SCHWAB_TOKEN_PATH)
    if not token_path.exists():
        return None
    try:
        import json as _json
        data = _json.loads(token_path.read_text())
        created = data.get("creation_timestamp")
        if created:
            return (_time.time() - float(created)) / 86400
        # Fallback: file creation time (less precise but better than nothing)
        return (_time.time() - token_path.stat().st_mtime) / 86400
    except Exception:
        return None
