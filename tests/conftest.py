"""
Shared pytest fixtures.

Scope note (M1.1): everything here is in-memory. No fixture touches
data/dashboard.db — the production database is 1.4 GB of irreplaceable history
and the test suite must never be able to read, lock, or write it. Tests that
need a database get a temporary one via the `integration` marker instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# The project uses a flat module layout at M0/M1 (see pyproject [tool.setuptools]
# py-modules). Tests are run from the repo root, but add it explicitly so the
# suite also works when pytest is invoked from elsewhere (e.g. an IDE).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ─────────────────────────────────────────────────────────────────────────────
# Chain fixtures
#
# CONVENTION REMINDER (iv_engine docstring): IV is in PERCENTAGE form here —
# 18.5 means 18.5%. The database stores decimals and callers multiply by 100 at
# the load boundary. Building fixtures in decimal form would silently test the
# wrong contract, so every IV below is a percentage.
# ─────────────────────────────────────────────────────────────────────────────

FRONT = "2026-08-07"
BACK = "2026-08-21"


def _row(expiry, strike, side, iv, bid, ask, *, mark=None, volume=100.0,
         open_interest=1000.0, theta=-0.5):
    return {
        "expiry": expiry,
        "strike": float(strike),
        "side": side,
        "iv": iv,
        "bid": bid,
        "ask": ask,
        "mark": mark,
        "volume": volume,
        "open_interest": open_interest,
        "theta": theta,
    }


@pytest.fixture
def chain_df() -> pd.DataFrame:
    """A small two-expiry chain around a 6000 spot.

    Deliberately shaped so the interesting cases are reachable:
      - strikes 5950 / 6000 / 6050 on both sides and both expiries
      - front IV below back IV (contango) so term-structure tests have a sign
      - `mark` populated on back legs, absent (None) on front legs, so the
        bid/ask midpoint fallback in strike_contract() and transform_credit()
        is actually exercised rather than skipped.
    """
    rows = []
    for strike, f_iv, b_iv in ((5950, 17.0, 19.0), (6000, 18.0, 20.0), (6050, 19.0, 21.0)):
        for side in ("CALL", "PUT"):
            # Front legs: no mark → forces midpoint fallback.
            rows.append(_row(FRONT, strike, side, f_iv, bid=9.0, ask=11.0,
                             mark=None, theta=-0.80))
            # Back legs: explicit mark that is NOT the midpoint, so a test can
            # tell whether the stored mark or a recomputed midpoint was used.
            rows.append(_row(BACK, strike, side, b_iv, bid=19.0, ask=21.0,
                             mark=25.0, theta=-0.30))
    return pd.DataFrame(rows)


@pytest.fixture
def spot() -> float:
    """Underlying price sitting exactly on the 6000 strike."""
    return 6000.0


# ─────────────────────────────────────────────────────────────────────────────
# Trade-row fixtures (journal P&L)
#
# The journal reads sqlite3.Row objects, which are NOT dicts: they raise
# IndexError on a missing key instead of returning None, and expose .keys().
# The row_get() helper in journal.py exists precisely because of that. Faking
# rows with a plain dict would make row_get() look unnecessary and would hide
# the legacy-schema bugs it guards against, so FakeRow reproduces the real
# behaviour instead.
# ─────────────────────────────────────────────────────────────────────────────

class FakeRow:
    """Minimal stand-in for sqlite3.Row."""

    def __init__(self, data: dict):
        self._data = dict(data)

    def __getitem__(self, key):
        try:
            return self._data[key]
        except KeyError:
            # sqlite3.Row raises IndexError, not KeyError, for an unknown column.
            raise IndexError(f"No item with that key: {key}") from None

    def keys(self):
        return list(self._data)

    def __contains__(self, key):
        return key in self._data


def make_trade(**overrides) -> FakeRow:
    """A Transformed-then-Expired trade with complete IC data.

    Baseline numbers, chosen so every derived figure is checkable by hand:
      profit_locked_in = 6.00 points, 2 contracts
      IC: long put 5900 / short put 5950 / short call 6050 / long call 6100
      spx_at_expiry = 6000 → between the shorts → no assignment
      => resolved_pl = (6.00 + 0.00) x 100 x 2 = $1,200.00
    """
    base = {
        "trade_id": 1,
        "status": "Expired",
        "contracts": 2,
        "profit_locked_in": 6.00,
        "final_pl": None,
        "spx_at_expiry": 6000.0,
        "ic_long_put": 5900.0,
        "ic_short_put": 5950.0,
        "ic_short_call": 6050.0,
        "ic_long_call": 6100.0,
        "entry_date": "2026-07-01",
        "result_date": "2026-07-15",
        "transform_minutes": 120,
        "total_debit": 4.00,
        "credit_received": 10.00,
        "commissions": 2.60,
        "transform_commissions": 2.60,
        "close_type": "transform",
    }
    base.update(overrides)
    return FakeRow(base)


def make_legacy_trade(**overrides) -> FakeRow:
    """A pre-IC-schema row: no ic_* columns, no transform_commissions, no close_type.

    These exist in the real database and are the reason for row_get() and the
    `"x" in t.keys()` guards. Any code path that assumes the modern schema will
    raise IndexError on this fixture.
    """
    base = {
        "trade_id": 99,
        "status": "Closed",
        "contracts": 1,
        "profit_locked_in": None,
        "final_pl": 250.0,
        "entry_date": "2026-06-01",
        "result_date": "2026-06-10",
        "transform_minutes": None,
        "total_debit": 3.00,
        "credit_received": None,
        "commissions": 1.30,
    }
    base.update(overrides)
    return FakeRow(base)


@pytest.fixture
def journal():
    """The pure P&L functions, loaded from pages/journal.py without Streamlit."""
    from journal_loader import load_journal_functions

    return load_journal_functions()


# ─────────────────────────────────────────────────────────────────────────────
# Temporary database fixtures (db.py — M1.5)
#
# The module docstring above states the rule: no fixture may touch
# data/dashboard.db. db.py is the one module that cannot be tested purely in
# memory, so it gets a throwaway file under pytest's tmp_path instead — a fresh
# directory per test, deleted by pytest afterwards.
#
# The guard in temp_db() is not ceremony. Several db.py functions default to
# config.DB_PATH when db_path is None (get_conn, init_db), so a single missing
# argument in a future test would silently point the suite at 1.4 GB of
# irreplaceable production history. The assert makes that a loud failure.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path) -> str:
    """An initialised, empty database at a throwaway path. Returns the path."""
    import config
    import db

    path = tmp_path / "test_dashboard.db"
    assert path.resolve() != Path(config.DB_PATH).resolve(), (
        "temp_db resolved to the PRODUCTION database path — refusing to run"
    )
    db.init_db(str(path))
    return str(path)


@pytest.fixture
def trades_db(temp_db) -> str:
    """temp_db with the trades table created as well (journal.py's schema)."""
    import db

    db.init_trades_table(temp_db)
    return temp_db


# ─────────────────────────────────────────────────────────────────────────────
# Collection-cycle fixtures (collector._run_cycle — M1.7)
#
# The cycle reaches the network ONLY through module-level schwab_client
# functions, so it can be driven end-to-end by patching those. Nothing here
# touches Schwab, the token file, or the network.
#
# The chain is built in Schwab's RAW nested JSON shape, not as a ready-made
# DataFrame, so chain_to_dataframe() parsing runs for real. A pre-parsed
# fixture would skip the exact layer where a Schwab format change would bite,
# and every test would still pass while collection was broken in production.
# ─────────────────────────────────────────────────────────────────────────────

def make_raw_chain(expiries=None, spot=6000.0, *, iv=18.4,
                   strikes=None, missing_iv_strikes=(), duplicate_contracts=False):
    """Build a raw Schwab option-chain response.

    Args:
        expiries:           list of (expiry_date_str, dte). Defaults to two.
        spot:               only used to centre the default strike ladder.
        iv:                 volatility as Schwab sends it — a PERCENTAGE (18.4
                            means 18.4%). The collector divides by 100 on the
                            way into the database; tests assert on that.
        strikes:            explicit strike ladder; defaults to spot ±100 by 50.
        missing_iv_strikes: strikes whose contracts carry volatility=None, to
                            exercise the "skip rows with no IV" path.
        duplicate_contracts: emit each contract TWICE. Schwab returns a list per
                            strike, so this is a shape it can genuinely produce.
                            The database's unique constraint drops the second
                            copy, which is the case where offered and stored
                            counts diverge (ADR-022 / DEBT-008).
    """
    if expiries is None:
        expiries = [("2026-08-07", 7), ("2026-08-21", 21)]
    if strikes is None:
        strikes = [spot - 100, spot - 50, spot, spot + 50, spot + 100]

    chain: dict = {"callExpDateMap": {}, "putExpDateMap": {}}

    for side_key in ("callExpDateMap", "putExpDateMap"):
        for expiry, dte in expiries:
            # Schwab keys expiries as "YYYY-MM-DD:DTE".
            exp_key = f"{expiry}:{dte}"
            def _contract(strike):
                return {
                    "bid":          9.0,
                    "ask":          11.0,
                    "last":         10.0,
                    "totalVolume":  100,
                    "openInterest": 1000,
                    "volatility":   None if strike in missing_iv_strikes else iv,
                    "delta":        0.5,
                    "gamma":        0.01,
                    "theta":        -0.5,
                    "vega":         0.2,
                }

            copies = 2 if duplicate_contracts else 1
            chain[side_key][exp_key] = {
                str(strike): [_contract(strike) for _ in range(copies)]
                for strike in strikes
            }

    return chain


class FakeResponse:
    """Stand-in for the HTTP response schwab-py returns.

    Only the two things the client layer actually uses: raise_for_status() and
    json(). `status_error` makes raise_for_status() raise, which is how a 401 or
    a 500 reaches the collector in production.
    """

    def __init__(self, payload=None, status_error=None):
        self._payload = payload if payload is not None else {}
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


def quote_payload(symbol: str, **fields) -> dict:
    """Schwab's quote shape: {SYMBOL: {"quote": {...}}}."""
    return {symbol: {"quote": dict(fields)}}


class RecordingClient:
    """A client that records the arguments it was called with.

    Used only where the ARGUMENTS are the contract worth checking — e.g. that
    get_option_chain passes the configured strike_count through rather than
    silently falling back to a Schwab default. Everywhere else the tests assert
    on returned data instead.
    """

    def __init__(self, quote_response=None, chain_response=None):
        self._quote_response = quote_response or FakeResponse()
        self._chain_response = chain_response or FakeResponse()
        self.quote_calls: list = []
        self.chain_calls: list = []

    def get_quote(self, symbol):
        self.quote_calls.append(symbol)
        return self._quote_response

    def get_option_chain(self, symbol, **kwargs):
        self.chain_calls.append({"symbol": symbol, **kwargs})
        return self._chain_response


class FakeSchwabClient:
    """Stand-in for the schwab-py client object.

    _run_cycle only ever passes this through to schwab_client functions, which
    the tests patch, so it needs no behaviour of its own. It exists so the
    tests pass something that is clearly NOT a real authenticated client.
    """


@pytest.fixture
def fake_client() -> FakeSchwabClient:
    return FakeSchwabClient()


@pytest.fixture
def patch_schwab(monkeypatch):
    """Patch collector's Schwab calls. Returns a configure() helper.

    Defaults are a healthy market: a good SPX quote, a VIX value, and a
    two-expiry chain with IV on every contract. Each test overrides only the
    part it is about, so a test's setup shows exactly what it is testing.
    """
    import collector

    def configure(*, quote=None, vix=18.5, raw_chain=None,
                  quote_error=None, chain_error=None):
        if quote is None:
            quote = {"bid": 5999.0, "ask": 6001.0, "last": 6000.5, "mark": 6000.0}
        if raw_chain is None:
            raw_chain = make_raw_chain()

        def _get_quote(_client):
            if quote_error is not None:
                raise quote_error
            return quote

        def _get_chain(_client, _from, _to):
            if chain_error is not None:
                raise chain_error
            return raw_chain

        monkeypatch.setattr(collector.schwab_client, "get_spx_quote_full", _get_quote)
        monkeypatch.setattr(collector.schwab_client, "get_vix_quote", lambda _c: vix)
        monkeypatch.setattr(collector.schwab_client, "get_option_chain", _get_chain)

    return configure
