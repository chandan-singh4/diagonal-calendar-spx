"""
demo_data.py — Synthetic data for Demo Mode.

Purpose: let you run `streamlit run app.py` and see a fully working dashboard —
chart, expirations panel, options chain, historical stats — before your Schwab
credentials are wired up, or any time you want to tweak the UI without burning
real API calls. None of this data is real; everything here is clearly labeled
as synthetic and writes to a separate database file so it can never contaminate
real collected history.

The IV process is a simple mean-reverting random walk (Ornstein-Uhlenbeck-style),
not a real market model — it's built to *look* plausible for chart-testing
purposes, not to simulate actual market dynamics.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import config
import db

DEMO_FRONT = "DEMO-FRONT (~5 DTE)"
DEMO_BACK = "DEMO-BACK (~14 DTE)"
DEMO_FRONT_DTE = 5
DEMO_BACK_DTE = 14
DEMO_BASE_SPX = 6840.0

_rng = np.random.default_rng(seed=42)  # fixed seed so the demo looks the same across runs


def get_demo_quote() -> float:
    """A slowly wandering fake SPX price, deterministic-ish within a session via
    the module-level RNG so the chain and quote stay roughly consistent."""
    return float(DEMO_BASE_SPX + _rng.normal(0, 3))


def _mean_reverting_series(n: int, start: float, mean: float, speed: float, vol: float) -> np.ndarray:
    """One Ornstein-Uhlenbeck path: wanders around `mean`, pulled back at rate
    `speed`, with per-step noise `vol`. This is the standard toy model for
    something that goes up and down but doesn't drift off forever — good enough
    for a chart-testing fixture."""
    series = np.empty(n)
    series[0] = start
    for i in range(1, n):
        series[i] = series[i - 1] + speed * (mean - series[i - 1]) + _rng.normal(0, vol)
    return series


def seed_if_empty(force: bool = False):
    """Backfills ~35 days of synthetic 30-minute-interval IV history for the two
    demo expiries, during a fake market-hours window each day. Only runs if the
    demo db is empty (or if force=True), so re-running the app doesn't keep
    re-seeding on every page load."""
    db.init_db(config.DEMO_DB_PATH)
    if not force and db.has_any_data(config.DEMO_DB_PATH):
        return

    days_back = 35
    samples_per_day = 13  # 9:30am-4:00pm in 30-min steps

    front_path = _mean_reverting_series(days_back * samples_per_day, start=12.5, mean=12.0, speed=0.05, vol=0.25)
    back_path = _mean_reverting_series(days_back * samples_per_day, start=13.2, mean=13.0, speed=0.04, vol=0.18)
    # Occasionally widen the spread to simulate the "elevated front IV" setups you're hunting for
    spike_days = _rng.choice(days_back, size=4, replace=False)

    now = datetime.now(timezone.utc)
    idx = 0
    for day_offset in range(days_back, 0, -1):
        day = now - timedelta(days=day_offset)
        spike = day_offset in [days_back - d for d in spike_days]
        for sample in range(samples_per_day):
            ts = (day.replace(hour=14, minute=30, second=0, microsecond=0)
                  + timedelta(minutes=30 * sample)).isoformat()  # ~9:30am ET in UTC, roughly
            spx = DEMO_BASE_SPX + _rng.normal(0, 15) + (day_offset - days_back / 2) * 0.5
            f_iv = front_path[idx] + (3.0 if spike else 0)
            b_iv = back_path[idx]
            db.save_expiry_snapshot(spx, DEMO_FRONT, DEMO_FRONT_DTE, max(f_iv, 5.0),
                                     db_path=config.DEMO_DB_PATH, timestamp=ts)
            db.save_expiry_snapshot(spx, DEMO_BACK, DEMO_BACK_DTE, max(b_iv, 5.0),
                                     db_path=config.DEMO_DB_PATH, timestamp=ts)
            idx += 1


def generate_synthetic_chain(spx_price: float) -> pd.DataFrame:
    """Builds a fake option chain DataFrame in the same shape schwab_client.chain_to_dataframe()
    produces, so app.py doesn't need separate code paths for the chain table."""
    strikes = np.arange(round(spx_price / 5) * 5 - 500, round(spx_price / 5) * 5 + 500, 5)
    rows = []
    for expiry, dte, base_iv in [(DEMO_FRONT, DEMO_FRONT_DTE, 12.3), (DEMO_BACK, DEMO_BACK_DTE, 13.1)]:
        for strike in strikes:
            moneyness = abs(strike - spx_price) / spx_price
            smile_iv = base_iv + moneyness * 40  # simple smile: IV rises away from ATM
            for side in ("CALL", "PUT"):
                itm = (side == "CALL" and strike < spx_price) or (side == "PUT" and strike > spx_price)
                intrinsic = abs(spx_price - strike) if itm else 0
                mid = max(intrinsic + _rng.uniform(1, 8), 0.05)
                spread = max(mid * 0.02, 0.1)
                rows.append({
                    "expiry": expiry, "dte": dte, "strike": float(strike), "side": side,
                    "bid": round(mid - spread / 2, 2), "ask": round(mid + spread / 2, 2),
                    "last": round(mid, 2),
                    "volume": int(max(_rng.normal(300, 200), 0)) if moneyness < 0.03 else int(max(_rng.normal(40, 30), 0)),
                    "open_interest": int(max(_rng.normal(1500, 800), 0)),
                    "iv": round(float(smile_iv), 2),
                    "delta": round(float(_rng.uniform(-1, 1)), 3),
                    "gamma": round(float(_rng.uniform(0, 0.01)), 4),
                    "theta": round(float(-abs(_rng.normal(2, 1))), 2),
                })
    return pd.DataFrame(rows)
