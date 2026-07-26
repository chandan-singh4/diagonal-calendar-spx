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
