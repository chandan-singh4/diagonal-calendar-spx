"""
schwab_client.py — Authentication + thin data-access layer over the Schwab API.

Uses the `schwab-py` community library, which handles the OAuth dance and token
refresh for you. We wrap it so the rest of the app (iv_engine.py, app.py) never
has to think about auth, tokens, or raw HTTP — it just calls get_spx_quote() or
get_option_chain() and gets clean data back.

Reference: https://schwab-py.readthedocs.io/
"""
import math
import logging
import schwab
import pandas as pd
from pathlib import Path

import config

logger = logging.getLogger(__name__)


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


def get_spx_quote(client) -> float:
    """Returns the current SPX index price."""
    resp = client.get_quote(config.UNDERLYING_SYMBOL)
    resp.raise_for_status()
    data = resp.json()
    return float(data[config.UNDERLYING_SYMBOL]["quote"]["lastPrice"])


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


def chain_to_dataframe(raw_chain: dict) -> pd.DataFrame:
    """
    Flattens Schwab's nested option chain JSON (callExpDateMap / putExpDateMap,
    each keyed by expiry-string -> strike-string -> [contract]) into one tidy
    DataFrame with one row per contract. This is the shape every other module
    in this project expects to work with.
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
                        "expiry": expiry_date,
                        "dte": dte,
                        "strike": float(strike_str),
                        "side": side,
                        "bid": c.get("bid"),
                        "ask": c.get("ask"),
                        "last": c.get("last"),
                        "volume": c.get("totalVolume"),
                        "open_interest": c.get("openInterest"),
                        "iv": c.get("volatility"),  # Schwab returns this as a percentage, e.g. 18.4
                        "delta": c.get("delta"),
                        "gamma": c.get("gamma"),
                        "theta": c.get("theta"),
                    })
    return pd.DataFrame(rows)


def filter_chain_by_strike_window(
    chain_df: pd.DataFrame,
    spot: float,
    width: int = config.STRIKE_FETCH_WIDTH_POINTS,
    atm_iv_pct: float | None = None,
    max_dte: int | None = None,
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

    Args:
        chain_df:     Full chain DataFrame from chain_to_dataframe().
        spot:         Current SPX underlying price.
        width:        Strike window half-width in points (default: config value).
        atm_iv_pct:   ATM IV as a percentage (e.g. 18.4), used for SD log check.
                      Pass None to skip the check.
        max_dte:      Longest DTE in the current fetch window, used for SD check.
                      Pass None to skip the check.

    Returns:
        Filtered DataFrame containing only strikes within [spot - width, spot + width].
    """
    if chain_df.empty:
        return chain_df

    lower = spot - width
    upper = spot + width
    filtered = chain_df[
        (chain_df["strike"] >= lower) &
        (chain_df["strike"] <= upper)
    ].copy()

    dropped = len(chain_df) - len(filtered)
    if dropped > 0:
        logger.debug(
            "filter_chain_by_strike_window: dropped %d contracts outside "
            "[%.0f, %.0f] (spot=%.2f, width=±%d)",
            dropped, lower, upper, spot, width,
        )

    # Optional: log a warning if 2 SD expected move exceeds the configured window.
    # This is a purely informational check — it does not change what gets stored.
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