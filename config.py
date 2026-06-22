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

SCHWAB_APP_KEY = os.environ.get("SCHWAB_APP_KEY", "")
SCHWAB_APP_SECRET = os.environ.get("SCHWAB_APP_SECRET", "")
SCHWAB_CALLBACK_URL = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
SCHWAB_TOKEN_PATH = str(PROJECT_ROOT / os.environ.get("SCHWAB_TOKEN_PATH", "data/token.json"))

DB_PATH = str(PROJECT_ROOT / os.environ.get("DB_PATH", "data/dashboard.db"))
DEMO_DB_PATH = str(PROJECT_ROOT / "data" / "demo_dashboard.db")

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "10"))

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

UNDERLYING_SYMBOL = "$SPX"  # Schwab's symbol convention for the SPX index


def validate():
    """Call this at startup so a missing credential fails loudly, not with a
    confusing downstream error."""
    missing = [
        name for name, val in [
            ("SCHWAB_APP_KEY", SCHWAB_APP_KEY),
            ("SCHWAB_APP_SECRET", SCHWAB_APP_SECRET),
        ] if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required .env values: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill them in."
        )
