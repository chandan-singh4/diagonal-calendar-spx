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

# Where the small JSON sidecar files live: chart colours, entry locks, and the
# eligibility registry. Anchored to the project root exactly as DB_PATH is —
# which the database got right from the start and these files did not.
#
# DEBT-011 (fixed 2026-07-30, ADR-035): they were `Path("eligible_history.json")`
# and friends, RELATIVE, so they resolved against whatever directory the
# dashboard happened to be launched from. Started anywhere but the project root,
# the app would find no registry, create an empty one there, and show a Mission
# Control panel that had silently forgotten every past opportunity.
#
# .resolve() rather than PROJECT_ROOT alone so the value is absolute even if
# Python ever hands us a relative __file__.
STATE_DIR    = Path(os.environ.get("STATE_DIR", PROJECT_ROOT)).resolve()

# NOTE: DEMO_MODE and DEMO_DB_PATH were removed 2026-07-25 (M0.11). Demo Mode
# had been removed from the dashboard UI, leaving the config flag with zero
# consumers and demo_data.py orphaned (it also still wrote to the pre-v2
# `strike_snapshots` schema). Both were deleted. Do not re-add a demo flag
# without a consumer — the README instructed users to "toggle Demo Mode off in
# the sidebar" for weeks after the toggle ceased to exist.

UNDERLYING_SYMBOL = "$SPX"   # Schwab's symbol convention for the SPX index
VIX_SYMBOL        = "$VIX" # Schwab's symbol for the CBOE Volatility Index

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

#Changed the logic - Collect exactly 20 expirations

MAX_EXPIRY_COUNT = 20        # collect exactly this many expirations per snapshot
MAX_EXPIRY_FETCH_DAYS = 90   # how far out to look when fetching (wide net; trimmed after)

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
# Retention (ADR-044)
# ---------------------------------------------------------------------------

# Per-strike option_rows are prunable this many days after their expiry date.
# atm_iv_by_expiry, snapshots and collection_gaps are kept forever, and any
# expiry a trade actually used is exempt at any age.
#
# NOTHING READS THIS ON A SCHEDULE. Pruning happens only when scripts/prune.py
# is run by hand, and that script defaults to reporting rather than deleting.
RETENTION_DAYS = 90

# ---------------------------------------------------------------------------
# Watchdog (M3.4)
# ---------------------------------------------------------------------------
# scripts/watchdog.py answers one question from OUTSIDE the dashboard: are
# prices still arriving? The dashboard already shouts when they are not, but it
# can only shout at someone who is looking at it — which is exactly why the
# expired token on 2026-08-09 went unnoticed until a check happened to run.

# How late prices must be before it alarms, as a multiple of the interval the
# collector is actually using this session (60s in the first and last half
# hour, 300s midday — see core/session.py). One missed cycle is a hiccup; this
# is set so a single slow poll does not raise an alarm, but two do.
WATCHDOG_LATE_MULTIPLE = 2.5

# Minimum gap between repeat alerts about the SAME ongoing outage, in minutes.
# Without it, a five-minute schedule turns one dead collector into twelve
# emails an hour and Chandan learns to delete them unread.
WATCHDOG_REALERT_MINUTES = 60

# Grace period after the opening bell, in minutes. At 09:31 the newest price
# is legitimately from yesterday's close, so an age-based check alarms every
# single morning unless it waits for the first cycle or two to land.
WATCHDOG_OPEN_GRACE_MINUTES = 5

# --- Email alerts ------------------------------------------------------------
# NO CREDENTIAL IS STORED HERE. These read from .env, which is gitignored; the
# password never enters the repository and is never printed. Leave
# ALERT_EMAIL_TO empty and email is skipped entirely — the desktop pop-up still
# works, so an unconfigured mailbox degrades to a quieter watchdog, not a
# broken one.
#
# For Gmail this must be an APP PASSWORD, not the account password: Google
# rejects plain passwords from scripts. See docs/OPERATIONS.md (M3.9).
ALERT_EMAIL_TO       = os.environ.get("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM     = os.environ.get("ALERT_EMAIL_FROM", "")
ALERT_SMTP_HOST      = os.environ.get("ALERT_SMTP_HOST", "smtp.gmail.com")
ALERT_SMTP_PORT      = int(os.environ.get("ALERT_SMTP_PORT", "587"))
ALERT_SMTP_PASSWORD  = os.environ.get("ALERT_SMTP_PASSWORD", "")

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
