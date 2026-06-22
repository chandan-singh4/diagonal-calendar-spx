"""
schwab_client.py — Authentication + thin data-access layer over the Schwab API.

Uses the `schwab-py` community library, which handles the OAuth dance and token
refresh for you. We wrap it so the rest of the app (iv_engine.py, app.py) never
has to think about auth, tokens, or raw HTTP — it just calls get_spx_quote() or
get_option_chain() and gets clean data back.

Reference: https://schwab-py.readthedocs.io/
"""
import schwab
import pandas as pd
from pathlib import Path

import config


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


def get_option_chain(client, from_date, to_date, strike_count: int = 20) -> dict:
    """
    Pulls the raw SPX option chain between from_date and to_date (datetime.date objects).

    strike_count controls how many strikes above/below ATM are returned — keep this
    reasonably small (20-30) to avoid pulling the entire chain on every poll, which
    is slower and burns through rate limits faster than you need for a diagonal
    strategy that only cares about strikes near spot.
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
