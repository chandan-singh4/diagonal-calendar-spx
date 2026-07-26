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
