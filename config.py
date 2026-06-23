"""
config.py — Centralized configuration loaded from .env

Every other module pulls settings from here instead of reading os.environ directly,
so there's exactly one place to look when you need to change a setting.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent

SCHWAB_APP_KEY      = os.environ.get("SCHWAB_APP_KEY", "")
SCHWAB_APP_SECRET   = os.environ.get("SCHWAB_APP_SECRET", "")
SCHWAB_CALLBACK_URL = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
SCHWAB_TOKEN_PATH   = str(PROJECT_ROOT / os.environ.get("SCHWAB_TOKEN_PATH", "data/token.json"))

DB_PATH      = str(PROJECT_ROOT / os.environ.get("DB_PATH", "data/dashboard.db"))
DEMO_DB_PATH = str(PROJECT_ROOT / "data" / "demo_dashboard.db")

# Default state of the Demo Mode toggle in the dashboard sidebar, used every time
# the Streamlit process (re)starts. Demo Mode uses synthetic data and needs no
# Schwab credentials — handy for previewing the UI or developing chart/layout
# changes without burning real API calls.
#
# If you set DEMO_MODE explicitly in .env, that wins, full stop. Otherwise, the
# default is smart: ON if no real credentials are present yet (so first-time
# setup just works with zero config), OFF once SCHWAB_APP_KEY/SECRET are filled
# in (so a process restart after you've gone live doesn't silently revert you
# back to synthetic data without you noticing — which is exactly what happened
# before this fix).
_explicit_demo_mode = os.environ.get("DEMO_MODE")
if _explicit_demo_mode is not None:
    DEMO_MODE = _explicit_demo_mode.lower() == "true"
else:
    DEMO_MODE = not (SCHWAB_APP_KEY and SCHWAB_APP_SECRET)

UNDERLYING_SYMBOL = "$SPX"   # Schwab's symbol convention for the SPX index
VIX_SYMBOL        = "$VIX.X" # Schwab's symbol for the CBOE Volatility Index

# ---------------------------------------------------------------------------
# Data Collection
# ---------------------------------------------------------------------------

# Number of strikes above and below ATM requested from Schwab per expiry.
# 80 covers approximately ±300–400 points at SPX's typical near-ATM spacing
# of 5 points. This is the API-level filter — coarse by design, with a
# Python-side safety filter (STRIKE_FETCH_WIDTH_POINTS) as a hard backstop.
STRIKE_COUNT = 80

# Hard boundary for the Python-side safety filter applied after the API fetch.
# Any strike outside spot ± this value is dropped before storage, regardless
# of what Schwab returned. Keeps storage clean if STRIKE_COUNT ever overshoots.
# Unit: points. Change this if your typical diagonal candidates move beyond ±300.
STRIKE_FETCH_WIDTH_POINTS = 300

# Maximum days-to-expiration for collected expirations. The fetch window runs
# from today through today + MAX_EXPIRY_DTE calendar days. SPX has ~10–11
# expirations in a typical 20-day window (Mon/Wed/Fri weeklies + end-of-month).
# Increase this only if you begin analyzing longer-dated diagonal pairings.
MAX_EXPIRY_DTE = 20

# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

# Standard polling interval for normal trading days. IV term structure shifts
# over minutes and hours — 5 minutes captures all meaningful moves without
# accumulating unnecessary database volume.
POLL_INTERVAL_NORMAL = 300   # seconds (5 minutes)

# High-resolution polling interval used during OPEN (9:30–10:00) and CLOSE
# (15:30–16:00) sessions, where IV moves most aggressively. Also activated
# manually via the Event Mode toggle in the sidebar for FOMC/CPI/NFP days.
POLL_INTERVAL_EVENT = 60     # seconds (1 minute)

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

# Timezone for all timestamp display and chart X-axis labels. All times stored
# in the database are UTC — this constant controls conversion at display time only.
DISPLAY_TIMEZONE = "America/New_York"

# ---------------------------------------------------------------------------
# Market Holidays
# ---------------------------------------------------------------------------
# US equity market holidays for 2026. The collector uses this list to classify
# collection gaps as HOLIDAY vs COLLECTOR_OFFLINE, so weekend/holiday gaps
# don't appear as unexpected data losses in the dashboard.
#
# Update this set each January for the new calendar year. Only full-day closures
# are listed — early-close days (e.g. Black Friday, Christmas Eve) are treated
# as normal trading days since SPX options still trade until 4:00 PM ET.
MARKET_HOLIDAYS = {
    # 2026
    "2026-01-01",   # New Year's Day (Thursday)
    "2026-01-19",   # Martin Luther King Jr. Day (3rd Monday)
    "2026-02-16",   # Presidents' Day (3rd Monday)
    "2026-04-03",   # Good Friday
    "2026-05-25",   # Memorial Day (last Monday)
    "2026-07-03",   # Independence Day observed (July 4 falls on Saturday)
    "2026-09-07",   # Labor Day (1st Monday)
    "2026-11-26",   # Thanksgiving Day (4th Thursday)
    "2026-12-25",   # Christmas Day (Friday)
}


def validate():
    """Call this at startup so a missing credential fails loudly, not with a
    confusing downstream error."""
    missing = [
        name for name, val in [
            ("SCHWAB_APP_KEY",    SCHWAB_APP_KEY),
            ("SCHWAB_APP_SECRET", SCHWAB_APP_SECRET),
        ] if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required .env values: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill them in."
        )
